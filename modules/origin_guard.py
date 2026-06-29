"""Middleware Origin/Referer CSRF — défense en profondeur (Phase I).

Cible UNIQUEMENT les requêtes MUTANTES (POST/PUT/PATCH/DELETE) authentifiées
PAR LE COOKIE SSO `__Secure-adision-session`. Exempte AUTO tout le reste via
un discriminant unique (présence du cookie) :

  - Webhooks Stripe          → pas de cookie (server-to-server)
  - Cross-service Bearer JWT → pas de cookie (httpx ne pose pas le cookie SSO)
  - Endpoints publics token-only (Ad RES /invite/{token}, /public/checkout)
                              → pas de cookie envoyé
  - GET/HEAD/OPTIONS         → exemptés par méthode (RFC 9110 §9.2.1 safes)

C'est cette discriminant-par-cookie qui rend le middleware ROBUSTE : aucune
liste path-based à maintenir (et donc à oublier de mettre à jour).

═══════════════════════════════════════════════════════════════════
PHASE I ÉTAPE 1 — LOG-ONLY (CE MODULE NE BLOQUE RIEN)
═══════════════════════════════════════════════════════════════════

Le paramètre `log_only=True` (défaut) fait que le middleware LOGGE ce qu'il
AURAIT bloqué (warning structuré) puis LAISSE PASSER. Objectif :
collecter en prod réelle ce qui serait rejeté, sans rien casser, avant
de basculer en ENFORCE (`log_only=False`, retourne 403) en Étape 2.

═══════════════════════════════════════════════════════════════════
INSTALLATION
═══════════════════════════════════════════════════════════════════

Dans api.py, JUSTE APRÈS le `app.add_middleware(CORSMiddleware, ...)` :

    from modules.origin_guard import install_origin_guard
    install_origin_guard(app, cors_allowed_origins=CORS_ALLOWED_ORIGINS,
                         log_only=True)

═══════════════════════════════════════════════════════════════════
PROPAGATION (Phase I étape 3 — après validation Étape 2 sur app-api)
═══════════════════════════════════════════════════════════════════

Ce module doit être DUPLIQUÉ IDENTIQUE dans les 8 autres backends
FastAPI (adision-api, adision-est-api, adision-con-api, adision-tak-api,
adision-viu-api, ad-tim-api, ad-vis-api, adision_dig/backend). Audit grep
de parité périodique recommandé. Pattern documenté (helpers cookie-aware
Phase D-v2, helpers Stripe pricing Phase X).
"""
import logging
import re
from urllib.parse import urlparse

from fastapi.responses import JSONResponse


# ── Constantes (DOIVENT matcher modules/auth_jwt.py) ────────────────
SESSION_COOKIE_NAME = "__Secure-adision-session"

# Méthodes RFC 9110 §9.2.1 « safe » — exemptées par construction.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Méthodes ciblées (mutantes).
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Regex sous-domaines .adision.ca — DOIT matcher la regex CORS de api.py
# (l. ~273). Anchored ^$ + [a-z0-9-]+ bloque evil.adision.ca.attacker.com
# via fullmatch Starlette.
_ADISION_SUBDOMAIN_RE = re.compile(r"^https://([a-z0-9-]+\.)?adision\.ca$")


logger = logging.getLogger("origin_guard")


def _origin_from_referer(referer: str) -> str:
    """Extrait scheme://host (= origin) depuis un Referer header.
    Retourne '' si parsing échoue."""
    try:
        p = urlparse(referer)
        if p.scheme in ("http", "https") and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        pass
    return ""


def install_origin_guard(app, *, cors_allowed_origins, log_only=True):
    """Installe le middleware Origin/Referer CSRF sur `app`.

    Args:
        app: instance FastAPI.
        cors_allowed_origins: liste des origins explicites autorisées
            (réutilise la même que CORS — DRY, source unique de vérité).
            Doit inclure adflo.ca + go.adflo.ca pour ad-join cross-eTLD.
        log_only: si True (défaut Étape 1), LOG seulement sans bloquer.
            Si False (Étape 2+), retourne 403 sur Origin/Referer hors
            allowlist.
    """
    explicit_origins = set(cors_allowed_origins)
    mode_label = "LOG-ONLY" if log_only else "ENFORCE"
    # logger.warning (pas .info) — le root logger Python filtre les INFO
    # par défaut (Uvicorn ne propage que uvicorn.* en INFO). En WARNING,
    # le log d'install reste visible dans Railway, utile pour confirmer
    # le mode (LOG-ONLY vs ENFORCE) au boot après chaque redeploy.
    logger.warning(
        "[origin_guard] installé en mode %s — origins explicites=%d, regex=%s",
        mode_label, len(explicit_origins), _ADISION_SUBDOMAIN_RE.pattern,
    )

    def _is_allowed(origin: str) -> bool:
        if not origin:
            return False
        if origin in explicit_origins:
            return True
        return bool(_ADISION_SUBDOMAIN_RE.match(origin))

    @app.middleware("http")
    async def csrf_origin_guard(request, call_next):
        # 1. Méthodes safes (GET/HEAD/OPTIONS) — pas notre cible.
        if request.method not in MUTATING_METHODS:
            return await call_next(request)

        # 2. Pas de cookie SSO ? Auth non-cookie (webhook, Bearer s2s,
        #    public token) — exemption AUTO via discriminant unique.
        if not request.cookies.get(SESSION_COOKIE_NAME):
            return await call_next(request)

        # 3. Mutant + auth-par-cookie : vérifier Origin OU Referer.
        origin_header = request.headers.get("origin", "")
        referer_header = request.headers.get("referer", "")
        effective_origin = origin_header or _origin_from_referer(referer_header)

        # 4. NI origin NI referer : ne PAS bloquer (cf. brief).
        #    L'absence des deux n'est pas un vecteur CSRF browser ;
        #    bloquer casserait des clients légitimes (curl, scripts,
        #    extensions ayant strippé les headers, vieux browsers).
        if not effective_origin:
            return await call_next(request)

        # 5. Vérification.
        if _is_allowed(effective_origin):
            return await call_next(request)

        # 6. Origin/Referer présent ET hors allowlist : LOG ou BLOCK
        #    selon le mode.
        logger.warning(
            "[origin_guard][%s] would block: method=%s path=%s "
            "origin=%s referer=%s cookie=yes",
            mode_label,
            request.method,
            request.url.path,
            origin_header or "(none)",
            referer_header or "(none)",
        )
        if log_only:
            return await call_next(request)
        return JSONResponse(
            {"detail": "Cross-origin mutation refusée (CSRF guard)."},
            status_code=403,
        )

    # BONUS — log INFO (sans bloquer JAMAIS) les GET avec paths suspects
    # ayant le cookie : helper de détection d'éventuels GET mutants oubliés
    # par l'audit Phase E (516 GET au total ; un seul mutant /qbo/connect
    # déjà passé en POST commit d5a91fa). Si un GET /sync, /connect, /update
    # apparaît authentifié-par-cookie, c'est un drapeau pour Phase E+ audit.
    _SUSPICIOUS_GET_PATTERNS = re.compile(
        r"/(connect|sync|update|reset|toggle|enable|disable|delete|create)(\b|/)",
        re.IGNORECASE,
    )

    @app.middleware("http")
    async def suspicious_get_logger(request, call_next):
        if request.method == "GET" \
                and request.cookies.get(SESSION_COOKIE_NAME) \
                and _SUSPICIOUS_GET_PATTERNS.search(request.url.path):
            logger.info(
                "[origin_guard] GET path suspect (audit GET-mutant) : "
                "path=%s origin=%s",
                request.url.path,
                request.headers.get("origin", "(none)"),
            )
        return await call_next(request)
