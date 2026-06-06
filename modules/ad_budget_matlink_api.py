"""Ad BUD — endpoints INTERNES de liaison Ad MAT (appelés par adision_dig).

Comptage + détachement des `budget_lignes` qui référencent des items Ad MAT
(`item_id_ad_mat`, lien logique cross-base). Servent au hard-delete d'items
Ad MAT : avant de supprimer un item `items_client` (dans adision_dig), on
DÉTACHE ici les lignes budget qui le pointent (item_id_ad_mat = NULL) — la
ligne budget SURVIT (code/desc/prix conservés), elle devient « manuelle ».

Gating volontairement DIFFÉRENT du routeur /budget standard :
  - PAS de vérification du module ad_bud (un user Ad MAT n'a pas forcément Ad
    BUD → éviter les 403). Auth allégée service-à-service : signature JWT valide
    (secret partagé) + organization_id présent.
  - Le CLOISONNEMENT organization_id REMPLACE le gating module et est STRICT :
    on ne compte/détache QUE les budget_lignes des projets de l'org du JWT.
    Aucun count/detach cross-org possible.
Transactions LOCALES à ad_budget (pas de transaction distribuée — assumé).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from modules.auth_jwt import _decode_token, _extract_bearer


def register_ad_budget_matlink_routes(get_conn):
    router = APIRouter(prefix="/budget", tags=["Ad BUD — liens Ad MAT (interne)"])

    def jwt_org(authorization: Optional[str] = Header(None)) -> str:
        """Valide la signature JWT et exige organization_id. PAS de module ad_bud."""
        token = _extract_bearer(authorization, None)
        payload = _decode_token(token)
        org = payload.get("active_organization_id") or payload.get("organization_id")
        if not org:
            raise HTTPException(status_code=403, detail="JWT sans organization_id")
        return str(org)

    def _ids(data: dict):
        out = []
        for i in (data.get("item_ids") or []):
            try:
                out.append(int(i))
            except (TypeError, ValueError):
                continue
        return out

    @router.post("/lignes/count-by-mat-items")
    def count_by_mat_items(data: dict, org: str = Depends(jwt_org)):
        """Compte les items (parmi item_ids) référencés par des budget_lignes des
        projets de l'org. → {referenced_count, referenced_item_ids}."""
        ids = _ids(data)
        if not ids:
            return {"referenced_count": 0, "referenced_item_ids": []}
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT DISTINCT bl.item_id_ad_mat
                FROM ad_budget.budget_lignes bl
                JOIN ad_budget.projets p ON p.id = bl.projet_id
                WHERE bl.item_id_ad_mat = ANY(%s) AND p.organization_id = %s
                """,
                (ids, org),
            )
            refs = [r[0] for r in cur.fetchall()]
        finally:
            cur.close()
            conn.close()
        return {"referenced_count": len(refs), "referenced_item_ids": refs}

    @router.post("/lignes/detach-mat-items")
    def detach_mat_items(data: dict, org: str = Depends(jwt_org)):
        """Détache (item_id_ad_mat = NULL) les budget_lignes des projets de l'org
        qui pointent ces items. La ligne est CONSERVÉE. → {detached_count}."""
        ids = _ids(data)
        if not ids:
            return {"detached_count": 0}
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE ad_budget.budget_lignes bl
                SET item_id_ad_mat = NULL, updated_at = NOW()
                FROM ad_budget.projets p
                WHERE bl.projet_id = p.id
                  AND bl.item_id_ad_mat = ANY(%s)
                  AND p.organization_id = %s
                """,
                (ids, org),
            )
            n = cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        return {"detached_count": n}

    return router
