import io
from collections import OrderedDict
from datetime import date

import openpyxl
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from psycopg2.extras import RealDictCursor
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

router = APIRouter(prefix="/budget", tags=["Ad Budget"])


def register_ad_budget_routes(get_conn):

    # ══════════════════════════════════════════════════════════
    # BASE DE DONNÉES PRINCIPALE (admin seulement)
    # ══════════════════════════════════════════════════════════

    @router.get("/prix-moyens")
    def get_budget_prix_moyens():
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT *
            FROM ad_budget.ad_budget_prix_moyens
            ORDER BY section, description;
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    @router.post("/item")
    def create_item(data: dict):
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            INSERT INTO ad_budget.ad_budget_prix_moyens
            (section, description, unite, prix_unitaire)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (
            data["section"],
            data["description"],
            data["unite"],
            data["prix_unitaire"]
        ))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "created", "id": row["id"]}

    @router.get("/search")
    def search_budget(q: str = ""):
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        q_clean = q.replace("-", " ").strip()
        words = [w for w in q_clean.split() if w]
        if not words:
            cur.close()
            conn.close()
            return []
        conditions = []
        params = []
        for word in words:
            conditions.append("""
                (description ILIKE %s OR section ILIKE %s OR division ILIKE %s)
            """)
            params.extend([f"%{word}%", f"%{word}%", f"%{word}%"])
        sql = f"""
            SELECT * FROM ad_budget.ad_budget_prix_moyens
            WHERE {" AND ".join(conditions)}
            ORDER BY section, description
            LIMIT 50;
        """
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    @router.get("/item/{item_id}")
    def get_item(item_id: int):
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM ad_budget.ad_budget_prix_moyens WHERE id = %s
        """, (item_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Item not found")
        return row

    @router.put("/item/{item_id}")
    def update_item(item_id: int, data: dict):
        conn = get_conn()
        cur = conn.cursor()
        fields = []
        values = []
        for field in ["section", "description", "unite", "prix_unitaire", "", "division", "note"]:
            if field in data:
                fields.append(f"{field} = %s")
                values.append(data[field])
        if not fields:
            cur.close()
            conn.close()
            return {"error": "No fields to update"}
        sql = f"UPDATE ad_budget.ad_budget_prix_moyens SET {', '.join(fields)} WHERE id = %s"
        values.append(item_id)
        cur.execute(sql, values)
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "updated"}

    @router.delete("/item/{item_id}")
    def delete_item(item_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM ad_budget.ad_budget_prix_moyens WHERE id = %s", (item_id,))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "deleted"}

    @router.delete("/items")
    def delete_items(data: dict):
        ids = data.get("ids", [])
        if not ids:
            return {"error": "No IDs provided"}
        conn = get_conn()
        cur = conn.cursor()
        placeholders = ','.join(['%s'] * len(ids))
        cur.execute(f"""
            DELETE FROM ad_budget.ad_budget_prix_moyens WHERE id IN ({placeholders})
        """, ids)
        deleted_count = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "deleted", "count": deleted_count}

    # ══════════════════════════════════════════════════════════
    # ADMIN — BD MAÎTRE (CRUD gardé par rôle admin)
    # ══════════════════════════════════════════════════════════

    def _require_admin(email: str):
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT role FROM ad_budget.users WHERE LOWER(email) = LOWER(%s)",
            (email or "",),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row or row["role"] != "admin":
            raise HTTPException(status_code=403, detail="Accès admin requis")

    @router.get("/admin/items")
    def admin_list_items(email: str = Query(...)):
        _require_admin(email)
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, section, division, description, unite, prix_unitaire, note
            FROM ad_budget.ad_budget_prix_moyens
            ORDER BY section, description
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    @router.post("/admin/items")
    def admin_create_item(data: dict, email: str = Query(...)):
        _require_admin(email)
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            INSERT INTO ad_budget.ad_budget_prix_moyens
                (section, division, description, unite, prix_unitaire, note)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, section, division, description, unite, prix_unitaire, note
        """, (
            data.get("section") or "",
            data.get("division") or None,
            data.get("description") or "",
            data.get("unite") or "global",
            data.get("prix_unitaire") or 0,
            data.get("note") or None,
        ))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "created", "item": row}

    @router.patch("/admin/items/{item_id}")
    def admin_update_item(item_id: int, data: dict, email: str = Query(...)):
        _require_admin(email)
        conn = get_conn()
        cur = conn.cursor()
        fields = []
        values = []
        for field in ["section", "division", "description", "unite", "prix_unitaire", "note"]:
            if field in data:
                fields.append(f"{field} = %s")
                values.append(data[field])
        if not fields:
            cur.close()
            conn.close()
            return {"error": "No fields to update"}
        sql = f"UPDATE ad_budget.ad_budget_prix_moyens SET {', '.join(fields)} WHERE id = %s"
        values.append(item_id)
        cur.execute(sql, values)
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "updated"}

    @router.delete("/admin/items/{item_id}")
    def admin_delete_item(item_id: int, email: str = Query(...)):
        _require_admin(email)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM ad_budget.ad_budget_prix_moyens WHERE id = %s", (item_id,))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "deleted"}

    # ══════════════════════════════════════════════════════════
    # USERS
    # ══════════════════════════════════════════════════════════

    @router.get("/users")
    def get_users():
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, nom, email, role, created_at
            FROM ad_budget.users
            ORDER BY nom;
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    @router.post("/users")
    def create_user(data: dict):
        EMAILS_AUTORISES = {"simon@adision.ca", "admin@adision.ca", "simon@contracta.ca", "povezina@contracta.ca", "steve@contracta.ca"}
        email = (data.get("email") or "").strip().lower()
        if email not in EMAILS_AUTORISES:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Email non autorise. Contactez l'administrateur.")
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            INSERT INTO ad_budget.users (nom, email, role)
            VALUES (%s, %s, %s)
            RETURNING id, nom, email, role
        """, (
            data["nom"],
            email,
            data.get("role", "user")
        ))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return row

    @router.get("/users/{user_id}")
    def get_user(user_id: int):
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, nom, email, role, created_at
            FROM ad_budget.users WHERE id = %s
        """, (user_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        return row

    @router.put("/users/{user_id}")
    def update_user(user_id: int, data: dict):
        conn = get_conn()
        cur = conn.cursor()
        fields = []
        values = []
        for field in ["nom", "email", "role"]:
            if field in data:
                fields.append(f"{field} = %s")
                values.append(data[field])
        if not fields:
            return {"error": "No fields to update"}
        sql = f"UPDATE ad_budget.users SET {', '.join(fields)} WHERE id = %s"
        values.append(user_id)
        cur.execute(sql, values)
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "updated"}

    @router.delete("/users/{user_id}")
    def delete_user(user_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM ad_budget.users WHERE id = %s", (user_id,))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "deleted"}

    # ══════════════════════════════════════════════════════════
    # PROJETS
    # ══════════════════════════════════════════════════════════

    @router.get("/projets")
    def get_projets(user_id: int = None):
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if user_id:
            cur.execute("""
                SELECT p.*, u.nom as user_nom
                FROM ad_budget.projets p
                JOIN ad_budget.users u ON u.id = p.user_id
                WHERE p.user_id = %s
                ORDER BY p.updated_at DESC;
            """, (user_id,))
        else:
            cur.execute("""
                SELECT p.*, u.nom as user_nom
                FROM ad_budget.projets p
                JOIN ad_budget.users u ON u.id = p.user_id
                ORDER BY p.updated_at DESC;
            """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    @router.post("/projets")
    def create_projet(data: dict):
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            INSERT INTO ad_budget.projets (user_id, nom, client, adresse, description, statut)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (
            data["user_id"],
            data["nom"],
            data.get("client", ""),
            data.get("adresse", ""),
            data.get("description", ""),
            data.get("statut", "en cours")
        ))
        row = cur.fetchone()
        projet_id = row["id"]
        cur.execute("INSERT INTO ad_budget.budget_lignes (projet_id, source_item_id, section, description, unite, prix_unitaire, qte, ajustement_pct, note, actif) SELECT %s, id, section, description, COALESCE(unite, 'global'), 	COALESCE(prix_unitaire, 0), 0, 0, COALESCE(note, ''), TRUE FROM ad_budget.ad_budget_prix_moyens", (projet_id,))
        nb_lignes = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "created", "projet": row, "nb_lignes_creees": nb_lignes}

    @router.get("/projets/{projet_id}")
    def get_projet(projet_id: int):
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT p.*, u.nom as user_nom
            FROM ad_budget.projets p
            JOIN ad_budget.users u ON u.id = p.user_id
            WHERE p.id = %s
        """, (projet_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Projet not found")
        return row

    @router.put("/projets/{projet_id}")
    def update_projet(projet_id: int, data: dict):
        conn = get_conn()
        cur = conn.cursor()
        fields = []
        values = []
        for field in ["nom", "client", "adresse", "description", "statut"]:
            if field in data:
                fields.append(f"{field} = %s")
                values.append(data[field])
        fields.append("updated_at = NOW()")
        if not fields:
            return {"error": "No fields to update"}
        sql = f"UPDATE ad_budget.projets SET {', '.join(fields)} WHERE id = %s"
        values.append(projet_id)
        cur.execute(sql, values)
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "updated"}

    @router.delete("/projets/{projet_id}")
    def delete_projet(projet_id: int):
        conn = get_conn()
        cur = conn.cursor()
        # Les budget_lignes sont supprimées automatiquement (CASCADE)
        cur.execute("DELETE FROM ad_budget.projets WHERE id = %s", (projet_id,))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "deleted"}

    @router.patch("/projets/{projet_id}/notes")
    def update_projet_notes(projet_id: int, data: dict):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE ad_budget.projets SET notes = %s, updated_at = NOW() WHERE id = %s",
            (data.get("notes", ""), projet_id),
        )
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "updated"}

    @router.post("/projets/{projet_id}/duplicate")
    def duplicate_projet(projet_id: int):
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM ad_budget.projets WHERE id = %s", (projet_id,))
        src = cur.fetchone()
        if not src:
            cur.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Projet not found")
        cur.execute("""
            INSERT INTO ad_budget.projets (user_id, nom, client, adresse, description, statut, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (
            src["user_id"],
            f"{src['nom']} (copie)",
            src.get("client") or "",
            src.get("adresse") or "",
            src.get("description") or "",
            src.get("statut") or "en cours",
            src.get("notes") or "",
        ))
        new_projet = cur.fetchone()
        new_id = new_projet["id"]
        cur.execute("""
            INSERT INTO ad_budget.budget_lignes
              (projet_id, source_item_id, section, description, unite, prix_unitaire, qte, ajustement_pct, note, actif)
            SELECT %s, source_item_id, section, description, unite, prix_unitaire, qte, ajustement_pct, note, actif
            FROM ad_budget.budget_lignes
            WHERE projet_id = %s
        """, (new_id, projet_id))
        nb_lignes = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "duplicated", "projet": new_projet, "nb_lignes_copiees": nb_lignes}

    @router.get("/projets/{projet_id}/export")
    def export_projet_excel(projet_id: int):
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT nom FROM ad_budget.projets WHERE id = %s", (projet_id,))
        projet = cur.fetchone()
        if not projet:
            cur.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Projet not found")
        cur.execute("""
            SELECT section, description, unite, qte, prix_unitaire, ajustement_pct, note
            FROM ad_budget.budget_lignes
            WHERE projet_id = %s
            ORDER BY section, description
        """, (projet_id,))
        lignes = cur.fetchall()
        cur.close()
        conn.close()

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Budget"
        ws.append(["Section", "Description", "Unité", "Quantité", "Prix unitaire", "Ajustement %", "Total", "Note"])

        total_general = 0.0
        for l in lignes:
            qte = float(l["qte"] or 0)
            prix = float(l["prix_unitaire"] or 0)
            adj = float(l["ajustement_pct"] or 0)
            total = qte * prix * (1 + adj / 100)
            total_general += total
            ws.append([l["section"], l["description"], l["unite"], qte, prix, adj, total, l["note"] or ""])

        ws.append([])
        ws.append(["", "", "", "", "", "TOTAL", total_general, ""])

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        safe_nom = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (projet["nom"] or "projet")).strip() or "projet"
        filename = f"{safe_nom}.xlsx"
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/projets/{projet_id}/pdf")
    def export_projet_pdf(
        projet_id: int,
        actifs_seulement: bool = True,
        avec_prix: bool = True,
        sections: str = Query("", description="CSV des sections à inclure ; vide = toutes"),
        colonnes: str = Query("", description="CSV des colonnes à inclure ; vide = toutes"),
        mobilisation: float = 0,
        surface_plancher: float = 0,
        hauteur_cloisons: float = 0,
        longueur_cloisons: float = 0,
    ):
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM ad_budget.projets WHERE id = %s", (projet_id,))
        projet = cur.fetchone()
        if not projet:
            cur.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Projet not found")

        where = ["projet_id = %s"]
        params = [projet_id]
        if actifs_seulement:
            where.append("actif = TRUE")
        sections_list = [s.strip() for s in sections.split(",") if s.strip()] if sections else []
        if sections_list:
            where.append("section = ANY(%s)")
            params.append(sections_list)

        cur.execute(
            f"""
            SELECT section, description, unite, qte, prix_unitaire, ajustement_pct,
                   sous_total, total, note
            FROM ad_budget.budget_lignes
            WHERE {' AND '.join(where)}
            ORDER BY section, description
            """,
            params,
        )
        lignes = cur.fetchall()
        cur.close()
        conn.close()

        sections_groups = OrderedDict()
        for l in lignes:
            sec = l["section"] or ""
            sections_groups.setdefault(sec, []).append(l)

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=letter,
            rightMargin=1.5 * cm, leftMargin=1.5 * cm,
            topMargin=1.5 * cm, bottomMargin=1.5 * cm,
            title=f"Rapport budget — {projet['nom']}",
        )
        ss = getSampleStyleSheet()
        story = []

        title_style = ParagraphStyle("PdfTitle", parent=ss["Title"], fontSize=18, alignment=0, spaceAfter=4)
        story.append(Paragraph(f"Rapport budget — {projet['nom']}", title_style))
        meta_style = ParagraphStyle("Meta", parent=ss["Normal"], fontSize=9, textColor=colors.grey)
        story.append(Paragraph(f"Émis le {date.today().strftime('%Y-%m-%d')}", meta_style))
        story.append(Spacer(1, 10))

        info_lines = []
        if projet.get("client"):
            info_lines.append(f"<b>Client :</b> {projet['client']}")
        if projet.get("adresse"):
            info_lines.append(f"<b>Adresse :</b> {projet['adresse']}")
        if projet.get("statut"):
            info_lines.append(f"<b>Statut :</b> {projet['statut']}")
        for line in info_lines:
            story.append(Paragraph(line, ss["Normal"]))
        if info_lines:
            story.append(Spacer(1, 8))

        params_lines = []
        if mobilisation:
            params_lines.append(f"Mobilisation : {mobilisation:g} sem")
        if surface_plancher:
            params_lines.append(f"Surface plancher : {surface_plancher:g} pi²")
        if hauteur_cloisons:
            params_lines.append(f"Hauteur cloisons : {hauteur_cloisons:g}")
        if longueur_cloisons:
            params_lines.append(f"Longueur cloisons : {longueur_cloisons:g}")
        if params_lines:
            story.append(Paragraph("<b>Paramètres :</b> " + " &nbsp;|&nbsp; ".join(params_lines), ss["Normal"]))
            story.append(Spacer(1, 8))

        if projet.get("notes"):
            story.append(Paragraph("<b>Notes du projet :</b>", ss["Normal"]))
            notes_html = (projet["notes"] or "").replace("\n", "<br/>")
            story.append(Paragraph(notes_html, ss["Normal"]))
            story.append(Spacer(1, 12))

        cell_style = ParagraphStyle("Cell", parent=ss["Normal"], fontSize=8, leading=10)

        def cell(text):
            return Paragraph(str(text), cell_style)

        ALL_COLS = ["section", "description", "qte", "unite", "prix_unitaire", "sous_total", "ajustement_pct", "total", "note"]
        COL_LABELS = {
            "section": "Section", "description": "Description", "qte": "Qté",
            "unite": "Unité", "prix_unitaire": "Prix unit.", "sous_total": "S/T",
            "ajustement_pct": "Adj %", "total": "Total", "note": "Note",
        }
        COL_WIDTHS = {
            "section": 55, "description": 130, "qte": 28, "unite": 30,
            "prix_unitaire": 50, "sous_total": 55, "ajustement_pct": 30,
            "total": 60, "note": 80,
        }
        PRIX_DEPENDENT = {"prix_unitaire", "sous_total", "ajustement_pct", "total", "note"}

        if colonnes:
            requested = {c.strip() for c in colonnes.split(",") if c.strip()}
        else:
            requested = set(ALL_COLS)
        if not avec_prix:
            requested -= PRIX_DEPENDENT
        selected = [c for c in ALL_COLS if c in requested] or ["section", "description"]
        headers = [COL_LABELS[c] for c in selected]
        col_widths = [COL_WIDTHS[c] for c in selected]

        def cell_for(col, l, qte, prix, adj, st, tot):
            if col == "section": return l["section"] or ""
            if col == "description": return cell(l["description"] or "")
            if col == "qte": return f"{qte:g}"
            if col == "unite": return l["unite"] or ""
            if col == "prix_unitaire": return f"{prix:,.2f}"
            if col == "sous_total": return f"{st:,.2f}"
            if col == "ajustement_pct": return f"{adj:g}"
            if col == "total": return f"{tot:,.2f}"
            if col == "note": return cell(l["note"] or "")
            return ""

        total_idx = selected.index("total") if "total" in selected else None
        desc_idx = selected.index("description") if "description" in selected else None
        show_totals_row = avec_prix and total_idx is not None

        def make_summary_row(label, value):
            row = [""] * len(selected)
            if desc_idx is not None and desc_idx != total_idx:
                row[desc_idx] = label
            elif total_idx is not None and total_idx > 0:
                row[total_idx - 1] = label
            row[total_idx] = f"{value:,.2f}"
            return row

        table_data = [headers]
        subtotal_rows = []
        grand_total = 0.0

        for sec, sec_lignes in sections_groups.items():
            sec_total = 0.0
            for l in sec_lignes:
                qte = float(l["qte"] or 0)
                prix = float(l["prix_unitaire"] or 0)
                adj = float(l["ajustement_pct"] or 0)
                st = float(l["sous_total"] or 0)
                tot = float(l["total"] or 0)
                sec_total += tot
                table_data.append([cell_for(c, l, qte, prix, adj, st, tot) for c in selected])
            if show_totals_row:
                table_data.append(make_summary_row(f"Sous-total {sec}", sec_total))
                subtotal_rows.append(len(table_data) - 1)
            grand_total += sec_total

        grand_total_row = None
        if show_totals_row:
            table_data.append(make_summary_row("GRAND TOTAL", grand_total))
            grand_total_row = len(table_data) - 1

        table_style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ]
        for r in subtotal_rows:
            table_style_cmds.append(("FONTNAME", (0, r), (-1, r), "Helvetica-Bold"))
            table_style_cmds.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#dbeafe")))
        if grand_total_row is not None:
            table_style_cmds.append(("FONTNAME", (0, grand_total_row), (-1, grand_total_row), "Helvetica-Bold"))
            table_style_cmds.append(("BACKGROUND", (0, grand_total_row), (-1, grand_total_row), colors.HexColor("#1e3a8a")))
            table_style_cmds.append(("TEXTCOLOR", (0, grand_total_row), (-1, grand_total_row), colors.white))

        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle(table_style_cmds))
        story.append(table)

        doc.build(story)
        buf.seek(0)

        safe_nom = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (projet["nom"] or "projet")).strip() or "projet"
        filename = f"{safe_nom}.pdf"
        return StreamingResponse(
            buf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ══════════════════════════════════════════════════════════
    # BUDGET LIGNES (items du budget d'un projet)
    # ══════════════════════════════════════════════════════════

    @router.get("/projets/{projet_id}/lignes")
    def get_budget_lignes(projet_id: int):
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT *
            FROM ad_budget.budget_lignes
            WHERE projet_id = %s
            ORDER BY section, description;
        """, (projet_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    @router.post("/projets/{projet_id}/lignes")
    def create_budget_ligne(projet_id: int, data: dict):
        """
        Ajoute un item au budget d'un projet.
        Si source_item_id est fourni, copie les données de la base principale.
        Sinon, utilise les données fournies directement.
        """
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        source_item_id = data.get("source_item_id")

        if source_item_id:
            # Copie depuis la base principale
            cur.execute("""
                SELECT section, description, unite, prix_unitaire
                FROM ad_budget.ad_budget_prix_moyens
                WHERE id = %s
            """, (source_item_id,))
            source = cur.fetchone()
            if not source:
                raise HTTPException(status_code=404, detail="Source item not found")
            section = source["section"]
            description = source["description"]
            unite = source["unite"]
            prix_unitaire = source["prix_unitaire"] or 0
        else:
            # Item personnalisé
            section = data.get("section", "Divers")
            description = data.get("description", "Nouvel élément")
            unite = data.get("unite", "global")
            prix_unitaire = data.get("prix_unitaire", 0)

        cur.execute("""
            INSERT INTO ad_budget.budget_lignes
            (projet_id, source_item_id, section, description, unite, prix_unitaire, qte, ajustement_pct, note, actif)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (
            projet_id,
            source_item_id,
            section,
            description,
            unite,
            prix_unitaire,
            data.get("qte", 0),
            data.get("ajustement_pct", 0),
            data.get("note", ""),
            data.get("actif", True)
        ))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "created", "ligne": row}

    @router.post("/projets/{projet_id}/lignes/import")
    def import_items_to_projet(projet_id: int, data: dict):
        """
        Importe plusieurs items de la base principale vers un projet.
        data = { "item_ids": [1, 2, 3, ...] }
        """
        item_ids = data.get("item_ids", [])
        if not item_ids:
            return {"error": "No item_ids provided"}

        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        inserted = 0
        for item_id in item_ids:
            cur.execute("""
                SELECT section, description, unite, prix_unitaire
                FROM ad_budget.ad_budget_prix_moyens
                WHERE id = %s
            """, (item_id,))
            source = cur.fetchone()
            if not source:
                continue
            cur.execute("""
                INSERT INTO ad_budget.budget_lignes
                (projet_id, source_item_id, section, description, unite, prix_unitaire)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                projet_id,
                item_id,
                source["section"],
                source["description"],
                source["unite"],
                source["prix_unitaire"] or 0
            ))
            inserted += 1

        conn.commit()
        cur.close()
        conn.close()
        return {"status": "imported", "count": inserted}

    @router.put("/projets/{projet_id}/lignes/{ligne_id}")
    def update_budget_ligne(projet_id: int, ligne_id: int, data: dict):
        conn = get_conn()
        cur = conn.cursor()
        fields = []
        values = []
        for field in ["section", "description", "unite", "prix_unitaire", "qte", "ajustement_pct", "note", "actif"]:
            if field in data:
                fields.append(f"{field} = %s")
                values.append(data[field])
        fields.append("updated_at = NOW()")
        if not fields:
            return {"error": "No fields to update"}
        sql = f"""
            UPDATE ad_budget.budget_lignes
            SET {', '.join(fields)}
            WHERE id = %s AND projet_id = %s
        """
        values.extend([ligne_id, projet_id])
        cur.execute(sql, values)
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "updated"}

    @router.delete("/projets/{projet_id}/lignes/{ligne_id}")
    def delete_budget_ligne(projet_id: int, ligne_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM ad_budget.budget_lignes
            WHERE id = %s AND projet_id = %s
        """, (ligne_id, projet_id))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "deleted"}

    @router.get("/projets/{projet_id}/total")
    def get_projet_total(projet_id: int):
        """Retourne le total du budget d'un projet groupé par section."""
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT
                section,
                COUNT(*) as nb_items,
                SUM(sous_total) as sous_total_section,
                SUM(total) as total_section
            FROM ad_budget.budget_lignes
            WHERE projet_id = %s AND actif = TRUE AND qte > 0
            GROUP BY section
            ORDER BY section;
        """, (projet_id,))
        sections = cur.fetchall()

        cur.execute("""
            SELECT
                SUM(total) as grand_total,
                COUNT(*) as nb_items_actifs
            FROM ad_budget.budget_lignes
            WHERE projet_id = %s AND actif = TRUE AND qte > 0;
        """, (projet_id,))
        totaux = cur.fetchone()

        cur.close()
        conn.close()
        return {
            "projet_id": projet_id,
            "sections": sections,
            "grand_total": totaux["grand_total"] or 0,
            "nb_items_actifs": totaux["nb_items_actifs"] or 0
        }

    return router
