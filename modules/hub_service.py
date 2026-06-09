"""
Hub Service — Client HTTP cross-service Ad BUD → Ad HUB.

Sprint C1 — Deuxième client backend→backend du codebase Adision.
Dupliqué depuis adision-viu-api/modules/hub_service.py (Sprint 2.1).
Sera extrait en package partagé `@adision/hub-client-py` au Sprint 2.5+
(règle des 3 : Ad VIU = 1ère implém, Ad BUD = 2ème, le 3ème consommateur
déclenchera la factorisation).

Authentification : le JWT user est propagé depuis la requête HTTP
originale (Authorization: Bearer …) vers Ad HUB. Ad HUB valide la
signature avec le même JWT_SECRET (HS256 partagé) + l'organization_id
(anti-cross-tenant).

Configuration :
  - HUB_API_URL  : env var, fallback URL publique
                   https://web-production-1c52e.up.railway.app
                   (projets Railway séparés → URL publique HTTPS
                   obligatoire, DNS interne *.railway.internal
                   indisponible cross-projet).
  - HUB_TIMEOUT_S : timeout HTTP, 10 s par défaut.

Modèle : appel synchrone bloquant — l'endpoint Ad BUD qui appelle ces
fonctions attend la réponse Ad HUB avant de poursuivre (création projet
ou enrichissement GET). Pas de fire-and-forget ici, contrairement au
push de snapshot Ad VIU qui était asynchrone.
"""
import logging
import os
from typing import Optional

import httpx


logger = logging.getLogger(__name__)

HUB_API_URL = os.environ.get(
    "HUB_API_URL",
    "https://web-production-1c52e.up.railway.app",
)
HUB_TIMEOUT_S = 10.0


class HubServiceError(Exception):
    """Erreur lors d'un appel cross-service vers Ad HUB.

    Attributs :
      - status_code : code HTTP renvoyé par Ad HUB, ou None si erreur
                      réseau (DNS, timeout, refus de connexion).
      - detail      : message d'erreur lisible (extrait du body JSON
                      side Ad HUB si possible, sinon premiers 200 chars).
    """

    def __init__(self, status_code: Optional[int], detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Ad HUB error {status_code}: {detail}")


def _hub_request(
    method: str, path: str, jwt_token: str, json_body: Optional[dict] = None,
) -> Optional[dict]:
    """Appel HTTP générique vers Ad HUB avec Bearer JWT propagé.

    Retourne le JSON décodé sur 2xx, None si 204 No Content.
    Lève HubServiceError sur 4xx/5xx ou erreur réseau.
    """
    target_url = f"{HUB_API_URL}{path}"
    headers = {"Authorization": f"Bearer {jwt_token}"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    try:
        with httpx.Client(timeout=HUB_TIMEOUT_S) as client:
            r = client.request(
                method, target_url, headers=headers, json=json_body,
            )
    except httpx.RequestError as e:
        logger.error(
            "hub_service network error: %s %s -> %s",
            method, target_url, e,
        )
        raise HubServiceError(None, f"Network error: {e}") from e

    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text[:200])
        except Exception:
            detail = r.text[:200]
        logger.error(
            "hub_service %s %s -> %s : %s",
            method, target_url, r.status_code, detail,
        )
        raise HubServiceError(r.status_code, str(detail))

    if r.status_code == 204 or not r.content:
        return None
    return r.json()


def create_project(jwt_token: str, payload: dict) -> dict:
    """Crée un projet Ad HUB via POST /api/projects.

    Le payload doit suivre le contrat de l'endpoint Ad HUB
    (projects_api.py:277) : 'name' obligatoire, champs optionnels
    parmi {code, client_nom, statut, creneau, region, date_adjudication,
    superficie_m2, notes, architecte_firme, type_batiment, nombre_etages,
    budget_client_min, budget_client_max, date_debut_construction,
    date_livraison_prevue, type_mandat, modules_actifs, contrat_ced_id}.

    Ad HUB renvoie la row complète (RETURNING *) — au minimum {id, name,
    organization_id, statut, created_at}. C'est cette row qu'on stocke /
    expose côté Ad BUD.

    Lève HubServiceError si Ad HUB répond non-2xx (validation, droits,
    indispo). Le caller est responsable du rollback applicatif côté
    Ad BUD si une erreur survient APRÈS cet appel — cf. soft_delete_project.
    """
    raw = _hub_request("POST", "/api/projects", jwt_token, json_body=payload)
    # Ad HUB enveloppe la row : POST /api/projects -> {"project": {...}}.
    # On déballe (et on tolère un éventuel format plat {id,...} par robustesse).
    data = raw.get("project") if isinstance(raw, dict) and isinstance(raw.get("project"), dict) else raw
    if not data or "id" not in data:
        raise HubServiceError(
            500,
            f"Ad HUB n'a pas retourné de row projet valide : {str(raw)[:200]}",
        )
    logger.info(
        "hub_service create_project ok: hub_id=%s name=%r",
        data.get("id"), data.get("name"),
    )
    return data


def soft_delete_project(jwt_token: str, project_id: int) -> None:
    """Soft-delete un projet Ad HUB via DELETE /api/projects/{id}.

    Utilisé pour le rollback applicatif quand la création Ad BUD échoue
    APRÈS un create_project Ad HUB réussi. Best-effort : on log mais on
    ne re-raise pas — sinon on perdrait le contexte de l'erreur Ad BUD
    originale qui doit remonter à l'utilisateur.

    Ad HUB applique un soft-delete (deleted_at = NOW()) côté
    app_hub.projects — la row reste auditable, juste invisible côté UI.
    """
    try:
        _hub_request("DELETE", f"/api/projects/{project_id}", jwt_token)
        logger.info(
            "hub_service soft_delete_project ok: hub_id=%s (rollback)",
            project_id,
        )
    except HubServiceError as e:
        # On log à WARNING : le projet HUB devient orphelin (sans
        # contrepartie BUD). Pas d'auto-retry — un opérateur humain peut
        # nettoyer via l'UI Ad HUB si besoin. La cause d'erreur amont
        # (qui a déclenché ce rollback) doit remonter sans masquage.
        logger.warning(
            "hub_service soft_delete_project FAILED hub_id=%s : %s "
            "(projet HUB orphelin, à nettoyer manuellement si nécessaire)",
            project_id, e,
        )


def fetch_project(jwt_token: str, project_id: int) -> Optional[dict]:
    """Récupère les métadonnées d'un projet Ad HUB via
    GET /api/projects/{id}.

    Retourne None si Ad HUB répond 404 (projet supprimé ou inaccessible
    pour cet utilisateur — l'enrichissement côté Ad BUD doit alors
    gracieusement omettre la section ad_hub).

    Lève HubServiceError pour tout autre code d'erreur (401, 403, 5xx,
    réseau) — le caller décide d'embarquer ou non l'erreur dans sa
    réponse (cf. include_hub=true qui catch et log warning).
    """
    try:
        return _hub_request("GET", f"/api/projects/{project_id}", jwt_token)
    except HubServiceError as e:
        if e.status_code == 404:
            return None
        raise


def fetch_client(jwt_token: str, client_id: int) -> Optional[dict]:
    """Récupère les métadonnées d'un client Ad HUB via
    GET /api/clients/{id} (Sprint DT-56 D1).

    Retourne None si 404 (client supprimé/inaccessible) — comportement
    gracieux côté Ad BUD : si le client référence par client_id est
    introuvable côté Ad HUB, le caller utilise un fallback (logo
    projet ou logo Adision pour le PDF, cf. Sprint D5).

    Lève HubServiceError pour 401/403/5xx/réseau.
    """
    try:
        return _hub_request("GET", f"/api/clients/{client_id}", jwt_token)
    except HubServiceError as e:
        if e.status_code == 404:
            return None
        raise


def fetch_organization(jwt_token: str, organization_id=None) -> Optional[dict]:
    """Fiche entreprise (HUB) : name, neq, rbq, telephone, courriel, logo_url…
    via GET /api/organization. Si organization_id fourni, lit CETTE org (et non
    l'org du JWT) — un super_admin peut lire toute org ; un user normal voit la
    sienne (= celle du projet). Indispensable pour que l'entrepreneur d'un devis
    soit l'org du PROJET, pas l'org de connexion. None si 404."""
    path = "/api/organization"
    if organization_id:
        from urllib.parse import quote
        path += f"?organization_id={quote(str(organization_id))}"
    try:
        data = _hub_request("GET", path, jwt_token)
        if isinstance(data, dict) and "organization" in data:
            return data["organization"]
        return data
    except HubServiceError as e:
        if e.status_code == 404:
            return None
        raise


def fetch_project_documents(jwt_token: str, project_id: int) -> Optional[dict]:
    """Arbre des documents GED d'un projet HUB (categories > disciplines >
    documents) via GET /api/projects/{id}/documents. None si 404."""
    try:
        return _hub_request("GET", f"/api/projects/{project_id}/documents", jwt_token)
    except HubServiceError as e:
        if e.status_code == 404:
            return None
        raise


def post_report(jwt_token: str, hub_project_id: int, pdf_bytes: bytes,
                fields: dict, timeout_s: float = 30.0) -> Optional[dict]:
    """Émet un rapport vers l'Espace Rapports HUB : POST multipart (PDF + form)
    sur /api/hub/projects/{hub_project_id}/reports (Sprint Espace Rapports).

    `fields` : champs form (report_type, revision_no, revision_label,
    montant_avant_taxes, montant_apres_taxes, gabarit_id, gabarit_nom,
    source_ref, generated_by, snapshot_data). snapshot_data DOIT déjà être une
    chaîne JSON. None -> chaîne vide (le HUB applique ses défauts).

    Timeout plus long que les GET (upload PDF + écriture R2 côté HUB). Lève
    HubServiceError sur 4xx/5xx/réseau (le caller décide du rollback applicatif).
    """
    url = f"{HUB_API_URL}/api/hub/projects/{hub_project_id}/reports"
    headers = {"Authorization": f"Bearer {jwt_token}"}
    files = {"pdf": ("rapport.pdf", pdf_bytes, "application/pdf")}
    data = {k: ("" if v is None else str(v)) for k, v in fields.items()}
    try:
        with httpx.Client(timeout=timeout_s) as client:
            r = client.post(url, headers=headers, files=files, data=data)
    except httpx.RequestError as e:
        logger.error("hub_service post_report network error: %s -> %s", url, e)
        raise HubServiceError(None, f"Network error: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text[:200])
        except Exception:  # noqa: BLE001
            detail = r.text[:200]
        logger.error("hub_service post_report %s -> %s : %s", url, r.status_code, detail)
        raise HubServiceError(r.status_code, str(detail))
    return r.json() if r.content else None
