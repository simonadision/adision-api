"""Ad BUD — DEVIS (lettre de proposition qualitative), distinct du rapport.

Réutilise le socle PDF du rapport (reportlab, build_pdf_logo) et hub_service
(fiche entreprise + documents GED). 1 devis par projet (ad_budget.devis),
champs éditables persistés ; entreprise/client/montant/logo auto-remplis à la
génération.

Endpoints (prefix /budget) :
  GET /projets/{id}/devis        -> {devis, entreprise, client, user, documents}
  PUT /projets/{id}/devis        -> sauve les champs éditables (UPSERT)
  GET /projets/{id}/devis-pdf    -> PDF reportlab (param montant, couleur)
"""
import io
import json
import logging
import os
import re
from datetime import date

import httpx
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from modules import hub_service
from modules.auth_jwt import SESSION_COOKIE_NAME, make_jwt_deps, _extract_bearer
from modules.ad_budget_api import (
    build_pdf_logo, _load_and_authorize_projet, draw_adflo_footer,
    _persist_hub_identity_snapshot,
)

logger = logging.getLogger(__name__)


def _frca_num(value):
    """fr-CA : milliers = espace, décimale = virgule. Ex. 1234.5 -> '1 234,50'."""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    return f"{v:,.2f}".replace(",", " ").replace(".", ",")


def _flatten_doc_names(tree) -> list:
    """Aplati l'arbre GED (categories>disciplines>documents) en liste de noms."""
    out = []
    if not isinstance(tree, dict):
        return out
    for cat in tree.get("categories", []) or []:
        for disc in cat.get("disciplines", []) or []:
            for d in disc.get("documents", []) or []:
                nm = d.get("display_name") or d.get("filename")
                if nm:
                    out.append(nm)
        for d in cat.get("documents_uncategorized", []) or []:
            nm = d.get("display_name") or d.get("filename")
            if nm:
                out.append(nm)
    return out


def _trim_logo_whitespace(content: bytes) -> bytes:
    """Rogne la marge blanche/transparente autour du logo. Sans ça, le blanc
    INTERNE du fichier décale le visuel vs le texte aligné à la marge (mesuré :
    le logo Contracta a ~16px de blanc à gauche -> ~10pt de décalage dans le
    PDF). Best-effort : retourne l'original si PIL absent ou crop vide/échec.
    RGBA/LA -> bbox du contenu opaque ; sinon -> bbox du contenu non blanc."""
    try:
        from PIL import Image as PILImage, ImageChops
        im = PILImage.open(io.BytesIO(content))
        if im.mode in ("RGBA", "LA"):
            bbox = im.split()[-1].getbbox()
        else:
            rgb = im.convert("RGB")
            bg = PILImage.new("RGB", im.size, (255, 255, 255))
            bbox = ImageChops.difference(rgb, bg).getbbox()
        if not bbox:
            return content
        out = io.BytesIO()
        im.crop(bbox).save(out, format="PNG")
        return out.getvalue()
    except Exception as e:  # noqa: BLE001
        print(f"[devis] trim logo échec: {e}", flush=True)
        return content


def _logo_flowable_for_org(org, max_width=150):
    """Image reportlab du logo entreprise depuis org.logo_url (R2 proxy stable).
    Le blanc autour du logo est rogné pour un alignement franc à la marge.
    Org sans logo / fetch en échec -> build_pdf_logo('') -> "" (placeholder
    NEUTRE, jamais Adision — décision Simon, cohérent multi-tenant)."""
    url = (org or {}).get("logo_url")
    if url:
        try:
            r = httpx.get(url, timeout=10.0)
            if r.status_code == 200 and r.content:
                import base64
                content = _trim_logo_whitespace(r.content)
                return build_pdf_logo(base64.b64encode(content).decode("ascii"), max_width=max_width)
        except Exception as e:  # noqa: BLE001
            print(f"[devis] logo org fetch échec: {e}", flush=True)
    return build_pdf_logo("", max_width=max_width)


# Format date fr-CA SANS dépendance babel (pas dans requirements.txt) ni locale
# système (risque sur Railway). Table hardcodée des 12 mois — déterministe,
# portable, zéro surcoût. Utilisée pour la date du devis sur la page de garde.
_MOIS_FR_CA = ["janvier", "février", "mars", "avril", "mai", "juin",
               "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _format_date_fr_ca(iso_d):
    """ISO 'YYYY-MM-DD' → 'D mois YYYY' fr-CA (mois en minuscule, sans virgule).
    Fallback today() si entrée absente, vide ou invalide — préserve la rétrocompat
    des appels qui ne fournissent pas la date (tests existants notamment)."""
    try:
        if iso_d:
            y, m, dd = str(iso_d).split("-")
            return f"{int(dd)} {_MOIS_FR_CA[int(m) - 1]} {int(y)}"
    except (ValueError, TypeError, IndexError):
        pass
    today = date.today()
    return f"{today.day} {_MOIS_FR_CA[today.month - 1]} {today.year}"


def register_ad_devis_routes(get_conn):
    jwt_user, jwt_user_or_token, _a, _s = make_jwt_deps(get_conn)
    router = APIRouter(prefix="/budget", tags=["Ad BUD — Devis"])

    def _load_devis(cur, projet_id):
        cur.execute("SELECT * FROM ad_budget.devis WHERE projet_id = %s", (projet_id,))
        return cur.fetchone()

    def _resolve_identity_with_snapshot(projet, jwt_token, cache):
        """Identité projet avec REPLI snapshot (Brief 1) : plus de 502 sur le
        chemin devis. Rafraîchit le miroir local quand le hub répond (source
        'hub') ; trace en log quand on retombe sur le snapshot ('snapshot').
        Retourne (ident, source) — source ∈ {'hub','snapshot','none'}."""
        if not jwt_token:
            return {}, "none"
        ident, source = hub_service.resolve_hub_identity_with_fallback(projet, jwt_token, cache)
        if source == "hub":
            try:
                _cc = get_conn(); _ccur = _cc.cursor()
                _persist_hub_identity_snapshot(_ccur, projet.get("id"), ident)
                _cc.commit(); _ccur.close(); _cc.close()
            except Exception:
                pass  # best-effort
        elif source == "snapshot":
            print(f"[devis] identité depuis SNAPSHOT local (hub injoignable) "
                  f"projet={projet.get('id')} snapshot_at={projet.get('hub_identity_snapshot_at')}", flush=True)
        return ident, source

    @router.get("/projets/{projet_id}/devis")
    def get_devis(projet_id: int, user=Depends(jwt_user),
                  authorization: Optional[str] = Header(None),
                  session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME)):
        # Phase D-v2b pré-requis — session_cookie pour le re-extract manuel
        # (proxy hub). _load_and_authorize_projet (l. 141) reste APRÈS extraction.
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute("SELECT * FROM ad_budget.projets WHERE id = %s", (projet_id,))
            projet = cur.fetchone()
            if not projet:
                raise HTTPException(status_code=404, detail="Projet introuvable")
            devis = _load_devis(cur, projet_id)
        finally:
            cur.close()
            conn.close()
        jwt_token = _extract_bearer(authorization, None, session_cookie)
        # Identité CLIENT depuis le hub AVEC REPLI snapshot (Brief 1) — plus de 502.
        _hubc = {}
        _ident, _ident_source = _resolve_identity_with_snapshot(projet, jwt_token, _hubc)
        # Entreprise (fiche org HUB) — non bloquant.
        entreprise = {}
        try:
            entreprise = hub_service.fetch_organization(jwt_token, projet.get("organization_id")) or {}
        except Exception as e:  # noqa: BLE001
            print(f"[devis] fetch_organization échec: {e}", flush=True)
        # Documents GED du projet HUB lié — non bloquant.
        documents = []
        ad_hub_pid = projet.get("ad_hub_project_id")
        if ad_hub_pid:
            try:
                tree = hub_service.fetch_project_documents(jwt_token, int(ad_hub_pid))
                documents = _flatten_doc_names(tree)
            except Exception as e:  # noqa: BLE001
                print(f"[devis] fetch_project_documents échec: {e}", flush=True)
        return {
            "devis": devis,  # null si pas encore créé
            "entreprise": {
                "name": entreprise.get("name"),
                "rbq": entreprise.get("rbq"),
                "neq": entreprise.get("neq"),
                "courriel": entreprise.get("courriel"),
                "telephone": entreprise.get("telephone"),
            },
            "client": {
                "nom": _ident.get("nom_client"),
                "contact": _ident.get("contact_client"),
                "courriel": _ident.get("email_client"),
                "telephone": _ident.get("telephone_client"),
            },
            "user": {"nom": user.get("nom"), "email": user.get("email")},
            "documents": documents,
            # Traçabilité : 'hub' (frais) | 'snapshot' (copie locale, hub down) |
            # 'none' (aucune identité). Le front peut afficher un bandeau discret.
            "identity_source": _ident_source,
        }

    @router.put("/projets/{projet_id}/devis")
    def save_devis(projet_id: int, data: dict, user=Depends(jwt_user)):
        _load_and_authorize_projet(get_conn, projet_id, user, "write")
        docs = data.get("documents")
        if not isinstance(docs, list):
            docs = []
        couleur = data.get("couleur") or "adision"
        if couleur not in ("adision", "nb"):
            couleur = "adision"
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                """
                INSERT INTO ad_budget.devis
                  (projet_id, presentation, travaux_inclus, travaux_non_inclus,
                   responsable_nom, titre_responsable, organisation,
                   responsable_email, responsable_tel,
                   documents, couleur, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW())
                ON CONFLICT (projet_id) DO UPDATE SET
                  presentation = EXCLUDED.presentation,
                  travaux_inclus = EXCLUDED.travaux_inclus,
                  travaux_non_inclus = EXCLUDED.travaux_non_inclus,
                  responsable_nom = EXCLUDED.responsable_nom,
                  titre_responsable = EXCLUDED.titre_responsable,
                  organisation = EXCLUDED.organisation,
                  responsable_email = EXCLUDED.responsable_email,
                  responsable_tel = EXCLUDED.responsable_tel,
                  documents = EXCLUDED.documents,
                  couleur = EXCLUDED.couleur,
                  updated_at = NOW()
                RETURNING *
                """,
                (
                    projet_id,
                    (data.get("presentation") or "").strip() or None,
                    (data.get("travaux_inclus") or "").strip() or None,
                    (data.get("travaux_non_inclus") or "").strip() or None,
                    (data.get("responsable_nom") or "").strip() or None,
                    (data.get("titre_responsable") or "").strip() or None,
                    (data.get("organisation") or "").strip() or None,
                    (data.get("responsable_email") or "").strip() or None,
                    (data.get("responsable_tel") or "").strip() or None,
                    json.dumps([str(x) for x in docs]),
                    couleur,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return {"devis": row}
        finally:
            cur.close()
            conn.close()

    @router.get("/projets/{projet_id}/devis-pdf")
    def devis_pdf(
        projet_id: int,
        user=Depends(jwt_user_or_token),
        authorization: Optional[str] = Header(None),
        token: Optional[str] = Query(None),
        session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
        montant: float = 0,
        couleur: str = "adision",
        date_devis: Optional[str] = None,
    ):
        # Phase D-v2b pré-requis — session_cookie en fallback (header + query
        # restent). _load_and_authorize_projet reste APRÈS extraction.
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        jwt_token = _extract_bearer(authorization, token, session_cookie)
        buf, _snap, _ident_source = _build_devis(projet_id, montant, couleur, jwt_token, date_devis)
        safe = "".join(c if c.isalnum() or c in "-_ " else "_"
                       for c in (_snap["project"]["nom"] or "devis")).strip() or "devis"
        return StreamingResponse(
            buf, media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="devis_{safe}.pdf"',
                # Traçabilité côté client : le front peut afficher un bandeau discret
                # « identité depuis copie locale » quand la valeur est 'snapshot'.
                "X-Bud-Identity-Source": _ident_source or "none",
            },
        )

    @router.post("/projets/{projet_id}/devis/emit")
    def emit_devis_to_hub(
        projet_id: int,
        user=Depends(jwt_user),
        authorization: Optional[str] = Header(None),
        session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
        montant: float = 0,
        couleur: str = "adision",
        revision_choice: Optional[str] = None,
        date_devis: Optional[str] = None,
    ):
        """Émet la PROPOSITION/DEVIS vers l'Espace Rapports HUB
        (report_type='proposition_devis', montant_avant_taxes = le quantitatif).
        Mécanique de révision (option A) identique au rapport : si la révision a
        déjà été émise ET que le budget a changé depuis, on demande à l'user
        (choice_required) ; 'new' bumpe, 'correction' remplace. dirty -> FALSE.

        Phase D-v2b pré-requis — session_cookie pour le re-extract manuel
        (proxy hub). _load_and_authorize_projet reste APRÈS extraction."""
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        jwt_token = _extract_bearer(authorization, None, session_cookie)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute("SELECT * FROM ad_budget.projets WHERE id = %s", (projet_id,))
            projet = cur.fetchone()
        finally:
            cur.close()
            conn.close()
        if not projet:
            raise HTTPException(status_code=404, detail="Projet introuvable")
        hub_pid = projet.get("ad_hub_project_id")
        if not hub_pid:
            raise HTTPException(status_code=400,
                                detail="Projet non lié à un projet HUB")

        # Mécanique de révision (option A) — choix user si budget changé.
        rev_no = projet.get("revision_no", 0)
        rev_label = projet.get("revision_label") or "Originale"
        needs_choice = bool(projet.get("emitted_at_current_revision")
                            and projet.get("budget_modifie_depuis_emission"))
        if needs_choice and revision_choice not in ("new", "correction"):
            return {"choice_required": True, "current_label": rev_label,
                    "next_label": f"R.{rev_no + 1}"}
        if needs_choice and revision_choice == "new":
            rev_no = rev_no + 1
            rev_label = f"R.{rev_no}"
            conn = get_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    "UPDATE ad_budget.projets SET revision_no = %s, "
                    "revision_label = %s, updated_at = NOW() WHERE id = %s",
                    (rev_no, rev_label, projet_id))
                conn.commit()
            finally:
                cur.close()
                conn.close()

        buf, snapshot, _ident_source = _build_devis(projet_id, montant, couleur, jwt_token, date_devis)
        pdf_bytes = buf.getvalue()
        fields = {
            "report_type": "proposition_devis",
            "revision_no": rev_no,
            "revision_label": rev_label,
            "montant_avant_taxes": snapshot["totaux"]["montant_avant_taxes"],
            "montant_apres_taxes": snapshot["totaux"]["montant_apres_taxes"],
            "source_ref": projet_id,
            "generated_by": user.get("id"),
            "generated_by_nom": user.get("nom") or user.get("email"),
            "snapshot_data": json.dumps(snapshot, default=str),
        }
        try:
            result = hub_service.post_report(jwt_token, int(hub_pid), pdf_bytes, fields)
        except hub_service.HubServiceError as e:
            raise HTTPException(status_code=502, detail=f"Échec émission HUB : {e.detail}")
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("UPDATE ad_budget.projets SET emitted_at_current_revision = TRUE, "
                        "budget_modifie_depuis_emission = FALSE, updated_at = NOW() "
                        "WHERE id = %s", (projet_id,))
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return {
            "emitted": True,
            "report_type": "proposition_devis",
            "revision_no": rev_no,
            "revision_label": rev_label,
            "hub": result,
        }

    def _build_devis(projet_id, montant, couleur, jwt_token, date_devis=None):
        """Construit le PDF devis ET le snapshot (proposition_devis) ; retourne
        (buf, snapshot). Auth faite par l'appelant (route)."""
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute("SELECT * FROM ad_budget.projets WHERE id = %s", (projet_id,))
            projet = cur.fetchone()
            if not projet:
                raise HTTPException(status_code=404, detail="Projet introuvable")
            devis = _load_devis(cur, projet_id) or {}
        finally:
            cur.close()
            conn.close()
        entreprise = {}
        try:
            entreprise = hub_service.fetch_organization(jwt_token, projet.get("organization_id")) or {}
        except Exception as e:  # noqa: BLE001
            print(f"[devis] fetch_organization échec (pdf): {e}", flush=True)

        # Identité CLIENT depuis le hub AVEC REPLI snapshot (Brief 1) — le devis ne
        # 502 plus si le hub est injoignable ; il repart du miroir local (identité
        # possiblement périmée → tracée en métadonnée PDF /Subject + log).
        _hubc = {}
        _ident, _ident_source = _resolve_identity_with_snapshot(projet, jwt_token, _hubc)

        nb = (couleur == "nb")
        ACCENT = colors.HexColor("#111827") if nb else colors.HexColor("#1e3a8a")
        MUTED = colors.HexColor("#374151") if nb else colors.HexColor("#475569")

        ss = getSampleStyleSheet()
        body = ParagraphStyle("body", parent=ss["Normal"], fontSize=10, leading=14)
        h = ParagraphStyle("h", parent=ss["Normal"], fontSize=11, leading=14,
                            textColor=ACCENT, spaceBefore=10, spaceAfter=4,
                            fontName="Helvetica-Bold")
        small = ParagraphStyle("small", parent=ss["Normal"], fontSize=9, leading=12, textColor=MUTED)

        def esc(s):
            return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        def para_multiline(txt):
            return Paragraph(esc(txt).replace("\n", "<br/>"), body)

        margin = 1.8 * cm
        # TRAÇABILITÉ invisible (rendu pixel-identique quand hub OK) : /Subject PDF
        # marqué SEULEMENT si l'identité vient du snapshot local.
        _pdf_subject = (
            f"Identité projet issue d'une copie locale (hub indisponible) — "
            f"snapshot du {projet.get('hub_identity_snapshot_at')}"
            if _ident_source == "snapshot" else ""
        )
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter,
                                rightMargin=margin, leftMargin=margin,
                                topMargin=margin, bottomMargin=2.2 * cm,
                                title=f"Devis — {_ident.get('nom') or projet_id}",
                                subject=_pdf_subject)
        story = []

        # 1. EN-TÊTE : logo entreprise (gauche) + titre (droite). Colonnes
        #    identiques à la table ENTREPRENEUR/CLIENT (doc.width/2) pour que
        #    logo s'aligne sur ENTREPRENEUR et titre sur CLIENT. Le logo est
        #    un Image hAlign=CENTER par défaut -> force LEFT pour le coller à
        #    la marge gauche (même x que le bloc ENTREPRENEUR en dessous).
        logo = _logo_flowable_for_org(entreprise, max_width=150)
        if logo is not None and not isinstance(logo, str):
            logo.hAlign = "LEFT"
        # alignment=0 (LEFT) : le titre démarre au même x que le bloc CLIENT
        # en dessous (début de la colonne droite, x mesuré = 306) — choix Simon.
        # Étiquette de révision (Espace Rapports) près du titre, en plus petit.
        _rev_lbl = esc(projet.get("revision_label") or "Originale")
        # Titre PRINCIPAL = NOM DU PROJET (gros) ; SOUS-TITRE dessous =
        # « Proposition / Devis ». Gardé à la position du titre actuel (colonne
        # droite, alignée au bloc CLIENT) -> AUCUN chevauchement avec le logo
        # gauche. ACCENT = noir en N&B, bleu marque en couleur. Nom vide ->
        # sous-titre seul (jamais de titre vide).
        _nom_projet = esc((_ident.get("nom") or "").strip().upper())
        titre_proj_style = ParagraphStyle(
            "tp", parent=ss["Normal"], fontSize=18, leading=21, alignment=0,
            textColor=ACCENT, fontName="Helvetica-Bold")
        sous_titre_style = ParagraphStyle(
            "stp", parent=ss["Normal"], fontSize=11, leading=14, alignment=0,
            textColor=ACCENT)
        # Date du devis — affichée sur la page de garde sous « Originale ». Format
        # long fr-CA (ex. « 23 juin 2026 »). date_devis arrive en ISO depuis le
        # front ; fallback today() si absent (rétrocompat tests + appels legacy).
        date_devis_style = ParagraphStyle(
            "ddv", parent=ss["Normal"], fontSize=9, leading=11, alignment=0,
            textColor=ACCENT, spaceBefore=2)
        _date_devis_fmt = esc(_format_date_fr_ca(date_devis))
        date_para = Paragraph(f"<b>Date du devis :</b> {_date_devis_fmt}", date_devis_style)
        if _nom_projet:
            title_cell = [Paragraph(_nom_projet, titre_proj_style),
                          Paragraph(f"Proposition / Devis <font size='9'>— {_rev_lbl}</font>", sous_titre_style),
                          date_para]
        else:
            title_cell = [Paragraph(f"PROPOSITION / DEVIS <font size='11'>— {_rev_lbl}</font>", titre_proj_style),
                          date_para]
        head = Table([[logo or "", title_cell]], colWidths=[doc.width / 2.0, doc.width / 2.0])
        head.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(head)
        story.append(Spacer(1, 10))

        # 2 + 3 : ENTREPRENEUR (gauche) et CLIENT (droite)
        def block(title, lines):
            rows = [[Paragraph(title, ParagraphStyle("bh", parent=h, spaceBefore=0))]]
            for label, val in lines:
                rows.append([Paragraph(f"<b>{esc(label)}</b> {esc(val)}", small)])
            t = Table(rows, colWidths=["*"])
            t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                   ("TOPPADDING", (0, 0), (-1, -1), 1),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
            return t

        entr = block("ENTREPRENEUR", [
            ("", entreprise.get("name") or "—"),
            ("RBQ :", entreprise.get("rbq") or "—"),
            ("Courriel :", entreprise.get("courriel") or "—"),
            ("Téléphone :", entreprise.get("telephone") or "—"),
        ])
        client = block("CLIENT", [
            ("", _ident.get("nom_client") or "—"),
            ("Contact :", _ident.get("contact_client") or "—"),
            ("Courriel :", _ident.get("email_client") or "—"),
            ("Téléphone :", _ident.get("telephone_client") or "—"),
        ])
        two = Table([[entr, client]], colWidths=[doc.width / 2.0, doc.width / 2.0])
        two.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (0, 0), 12)]))
        story.append(two)

        # Travaux inclus/non inclus calculés TÔT : pilotent l'espace de centrage
        # du bloc présentation (grand si aucun travaux pour descendre/centrer le
        # paragraphe ; modéré sinon pour ne pas pousser le contenu hors page).
        _inclus = (devis.get("travaux_inclus") or "").strip()
        _non_inclus = (devis.get("travaux_non_inclus") or "").strip()

        # Espace au-dessus de la présentation. Aucun bloc travaux -> 94 (la page
        # a de la place -> on centre, un peu plus haut que 120 : ~2 lignes de
        # moins) ; avec travaux -> 18 (sûr, inchangé).
        story.append(Spacer(1, 18 if (_inclus or _non_inclus) else 94))

        # 4. PRÉSENTATION — texte seul, sans titre de section (le corps
        #    « Madame, Monsieur… » commence directement). Rendu paragraphe par
        #    paragraphe (séparés par des lignes vides) avec interligne plus
        #    confortable (leading 16) et espace après chaque paragraphe, pour
        #    aérer la page 1 (qui a de la place grâce au saut de page forcé).
        #    Aéré mais pas dispersé. Espace d'aération avant TRAVAUX INCLUS.
        pres_style = ParagraphStyle("pres", parent=body, leading=16, spaceAfter=11)
        pres_txt = (devis.get("presentation") or "—").strip()
        # Source unique de vérité pour le nom du contact : le PDF rend toujours
        # la valeur LIVE de contact_client (fiche projet), peu importe ce qui
        # a été figé dans le texte au moment de l'insertion des templates
        # « Présentation budget/soumission » côté Ad BUD. Deux chemins :
        #   (a) placeholder {contact} préservé par le front → substitué live.
        #   (b) legacy : 1re ligne « À l'attention de … » figée → ré-écrite
        #       avec contact_client courant. Cohérent avec le bloc en-tête
        #       CLIENT (cf. ligne ~487, même source _ident.contact_client).
        # Si contact_client vide : on retire la ligne pour éviter
        # « À l'attention de » orphelin (et le placeholder devient vide).
        _contact_live = (_ident.get("contact_client") or "").strip()
        if _contact_live:
            pres_txt = pres_txt.replace("{contact}", _contact_live)
            pres_txt = re.sub(
                r"^\s*À l'attention de[^\n]*",
                f"À l'attention de {_contact_live}",
                pres_txt, count=1,
            )
        else:
            pres_txt = pres_txt.replace("{contact}", "")
            pres_txt = re.sub(
                r"^\s*À l'attention de[^\n]*\n?",
                "", pres_txt, count=1,
            ).lstrip()
        for para in re.split(r"\n\s*\n", pres_txt) or [pres_txt]:
            if para.strip():
                story.append(Paragraph(esc(para).replace("\n", "<br/>"), pres_style))
        story.append(Spacer(1, 22))

        # 6 + 7 : TRAVAUX INCLUS (titre vert) / NON INCLUS (titre rouge).
        #    SEULS les titres sont colorés, et uniquement en mode couleur ;
        #    en N&B ils restent neutres (couleur ACCENT = #111827). Le corps
        #    de texte reste en `body` (noir) dans tous les cas.
        h_inclus = ParagraphStyle("h_inclus", parent=h,
                                  textColor=h.textColor if nb else colors.HexColor("#10b981"))
        h_non_inclus = ParagraphStyle("h_non_inclus", parent=h,
                                      textColor=h.textColor if nb else colors.HexColor("#ef4444"))
        # Chaque section ne s'affiche QUE si elle a du contenu (titre + bloc),
        # indépendamment l'une de l'autre. (_inclus/_non_inclus calculés plus haut.)
        if _inclus:
            story.append(Paragraph("TRAVAUX INCLUS", h_inclus))
            story.append(para_multiline(_inclus))
        if _non_inclus:
            story.append(Paragraph("TRAVAUX NON INCLUS", h_non_inclus))
            story.append(para_multiline(_non_inclus))

        # 8. MONTANT (avant taxes)
        story.append(Spacer(1, 8))
        montant_tbl = Table(
            [[Paragraph("MONTANT DES TRAVAUX (avant taxes)",
                        ParagraphStyle("m", parent=body, fontName="Helvetica-Bold",
                                       textColor=colors.white)),
              Paragraph(f"{_frca_num(montant)} $",
                        ParagraphStyle("mv", parent=body, fontName="Helvetica-Bold",
                                       textColor=colors.white, alignment=2))]],
            colWidths=[doc.width * 0.6, doc.width * 0.4])
        montant_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(montant_tbl)

        # ── SAUT DE PAGE FORCÉ : fin page 1 (montant), début page 2.
        story.append(PageBreak())

        # 7bis. DOCUMENTS CONSULTÉS — déplacé en page 2, juste avant la
        #       signature (était auparavant page 1 après la présentation).
        docs = devis.get("documents") or []
        if isinstance(docs, str):
            try:
                docs = json.loads(docs)
            except Exception:  # noqa: BLE001
                docs = []
        story.append(Paragraph("DOCUMENTS CONSULTÉS", h))
        if docs:
            for nm in docs:
                story.append(Paragraph(f"• {esc(nm)}", body))
        else:
            story.append(Paragraph("—", body))

        # 9. SIGNATURE : une seule colonne empilée à gauche, sans libellés
        #    « Responsable »/« Entrepreneur ». Ordre : nom -> titre ->
        #    organisation -> courriel -> téléphone. Organisation pré-remplie
        #    depuis l'entrepreneur (org du projet) si non saisie.
        story.append(Spacer(1, 24))
        nom_resp = devis.get("responsable_nom") or user.get("nom") or "—"
        titre_resp = devis.get("titre_responsable")
        org_resp = devis.get("organisation") or entreprise.get("name")
        courriel_resp = devis.get("responsable_email") or user.get("email") or "—"
        tel_resp = devis.get("responsable_tel") or "—"

        sig_name_style = ParagraphStyle("signame", parent=small,
                                        fontName="Helvetica-Bold", textColor=ACCENT)
        sig_lines = [[Paragraph(esc(nom_resp), sig_name_style)]]
        if titre_resp:
            sig_lines.append([Paragraph(esc(titre_resp), small)])
        if org_resp:
            sig_lines.append([Paragraph(esc(org_resp), small)])
        sig_lines.append([Paragraph(f"<b>Courriel :</b> {esc(courriel_resp)}", small)])
        sig_lines.append([Paragraph(f"<b>Téléphone :</b> {esc(tel_resp)}", small)])
        sig = Table(sig_lines, colWidths=[doc.width * 0.5])
        sig.hAlign = "LEFT"  # défaut Table = CENTER -> calait le bloc à droite
        sig.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                 ("TOPPADDING", (0, 0), (-1, -1), 1),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                                 ("LINEABOVE", (0, 0), (0, 0), 0.5, MUTED),
                                 ("TOPPADDING", (0, 0), (0, 0), 6)]))
        story.append(sig)

        # 10. FOOTER : « Propulsé par » + logo Ad FLO — source unique partagée
        #     avec le rapport de calcul (draw_adflo_footer, ad_budget_api).
        #     nb propage le mode couleur/N&B du devis.
        def _footer(canvas, _doc):
            draw_adflo_footer(canvas, _doc, nb=nb)

        doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
        buf.seek(0)

        # Snapshot proposition_devis : montant_avant_taxes = le quantitatif (le
        # prix du devis EST le quantitatif). Bloc devis rempli ; totaux détaillés
        # (mat/mo/st, heures, A&P) restent côté rapport de calcul (null ici).
        def _lines(txt):
            return [ln.strip() for ln in (txt or "").split("\n") if ln.strip()]
        m = float(montant or 0)
        tps = round(m * 0.05, 2)
        tvq = round(m * 0.09975, 2)
        snapshot = {
            "schema_version": 1,
            "report_type": "proposition_devis",
            "project": {"central_project_id": projet.get("ad_hub_project_id"),
                        "nom": _ident.get("nom")},
            "revision": {"no": projet.get("revision_no", 0),
                         "label": projet.get("revision_label") or "Originale"},
            "options": {"arrondi_dollar": bool(projet.get("arrondi_dollar"))},
            "totaux": {
                "sous_total_mat": None, "sous_total_mo": None, "sous_total_st": None,
                "montant_avant_taxes": round(m, 2),
                "tps": tps, "tvq": tvq,
                "montant_apres_taxes": round(m + tps + tvq, 2),
            },
            "heures": {"mo": None, "contremaitre": None, "total": None},
            "admin_profit": {"mode": None, "details": []},
            "regroupements_csi": [],
            "devis": {
                "destinataire_contact": _ident.get("contact_client"),
                "travaux_inclus": _lines(devis.get("travaux_inclus")),
                "travaux_non_inclus": _lines(devis.get("travaux_non_inclus")),
            },
        }
        return buf, snapshot, _ident_source

    return router
