import base64
import io
import os
import re
from collections import OrderedDict
from datetime import date
from typing import Optional

import openpyxl
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from psycopg2.extras import RealDictCursor
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

DEFAULT_LOGO_PATH = os.path.join(
    os.path.dirname(__file__), "..", "assets", "adision_logo_default.png"
)

BUDGET_GROUPS_PDF = [
    ("conditions", "Conditions générales", "pct_admin_conditions", lambda n: n == 1),
    ("architecture", "Architecture", "pct_admin_architecture", lambda n: 2 <= n <= 14),
    ("mecanique", "Mécanique", "pct_admin_mecanique", lambda n: 20 <= n <= 28),
    ("excavation", "Excavation", "pct_admin_excavation", lambda n: n == 31),
]

# Termes de description qui déclenchent l'utilisation des surfaces globales
# (mêmes que côté frontend, voir App.jsx)
SURFACE_PLANCHER_TERMS = ["nettoyage", "revêtement de sol", "revetement de sol"]
SURFACE_MUR_TERMS = ["cloisons système intérieur", "cloisons systeme interieur"]
SURFACE_GYPSE_TERMS = ["plâtrage", "platrage", "peinture", "papier peint"]


def _effective_qte(ligne, mobilisation, surface_plancher, surface_mur, surface_gypse):
    """Qté effective d'une ligne en tenant compte des paramètres globaux du projet,
    miroir de getQte côté frontend (App.jsx). Permet d'avoir des totaux PDF qui
    matchent l'affichage de la page projet quand qte n'est pas persisté en DB."""
    desc = (ligne.get("description") or "").lower()
    unite = (ligne.get("unite") or "global").lower()
    if any(t in desc for t in SURFACE_PLANCHER_TERMS):
        return float(surface_plancher or 0)
    if any(t in desc for t in SURFACE_MUR_TERMS):
        return float(surface_mur or 0)
    if any(t in desc for t in SURFACE_GYPSE_TERMS):
        return float(surface_gypse or 0)
    if unite == "sem":
        return float(mobilisation or 0)
    return float(ligne.get("qte") or 0)


def _section_prefix_n(s: str):
    s = (s or "").strip()
    m = re.match(r"^(\d+)", s)
    return int(m.group(1)[:2]) if m else None


def _group_key_for(n):
    if n is None:
        return None
    for key, _, _, matches in BUDGET_GROUPS_PDF:
        if matches(n):
            return key
    return None


def build_pdf_logo(logo_base64: str, max_width: float = 110):
    """Return a reportlab Image flowable for the projet logo, or default if empty.
    Falls back to empty string if no source is usable.
    """
    source = None
    try:
        if logo_base64:
            data = logo_base64
            if "," in data:
                data = data.split(",", 1)[1]
            source = io.BytesIO(base64.b64decode(data))
        elif os.path.exists(DEFAULT_LOGO_PATH):
            source = DEFAULT_LOGO_PATH
        if source is None:
            return ""
        ir = ImageReader(source if isinstance(source, str) else io.BytesIO(source.getvalue()))
        iw, ih = ir.getSize()
        h = max_width * ih / iw if iw else max_width
        # For BytesIO, pass a fresh copy to Image() since ImageReader consumed it
        img_src = source if isinstance(source, str) else io.BytesIO(
            base64.b64decode(logo_base64.split(",", 1)[1] if "," in logo_base64 else logo_base64)
        )
        return Image(img_src, width=max_width, height=h)
    except Exception:
        return ""

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

        def _date(v):
            return None if v in (None, "") else v

        def _pct(v):
            return 0 if v in (None, "") else v

        cur.execute("""
            INSERT INTO ad_budget.projets
              (user_id, nom, client, adresse, description, statut,
               nom_client, contact_client, email_client, telephone_client,
               numero_projet, date_debut, date_fin,
               contact_entrepreneur, email_entrepreneur, telephone_entrepreneur,
               logo_base64,
               pct_admin_conditions, pct_admin_architecture,
               pct_admin_mecanique, pct_admin_excavation)
            VALUES (%s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s,
                    %s, %s, %s, %s)
            RETURNING *
        """, (
            data["user_id"],
            data["nom"],
            data.get("client", ""),
            data.get("adresse", ""),
            data.get("description", ""),
            data.get("statut", "en cours"),
            data.get("nom_client", ""),
            data.get("contact_client", ""),
            data.get("email_client", ""),
            data.get("telephone_client", ""),
            data.get("numero_projet", ""),
            _date(data.get("date_debut")),
            _date(data.get("date_fin")),
            data.get("contact_entrepreneur", ""),
            data.get("email_entrepreneur", ""),
            data.get("telephone_entrepreneur", ""),
            data.get("logo_base64", ""),
            _pct(data.get("pct_admin_conditions")),
            _pct(data.get("pct_admin_architecture")),
            _pct(data.get("pct_admin_mecanique")),
            _pct(data.get("pct_admin_excavation")),
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
        PCT_FIELDS = {
            "pct_admin_conditions", "pct_admin_architecture",
            "pct_admin_mecanique", "pct_admin_excavation",
        }
        for field in [
            "nom", "client", "adresse", "description", "statut",
            "nom_client", "contact_client", "email_client", "telephone_client",
            "numero_projet", "date_debut", "date_fin",
            "contact_entrepreneur", "email_entrepreneur", "telephone_entrepreneur",
            "logo_base64",
            "pct_admin_conditions", "pct_admin_architecture",
            "pct_admin_mecanique", "pct_admin_excavation",
        ]:
            if field in data:
                fields.append(f"{field} = %s")
                v = data[field]
                # Normalize empty values for typed columns
                if field in ("date_debut", "date_fin") and v == "":
                    v = None
                elif field in PCT_FIELDS and (v == "" or v is None):
                    v = 0
                values.append(v)
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
        cur.execute("SELECT id, nom FROM ad_budget.projets WHERE id = %s", (projet_id,))
        src = cur.fetchone()
        if not src:
            cur.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Projet not found")
        cur.execute("""
            INSERT INTO ad_budget.projets
              (user_id, nom, client, adresse, description, statut, notes,
               nom_client, contact_client, email_client, telephone_client,
               numero_projet, date_debut, date_fin,
               contact_entrepreneur, email_entrepreneur, telephone_entrepreneur,
               logo_base64,
               pct_admin_conditions, pct_admin_architecture,
               pct_admin_mecanique, pct_admin_excavation)
            SELECT user_id, %s, client, adresse, description, statut, notes,
                   nom_client, contact_client, email_client, telephone_client,
                   numero_projet, date_debut, date_fin,
                   contact_entrepreneur, email_entrepreneur, telephone_entrepreneur,
                   logo_base64,
                   pct_admin_conditions, pct_admin_architecture,
                   pct_admin_mecanique, pct_admin_excavation
            FROM ad_budget.projets
            WHERE id = %s
            RETURNING *
        """, (f"{src['nom']} (copie)", projet_id))
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
        avec_parametres: bool = True,
        sections: str = Query("", description="CSV des sections à inclure ; vide = toutes"),
        colonnes: str = Query("", description="CSV des colonnes à inclure ; vide = toutes"),
        sous_totaux: Optional[str] = Query(None, description="CSV des regroupements à afficher en sous-total ; param omis = tous, vide explicite = aucun"),
        admin_profits: Optional[str] = Query(None, description="CSV des regroupements pour admin&profit ; param omis = tous, vide explicite = aucun"),
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

        title_style = ParagraphStyle(
            "PdfTitle", parent=ss["Title"], fontSize=20, alignment=1,
            spaceAfter=0, textColor=colors.HexColor("#1e3a8a"),
        )
        title_para = Paragraph("RAPPORT DE BUDGET", title_style)
        logo_flowable = build_pdf_logo(projet.get("logo_base64") or "")
        title_row = Table(
            [[logo_flowable, title_para, ""]],
            colWidths=[120, 286, 120],
        )
        title_row.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (0, 0), "LEFT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(title_row)
        story.append(Spacer(1, 14))

        info_style = ParagraphStyle("Info", parent=ss["Normal"], fontSize=9, leading=13)

        def fmt_date(v):
            if v is None or v == "":
                return ""
            if hasattr(v, "strftime"):
                return v.strftime("%Y-%m-%d")
            return str(v)

        def field_line(label, value):
            if value:
                return f"<b>{label}</b> : {value}"
            return f"<b>{label}</b> : <font color='#94a3b8'>—</font>"

        client_html = "<br/>".join([
            "<b><font size='10' color='#1e3a8a'>CLIENT</font></b>",
            field_line("Nom du projet", projet.get("nom")),
            field_line("Nom du client", projet.get("nom_client") or projet.get("client")),
            field_line("Contact", projet.get("contact_client")),
            field_line("Courriel", projet.get("email_client")),
            field_line("Téléphone", projet.get("telephone_client")),
        ])
        ent_html = "<br/>".join([
            "<b><font size='10' color='#1e3a8a'>ENTREPRENEUR</font></b>",
            field_line("Date du jour", date.today().strftime("%Y-%m-%d")),
            field_line("Numéro du projet", projet.get("numero_projet")),
            field_line("Date début travaux", fmt_date(projet.get("date_debut"))),
            field_line("Date fin travaux", fmt_date(projet.get("date_fin"))),
            field_line("Contact entrepreneur", projet.get("contact_entrepreneur")),
            field_line("Courriel", projet.get("email_entrepreneur")),
            field_line("Téléphone", projet.get("telephone_entrepreneur")),
        ])

        client_para = Paragraph(client_html, info_style)
        ent_para = Paragraph(ent_html, info_style)

        statut_label = projet.get("statut") or "—"
        statut_para = Paragraph(f"<b>STATUT :</b> &nbsp;{statut_label}", info_style)
        col_w = 260
        statut_box = Table([[statut_para]], colWidths=[col_w])
        statut_box.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#1e3a8a")),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#dbeafe")),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))

        header_table = Table(
            [[client_para, ent_para], ["", statut_box]],
            colWidths=[col_w, col_w],
        )
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 1), (-1, 1), 8),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 14))

        if avec_parametres:
            surface_mur = (hauteur_cloisons or 0) * (longueur_cloisons or 0)
            surface_gypse = surface_mur * 2
            params_lines = []
            if mobilisation:
                params_lines.append(f"Mobilisation : {mobilisation:g} sem")
            if surface_plancher:
                params_lines.append(f"Surface plancher : {surface_plancher:g} pi²")
            if hauteur_cloisons:
                params_lines.append(f"Hauteur cloisons : {hauteur_cloisons:g}")
            if longueur_cloisons:
                params_lines.append(f"Longueur cloisons : {longueur_cloisons:g}")
            if surface_mur:
                params_lines.append(f"Surface mur : {surface_mur:g} pi²")
            if surface_gypse:
                params_lines.append(f"Surface gypse : {surface_gypse:g} pi²")
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

        # Surfaces dérivées (mêmes formules que côté frontend)
        surface_mur_calc = (hauteur_cloisons or 0) * (longueur_cloisons or 0)
        surface_gypse_calc = surface_mur_calc * 2

        # Résolution des regroupements sélectionnés (utilisée pour la distribution
        # pro rata sur les Total des lignes ET pour le bloc totaux en fin de PDF).
        # Sémantique : param omis (None) = tous sélectionnés (default rétrocompat) ;
        # chaîne vide = aucun sélectionné (l'utilisateur a explicitement décoché tout).
        all_group_keys = {key for key, _, _, _ in BUDGET_GROUPS_PDF}
        if sous_totaux is None:
            selected_st = set(all_group_keys)
        else:
            selected_st = {s.strip() for s in sous_totaux.split(",") if s.strip()} & all_group_keys
        if admin_profits is None:
            selected_ap = set(all_group_keys)
        else:
            selected_ap = {s.strip() for s in admin_profits.split(",") if s.strip()} & all_group_keys
        # Facteur d'affichage par regroupement : si sous-total inclus mais
        # admin & profit exclu, on gonfle le Total des lignes (et la sec_total
        # affichée) en distribuant le montant admin pro rata.
        group_factors = {}
        for key, _, pct_field, _ in BUDGET_GROUPS_PDF:
            if key in selected_st and key not in selected_ap:
                pct_d = float(projet.get(pct_field) or 0)
                group_factors[key] = 1 + pct_d / 100
            else:
                group_factors[key] = 1

        table_data = [headers]
        subtotal_rows = []
        group_subtotals = {key: 0.0 for key, _, _, _ in BUDGET_GROUPS_PDF}
        non_grouped_total = 0.0

        for sec, sec_lignes in sections_groups.items():
            sec_total = 0.0
            section_has_visible_lines = False
            for l in sec_lignes:
                qte = _effective_qte(l, mobilisation, surface_plancher,
                                     surface_mur_calc, surface_gypse_calc)
                if qte <= 0:
                    continue  # cohérent avec la page projet qui filtre qte > 0
                prix = float(l["prix_unitaire"] or 0)
                adj = float(l["ajustement_pct"] or 0)
                st = qte * prix
                tot_real = st * (1 + adj / 100)
                gkey = _group_key_for(_section_prefix_n(l["section"]))
                factor = group_factors.get(gkey, 1) if gkey is not None else 1
                tot = tot_real * factor  # gonflé si admin distribué
                sec_total += tot  # le sous-total par section CSI reflète l'affichage
                if gkey is not None:
                    group_subtotals[gkey] += tot_real  # on garde la valeur RÉELLE
                else:
                    non_grouped_total += tot_real
                # Le S/T par ligne (st) reste qte × prix (non gonflé) — seul le Total l'est.
                table_data.append([cell_for(c, l, qte, prix, adj, st, tot) for c in selected])
                section_has_visible_lines = True
            if show_totals_row and section_has_visible_lines:
                table_data.append(make_summary_row(f"Sous-total {sec}", sec_total))
                subtotal_rows.append(len(table_data) - 1)

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

        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle(table_style_cmds))
        story.append(table)

        # ── Totals block (per-regroupement subtotals + admin&profit + grand total) ──
        if avec_prix:
            totals_rows = []
            totals_kinds = []
            total_general = non_grouped_total
            for key, label, pct_field, _ in BUDGET_GROUPS_PDF:
                sub = group_subtotals[key]  # valeur réelle (sans facteur)
                if sub <= 0:
                    continue
                if key not in selected_st:
                    continue
                pct = float(projet.get(pct_field) or 0)
                ap = sub * pct / 100
                if key in selected_ap:
                    # Mode classique : sous-total et admin & profit affichés séparément
                    totals_rows.append([f"Sous-total {label}", f"{sub:,.2f} $"])
                    totals_kinds.append("subtotal")
                    total_general += sub
                    totals_rows.append([f"Administration et profit {pct:g}%", f"{ap:,.2f} $"])
                    totals_kinds.append("admin")
                    total_general += ap
                else:
                    # Mode distribution : sous-total gonflé (= sub + ap), pas de ligne admin
                    sub_displayed = sub + ap
                    totals_rows.append([f"Sous-total {label}", f"{sub_displayed:,.2f} $"])
                    totals_kinds.append("subtotal")
                    total_general += sub_displayed
            totals_rows.append(["TOTAL GÉNÉRAL", f"{total_general:,.2f} $"])
            totals_kinds.append("grand")

            totals_style = [
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOX", (0, 0), (-1, -1), 0.4, colors.grey),
            ]
            for i, kind in enumerate(totals_kinds):
                if kind == "subtotal":
                    totals_style.append(("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"))
                    totals_style.append(("LINEABOVE", (0, i), (-1, i), 0.4, colors.HexColor("#cbd5e1")))
                    totals_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f1f5f9")))
                elif kind == "admin":
                    totals_style.append(("LEFTPADDING", (0, i), (0, i), 32))
                    totals_style.append(("FONTSIZE", (0, i), (-1, i), 9))
                    totals_style.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor("#475569")))
                elif kind == "grand":
                    totals_style.append(("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"))
                    totals_style.append(("FONTSIZE", (0, i), (-1, i), 12))
                    totals_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#1e3a8a")))
                    totals_style.append(("TEXTCOLOR", (0, i), (-1, i), colors.white))
                    totals_style.append(("TOPPADDING", (0, i), (-1, i), 9))
                    totals_style.append(("BOTTOMPADDING", (0, i), (-1, i), 9))
                    totals_style.append(("LINEABOVE", (0, i), (-1, i), 1.5, colors.HexColor("#1e3a8a")))

            totals_table = Table(totals_rows, colWidths=[400, 126])
            totals_table.setStyle(TableStyle(totals_style))
            story.append(Spacer(1, 14))
            story.append(totals_table)

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
