"""Ad BUD — Bibliothèque de gabarits de budget (trames réutilisables).

Un GABARIT = structure (sections + lignes) SANS valeurs (qté/prix/taux),
PRIVÉE par organization_id (scope strict, pas de partage cross-org).
  - ligne 'manuelle' : description libre,
  - ligne 'ad_typ'   : code catalogue Ad TYP + description d'affichage.

Endpoints (router prefix /budget) :
  CRUD          GET/POST /gabarits, GET/PUT/DELETE /gabarits/{id},
                POST /gabarits/{id}/duplicate
  Volet 4       POST /projets/{projet_id}/insert-gabarit  (insère dans un budget,
                lignes ad_typ tarifées au catalogue COURANT ; fallback manuelle
                + warning si code introuvable ; n'efface rien)
  Volet 5       POST /gabarits/from-projet/{projet_id}     (enregistre la STRUCTURE
                d'un budget comme gabarit ; aucune valeur)

Réutilise le pont Ad TYP (typ_service + _map_typ_to_budget_cols) et
l'autorisation projet (_load_and_authorize_projet) d'ad_budget_api — pas de
duplication.
"""
import json
import logging
import os
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from psycopg.rows import dict_row

from modules import typ_service
from modules.auth_jwt import make_jwt_deps, _extract_bearer
from modules.ad_budget_api import (
    _map_typ_to_budget_cols,
    _load_and_authorize_projet,
)

logger = logging.getLogger(__name__)

# Taxonomie CSI MasterFormat servie par Ad EST (publique, /reference/csi-sections).
EST_API_URL = os.environ.get(
    "EST_API_URL", "https://adision-est-api-production.up.railway.app"
).rstrip("/")


def _org(user) -> str:
    """organization_id du jeton (scope privé strict). 403 si absent."""
    org = user.get("organization_id")
    if not org:
        raise HTTPException(
            status_code=403,
            detail="Organisation absente du jeton — les gabarits sont privés par organisation.",
        )
    return str(org)


def register_ad_gabarits_routes(get_conn):
    jwt_user, _jwt_user_or_token, _jwt_admin, jwt_super_admin = make_jwt_deps(get_conn)
    router = APIRouter(prefix="/budget", tags=["Ad BUD — Gabarits"])

    # ─── Helpers internes ─────────────────────────────────────────────────
    def _load_gabarit_scoped(cur, gabarit_id, org):
        """Charge l'en-tête d'un gabarit en SCOPE STRICT org. 404 sinon
        (vaut aussi pour un gabarit d'une autre org → invisible)."""
        cur.execute(
            "SELECT id, organization_id, nom, description, created_at, updated_at "
            "FROM ad_budget.gabarits WHERE id = %s AND organization_id = %s",
            (gabarit_id, org),
        )
        g = cur.fetchone()
        if not g:
            raise HTTPException(status_code=404, detail="Gabarit introuvable")
        return g

    def _full_gabarit(cur, gabarit_id):
        """Hiérarchie 3 niveaux ordonnée (gabarit déjà autorisé) :
        divisions (gabarit_sections) → sous_sections → lignes."""
        # Niveau 1 — divisions.
        cur.execute(
            "SELECT id, nom_section, numero, ordre FROM ad_budget.gabarit_sections "
            "WHERE gabarit_id = %s ORDER BY ordre, id",
            (gabarit_id,),
        )
        divisions = cur.fetchall()
        div_ids = [d["id"] for d in divisions]

        # Niveau 2 — sous-sections (par division).
        ss_all = []
        ss_by_div = {did: [] for did in div_ids}
        if div_ids:
            cur.execute(
                "SELECT id, gabarit_section_id, code_csi, libelle, ordre "
                "FROM ad_budget.gabarit_sous_sections "
                "WHERE gabarit_section_id = ANY(%s) ORDER BY ordre, id",
                (div_ids,),
            )
            ss_all = cur.fetchall()
            for ss in ss_all:
                ss_by_div[ss["gabarit_section_id"]].append(ss)

        # Niveau 3 — lignes (par sous-section).
        ss_ids = [ss["id"] for ss in ss_all]
        lignes_by_ss = {sid: [] for sid in ss_ids}
        if ss_ids:
            cur.execute(
                "SELECT id, gabarit_sous_section_id, ordre, type, description, code_typ "
                "FROM ad_budget.gabarit_lignes WHERE gabarit_sous_section_id = ANY(%s) "
                "ORDER BY ordre, id",
                (ss_ids,),
            )
            for ln in cur.fetchall():
                lignes_by_ss[ln["gabarit_sous_section_id"]].append(ln)

        for ss in ss_all:
            ss["lignes"] = lignes_by_ss.get(ss["id"], [])
        for d in divisions:
            d["sous_sections"] = ss_by_div.get(d["id"], [])
        return divisions

    def _replace_structure(cur, gabarit_id, sections):
        """Remplace TOUTE la structure 3 niveaux (CASCADE delete puis recrée).
        `sections` = divisions : chacune {numero, nom_section, ordre, sous_sections};
        chaque sous-section {code_csi, libelle, ordre, lignes}."""
        cur.execute(
            "DELETE FROM ad_budget.gabarit_sections WHERE gabarit_id = %s",
            (gabarit_id,),
        )
        for si, sec in enumerate(sections or []):
            cur.execute(
                "INSERT INTO ad_budget.gabarit_sections "
                "(gabarit_id, nom_section, numero, ordre) VALUES (%s, %s, %s, %s) "
                "RETURNING id",
                (
                    gabarit_id,
                    (sec.get("nom_section") or "").strip(),
                    (sec.get("numero") or "").strip() or None,
                    sec.get("ordre", si),
                ),
            )
            div_id = cur.fetchone()["id"]
            for ssi, ss in enumerate(sec.get("sous_sections") or []):
                cur.execute(
                    "INSERT INTO ad_budget.gabarit_sous_sections "
                    "(gabarit_section_id, code_csi, libelle, ordre) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (
                        div_id,
                        (ss.get("code_csi") or "").strip() or None,
                        (ss.get("libelle") or "").strip(),
                        ss.get("ordre", ssi),
                    ),
                )
                ss_id = cur.fetchone()["id"]
                for li, lg in enumerate(ss.get("lignes") or []):
                    typ = lg.get("type")
                    if typ not in ("manuelle", "ad_typ"):
                        typ = "ad_typ" if lg.get("code_typ") else "manuelle"
                    code = (lg.get("code_typ") or "").strip() or None
                    if typ != "ad_typ":
                        code = None
                    cur.execute(
                        "INSERT INTO ad_budget.gabarit_lignes "
                        "(gabarit_sous_section_id, ordre, type, description, code_typ) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (ss_id, lg.get("ordre", li), typ,
                         (lg.get("description") or "").strip(), code),
                    )

    def _insert_manual(cur, projet_id, section, description):
        """Ligne manuelle dans un budget : valeurs vides (à remplir)."""
        cur.execute(
            "INSERT INTO ad_budget.budget_lignes "
            "(projet_id, section, description, unite, prix_unitaire, qte, actif, taux_horaire) "
            "VALUES (%s, %s, %s, 'global', 0, 0, TRUE, 0)",
            (projet_id, section, description),
        )

    # ─── Taxonomie CSI pour les sélecteurs de l'éditeur (2 niveaux) ───────
    # Réutilise la taxonomie CSI MasterFormat EXISTANTE d'Ad EST/Ad MAT
    # (Ad EST /reference/csi-sections, publique — PAS de liste CSI parallèle).
    # Proxy serveur (pas de CORS). Best-effort : Ad EST indispo → liste vide
    # (le sélecteur reste vide, l'éditeur ne plante pas).
    def _fetch_csi_filtered(suffix):
        """Codes de la taxonomie Ad EST finissant par `suffix`, en {code, libelle}.
        suffix=' 00'    → NIVEAU SECTION fine (06 40 00, 09 91 00…) — sous-sections.
        suffix=' 00 00' → NIVEAU DIVISION    (06 00 00, 09 00 00…) — divisions (34)."""
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{EST_API_URL}/reference/csi-sections")
            r.raise_for_status()
            raw = r.json().get("csi_sections", [])
        except Exception as e:  # noqa: BLE001 — dégradation gracieuse
            logger.warning("csi : taxonomie Ad EST injoignable : %s", e)
            return None
        out = []
        for s in raw:
            code = (s.get("code") or "").strip()
            if code.endswith(suffix):
                out.append({"code": code, "libelle": s.get("label_fr") or ""})
        return out

    # Sous-sections = sections fines MasterFormat (codes "XX YY 00", ~1732).
    @router.get("/csi-sections")
    def list_csi_sections(user=Depends(jwt_user)):
        out = _fetch_csi_filtered(" 00")
        return {"sections": out if out is not None else []}

    # Divisions = niveau division MasterFormat (codes "XX 00 00", 34) — niveau 1.
    @router.get("/csi-divisions")
    def list_csi_divisions(user=Depends(jwt_user)):
        out = _fetch_csi_filtered(" 00 00")
        return {"divisions": out if out is not None else []}

    # ─── CRUD ─────────────────────────────────────────────────────────────
    @router.get("/gabarits")
    def list_gabarits(user=Depends(jwt_user)):
        org = _org(user)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            # is_default : LEFT JOIN sur le défaut DE CE USER (par-user, pas org).
            cur.execute(
                """
                SELECT g.id, g.nom, g.description, g.created_at, g.updated_at,
                       COUNT(DISTINCT s.id)  AS nb_sections,
                       COUNT(DISTINCT ss.id) AS nb_sous_sections,
                       COUNT(l.id)           AS nb_lignes,
                       (d.user_id IS NOT NULL) AS is_default,
                       g.est_master,
                       (g.source_master_gabarit_id IS NOT NULL) AS is_pushed_copy
                FROM ad_budget.gabarits g
                LEFT JOIN ad_budget.gabarit_sections s       ON s.gabarit_id = g.id
                LEFT JOIN ad_budget.gabarit_sous_sections ss ON ss.gabarit_section_id = s.id
                LEFT JOIN ad_budget.gabarit_lignes  l        ON l.gabarit_sous_section_id = ss.id
                LEFT JOIN ad_budget.user_gabarit_defaut d
                       ON d.gabarit_id = g.id AND d.user_id = %s
                WHERE g.organization_id = %s
                GROUP BY g.id, d.user_id
                ORDER BY g.updated_at DESC, g.id DESC
                """,
                (user["id"], org),
            )
            return {"gabarits": cur.fetchall()}
        finally:
            cur.close()
            conn.close()

    # ─── Gabarit PAR DÉFAUT (par user — un seul) ──────────────────────────
    @router.get("/gabarits/default")
    def get_default_gabarit(user=Depends(jwt_user)):
        """Gabarit par défaut DU USER courant (scopé à son org). null sinon."""
        org = _org(user)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                "SELECT d.gabarit_id, g.nom FROM ad_budget.user_gabarit_defaut d "
                "JOIN ad_budget.gabarits g "
                "  ON g.id = d.gabarit_id AND g.organization_id = %s "
                "WHERE d.user_id = %s",
                (org, user["id"]),
            )
            row = cur.fetchone()
            return {"gabarit_id": row["gabarit_id"], "nom": row["nom"]} if row \
                else {"gabarit_id": None, "nom": None}
        finally:
            cur.close()
            conn.close()

    # ─── Partage MASTER (super_admin) — pattern push-master réversible ────────
    # Calque le carnet Ad RES (push descendant + copie réversible + audit/rollback)
    # dans ad_budget. Marquer un gabarit MASTER, le pousser (deep-copy) vers org(s)
    # cible(s), historiser, annuler. PAS de lien vivant, PAS de consentement.
    def _load_gabarit_any(cur, gabarit_id):
        """Charge un gabarit SANS scope org (super_admin transverse). 404 sinon."""
        cur.execute(
            "SELECT id, organization_id, nom, description, est_master "
            "FROM ad_budget.gabarits WHERE id = %s", (gabarit_id,))
        g = cur.fetchone()
        if not g:
            raise HTTPException(status_code=404, detail="Gabarit introuvable")
        return g

    def _copy_gabarit_to_org(cur, master, target_org, push_id):
        """Deep-copy d'un gabarit master vers target_org (copie indépendante,
        traçable). Réutilise _full_gabarit (lecture arbre) + _replace_structure."""
        cur.execute(
            "INSERT INTO ad_budget.gabarits "
            "(organization_id, nom, description, est_master, source_master_gabarit_id, push_id) "
            "VALUES (%s, %s, %s, FALSE, %s, %s) RETURNING id",
            (target_org, master["nom"], master.get("description"), master["id"], push_id))
        new_id = cur.fetchone()["id"]
        _replace_structure(cur, new_id, _full_gabarit(cur, master["id"]))
        return new_id

    @router.patch("/gabarits/{gabarit_id}/master")
    def set_gabarit_master(gabarit_id: int, data: dict, _su=Depends(jwt_super_admin)):
        """Marque/démarque un gabarit comme MASTER (partageable). super_admin only."""
        est_master = bool((data or {}).get("est_master"))
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            _load_gabarit_any(cur, gabarit_id)
            cur.execute(
                "UPDATE ad_budget.gabarits SET est_master = %s, updated_at = NOW() "
                "WHERE id = %s RETURNING id, est_master", (est_master, gabarit_id))
            row = cur.fetchone()
            conn.commit()
            return {"id": row["id"], "est_master": row["est_master"]}
        finally:
            cur.close(); conn.close()

    @router.post("/gabarits/{gabarit_id}/push-to-org")
    def push_gabarit_to_org(gabarit_id: int, data: dict, su=Depends(jwt_super_admin)):
        """Pousse (deep-copy réversible) un gabarit MASTER vers une/des org(s).
        Body { target_organization_ids:[uuid…] | target_organization_id, mode }.
        mode 'additif' (défaut) = skip si déjà poussé chez la cible ; 'replace' =
        remplace la copie existante par la version master courante. Un push_id +
        une ligne gabarit_push_log par org cible (rollback granulaire)."""
        data = data or {}
        targets = data.get("target_organization_ids")
        if not targets and data.get("target_organization_id"):
            targets = [data["target_organization_id"]]
        mode = (data.get("mode") or "additif").lower()
        if mode not in ("additif", "replace"):
            mode = "additif"
        if not targets:
            raise HTTPException(status_code=400, detail="target_organization_ids requis")
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            master = _load_gabarit_any(cur, gabarit_id)
            source_org = str(master["organization_id"])
            results = []
            for raw in targets:
                target_org = str(raw)
                if target_org == source_org:
                    results.append({"org": target_org, "error": "source = cible (flux descendant)"})
                    continue
                # Copies déjà poussées de CE master chez cette org.
                cur.execute(
                    "SELECT id FROM ad_budget.gabarits "
                    "WHERE organization_id = %s AND source_master_gabarit_id = %s",
                    (target_org, gabarit_id))
                existing = [r["id"] for r in cur.fetchall()]
                # Log AVANT → obtient push_id.
                cur.execute(
                    "INSERT INTO ad_budget.gabarit_push_log "
                    "(org_source, org_cible, gabarit_master_id, pushed_by_user_id, "
                    " pushed_by_email, snapshot_before) "
                    "VALUES (%s,%s,%s,%s,%s,%s::jsonb) RETURNING push_id",
                    (source_org, target_org, gabarit_id, su.get("sub"),
                     su.get("email"), json.dumps([str(i) for i in existing])))
                push_id = cur.fetchone()["push_id"]
                inserted = replaced = skipped = 0
                new_ids = []
                if existing and mode == "additif":
                    skipped = len(existing)            # déjà poussé → ne pas dupliquer
                else:
                    # Défauts personnels (par-user) pointant sur les copies à
                    # REMPLACER : la FK user_gabarit_defaut → gabarits ON DELETE
                    # CASCADE les effacerait au DELETE ci-dessous (le « par défaut »
                    # se décocherait tout seul au re-push). On les CAPTURE pour les
                    # RE-POINTER vers la nouvelle copie (même org, mêmes users).
                    carried_default_users = []
                    if existing and mode == "replace":
                        cur.execute(
                            "SELECT user_id FROM ad_budget.user_gabarit_defaut "
                            "WHERE gabarit_id = ANY(%s)", (existing,))
                        carried_default_users = [r["user_id"] for r in cur.fetchall()]
                        cur.execute("DELETE FROM ad_budget.gabarits WHERE id = ANY(%s)", (existing,))
                        replaced = len(existing)
                    new_id = _copy_gabarit_to_org(cur, master, target_org, push_id)
                    new_ids.append(new_id)
                    inserted = 1
                    # Le défaut survit au re-push : on le re-pointe vers la copie
                    # fraîche (sinon perdu par la cascade ci-dessus).
                    for uid in carried_default_users:
                        cur.execute(
                            "INSERT INTO ad_budget.user_gabarit_defaut "
                            "(user_id, gabarit_id, organization_id, updated_at) "
                            "VALUES (%s, %s, %s, NOW()) "
                            "ON CONFLICT (user_id) DO UPDATE SET "
                            "  gabarit_id = EXCLUDED.gabarit_id, "
                            "  organization_id = EXCLUDED.organization_id, updated_at = NOW()",
                            (uid, new_id, target_org))
                cur.execute(
                    "UPDATE ad_budget.gabarit_push_log SET count_inserted=%s, count_replaced=%s, "
                    "count_skipped=%s, snapshot_after=%s::jsonb WHERE push_id=%s",
                    (inserted, replaced, skipped, json.dumps([str(i) for i in new_ids]), push_id))
                results.append({"org": target_org, "push_id": str(push_id),
                                "inserted": inserted, "replaced": replaced, "skipped": skipped})
            conn.commit()
            return {"results": results}
        except HTTPException:
            conn.rollback(); raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Push refusé : {e}")
        finally:
            cur.close(); conn.close()

    @router.get("/gabarits/push-log")
    def gabarit_push_log(_su=Depends(jwt_super_admin)):
        """Historique des pushes (audit). super_admin only."""
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                "SELECT push_id, org_source, org_cible, gabarit_master_id, pushed_by_email, "
                "count_inserted, count_replaced, count_skipped, created_at, "
                "rolled_back_at, rolled_back_by_email "
                "FROM ad_budget.gabarit_push_log ORDER BY created_at DESC LIMIT 200")
            return {"push_log": [{
                "push_id": str(r["push_id"]),
                "org_source": str(r["org_source"]), "org_cible": str(r["org_cible"]),
                "gabarit_master_id": r["gabarit_master_id"],
                "pushed_by_email": r["pushed_by_email"],
                "count_inserted": r["count_inserted"], "count_replaced": r["count_replaced"],
                "count_skipped": r["count_skipped"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "rolled_back_at": r["rolled_back_at"].isoformat() if r["rolled_back_at"] else None,
                "rolled_back_by_email": r["rolled_back_by_email"],
            } for r in cur.fetchall()]}
        finally:
            cur.close(); conn.close()

    @router.post("/gabarits/push-rollback")
    def gabarit_push_rollback(data: dict, su=Depends(jwt_super_admin)):
        """Annule un push : supprime les copies insérées par ce push_id (réversible).
        super_admin only. Les gabarits PRIVÉS du client et les autres push intacts."""
        push_id = (data or {}).get("push_id")
        if not push_id:
            raise HTTPException(status_code=400, detail="push_id requis")
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute("SELECT push_id, rolled_back_at FROM ad_budget.gabarit_push_log "
                        "WHERE push_id = %s", (push_id,))
            log = cur.fetchone()
            if not log:
                raise HTTPException(status_code=404, detail="Push introuvable")
            if log["rolled_back_at"]:
                raise HTTPException(status_code=409, detail="Push déjà annulé")
            cur.execute("DELETE FROM ad_budget.gabarits WHERE push_id = %s", (push_id,))
            deleted = cur.rowcount
            cur.execute("UPDATE ad_budget.gabarit_push_log SET rolled_back_at = NOW(), "
                        "rolled_back_by_email = %s WHERE push_id = %s", (su.get("email"), push_id))
            conn.commit()
            return {"deleted": deleted}
        except HTTPException:
            conn.rollback(); raise
        finally:
            cur.close(); conn.close()

    @router.post("/gabarits/{gabarit_id}/default")
    def set_default_gabarit(gabarit_id: int, user=Depends(jwt_user)):
        """Marque ce gabarit comme défaut DU USER (UPSERT → un seul défaut :
        écrase le précédent défaut de ce user). Le gabarit doit être de l'org."""
        org = _org(user)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            _load_gabarit_scoped(cur, gabarit_id, org)  # 404 si autre org
            cur.execute(
                "INSERT INTO ad_budget.user_gabarit_defaut "
                "(user_id, gabarit_id, organization_id, updated_at) "
                "VALUES (%s, %s, %s, NOW()) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "  gabarit_id = EXCLUDED.gabarit_id, "
                "  organization_id = EXCLUDED.organization_id, updated_at = NOW()",
                (user["id"], gabarit_id, org),
            )
            conn.commit()
            return {"status": "default_set", "gabarit_id": gabarit_id}
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @router.delete("/gabarits/{gabarit_id}/default")
    def unset_default_gabarit(gabarit_id: int, user=Depends(jwt_user)):
        """Retire le défaut DU USER si ce gabarit en était le défaut."""
        _org(user)
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "DELETE FROM ad_budget.user_gabarit_defaut "
                "WHERE user_id = %s AND gabarit_id = %s",
                (user["id"], gabarit_id),
            )
            conn.commit()
            return {"status": "default_unset"}
        finally:
            cur.close()
            conn.close()

    @router.post("/gabarits")
    def create_gabarit(data: dict, user=Depends(jwt_user)):
        org = _org(user)
        nom = (data.get("nom") or "").strip()
        if not nom:
            raise HTTPException(status_code=400, detail="Nom du gabarit requis")
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                "INSERT INTO ad_budget.gabarits (organization_id, nom, description) "
                "VALUES (%s, %s, %s) RETURNING id",
                (org, nom, (data.get("description") or "").strip() or None),
            )
            gid = cur.fetchone()["id"]
            if data.get("sections"):
                _replace_structure(cur, gid, data["sections"])
            conn.commit()
            return {"status": "created", "id": gid}
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @router.get("/gabarits/{gabarit_id}")
    def get_gabarit(gabarit_id: int, user=Depends(jwt_user)):
        org = _org(user)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            g = _load_gabarit_scoped(cur, gabarit_id, org)
            g["sections"] = _full_gabarit(cur, gabarit_id)
            return g
        finally:
            cur.close()
            conn.close()

    @router.put("/gabarits/{gabarit_id}")
    def update_gabarit(gabarit_id: int, data: dict, user=Depends(jwt_user)):
        org = _org(user)
        nom = (data.get("nom") or "").strip()
        if not nom:
            raise HTTPException(status_code=400, detail="Nom du gabarit requis")
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            _load_gabarit_scoped(cur, gabarit_id, org)
            cur.execute(
                "UPDATE ad_budget.gabarits SET nom=%s, description=%s, updated_at=NOW() "
                "WHERE id=%s AND organization_id=%s",
                (nom, (data.get("description") or "").strip() or None, gabarit_id, org),
            )
            if "sections" in data:
                _replace_structure(cur, gabarit_id, data["sections"])
            conn.commit()
            return {"status": "updated"}
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @router.delete("/gabarits/{gabarit_id}")
    def delete_gabarit(gabarit_id: int, user=Depends(jwt_user)):
        org = _org(user)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            _load_gabarit_scoped(cur, gabarit_id, org)
            cur.execute(
                "DELETE FROM ad_budget.gabarits WHERE id=%s AND organization_id=%s",
                (gabarit_id, org),
            )
            conn.commit()
            return {"status": "deleted"}
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @router.post("/gabarits/{gabarit_id}/duplicate")
    def duplicate_gabarit(gabarit_id: int, user=Depends(jwt_user)):
        org = _org(user)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            g = _load_gabarit_scoped(cur, gabarit_id, org)
            sections = _full_gabarit(cur, gabarit_id)
            cur.execute(
                "INSERT INTO ad_budget.gabarits (organization_id, nom, description) "
                "VALUES (%s, %s, %s) RETURNING id",
                (org, (g["nom"] + " (copie)")[:200], g["description"]),
            )
            gid = cur.fetchone()["id"]
            _replace_structure(cur, gid, sections)
            conn.commit()
            return {"status": "duplicated", "id": gid}
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    # ─── Volet 5 : enregistrer un budget comme gabarit (structure seule) ───
    @router.post("/gabarits/from-projet/{projet_id}")
    def gabarit_from_projet(projet_id: int, data: dict, user=Depends(jwt_user)):
        org = _org(user)
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        nom = (data.get("nom") or "").strip()
        if not nom:
            raise HTTPException(status_code=400, detail="Nom du gabarit requis")
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                "SELECT section, description, source_typ_code, actif "
                "FROM ad_budget.budget_lignes WHERE projet_id=%s ORDER BY id",
                (projet_id,),
            )
            # Le budget est PLAT (lignes portant un code CSI fin). On reconstruit la
            # hiérarchie 3 niveaux : chaque code de ligne devient une SOUS-SECTION
            # (section fine), regroupée sous la DIVISION dérivée de ses 2 premiers
            # chiffres ("09 91 00" → division "09 00 00"). Ordre d'apparition préservé.
            order, by_section = [], {}
            for r in cur.fetchall():
                if not r.get("actif", True):
                    continue
                sec = (r.get("section") or "").strip() or "Divers"
                if sec not in by_section:
                    by_section[sec] = []
                    order.append(sec)
                code = (r.get("source_typ_code") or "").strip() or None
                by_section[sec].append({
                    "type": "ad_typ" if code else "manuelle",
                    "description": r.get("description") or "",
                    "code_typ": code,
                })

            def _division_of(code_csi):
                """'09 91 00' → '09 00 00' ; code non-CSI → None (→ division 'Divers')."""
                m = re.match(r"^\s*(\d{2})\b", code_csi or "")
                return f"{m.group(1)} 00 00" if m else None

            # Groupe les sous-sections par division (ordre d'apparition préservé).
            div_order, div_sous = [], {}
            for sec in order:
                dc = _division_of(sec) or "Divers"
                if dc not in div_sous:
                    div_sous[dc] = []
                    div_order.append(dc)
                div_sous[dc].append(sec)

            # Libellés CSI officiels (ad_budget_prix_moyens) pour sous-sections ET
            # divisions ; code custom non catalogué → libellé vide (fallback).
            wanted = [c for c in (order + [d for d in div_order if d != "Divers"]) if c]
            labels = {}
            if wanted:
                cur.execute(
                    "SELECT DISTINCT section, description FROM ad_budget.ad_budget_prix_moyens "
                    "WHERE section = ANY(%s)",
                    (wanted,),
                )
                labels = {r["section"]: r["description"] for r in cur.fetchall()}

            structure = [
                {
                    "numero": None if dc == "Divers" else dc,
                    "nom_section": labels.get(dc, ""),
                    "ordre": i,
                    "sous_sections": [
                        {"code_csi": None if sec == "Divers" else sec,
                         "libelle": labels.get(sec, ""),
                         "ordre": j,
                         "lignes": [{**ln, "ordre": k}
                                    for k, ln in enumerate(by_section[sec])]}
                        for j, sec in enumerate(div_sous[dc])
                    ],
                }
                for i, dc in enumerate(div_order)
            ]
            cur.execute(
                "INSERT INTO ad_budget.gabarits (organization_id, nom, description) "
                "VALUES (%s, %s, %s) RETURNING id",
                (org, nom, (data.get("description") or "").strip() or None),
            )
            gid = cur.fetchone()["id"]
            _replace_structure(cur, gid, structure)
            conn.commit()
            return {"status": "created", "id": gid, "nb_sections": len(structure)}
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    # ─── Volet 4 : insérer un gabarit dans un budget ──────────────────────
    @router.post("/projets/{projet_id}/insert-gabarit")
    def insert_gabarit(projet_id: int, data: dict,
                       authorization: Optional[str] = Header(None),
                       user=Depends(jwt_user)):
        org = _org(user)
        _load_and_authorize_projet(get_conn, projet_id, user, "write")
        gabarit_id = data.get("gabarit_id")
        if not gabarit_id:
            raise HTTPException(status_code=400, detail="gabarit_id requis")
        jwt_token = _extract_bearer(authorization, None)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            _load_gabarit_scoped(cur, gabarit_id, org)  # scope strict
            sections = _full_gabarit(cur, gabarit_id)
            inserted, warnings = 0, []
            for div in sections:
                # Code de division (fallback pour les sous-sections sans code CSI).
                div_code = ((div.get("numero") or div.get("nom_section") or "").strip())
                for ss in div.get("sous_sections", []):
                    # Section du budget = code CSI de la SOUS-SECTION (section fine,
                    # ex. "09 91 00") → se regroupe correctement par 2 paires avec
                    # le regroupement budget ACTUEL (Temps 1). Fallback : code de la
                    # division, sinon "Divers". On respecte l'organisation du gabarit.
                    sec_code = ((ss.get("code_csi") or "").strip()
                                or div_code or "Divers")
                    for lg in ss["lignes"]:
                        desc = lg.get("description") or ""
                        code = (lg.get("code_typ") or "").strip() or None
                        if lg.get("type") == "ad_typ" and code:
                            try:
                                typ = typ_service.get_ligne(jwt_token, code)
                            except typ_service.TypServiceError as e:
                                if e.status_code == 404:
                                    # Introuvable → ligne manuelle + signalement.
                                    warnings.append({
                                        "code": code, "description": desc,
                                        "message": "item catalogue introuvable, inséré en ligne manuelle",
                                    })
                                    _insert_manual(cur, projet_id, sec_code, desc)
                                    inserted += 1
                                    continue
                                # Indispo (502, etc.) : on abandonne proprement.
                                raise HTTPException(
                                    status_code=502,
                                    detail=f"Ad TYP indisponible : {e.detail}",
                                )
                            # Tarifé au catalogue COURANT mais à QTÉ 0 (défaut Ad BUD :
                            # une ligne neuve naît sans quantité). prix_unitaire (par
                            # unité) est préservé → quand l'user saisit une qté, MAT
                            # scale live et MO/ST sont re-snapshotés via apply-typ.
                            m = _map_typ_to_budget_cols(typ, 0)
                            # section = CODE CSI COMPLET de l'item (m["section"] =
                            # typ["code"], ex. « 06 40 00.01 »), comme apply-typ —
                            # PAS le code de sous-section du gabarit (sec_code).
                            # Le regroupement budget dérive division/sous-section
                            # des 4 premiers chiffres → le suffixe « .01 » n'altère
                            # pas le rangement. Fallback sec_code si m["section"] vide.
                            line_section = m["section"] or sec_code
                            cur.execute(
                                """
                                INSERT INTO ad_budget.budget_lignes
                                (projet_id, section, description, unite, prix_unitaire, qte,
                                 heures, taux_horaire, sous_traitant_montant,
                                 ajust_materiaux, ajust_main_oeuvre, ajust_sous_traitant, actif,
                                 source_typ_code, source_typ_snapshot_at)
                                VALUES (%s,%s,%s,%s,%s,0,%s,%s,%s,0,0,0,TRUE,%s,NOW())
                                """,
                                (projet_id, line_section, desc or m["description"], m["unite"],
                                 m["prix_unitaire"], m["heures"], m["taux_horaire"],
                                 m["sous_traitant_montant"], code),
                            )
                            inserted += 1
                        else:
                            _insert_manual(cur, projet_id, sec_code, desc)
                            inserted += 1
            conn.commit()
            return {"status": "inserted", "nb_lignes": inserted, "warnings": warnings}
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    return router
