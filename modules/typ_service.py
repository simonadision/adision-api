"""
Typ Service — Client HTTP cross-service Ad BUD → Ad TYP (adision_dig).

Pont Ad TYP → Ad BUD (étape 1). Réplique du pattern con_service.py / hub_service.py :
client httpx, URL via env (TYP_API_URL + fallback public), JWT du user propagé
tel quel (Authorization: Bearer <JWT>, secret HS256 partagé). Ad BUD LIT le
catalogue Ad TYP (lignes validées) ; n'écrit JAMAIS dans adision_dig.

Contrat côté adision_dig (admin_typ_api, gated jwt_user — non-admin) :
  GET ${TYP_API_URL}/typ/catalogue?q=&division=&section=&limit=
      → { total, items:[{code, csi_section_code, csi_division_code, description,
                          unite, prix_mat, prix_mo, prix_st, prix_unitaire, …}] }
  GET ${TYP_API_URL}/typ/catalogue/<code>
      → ligne + composants[] (mat_source_ref, mo_rendement, mo_metier_code, qte…)

Contrairement à con_service (fallback gracieux), ce client RAISE (TypServiceError)
sur échec : on ne peut pas « snapshoter » une ligne absente — l'endpoint Ad BUD
appelant convertit en HTTPException claire.
"""
import logging
import os
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

TYP_API_URL = os.environ.get(
    "TYP_API_URL",
    "https://adisiondig-production.up.railway.app",
).rstrip("/")
TYP_TIMEOUT_S = 8.0


class TypServiceError(Exception):
    """Erreur d'appel cross-service vers Ad TYP (status HTTP + détail)."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"[{status_code}] {detail}")


def _headers(jwt_token: str) -> dict:
    return {"Authorization": f"Bearer {jwt_token}"}


def search_catalogue(jwt_token: str, *, q=None, division=None, section=None, limit=50) -> dict:
    """Liste légère du catalogue Ad TYP (lignes validées). Pour l'autocomplete."""
    params = {"limit": limit}
    if q:
        params["q"] = q
    if division:
        params["division"] = division
    if section:
        params["section"] = section
    url = f"{TYP_API_URL}/typ/catalogue"
    try:
        with httpx.Client(timeout=TYP_TIMEOUT_S) as client:
            r = client.get(url, params=params, headers=_headers(jwt_token))
    except httpx.HTTPError as e:
        logger.warning("typ_service search réseau: %s -> %s", url, e)
        raise TypServiceError(502, f"Ad TYP injoignable ({type(e).__name__})")
    if r.status_code != 200:
        raise TypServiceError(r.status_code, f"Catalogue Ad TYP HTTP {r.status_code}")
    return r.json()


def get_batch(jwt_token: str, codes) -> dict:
    """Résolution CLIENT-FIRST EN LOT de plusieurs codes Ad TYP (anti-N+1 pour
    l'onglet « MAJ » d'Ad BUD). UN appel cross-service au lieu de N get_ligne.

    Renvoie {code: entry} ; entry = {description, unite, prix_mat, prix_mo,
    prix_st, prix_unitaire, heures_unit, source}. Codes absents du catalogue
    (inactifs/non validés/supprimés) = simplement omis de la map (le caller
    traite alors la ligne comme « source introuvable », pas comme divergente)."""
    codes = [c for c in (codes or []) if c]
    if not codes:
        return {}
    url = f"{TYP_API_URL}/typ/catalogue/batch"
    try:
        with httpx.Client(timeout=TYP_TIMEOUT_S) as client:
            r = client.post(url, headers=_headers(jwt_token), json={"codes": list(codes)})
    except httpx.HTTPError as e:
        logger.warning("typ_service get_batch réseau: %s -> %s", url, e)
        raise TypServiceError(502, f"Ad TYP injoignable ({type(e).__name__})")
    if r.status_code != 200:
        raise TypServiceError(r.status_code, f"Ad TYP batch HTTP {r.status_code}")
    items = r.json().get("items", []) if isinstance(r.json(), dict) else []
    return {it["code"]: it for it in items if it.get("code")}


def get_ligne(jwt_token: str, code: str) -> dict:
    """Détail d'une ligne Ad TYP validée + ses composants (pour le mapping)."""
    url = f"{TYP_API_URL}/typ/catalogue/{urllib.parse.quote(code, safe='')}"
    try:
        with httpx.Client(timeout=TYP_TIMEOUT_S) as client:
            r = client.get(url, headers=_headers(jwt_token))
    except httpx.HTTPError as e:
        logger.warning("typ_service get_ligne réseau: %s -> %s", url, e)
        raise TypServiceError(502, f"Ad TYP injoignable ({type(e).__name__})")
    if r.status_code == 404:
        raise TypServiceError(404, "Ligne Ad TYP introuvable, inactive ou non validée")
    if r.status_code != 200:
        raise TypServiceError(r.status_code, f"Ad TYP HTTP {r.status_code}")
    return r.json()
