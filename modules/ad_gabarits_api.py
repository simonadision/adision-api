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
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException
from psycopg.rows import dict_row

from modules import typ_service
from modules.auth_jwt import SESSION_COOKIE_NAME, make_jwt_deps, _extract_bearer
from modules.ad_budget_api import (
    _map_typ_to_budget_cols,
    _load_and_authorize_projet,
    _next_free_csi_suffix,
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
            "SELECT id, organization_id, nom, description, regroupements, "
            "created_at, updated_at "
            "FROM ad_budget.gabarits WHERE id = %s AND organization_id = %s",
            (gabarit_id, org),
        )
        g = cur.fetchone()
        if not g:
            raise HTTPException(status_code=404, detail="Gabarit introuvable")
        return g

    def _normaliser_regroupements(data):
        """Valide/nettoie la liste de regroupements reçue du client — une
        liste vide est valide (aucun regroupement, comportement d'avant).
        Chaque entrée : {nom, divisions: [code, …], apres}. Une entrée sans
        nom OU sans aucune division retenue est écartée plutôt que rejetée :
        un regroupement à moitié rempli ne doit pas bloquer l'enregistrement
        du reste du gabarit.

        `apres` : le NUMÉRO (code CSI) de la division après laquelle ce
        regroupement s'affiche dans la liste des divisions du gabarit, ou
        None s'il précède toutes les divisions — c'est la position visuelle
        du regroupement, glissé-déposé par l'utilisateur au même titre
        qu'une division (brief Simon, 31 août 2026, 2e demande). Une valeur
        pointant vers une division disparue n'est PAS rejetée : elle reste
        en base telle quelle, le client la traite comme "en fin de liste"
        au rendu plutôt que de planter."""
        out = []
        for r in data or []:
            if not isinstance(r, dict):
                continue
            nom = (r.get("nom") or "").strip()
            divisions = [
                (d or "").strip() for d in (r.get("divisions") or []) if (d or "").strip()
            ]
            if not nom or not divisions:
                continue
            apres = r.get("apres")
            apres = apres.strip()[:20] if isinstance(apres, str) and apres.strip() else None
            out.append({"nom": nom[:200], "divisions": divisions, "apres": apres})
        return out

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

    # ─── Titres personnalisés de section CSI ──────────────────────────────
    #
    # LE CODE CSI EST L'IDENTITÉ, IL NE SE MODIFIE JAMAIS. Ces routes ne
    # touchent que l'étiquette affichée : `budget_lignes.section` n'est
    # jamais réécrite par ici, donc regroupements, totaux, filtres du PDF
    # et export vers Ad CON continuent de voir exactement les mêmes codes.
    #
    # LE STOCKAGE EST CHEZ AD EST, ET C'EST VOULU. Les deux modules
    # affichent les MÊMES libellés depuis la MÊME table (app_est.csi_sections,
    # relayée juste au-dessus par _fetch_csi_filtered). Tenir un deuxième
    # jeu de titres ici obligerait Simon à renommer deux fois la même
    # section, et les deux modules divergeraient au premier oubli. Ad BUD
    # relaie donc, comme il relaie déjà la taxonomie.
    #
    # L'AUTORISATION PROJET SE FAIT ICI, PAS LÀ-BAS : la base d'Ad EST ne
    # voit pas ad_budget.projets. On passe par _load_and_authorize_projet
    # (org + verrou + détention) AVANT de relayer, et Ad EST ajoute le
    # scope organisation, qui lui est absolu.
    def _refs_du_projet(cur, projet_id):
        """(ref projet, ref gabarit d'origine) au format attendu par Ad EST.

        Le préfixe « ad_bud: » n'est pas décoratif : les projets d'Ad BUD
        sont numérotés en SERIAL dans ad_budget, ceux d'Ad EST en UUID dans
        app_est. Sans préfixe, les deux se disputeraient la même clé.
        """
        cur.execute(
            "SELECT p.source_gabarit_id, g.nom AS gabarit_nom "
            "FROM ad_budget.projets p "
            "LEFT JOIN ad_budget.gabarits g ON g.id = p.source_gabarit_id "
            "WHERE p.id = %s",
            (projet_id,),
        )
        row = cur.fetchone()
        gab = row.get("source_gabarit_id") if row else None
        nom = row.get("gabarit_nom") if row else None
        return f"ad_bud:{projet_id}", (f"ad_bud:{gab}" if gab else None), nom

    def _autoriser_portee(portee, data, user, org):
        """Autorise l'écriture d'un titre à cette portée, et rend la ref Ad EST.

        C'est LA garde côté Ad BUD : Ad EST ne voit pas ad_budget, il ne
        pourra vérifier que l'organisation. Tout ce qui touche un budget ou
        un gabarit se prouve donc ici, avec les mêmes fonctions que
        n'importe quelle autre écriture du module — aucune porte parallèle.
        """
        if portee == "organisation":
            return ""
        if portee == "projet":
            projet_id = data.get("projet_id")
            if not projet_id:
                raise HTTPException(status_code=400, detail="projet_id requis")
            # "write" : renommer une section EST une écriture. Un budget gelé
            # ou tenu par un collègue se refuse ici, comme une saisie de ligne.
            _load_and_authorize_projet(get_conn, int(projet_id), user, "write")
            return f"ad_bud:{int(projet_id)}"
        if portee == "gabarit":
            gabarit_id = data.get("gabarit_id")
            if not gabarit_id:
                raise HTTPException(status_code=400, detail="gabarit_id requis")
            conn = get_conn()
            cur = conn.cursor(row_factory=dict_row)
            try:
                _load_gabarit_scoped(cur, int(gabarit_id), org)  # scope strict org
            finally:
                cur.close()
                conn.close()
            return f"ad_bud:{int(gabarit_id)}"
        raise HTTPException(status_code=400, detail=f"Portée inconnue : {portee}")

    # `corps` et non `json` : le module importe déjà json, et un paramètre du
    # même nom le masquerait silencieusement dans cette fonction.
    def _relais_est(method, chemin, jwt_token, *, params=None, corps=None):
        """Relaie vers Ad EST en portant le jeton de l'appelant.

        Les erreurs d'Ad EST sont retransmises AVEC leur code : un 404
        « code CSI inconnu » doit arriver tel quel à l'écran, pas déguisé
        en 502. Seule l'indisponibilité du service devient un 502.
        """
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.request(
                    method,
                    f"{EST_API_URL}{chemin}",
                    params=params,
                    json=corps,
                    headers={"Authorization": f"Bearer {jwt_token}"},
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("titres CSI : Ad EST injoignable : %s", e)
            raise HTTPException(
                status_code=502,
                detail="Service des titres CSI (Ad EST) indisponible.",
            )
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail") or f"HTTP {r.status_code}"
            except Exception:  # noqa: BLE001
                detail = f"HTTP {r.status_code}"
            # UNE PANNE RELAYÉE DOIT LAISSER UNE TRACE ICI, PAS SEULEMENT
            # LÀ-BAS. Un 5xx d'Ad EST remonté en HTTPException n'est plus une
            # exception non rattrapée : le filet de api.py ne le voit pas, et
            # les journaux d'Ad BUD n'affichaient qu'un « 500 Internal Server
            # Error » nu (constaté le 6 août 2026 — il a fallu aller lire les
            # journaux de l'AUTRE service pour trouver la cause). On nomme donc
            # ici le service, la route et ce qu'il a répondu. Les 4xx sont des
            # refus normaux et attendus — verrou, division tenue, code inconnu ;
            # les journaliser en erreur noierait les vraies pannes.
            if r.status_code >= 500:
                logger.error(
                    "titres CSI : Ad EST a répondu %s sur %s %s (params=%s) — %s",
                    r.status_code, method, chemin, params, detail,
                )
            raise HTTPException(status_code=r.status_code, detail=detail)
        return r.json()

    @router.get("/csi-titres")
    def lire_csi_titres(projet_id: Optional[int] = None,
                        gabarit_id: Optional[int] = None,
                        authorization: Optional[str] = Header(None),
                        session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
                        user=Depends(jwt_user)):
        org = _org(user)
        jwt_token = _extract_bearer(authorization, None, session_cookie)
        params = {}
        gabarit_nom = None
        if projet_id is not None:
            # Lecture : mode "read" — consulter les titres d'un budget gelé
            # ou tenu par un collègue reste normal.
            _load_and_authorize_projet(get_conn, projet_id, user, "read")
            conn = get_conn()
            cur = conn.cursor(row_factory=dict_row)
            try:
                p_ref, g_ref, gabarit_nom = _refs_du_projet(cur, projet_id)
            finally:
                cur.close()
                conn.close()
            params["projet"] = p_ref
            if g_ref:
                params["gabarit"] = g_ref
        if gabarit_id is not None:
            conn = get_conn()
            cur = conn.cursor(row_factory=dict_row)
            try:
                g = _load_gabarit_scoped(cur, gabarit_id, org)
                gabarit_nom = g.get("nom")
            finally:
                cur.close()
                conn.close()
            params["gabarit"] = f"ad_bud:{gabarit_id}"
        out = _relais_est("GET", "/reference/csi-titres", jwt_token, params=params)
        # Ad EST ne peut pas nommer un gabarit d'Ad BUD — il ne voit pas
        # ad_budget. On complète ici, pour que le bouton de remontée puisse
        # dire vers QUOI il pousse au lieu d'un « le gabarit » anonyme.
        if gabarit_nom:
            out["gabarit_nom"] = gabarit_nom
        return out

    @router.put("/csi-titres")
    def poser_csi_titre(data: dict,
                        authorization: Optional[str] = Header(None),
                        session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
                        user=Depends(jwt_user)):
        org = _org(user)
        jwt_token = _extract_bearer(authorization, None, session_cookie)
        portee = (data.get("portee") or "").strip()
        ref = _autoriser_portee(portee, data, user, org)
        return _relais_est(
            "PUT", "/reference/csi-titres", jwt_token,
            corps={"portee": portee, "ref": ref,
                  "code": data.get("code"), "titre": data.get("titre")},
        )

    @router.delete("/csi-titres")
    def retour_csi_titre_officiel(portee: str, code: str,
                                  projet_id: Optional[int] = None,
                                  gabarit_id: Optional[int] = None,
                                  authorization: Optional[str] = Header(None),
                                  session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
                                  user=Depends(jwt_user)):
        org = _org(user)
        jwt_token = _extract_bearer(authorization, None, session_cookie)
        ref = _autoriser_portee(
            portee, {"projet_id": projet_id, "gabarit_id": gabarit_id}, user, org
        )
        params = {"portee": portee, "code": code}
        if ref:
            params["ref"] = ref
        return _relais_est("DELETE", "/reference/csi-titres", jwt_token, params=params)

    @router.post("/csi-titres/remontee")
    def remonter_csi_titre(data: dict,
                           authorization: Optional[str] = Header(None),
                           session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
                           user=Depends(jwt_user)):
        """Pousse un titre de budget vers son gabarit, ou vers l'organisation.

        Route séparée du PUT parce qu'elle n'écrit PAS là où on travaille :
        on est dans un budget, elle pose le titre ailleurs — et vers
        l'organisation, elle le pose pour Ad BUD ET Ad EST. Un renommage
        ordinaire ne doit pas pouvoir déclencher ça au passage.
        """
        org = _org(user)
        jwt_token = _extract_bearer(authorization, None, session_cookie)
        cible = (data.get("cible_portee") or "").strip()
        ref = _autoriser_portee(cible, data, user, org)
        return _relais_est(
            "POST", "/reference/csi-titres/remontee", jwt_token,
            corps={"code": data.get("code"), "titre": data.get("titre"),
                  "cible_portee": cible, "cible_ref": ref},
        )

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
                       g.created_by, g.created_by_email,
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

    def _copy_gabarit_to_org(cur, master, target_org, push_id, by_email=None):
        """Deep-copy d'un gabarit master vers target_org (copie indépendante,
        traçable). Réutilise _full_gabarit (lecture arbre) + _replace_structure.
        L'auteur affiché chez la cible est CELUI QUI POUSSE (super_admin) : la
        copie n'a pas d'auteur dans l'org cliente, et laisser « — » cacherait
        d'où elle vient."""
        cur.execute(
            "INSERT INTO ad_budget.gabarits "
            "(organization_id, nom, description, est_master, source_master_gabarit_id, push_id, "
            " created_by_email) "
            "VALUES (%s, %s, %s, FALSE, %s, %s, %s) RETURNING id",
            (target_org, master["nom"], master.get("description"), master["id"], push_id,
             by_email))
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
                    new_id = _copy_gabarit_to_org(cur, master, target_org, push_id,
                                                  by_email=su.get("email"))
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
                "INSERT INTO ad_budget.gabarits "
                "(organization_id, nom, description, created_by, created_by_email) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (org, nom, (data.get("description") or "").strip() or None,
                 user.get("id"), user.get("email")),
            )
            gid = cur.fetchone()["id"]
            if data.get("sections"):
                _replace_structure(cur, gid, data["sections"])
            if data.get("regroupements"):
                cur.execute(
                    "UPDATE ad_budget.gabarits SET regroupements=%s WHERE id=%s",
                    (json.dumps(_normaliser_regroupements(data["regroupements"])), gid),
                )
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
            g["regroupements"] = g.get("regroupements") or []
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
            # `regroupements` suit le même contrat que `sections` : présent
            # dans le payload → remplace en entier ; absent → inchangé (un
            # appelant qui ignore ce champ ne l'efface pas silencieusement).
            if "regroupements" in data:
                cur.execute(
                    "UPDATE ad_budget.gabarits SET nom=%s, description=%s, "
                    "regroupements=%s, updated_at=NOW() "
                    "WHERE id=%s AND organization_id=%s",
                    (nom, (data.get("description") or "").strip() or None,
                     json.dumps(_normaliser_regroupements(data["regroupements"])),
                     gabarit_id, org),
                )
            else:
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

    @router.patch("/gabarits/{gabarit_id}")
    def rename_gabarit(gabarit_id: int, data: dict, user=Depends(jwt_user)):
        """RENOMMER (et/ou re-décrire) SANS toucher à la structure.

        Le PUT remplace tout l'arbre dès que la clé `sections` est présente ;
        renommer depuis la bibliothèque, où l'arbre n'est PAS chargé, passerait
        donc par un PUT dont on devrait deviner le contenu. Ce PATCH ne touche
        QUE l'en-tête. Champs omis = inchangés (vrai PATCH partiel : `description`
        absente ne l'efface pas, contrairement au PUT)."""
        org = _org(user)
        sets, params = [], []
        if "nom" in data:
            nom = (data.get("nom") or "").strip()
            if not nom:
                raise HTTPException(status_code=400, detail="Nom du gabarit requis")
            sets.append("nom=%s")
            params.append(nom[:200])
        if "description" in data:
            sets.append("description=%s")
            params.append((data.get("description") or "").strip() or None)
        if not sets:
            raise HTTPException(status_code=400, detail="Rien à modifier (nom et/ou description)")
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            _load_gabarit_scoped(cur, gabarit_id, org)  # scope strict → 404 hors org
            cur.execute(
                f"UPDATE ad_budget.gabarits SET {', '.join(sets)}, updated_at=NOW() "
                "WHERE id=%s AND organization_id=%s",
                (*params, gabarit_id, org),
            )
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
            # L'auteur de la COPIE est celui qui duplique — pas l'auteur de
            # l'original (la copie est un gabarit neuf, indépendant).
            cur.execute(
                "INSERT INTO ad_budget.gabarits "
                "(organization_id, nom, description, regroupements, created_by, created_by_email) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (org, (g["nom"] + " (copie)")[:200], g["description"],
                 json.dumps(g.get("regroupements") or []),
                 user.get("id"), user.get("email")),
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
                "INSERT INTO ad_budget.gabarits "
                "(organization_id, nom, description, created_by, created_by_email) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (org, nom, (data.get("description") or "").strip() or None,
                 user.get("id"), user.get("email")),
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
    @router.get("/projets/{projet_id}/regroupements")
    def get_projet_regroupements(projet_id: int, user=Depends(jwt_user)):
        """Regroupements de total en vigueur pour ce budget — résolus depuis
        son `source_gabarit_id`, JAMAIS copiés (même principe que la
        résolution des titres CSI : un changement posé sur le gabarit se
        répercute dans tous les budgets qui en sont nés). Un projet sans
        gabarit d'origine, ou dont le gabarit n'a aucun regroupement, ou dont
        le gabarit d'origine a depuis été supprimé, rend une liste vide —
        jamais une erreur : c'est un affichage optionnel, pas une donnée
        dont le budget dépend."""
        org = _org(user)
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                "SELECT source_gabarit_id FROM ad_budget.projets WHERE id=%s", (projet_id,),
            )
            row = cur.fetchone()
            gabarit_id = row and row.get("source_gabarit_id")
            if not gabarit_id:
                return {"regroupements": [], "gabarit_id": None}
            cur.execute(
                "SELECT regroupements FROM ad_budget.gabarits "
                "WHERE id=%s AND organization_id=%s",
                (gabarit_id, org),
            )
            g = cur.fetchone()
            return {"regroupements": (g and g.get("regroupements")) or [], "gabarit_id": gabarit_id}
        finally:
            cur.close()
            conn.close()

    @router.post("/projets/{projet_id}/insert-gabarit")
    def insert_gabarit(projet_id: int, data: dict,
                       authorization: Optional[str] = Header(None),
                       session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
                       user=Depends(jwt_user)):
        # Phase D-v2b pré-requis — session_cookie en fallback header pour le
        # re-extract manuel (proxy hub). Depends(jwt_user) supporte déjà le
        # cookie nativement (auth_jwt.py:155-166). Check d'autorisation
        # _load_and_authorize_projet (l. 710) reste APRÈS extraction.
        org = _org(user)
        _load_and_authorize_projet(get_conn, projet_id, user, "write")
        gabarit_id = data.get("gabarit_id")
        if not gabarit_id:
            raise HTTPException(status_code=400, detail="gabarit_id requis")
        jwt_token = _extract_bearer(authorization, None, session_cookie)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            _load_gabarit_scoped(cur, gabarit_id, org)  # scope strict
            sections = _full_gabarit(cur, gabarit_id)
            inserted, warnings = 0, []
            for div in sections:
                # Code de division (fallback pour les sous-sections sans code CSI).
                div_code = ((div.get("numero") or div.get("nom_section") or "").strip())
                sous_sections = div.get("sous_sections") or []
                # Division SANS AUCUNE sous-section (ex. import PDF de bordereau,
                # qui ne détecte que les numéros de section, jamais les lignes —
                # cf. GabaritEditorPage.jsx/confirmerImportPdf) : sans ce cas, la
                # division n'insère RIEN, donc n'apparaît dans AUCUNE ligne du
                # budget — le budget entier semble vide (« Aucun item dans ce
                # projet ») même quand le gabarit a 18 divisions et 2
                # regroupements de définis. Une ligne vide « à remplir » rend la
                # division visible et éditable, exactement comme dans l'éditeur
                # de gabarit (« + Ajouter une sous-section »). Simon, en direct,
                # 1er sept 2026 : « JE veux voir mes divisions et mes deux total ».
                if not sous_sections:
                    _insert_manual(cur, projet_id, div_code or "Divers", "")
                    inserted += 1
                    continue
                for ss in sous_sections:
                    # Section du budget = code CSI de la SOUS-SECTION (section fine,
                    # ex. "09 91 00") → se regroupe correctement par 2 paires avec
                    # le regroupement budget ACTUEL (Temps 1). Fallback : code de la
                    # division, sinon "Divers". On respecte l'organisation du gabarit.
                    sec_code = ((ss.get("code_csi") or "").strip()
                                or div_code or "Divers")
                    lignes_ss = ss["lignes"]
                    # Même raisonnement : une sous-section sans aucune ligne
                    # n'insère rien et disparaît du budget sans cette ligne vide.
                    if not lignes_ss:
                        _insert_manual(cur, projet_id, sec_code, "")
                        inserted += 1
                        continue
                    for lg in lignes_ss:
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
                            # Sprint PU_ST référence Ad TYP — pré-remplissage à
                            # l'insertion gabarit. override = FALSE (héritage
                            # carnet). Le mode COMPUTED côté Ad BUD prend le
                            # relais quand l'user saisira une qté > 0.
                            # Auto-incrément CSI : un gabarit contenant N fois le
                            # même code → suffixes séquentiels libres. Le SELECT
                            # interne voit les INSERTs précédents de la transaction
                            # courante (PostgreSQL READ COMMITTED).
                            section_final = _next_free_csi_suffix(cur, projet_id, line_section)
                            cur.execute(
                                """
                                INSERT INTO ad_budget.budget_lignes
                                (projet_id, section, description, unite, prix_unitaire, qte,
                                 heures, taux_horaire, sous_traitant_montant, prix_unitaire_st,
                                 ajust_materiaux, ajust_main_oeuvre, ajust_sous_traitant, actif,
                                 source_typ_code, source_typ_snapshot_at)
                                VALUES (%s,%s,%s,%s,%s,0,%s,%s,%s,%s,0,0,0,TRUE,%s,NOW())
                                """,
                                (projet_id, section_final, desc or m["description"], m["unite"],
                                 m["prix_unitaire"], m["heures"], m["taux_horaire"],
                                 m["sous_traitant_montant"], m["prix_unitaire_st"], code),
                            )
                            inserted += 1
                        else:
                            _insert_manual(cur, projet_id, sec_code, desc)
                            inserted += 1
            # D'OÙ VIENT CE BUDGET. Sans ce lien, un budget né d'un gabarit
            # ne peut pas hériter des titres de section personnalisés qui y
            # ont été posés, ni les lui remonter. COALESCE : le PREMIER
            # gabarit inséré reste l'origine, comme dans Ad EST
            # (apply_template garde COALESCE(source_template_id, …)).
            cur.execute(
                "UPDATE ad_budget.projets "
                "SET source_gabarit_id = COALESCE(source_gabarit_id, %s) "
                "WHERE id = %s",
                (gabarit_id, projet_id),
            )
            conn.commit()
            return {"status": "inserted", "nb_lignes": inserted, "warnings": warnings}
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    return router
