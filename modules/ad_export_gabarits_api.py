"""Ad BUD — Gabarits d'EXPORT PDF (rapports quantitatifs).

Un gabarit d'export = jeu réutilisable de paramètres du générateur PDF existant
(GET /budget/projets/{id}/pdf) : COLONNES incluses + options (sous-totaux,
admin&profit, taxes, prix, orientation). PARTAGÉ par organisation (scope strict
organization_id). UN seul défaut par org.

Endpoints (router prefix /budget) :
  GET    /export-gabarits                 liste (org)
  POST   /export-gabarits                 créer {nom, colonnes, options, is_default?}
  PUT    /export-gabarits/{id}            éditer
  DELETE /export-gabarits/{id}            supprimer
  POST   /export-gabarits/{id}/default    marquer défaut org (transaction)
  DELETE /export-gabarits/{id}/default    retirer le défaut

Distinct des gabarits de BUDGET (ad_budget.gabarits = structure de lignes).
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

from modules.auth_jwt import make_jwt_deps

logger = logging.getLogger(__name__)


def _org(user) -> str:
    org = user.get("organization_id")
    if not org:
        raise HTTPException(
            status_code=403,
            detail="Organisation absente du jeton — les gabarits d'export sont par organisation.",
        )
    return str(org)


def _validate_payload(data: dict):
    """Retourne (nom, colonnes_json, options_json) validés."""
    nom = (data.get("nom") or "").strip()
    if not nom:
        raise HTTPException(status_code=400, detail="Nom du gabarit requis")
    if len(nom) > 200:
        raise HTTPException(status_code=400, detail="Nom trop long (max 200)")
    colonnes = data.get("colonnes")
    if colonnes is None:
        colonnes = []
    if not isinstance(colonnes, list):
        raise HTTPException(status_code=400, detail="colonnes doit être une liste")
    # On stocke des clés string uniquement.
    colonnes = [str(c) for c in colonnes]
    options = data.get("options")
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise HTTPException(status_code=400, detail="options doit être un objet")
    return nom, json.dumps(colonnes), json.dumps(options)


def register_ad_export_gabarits_routes(get_conn):
    jwt_user, _jwt_user_or_token, _jwt_admin, _jwt_super_admin = make_jwt_deps(get_conn)
    router = APIRouter(prefix="/budget", tags=["Ad BUD — Gabarits export"])

    def _load_scoped(cur, gid, org):
        """404 si le gabarit n'existe pas dans l'org (isolation)."""
        cur.execute(
            "SELECT id FROM ad_budget.export_gabarits WHERE id = %s AND organization_id = %s",
            (gid, org),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Gabarit d'export introuvable")

    @router.get("/export-gabarits")
    def list_export_gabarits(user=Depends(jwt_user)):
        org = _org(user)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                "SELECT id, nom, colonnes, options, is_default, created_at, updated_at "
                "FROM ad_budget.export_gabarits WHERE organization_id = %s "
                "ORDER BY is_default DESC, nom",
                (org,),
            )
            return {"gabarits": cur.fetchall()}
        finally:
            cur.close()
            conn.close()

    @router.post("/export-gabarits")
    def create_export_gabarit(data: dict, user=Depends(jwt_user)):
        org = _org(user)
        nom, colonnes_j, options_j = _validate_payload(data)
        want_default = bool(data.get("is_default"))
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            if want_default:
                # Un seul défaut par org.
                cur.execute(
                    "UPDATE ad_budget.export_gabarits SET is_default = FALSE, updated_at = NOW() "
                    "WHERE organization_id = %s AND is_default",
                    (org,),
                )
            cur.execute(
                "INSERT INTO ad_budget.export_gabarits "
                "(organization_id, nom, colonnes, options, is_default, created_by) "
                "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s) RETURNING id",
                (org, nom, colonnes_j, options_j, want_default, user.get("id")),
            )
            gid = cur.fetchone()["id"]
            conn.commit()
            return {"status": "created", "id": gid}
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @router.put("/export-gabarits/{gid}")
    def update_export_gabarit(gid: int, data: dict, user=Depends(jwt_user)):
        org = _org(user)
        nom, colonnes_j, options_j = _validate_payload(data)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            _load_scoped(cur, gid, org)
            cur.execute(
                "UPDATE ad_budget.export_gabarits "
                "SET nom = %s, colonnes = %s::jsonb, options = %s::jsonb, updated_at = NOW() "
                "WHERE id = %s AND organization_id = %s",
                (nom, colonnes_j, options_j, gid, org),
            )
            conn.commit()
            return {"status": "updated", "id": gid}
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @router.delete("/export-gabarits/{gid}")
    def delete_export_gabarit(gid: int, user=Depends(jwt_user)):
        org = _org(user)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            _load_scoped(cur, gid, org)
            cur.execute(
                "DELETE FROM ad_budget.export_gabarits WHERE id = %s AND organization_id = %s",
                (gid, org),
            )
            conn.commit()
            return {"status": "deleted"}
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @router.post("/export-gabarits/{gid}/default")
    def set_default_export_gabarit(gid: int, user=Depends(jwt_user)):
        """Marque ce gabarit comme défaut DE L'ORG (un seul). Transaction :
        on retire le défaut des autres puis on pose celui-ci."""
        org = _org(user)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            _load_scoped(cur, gid, org)
            cur.execute(
                "UPDATE ad_budget.export_gabarits SET is_default = FALSE, updated_at = NOW() "
                "WHERE organization_id = %s AND is_default AND id <> %s",
                (org, gid),
            )
            cur.execute(
                "UPDATE ad_budget.export_gabarits SET is_default = TRUE, updated_at = NOW() "
                "WHERE id = %s AND organization_id = %s",
                (gid, org),
            )
            conn.commit()
            return {"status": "default_set", "id": gid}
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @router.delete("/export-gabarits/{gid}/default")
    def unset_default_export_gabarit(gid: int, user=Depends(jwt_user)):
        org = _org(user)
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE ad_budget.export_gabarits SET is_default = FALSE, updated_at = NOW() "
                "WHERE id = %s AND organization_id = %s",
                (gid, org),
            )
            conn.commit()
            return {"status": "default_unset"}
        finally:
            cur.close()
            conn.close()

    return router
