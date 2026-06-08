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

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from modules import hub_service
from modules.auth_jwt import make_jwt_deps, _extract_bearer
from modules.ad_budget_api import build_pdf_logo, _load_and_authorize_projet

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


def _logo_flowable_for_org(org, max_width=150):
    """Image reportlab du logo entreprise depuis org.logo_url (R2 proxy stable).
    Fallback build_pdf_logo('') -> logo Adision si indispo."""
    url = (org or {}).get("logo_url")
    if url:
        try:
            r = httpx.get(url, timeout=10.0)
            if r.status_code == 200 and r.content:
                import base64
                return build_pdf_logo(base64.b64encode(r.content).decode("ascii"), max_width=max_width)
        except Exception as e:  # noqa: BLE001
            print(f"[devis] logo org fetch échec: {e}", flush=True)
    return build_pdf_logo("", max_width=max_width)


def register_ad_devis_routes(get_conn):
    jwt_user, jwt_user_or_token, _a, _s = make_jwt_deps(get_conn)
    router = APIRouter(prefix="/budget", tags=["Ad BUD — Devis"])

    def _load_devis(cur, projet_id):
        cur.execute("SELECT * FROM ad_budget.devis WHERE projet_id = %s", (projet_id,))
        return cur.fetchone()

    @router.get("/projets/{projet_id}/devis")
    def get_devis(projet_id: int, user=Depends(jwt_user),
                  authorization: Optional[str] = Header(None)):
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
        jwt_token = _extract_bearer(authorization, None)
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
                "nom": projet.get("nom_client") or projet.get("client"),
                "contact": projet.get("contact_client"),
                "courriel": projet.get("email_client"),
                "telephone": projet.get("telephone_client"),
            },
            "user": {"nom": user.get("nom"), "email": user.get("email")},
            "documents": documents,
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
                   responsable_nom, responsable_email, responsable_tel,
                   documents, couleur, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW())
                ON CONFLICT (projet_id) DO UPDATE SET
                  presentation = EXCLUDED.presentation,
                  travaux_inclus = EXCLUDED.travaux_inclus,
                  travaux_non_inclus = EXCLUDED.travaux_non_inclus,
                  responsable_nom = EXCLUDED.responsable_nom,
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
        montant: float = 0,
        couleur: str = "adision",
    ):
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
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
        jwt_token = _extract_bearer(authorization, token)
        entreprise = {}
        try:
            entreprise = hub_service.fetch_organization(jwt_token, projet.get("organization_id")) or {}
        except Exception as e:  # noqa: BLE001
            print(f"[devis] fetch_organization échec (pdf): {e}", flush=True)

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
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter,
                                rightMargin=margin, leftMargin=margin,
                                topMargin=margin, bottomMargin=2.2 * cm,
                                title=f"Devis — {projet.get('nom') or projet_id}")
        story = []

        # 1. EN-TÊTE : logo entreprise (gauche) + titre (droite)
        logo = _logo_flowable_for_org(entreprise, max_width=150)
        title_para = Paragraph("PROPOSITION / DEVIS", ParagraphStyle(
            "t", parent=ss["Normal"], fontSize=20, leading=24, alignment=2,
            textColor=ACCENT, fontName="Helvetica-Bold"))
        head = Table([[logo or "", title_para]], colWidths=[170, doc.width - 170])
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
            ("", projet.get("nom_client") or projet.get("client") or "—"),
            ("Contact :", projet.get("contact_client") or "—"),
            ("Courriel :", projet.get("email_client") or "—"),
            ("Téléphone :", projet.get("telephone_client") or "—"),
        ])
        two = Table([[entr, client]], colWidths=[doc.width / 2.0, doc.width / 2.0])
        two.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (0, 0), 12)]))
        story.append(two)

        # 4. PRÉSENTATION
        story.append(Paragraph("PRÉSENTATION DE LA PROPOSITION", h))
        story.append(para_multiline(devis.get("presentation") or "—"))

        # 5. DOCUMENTS CONSULTÉS
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

        # 6 + 7 : TRAVAUX INCLUS / NON INCLUS
        story.append(Paragraph("TRAVAUX INCLUS", h))
        story.append(para_multiline(devis.get("travaux_inclus") or "—"))
        story.append(Paragraph("TRAVAUX NON INCLUS", h))
        story.append(para_multiline(devis.get("travaux_non_inclus") or "—"))

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

        # 9. SIGNATURE : responsable (gauche) + entrepreneur (droite)
        story.append(Spacer(1, 24))
        resp = block("Responsable", [
            ("", devis.get("responsable_nom") or user.get("nom") or "—"),
            ("Courriel :", devis.get("responsable_email") or user.get("email") or "—"),
            ("Téléphone :", devis.get("responsable_tel") or "—"),
        ])
        entr_sig = block("Entrepreneur", [("", entreprise.get("name") or "—")])
        sig = Table([[resp, entr_sig]], colWidths=[doc.width / 2.0, doc.width / 2.0])
        sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                 ("LINEABOVE", (0, 0), (0, 0), 0.5, MUTED),
                                 ("LINEABOVE", (1, 0), (1, 0), 0.5, MUTED),
                                 ("TOPPADDING", (0, 0), (-1, -1), 6)]))
        story.append(sig)

        # 10. FOOTER : « Propulsé par Adision » + petite croix tricolore Ad FLO.
        def _footer(canvas, _doc):
            canvas.saveState()
            cx = _doc.pagesize[0] / 2.0
            y = 12 * mm
            canvas.setFont("Helvetica", 7)
            canvas.setFillColor(colors.HexColor("#94a3b8") if not nb else colors.HexColor("#6b7280"))
            txt = "Propulsé par Adision"
            canvas.drawCentredString(cx, y, txt)
            # Petite croix tricolore (Ad FLO) à gauche du texte.
            if not nb:
                w = canvas.stringWidth(txt, "Helvetica", 7)
                bx = cx - w / 2.0 - 10
                by = y - 0.5
                s = 2.2
                canvas.setFillColor(colors.HexColor("#1e3a8a"))
                canvas.rect(bx + s, by, s, s * 2, fill=1, stroke=0)
                canvas.setFillColor(colors.HexColor("#ef4444"))
                canvas.rect(bx, by, s, s, fill=1, stroke=0)
                canvas.setFillColor(colors.HexColor("#10b981"))
                canvas.rect(bx + s, by + s * 2, s, s, fill=1, stroke=0)
            canvas.restoreState()

        doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
        buf.seek(0)
        safe = "".join(c if c.isalnum() or c in "-_ " else "_"
                       for c in (projet.get("nom") or "devis")).strip() or "devis"
        return StreamingResponse(
            buf, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="devis_{safe}.pdf"'},
        )

    return router
