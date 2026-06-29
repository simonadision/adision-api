"""
Cache module-level de révocation JWT — Phase J3 étape 2 (satellites cat.2).

api-bud (ce module ici) et les 7 autres satellites (con, tak, viu, vis, est,
tim, dig en J3 étape 3) n'ont PAS accès direct à app_central.users (DB
séparée du hub — cf. incident J2 dans memory/phase-j-killswitch-categorie-api-bud).

Pattern cat.2 :
  - Consomme l'endpoint hub GET /auth/users/{id}/tokens-valid-from
    (Phase J3 étape 1, hub SHA 69877e1) avec X-Internal-Secret =
    INTERNAL_SERVICE_SECRET (env var partagée hub ↔ satellite).
  - Cache TTL 60s → révocation effective en ≤60s sur satellite (vs
    instantané sur hub). Compromis disponibilité vs immédiateté.
  - Fail-open BORNÉ avec préférence cache (Simon, GO J3 étape 2) :
      * cache hit frais → décide immédiatement
      * cache miss / périmé → fetch hub
      * hub OK → MAJ cache + décide
      * hub KO + cache existe (même périmé) → utilise le cache (le kill
        récent y est probablement déjà)
      * hub KO + aucun cache → FAIL-OPEN (pas révoqué), borné par
        _FAIL_OPEN_GRACE après quoi on log warning à chaque call (sans
        changer la décision : fail-closed casserait l'auth si le hub
        est durablement down).

⚠ LEÇON J2 — fail-safe ÉLARGI : tout le corps de `is_token_revoked` dans
un try/except qui catch TOUT (UndefinedColumn/Table/Schema même si on ne
fait pas de SQL ici, erreurs réseau/HTTP, toute Exception inattendue) →
log warning + return False. Ce module ne DOIT JAMAIS propager un 500 sur
le chemin auth.

⚠ Cas tvf_epoch=0 (epoch zero, user jamais révoqué) : `iat < 0` toujours
faux → pas révoqué. C'est la sémantique correcte du hub (default 0).

Cap mémoire : LRU simple (~10000 entrées max).
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple

logger = logging.getLogger("revocation_cache")

_TTL_SECONDS = 60
_FAIL_OPEN_GRACE_SECONDS = 300
_FETCH_TIMEOUT_SECONDS = 2.0
_CACHE_MAX_ENTRIES = 10_000

_HUB_BASE_URL = (
    os.environ.get("AD_HUB_BASE_URL") or "https://api.adision.ca"
).rstrip("/")

# {user_id_int: (tvf_epoch_int, last_fetch_ok_monotonic, first_fail_monotonic)}
#   tvf_epoch_int                  : valeur côté hub. 0 = jamais révoqué.
#   last_fetch_ok_monotonic        : monotonic() du dernier fetch RÉUSSI.
#                                    0 = sentinel "jamais réussi à fetch".
#   first_fail_monotonic           : monotonic() du 1er échec consécutif
#                                    (None si jamais d'échec courant).
_cache: dict = {}


def _evict_if_needed() -> None:
    """LRU simple : si cap dépassé, on jette les 10 % les plus anciens
    (par last_fetch_ok_monotonic croissant). Pas de précision LRU stricte,
    c'est un garde-fou contre la croissance illimitée."""
    if len(_cache) <= _CACHE_MAX_ENTRIES:
        return
    items = sorted(_cache.items(), key=lambda kv: kv[1][1])
    n_to_evict = max(1, len(_cache) // 10)
    for k, _ in items[:n_to_evict]:
        _cache.pop(k, None)


def _fetch_tvf_from_hub(user_id: int) -> Optional[int]:
    """Appelle l'endpoint hub. Retourne :
      - tvf_epoch (int ≥ 0) si succès
      - 0 si 404 hub (user inexistant → traiter comme jamais révoqué)
      - None si échec (réseau, timeout, 5xx, 403 = secret mal posé, etc.)
    """
    secret = os.environ.get("INTERNAL_SERVICE_SECRET", "")
    if not secret:
        logger.warning(
            "[revocation_cache] INTERNAL_SERVICE_SECRET absent côté satellite "
            "— fetch impossible (kill-switch satellite désactivé)"
        )
        return None
    url = f"{_HUB_BASE_URL}/auth/users/{int(user_id)}/tokens-valid-from"
    req = urllib.request.Request(
        url, headers={"X-Internal-Secret": secret}
    )
    try:
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return int(data.get("tokens_valid_from", 0))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 0  # user inexistant côté hub → jamais révoqué
        logger.warning(
            "[revocation_cache] hub HTTP %s pour user %s — fetch échoué",
            e.code, user_id,
        )
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.warning(
            "[revocation_cache] hub injoignable pour user %s : %s",
            user_id, type(e).__name__,
        )
        return None


def is_token_revoked(user_id, iat) -> bool:
    """Décision binaire : ce token (user_id + iat epoch) est-il révoqué ?

    Retourne True UNIQUEMENT si on a une preuve positive (tvf connue +
    iat < tvf). Dans tous les autres cas (manque d'info, hub down, erreur
    inattendue) → False (fail-open). Voir module docstring pour le détail.
    """
    try:
        if user_id is None or iat is None:
            return False
        user_id_int = int(user_id)
        iat_int = int(iat)
        now_mono = time.monotonic()
        entry = _cache.get(user_id_int)

        # 1. cache hit frais (valeur réelle, pas sentinel)
        if entry is not None:
            tvf, last_ok, _first_fail = entry
            if last_ok > 0 and (now_mono - last_ok) < _TTL_SECONDS:
                return iat_int < tvf

        # 2. fetch hub
        fresh = _fetch_tvf_from_hub(user_id_int)
        if fresh is not None:
            _cache[user_id_int] = (fresh, now_mono, None)
            _evict_if_needed()
            return iat_int < fresh

        # 3. échec fetch — préférence cache si valeur réelle dispo
        if entry is not None:
            tvf, last_ok, first_fail = entry
            if last_ok > 0:
                logger.warning(
                    "[revocation_cache] hub injoignable pour user %s — "
                    "préférence cache (tvf=%s, âge=%.0fs)",
                    user_id_int, tvf, now_mono - last_ok,
                )
                return iat_int < tvf
            # sinon = sentinel "déjà au moins 1 échec" → fall-through

        # 4. AUCUN cache exploitable + échec fetch → fail-open BORNÉ
        first_fail = (entry[2] if entry else None) or now_mono
        if (now_mono - first_fail) > _FAIL_OPEN_GRACE_SECONDS:
            logger.warning(
                "[revocation_cache] hub injoignable depuis >%ds — "
                "révocation NON vérifiable pour user %s (fail-open)",
                _FAIL_OPEN_GRACE_SECONDS, user_id_int,
            )
        _cache[user_id_int] = (0, 0, first_fail)
        _evict_if_needed()
        return False

    except Exception as e:
        # Filet ULTIME (leçon J2). Une exception dans ce module ne DOIT
        # JAMAIS casser le chemin auth — au pire on rate un kill (rare,
        # 60s de fenêtre max), mais on ne 500 jamais.
        logger.warning(
            "[revocation_cache] exception inattendue user=%s iat=%s : %s %s",
            user_id, iat, type(e).__name__, str(e)[:100],
        )
        return False
