import base64
import io
import json
import os
import re
from collections import OrderedDict
from datetime import date
from typing import Optional

import openpyxl
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row
from psycopg.types.json import Json

from modules.ad_budget_constants import AD_VIU_BLINDSPOT_DIVISIONS
from modules.aggregates import adapt_budget_lines, compute_aggregates
from modules.auth_jwt import make_jwt_deps
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
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

# Taxes Québec — taux fixes hardcodés
TPS_RATE = 0.05
TVQ_RATE = 0.09975

# === Sprint A : whitelists pour validation des champs projet ===
ALLOWED_STATUTS = {"brouillon", "adjuge", "complet", "perdu", "archive"}
ALLOWED_TYPES_BATIMENT = {
    "residentiel", "commercial", "institutionnel", "industriel", "mixte",
}
ALLOWED_REGIONS = {
    "Bas-Saint-Laurent", "Saguenay–Lac-Saint-Jean", "Capitale-Nationale",
    "Mauricie", "Estrie", "Montréal", "Outaouais", "Abitibi-Témiscamingue",
    "Côte-Nord", "Nord-du-Québec", "Gaspésie–Îles-de-la-Madeleine",
    "Chaudière-Appalaches", "Laval", "Lanaudière", "Laurentides",
    "Montérégie", "Centre-du-Québec",
}
DATE_ADJ_ALLOWED_FOR_STATUTS = {"adjuge", "complet", "perdu"}

# === Sprint B : statuts qui figent le budget (snapshot dans app_ana) ===
# Quand un projet bascule depuis un statut hors de ce set vers un statut dedans,
# le hook PUT /projets/{id} declenche la creation d'un snapshot consomme par
# Ad ANA. Note : le set est identique a DATE_ADJ_ALLOWED_FOR_STATUTS, mais
# semantiquement different (figement vs UI date picker), donc constante separee.
DEFINITIVE_STATUSES = {"adjuge", "complet", "perdu"}


def _validate_projet_fields(data: dict, current_statut: Optional[str] = None) -> None:
    """Valide les champs Sprint A dans `data`. Lève HTTPException 400 si invalide.

    `current_statut` = statut actuel en BD (utilisé pour la cross-field validation
    de date_adjudication quand le payload ne contient pas 'statut').
    """
    if "statut" in data and data["statut"] not in ALLOWED_STATUTS:
        raise HTTPException(status_code=400, detail="Statut invalide")
    if "type_batiment" in data:
        v = data["type_batiment"]
        if v not in (None, "") and v not in ALLOWED_TYPES_BATIMENT:
            raise HTTPException(status_code=400, detail="Type de bâtiment invalide")
    if "region" in data:
        v = data["region"]
        if v not in (None, "") and v not in ALLOWED_REGIONS:
            raise HTTPException(status_code=400, detail="Région invalide")
    if "superficie_m2" in data:
        v = data["superficie_m2"]
        if v not in (None, ""):
            try:
                fv = float(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Superficie invalide")
            if fv <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="La superficie doit être supérieure à 0",
                )
    if "date_adjudication" in data and data["date_adjudication"] not in (None, ""):
        # Statut effectif après ce PUT/POST : valeur du payload si présente,
        # sinon valeur actuelle en BD.
        effective_statut = data.get("statut", current_statut)
        if effective_statut not in DATE_ADJ_ALLOWED_FOR_STATUTS:
            raise HTTPException(
                status_code=400,
                detail="Date d'adjudication non autorisée pour ce statut",
            )


def _json_dumps_with_default(obj):
    """Sérialiseur JSON tolérant : Decimal et date deviennent des strings.
    Utilisé pour persister budget_lines_jsonb (rows BD bruts contenant
    Decimal et date) et aggregates_jsonb dans app_ana.project_snapshots.
    """
    return json.dumps(obj, default=str)


def _create_snapshot(cur, projet_row, trigger_event: str) -> int:
    """Crée un snapshot du projet dans app_ana.project_snapshots et le marque
    is_latest=TRUE. Marque les anciens snapshots du même projet à FALSE et
    met à jour ad_budget.projets.dernier_snapshot_id.

    `cur` est un curseur (row_factory=dict_row) déjà ouvert sur la transaction
    en cours. NE COMMIT PAS — l'appelant gère la transaction.
    Retourne l'id du snapshot créé.
    """
    projet_id = projet_row["id"]

    # 1. Récupérer les budget_lignes courantes (rows BD bruts)
    cur.execute(
        "SELECT * FROM ad_budget.budget_lignes WHERE projet_id = %s "
        "ORDER BY section, description",
        (projet_id,),
    )
    budget_rows = cur.fetchall()
    # RealDictRow -> dict pour serialisation JSON propre
    budget_lines_raw = [dict(r) for r in budget_rows]

    # 2. Calculer la qté EFFECTIVE pour chaque ligne (miroir de getQte
    # frontend / _effective_qte PDF). Sans ça, les lignes du squelette
    # template (qte=0 stocké en BD, qte calculée live côté frontend
    # depuis les params globaux) donnent des aggregates à 0.
    # Les params globaux du projet (mobilisation, surface_plancher, etc.)
    # viennent de projet_row directement (persistés en BD depuis le fix
    # 5cf146c). On mute UNE COPIE des rows pour le calcul ; budget_lines_raw
    # reste intact pour persister les lignes brutes (utile pour Ad ANA si
    # méthodo de calcul change un jour).
    mob = float(projet_row.get("mobilisation") or 0)
    sp = float(projet_row.get("surface_plancher") or 0)
    hc = float(projet_row.get("hauteur_cloisons") or 0)
    lc = float(projet_row.get("longueur_cloisons") or 0)
    surface_mur = hc * lc
    surface_gypse = surface_mur * 2

    # Pour le storage (budget_lines_jsonb) : ajouter qte_effective a chaque
    # row, en preservant la qte brute (audit trail "ce que user a tape").
    # Pour le calcul aggregates : copie ou qte = effective_qte (ce que JS
    # adapt_budget_lines/compute_aggregates attendent).
    # Vue 7 frontend lit qte_effective ?? qte pour agreger au niveau ligne.
    rows_with_eff_qte = []
    for r in budget_lines_raw:
        eff = _effective_qte(r, mob, sp, surface_mur, surface_gypse)
        r["qte_effective"] = eff  # mute en place -> sera persiste dans budget_lines_jsonb
        rc = dict(r)
        rc["qte"] = eff  # copie pour le compute (qte = effective)
        rows_with_eff_qte.append(rc)

    # 3. Adapter au format aggregate (mat/mo/st nested) puis calculer
    aggregate_lines = adapt_budget_lines(rows_with_eff_qte)
    superficie = projet_row.get("superficie_m2")
    aggregates = compute_aggregates(
        aggregate_lines,
        {
            "superficie_m2": float(superficie)
            if superficie is not None
            else None
        },
    )

    # 3. Marquer les snapshots précédents de ce projet comme non-latest.
    # Limité à WHERE is_latest = TRUE (touche au plus 1 row en pratique
    # grâce à l'invariant maintenu par cette fonction elle-même).
    cur.execute(
        "UPDATE app_ana.project_snapshots SET is_latest = FALSE "
        "WHERE projet_id = %s AND is_latest = TRUE",
        (projet_id,),
    )

    # 4. Insérer le nouveau snapshot
    cur.execute(
        """
        INSERT INTO app_ana.project_snapshots (
            projet_id, nom_projet, client_nom, statut, type_batiment,
            region, date_adjudication, superficie_m2,
            budget_lines_jsonb, aggregates_jsonb,
            trigger_event, schema_version, is_latest
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
        RETURNING id
        """,
        (
            projet_id,
            projet_row.get("nom"),
            projet_row.get("nom_client") or projet_row.get("client"),
            projet_row["statut"],
            projet_row.get("type_batiment"),
            projet_row.get("region"),
            projet_row.get("date_adjudication"),
            projet_row.get("superficie_m2"),
            Json(budget_lines_raw, dumps=_json_dumps_with_default),
            Json(aggregates, dumps=_json_dumps_with_default),
            trigger_event,
            aggregates.get("schema_version", 1),
        ),
    )
    snapshot_id = cur.fetchone()["id"]

    # 5. Mettre à jour la FK projet.dernier_snapshot_id (FK est ON DELETE
    # SET NULL côté BD, donc cohérent même si le snapshot est purgé).
    cur.execute(
        "UPDATE ad_budget.projets SET dernier_snapshot_id = %s WHERE id = %s",
        (snapshot_id, projet_id),
    )

    return snapshot_id


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


# Mapping notation Ad VIU v2 (ASCII plain, decision produit J7
# "Unites cibles HARDCODEES, pas d'exposants Unicode") -> notation
# Ad BUD frontend (allowedUnites avec Unicode + libelles longs).
# Cf. docs/AD_BUD_TECH_DEBT.md #3 pour la vraie solution long terme
# (standardize cross-module). Ce mapping est la couche d'integration
# en attendant.
_VIU_V2_TO_BUD_UNITE = {
    "pi2": "pi²",
    "m2":  "m²",
    "m3":  "m³",
    "un":  "unité",
    "pl":  "plin",
    "ml":  "mlin",
    "ea":  "unité",  # legacy 'each' -> 'unité' (avant les regles J6)
}


def _map_v2_unite_to_bud(unite: Optional[str]) -> Optional[str]:
    """Convertit une unite Ad VIU v2 (ASCII plain) vers la notation
    Ad BUD (allowedUnites du frontend). Passthrough si l'unite n'est
    pas dans la table de mapping (deja conforme ou cas inattendu)."""
    if not unite:
        return unite
    key = unite.strip().lower()
    return _VIU_V2_TO_BUD_UNITE.get(key, unite)


def _apply_master_template(
    cur, projet_id: int, *,
    skip_section_01: bool = False,
    default_divisions: Optional[list] = None,
) -> int:
    """Copie les items master (`ad_budget.ad_budget_prix_moyens`) dans
    `budget_lignes` du projet. C'est le squelette standard de soumission
    appliqué à toute création de projet — qte=0 par défaut, à compléter
    par l'utilisateur. Réutilisé par POST /budget/projets ET par POST
    /budget/projects/from-viu en mode=new pour cohérence d'archi.

    Filtres optionnels (mutuellement exclusifs) :
      - `skip_section_01=True` : exclut la division 01 (autres divisions
        ajoutees).
      - `default_divisions=['01', '20', '21', ...]` : importe UNIQUEMENT
        les lignes des divisions listees (filtre sur les 2 premiers
        caracteres de section). Utilise par from-viu-v2 mode=new pour
        importer les divisions transversales que le sous-module v2
        Architecture ne couvre pas (mecanique 20-25, electrique 26-28,
        excavation 31, plus les frais generaux 01).

    Le curseur est partagé pour rester dans la même transaction que la
    création du projet appelante. Retourne le nombre de lignes insérées.
    """
    if skip_section_01 and default_divisions is not None:
        raise ValueError(
            "skip_section_01 et default_divisions sont mutuellement exclusifs"
        )
    sql = (
        "INSERT INTO ad_budget.budget_lignes "
        "(projet_id, source_item_id, section, description, unite, "
        " prix_unitaire, qte, ajustement_pct, note, actif) "
        "SELECT %s, id, section, description, COALESCE(unite, 'global'), "
        "       COALESCE(prix_unitaire, 0), 0, 0, COALESCE(note, ''), TRUE "
        "FROM ad_budget.ad_budget_prix_moyens"
    )
    params: list = [projet_id]
    if skip_section_01:
        # Le pattern LIKE est parametrise (au lieu de '01 %' inline) pour
        # eviter que psycopg3 interprete le % comme placeholder mal forme.
        sql += " WHERE section NOT LIKE %s"
        params.append("01 %")
    elif default_divisions:
        # Filtre sur les 2 premiers chars de section (= code division CSI).
        # ANY(%s::text[]) accepte un array Python, idiomatique psycopg3.
        sql += " WHERE LEFT(section, 2) = ANY(%s)"
        params.append(list(default_divisions))
    cur.execute(sql, params)
    return cur.rowcount


def build_pdf_logo(logo_base64: str, max_width: float = 180):
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

def register_ad_budget_routes(get_conn):

    jwt_user, jwt_user_or_token, jwt_admin = make_jwt_deps(get_conn)

    # Toutes les routes /budget/* exigent un JWT valide avec module ad_bud.
    # Les routes admin imposent en plus role="admin" (Depends(jwt_admin)).
    router = APIRouter(
        prefix="/budget",
        tags=["Ad Budget"],
        dependencies=[Depends(jwt_user)],
    )

    # ══════════════════════════════════════════════════════════
    # BASE DE DONNÉES PRINCIPALE (admin seulement)
    # ══════════════════════════════════════════════════════════

    @router.get("/prix-moyens")
    def get_budget_prix_moyens():
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
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
        cur = conn.cursor(row_factory=dict_row)
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
        cur = conn.cursor(row_factory=dict_row)
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
        cur = conn.cursor(row_factory=dict_row)
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
    # ADMIN — BD MAÎTRE (CRUD gardé par rôle admin via JWT)
    # ══════════════════════════════════════════════════════════

    @router.get("/admin/items")
    def admin_list_items(_admin=Depends(jwt_admin)):
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
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
    def admin_create_item(data: dict, _admin=Depends(jwt_admin)):
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
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
    def admin_update_item(item_id: int, data: dict, _admin=Depends(jwt_admin)):
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
    def admin_delete_item(item_id: int, _admin=Depends(jwt_admin)):
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

    # Note : depuis le passage au SSO, les users sont auto-provisionnés au
    # premier login via le JWT (cf. modules/auth_jwt.py::_provision_user).
    # Ces endpoints restent dispo pour l'admin (ex. lister les users d'Ad BUD).
    # POST /users n'est plus utilisé pour le login — la whitelist est gérée
    # côté dashboard adision-app-api.

    @router.get("/users")
    def get_users(_admin=Depends(jwt_admin)):
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
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
    def create_user(data: dict, _admin=Depends(jwt_admin)):
        email = (data.get("email") or "").strip().lower()
        if not email:
            raise HTTPException(status_code=400, detail="Email requis")
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        cur.execute("""
            INSERT INTO ad_budget.users (nom, email, role)
            VALUES (%s, %s, %s)
            RETURNING id, nom, email, role
        """, (
            data.get("nom") or email.split("@", 1)[0],
            email,
            data.get("role", "user")
        ))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return row

    @router.get("/users/{user_id}")
    def get_user(user_id: int, _admin=Depends(jwt_admin)):
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
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
    def update_user(user_id: int, data: dict, _admin=Depends(jwt_admin)):
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
    def delete_user(user_id: int, _admin=Depends(jwt_admin)):
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
    def get_projets(
        user=Depends(jwt_user),
        statut: Optional[str] = None,
        type_batiment: Optional[str] = None,
        region: Optional[str] = None,
        client_nom: Optional[str] = None,
        include_archived: bool = False,
    ):
        # Toujours filtrer sur le user du JWT — l'éventuel ?user_id= en query
        # est ignoré (un user ne voit que ses propres projets).
        # Sprint A : nouveaux query params pour la liste filtrée. Par défaut,
        # les projets 'archive' sont masqués (sauf si include_archived=true ou
        # si on demande explicitement statut=archive).
        where = ["p.user_id = %s"]
        params = [user["id"]]
        if statut:
            where.append("p.statut = %s")
            params.append(statut)
        elif not include_archived:
            where.append("p.statut <> 'archive'")
        if type_batiment:
            where.append("p.type_batiment = %s")
            params.append(type_batiment)
        if region:
            where.append("p.region = %s")
            params.append(region)
        if client_nom:
            # Recherche sur les deux colonnes (nom_client = champ structuré,
            # client = champ libre legacy).
            where.append("(p.nom_client ILIKE %s OR p.client ILIKE %s)")
            params.append(f"%{client_nom}%")
            params.append(f"%{client_nom}%")
        # LEFT JOIN sur ad_budget.users : sans ce LEFT, les projets
        # orphelins (user_id n'a plus de row matching dans users —
        # cf. duplicate users docs/AD_BUD_TECH_DEBT.md #1) etaient
        # silencieusement exclus de la liste, donnant l'impression que
        # le projet "n'existait pas". user_nom devient NULL si pas de
        # match (frontend ne l'utilise pas).
        sql = (
            "SELECT p.*, u.nom as user_nom "
            "FROM ad_budget.projets p "
            "LEFT JOIN ad_budget.users u ON u.id = p.user_id "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY p.updated_at DESC"
        )
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    # ══════════════════════════════════════════════════════════
    # IMPORT DEPUIS AD VIU (push cross-module)
    # ══════════════════════════════════════════════════════════

    @router.get("/projects/mine")
    def projects_mine(user=Depends(jwt_user)):
        """Liste légère des projets du user — utilisée par le modal "Pousser
        vers Ad BUD" d'Ad VIU. nb_sections = nombre de valeurs section
        distinctes (codes CSI) déjà présentes dans le projet."""
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        cur.execute(
            """
            SELECT
                p.id,
                p.nom,
                p.updated_at AS date_creation,
                COALESCE(
                    COUNT(DISTINCT bl.section)
                        FILTER (WHERE bl.section IS NOT NULL AND bl.section <> ''),
                    0
                ) AS nb_sections
            FROM ad_budget.projets p
            LEFT JOIN ad_budget.budget_lignes bl ON bl.projet_id = p.id
            WHERE p.user_id = %s
            GROUP BY p.id, p.nom, p.updated_at
            ORDER BY p.updated_at DESC
            """,
            (user["id"],),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    @router.post("/projects/from-viu")
    def projects_from_viu(data: dict, user=Depends(jwt_user)):
        """Crée un projet (mode=new) ou ajoute des lignes à un projet existant
        (mode=existing) à partir des sections CSI détectées par Ad VIU.

        Mapping selon type_source :
        - type_source='soumission' : qte=1, unite='global', prix_unitaire=
          montant_total → la ligne reflète directement le montant détecté de
          la soumission, le total Ad BUD se met à jour immédiatement.
        - type_source='plan' / 'devis' (ou absent) : qte=0, unite='global',
          prix_unitaire=0 → squelette CSI à compléter par l'user dans Ad BUD.

        Idempotent sur (projet_id, section, description, source_file,
        type_source) — vérif explicite avant INSERT (pas d'INDEX UNIQUE pour
        laisser à l'user la liberté d'ajouter des doublons manuels). Si la
        ligne existe déjà, on SKIP — on ne met PAS à jour, ce qui préserve
        les modifs manuelles que l'user aurait faites dans Ad BUD entre les
        deux pushes.
        """
        mode = (data.get("mode") or "").strip()
        if mode not in ("new", "existing"):
            raise HTTPException(status_code=400, detail="mode doit être 'new' ou 'existing'")

        sections = data.get("sections") or []
        if not isinstance(sections, list) or not sections:
            raise HTTPException(status_code=400, detail="Aucune section à pousser")

        source_file = (data.get("source_file") or "").strip() or None

        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            template_lines_added = 0
            if mode == "new":
                project_name = (data.get("project_name") or "").strip()
                if not project_name:
                    raise HTTPException(
                        status_code=400,
                        detail="project_name requis pour mode=new",
                    )
                cur.execute(
                    """
                    INSERT INTO ad_budget.projets (user_id, nom, statut)
                    VALUES (%s, %s, 'brouillon')
                    RETURNING id, nom
                    """,
                    (user["id"], project_name),
                )
                proj = cur.fetchone()
                # Squelette standard appliqué au nouveau projet — même logique
                # que POST /budget/projets pour rester cohérent avec une création
                # manuelle. Les lignes Ad VIU s'ajoutent ensuite par-dessus
                # (coexistence assumée si même section, l'user choisit).
                template_lines_added = _apply_master_template(cur, proj["id"])
            else:
                project_id_in = data.get("project_id")
                if not project_id_in:
                    raise HTTPException(
                        status_code=400,
                        detail="project_id requis pour mode=existing",
                    )
                cur.execute(
                    "SELECT id, nom, user_id FROM ad_budget.projets WHERE id = %s",
                    (project_id_in,),
                )
                proj = cur.fetchone()
                if not proj:
                    raise HTTPException(status_code=404, detail="Projet introuvable")
                if proj["user_id"] != user["id"]:
                    raise HTTPException(
                        status_code=403,
                        detail="Ce projet ne vous appartient pas",
                    )

            project_id = proj["id"]

            # Sections déjà présentes AVANT le push, pour calculer "sections_added".
            cur.execute(
                """
                SELECT DISTINCT section
                FROM ad_budget.budget_lignes
                WHERE projet_id = %s AND section IS NOT NULL AND section <> ''
                """,
                (project_id,),
            )
            existing_sections = {row["section"] for row in cur.fetchall()}

            sections_touched = set()
            lines_added = 0
            note_text = (
                f"Importé depuis Ad VIU ({source_file})"
                if source_file else "Importé depuis Ad VIU"
            )

            for s in sections:
                section = (s.get("code_csi") or "").strip()
                description = (s.get("description") or s.get("nom") or "").strip()
                if not section:
                    continue

                type_source = (s.get("type_source") or "").strip() or None

                # Mapping selon type_source — depuis la refonte 3 sections,
                # une soumission Ad VIU pousse directement dans la section
                # SOUS-TRAITANT (type=Soumission, montant=montant_total) plutôt
                # qu'en matériel. Plan/devis = ligne placeholder à compléter.
                qte = 0
                prix_unitaire = 0
                sous_traitant_type = None
                sous_traitant_montant = 0
                sous_traitant_nom = None

                if type_source == "soumission":
                    try:
                        montant = float(s.get("montant_total") or 0)
                    except (TypeError, ValueError):
                        montant = 0.0
                    sous_traitant_type = "Soumission"
                    sous_traitant_montant = round(montant, 2)
                    nom = (s.get("entrepreneur") or s.get("nom_entrepreneur")
                           or s.get("sous_traitant_nom") or "").strip()
                    sous_traitant_nom = nom or None

                # Idempotence sur (projet_id, section, description, source_file,
                # type_source). Vérif explicite — si la ligne existe, on SKIP
                # (pas d'UPDATE) pour préserver d'éventuelles modifs manuelles.
                cur.execute(
                    """
                    SELECT id FROM ad_budget.budget_lignes
                    WHERE projet_id = %s
                      AND section = %s
                      AND COALESCE(description, '') = %s
                      AND COALESCE(source_file, '') = COALESCE(%s, '')
                      AND COALESCE(type_source, '') = COALESCE(%s, '')
                    LIMIT 1
                    """,
                    (project_id, section, description, source_file, type_source),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    """
                    INSERT INTO ad_budget.budget_lignes
                      (projet_id, section, description, unite, prix_unitaire,
                       qte, ajustement_pct, note, actif, source_file, type_source,
                       sous_traitant_type, sous_traitant_montant, sous_traitant_nom)
                    VALUES (%s, %s, %s, 'global', %s, %s, 0, %s, TRUE, %s, %s,
                            %s, %s, %s)
                    """,
                    (
                        project_id, section, description,
                        prix_unitaire, qte, note_text,
                        source_file, type_source,
                        sous_traitant_type, sous_traitant_montant, sous_traitant_nom,
                    ),
                )
                lines_added += 1
                sections_touched.add(section)

            new_sections_count = len(sections_touched - existing_sections)
            conn.commit()
            return {
                "project_id": project_id,
                "project_name": proj["nom"],
                "sections_added": new_sections_count,
                "lines_added": lines_added,
                # Nombre de lignes du squelette standard ajoutées si le projet
                # vient d'être créé (mode=new). 0 en mode=existing.
                "template_lines_added": template_lines_added,
            }
        except HTTPException:
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    # ════════════════════════════════════════════════════════════════════
    # Ad VIU v2 - Push d'une analyse vers Ad BUD (Jalon 4)
    # ════════════════════════════════════════════════════════════════════
    @router.post("/projects/from-viu-v2")
    def projects_from_viu_v2(data: dict, user=Depends(jwt_user)):
        """Cree un projet (mode=new) ou ajoute des lignes a un projet existant
        (mode=existing) a partir des items detectes/valides par Ad VIU v2.

        Mapping items v2 -> budget_lignes :
        - csi_section -> section
        - description -> description
        - unite -> unite (defaut 'global')
        - quantite (deja calculee cote frontend : finale > ia > 0) -> qte
        - prix_unitaire = 0 (l'utilisateur ajustera dans Ad BUD)
        - source_viu_analysis_id, source_viu_item_id : tracabilite + idempotence

        Idempotence : si une ligne avec le meme source_viu_item_id existe deja
        dans le projet, on SKIP (pas d'UPDATE, pour preserver les modifs manuelles
        que l'user aurait faites dans Ad BUD entre les deux pushes).
        """
        mode = (data.get("mode") or "").strip()
        if mode not in ("new", "existing"):
            raise HTTPException(status_code=400, detail="mode doit etre 'new' ou 'existing'")

        items = data.get("items") or []
        if not isinstance(items, list) or not items:
            raise HTTPException(status_code=400, detail="Aucun item a pousser")

        source_analysis_id = data.get("source_analysis_id")
        try:
            source_analysis_id = int(source_analysis_id) if source_analysis_id is not None else None
        except (TypeError, ValueError):
            source_analysis_id = None

        source_file = (data.get("source_pdf_filename") or "").strip() or None

        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            template_lines_added = 0
            if mode == "new":
                project_name = (data.get("project_name") or "").strip()
                if not project_name:
                    raise HTTPException(
                        status_code=400,
                        detail="project_name requis pour mode=new",
                    )
                cur.execute(
                    """
                    INSERT INTO ad_budget.projets (user_id, nom, statut)
                    VALUES (%s, %s, 'brouillon')
                    RETURNING id, nom
                    """,
                    (user["id"], project_name),
                )
                proj = cur.fetchone()
                # Squelette complet sans filtre. Le DELETE REPLACE ci-dessous
                # va immediatement effacer les lignes architecturales que ce
                # squelette vient de creer — les seules lignes squelette
                # conservees sont celles des divisions blindspot Ad VIU
                # (mecanique, civil, conditions generales) que l'estimateur
                # remplira manuellement (Ad VIU n'analyse pas ces divisions).
                template_lines_added = _apply_master_template(cur, proj["id"])
            else:
                project_id_in = data.get("project_id")
                if not project_id_in:
                    raise HTTPException(
                        status_code=400,
                        detail="project_id requis pour mode=existing",
                    )
                cur.execute(
                    "SELECT id, nom, user_id FROM ad_budget.projets WHERE id = %s",
                    (project_id_in,),
                )
                proj = cur.fetchone()
                if not proj:
                    raise HTTPException(status_code=404, detail="Projet introuvable")
                if proj["user_id"] != user["id"]:
                    raise HTTPException(
                        status_code=403,
                        detail="Ce projet ne vous appartient pas",
                    )

            project_id = proj["id"]

            # === DELETE REPLACE v3 (2026-05-12, fix doublons re-push) =====
            # Efface :
            #   (a) Toutes les lignes des divisions NON-blindspot
            #       (architectural skeleton + items Ad VIU precedents la-bas)
            #   (b) Tous les items Ad VIU (type_source='viu_v2') peu importe
            #       la division, INCLUANT les divisions blindspot
            #
            # Le (b) corrige le bug doublons : sans lui, un re-push avec des
            # source_viu_item_id differents (suite a un Refaire de l'analyse
            # Ad VIU) ajoutait des copies des items dans les divisions
            # blindspot car le DELETE (a) ne les touchait pas et l'idempotence
            # par source_viu_item_id ne reconnaissait pas les nouveaux IDs.
            #
            # Resultat : apres push, dans les divisions blindspot subsistent
            # UNIQUEMENT les lignes manuelles / master template (squelette
            # Ad BUD), pas d'items Ad VIU des pushs precedents. Push = REPLACE
            # propre de la contribution Ad VIU au projet.
            #
            # Format section : REGEXP_REPLACE(..., '\D', '') strip tous les
            # non-chiffres pour matcher "02 - Site Preparation" comme "02".
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE LEFT(REGEXP_REPLACE(COALESCE(section, ''), '\\D', '', 'g'), 2)
                              NOT IN ('01','20','21','22','23','25','26','27','28','31','32','33')
                    ) AS non_blindspot,
                    COUNT(*) FILTER (
                        WHERE type_source = 'viu_v2'
                    ) AS viu_items,
                    COUNT(*) FILTER (
                        WHERE LEFT(REGEXP_REPLACE(COALESCE(section, ''), '\\D', '', 'g'), 2)
                              IN ('01','20','21','22','23','25','26','27','28','31','32','33')
                          AND (type_source IS NULL OR type_source <> 'viu_v2')
                    ) AS blindspot_skeleton
                FROM ad_budget.budget_lignes
                WHERE projet_id = %s
                """,
                (project_id,),
            )
            counts_before = cur.fetchone()
            print(
                f"[from_viu_v2] project {project_id}: BEFORE DELETE — "
                f"total={counts_before['total']}, "
                f"non_blindspot_to_delete={counts_before['non_blindspot']}, "
                f"viu_items_to_delete={counts_before['viu_items']}, "
                f"blindspot_skeleton_preserved={counts_before['blindspot_skeleton']}",
                flush=True,
            )

            cur.execute(
                """
                DELETE FROM ad_budget.budget_lignes
                WHERE projet_id = %s
                  AND (
                      LEFT(REGEXP_REPLACE(COALESCE(section, ''), '\\D', '', 'g'), 2) NOT IN (
                          '01','20','21','22','23','25','26','27','28','31','32','33'
                      )
                      OR type_source = 'viu_v2'
                  )
                """,
                (project_id,),
            )
            deleted_count = cur.rowcount
            print(
                f"[from_viu_v2] project {project_id}: DELETE REPLACE — "
                f"rowcount={deleted_count}",
                flush=True,
            )

            # Log samples post-DELETE pour identifier format du squelette
            # qui survit. Apres ce DELETE, ne devraient survivre QUE des
            # lignes blindspot non-viu (squelette Ad BUD manuel/master).
            cur.execute(
                """
                SELECT section, COUNT(*) AS n
                FROM ad_budget.budget_lignes
                WHERE projet_id = %s
                GROUP BY section
                ORDER BY section
                LIMIT 8
                """,
                (project_id,),
            )
            samples = [(r["section"], r["n"]) for r in cur.fetchall()]
            print(
                f"[from_viu_v2] project {project_id}: AFTER DELETE — "
                f"sample sections (max 8): {samples}",
                flush=True,
            )

            # Recolte les sections survivantes pour calculer correctement
            # le compte de nouvelles sections introduites par l'INSERT.
            cur.execute(
                """
                SELECT DISTINCT section
                FROM ad_budget.budget_lignes
                WHERE projet_id = %s AND section IS NOT NULL AND section <> ''
                """,
                (project_id,),
            )
            existing_sections = {row["section"] for row in cur.fetchall()}

            sections_touched = set()
            inserted_count = 0
            lines_skipped = 0

            # Defensive dedup : evite les doublons quand Ad VIU envoie 2 items
            # avec meme (section, description) dans le meme payload. Cas
            # observe en prod 2026-05-12 sur projet 93 / analyse #18 (la
            # cause exacte cote Ad VIU reste a investiguer separement, mais
            # le defensive dedup ici evite la corruption d'Ad BUD).
            # Cle = (section_normalized, description_normalized.lower()) pour
            # matcher meme avec variance de casse ou espaces.
            seen_keys: set[tuple[str, str]] = set()

            note_prefix = (
                f"Importe d'Ad VIU v2 - Analyse #{source_analysis_id}"
                if source_analysis_id else "Importe d'Ad VIU v2"
            )

            for item in items:
                section = (item.get("csi_section") or "").strip()
                description = (item.get("description") or "").strip()
                if not description:
                    continue

                # Dedup intra-push : si on a deja INSERTed un item avec meme
                # (section, description) dans cette transaction, on skip.
                dedup_key = (section, description.lower())
                if dedup_key in seen_keys:
                    lines_skipped += 1
                    print(
                        f"[from_viu_v2] project {project_id}: dedup skip — "
                        f"section={section!r} description={description[:60]!r}",
                        flush=True,
                    )
                    continue
                seen_keys.add(dedup_key)

                unite = (item.get("unite") or "global").strip() or "global"
                # Mapping ASCII (Ad VIU v2) -> Unicode/libelle long (Ad BUD
                # allowedUnites). Sans ce mapping, le <select> Ad BUD ne
                # selectionnait rien et l'estimateur voyait les cellules
                # unite vides. Cf. docs/AD_BUD_TECH_DEBT.md #3.
                unite = _map_v2_unite_to_bud(unite) or "global"

                qte = item.get("quantite")
                if qte is None:
                    qte = item.get("quantite_ia")
                try:
                    qte = float(qte) if qte is not None else 0.0
                except (TypeError, ValueError):
                    qte = 0.0

                viu_item_id = item.get("id")
                try:
                    viu_item_id = int(viu_item_id) if viu_item_id is not None else None
                except (TypeError, ValueError):
                    viu_item_id = None

                # L'idempotence par source_viu_item_id n'est PLUS necessaire
                # depuis le DELETE REPLACE v3 (2026-05-12) : tous les items
                # type_source='viu_v2' precedents sont effaces avant cet
                # INSERT, peu importe leur source_viu_item_id. lines_skipped
                # reste a 0 (champ conserve dans response pour backward compat
                # frontend mais semantiquement obsolete).

                note_parts = [note_prefix]
                item_notes = (item.get("notes") or "").strip()
                if item_notes:
                    note_parts.append(item_notes)
                note_text = " | ".join(note_parts)

                cur.execute(
                    """
                    INSERT INTO ad_budget.budget_lignes
                      (projet_id, section, description, unite, prix_unitaire,
                       qte, ajustement_pct, note, actif, source_file, type_source,
                       source_viu_analysis_id, source_viu_item_id)
                    VALUES (%s, %s, %s, %s, 0, %s, 0, %s, TRUE, %s, 'viu_v2',
                            %s, %s)
                    """,
                    (
                        project_id, section or None, description, unite,
                        qte, note_text,
                        source_file,
                        source_analysis_id, viu_item_id,
                    ),
                )
                inserted_count += 1
                if section:
                    sections_touched.add(section)

            new_sections_count = len(sections_touched - existing_sections)
            conn.commit()
            return {
                "project_id": project_id,
                "project_name": proj["nom"],
                # Nouvelle structure (Phase 2 — 2026-05-12) : REPLACE
                # behavior. deleted_count = squelette architectural + items
                # Ad VIU precedents vires ; inserted_count = nouveaux items
                # detectes par Ad VIU effectivement inseres.
                "deleted_count": deleted_count,
                "inserted_count": inserted_count,
                "message": (
                    f"{deleted_count} ligne(s) supprimee(s) "
                    f"(squelette architectural remplace), "
                    f"{inserted_count} item(s) insere(s) depuis Ad VIU."
                ),
                # Champs legacy preserves pour backward compat avec le
                # frontend V2PushToBudModal en prod (qui lit lines_added,
                # lines_skipped, sections_added, template_lines_added).
                "lines_added": inserted_count,
                "lines_skipped": lines_skipped,
                "sections_added": new_sections_count,
                "template_lines_added": template_lines_added,
            }
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Erreur push : {e}")
        finally:
            cur.close()
            conn.close()
    @router.post("/projets")
    def create_projet(data: dict, user=Depends(jwt_user)):
        # On force user_id depuis le JWT, pas depuis le body — un user ne
        # peut pas créer un projet pour quelqu'un d'autre.
        data["user_id"] = user["id"]
        # Sprint A : valider les champs descriptifs avant d'insérer. Le default
        # de statut à la création est 'brouillon', donc passé en current_statut
        # pour la cross-field validation de date_adjudication.
        _validate_projet_fields(data, current_statut="brouillon")
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)

        def _date(v):
            return None if v in (None, "") else v

        def _pct(v):
            return 0 if v in (None, "") else v

        def _opt(v):
            return None if v in (None, "") else v

        cur.execute("""
            INSERT INTO ad_budget.projets
              (user_id, nom, client, adresse, description, statut,
               nom_client, contact_client, email_client, telephone_client,
               numero_projet, date_debut, date_fin,
               contact_entrepreneur, email_entrepreneur, telephone_entrepreneur,
               logo_base64,
               pct_admin_conditions, pct_admin_architecture,
               pct_admin_mecanique, pct_admin_excavation,
               type_batiment, region, date_adjudication, superficie_m2)
            VALUES (%s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s)
            RETURNING *
        """, (
            data["user_id"],
            data["nom"],
            data.get("client", ""),
            data.get("adresse", ""),
            data.get("description", ""),
            data.get("statut", "brouillon"),
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
            # Sprint A — tous nullables.
            _opt(data.get("type_batiment")),
            _opt(data.get("region")),
            _date(data.get("date_adjudication")),
            _opt(data.get("superficie_m2")),
        ))
        row = cur.fetchone()
        projet_id = row["id"]
        # Squelette complet (~180 lignes catalogue master, toutes divisions).
        # Le filtre AUTHORIZED_DIVISIONS du commit 3a35308 (2026-05-11) a ete
        # reverte : la creation manuelle de projet n'a pas a savoir ce que
        # Ad VIU analyse, l'estimateur veut son squelette complet pour saisie
        # manuelle. Le filtre n'est applique que sur push Ad VIU (DELETE
        # REPLACE dans from-viu-v2).
        nb_lignes = _apply_master_template(cur, projet_id)
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "created", "projet": row, "nb_lignes_creees": nb_lignes}

    @router.get("/projets/{projet_id}")
    def get_projet(projet_id: int):
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        # LEFT JOIN : meme rationale que get_projets ci-dessus. Sans LEFT
        # JOIN, un projet dont user_id pointe vers une row absente de
        # ad_budget.users (orphelin, cf. docs/AD_BUD_TECH_DEBT.md #1)
        # remontait 404, cassant le deep-link "Ouvrir dans Ad BUD" depuis
        # Ad VIU v2.
        cur.execute("""
            SELECT p.*, u.nom as user_nom
            FROM ad_budget.projets p
            LEFT JOIN ad_budget.users u ON u.id = p.user_id
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
        cur = conn.cursor(row_factory=dict_row)
        # Sprint A : récupère le statut actuel pour la cross-field validation
        # (date_adjudication permise uniquement si statut ∈ adjuge/complet/perdu).
        cur.execute(
            "SELECT statut FROM ad_budget.projets WHERE id = %s",
            (projet_id,),
        )
        existing = cur.fetchone()
        if not existing:
            cur.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Projet not found")
        _validate_projet_fields(data, current_statut=existing["statut"])

        fields = []
        values = []
        PCT_FIELDS = {
            "pct_admin_conditions", "pct_admin_architecture",
            "pct_admin_mecanique", "pct_admin_excavation",
        }
        # Champs qui doivent être normalisés à NULL si reçus en chaîne vide
        # (typés DATE, NUMERIC, ou VARCHAR optionnels).
        NULLABLE_EMPTY_FIELDS = {
            "date_debut", "date_fin",
            "type_batiment", "region", "date_adjudication", "superficie_m2",
            # Params globaux du tableau budget (persistes maintenant)
            "mobilisation", "surface_plancher",
            "hauteur_cloisons", "longueur_cloisons",
        }
        for field in [
            "nom", "client", "adresse", "description", "statut",
            "nom_client", "contact_client", "email_client", "telephone_client",
            "numero_projet", "date_debut", "date_fin",
            "contact_entrepreneur", "email_entrepreneur", "telephone_entrepreneur",
            "logo_base64",
            "pct_admin_conditions", "pct_admin_architecture",
            "pct_admin_mecanique", "pct_admin_excavation",
            # Sprint A
            "type_batiment", "region", "date_adjudication", "superficie_m2",
            "dernier_snapshot_id",
            # Params globaux du tableau budget
            "mobilisation", "surface_plancher",
            "hauteur_cloisons", "longueur_cloisons",
        ]:
            if field in data:
                v = data[field]
                if field in NULLABLE_EMPTY_FIELDS and v == "":
                    v = None
                elif field in PCT_FIELDS and (v == "" or v is None):
                    v = 0
                fields.append(f"{field} = %s")
                values.append(v)
        if not fields:
            cur.close()
            conn.close()
            return {"error": "No fields to update"}
        fields.append("updated_at = NOW()")
        sql = (
            f"UPDATE ad_budget.projets SET {', '.join(fields)} "
            "WHERE id = %s RETURNING *"
        )
        values.append(projet_id)
        cur.execute(sql, values)
        updated = cur.fetchone()

        # Sprint B : detecter la transition vers un statut definitif
        # (adjuge / complet / perdu) et figer le budget dans un snapshot.
        # Tout dans la meme transaction : si la creation du snapshot echoue,
        # l'UPDATE projet sera rollback aussi.
        old_statut = existing["statut"]
        new_statut = updated["statut"]
        if (
            old_statut not in DEFINITIVE_STATUSES
            and new_statut in DEFINITIVE_STATUSES
        ):
            snapshot_id = _create_snapshot(
                cur, updated, f"statut_change_to_{new_statut}"
            )
            updated["dernier_snapshot_id"] = snapshot_id

        conn.commit()
        cur.close()
        conn.close()
        return updated

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
        cur = conn.cursor(row_factory=dict_row)
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
              (projet_id, source_item_id, section, description, unite, prix_unitaire,
               qte, ajustement_pct, note, actif,
               heures, taux_horaire, cout_sous_traitant, sous_traitant_nom,
               ajust_materiaux, ajust_main_oeuvre, ajust_sous_traitant,
               sous_traitant_type, sous_traitant_montant)
            SELECT %s, source_item_id, section, description, unite, prix_unitaire,
                   qte, ajustement_pct, note, actif,
                   heures, taux_horaire, cout_sous_traitant, sous_traitant_nom,
                   ajust_materiaux, ajust_main_oeuvre, ajust_sous_traitant,
                   sous_traitant_type, sous_traitant_montant
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
        cur = conn.cursor(row_factory=dict_row)
        cur.execute("SELECT nom FROM ad_budget.projets WHERE id = %s", (projet_id,))
        projet = cur.fetchone()
        if not projet:
            cur.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Projet not found")
        cur.execute("""
            SELECT section, description, unite, qte, prix_unitaire,
                   ajust_materiaux, ajust_main_oeuvre, ajust_sous_traitant,
                   heures, taux_horaire,
                   sous_traitant_type, sous_traitant_montant, sous_traitant_nom,
                   note
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
        # Une seule rangée d'entêtes : pas de header de groupe (Excel rendrait
        # mal en sortie standard). Les colonnes sont nommées explicitement.
        ws.append([
            "Section", "Description", "Unité", "Qté",
            "Coût unitaire", "Ajust. mat. %", "S/T matériaux",
            "Heures", "Taux $", "Ajust. M-O %", "S/T main-d'œuvre",
            "Type S-T", "Sous-traitant", "Montant S-T", "Ajust. S-T %", "S/T sous-traitant",
            "Total ligne", "Note",
        ])

        total_materiaux = 0.0
        total_mo = 0.0
        total_st = 0.0
        for l in lignes:
            qte = float(l["qte"] or 0)
            prix = float(l["prix_unitaire"] or 0)
            ajm = float(l["ajust_materiaux"] or 0)
            heures = float(l["heures"] or 0)
            taux = float(l["taux_horaire"] or 0)
            ajmo = float(l["ajust_main_oeuvre"] or 0)
            st_montant = float(l["sous_traitant_montant"] or 0)
            ajst = float(l["ajust_sous_traitant"] or 0)
            # Formules de la refonte 3 sections : ajustement appliqué par
            # section, total ligne = somme des 3 sous-totaux. M-O et S-T ne
            # sont PAS multipliés par qte (contrairement aux matériaux).
            st_mat = qte * prix * (1 + ajm / 100)
            st_mo = heures * taux * (1 + ajmo / 100)
            st_st = st_montant * (1 + ajst / 100)
            total_ligne = st_mat + st_mo + st_st
            total_materiaux += st_mat
            total_mo += st_mo
            total_st += st_st
            ws.append([
                l["section"], l["description"], l["unite"], qte,
                prix, ajm, st_mat,
                heures, taux, ajmo, st_mo,
                l["sous_traitant_type"] or "", l["sous_traitant_nom"] or "",
                st_montant, ajst, st_st,
                total_ligne, l["note"] or "",
            ])

        total_general = total_materiaux + total_mo + total_st
        ws.append([])
        ws.append(["", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
                   "Coût total matériaux", total_materiaux, ""])
        ws.append(["", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
                   "Coût total main-d'œuvre", total_mo, ""])
        ws.append(["", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
                   "Coût total sous-traitant", total_st, ""])
        ws.append(["", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
                   "TOTAL GÉNÉRAL", total_general, ""])

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
        sous_totaux: Optional[str] = Query(None, description="CSV des regroupements à afficher en sous-total ; param omis = tous, vide explicite = aucun"),
        admin_profits: Optional[str] = Query(None, description="CSV des regroupements pour admin&profit ; param omis = tous, vide explicite = aucun"),
        avec_sous_total_avant_taxes: bool = True,
        avec_tps: bool = True,
        avec_tvq: bool = True,
        orientation: str = "portrait",
        mobilisation: float = 0,
        surface_plancher: float = 0,
        hauteur_cloisons: float = 0,
        longueur_cloisons: float = 0,
    ):
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
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
                   heures, taux_horaire, cout_sous_traitant, sous_traitant_nom,
                   ajust_materiaux, ajust_main_oeuvre, ajust_sous_traitant,
                   sous_traitant_type, sous_traitant_montant,
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

        # Orientation et largeur disponible
        is_landscape = (orientation or "").lower() == "paysage"
        pagesize = landscape(letter) if is_landscape else letter
        margin = 1.5 * cm
        total_w = pagesize[0] - 2 * margin  # ~527 portrait, ~707 landscape

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=pagesize,
            rightMargin=margin, leftMargin=margin,
            topMargin=margin, bottomMargin=margin,
            title=f"Rapport budget — {projet['nom']}",
        )
        ss = getSampleStyleSheet()
        story = []

        title_style = ParagraphStyle(
            "PdfTitle", parent=ss["Title"], fontSize=10, alignment=1,
            spaceAfter=0, textColor=colors.HexColor("#1e3a8a"),
        )
        # Non-breaking spaces ( ) au lieu d'espaces normaux : empêchent
        # le wrap de la Paragraph reportlab si la colonne centrale est étroite.
        title_para = Paragraph("RAPPORT DE BUDGET", title_style)
        logo_flowable = build_pdf_logo(projet.get("logo_base64") or "")
        # Logo plus grand (180pt) → colonnes latérales 190pt pour l'accommoder
        # avec une petite marge ; centre = total_w - 380 (~147pt portrait,
        # 327pt landscape) pour que le titre tienne sur une seule ligne.
        title_side = 190
        title_row = Table(
            [[logo_flowable, title_para, ""]],
            colWidths=[title_side, total_w - 2 * title_side, title_side],
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
        # ENTREPRENEUR : titre sur toute la largeur, puis 2 sous-colonnes
        ent_heading_html = "<b><font size='10' color='#1e3a8a'>ENTREPRENEUR</font></b>"
        ent_left_html = "<br/>".join([
            field_line("Date du jour", date.today().strftime("%Y-%m-%d")),
            field_line("Numéro du projet", projet.get("numero_projet")),
            field_line("Date début travaux", fmt_date(projet.get("date_debut"))),
            field_line("Date fin travaux", fmt_date(projet.get("date_fin"))),
        ])
        ent_right_html = "<br/>".join([
            field_line("Contact entrepreneur", projet.get("contact_entrepreneur")),
            field_line("Courriel", projet.get("email_entrepreneur")),
            field_line("Téléphone", projet.get("telephone_entrepreneur")),
        ])

        client_para = Paragraph(client_html, info_style)
        ent_heading_para = Paragraph(ent_heading_html, info_style)
        ent_left_para = Paragraph(ent_left_html, info_style)
        ent_right_para = Paragraph(ent_right_html, info_style)

        col_w = total_w / 2  # 2 colonnes égales (Client / Entrepreneur)
        ent_subtable = Table(
            [[ent_heading_para, ""], [ent_left_para, ent_right_para]],
            colWidths=[col_w / 2, col_w / 2],
        )
        ent_subtable.setStyle(TableStyle([
            ("SPAN", (0, 0), (1, 0)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 1), (0, 1), 10),  # gap horizontal entre sous-cols
        ]))

        header_table = Table(
            [[client_para, ent_subtable]],
            colWidths=[col_w, col_w],
        )
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 14))


        if projet.get("notes"):
            story.append(Paragraph("<b>Notes du projet :</b>", ss["Normal"]))
            notes_html = (projet["notes"] or "").replace("\n", "<br/>")
            story.append(Paragraph(notes_html, ss["Normal"]))
            story.append(Spacer(1, 12))

        cell_style = ParagraphStyle("Cell", parent=ss["Normal"], fontSize=8, leading=10)

        def cell(text):
            return Paragraph(str(text), cell_style)

        # Colonnes disponibles — refonte 3 sections (matériaux / M-O / S-T).
        # Les colonnes par section sont opt-in (pas dans _default_cols) pour
        # ne pas casser la mise en page PDF existante. ajustement_pct (ancien
        # ajustement global) est conservé pour compat mais n'est plus posé
        # par la nouvelle UI ; les ajust_* par section le remplacent.
        ALL_COLS = [
            "section", "description", "qte", "unite",
            "prix_unitaire", "ajust_materiaux",
            "heures", "taux_horaire", "ajust_main_oeuvre",
            "sous_traitant_type", "sous_traitant_nom",
            "sous_traitant_montant", "ajust_sous_traitant",
            "sous_total", "ajustement_pct", "total", "note",
        ]
        COL_LABELS = {
            "section": "Section", "description": "Description", "qte": "Qté",
            "unite": "Unité",
            "prix_unitaire": "Coût u.", "ajust_materiaux": "Aj. mat.",
            "heures": "Heures", "taux_horaire": "Taux $",
            "ajust_main_oeuvre": "Aj. M-O",
            "sous_traitant_type": "Type S-T", "sous_traitant_nom": "Sous-traitant",
            "sous_traitant_montant": "Mt S-T", "ajust_sous_traitant": "Aj. S-T",
            "sous_total": "S/T", "ajustement_pct": "Adj %",
            "total": "Total", "note": "Note",
        }
        _col_widths_base = {
            "section": 55, "description": 130, "qte": 28, "unite": 30,
            "prix_unitaire": 45, "ajust_materiaux": 30,
            "heures": 30, "taux_horaire": 35, "ajust_main_oeuvre": 30,
            "sous_traitant_type": 45, "sous_traitant_nom": 70,
            "sous_traitant_montant": 45, "ajust_sous_traitant": 30,
            "sous_total": 55, "ajustement_pct": 30,
            "total": 60, "note": 80,
        }
        # Mapping colonne → section. Sert pour la rangée header de section
        # avec colspan ; les colonnes hors section restent vides dans cette
        # rangée.
        COL_SECTION = {
            "prix_unitaire": "materiaux", "ajust_materiaux": "materiaux",
            "heures": "mainOeuvre", "taux_horaire": "mainOeuvre",
            "ajust_main_oeuvre": "mainOeuvre",
            "sous_traitant_type": "sousTraitant",
            "sous_traitant_nom": "sousTraitant",
            "sous_traitant_montant": "sousTraitant",
            "ajust_sous_traitant": "sousTraitant",
        }
        SECTION_LABELS = {
            "materiaux": "MATÉRIAUX",
            "mainOeuvre": "MAIN-D'ŒUVRE",
            "sousTraitant": "SOUS-TRAITANT",
        }
        SECTION_COLORS = {
            "materiaux": colors.HexColor("#E3E7F4"),
            "mainOeuvre": colors.HexColor("#FCE5E5"),
            "sousTraitant": colors.HexColor("#DDF2EC"),
        }
        PRIX_DEPENDENT = {
            "prix_unitaire", "ajust_materiaux",
            "heures", "taux_horaire", "ajust_main_oeuvre",
            "sous_traitant_type", "sous_traitant_nom",
            "sous_traitant_montant", "ajust_sous_traitant",
            "sous_total", "ajustement_pct", "total", "note",
        }

        if colonnes:
            requested = {c.strip() for c in colonnes.split(",") if c.strip()}
        else:
            # Sans param `colonnes`, on prend toutes les colonnes — le frontend
            # omet le param quand l'user a tout coché (allColsSelected), donc
            # "pas de param" = "tout cocher". L'ancien comportement (vue
            # compacte 9 cols) cachait les 8 colonnes des sections refondues.
            requested = set(ALL_COLS)
        if not avec_prix:
            requested -= PRIX_DEPENDENT
        selected = [c for c in ALL_COLS if c in requested] or ["section", "description"]
        headers = [COL_LABELS[c] for c in selected]
        # Scale calculé sur la sélection effective : tient compte des opt-in.
        _scale = total_w / sum(_col_widths_base[c] for c in selected)
        col_widths = [_col_widths_base[c] * _scale for c in selected]

        # Police adaptive : 8pt par défaut, 7pt si > 12 colonnes pour rester
        # lisible sans débordement en paysage A4.
        body_fontsize = 7 if len(selected) > 12 else 8

        # Construction de la rangée "section header" (colspans). On group les
        # colonnes consécutives qui partagent un même groupe de section ;
        # une section qui n'a aucune colonne visible disparaît automatiquement.
        section_header_row = None
        section_spans = []  # [(start_col, end_col, section_key)]
        any_section_visible = any(COL_SECTION.get(c) for c in selected)
        if any_section_visible:
            section_header_row = [""] * len(selected)
            i = 0
            while i < len(selected):
                grp = COL_SECTION.get(selected[i])
                if grp is None:
                    i += 1
                    continue
                j = i
                while j < len(selected) and COL_SECTION.get(selected[j]) == grp:
                    j += 1
                section_header_row[i] = SECTION_LABELS[grp]
                section_spans.append((i, j - 1, grp))
                i = j

        def cell_for(col, l, qte, prix, adj, st, tot,
                     heures, taux, st_mo, st_montant,
                     ajm, ajmo, ajst):
            if col == "section": return l["section"] or ""
            if col == "description": return cell(l["description"] or "")
            if col == "qte": return f"{qte:g}"
            if col == "unite": return l["unite"] or ""
            if col == "prix_unitaire": return f"{prix:,.2f}"
            if col == "ajust_materiaux": return f"{ajm:g}"
            if col == "heures": return f"{heures:g}"
            if col == "taux_horaire": return f"{taux:,.2f}"
            if col == "ajust_main_oeuvre": return f"{ajmo:g}"
            if col == "sous_traitant_type": return l["sous_traitant_type"] or ""
            if col == "sous_traitant_nom": return cell(l["sous_traitant_nom"] or "")
            if col == "sous_traitant_montant": return f"{st_montant:,.2f}"
            if col == "ajust_sous_traitant": return f"{ajst:g}"
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

        # Préfixage du tableau : rangée 0 = headers de section (colspan) si
        # au moins une section est visible, sinon on commence direct avec les
        # libellés. header_offset = nombre de rangées d'en-tête à sauter pour
        # localiser le début du body (utilisé par les styles indexés ci-dessous).
        if section_header_row is not None:
            table_data = [section_header_row, headers]
            header_offset = 2
        else:
            table_data = [headers]
            header_offset = 1
        subtotal_rows = []
        group_subtotals = {key: 0.0 for key, _, _, _ in BUDGET_GROUPS_PDF}
        non_grouped_total = 0.0

        for sec, sec_lignes in sections_groups.items():
            sec_total = 0.0
            section_has_visible_lines = False
            for l in sec_lignes:
                qte = _effective_qte(l, mobilisation, surface_plancher,
                                     surface_mur_calc, surface_gypse_calc)
                # Avec la refonte 3 sections, une ligne peut être active sans
                # qte > 0 (ex. ligne pure sous-traitant). On garde le filtre
                # qte > 0 historique mais on l'élargit aux lignes M-O / S-T.
                heures = float(l["heures"] or 0)
                taux = float(l["taux_horaire"] or 0)
                ajmo = float(l["ajust_main_oeuvre"] or 0)
                st_montant = float(l["sous_traitant_montant"] or 0)
                ajst = float(l["ajust_sous_traitant"] or 0)
                has_mo = heures > 0 and taux > 0
                has_st = st_montant > 0
                if qte <= 0 and not has_mo and not has_st:
                    continue
                prix = float(l["prix_unitaire"] or 0)
                ajm = float(l["ajust_materiaux"] or 0)
                adj = float(l["ajustement_pct"] or 0)
                st_mat = qte * prix * (1 + ajm / 100)
                st_mo = heures * taux * (1 + ajmo / 100)
                st_st = st_montant * (1 + ajst / 100)
                # Sous-total ligne = somme des 3 sous-totaux par section.
                st = st_mat + st_mo + st_st
                # tot_real = total brut. ajustement_pct historique reste appliqué
                # pour rétro-compat des projets antérieurs à la refonte ; sera
                # à 0 sur les nouvelles saisies.
                tot_real = st * (1 + adj / 100)
                gkey = _group_key_for(_section_prefix_n(l["section"]))
                factor = group_factors.get(gkey, 1) if gkey is not None else 1
                tot = tot_real * factor  # gonflé si admin distribué
                sec_total += tot
                if gkey is not None:
                    group_subtotals[gkey] += tot_real
                else:
                    non_grouped_total += tot_real
                table_data.append([
                    cell_for(c, l, qte, prix, adj, st, tot,
                             heures, taux, st_mo, st_montant,
                             ajm, ajmo, ajst)
                    for c in selected
                ])
                section_has_visible_lines = True
            if show_totals_row and section_has_visible_lines:
                table_data.append(make_summary_row(f"Sous-total {sec}", sec_total))
                subtotal_rows.append(len(table_data) - 1)

        # Indice de la rangée des libellés de colonnes (sous l'éventuelle
        # rangée de header de section).
        col_header_row = header_offset - 1
        body_start_row = header_offset

        table_style_cmds = [
            # Fond bleu Adision sur la rangée des libellés de colonnes.
            ("BACKGROUND", (0, col_header_row), (-1, col_header_row), colors.HexColor("#1e3a8a")),
            ("TEXTCOLOR", (0, col_header_row), (-1, col_header_row), colors.white),
            ("FONTNAME", (0, col_header_row), (-1, col_header_row), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), body_fontsize),
            ("ALIGN", (2, body_start_row), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, body_start_row), (-1, -1),
             [colors.white, colors.HexColor("#f1f5f9")]),
        ]
        # Header de section (rangée 0) : SPAN + fond couleur palette Adision
        # pâle pour chaque section visible.
        if section_header_row is not None:
            table_style_cmds.append(("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"))
            table_style_cmds.append(("ALIGN", (0, 0), (-1, 0), "CENTER"))
            for start, end, grp in section_spans:
                table_style_cmds.append(("SPAN", (start, 0), (end, 0)))
                table_style_cmds.append(("BACKGROUND", (start, 0), (end, 0),
                                         SECTION_COLORS[grp]))
        for r in subtotal_rows:
            table_style_cmds.append(("FONTNAME", (0, r), (-1, r), "Helvetica-Bold"))
            table_style_cmds.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#dbeafe")))

        table = Table(table_data, colWidths=col_widths, repeatRows=header_offset)
        table.setStyle(TableStyle(table_style_cmds))
        story.append(table)

        # ── Totals block (per-regroupement subtotals + admin&profit + taxes + grand total) ──
        if avec_prix:
            totals_rows = []
            totals_kinds = []

            # 1. Sous-total avant taxes RÉEL : toutes les valeurs réelles, indépendant
            #    des cases selected_st / selected_ap (qui n'agissent que sur l'affichage).
            real_sub_avant_taxes = non_grouped_total
            for key, _, pct_field, _ in BUDGET_GROUPS_PDF:
                sub = group_subtotals[key]
                if sub <= 0:
                    continue
                pct_d = float(projet.get(pct_field) or 0)
                real_sub_avant_taxes += sub + sub * pct_d / 100

            # 2. Affichage des lignes par regroupement (filtré par selected_st / selected_ap)
            for key, label, pct_field, _ in BUDGET_GROUPS_PDF:
                sub = group_subtotals[key]
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
                    totals_rows.append([f"Administration et profit {pct:g}%", f"{ap:,.2f} $"])
                    totals_kinds.append("admin")
                else:
                    # Mode distribution : sous-total gonflé (= sub + ap), pas de ligne admin
                    totals_rows.append([f"Sous-total {label}", f"{(sub + ap):,.2f} $"])
                    totals_kinds.append("subtotal")

            # 3. Sous-total avant taxes (visuel uniquement)
            if avec_sous_total_avant_taxes:
                totals_rows.append(["Sous-total avant taxes", f"{real_sub_avant_taxes:,.2f} $"])
                totals_kinds.append("subtotal_taxes")

            # 4. TPS / TVQ — affectent réellement le TOTAL GÉNÉRAL
            tps_amount = real_sub_avant_taxes * TPS_RATE
            tvq_amount = real_sub_avant_taxes * TVQ_RATE
            if avec_tps:
                totals_rows.append([f"TPS {TPS_RATE * 100:g}%", f"{tps_amount:,.2f} $"])
                totals_kinds.append("tax")
            if avec_tvq:
                totals_rows.append([f"TVQ {TVQ_RATE * 100:g}%", f"{tvq_amount:,.2f} $"])
                totals_kinds.append("tax")

            # 5. TOTAL GÉNÉRAL = sous-total avant taxes + TPS (si coché) + TVQ (si coché)
            total_general = real_sub_avant_taxes
            if avec_tps:
                total_general += tps_amount
            if avec_tvq:
                total_general += tvq_amount
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
                elif kind == "subtotal_taxes":
                    totals_style.append(("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"))
                    totals_style.append(("LINEABOVE", (0, i), (-1, i), 1, colors.HexColor("#1e3a8a")))
                    totals_style.append(("TOPPADDING", (0, i), (-1, i), 8))
                    totals_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#eef4ff")))
                elif kind == "tax":
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

            totals_table = Table(totals_rows, colWidths=[total_w * (400 / 526), total_w * (126 / 526)])
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
    # SNAPSHOTS (Sprint B - app_ana.project_snapshots)
    # ══════════════════════════════════════════════════════════
    # Colonnes retournées par défaut par les endpoints snapshots ; on omet
    # budget_lines_jsonb qui est lourd. Opt-in via ?include_lines=true.
    _SNAPSHOT_DEFAULT_COLS = (
        "id, projet_id, nom_projet, client_nom, statut, type_batiment, "
        "region, date_adjudication, superficie_m2, aggregates_jsonb, "
        "created_at, trigger_event, schema_version, is_latest"
    )

    @router.get("/projets/{projet_id}/snapshots")
    def list_projet_snapshots(projet_id: int, include_lines: bool = False):
        """Liste tous les snapshots d'un projet, du plus récent au plus ancien.

        budget_lines_jsonb est OMIS par défaut (champ lourd). Opt-in via
        ?include_lines=true (utile pour Ad ANA recalcul).
        """
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        columns = _SNAPSHOT_DEFAULT_COLS
        if include_lines:
            columns += ", budget_lines_jsonb"
        cur.execute(
            f"SELECT {columns} FROM app_ana.project_snapshots "
            "WHERE projet_id = %s ORDER BY created_at DESC",
            (projet_id,),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    @router.get("/projets/{projet_id}/snapshots/latest")
    def get_latest_snapshot(projet_id: int, include_lines: bool = False):
        """Retourne le snapshot le plus récent (is_latest = TRUE) du projet.
        404 si aucun snapshot n'existe.
        """
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        columns = _SNAPSHOT_DEFAULT_COLS
        if include_lines:
            columns += ", budget_lines_jsonb"
        cur.execute(
            f"SELECT {columns} FROM app_ana.project_snapshots "
            "WHERE projet_id = %s AND is_latest = TRUE LIMIT 1",
            (projet_id,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            raise HTTPException(
                status_code=404, detail="Aucun snapshot pour ce projet"
            )
        return row

    # ══════════════════════════════════════════════════════════
    # BUDGET LIGNES (items du budget d'un projet)
    # ══════════════════════════════════════════════════════════

    @router.get("/projets/{projet_id}/lignes")
    def get_budget_lignes(projet_id: int):
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
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
        cur = conn.cursor(row_factory=dict_row)

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
        cur = conn.cursor(row_factory=dict_row)

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

    # Valeurs autorisées pour sous_traitant_type. None / "" = vide.
    SOUS_TRAITANT_TYPES = {"Budget", "Soumission", "BSDQ", "Allocation"}

    @router.put("/projets/{projet_id}/lignes/{ligne_id}")
    def update_budget_ligne(projet_id: int, ligne_id: int, data: dict):
        # Validation du type de sous-traitant. On accepte vide / null ou un
        # des types whitelist — refus 400 sur valeur inconnue pour éviter
        # qu'une typo silencieuse pollue la BD.
        if "sous_traitant_type" in data:
            v = data["sous_traitant_type"]
            if v not in (None, "") and v not in SOUS_TRAITANT_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"sous_traitant_type doit être un de {sorted(SOUS_TRAITANT_TYPES)} ou vide",
                )
            if v == "":
                data["sous_traitant_type"] = None
        conn = get_conn()
        cur = conn.cursor()
        fields = []
        values = []
        for field in [
            "section", "description", "unite", "prix_unitaire", "qte",
            "ajustement_pct", "note", "actif",
            # Ventilation tri-axiale matériel / main-d'œuvre / sous-traitant.
            # prix_unitaire conserve son nom mais représente le coût matériel.
            "heures", "taux_horaire", "cout_sous_traitant", "sous_traitant_nom",
            # Refonte 3 sections : ajust % par section + type et montant S-T.
            "ajust_materiaux", "ajust_main_oeuvre", "ajust_sous_traitant",
            "sous_traitant_type", "sous_traitant_montant",
        ]:
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

    @router.patch("/projets/{projet_id}/lignes/{ligne_id}")
    def patch_budget_ligne(projet_id: int, ligne_id: int, data: dict):
        # Alias PATCH du PUT — même comportement, même whitelist de champs.
        return update_budget_ligne(projet_id, ligne_id, data)

    @router.get("/sous-traitants/suggestions")
    def get_sous_traitants_suggestions(user=Depends(jwt_user)):
        """Liste DISTINCT des noms de sous-traitants déjà saisis par ce user
        (toutes lignes confondues), pour autocomplétion dans l'UI."""
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT bl.sous_traitant_nom
            FROM ad_budget.budget_lignes bl
            JOIN ad_budget.projets p ON p.id = bl.projet_id
            WHERE p.user_id = %s
              AND bl.sous_traitant_nom IS NOT NULL
              AND bl.sous_traitant_nom <> ''
            ORDER BY bl.sous_traitant_nom
            """,
            (user["id"],),
        )
        rows = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows

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
        cur = conn.cursor(row_factory=dict_row)
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