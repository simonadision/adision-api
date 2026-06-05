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
import logging
import os
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
    jwt_user, _jwt_user_or_token, _jwt_admin, _ = make_jwt_deps(get_conn)
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
        """Sections + lignes ordonnées (gabarit déjà autorisé)."""
        cur.execute(
            "SELECT id, nom_section, numero, ordre FROM ad_budget.gabarit_sections "
            "WHERE gabarit_id = %s ORDER BY ordre, id",
            (gabarit_id,),
        )
        sections = cur.fetchall()
        sec_ids = [s["id"] for s in sections]
        lignes_by_sec = {sid: [] for sid in sec_ids}
        if sec_ids:
            cur.execute(
                "SELECT id, gabarit_section_id, ordre, type, description, code_typ "
                "FROM ad_budget.gabarit_lignes WHERE gabarit_section_id = ANY(%s) "
                "ORDER BY ordre, id",
                (sec_ids,),
            )
            for ln in cur.fetchall():
                lignes_by_sec[ln["gabarit_section_id"]].append(ln)
        for s in sections:
            s["lignes"] = lignes_by_sec.get(s["id"], [])
        return sections

    def _replace_structure(cur, gabarit_id, sections):
        """Remplace TOUTES les sections/lignes (CASCADE delete puis recrée)."""
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
            sec_id = cur.fetchone()["id"]
            for li, lg in enumerate(sec.get("lignes") or []):
                typ = lg.get("type")
                if typ not in ("manuelle", "ad_typ"):
                    typ = "ad_typ" if lg.get("code_typ") else "manuelle"
                code = (lg.get("code_typ") or "").strip() or None
                if typ != "ad_typ":
                    code = None
                cur.execute(
                    "INSERT INTO ad_budget.gabarit_lignes "
                    "(gabarit_section_id, ordre, type, description, code_typ) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (sec_id, lg.get("ordre", li), typ,
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

    # ─── Taxonomie CSI (sections) pour le sélecteur de l'éditeur ──────────
    # Réutilise la taxonomie CSI MasterFormat EXISTANTE d'Ad EST/Ad MAT
    # (Ad EST /reference/csi-sections, publique — PAS de liste CSI parallèle),
    # filtrée au NIVEAU SECTION : on ne garde que les codes finissant en " 00"
    # (sections MasterFormat type 06 00 00, 06 40 00, 07 20 00…), pas les items
    # fins (01 90 10…) ni les divisions à 2 chiffres. Proxy serveur (pas de
    # CORS). Best-effort : si Ad EST est indispo, on renvoie une liste vide
    # (le sélecteur reste vide, l'éditeur ne plante pas).
    @router.get("/csi-sections")
    def list_csi_sections(user=Depends(jwt_user)):
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{EST_API_URL}/reference/csi-sections")
            r.raise_for_status()
            raw = r.json().get("csi_sections", [])
        except Exception as e:  # noqa: BLE001 — dégradation gracieuse
            logger.warning("csi-sections : taxonomie Ad EST injoignable : %s", e)
            return {"sections": []}
        out = []
        for s in raw:
            code = (s.get("code") or "").strip()
            if code.endswith(" 00"):  # niveau SECTION MasterFormat
                out.append({"code": code, "libelle": s.get("label_fr") or ""})
        return {"sections": out}

    # ─── CRUD ─────────────────────────────────────────────────────────────
    @router.get("/gabarits")
    def list_gabarits(user=Depends(jwt_user)):
        org = _org(user)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                """
                SELECT g.id, g.nom, g.description, g.created_at, g.updated_at,
                       COUNT(DISTINCT s.id) AS nb_sections,
                       COUNT(l.id)          AS nb_lignes
                FROM ad_budget.gabarits g
                LEFT JOIN ad_budget.gabarit_sections s ON s.gabarit_id = g.id
                LEFT JOIN ad_budget.gabarit_lignes  l ON l.gabarit_section_id = s.id
                WHERE g.organization_id = %s
                GROUP BY g.id
                ORDER BY g.updated_at DESC, g.id DESC
                """,
                (org,),
            )
            return {"gabarits": cur.fetchall()}
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
            # Résout le LIBELLÉ CSI officiel de chaque code section depuis la
            # taxonomie Ad BUD (ad_budget_prix_moyens). Le budget est déjà groupé
            # par code CSI → le save-as capture de vraies sections CSI (code +
            # libellé). Code custom non catalogué → libellé vide (fallback).
            labels = {}
            if order:
                cur.execute(
                    "SELECT DISTINCT section, description FROM ad_budget.ad_budget_prix_moyens "
                    "WHERE section = ANY(%s)",
                    (order,),
                )
                labels = {r["section"]: r["description"] for r in cur.fetchall()}
            structure = [
                {"nom_section": labels.get(sec, ""), "numero": sec, "ordre": i,
                 "lignes": [{**ln, "ordre": j} for j, ln in enumerate(by_section[sec])]}
                for i, sec in enumerate(order)
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
            for sec in sections:
                # Section du budget = numero du gabarit (sinon nom), TELLE QUELLE
                # (on n'écrase pas avec la section catalogue des lignes ad_typ →
                # l'organisation en sections du gabarit est respectée).
                sec_code = ((sec.get("numero") or sec.get("nom_section") or "Divers").strip()
                            or "Divers")
                for lg in sec["lignes"]:
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
                        m = _map_typ_to_budget_cols(typ, 1)  # tarifé catalogue courant, qté=1
                        cur.execute(
                            """
                            INSERT INTO ad_budget.budget_lignes
                            (projet_id, section, description, unite, prix_unitaire, qte,
                             heures, taux_horaire, sous_traitant_montant,
                             ajust_materiaux, ajust_main_oeuvre, ajust_sous_traitant, actif,
                             source_typ_code, source_typ_snapshot_at)
                            VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s,0,0,0,TRUE,%s,NOW())
                            """,
                            (projet_id, sec_code, desc or m["description"], m["unite"],
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
