"""
Vérification du JWT SSO Adision côté backend Ad BUD.

Le JWT est émis par le dashboard adision-app-api (HS256, secret partagé via
JWT_SECRET). Ad BUD ne fait PAS de login : il vérifie la signature, contrôle
que l'utilisateur a accès au module "ad_bud", et auto-provisionne le user
dans ad_budget.users si c'est sa première connexion SSO.

Le user_id retourné par get_current_user() est le `ad_budget.users.id` LOCAL,
pas le `app_central.users.id` du dashboard — c'est lui qui est utilisé comme
foreign key dans `ad_budget.projets.user_id`.
"""
import os
from typing import Optional

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Query
from psycopg.rows import dict_row

from modules.revocation_cache import is_token_revoked

JWT_ALGORITHM = "HS256"
REQUIRED_MODULE = "ad_bud"

# Étape 1 SSO (juin 2026) — cookie de session cross-subdomain ADDITIF.
# Lu en dernier ressort par `_extract_bearer`. Posé par adision-app-api
# au login. Si absent, le système header/query reste fonctionnel.
SESSION_COOKIE_NAME = "__Secure-adision-session"


def _get_jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="JWT_SECRET non configuré côté serveur",
        )
    return secret


def _decode_token(token: str) -> dict:
    """Vérifie signature + expiration. Lève 401 si invalide.

    Rotation gracieuse : si la signature échoue avec JWT_SECRET et qu'un
    JWT_SECRET_OLD est configuré, réessaie avec l'ancien secret — permet
    de tourner JWT_SECRET sans déconnecter les utilisateurs. Seul un échec
    de signature déclenche le repli ; un token expiré ou malformé non.
    """
    try:
        try:
            return jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        except jwt.InvalidSignatureError:
            old = os.environ.get("JWT_SECRET_OLD", "")
            if old:
                return jwt.decode(token, old, algorithms=[JWT_ALGORITHM])
            raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expiré, reconnexion nécessaire")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Token invalide : {e}")


def _derive_platform_role(role):
    """Dérive platform_role depuis l'ancien `role`. Fallback pour les tokens
    legacy (émis avant l'enrichissement du JWT, encore valides jusqu'à 24h
    après déploiement) et les users dont la colonne platform_role n'est pas
    renseignée. Mapping IDENTIQUE au backfill de la migration 006 :
    super_admin->super_admin, admin->staff, tout le reste->client."""
    if role == "super_admin":
        return "super_admin"
    if role == "admin":
        return "staff"
    return "client"


def _extract_bearer(
    authorization: Optional[str],
    token_query: Optional[str] = None,
    session_cookie: Optional[str] = None,
) -> str:
    """Lit le JWT soit du header `Authorization: Bearer <jwt>`, soit du
    query param `?token=<jwt>` (utilisé seulement pour les GET de download
    PDF / Excel ouverts via window.open, qui n'acceptent pas de header), soit
    du cookie de session cross-subdomain (étape 1 SSO, juin 2026 — additif)."""
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
        raise HTTPException(
            status_code=401,
            detail="Header Authorization mal formé (attendu : Bearer <JWT>)",
        )
    if token_query:
        return token_query.strip()
    if session_cookie:
        return session_cookie.strip()
    raise HTTPException(
        status_code=401,
        detail="Authentification requise (header Authorization: Bearer <JWT>)",
    )


def _provision_user(conn, payload: dict) -> dict:
    """Lookup user dans ad_budget.users par email. Si absent, auto-provision
    avec les infos du JWT (premier login SSO). Retourne le row local complet."""
    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="JWT sans email")

    cur = conn.cursor(row_factory=dict_row)
    cur.execute(
        "SELECT id, nom, email, role, created_at "
        "FROM ad_budget.users WHERE LOWER(email) = %s",
        (email,),
    )
    row = cur.fetchone()
    if not row:
        # Le JWT du dashboard ne contient pas "nom" → on prend la part avant
        # le @ comme nom par défaut. L'utilisateur peut le corriger ensuite.
        nom = (payload.get("nom") or email.split("@", 1)[0]).strip() or email
        role = payload.get("role") or "user"
        cur.execute(
            """
            INSERT INTO ad_budget.users (nom, email, role)
            VALUES (%s, %s, %s)
            RETURNING id, nom, email, role, created_at
            """,
            (nom, email, role),
        )
        row = cur.fetchone()
        conn.commit()
    cur.close()
    return dict(row)


def make_jwt_deps(get_conn):
    """Crée les FastAPI dependencies bound au get_conn de l'app.

    Retourne un 4-uple :
        jwt_user, jwt_user_or_token, jwt_admin, jwt_super_admin = make_jwt_deps(get_conn)

    Tout appelant qui déballe ce retour DOIT prévoir 4 variables (utiliser
    `_` pour celles non utilisées). Exemple :
        @router.get("/budget/projets")
        def get_projets(user=Depends(jwt_user)):
            ...
    """

    def _check_module(payload: dict) -> None:
        modules = payload.get("modules") or []
        if REQUIRED_MODULE not in modules:
            raise HTTPException(
                status_code=403,
                detail="Module Ad BUD non autorisé pour cet utilisateur",
            )

    def jwt_user(
        authorization: Optional[str] = Header(None),
        token: Optional[str] = Query(None),
        session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    ) -> dict:
        """Dependency standard : accepte le JWT soit via header
        `Authorization: Bearer <jwt>`, soit via query `?token=<jwt>` (utilisé
        par les GET de download PDF / Excel ouverts en window.open), soit via
        cookie de session cross-subdomain (étape 1 SSO, juin 2026).
        Vérifie signature + module ad_bud, auto-provisionne le user dans
        ad_budget.users, retourne le row local enrichi des modules JWT."""
        jwt_token = _extract_bearer(authorization, token, session_cookie)
        payload = _decode_token(jwt_token)
        # Phase J3 étape 2 — kill-switch satellite (cat.2, endpoint hub
        # + cache TTL 60s). Voir modules/revocation_cache.py. Fail-open
        # défensif si erreur (ne propage jamais de 500).
        if is_token_revoked(payload.get("user_id"), payload.get("iat")):
            raise HTTPException(
                status_code=401,
                detail="Session révoquée — reconnexion requise",
            )
        _check_module(payload)
        conn = get_conn()
        try:
            user = _provision_user(conn, payload)
        finally:
            conn.close()
        user["modules"] = payload.get("modules") or []
        # Multi-tenant (PHASE 2) — champs greffés depuis le payload JWT.
        # Token legacy (émis avant l'enrichissement, valide jusqu'à 24h) :
        # organization_id / org_role absents -> None ; platform_role dérivé de
        # `role` (mapping migration 006) pour ne pas perdre le rôle plateforme.
        user["organization_id"] = payload.get("organization_id")
        user["platform_role"] = (
            payload.get("platform_role")
            or _derive_platform_role(payload.get("role"))
        )
        user["org_role"] = payload.get("org_role")
        return user

    def jwt_admin(user: dict = Depends(jwt_user)) -> dict:
        """Dependency réservée aux admins. FastAPI dédupe Depends(jwt_user)
        si la même route le déclare aussi en param, donc 1 seul DB lookup."""
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Accès admin requis")
        return user

    def jwt_super_admin(
        authorization: Optional[str] = Header(None),
        token: Optional[str] = Query(None),
        session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    ) -> dict:
        """Dependency réservée aux super_admin (administration transverse :
        grille des taux horaires, etc.).

        Différences volontaires avec jwt_user / jwt_admin :
          - NE vérifie PAS le module ad_bud (`_check_module`) : ces endpoints
            d'administration ne sont pas un usage du module Ad BUD, ils sont
            appelés depuis Ad ADM. Un super_admin n'a pas forcément le flag
            ad_bud dans son JWT.
          - NE provisionne PAS de user local dans ad_budget.users : pas besoin
            du user_id local ici. Retourne le payload JWT brut (claims sub,
            email, role, modules, exp) — l'appelant prend `sub`/`email` pour
            renseigner les colonnes updated_by.
        """
        jwt_token = _extract_bearer(authorization, token, session_cookie)
        payload = _decode_token(jwt_token)
        # Phase J3 étape 2 — kill-switch satellite (cat.2). Un super_admin
        # révoqué est révoqué partout, donc check identique à jwt_user.
        if is_token_revoked(payload.get("user_id"), payload.get("iat")):
            raise HTTPException(
                status_code=401,
                detail="Session révoquée — reconnexion requise",
            )
        if payload.get("role") != "super_admin":
            raise HTTPException(
                status_code=403, detail="Accès super_admin requis"
            )
        return payload

    # On garde jwt_user_or_token comme alias pour la lisibilité des routes
    # de download (PDF / Excel), mais c'est exactement la même fonction.
    return jwt_user, jwt_user, jwt_admin, jwt_super_admin
