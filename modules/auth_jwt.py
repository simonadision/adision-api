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
from fastapi import Depends, Header, HTTPException, Query
from psycopg.rows import dict_row

JWT_ALGORITHM = "HS256"
REQUIRED_MODULE = "ad_bud"


def _get_jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="JWT_SECRET non configuré côté serveur",
        )
    return secret


def _decode_token(token: str) -> dict:
    """Vérifie signature + expiration. Lève 401 si invalide."""
    try:
        return jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expiré, reconnexion nécessaire")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Token invalide : {e}")


def _extract_bearer(authorization: Optional[str], token_query: Optional[str] = None) -> str:
    """Lit le JWT soit du header `Authorization: Bearer <jwt>`, soit du
    query param `?token=<jwt>` (utilisé seulement pour les GET de download
    PDF / Excel ouverts via window.open, qui n'acceptent pas de header)."""
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

    Usage :
        jwt_user, jwt_user_or_token = make_jwt_deps(get_conn)
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
    ) -> dict:
        """Dependency standard : accepte le JWT soit via header
        `Authorization: Bearer <jwt>`, soit via query `?token=<jwt>` (utilisé
        par les GET de download PDF / Excel ouverts en window.open).
        Vérifie signature + module ad_bud, auto-provisionne le user dans
        ad_budget.users, retourne le row local enrichi des modules JWT."""
        jwt_token = _extract_bearer(authorization, token)
        payload = _decode_token(jwt_token)
        _check_module(payload)
        conn = get_conn()
        try:
            user = _provision_user(conn, payload)
        finally:
            conn.close()
        user["modules"] = payload.get("modules") or []
        return user

    def jwt_admin(user: dict = Depends(jwt_user)) -> dict:
        """Dependency réservée aux admins. FastAPI dédupe Depends(jwt_user)
        si la même route le déclare aussi en param, donc 1 seul DB lookup."""
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Accès admin requis")
        return user

    # On garde jwt_user_or_token comme alias pour la lisibilité des routes
    # de download (PDF / Excel), mais c'est exactement la même fonction.
    return jwt_user, jwt_user, jwt_admin
