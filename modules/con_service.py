"""
Con Service — Client HTTP cross-service Ad BUD → Ad CON.

Sprint 1.A Ad CON — Troisième client backend→backend du codebase Adision
(après Ad VIU → Ad HUB et Ad BUD → Ad HUB). Le pattern de factorisation
en `@adision/hub-client-py` (cf. hub_service.py docstring, "règle des 3")
peut être déclenché ici, mais le scope Sprint 1.A reste minimaliste : un
seul appel `check_has_con()` avec fallback gracieux + cache 5 min.

Différence architecturale critique vs hub_service :
  hub_service raise HubServiceError sur 4xx/5xx ou réseau ;
  con_service retourne TOUJOURS un dict ({exists:false} en fallback).
Raison : Ad CON est un module en cours de Sprint 1.B, son endpoint
`/api/projects/by-bud/<id>` n'existe pas encore. On ne veut PAS qu'Ad BUD
plante quand un user ouvre un projet Adjugé.

Contrat attendu côté Ad CON (à livrer Sprint 1.B) :
  GET ${AD_CON_API_URL}/api/projects/by-bud/<bud_project_id>
    Auth : header Authorization: Bearer <JWT_USER> (HS256 partagé)
    → 200 {"exists": true, "con_project_id": "<uuid>"}    si trouvé
    → 404 {"exists": false}                                 si pas trouvé
    → 401/403                                              si auth invalide
Sprint 1.A traite 404, timeout, réseau, 5xx, JSON parse error, et tout
autre cas non-200 comme "exists=false" (defensive programming strict).

Configuration :
  - AD_CON_API_URL  : env var Railway adision-api, fallback URL publique
                      https://adision-con-api-production.up.railway.app
  - CON_TIMEOUT_S   : 2.0 s (cf. brief Sprint 1.A — bien plus court que
                      hub_service à 10s car on accepte le fallback).

Cache in-memory :
  - Dict {bud_project_id: (result_dict, cached_at_datetime)}
  - TTL 5 min (CACHE_TTL_S = 300)
  - Pas de Redis, pas de DB — au reboot du worker Railway, cache vide.
    OK car le pire cas = re-call vers Ad CON, qui re-fallback gracieux.
  - Pas thread-safe au sens GIL strict, mais FastAPI sync endpoint
    + dict atomic operations en Python = pas de race condition réelle
    sur le pattern set/get utilisé ici.
"""
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

AD_CON_API_URL = os.environ.get(
    "AD_CON_API_URL",
    "https://adision-con-api-production.up.railway.app",
)
CON_TIMEOUT_S = 2.0
CACHE_TTL_S = 300  # 5 min

# Cache module-level. Pas thread-safe au sens strict mais suffisant ici :
# 1) writes idempotents (toujours overwrite du même key), 2) reads atomiques
# en CPython (GIL). Un lock fin de granularité dict serait du surdesign.
_HAS_CON_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def _cache_get(bud_project_id) -> Optional[dict]:
    """Retourne le result du cache si valide (TTL non-expiré), sinon None."""
    key = str(bud_project_id)
    with _CACHE_LOCK:
        entry = _HAS_CON_CACHE.get(key)
    if entry is None:
        return None
    result, cached_at = entry
    age = time.monotonic() - cached_at
    if age > CACHE_TTL_S:
        return None
    # Retourne une copie pour éviter mutation accidentelle par l'appelant.
    return dict(result, _cached_at_age_s=round(age, 2))


def _cache_set(bud_project_id, result: dict) -> None:
    """Stocke le result dans le cache avec timestamp courant."""
    key = str(bud_project_id)
    with _CACHE_LOCK:
        _HAS_CON_CACHE[key] = (dict(result), time.monotonic())


def _fallback(reason: str, **extra) -> dict:
    """Construit un fallback {exists:false} avec un marker de raison
    (utile pour debug/observabilité côté Ad BUD logs)."""
    out = {"exists": False, "con_project_id": None, "_fallback_reason": reason}
    out.update(extra)
    return out


def check_has_con(jwt_token: str, bud_project_id) -> dict:
    """Vérifie si un projet Ad CON existe déjà pour un bud_project_id donné.

    GARANTIE : ne raise JAMAIS. Retourne toujours un dict {exists, con_project_id,
    [_fallback_reason]}. Si Ad CON est down/404/timeout/5xx, retourne
    {exists:false}. C'est intentionnel pour le Sprint 1.A — l'UI Ad BUD
    affichera le bouton "Démarrer suivi chantier" tant que Ad CON répond
    pas, et "Voir suivi chantier" dès que Ad CON dit oui.

    Args :
      jwt_token : JWT du user appelant, propagé tel quel vers Ad CON.
      bud_project_id : int ou str, l'id BUD du projet à checker.

    Returns :
      {
        "exists": bool,
        "con_project_id": str | None,
        "_fallback_reason": str  # SEULEMENT si fallback déclenché
        "_cached_at_age_s": float  # SEULEMENT si cache hit
      }
    """
    # 1) Cache lookup.
    cached = _cache_get(bud_project_id)
    if cached is not None:
        return cached

    # 2) Appel HTTP vers Ad CON avec fallback gracieux total.
    target_url = f"{AD_CON_API_URL}/api/projects/by-bud/{bud_project_id}"
    headers = {"Authorization": f"Bearer {jwt_token}"}

    try:
        with httpx.Client(timeout=CON_TIMEOUT_S) as client:
            r = client.get(target_url, headers=headers)
    except httpx.TimeoutException as e:
        logger.warning("con_service timeout (%ss): %s -> %s", CON_TIMEOUT_S, target_url, e)
        # Pas de cache sur timeout : on retentera au prochain call (le user
        # peut être en train d'attendre la création côté Ad CON).
        return _fallback(f"timeout_{CON_TIMEOUT_S}s")
    except httpx.RequestError as e:
        logger.warning("con_service network error: %s -> %s: %s", target_url, type(e).__name__, e)
        return _fallback(f"network_error_{type(e).__name__}")

    # 3) Traitement de la réponse HTTP.
    if r.status_code == 404:
        # Cas dominant Sprint 1.A : endpoint pas encore livré côté Ad CON
        # OU projet pas dans Ad CON. Dans les 2 cas → exists=false, cachable.
        result = {"exists": False, "con_project_id": None}
        _cache_set(bud_project_id, result)
        return result

    if r.status_code != 200:
        # 401/403/5xx : pas un cas où on peut conclure définitivement.
        # On fallback gracieusement sans cacher (pour retry au prochain call).
        logger.warning(
            "con_service unexpected status %s: %s -> body=%s",
            r.status_code, target_url, r.text[:200],
        )
        return _fallback(f"http_{r.status_code}")

    # 4) 200 — on parse le JSON et on cache.
    try:
        data = r.json()
    except Exception as e:
        logger.warning("con_service json decode failed: %s -> %s", target_url, e)
        return _fallback("json_decode_error")

    if not isinstance(data, dict) or "exists" not in data:
        logger.warning("con_service malformed response: %s -> body=%s", target_url, r.text[:200])
        return _fallback("malformed_response")

    # 5) Normalisation + cache.
    result = {
        "exists": bool(data.get("exists")),
        "con_project_id": (
            str(data["con_project_id"])
            if data.get("con_project_id") is not None else None
        ),
    }
    _cache_set(bud_project_id, result)
    return result


def cache_stats() -> dict:
    """Helper debug : taille + clés du cache. Pas exposé en endpoint
    Sprint 1.A, juste utile en logs / future debug route."""
    with _CACHE_LOCK:
        return {
            "size": len(_HAS_CON_CACHE),
            "keys": list(_HAS_CON_CACHE.keys()),
            "ttl_s": CACHE_TTL_S,
        }


def cache_clear() -> None:
    """Helper debug : purge le cache. Utile pour tests."""
    with _CACHE_LOCK:
        _HAS_CON_CACHE.clear()
