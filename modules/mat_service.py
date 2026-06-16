"""
Mat Service — Client HTTP cross-service Ad BUD → Ad MAT (adision_dig).

Miroir de typ_service.py. Ad BUD LIT Ad MAT (prix COURANT des items liés) ;
n'écrit JAMAIS. Utilisé par l'onglet « MAJ » phase 2 : résolution EN LOT du prix
courant des items MAT liés aux lignes budget, pour détecter une dérive vs le prix
snapshoté à la pioche.

Authentification : JWT user propagé tel quel (Authorization: Bearer …), secret
HS256 partagé. Configuration : MAT_API_URL (env, fallback URL publique du domaine
Ad MAT — adisiondig-production.up.railway.app, identifié par le DOMAINE).

Contrat côté adision_dig (api_external.py) :
  POST ${MAT_API_URL}/api/mat/items/batch
       body  {"items": [{"id": int, "scope": "master"|"client"}, ...]}
       -> {"items": [{id, scope, description, unite, prix_courant}]}
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

MAT_API_URL = os.environ.get(
    "MAT_API_URL",
    "https://adisiondig-production.up.railway.app",
).rstrip("/")
MAT_TIMEOUT_S = 8.0


class MatServiceError(Exception):
    """Erreur d'appel cross-service vers Ad MAT (status HTTP + détail)."""
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"[{status_code}] {detail}")


def _headers(jwt_token: str) -> dict:
    return {"Authorization": f"Bearer {jwt_token}"}


def get_batch(jwt_token: str, refs, org=None) -> dict:
    """Résolution EN LOT du prix courant de plusieurs items Ad MAT (anti-N+1).

    `refs` = itérable de couples (id, scope) — scope ∈ {'master','client'}.
    `org` = organization_id DU PROJET (isolation cross-org) : transmis pour que la
    résolution client (items_client) se fasse sur l'org du PROJET, pas du JWT.
    Honoré uniquement pour un super_admin côté adision_dig ; ignoré sinon (= org JWT).
    Renvoie {(id, scope): {description, unite, prix_courant}}. Items absents
    (inactifs / cross-org / supprimés) = simplement omis (le caller traite alors
    la ligne comme « source introuvable », pas comme divergente). Le scope est
    OBLIGATOIRE : ids items/items_client se chevauchent → un id seul est ambigu."""
    pairs = []
    for ref in (refs or []):
        try:
            iid = int(ref[0])
        except (TypeError, ValueError, IndexError):
            continue
        scope = ref[1] if len(ref) > 1 and ref[1] in ("master", "client") else "master"
        pairs.append((iid, scope))
    if not pairs:
        return {}
    body = {"items": [{"id": i, "scope": s} for (i, s) in pairs]}
    if org:
        body["org"] = str(org)   # org du PROJET (UUID → str, JSON) — isolation cross-org
    url = f"{MAT_API_URL}/api/mat/items/batch"
    try:
        with httpx.Client(timeout=MAT_TIMEOUT_S) as client:
            r = client.post(url, headers=_headers(jwt_token), json=body)
    except httpx.HTTPError as e:
        logger.warning("mat_service get_batch réseau: %s -> %s", url, e)
        raise MatServiceError(502, f"Ad MAT injoignable ({type(e).__name__})")
    if r.status_code != 200:
        raise MatServiceError(r.status_code, f"Ad MAT batch HTTP {r.status_code}")
    data = r.json()
    items = data.get("items", []) if isinstance(data, dict) else []
    return {
        (it["id"], it.get("scope", "master")): it
        for it in items if it.get("id") is not None
    }
