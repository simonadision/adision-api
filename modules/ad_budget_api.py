import base64
import io
import json
import os
import re
import unicodedata
from collections import OrderedDict
from datetime import date, datetime, timezone
from typing import Optional

import httpx
import openpyxl
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Json

from modules import con_service, hub_service, mat_service, typ_service
from modules.ad_budget_constants import AD_VIU_BLINDSPOT_DIVISIONS
from modules.aggregates import adapt_budget_lines, compute_aggregates, _js_round
from modules.auth_jwt import _extract_bearer, make_jwt_deps
from modules.taux_horaires_api import (
    _load_taux_default_map,
    _resolve_taux_default,
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
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


def _deaccent_lower(s):
    """Désaccentue + minuscule (NFKD, drop combining) — pour matcher
    'Contremaître'/'Contremaitre' insensiblement aux accents et à la casse."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)
    ).lower()


def _is_contremaitre(desc):
    return "contremaitre" in _deaccent_lower(desc)


def _group_admin_profit(projet, pct_field, sub, sub_mat, sub_mo, sub_st):
    """Admin & profit d'un regroupement. Hiérarchie : %catégorie (pct_field_<cat>)
    -> %discipline (pct_field) -> 0. GARANTIE RÉTROCOMPAT : si AUCUN % catégorie
    n'est défini (les 3 NULL), on retombe EXACTEMENT sur l'existant
    (sub × %discipline). Sinon : Σ sous-total_cat × %cat_applicable."""
    disc = float(projet.get(pct_field) or 0)
    raws = {c: projet.get(f"{pct_field}_{c}") for c in ("mat", "mo", "st")}
    if all(raws[c] is None for c in raws):
        return sub * disc / 100.0
    def eff(c):
        r = raws[c]
        return float(r) if r is not None else disc
    return (sub_mat * eff("mat") + sub_mo * eff("mo") + sub_st * eff("st")) / 100.0

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
# Sprint 3.9 — Ad HUB stocke la région en CODE snake_case ('capitale_nationale'),
# Ad BUD valide des LIBELLÉS FR ('Capitale-Nationale'). Un projet hérité d'Ad HUB
# (Direction A) envoyait donc une région « invalide ». Filet de sécurité : on
# normalise le code HUB → libellé avant validation (miroir @adision/ui
# REGION_LABELS). Le front mappe déjà ; ceci protège tout autre appelant.
HUB_REGION_CODE_TO_LABEL = {
    "bas_saint_laurent": "Bas-Saint-Laurent",
    "saguenay_lac_saint_jean": "Saguenay–Lac-Saint-Jean",
    "capitale_nationale": "Capitale-Nationale",
    "mauricie": "Mauricie",
    "estrie": "Estrie",
    "montreal": "Montréal",
    "outaouais": "Outaouais",
    "abitibi_temiscamingue": "Abitibi-Témiscamingue",
    "cote_nord": "Côte-Nord",
    "nord_du_quebec": "Nord-du-Québec",
    "gaspesie_iles_de_la_madeleine": "Gaspésie–Îles-de-la-Madeleine",
    "chaudiere_appalaches": "Chaudière-Appalaches",
    "laval": "Laval",
    "lanaudiere": "Lanaudière",
    "laurentides": "Laurentides",
    "monteregie": "Montérégie",
    "centre_du_quebec": "Centre-du-Québec",
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
        # Normalise un code région HUB (snake_case) en libellé FR avant validation.
        if v in HUB_REGION_CODE_TO_LABEL:
            v = HUB_REGION_CODE_TO_LABEL[v]
            data["region"] = v
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
    """Qté de la ligne = qté SAISIE (stockée en BD).

    L'auto-remplissage depuis les paramètres globaux (mobilisation/surfaces) a été
    RETIRÉ (chantier « tout manuel ») : le budget — et donc les totaux PDF /
    snapshots / export Ad CON — ne se calcule QUE depuis les lignes. Les paramètres
    restent en arguments (compat des appelants) mais ne pilotent plus la qté ;
    ils demeurent une donnée projet (usage futur Ad ANA)."""
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
    # taux_horaire : auto-fill du taux par défaut (D5), résolu directement en
    # SQL via la division CSI = LEFT(section, 2), JOIN sur le taux actif du
    # métier mappé. COALESCE -> 0 si division non mappée ou taux inactif.
    # Même logique de résolution que _resolve_taux_default (taux_horaires_api)
    # — garder synchronisé. ad_budget_prix_moyens est aliasé `pm` : le JOIN
    # sur taux_horaires (qui a aussi une colonne `id`) rendrait `id` ambigu.
    sql = (
        "INSERT INTO ad_budget.budget_lignes "
        "(projet_id, source_item_id, section, description, unite, "
        " prix_unitaire, qte, ajustement_pct, note, actif, taux_horaire) "
        "SELECT %s, pm.id, pm.section, pm.description, "
        "       COALESCE(pm.unite, 'global'), COALESCE(pm.prix_unitaire, 0), "
        "       0, 0, COALESCE(pm.note, ''), TRUE, COALESCE(t.taux_col17, 0) "
        "FROM ad_budget.ad_budget_prix_moyens pm "
        "LEFT JOIN ad_budget.csi_division_default_metier dm "
        "       ON dm.csi_division = LEFT(pm.section, 2) "
        "LEFT JOIN ad_budget.taux_horaires t "
        "       ON t.id = dm.taux_id AND t.actif = TRUE"
    )
    params: list = [projet_id]
    if skip_section_01:
        # Le pattern LIKE est parametrise (au lieu de '01 %' inline) pour
        # eviter que psycopg3 interprete le % comme placeholder mal forme.
        sql += " WHERE pm.section NOT LIKE %s"
        params.append("01 %")
    elif default_divisions:
        # Filtre sur les 2 premiers chars de section (= code division CSI).
        # ANY(%s::text[]) accepte un array Python, idiomatique psycopg3.
        sql += " WHERE LEFT(pm.section, 2) = ANY(%s)"
        params.append(list(default_divisions))
    cur.execute(sql, params)
    return cur.rowcount


def build_pdf_logo(logo_base64: str, max_width: float = 180):
    """Return a reportlab Image flowable for the given logo (base64), or "" if
    none. Décision Simon : PAS de fallback Adision codé en dur — une org/projet
    sans logo rend un PLACEHOLDER NEUTRE (aucun logo), jamais la marque Adision.
    Le défaut white-label (logo de l'org active) est résolu en amont par les
    callers via fetch_org_logo_base64(). DEFAULT_LOGO_PATH conservé (legacy) mais
    n'est plus un fallback automatique.
    """
    source = None
    try:
        if logo_base64:
            data = logo_base64
            if "," in data:
                data = data.split(",", 1)[1]
            source = io.BytesIO(base64.b64decode(data))
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

def draw_adflo_footer(canvas, doc, nb=False):
    """Footer PDF partagé : « Propulsé par » + logo Ad FLO (wordmark tricolore
    « A d FLO » + croix), reproduit 1:1 du SVG officiel @adision/ui AdLogo
    suffix='FLO'. SOURCE UNIQUE utilisée en onFirstPage/onLaterPages par le
    devis (_build_devis) ET le rapport de calcul (_build_projet_report) — même
    rendu, même logo, même texte. nb=True -> version N&B (lettres encre, croix
    grise). Centré horizontalement, ~12 mm du bord inférieur.

    Coords SVG (viewBox 300x140, y vers le BAS) -> canvas reportlab (y vers le
    HAUT) via flip base_y + k*(SVG_H - y)."""
    C_RED, C_GREEN, C_BLUE = (
        colors.HexColor("#ef4444"),
        colors.HexColor("#10b981"),
        colors.HexColor("#1e3a8a"),
    )
    C_INK, C_GREY = colors.HexColor("#111827"), colors.HexColor("#6b7280")

    canvas.saveState()
    cx = doc.pagesize[0] / 2.0
    y0 = 12 * mm                       # baseline « Propulsé par » + « A »/« d »
    k = 0.12                           # échelle SVG -> points
    SVG_H = 140.0
    base_y = y0 - k * (SVG_H - 112.0)  # repère canvas pour SVG y=140

    def _ty(svg_y):                    # baseline texte SVG -> canvas
        return base_y + k * (SVG_H - svg_y)

    # Largeurs pour centrer le groupe « Propulsé par » + logo.
    prefix = "Propulsé par "
    w_prefix = canvas.stringWidth(prefix, "Helvetica", 7)
    fs = 76.0 * k
    logo_w = k * 98.0 + canvas.stringWidth("FLO", "Helvetica-Bold", fs)
    start_x = cx - (w_prefix + logo_w) / 2.0
    lx = start_x + w_prefix            # origine x du logo

    def _rect(svg_x, svg_y, w, h, col):  # rect SVG (top-left) -> canvas
        canvas.setFillColor(col)
        canvas.rect(lx + k * svg_x, base_y + k * (SVG_H - svg_y - h),
                    k * w, k * h, fill=1, stroke=0)

    # « Propulsé par » (gris), baseline alignée sur « A »/« d ».
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(C_GREY if nb else colors.HexColor("#94a3b8"))
    canvas.drawString(start_x, y0, prefix)

    col_a = col_d = col_flo = C_INK
    col_x1 = col_x2 = col_x3 = C_GREY
    if not nb:
        col_a, col_d, col_flo = C_RED, C_GREEN, C_BLUE
        col_x1, col_x2, col_x3 = C_BLUE, C_RED, C_GREEN

    # Croix tricolore (translate(28,0) absorbé dans les x absolus).
    _rect(55, 0,  6,  27, col_x1)      # barre haute (bleu)
    _rect(61, 27, 27, 6,  col_x1)      # barre droite (bleu)
    _rect(55, 33, 6,  27, col_x2)      # barre basse (rouge)
    _rect(28, 27, 27, 6,  col_x3)      # barre gauche (vert)

    # Wordmark « A d FLO ».
    canvas.setFont("Helvetica-Bold", fs)
    canvas.setFillColor(col_a);   canvas.drawString(lx + k * 0.0,  _ty(112), "A")
    canvas.setFillColor(col_d);   canvas.drawString(lx + k * 52.0, _ty(112), "d")
    canvas.setFillColor(col_flo); canvas.drawString(lx + k * 98.0, _ty(130), "FLO")

    canvas.restoreState()


def fetch_org_logo_base64(jwt_token, organization_id):
    """Logo de l'org (HUB Info entreprise / R2 proxy stable) en base64, ou "" si
    l'org n'a pas de logo. Sert de DÉFAUT white-label : le PDF porte le logo de
    l'org du projet / active. Échec gracieux (network/401/timeout) -> "" (le
    caller tombe alors sur le placeholder neutre — jamais Adision)."""
    try:
        org = hub_service.fetch_organization(jwt_token, organization_id) or {}
        url = org.get("logo_url")
        if not url:
            return ""
        r = httpx.get(url, timeout=10.0)
        if r.status_code == 200 and r.content:
            return base64.b64encode(r.content).decode("ascii")
    except Exception as e:  # noqa: BLE001
        print(f"[ad-bud PDF] logo org échec (organization_id={organization_id}): {e}", flush=True)
    return ""


def _authorize_projet(projet, user, mode):
    """Contrôle d'accès centralisé à un projet Ad BUD (PHASE 3A multi-tenant).

    projet : ligne ad_budget.projets déjà chargée — doit contenir `user_id` et
             `organization_id` — ou None si le projet est introuvable.
    user   : objet retourné par Depends(jwt_user) — id, platform_role,
             org_role, organization_id.
    mode   : 'read' ou 'write'.

    LECTURE  : propriétaire OU super_admin OU superviseur.
    ÉCRITURE : propriétaire OU super_admin (jamais superviseur — un
               org_role='admin' supervise en lecture seule).
    Lève 404 si le projet est inexistant OU hors du périmètre visible (on ne
    révèle pas l'existence d'un projet d'une autre organisation). Lève 403 si
    le projet est visible mais l'écriture refusée (superviseur en mode 'write').
    """
    if projet is None:
        raise HTTPException(status_code=404, detail="Projet introuvable")

    is_owner = projet["user_id"] == user["id"]
    is_super_admin = user.get("platform_role") == "super_admin"

    # Superviseur : org_role='admin' ET les DEUX organization_id non-NULL et
    # égaux. La double non-nullité est CRITIQUE — sans elle, NULL == NULL
    # ferait tout matcher (aujourd'hui presque tous les organization_id sont
    # NULL).
    # Comparaison via str() : projet_org est un uuid.UUID (psycopg3 adapte la
    # colonne UUID), user_org est une str (le JWT sérialise l'UUID en chaîne) —
    # un `uuid.UUID == str` renvoie toujours False. On normalise les deux côtés.
    user_org = user.get("organization_id")
    projet_org = projet.get("organization_id")
    is_supervisor = (
        user.get("org_role") == "admin"
        and user_org is not None
        and projet_org is not None
        and str(projet_org) == str(user_org)
    )

    if is_owner or is_super_admin:
        return  # accès total (lecture + écriture)
    if mode == "read" and is_supervisor:
        return  # superviseur : lecture seule
    if is_supervisor:
        # mode 'write' : projet VISIBLE mais non modifiable -> 403.
        raise HTTPException(
            status_code=403,
            detail="Lecture seule : vous ne pouvez pas modifier le projet "
                   "d'un autre utilisateur de votre organisation",
        )
    # Ni propriétaire, ni super_admin, ni superviseur du périmètre : projet
    # hors périmètre -> 404 (ne pas révéler son existence).
    raise HTTPException(status_code=404, detail="Projet introuvable")


def _load_and_authorize_projet(get_conn, projet_id, user, mode, check_lock=True):
    """Charge le projet {projet_id} sur une connexion DÉDIÉE (fermée aussitôt
    via try/finally — aucune fuite de connexion, même sur un rejet 403/404)
    puis applique _authorize_projet. À appeler en PREMIÈRE ligne de chaque
    endpoint projet ; pour les endpoints budget_lignes, avec le projet_id du
    PROJET PARENT de la route."""
    conn = get_conn()
    try:
        cur = conn.cursor(row_factory=dict_row)
        cur.execute(
            "SELECT user_id, organization_id, is_verrouille FROM ad_budget.projets "
            "WHERE id = %s",
            (projet_id,),
        )
        projet = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    _authorize_projet(projet, user, mode)
    # VERROU (lecture seule, ORTHOGONAL au statut de cycle de vie) : tout WRITE est
    # refusé 409 si le projet est verrouillé. C'est LE garde-fou central — tous les
    # endpoints d'écriture de contenu projet passent par ici en mode='write', donc
    # aucun n'est oublié. `check_lock=False` pour les exceptions LÉGITIMES : le toggle
    # du verrou lui-même (sinon on ne pourrait jamais déverrouiller) et la duplication
    # (crée un NOUVEAU projet, ne modifie pas le verrouillé). Les LECTURES (mode='read'
    # : GET projet/lignes, rapport, export) ne sont JAMAIS bloquées.
    if check_lock and mode == "write" and projet and projet.get("is_verrouille"):
        raise HTTPException(
            status_code=409,
            detail="Projet verrouillé — déverrouillez-le pour le modifier.",
        )
    # Retourne le projet autorisé (user_id, organization_id, is_verrouille) — les
    # chemins qui résolvent un catalogue cross-service DOIVENT utiliser
    # projet.organization_id (org du PROJET), pas l'org du JWT (Loi 25).
    return projet


def _map_typ_to_budget_cols(typ: dict, qte) -> dict:
    """Aplatissement d'une ligne Ad TYP (catalogue cross-service) → colonnes
    budget_lignes (3 sections). PRÉSERVE les montants Ad TYP exacts :
      - MAT : prix_unitaire ← prix_mat (roulé Ad TYP, PAR UNITÉ) ; getRow le
              multiplie par qte côté front (scaling live).
      - ST  : sous_traitant_montant ← prix_st × qte (TOTAL à la qte du snapshot,
              cohérent avec MO ci-dessous). prix_st seul (par unité) sous-estimait
              le S/T ST d'un facteur qte (bug : ne scalait pas avec la qte).
      - MO  : Ad BUD = heures × taux (PAS × qte). On pose
              heures = Σ(composant.mo_rendement × composant.qte) × qte_budget,
              taux  = prix_mo / Σ(rendement×qte)   (taux PONDÉRÉ → heures×taux = prix_mo×qte).
              Sans rendement mais prix_mo>0 : heures=qte, taux=prix_mo (pseudo-taux
              = montant unitaire) → MO = prix_mo × qte. `mo_flat=True` le signale.
    Renvoie aussi `mo_flat` et `taux_pondere` (debug/observabilité)."""
    qte = float(qte or 0)
    prix_mat = float(typ.get("prix_mat") or 0)
    prix_mo = float(typ.get("prix_mo") or 0)
    prix_st = float(typ.get("prix_st") or 0)
    # heures_unit = Σ(rendement×qté) des composants. La résolution DÉTAIL
    # (get_ligne) fournit `composants` → on somme. La résolution EN LOT (batch,
    # onglet MAJ) fournit `heures_unit` PRÉ-CALCULÉ (même formule SQL que la liste)
    # et PAS de composants → on l'utilise tel quel. Les deux donnent le même MO.
    hu = typ.get("heures_unit")
    if hu is None:
        heures_unit = 0.0
        for c in (typ.get("composants") or []):
            rend = c.get("mo_rendement")
            if rend not in (None, ""):
                heures_unit += float(rend) * float(c.get("qte") or 1)
    else:
        heures_unit = float(hu or 0)
    mo_flat = False
    if heures_unit > 0:
        heures = round(heures_unit * qte, 4)
        taux = round(prix_mo / heures_unit, 4)
    elif prix_mo > 0:
        # Pas de rendement (MO brute Ad TYP) : pseudo-taux = montant unitaire.
        heures = round(qte, 4)
        taux = round(prix_mo, 4)
        mo_flat = True
    else:
        heures = 0.0
        taux = 0.0
    # Cellule code du budget = CODE CSI COMPLET de la ligne Ad TYP (ex.
    # « 06 40 00.01 »), pas la section (06 40 00) ni la division (06 00 00).
    # Le regroupement budget (getPrefix) dérive division/sous-section des 4
    # premiers chiffres → le suffixe « .01 » ne casse pas le rangement.
    section = (typ.get("code") or typ.get("csi_section_code")
               or typ.get("csi_division_code") or "")
    return {
        "section": section,
        "description": typ.get("description") or "",
        "unite": typ.get("unite") or "global",
        "prix_unitaire": prix_mat,
        "heures": heures,
        "taux_horaire": taux,
        "sous_traitant_montant": round(prix_st * qte, 4),
        "mo_flat": mo_flat,
        "taux_pondere": taux,
    }


# ─── Onglet « MAJ » Ad BUD : détection de divergence ligne ↔ source Ad TYP ───
# Helpers PURS (testables) partagés par les endpoints detect_maj_typ / apply_maj_typ.
_MAJ_LINE_COLS = ("id, source_typ_code, qte, section, description, unite, "
                  "prix_unitaire, sous_traitant_montant, heures, taux_horaire, "
                  "prix_unitaire_override, heures_manuelles")


def _maj_money_diff(a, b) -> bool:
    return abs(float(a or 0) - float(b or 0)) > 0.005


def _maj_str_diff(a, b) -> bool:
    return str(a or "").strip() != str(b or "").strip()


def _maj_is_override_heures(ligne) -> bool:
    # Override MO RÉEL = heures_manuelles ET heures ≠ 0 (cf. fix faux-override à 0).
    return bool(ligne.get("heures_manuelles")) and float(ligne.get("heures") or 0) != 0


def _maj_compute_divergences(ligne, m):
    """Écarts SIGNALABLES d'une ligne budget vs sa source Ad TYP (déjà mappée par
    _map_typ_to_budget_cols). V1 = signaux NON ambigus : description, unité,
    coût unitaire (MAT, override-aware) et sous-traitant (ST). Respecte l'override
    volontaire prix_unitaire_override (coût non signalé). Ne présume pas la cause
    d'un écart description/unité — on l'expose tel quel, l'user décide.

    ⚠️ MO / taux EXCLU de V1 (comme MAT) : le taux MO d'une ligne a une DOUBLE
    source — le catalogue Ad TYP (assemblages avec rendement) OU le défaut projet
    appliqué (col 17 ACQ « Appliquer les taux par défaut »). Comparer le taux
    génèrerait de faux écarts DESTRUCTEURS (ligne au défaut 84,64 vs catalogue 0
    → « accepter » remettrait le MO à 0) et contredirait la règle de PRÉSERVATION
    du taux (resync auto ne réaligne jamais le taux ; seul le ⟳ explicite le fait).
    À rebrancher en phase 2 avec un drapeau de provenance du taux."""
    divs = []
    if _maj_str_diff(ligne["description"], m["description"]):
        divs.append({"champ": "description", "label": "Description",
                     "valeur_ligne": ligne["description"], "valeur_base": m["description"]})
    if _maj_str_diff(ligne["unite"], m["unite"]):
        divs.append({"champ": "unite", "label": "Unité",
                     "valeur_ligne": ligne["unite"], "valeur_base": m["unite"]})
    if not ligne.get("prix_unitaire_override") and _maj_money_diff(ligne["prix_unitaire"], m["prix_unitaire"]):
        divs.append({"champ": "prix_unitaire", "label": "Coût unitaire",
                     "valeur_ligne": round(float(ligne["prix_unitaire"] or 0), 2),
                     "valeur_base": round(float(m["prix_unitaire"] or 0), 2)})
    if _maj_money_diff(ligne["sous_traitant_montant"], m["sous_traitant_montant"]):
        divs.append({"champ": "sous_traitant_montant", "label": "Sous-traitant",
                     "valeur_ligne": round(float(ligne["sous_traitant_montant"] or 0), 2),
                     "valeur_base": round(float(m["sous_traitant_montant"] or 0), 2)})
    return divs


# ─── Onglet « MAJ » phase 2 — détection de divergence ligne ↔ item Ad MAT ───
# Colonnes lues pour la détection MAT (item lié + scope + snapshot-prix dédié).
_MAJ_MAT_LINE_COLS = ("id, section, qte, item_id_ad_mat, item_ad_mat_scope, "
                      "description, unite, prix_unitaire, source_mat_prix_snapshot")


def _maj_compute_mat_divergences(ligne, src):
    """Écarts SIGNALABLES d'une ligne MAT vs son item Ad MAT COURANT (`src` = sortie
    du batch : {description, unite, prix_courant}).

    PRIX : comparé au SNAPSHOT DÉDIÉ `source_mat_prix_snapshot` (PAS au prix affiché —
    le flag prix_unitaire_override est inutilisable pour MAT, cf. recon). On ne compare
    que si snapshot ET prix_courant existent tous les deux → exclut NULL↔NULL (et toute
    moitié-NULL : item master sans prix_items, ligne legacy sans snapshot). Toute
    variation non nulle est signalée (pas de seuil en V1).
    DESC/UNITÉ : ligne courante vs MAT courant (cohérent avec TYP)."""
    divs = []
    snap = ligne.get("source_mat_prix_snapshot")
    cur = src.get("prix_courant") if src else None
    if snap is not None and cur is not None and _maj_money_diff(snap, cur):
        divs.append({"champ": "prix_unitaire", "label": "Coût unitaire",
                     "valeur_ligne": round(float(snap), 2),
                     "valeur_base": round(float(cur), 2)})
    if src is not None and _maj_str_diff(ligne.get("description"), src.get("description")):
        divs.append({"champ": "description", "label": "Description",
                     "valeur_ligne": ligne.get("description"), "valeur_base": src.get("description")})
    if src is not None and _maj_str_diff(ligne.get("unite"), src.get("unite")):
        divs.append({"champ": "unite", "label": "Unité",
                     "valeur_ligne": ligne.get("unite"), "valeur_base": src.get("unite")})
    return divs


# Unité « heures de MO » : la quantité représente des heures → heures = qté par défaut.
# Couvre la vraie unité prod « hr » + variantes. PAS « sem. » / « jours » (temps ≠ heures).
_HEURE_UNITS = {"hr", "hre", "heure", "heures", "h"}


def _is_heure_unit(unite) -> bool:
    return str(unite or "").strip().lower() in _HEURE_UNITS


def _heures_effectives(unite, heures, heures_manuelles, qte) -> float:
    """Heures utilisées dans le calcul MO. Ligne unité hr → heures effectives = qté
    SAUF override RÉEL (heures_manuelles ET heures ≠ 0). Un « override » à 0 est un
    FAUX override (ex. apply-typ d'un assemblage SANS rendement, ou flag posé par
    erreur) → on retombe sur le défaut qté (0 h sur une ligne facturée à l'heure n'a
    pas de sens). Miroir EXACT de getRow côté front."""
    h = float(heures or 0)
    if _is_heure_unit(unite) and not (heures_manuelles and h != 0):
        return float(qte or 0)
    return h


def register_ad_budget_routes(get_conn):

    jwt_user, jwt_user_or_token, jwt_admin, _ = make_jwt_deps(get_conn)

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
        # PHASE 3A — périmètre de visibilité :
        #   super_admin                       -> tous les projets ;
        #   org_role='admin' + organization_id -> ses projets + ceux de son
        #       organisation (vue superviseur, lecture seule) ;
        #   sinon                             -> ses propres projets (legacy).
        if user.get("platform_role") == "super_admin":
            where = ["TRUE"]
            params = []
        elif (user.get("org_role") == "admin"
              and user.get("organization_id") is not None):
            where = ["(p.user_id = %s OR p.organization_id = %s)"]
            params = [user["id"], user["organization_id"]]
        else:
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
                    INSERT INTO ad_budget.projets
                        (user_id, organization_id, nom, statut)
                    VALUES (%s, %s, %s, 'brouillon')
                    RETURNING id, nom
                    """,
                    (user["id"], user["organization_id"], project_name),
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
                    INSERT INTO ad_budget.projets
                        (user_id, organization_id, nom, statut)
                    VALUES (%s, %s, %s, 'brouillon')
                    RETURNING id, nom
                    """,
                    (user["id"], user["organization_id"], project_name),
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

            # D5 — auto-fill du taux horaire par défaut. Le mapping division
            # CSI -> taux est chargé UNE seule fois ici (et non par item) pour
            # éviter le N+1 ; résolution en mémoire dans la boucle ci-dessous.
            taux_default_map = _load_taux_default_map(conn)

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

                # D5 — taux horaire par défaut résolu via la division CSI
                # (section déjà strippée plus haut ; peut être vide ici -> 0).
                taux_horaire = (
                    taux_default_map.get(section[:2], 0) if section else 0
                )
                cur.execute(
                    """
                    INSERT INTO ad_budget.budget_lignes
                      (projet_id, section, description, unite, prix_unitaire,
                       qte, ajustement_pct, note, actif, source_file, type_source,
                       source_viu_analysis_id, source_viu_item_id, taux_horaire)
                    VALUES (%s, %s, %s, %s, 0, %s, 0, %s, TRUE, %s, 'viu_v2',
                            %s, %s, %s)
                    """,
                    (
                        project_id, section or None, description, unite,
                        qte, note_text,
                        source_file,
                        source_analysis_id, viu_item_id, taux_horaire,
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
    def create_projet(
        data: dict,
        user=Depends(jwt_user),
        authorization: Optional[str] = Header(None),
    ):
        # On force user_id depuis le JWT, pas depuis le body — un user ne
        # peut pas créer un projet pour quelqu'un d'autre.
        data["user_id"] = user["id"]

        # Sprint C1 — modes de liaison Ad HUB (pop AVANT _validate_projet_fields
        # pour éviter qu'il rejette ces champs custom).
        # Mode A : ad_hub_project_id fourni → set tel quel (projet HUB existant).
        # Mode B : create_ad_hub_project=true → créer HUB d'abord, lier au BUD.
        ad_hub_project_id = data.pop("ad_hub_project_id", None)
        create_ad_hub_project = bool(data.pop("create_ad_hub_project", False))
        if ad_hub_project_id is not None and create_ad_hub_project:
            raise HTTPException(
                status_code=400,
                detail=(
                    "ad_hub_project_id et create_ad_hub_project sont "
                    "mutuellement exclusifs"
                ),
            )
        if ad_hub_project_id is not None:
            try:
                ad_hub_project_id = int(ad_hub_project_id)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail="ad_hub_project_id doit être un entier",
                )

        # Sprint DT-56 D1 — référence applicative vers app_central.clients.id
        # (cross-base, pas de FK SQL). Optionnel : NULL = client legacy
        # (texte libre dans nom_client) ou ad hoc. Pop AVANT _validate
        # pour éviter rejet champ custom.
        client_id = data.pop("client_id", None)
        if client_id is not None:
            try:
                client_id = int(client_id)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail="client_id doit être un entier",
                )

        # Sprint A : valider les champs descriptifs avant d'insérer. Le default
        # de statut à la création est 'brouillon', donc passé en current_statut
        # pour la cross-field validation de date_adjudication.
        _validate_projet_fields(data, current_statut="brouillon")

        # Mode B — créer projet Ad HUB AVANT BUD. Le HUB est source de vérité
        # pour les docs (règle D-GED) → on crée là d'abord, on lie ensuite.
        # Atomicité (a) : si l'INSERT BUD échoue après, on soft-delete le HUB
        # via hub_service.soft_delete_project (best-effort, cf. bloc except).
        created_hub_id = None
        if create_ad_hub_project:
            jwt_token = _extract_bearer(authorization, None)
            # Mapping des champs BUD → HUB : sous-ensemble commun. Le HUB
            # exige `name` (>= 1 char) ; les autres sont optionnels et seront
            # filtrés s'ils sont vides pour ne pas spammer Ad HUB avec des
            # nulls qu'il refuserait via ses propres validateurs.
            hub_payload_raw = {
                "name": data.get("nom"),
                "client_nom": data.get("nom_client") or data.get("client"),
                "region": data.get("region"),
                "superficie_m2": data.get("superficie_m2"),
                "type_batiment": data.get("type_batiment"),
                "date_adjudication": data.get("date_adjudication"),
            }
            hub_payload = {
                k: v for k, v in hub_payload_raw.items()
                if v not in (None, "")
            }
            try:
                hub_project = hub_service.create_project(jwt_token, hub_payload)
            except hub_service.HubServiceError as e:
                # Pas encore d'INSERT BUD : rien à rollback.
                raise HTTPException(
                    status_code=502 if e.status_code is None else e.status_code,
                    detail=f"Ad HUB indisponible : {e.detail}",
                )
            created_hub_id = hub_project.get("id")
            if created_hub_id is None:
                raise HTTPException(
                    status_code=502,
                    detail="Ad HUB a accepté la création mais n'a pas retourné d'id",
                )
            ad_hub_project_id = created_hub_id

        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)

        def _date(v):
            return None if v in (None, "") else v

        def _pct(v):
            return 0 if v in (None, "") else v

        def _opt(v):
            return None if v in (None, "") else v

        try:
            # V1.3 — garde anti-double-budget : UN seul budget ACTIF
            # (statut <> 'archive') par projet Ad HUB. Si un existe déjà -> 409 +
            # son id : le front ouvre l'existant au lieu de créer un jumeau (cf.
            # incident "Presse de compaction"). En mode B le HUB vient d'être créé
            # -> aucun budget existant, le check est inoffensif. L'index partiel
            # uniq_budget_actif_par_hub est le filet STRUCTUREL si le code régresse.
            if ad_hub_project_id is not None:
                cur.execute(
                    "SELECT id FROM ad_budget.projets "
                    "WHERE ad_hub_project_id = %s AND statut <> 'archive' "
                    "ORDER BY id LIMIT 1",
                    (ad_hub_project_id,),
                )
                _exist = cur.fetchone()
                if _exist:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "budget_actif_existe",
                            "message": "Un budget actif existe déjà pour ce projet Ad HUB.",
                            "existing_budget_id": _exist["id"],
                        },
                    )
            cur.execute("""
                INSERT INTO ad_budget.projets
                  (user_id, organization_id, nom, client, adresse, description, statut,
                   nom_client, contact_client, email_client, telephone_client,
                   numero_projet, date_debut, date_fin,
                   contact_entrepreneur, email_entrepreneur, telephone_entrepreneur,
                   logo_base64,
                   pct_admin_conditions, pct_admin_architecture,
                   pct_admin_mecanique, pct_admin_excavation,
                   type_batiment, region, date_adjudication, superficie_m2,
                   ad_hub_project_id, client_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s)
                RETURNING *
            """, (
                data["user_id"],
                user["organization_id"],
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
                # Sprint C1 — lien Ad HUB (None si ni mode A ni mode B).
                ad_hub_project_id,
                # Sprint DT-56 D1 — lien app_central.clients.id (None si
                # legacy ou client ad hoc texte libre).
                client_id,
            ))
            row = cur.fetchone()
            projet_id = row["id"]
            # Plus de squelette par défaut : un nouveau budget naît VIDE. Le
            # contenu de départ vient UNIQUEMENT du système de gabarits (gabarit
            # par défaut / nouveau gabarit insérés via insert-gabarit), ou reste
            # vide (« sans gabarit »). L'ancien _apply_master_template s'empilait
            # par-dessus les gabarits insérés (redondant) — retiré.
            nb_lignes = 0
            conn.commit()
        except UniqueViolation:
            # Race : un budget actif pour ce HUB a été créé entre le pré-check et
            # l'INSERT -> l'index structurel a tranché. On répond 409 (PAS 500) en
            # pointant vers l'existant, et on nettoie un éventuel HUB mode B.
            conn.rollback()
            if created_hub_id is not None:
                jwt_token = _extract_bearer(authorization, None)
                hub_service.soft_delete_project(jwt_token, created_hub_id)
            existing_id = None
            try:
                c2 = conn.cursor(row_factory=dict_row)
                c2.execute(
                    "SELECT id FROM ad_budget.projets WHERE ad_hub_project_id = %s "
                    "AND statut <> 'archive' ORDER BY id LIMIT 1",
                    (ad_hub_project_id,),
                )
                _r = c2.fetchone(); c2.close()
                existing_id = _r["id"] if _r else None
            except Exception:
                pass
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "budget_actif_existe",
                    "message": "Un budget actif existe déjà pour ce projet Ad HUB.",
                    "existing_budget_id": existing_id,
                },
            )
        except Exception:
            # Sprint C1 — rollback applicatif : si on a créé un projet HUB
            # en amont (mode B), il devient orphelin sans contrepartie BUD.
            # On tente un soft-delete best-effort sans masquer l'erreur amont.
            conn.rollback()
            if created_hub_id is not None:
                jwt_token = _extract_bearer(authorization, None)
                hub_service.soft_delete_project(jwt_token, created_hub_id)
            raise
        finally:
            cur.close()
            conn.close()
        return {"status": "created", "projet": row, "nb_lignes_creees": nb_lignes}

    @router.get("/projets/{projet_id}")
    def get_projet(
        projet_id: int,
        include_hub: bool = False,
        user=Depends(jwt_user),
        authorization: Optional[str] = Header(None),
    ):
        # PHASE 3A — authentification + autorisation (lecture).
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
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

        # Sprint C1 — enrichissement Ad HUB optionnel. On échoue
        # gracieusement (warning log + ad_hub=None + ad_hub_fetch_error)
        # plutôt que de casser le GET principal si Ad HUB est indispo —
        # le projet BUD reste consultable même si la branche HUB déconne.
        result = dict(row)
        if include_hub and result.get("ad_hub_project_id") is not None:
            try:
                jwt_token = _extract_bearer(authorization, None)
                hub_data = hub_service.fetch_project(
                    jwt_token, result["ad_hub_project_id"],
                )
                result["ad_hub"] = hub_data  # None si 404 (projet HUB supprimé/inaccessible)
            except hub_service.HubServiceError as e:
                result["ad_hub"] = None
                result["ad_hub_fetch_error"] = (
                    f"Ad HUB {e.status_code}: {e.detail}"
                )
            except HTTPException:
                # _extract_bearer peut lever 401 si pas d'Authorization
                # côté query (ce qui ne devrait pas arriver puisque
                # jwt_user a déjà validé le JWT amont — mais ceinture et
                # bretelles).
                result["ad_hub"] = None
                result["ad_hub_fetch_error"] = "JWT token introuvable pour enrichissement"
        return result

    @router.put("/projets/{projet_id}")
    def update_projet(projet_id: int, data: dict, user=Depends(jwt_user)):
        # PHASE 3A — authentification + autorisation (écriture).
        _load_and_authorize_projet(get_conn, projet_id, user, "write")
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
        # Admin & profit PAR CATÉGORIE (mat/mo/st) — NULL = non défini = hérite
        # du % discipline (≠ PCT_FIELDS qui force 0). "" reçu -> NULL.
        PCT_CAT_FIELDS = {
            f"pct_admin_{g}_{c}"
            for g in ("conditions", "architecture", "mecanique", "excavation")
            for c in ("mat", "mo", "st")
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
            # Admin & profit par catégorie (12 champs ; NULL = hérite discipline)
            "pct_admin_conditions_mat", "pct_admin_conditions_mo", "pct_admin_conditions_st",
            "pct_admin_architecture_mat", "pct_admin_architecture_mo", "pct_admin_architecture_st",
            "pct_admin_mecanique_mat", "pct_admin_mecanique_mo", "pct_admin_mecanique_st",
            "pct_admin_excavation_mat", "pct_admin_excavation_mo", "pct_admin_excavation_st",
            # Sprint A
            "type_batiment", "region", "date_adjudication", "superficie_m2",
            "dernier_snapshot_id",
            # Params globaux du tableau budget
            "mobilisation", "surface_plancher",
            "hauteur_cloisons", "longueur_cloisons",
            # Sprint DT-56 D1 — lien vers app_central.clients.id
            # (cross-base, BIGINT nullable). Le client peut être relié
            # ou délié après la création initiale.
            "client_id",
            # Option d'arrondissement au dollar (mode calcul/affichage).
            "arrondi_dollar",
            # Étiquette de révision éditable (Espace Rapports). PAS dans
            # SNAPSHOT_AFFECTING_FIELDS -> l'éditer ne bump pas la révision.
            "revision_label",
        ]:
            if field in data:
                v = data[field]
                if field == "arrondi_dollar":
                    v = bool(v)
                elif field in PCT_CAT_FIELDS and (v == "" or v is None):
                    v = None  # NULL = non défini = hérite du % discipline
                elif field in NULLABLE_EMPTY_FIELDS and v == "":
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
        # Bump de révision UNIQUEMENT si un champ affectant le snapshot/montant a
        # changé (% A&P, mode A&P, arrondi, params de quantités). Les métadonnées
        # (nom, client, statut, notes…) ne bumpent pas.
        if set(data) & SNAPSHOT_AFFECTING_FIELDS:
            _mark_budget_dirty_if_emitted(projet_id)
        return updated

    @router.delete("/projets/{projet_id}")
    def delete_projet(
        projet_id: int,
        user=Depends(jwt_user),
        authorization: Optional[str] = Header(None),
    ):
        # PHASE 3A — authentification + autorisation (écriture).
        _load_and_authorize_projet(get_conn, projet_id, user, "write")
        conn = get_conn()
        cur = conn.cursor()
        # Les budget_lignes sont supprimées automatiquement (CASCADE).
        # RETURNING ad_hub_project_id : on récupère le lien Ad HUB AVANT de
        # perdre la row, pour soft-delete le master en aval (symétrie du fix
        # create, Sprint C1). Sans ça, supprimer un projet hub-first laissait
        # le master app_hub.projects vivant = orphelin (cf. fix create).
        cur.execute(
            "DELETE FROM ad_budget.projets WHERE id = %s "
            "RETURNING ad_hub_project_id",
            (projet_id,),
        )
        deleted = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        # Best-effort, APRÈS le commit local : si le projet était lié à un
        # master Ad HUB (mode A ou B), on le soft-delete aussi pour ne pas
        # laisser d'orphelin. On log mais on ne re-raise pas — le DELETE BUD
        # a réussi, c'est ce qui compte pour l'utilisateur ; un master Ad HUB
        # résiduel reste auditable et nettoyable. Ordre (local puis hub) :
        # évite un lien cassé (hub soft-deleted mais local encore présent) si
        # le second échouait.
        ad_hub_project_id = deleted[0] if deleted else None
        if ad_hub_project_id is not None:
            jwt_token = _extract_bearer(authorization, None)
            hub_service.soft_delete_project(jwt_token, ad_hub_project_id)
        return {"status": "deleted"}

    @router.get("/projets/{projet_id}/export-for-con")
    def export_projet_for_con(projet_id: int, user=Depends(jwt_user)):
        """Sprint 1.A Ad CON — Export du projet+lignes pour Ad CON.

        Architecture PULL : Ad CON viendra appeler cet endpoint au Sprint 1.B
        quand un user clique sur "🏗️ Démarrer suivi chantier" depuis Ad BUD,
        pour créer le con_projects + figer les con_lines.

        Mapping BUD → con_lines (17 champs cibles, cadré au STOP 1.A.1) :
          bud_line_id                ← budget_lignes.id          (INTEGER stringifié ;
                                                                  Ad CON Sprint 1.B migrera
                                                                  bud_line_id en BIGINT)
          csi_code                   ← budget_lignes.section      (varchar 100, ex "01 50 01")
          csi_division               ← LEFT(TRIM(section), 2)     (dérivé)
          description                ← budget_lignes.description
          discipline                 ← NULL                       (TODO derive in future sprint
                                                                  via csi_division_default_metier
                                                                  → taux_horaires.metier)
          qty_origin                 ← _effective_qte(...)        (calculé live, BUD.qte=0 partout)
          unit                       ← budget_lignes.unite
          mat_cost_unit_origin       ← prix_unitaire
          mat_adjustment_pct_origin  ← ajust_materiaux
          mat_subtotal_origin        ← qty_eff × prix_u × (1+ajust_mat/100)   (calc backend)
          mo_hours_origin            ← heures
          mo_rate_origin             ← taux_horaire
          mo_adjustment_pct_origin   ← ajust_main_oeuvre
          mo_subtotal_origin         ← qty_eff × heures × taux × (1+ajust_mo/100) (calc backend)
          st_type                    ← sous_traitant_type         (NULL en prod actuellement)
          st_supplier_origin         ← sous_traitant_nom
          st_amount_origin           ← sous_traitant_montant
          st_adjustment_pct_origin   ← ajust_sous_traitant
          st_subtotal_origin         ← st_amount × (1+ajust_st/100)           (calc backend)

        Sécurité :
          - JWT user requis (jwt_user dep)
          - Auth via _load_and_authorize_projet (mode='read', supervisor OK)
          - Refuse 403 si statut != 'adjuge' (defense in depth ; UI ne montre
            le bouton "Démarrer suivi chantier" que sur projets adjugés, mais
            on bloque aussi côté backend)
          - Lignes inactives (actif=FALSE) EXCLUSES de l'export

        organization_id fallback (décision STOP 1.A.1, critique #2) :
          Si projet.organization_id IS NULL (projets legacy pre-multi-tenant
          comme le projet 97 VQ-CED Contracta), on fallback sur user.organization_id
          du JWT et on marque "organization_id_source": "jwt_fallback".
          Sinon "organization_id_source": "project".

        raw_snapshot : copie INTÉGRALE projet + 30 colonnes budget_lignes,
        JSON-safe via _json_dumps_with_default (Decimal/date → str). Sprint 1.B
        stockera dans con_budget_snapshots.snapshot_data JSONB pour audit total.
        """
        # 1) Auth (lecture) + 404 si hors périmètre.
        _load_and_authorize_projet(get_conn, projet_id, user, "read")

        # 2) Charge le projet COMPLET puis ses lignes actives.
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute("SELECT * FROM ad_budget.projets WHERE id = %s", (projet_id,))
            projet = cur.fetchone()
            if projet is None:
                raise HTTPException(status_code=404, detail="Projet introuvable")

            # Defense in depth — UI n'affiche pas le bouton hors 'adjuge' mais
            # un user qui tape l'URL directement doit être bloqué aussi.
            if projet["statut"] != "adjuge":
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Le projet doit être au statut Adjugé pour être exporté "
                        f"vers Ad CON (statut actuel: {projet['statut']})"
                    ),
                )

            cur.execute(
                """SELECT * FROM ad_budget.budget_lignes
                   WHERE projet_id = %s AND actif = TRUE
                   ORDER BY section, description, id""",
                (projet_id,),
            )
            budget_rows = cur.fetchall()
        finally:
            try:
                cur.close()
            except Exception:
                pass
            conn.close()

        # 3) Params globaux du projet (miroir _create_snapshot Sprint B).
        mob = float(projet.get("mobilisation") or 0)
        sp = float(projet.get("surface_plancher") or 0)
        hc = float(projet.get("hauteur_cloisons") or 0)
        lc = float(projet.get("longueur_cloisons") or 0)
        surface_mur = hc * lc
        surface_gypse = surface_mur * 2

        # 4) Ventilation ligne par ligne + raw passthrough.
        lines_out = []
        contract_total = 0.0
        for idx, row in enumerate(budget_rows, start=1):
            section = (row.get("section") or "").strip()
            csi_division = section[:2] if section else None

            qty_eff = _effective_qte(row, mob, sp, surface_mur, surface_gypse)

            # Cast safe Decimal -> float (psycopg renvoie Decimal pour NUMERIC).
            prix_u = float(row.get("prix_unitaire") or 0)
            ajust_mat = float(row.get("ajust_materiaux") or 0)
            heures = float(row.get("heures") or 0)
            taux = float(row.get("taux_horaire") or 0)
            ajust_mo = float(row.get("ajust_main_oeuvre") or 0)
            st_amount = float(row.get("sous_traitant_montant") or 0)
            ajust_st = float(row.get("ajust_sous_traitant") or 0)

            mat_subtotal = qty_eff * prix_u * (1.0 + ajust_mat / 100.0)
            mo_subtotal = qty_eff * heures * taux * (1.0 + ajust_mo / 100.0)
            st_subtotal = st_amount * (1.0 + ajust_st / 100.0)
            contract_total += mat_subtotal + mo_subtotal + st_subtotal

            lines_out.append({
                # INTEGER stringifié : décision STOP 1.A.1 critique #1 (Sprint 1.B
                # migrera bud_line_id en BIGINT côté Ad CON).
                "bud_line_id": str(row["id"]),
                "line_order": idx,
                "csi_code": section or None,
                "csi_division": csi_division,
                "description": row.get("description"),
                # TODO derive in future sprint when needed (cf. STOP 1.A.1 #3).
                "discipline": None,
                "qty_origin": round(qty_eff, 4),
                "unit": row.get("unite"),
                "mat_cost_unit_origin": prix_u,
                "mat_adjustment_pct_origin": ajust_mat,
                "mat_subtotal_origin": round(mat_subtotal, 2),
                "mo_hours_origin": heures,
                "mo_rate_origin": taux,
                "mo_adjustment_pct_origin": ajust_mo,
                "mo_subtotal_origin": round(mo_subtotal, 2),
                "st_type": row.get("sous_traitant_type"),
                "st_supplier_origin": row.get("sous_traitant_nom"),
                "st_amount_origin": st_amount,
                "st_adjustment_pct_origin": ajust_st,
                "st_subtotal_origin": round(st_subtotal, 2),
            })

        # 5) Fallback organization_id (décision STOP 1.A.1 critique #2).
        proj_org = projet.get("organization_id")
        if proj_org is None:
            organization_id_out = user.get("organization_id")
            org_source = "jwt_fallback"
        else:
            organization_id_out = str(proj_org)
            org_source = "project"

        # 6) Sérialiser raw_snapshot via le helper Adision déjà éprouvé
        # (_json_dumps_with_default convertit Decimal/date/datetime → str).
        # Tour-trip pour normaliser en pur JSON-natif.
        raw_snapshot = json.loads(_json_dumps_with_default({
            "projet": dict(projet),
            "budget_lignes": [dict(r) for r in budget_rows],
        }))

        return {
            "bud_project_id": str(projet["id"]),
            "organization_id": (
                str(organization_id_out) if organization_id_out else None
            ),
            "snapshot_metadata": {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "exported_by_user_id": user["id"],
                "bud_schema_version": "v1.0",
                "lines_count": len(lines_out),
            },
            "project": {
                "name": projet.get("nom"),
                "project_number": projet.get("numero_projet") or None,
                "client_name": (
                    projet.get("nom_client") or projet.get("client") or None
                ),
                "address": projet.get("adresse"),
                # TODO derive in future sprint when needed (cf. STOP 1.A.1 #4).
                "city": None,
                "status": projet.get("statut"),
                "type_batiment": projet.get("type_batiment"),
                "region": projet.get("region"),
                # Lien Ad HUB (INTEGER → app_hub.projects.id, nullable). Exposé pour
                # qu'Ad CON rattache le projet au hub directement (brique 3). LECTURE
                # SEULE : on remonte la valeur déjà présente sur le projet BUD.
                "ad_hub_project_id": projet.get("ad_hub_project_id"),
                "date_adjudication": (
                    projet["date_adjudication"].isoformat()
                    if projet.get("date_adjudication") else None
                ),
                "superficie_m2": (
                    float(projet["superficie_m2"])
                    if projet.get("superficie_m2") is not None else None
                ),
                "contract_initial_amount": round(contract_total, 2),
                "organization_id_source": org_source,
            },
            "lines": lines_out,
            "raw_snapshot": raw_snapshot,
        }

    @router.get("/projets/{projet_id}/has-con")
    def projet_has_con(
        projet_id: int,
        user=Depends(jwt_user),
        authorization: Optional[str] = Header(None),
    ):
        """Sprint 1.A Ad CON — Détection d'existence d'un projet Ad CON
        pour adapter le label du bouton header projet Ad BUD.

        - Si conStatus.exists == false → bouton "🏗️ Démarrer suivi chantier"
        - Si conStatus.exists == true  → bouton "🏗️ Voir suivi chantier"

        Architecture : 1 appel cross-service depuis Ad BUD vers Ad CON
        (GET /api/projects/by-bud/{bud_project_id}). Cf. con_service.py
        pour le contrat HTTP attendu côté Ad CON Sprint 1.B.

        Fallback gracieux total (cf. con_service docstring) : Ad CON
        404/timeout/réseau/5xx → {exists:false}. Garantit qu'Ad BUD reste
        opérationnel même si Ad CON est down ou pas encore Sprint 1.B livré.

        Cache 5 min in-memory côté Ad BUD (con_service._HAS_CON_CACHE) sur
        les réponses 200/404 (pas sur les fallbacks transitoires).

        Auth : JWT user requis + autorisation projet (read mode, supervisor
        org_role='admin' OK). Le JWT user est forwardé vers Ad CON.
        """
        # 1) Auth + 404 si projet hors périmètre (avant tout appel cross-service).
        _load_and_authorize_projet(get_conn, projet_id, user, "read")

        # 2) Récupère le JWT brut depuis le header pour le forwarder vers Ad CON
        # (même module HS256 partagé, Ad CON vérifie signature + ad_con dans modules).
        jwt_token = _extract_bearer(authorization, None)

        # 3) Délégation au client cross-service (cache + fallback gracieux).
        return con_service.check_has_con(jwt_token, projet_id)

    @router.patch("/projets/{projet_id}/notes")
    def update_projet_notes(projet_id: int, data: dict, user=Depends(jwt_user)):
        # PHASE 3A — authentification + autorisation (écriture).
        _load_and_authorize_projet(get_conn, projet_id, user, "write")
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

    @router.patch("/projets/{projet_id}/verrou")
    def toggle_projet_verrou(projet_id: int, data: dict, user=Depends(jwt_user)):
        """Verrouille / déverrouille un projet (lecture seule), INDÉPENDAMMENT du statut
        de cycle de vie. Accessible à tout user autorisé en ÉCRITURE sur le projet (même
        autorisation que les autres writes) MAIS bypass le gate de verrou
        (check_lock=False) — sinon on ne pourrait jamais déverrouiller. Trace
        verrouille_par (email) + verrouille_le ; les efface au déverrouillage."""
        _load_and_authorize_projet(get_conn, projet_id, user, "write", check_lock=False)
        verrouille = bool(data.get("verrouille"))
        email = user.get("email") or (str(user.get("id")) if user.get("id") is not None else "inconnu")
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            if verrouille:
                cur.execute(
                    "UPDATE ad_budget.projets SET is_verrouille = TRUE, "
                    "verrouille_par = %s, verrouille_le = NOW(), updated_at = NOW() "
                    "WHERE id = %s RETURNING is_verrouille, verrouille_par, verrouille_le",
                    (email, projet_id),
                )
            else:
                cur.execute(
                    "UPDATE ad_budget.projets SET is_verrouille = FALSE, "
                    "verrouille_par = NULL, verrouille_le = NULL, updated_at = NOW() "
                    "WHERE id = %s RETURNING is_verrouille, verrouille_par, verrouille_le",
                    (projet_id,),
                )
            row = cur.fetchone()
            conn.commit()
        finally:
            cur.close(); conn.close()
        return {"status": "ok", "is_verrouille": row["is_verrouille"],
                "verrouille_par": row["verrouille_par"], "verrouille_le": row["verrouille_le"]}

    @router.post("/projets/{projet_id}/duplicate")
    def duplicate_projet(projet_id: int, user=Depends(jwt_user)):
        # PHASE 3A — authentification + autorisation (écriture : dupliquer le
        # projet d'un autre user est refusé, même à un superviseur d'org).
        # check_lock=False : la duplication LIT la source et CRÉE un nouveau projet
        # (déverrouillé par défaut) — elle ne modifie pas le projet verrouillé.
        _load_and_authorize_projet(get_conn, projet_id, user, "write", check_lock=False)
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
              (user_id, organization_id, nom, client, adresse, description, statut, notes,
               nom_client, contact_client, email_client, telephone_client,
               numero_projet, date_debut, date_fin,
               contact_entrepreneur, email_entrepreneur, telephone_entrepreneur,
               logo_base64,
               pct_admin_conditions, pct_admin_architecture,
               pct_admin_mecanique, pct_admin_excavation)
            SELECT %s, %s, %s, client, adresse, description, statut, notes,
                   nom_client, contact_client, email_client, telephone_client,
                   numero_projet, date_debut, date_fin,
                   contact_entrepreneur, email_entrepreneur, telephone_entrepreneur,
                   logo_base64,
                   pct_admin_conditions, pct_admin_architecture,
                   pct_admin_mecanique, pct_admin_excavation
            FROM ad_budget.projets
            WHERE id = %s
            RETURNING *
        """, (user["id"], user["organization_id"], f"{src['nom']} (copie)", projet_id))
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
    def export_projet_excel(projet_id: int, user=Depends(jwt_user)):
        # PHASE 3A — authentification + autorisation (lecture : export = lecture).
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
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
                   heures, heures_manuelles, taux_horaire,
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
            # Heures effectives : unité hr sans saisie manuelle → heures = qté.
            heures = _heures_effectives(l["unite"], l["heures"], l.get("heures_manuelles"), qte)
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
        user=Depends(jwt_user_or_token),
        authorization: Optional[str] = Header(None),
        token: Optional[str] = Query(None),
        actifs_seulement: bool = True,
        avec_prix: bool = True,
        sections: str = Query("", description="CSV des sections à inclure ; vide = toutes"),
        colonnes: str = Query("", description="CSV des colonnes à inclure ; vide = toutes"),
        sous_totaux: Optional[str] = Query(None, description="CSV des regroupements à afficher en sous-total ; param omis = tous, vide explicite = aucun"),
        admin_profits: Optional[str] = Query(None, description="CSV des regroupements pour admin&profit ; param omis = tous, vide explicite = aucun"),
        avec_sous_total_avant_taxes: bool = True,
        avec_tps: bool = True,
        avec_tvq: bool = True,
        inclure_ligne_groupe: bool = False,
        inclure_ligne_titres: bool = True,
        orientation: str = "portrait",
        mobilisation: float = 0,
        surface_plancher: float = 0,
        hauteur_cloisons: float = 0,
        longueur_cloisons: float = 0,
        avec_heures: bool = True,
    ):
        # PHASE 3A — auth (lecture). Délègue au builder partagé qui produit le
        # PDF ET le snapshot JSON dans la MÊME passe ; la route ne renvoie que
        # le PDF (rendu byte-identique — l'émission Espace Rapports réutilise
        # _build_projet_report pour le PDF + snapshot).
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        buf, _snapshot = _build_projet_report(
            projet_id, actifs_seulement, avec_prix, sections, colonnes,
            sous_totaux, admin_profits, avec_sous_total_avant_taxes, avec_tps,
            avec_tvq, orientation, mobilisation, surface_plancher,
            hauteur_cloisons, longueur_cloisons, avec_heures=avec_heures,
            jwt_token=_extract_bearer(authorization, token),
            inclure_ligne_groupe=inclure_ligne_groupe,
            inclure_ligne_titres=inclure_ligne_titres,
        )
        safe_nom = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (_snapshot["project"]["nom"] or "projet")).strip() or "projet"
        return StreamingResponse(
            buf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_nom}.pdf"'},
        )

    @router.get("/projets/{projet_id}/report-snapshot")
    def export_projet_report_snapshot(
        projet_id: int,
        user=Depends(jwt_user_or_token),
        authorization: Optional[str] = Header(None),
        token: Optional[str] = Query(None),
        actifs_seulement: bool = True,
        avec_tps: bool = True,
        avec_tvq: bool = True,
    ):
        # Snapshot JSON seul (inspection + preuve poste-par-poste) — calculé par
        # la MÊME passe que le PDF, donc nombres identiques au rapport.
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        _buf, snapshot = _build_projet_report(
            projet_id, actifs_seulement, True, "", "", None, None, True,
            avec_tps, avec_tvq, "portrait", 0, 0, 0, 0,
            jwt_token=_extract_bearer(authorization, None),
        )
        return snapshot

    def _build_projet_report(
        projet_id,
        actifs_seulement=True,
        avec_prix=True,
        sections="",
        colonnes="",
        sous_totaux=None,
        admin_profits=None,
        avec_sous_total_avant_taxes=True,
        avec_tps=True,
        avec_tvq=True,
        orientation="portrait",
        mobilisation=0,
        surface_plancher=0,
        hauteur_cloisons=0,
        longueur_cloisons=0,
        avec_heures=True,
        jwt_token=None,
        inclure_ligne_groupe=False,
        inclure_ligne_titres=True,
    ):
        """Construit le PDF rapport ET le snapshot JSON dans la MÊME passe.
        Retourne (buf: BytesIO, snapshot: dict). Le rendu PDF est INCHANGÉ
        (rétrocompat byte-identique — preuve hash). L'auth est faite par
        l'appelant (route), qui passe AUSSI le jwt_token (pour les logos
        client/org via hub_service) ; None -> logo neutre, jamais de crash."""
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
                   heures, heures_manuelles, taux_horaire, cout_sous_traitant, sous_traitant_nom,
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

        # Titre PRINCIPAL = NOM DU PROJET (gros, centré) ; SOUS-TITRE dessous =
        # « Rapport de budget — [révision] ». Même source que le bloc CLIENT
        # (projet["nom"]). Nom vide -> sous-titre seul (jamais de titre vide).
        nom_titre_style = ParagraphStyle(
            "PdfNomProjet", parent=ss["Title"], fontSize=15, leading=17, alignment=1,
            spaceAfter=2, textColor=colors.HexColor("#1e3a8a"),
        )
        sous_titre_style = ParagraphStyle(
            "PdfSousTitre", parent=ss["Normal"], fontSize=9, leading=11, alignment=1,
            textColor=colors.HexColor("#475569"),
        )
        _rev_lbl = projet.get("revision_label") or "Originale"
        _nom_projet = (projet.get("nom") or "").strip()
        title_cell = []
        if _nom_projet:
            title_cell.append(Paragraph(_nom_projet.upper(), nom_titre_style))
        title_cell.append(Paragraph("Rapport de budget — " + _rev_lbl, sous_titre_style))

        # Sprint DT-56 D5 — white-label PDF officiel.
        # Priorité du logo en haut du rapport :
        #   1. logo du CLIENT lié (projet.client_id non-NULL + client a un
        #      logo_base64 récupéré via hub_service cross-service)
        #   2. logo HISTORIQUE du projet (ad_budget.projets.logo_base64 —
        #      compat anciens projets sans client formel)
        #   3. logo de l'ORG du projet (HUB Info entreprise / R2) — défaut
        #      white-label : chaque org voit SON logo (multi-tenant).
        #   4. Placeholder NEUTRE (aucun logo) — décision Simon : JAMAIS Adision.
        # Échec gracieux : si un fetch Ad HUB plante (network/401/timeout),
        # on retombe sur le rang suivant plutôt que de casser l'export PDF.
        # jwt_token est fourni par l'appelant (route) ; None -> pas de fetch
        # HUB -> logo neutre (jamais de crash).
        # rang 2 (défaut) : logo historique du projet.
        chosen_logo_base64 = projet.get("logo_base64") or ""
        # rang 1 : logo du client lié — PRIME sur le logo projet.
        if projet.get("client_id") and jwt_token:
            try:
                client_data = hub_service.fetch_client(jwt_token, int(projet["client_id"]))
                if client_data and client_data.get("logo_base64"):
                    chosen_logo_base64 = client_data["logo_base64"]
            except hub_service.HubServiceError as e:
                print(
                    f"[ad-bud PDF] fetch_client {projet['client_id']} échec, "
                    f"fallback org/neutre : {e}",
                    flush=True,
                )
        # rang 3 — défaut white-label : logo de l'org du projet (jamais Adision).
        if not chosen_logo_base64 and jwt_token:
            chosen_logo_base64 = fetch_org_logo_base64(jwt_token, projet.get("organization_id"))
        logo_flowable = build_pdf_logo(chosen_logo_base64)
        # Logo plus grand (180pt) → colonnes latérales 190pt pour l'accommoder
        # avec une petite marge ; centre = total_w - 380 (~147pt portrait,
        # 327pt landscape) pour que le titre tienne sur une seule ligne.
        title_side = 190
        title_row = Table(
            [[logo_flowable, title_cell, ""]],
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
            "prix_unitaire", "ajust_materiaux", "st_materiaux",
            "heures", "taux_horaire", "ajust_main_oeuvre", "st_main_oeuvre",
            "sous_traitant_type", "sous_traitant_nom",
            "sous_traitant_montant", "ajust_sous_traitant", "st_sous_traitant",
            "sous_total", "ajustement_pct", "total", "note",
        ]
        # Libellés ALIGNÉS sur l'écran (App.jsx). Les longs wrappent via Paragraph.
        COL_LABELS = {
            "section": "Section CSI", "description": "Description", "qte": "Qté",
            "unite": "Unité",
            "prix_unitaire": "Coût u.", "ajust_materiaux": "Aj. mat.",
            "st_materiaux": "Sous-total Mat",
            "heures": "Heures", "taux_horaire": "Taux horaire",
            "ajust_main_oeuvre": "Aj. M-O", "st_main_oeuvre": "Sous-total M-O",
            "sous_traitant_type": "Type de document",
            "sous_traitant_nom": "Sous-traitants et fournisseurs",
            "sous_traitant_montant": "Mt S-T", "ajust_sous_traitant": "Aj. S-T",
            "st_sous_traitant": "Sous-total S-TR",
            "sous_total": "S/T ligne", "ajustement_pct": "Adj %",
            "total": "Total", "note": "Note",
        }
        _col_widths_base = {
            "section": 55, "description": 130, "qte": 28, "unite": 30,
            "prix_unitaire": 45, "ajust_materiaux": 30, "st_materiaux": 50,
            "heures": 30, "taux_horaire": 35, "ajust_main_oeuvre": 30, "st_main_oeuvre": 50,
            "sous_traitant_type": 45, "sous_traitant_nom": 70,
            "sous_traitant_montant": 45, "ajust_sous_traitant": 30, "st_sous_traitant": 50,
            "sous_total": 55, "ajustement_pct": 30,
            "total": 60, "note": 80,
        }
        # Mapping colonne → section. Sert pour la rangée header de section
        # avec colspan ; les colonnes hors section restent vides dans cette
        # rangée.
        COL_SECTION = {
            "prix_unitaire": "materiaux", "ajust_materiaux": "materiaux",
            "st_materiaux": "materiaux",
            "heures": "mainOeuvre", "taux_horaire": "mainOeuvre",
            "ajust_main_oeuvre": "mainOeuvre", "st_main_oeuvre": "mainOeuvre",
            "sous_traitant_type": "sousTraitant",
            "sous_traitant_nom": "sousTraitant",
            "sous_traitant_montant": "sousTraitant",
            "ajust_sous_traitant": "sousTraitant",
            "st_sous_traitant": "sousTraitant",
        }
        SECTION_LABELS = {
            "materiaux": "MATÉRIAUX",
            "mainOeuvre": "MAIN-D'ŒUVRE",
            "sousTraitant": "SOUS-TRAITANTS ET FOURNISSEURS",
        }
        SECTION_COLORS = {
            "materiaux": colors.HexColor("#E3E7F4"),
            "mainOeuvre": colors.HexColor("#FCE5E5"),
            "sousTraitant": colors.HexColor("#DDF2EC"),
        }
        PRIX_DEPENDENT = {
            "prix_unitaire", "ajust_materiaux", "st_materiaux",
            "heures", "taux_horaire", "ajust_main_oeuvre", "st_main_oeuvre",
            "sous_traitant_type", "sous_traitant_nom",
            "sous_traitant_montant", "ajust_sous_traitant", "st_sous_traitant",
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
        # `headers` (rangée de libellés) construite plus bas en Paragraph (wrap).
        # Scale calculé sur la sélection effective : tient compte des opt-in.
        _scale = total_w / sum(_col_widths_base[c] for c in selected)
        col_widths = [_col_widths_base[c] * _scale for c in selected]

        # Police adaptive : 8pt par défaut, 7pt si > 12 colonnes pour rester
        # lisible sans débordement en paysage A4.
        body_fontsize = 7 if len(selected) > 12 else 8
        # En-têtes en Paragraph (et NON strings brutes) : reportlab WRAPPE alors
        # le libellé sur plusieurs lignes dans la largeur de colonne — les titres
        # longs (« Sous-traitants et fournisseurs », « Type de document », « Sous-
        # total … ») ne tiennent pas sur une ligne en paysage 20 colonnes. Centré,
        # blanc, gras ; splitLongWords (défaut True) évite tout débordement. La
        # hauteur de la rangée d'en-tête s'ajuste seule à la ligne la plus haute.
        header_para_style = ParagraphStyle(
            "BudgetHeader", fontName="Helvetica-Bold",
            fontSize=body_fontsize, leading=body_fontsize + 1.5,
            textColor=colors.white, alignment=1,  # 1 = TA_CENTER
        )
        headers = [Paragraph(COL_LABELS[c], header_para_style) for c in selected]

        # Construction de la rangée "section header" (colspans). On group les
        # colonnes consécutives qui partagent un même groupe de section ;
        # une section qui n'a aucune colonne visible disparaît automatiquement.
        # Rangée de REGROUPEMENT (bandeaux MATÉRIAUX / MAIN-D'ŒUVRE / SOUS-TRAITANTS
        # ET FOURNISSEURS au-dessus des libellés) : affichée UNIQUEMENT si le toggle
        # inclure_ligne_groupe est coché (défaut OFF). On group les colonnes
        # consécutives d'une même section (colspan) ; une section sans colonne
        # visible disparaît d'elle-même.
        section_header_row = None
        section_spans = []  # [(start_col, end_col, section_key)]
        any_section_visible = any(COL_SECTION.get(c) for c in selected)
        if inclure_ligne_groupe and any_section_visible:
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

        def _frca_num(value):
            # Format fr-CA : séparateur de milliers = ESPACE, décimale = VIRGULE.
            # Ex. 1234.5 -> "1 234,50". US (,/.) puis permutation. On N'utilise PAS
            # U+00A0 (insécable) : il se rend mal dans ce PDF reportlab. Un espace
            # régulier rend bien ; dans une colonne montant le nombre ne se coupe pas.
            try:
                v = float(value or 0)
            except (TypeError, ValueError):
                v = 0.0
            return f"{v:,.2f}".replace(",", " ").replace(".", ",")

        def cell_for(col, l, qte, prix, adj, st, tot,
                     heures, taux, st_mo, st_mat, st_st, st_montant,
                     ajm, ajmo, ajst):
            if col == "section": return l["section"] or ""
            if col == "description": return cell(l["description"] or "")
            if col == "qte": return f"{qte:g}"
            if col == "unite": return l["unite"] or ""
            if col == "prix_unitaire": return _frca_num(prix)
            if col == "ajust_materiaux": return f"{ajm:g}"
            if col == "st_materiaux": return _frca_num(st_mat)
            if col == "heures": return f"{heures:g}"
            if col == "taux_horaire": return _frca_num(taux)
            if col == "ajust_main_oeuvre": return f"{ajmo:g}"
            if col == "st_main_oeuvre": return _frca_num(st_mo)
            if col == "sous_traitant_type": return l["sous_traitant_type"] or ""
            if col == "sous_traitant_nom": return cell(l["sous_traitant_nom"] or "")
            if col == "sous_traitant_montant": return _frca_num(st_montant)
            if col == "ajust_sous_traitant": return f"{ajst:g}"
            if col == "st_sous_traitant": return _frca_num(st_st)
            if col == "sous_total": return _frca_num(st)
            if col == "ajustement_pct": return f"{adj:g}"
            if col == "total": return _frca_num(tot)
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
            row[total_idx] = _frca_num(value)
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

        # Préfixage du tableau par les rangées d'en-tête réellement demandées :
        # 0, 1 ou 2 rangées selon les 2 toggles. On mémorise l'INDEX de chaque
        # rangée (groupe / titres) pour que les styles indexés (SPAN, BACKGROUND,
        # FONTNAME…) suivent dynamiquement le nombre de rangées présentes — c'est
        # le recalage qui évitait un bandeau résiduel mal stylé.
        table_data = []
        group_row_idx = None
        titres_row_idx = None
        if section_header_row is not None:        # groupe (gated inclure_ligne_groupe)
            group_row_idx = len(table_data)
            table_data.append(section_header_row)
        if inclure_ligne_titres:
            titres_row_idx = len(table_data)
            table_data.append(headers)
        header_offset = len(table_data)           # = nb de rangées d'en-tête
        subtotal_rows = []
        group_subtotals = {key: 0.0 for key, _, _, _ in BUDGET_GROUPS_PDF}
        # Sous-totaux par CATÉGORIE et par groupe (pré-ajustement_pct, comme le
        # front) pour l'admin & profit décomposé.
        group_sub_mat = {key: 0.0 for key, _, _, _ in BUDGET_GROUPS_PDF}
        group_sub_mo = {key: 0.0 for key, _, _, _ in BUDGET_GROUPS_PDF}
        group_sub_st = {key: 0.0 for key, _, _, _ in BUDGET_GROUPS_PDF}
        non_grouped_total = 0.0
        # Totaux d'heures : production (MO hors contremaître) vs contremaître.
        # Heures lues directement dans la colonne `heures` (décision Simon :
        # somme directe, pas de conversion ; l'unité « sem. » est cosmétique).
        mo_h = 0.0
        contremaitre_h = 0.0
        # Sous-totaux GLOBAUX par section (toutes lignes, groupées ou non) pour
        # le snapshot : sous_total_mat/mo/st. Exacts ici, arrondis à la sortie.
        snap_mat = 0.0
        snap_mo = 0.0
        snap_st = 0.0

        # Mode arrondi au dollar (option projet). R() arrondit au dollar le plus
        # près en mode arrondi, sinon identité (rétrocompat exacte). Appliqué au
        # TOTAL DE LIGNE, à l'A&P par groupe et aux taxes — modèle « lignes + A&P
        # arrondis séparément » : sous-total = Σ lignes arrondies + Σ A&P arrondis.
        arr = bool(projet.get("arrondi_dollar"))

        def R(v):
            # Arrondi « demi vers le HAUT » (_js_round = math.floor(v+0.5)),
            # IDENTIQUE à JS Math.round du front — et NON le round() natif de
            # Python (banker's / demi-vers-pair) qui divergeait d'un dollar sur
            # un cas pile au demi (ex. A&P 6 568,50 → 6 569 et non 6 568). Le
            # front fait foi : rapport PDF = devis = écran, au dollar près.
            return float(_js_round(v)) if arr else v

        for sec, sec_lignes in sections_groups.items():
            sec_total = 0.0
            section_has_visible_lines = False
            for l in sec_lignes:
                qte = _effective_qte(l, mobilisation, surface_plancher,
                                     surface_mur_calc, surface_gypse_calc)
                # Avec la refonte 3 sections, une ligne peut être active sans
                # qte > 0 (ex. ligne pure sous-traitant). On garde le filtre
                # qte > 0 historique mais on l'élargit aux lignes M-O / S-T.
                # Heures effectives : unité hr sans saisie manuelle → heures = qté.
                heures = _heures_effectives(l["unite"], l["heures"], l.get("heures_manuelles"), qte)
                taux = float(l["taux_horaire"] or 0)
                ajmo = float(l["ajust_main_oeuvre"] or 0)
                st_montant = float(l["sous_traitant_montant"] or 0)
                ajst = float(l["ajust_sous_traitant"] or 0)
                has_mo = heures > 0 and taux > 0
                has_st = st_montant > 0
                if qte <= 0 and not has_mo and not has_st:
                    continue
                if heures > 0:
                    if _is_contremaitre(l["description"]):
                        contremaitre_h += heures
                    else:
                        mo_h += heures
                prix = float(l["prix_unitaire"] or 0)
                ajm = float(l["ajust_materiaux"] or 0)
                adj = float(l["ajustement_pct"] or 0)
                st_mat = qte * prix * (1 + ajm / 100)
                st_mo = heures * taux * (1 + ajmo / 100)
                st_st = st_montant * (1 + ajst / 100)
                # Sous-total ligne = somme des 3 sous-totaux par section.
                st = st_mat + st_mo + st_st
                snap_mat += st_mat
                snap_mo += st_mo
                snap_st += st_st
                # tot_real = total brut. ajustement_pct historique reste appliqué
                # pour rétro-compat des projets antérieurs à la refonte ; sera
                # à 0 sur les nouvelles saisies.
                tot_real = st * (1 + adj / 100)
                gkey = _group_key_for(_section_prefix_n(l["section"]))
                factor = group_factors.get(gkey, 1) if gkey is not None else 1
                tot = tot_real * factor  # gonflé si admin distribué
                # Total de ligne arrondi (mode arrondi) : accumulé dans les
                # sous-totaux ET affiché, pour que Σ lignes affichées = sous-total.
                tot_disp = R(tot)
                # RAPPORT — masquer une ligne d'ITEM dont le TOTAL affiché est 0
                # (ex. qté=1 sans prix unitaire → total 0 légitime, mais inutile au
                # rapport). Uniquement quand les prix sont affichés (un rapport sans
                # prix est structurel, on ne masque rien). N'affecte NI l'éditeur
                # (GET /lignes, autre chemin) NI les lignes de structure (titres /
                # groupes / sous-totaux), générées hors de cette boucle.
                if avec_prix and tot_disp == 0:
                    continue
                sec_total += tot_disp
                if gkey is not None:
                    group_subtotals[gkey] += R(tot_real)
                    group_sub_mat[gkey] += st_mat
                    group_sub_mo[gkey] += st_mo
                    group_sub_st[gkey] += st_st
                else:
                    non_grouped_total += R(tot_real)
                table_data.append([
                    cell_for(c, l, qte, prix, adj, R(st), tot_disp,
                             heures, taux, st_mo, st_mat, st_st, st_montant,
                             ajm, ajmo, ajst)
                    for c in selected
                ])
                section_has_visible_lines = True
            if show_totals_row and section_has_visible_lines:
                table_data.append(make_summary_row(f"Sous-total {sec}", sec_total))
                subtotal_rows.append(len(table_data) - 1)

        # body_start_row = 1re rangée de DONNÉES (après les rangées d'en-tête).
        body_start_row = header_offset

        table_style_cmds = [
            ("FONTSIZE", (0, 0), (-1, -1), body_fontsize),
            ("ALIGN", (2, body_start_row), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, body_start_row), (-1, -1),
             [colors.white, colors.HexColor("#f1f5f9")]),
        ]
        # Rangée des TITRES de colonnes (si présente) : fond bleu Adision + texte
        # blanc gras. Index dynamique (peut être 0 ou 1 selon la rangée groupe).
        if titres_row_idx is not None:
            table_style_cmds.append(("BACKGROUND", (0, titres_row_idx), (-1, titres_row_idx), colors.HexColor("#1e3a8a")))
            table_style_cmds.append(("TEXTCOLOR", (0, titres_row_idx), (-1, titres_row_idx), colors.white))
            table_style_cmds.append(("FONTNAME", (0, titres_row_idx), (-1, titres_row_idx), "Helvetica-Bold"))
        # Rangée de REGROUPEMENT (si présente) : SPAN + fond couleur de section,
        # gras, centré. Index dynamique (toujours 0 quand présente).
        if group_row_idx is not None:
            table_style_cmds.append(("FONTNAME", (0, group_row_idx), (-1, group_row_idx), "Helvetica-Bold"))
            table_style_cmds.append(("ALIGN", (0, group_row_idx), (-1, group_row_idx), "CENTER"))
            for start, end, grp in section_spans:
                table_style_cmds.append(("SPAN", (start, group_row_idx), (end, group_row_idx)))
                table_style_cmds.append(("BACKGROUND", (start, group_row_idx), (end, group_row_idx),
                                         SECTION_COLORS[grp]))
        for r in subtotal_rows:
            table_style_cmds.append(("FONTNAME", (0, r), (-1, r), "Helvetica-Bold"))
            table_style_cmds.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#dbeafe")))

        table = Table(table_data, colWidths=col_widths, repeatRows=header_offset)
        table.setStyle(TableStyle(table_style_cmds))
        story.append(table)

        # Valeurs financières du snapshot — init à 0 (cas avec_prix=False) ;
        # le bloc ci-dessous les renseigne quand avec_prix. group_ap = A&P par
        # groupe (mêmes nombres que le bloc totaux affiché).
        real_sub_avant_taxes = 0.0
        tps_amount = 0.0
        tvq_amount = 0.0
        total_general = 0.0
        group_ap = {}

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
                ap = R(_group_admin_profit(projet, pct_field, sub,
                                           group_sub_mat[key], group_sub_mo[key], group_sub_st[key]))
                group_ap[key] = ap
                real_sub_avant_taxes += sub + ap

            # 2. Affichage des lignes par regroupement (filtré par selected_st / selected_ap)
            for key, label, pct_field, _ in BUDGET_GROUPS_PDF:
                sub = group_subtotals[key]
                if sub <= 0:
                    continue
                if key not in selected_st:
                    continue
                pct = float(projet.get(pct_field) or 0)
                ap = R(_group_admin_profit(projet, pct_field, sub,
                                           group_sub_mat[key], group_sub_mo[key], group_sub_st[key]))
                decomposed = any(projet.get(f"{pct_field}_{c}") is not None for c in ("mat", "mo", "st"))
                ap_label = ("Administration et profit (décomposé)" if decomposed
                            else f"Administration et profit {pct:g}%")
                if key in selected_ap:
                    # Mode classique : sous-total et admin & profit affichés séparément
                    totals_rows.append([f"Sous-total {label}", f"{_frca_num(sub)} $"])
                    totals_kinds.append("subtotal")
                    totals_rows.append([ap_label, f"{_frca_num(ap)} $"])
                    totals_kinds.append("admin")
                else:
                    # Mode distribution : sous-total gonflé (= sub + ap), pas de ligne admin
                    totals_rows.append([f"Sous-total {label}", f"{_frca_num(sub + ap)} $"])
                    totals_kinds.append("subtotal")

            # 3. Sous-total avant taxes (visuel uniquement)
            if avec_sous_total_avant_taxes:
                totals_rows.append(["Sous-total avant taxes", f"{_frca_num(real_sub_avant_taxes)} $"])
                totals_kinds.append("subtotal_taxes")

            # 4. TPS / TVQ — affectent réellement le TOTAL GÉNÉRAL. Chaque taxe
            #    arrondie au dollar séparément (mode arrondi).
            tps_amount = R(real_sub_avant_taxes * TPS_RATE)
            tvq_amount = R(real_sub_avant_taxes * TVQ_RATE)
            if avec_tps:
                totals_rows.append([f"TPS {TPS_RATE * 100:g}%", f"{_frca_num(tps_amount)} $"])
                totals_kinds.append("tax")
            if avec_tvq:
                totals_rows.append([f"TVQ {TVQ_RATE * 100:g}%", f"{_frca_num(tvq_amount)} $"])
                totals_kinds.append("tax")

            # 5. TOTAL GÉNÉRAL = sous-total avant taxes + TPS (si coché) + TVQ (si coché)
            total_general = real_sub_avant_taxes
            if avec_tps:
                total_general += tps_amount
            if avec_tvq:
                total_general += tvq_amount
            totals_rows.append(["TOTAL GÉNÉRAL", f"{_frca_num(total_general)} $"])
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

        # Récap des HEURES de main-d'œuvre (production vs contremaître). HORS du
        # gate `show_totals_row` : les heures ne sont PAS des montants, on les
        # affiche même en mode « sans prix ». Option `avec_heures` (gabarit
        # d'export) : contrôle UNIQUEMENT l'AFFICHAGE PDF ; les heures restent
        # TOUJOURS dans snapshot_data.heures (flywheel Ad ANA) — cf. plus bas.
        if avec_heures and (mo_h + contremaitre_h) > 0:
            def _h(v):
                # fr-CA : espace milliers (régulier), virgule décimale, pas de
                # décimale inutile si entier.
                s = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"
                return s.replace(",", " ").replace(".", ",")
            heures_para = (
                "Heures — Main-d'œuvre : <b>" + _h(mo_h) + " h</b>"
                " · Contremaître : <b>" + _h(contremaitre_h) + " h</b>"
                " · Total : <b>" + _h(mo_h + contremaitre_h) + " h</b>"
            )
            story.append(Spacer(1, 10))
            story.append(Paragraph(heures_para, ss["Normal"]))

        # Footer « Propulsé par Ad FLO » + logo, IDENTIQUE au devis (source
        # unique draw_adflo_footer). Remplace l'ancien texte « Propulsé par
        # Adision ». Rapport toujours en couleur -> nb=False.
        def _draw_pdf_footer(canvas, _doc):
            draw_adflo_footer(canvas, _doc, nb=False)

        doc.build(story, onFirstPage=_draw_pdf_footer, onLaterPages=_draw_pdf_footer)
        buf.seek(0)

        # ── Snapshot JSON (MÊME passe que le PDF) — chaque poste vient des
        #    variables qui alimentent le rapport, donc identique au rendu. ──
        groups_detail = []
        ap_mode = "simple"
        for key, label, pct_field, _ in BUDGET_GROUPS_PDF:
            sub = group_subtotals.get(key, 0.0)
            if sub <= 0:
                continue
            decomposed = any(projet.get(f"{pct_field}_{c}") is not None for c in ("mat", "mo", "st"))
            pct_disc = float(projet.get(pct_field) or 0)
            if decomposed:
                ap_mode = "categorie"
            elif pct_disc and ap_mode != "categorie":
                ap_mode = "discipline"
            gd = {
                "key": key,
                "label": label,
                "sous_total": round(sub, 2),
                "admin_profit": round(group_ap.get(key, 0.0), 2),
                "pct_discipline": pct_disc,
                "decomposed": decomposed,
            }
            if decomposed:
                gd["pct_categorie"] = {
                    c: (float(projet[f"{pct_field}_{c}"]) if projet.get(f"{pct_field}_{c}") is not None else None)
                    for c in ("mat", "mo", "st")
                }
            groups_detail.append(gd)

        snapshot = {
            "schema_version": 1,
            "report_type": "calcul_quantitatif",
            "project": {
                "central_project_id": projet.get("ad_hub_project_id"),
                "nom": projet.get("nom"),
            },
            "revision": {
                "no": projet.get("revision_no", 0),
                "label": projet.get("revision_label") or "Originale",
            },
            "options": {"arrondi_dollar": bool(projet.get("arrondi_dollar"))},
            "totaux": {
                "sous_total_mat": round(R(snap_mat), 2),
                "sous_total_mo": round(R(snap_mo), 2),
                "sous_total_st": round(R(snap_st), 2),
                "montant_avant_taxes": round(real_sub_avant_taxes, 2),
                "tps": round(tps_amount, 2),
                "tvq": round(tvq_amount, 2),
                "montant_apres_taxes": round(total_general, 2),
            },
            "heures": {
                "mo": round(mo_h, 2),
                "contremaitre": round(contremaitre_h, 2),
                "total": round(mo_h + contremaitre_h, 2),
            },
            "admin_profit": {"mode": ap_mode, "details": groups_detail},
            "regroupements_csi": [
                {"key": g["key"], "label": g["label"],
                 "sous_total": g["sous_total"], "admin_profit": g["admin_profit"]}
                for g in groups_detail
            ],
            "devis": {"destinataire_contact": None, "travaux_inclus": [], "travaux_non_inclus": []},
        }
        return buf, snapshot

    # ══════════════════════════════════════════════════════════
    # ESPACE RAPPORTS — émission vers le HUB + mécanique de révision
    # ══════════════════════════════════════════════════════════
    # Champs projet qui affectent le SNAPSHOT/MONTANT -> une mutation de ces
    # champs grave la révision suivante (si déjà émise). Le cosmétique / les
    # métadonnées (nom, client, notes, largeur de colonnes…) n'y sont PAS.
    SNAPSHOT_AFFECTING_FIELDS = (
        {f"pct_admin_{g}" for g in ("conditions", "architecture", "mecanique", "excavation")}
        | {f"pct_admin_{g}_{c}"
           for g in ("conditions", "architecture", "mecanique", "excavation")
           for c in ("mat", "mo", "st")}
        | {"arrondi_dollar", "mobilisation", "surface_plancher",
           "hauteur_cloisons", "longueur_cloisons"}
    )

    def _mark_budget_dirty_if_emitted(projet_id):
        """Marque le budget « modifié depuis la dernière émission »
        (budget_modifie_depuis_emission=TRUE) SI la révision courante a déjà été
        émise. NE bumpe PLUS automatiquement (décision option A) : le bump devient
        une CONSÉQUENCE du choix user « Nouvelle révision » à l'émission. No-op si
        pas encore émis (mutations libres). Critère inchangé : appelé uniquement
        sur les mutations qui changent snapshot_data/montant, jamais le
        cosmétique/UI. Le flag sert à savoir si la QUESTION doit se poser."""
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE ad_budget.projets
                   SET budget_modifie_depuis_emission = TRUE,
                       updated_at = NOW()
                 WHERE id = %s AND emitted_at_current_revision = TRUE
                """,
                (projet_id,),
            )
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()
        finally:
            cur.close()
            conn.close()

    @router.post("/projets/{projet_id}/emit-report")
    def emit_report_to_hub(
        projet_id: int,
        user=Depends(jwt_user),
        authorization: Optional[str] = Header(None),
        # Config PDF (modale) — pilote UNIQUEMENT l'artefact PDF (une VUE).
        actifs_seulement: bool = True,
        avec_prix: bool = True,
        sections: str = "",
        colonnes: str = "",
        sous_totaux: Optional[str] = None,
        admin_profits: Optional[str] = None,
        avec_sous_total_avant_taxes: bool = True,
        avec_tps: bool = True,
        avec_tvq: bool = True,
        avec_heures: bool = True,
        orientation: str = "portrait",
        mobilisation: float = 0,
        surface_plancher: float = 0,
        hauteur_cloisons: float = 0,
        longueur_cloisons: float = 0,
        gabarit_id: Optional[int] = None,
        gabarit_nom: Optional[str] = None,
        # Choix user (option A) quand le budget a changé depuis la dernière
        # émission : 'new' (nouvelle révision) | 'correction' (remplace la
        # courante). None = pas encore choisi -> si une question est requise,
        # l'endpoint répond {choice_required:true} sans émettre.
        revision_choice: Optional[str] = None,
    ):
        """Émet le RAPPORT DE CALCUL (quantitatif) vers l'Espace Rapports HUB.
        DISSOCIATION (option 1) : PDF = vue modale, snapshot = budget COMPLET.
        RÉVISION (option A) : si la révision courante a déjà été émise ET que le
        budget a changé depuis (budget_modifie_depuis_emission), on demande à
        l'user (choice_required) ; 'new' bumpe la révision avant d'émettre,
        'correction' reste sur la courante (remplace l'artefact). Sinon émet
        directement. Le flag dirty est remis à FALSE à l'émission."""
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
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
                                detail="Projet non lié à un projet HUB (ad_hub_project_id manquant)")

        # ── Mécanique de révision (option A) — choix user si budget changé ──
        rev_no = projet.get("revision_no", 0)
        rev_label = projet.get("revision_label") or "Originale"
        needs_choice = bool(projet.get("emitted_at_current_revision")
                            and projet.get("budget_modifie_depuis_emission"))
        if needs_choice and revision_choice not in ("new", "correction"):
            # On NE génère PAS le PDF : on demande d'abord à l'user.
            return {
                "choice_required": True,
                "current_label": rev_label,
                "next_label": f"R.{rev_no + 1}",
            }
        if needs_choice and revision_choice == "new":
            # Bump ordonné par l'user : nouvelle révision, l'ancienne version
            # reste conservée intacte côté HUB (UNIQUE par revision_no).
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
        # 'correction' (ou émission directe) : on reste sur rev_no/rev_label courants.

        # Token pour les logos (client/org via hub_service) ET l'émission HUB.
        jwt_token = _extract_bearer(authorization, None)
        # 1. PDF = config de la modale (artefact / vue). On ignore son snapshot.
        buf, _filtered_snap = _build_projet_report(
            projet_id, actifs_seulement, avec_prix, sections, colonnes,
            sous_totaux, admin_profits, avec_sous_total_avant_taxes, avec_tps,
            avec_tvq, orientation, mobilisation, surface_plancher,
            hauteur_cloisons, longueur_cloisons, avec_heures=avec_heures,
            jwt_token=jwt_token,
        )
        pdf_bytes = buf.getvalue()
        # 2. Snapshot = budget COMPLET (vérité Ad ANA) : aucune section/colonne
        #    filtrée, taxes complètes (avec_tps/avec_tvq=True), lignes actives.
        _full_buf, snapshot = _build_projet_report(
            projet_id, True, True, "", "", None, None, True, True, True,
            "portrait", 0, 0, 0, 0,
            jwt_token=jwt_token,
        )

        fields = {
            "report_type": "calcul_quantitatif",
            "revision_no": rev_no,
            "revision_label": rev_label,
            "montant_avant_taxes": snapshot["totaux"]["montant_avant_taxes"],
            "montant_apres_taxes": snapshot["totaux"]["montant_apres_taxes"],
            # Montant TEL QU'AFFICHÉ dans le PDF filtré (TOTAL GÉNÉRAL de la vue
            # modale, reflète sections/taxes décochées) -> détection rapport
            # partiel côté HUB. Le snapshot ci-dessus reste le budget complet.
            "montant_affiche_pdf": _filtered_snap["totaux"]["montant_apres_taxes"],
            "gabarit_id": gabarit_id,
            "gabarit_nom": gabarit_nom,
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
            cur.execute(
                "UPDATE ad_budget.projets SET emitted_at_current_revision = TRUE, "
                "budget_modifie_depuis_emission = FALSE, updated_at = NOW() "
                "WHERE id = %s", (projet_id,))
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return {
            "emitted": True,
            "report_type": "calcul_quantitatif",
            "revision_no": rev_no,
            "revision_label": rev_label,
            "hub": result,
        }

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
    def list_projet_snapshots(
        projet_id: int, include_lines: bool = False, user=Depends(jwt_user),
    ):
        """Liste tous les snapshots d'un projet, du plus récent au plus ancien.

        budget_lines_jsonb est OMIS par défaut (champ lourd). Opt-in via
        ?include_lines=true (utile pour Ad ANA recalcul).
        """
        # PHASE 3A — authentification + autorisation (lecture).
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
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
    def get_latest_snapshot(
        projet_id: int, include_lines: bool = False, user=Depends(jwt_user),
    ):
        """Retourne le snapshot le plus récent (is_latest = TRUE) du projet.
        404 si aucun snapshot n'existe.
        """
        # PHASE 3A — authentification + autorisation (lecture).
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
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
    def get_budget_lignes(projet_id: int, user=Depends(jwt_user)):
        # PHASE 3A — authentification + autorisation (lecture) sur le projet parent.
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
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
    def create_budget_ligne(projet_id: int, data: dict, user=Depends(jwt_user)):
        """
        Ajoute un item au budget d'un projet.
        Si source_item_id est fourni, copie les données de la base principale.
        Sinon, utilise les données fournies directement.
        """
        # PHASE 3A — authentification + autorisation (écriture) sur le projet parent.
        _load_and_authorize_projet(get_conn, projet_id, user, "write")
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

        # D5 — auto-fill du taux horaire par défaut. On ne l'applique QUE si
        # l'appelant n'a pas fourni de taux explicite (ne jamais écraser une
        # valeur saisie). _resolve_taux_default -> None (division non mappée
        # ou section vide) => 0.
        taux_horaire = data.get("taux_horaire")
        if taux_horaire is None:
            resolved = _resolve_taux_default(section, conn)
            taux_horaire = resolved if resolved is not None else 0

        cur.execute("""
            INSERT INTO ad_budget.budget_lignes
            (projet_id, source_item_id, section, description, unite, prix_unitaire, qte, ajustement_pct, note, actif,
             item_id_ad_mat, ad_hub_pending_id, taux_horaire)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            data.get("actif", True),
            data.get("item_id_ad_mat"),
            data.get("ad_hub_pending_id"),
            taux_horaire,
        ))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        _mark_budget_dirty_if_emitted(projet_id)  # ajout de ligne -> snapshot
        return {"status": "created", "ligne": row}

    # ─── Pont Ad TYP → Ad BUD (étape 1) — lecture cross-service + snapshot ───
    def _typ_err(e):
        code = e.status_code if e.status_code in (401, 403, 404) else 502
        return HTTPException(status_code=code, detail=f"Ad TYP : {e.detail}")

    @router.get("/typ/catalogue")
    def proxy_typ_catalogue(q: Optional[str] = None, division: Optional[str] = None,
                            section: Optional[str] = None, limit: int = 50,
                            authorization: Optional[str] = Header(None),
                            user=Depends(jwt_user)):
        """Proxy lecture seule du catalogue Ad TYP (cross-service) pour
        l'autocomplete « + depuis Ad TYP ». Ad BUD LIT Ad TYP ; n'écrit jamais.

        DÉ-DUPLICATION CLIENT-FIRST : adision_dig renvoie l'UNION master + overlay
        client de l'org ; à code égal on ne garde QUE l'overlay client (la valeur
        EFFECTIVE de l'org) — cohérent avec la résolution client-first de
        /typ/catalogue/{code} (snapshot/pioche/⟳). L'autocomplete affiche donc UNE
        seule entrée par code, plus le doublon « master 0,00 + client 15,00 ».
        On déduplique ICI (proxy EXCLUSIF d'Ad BUD) et NON dans adision_dig : la vue
        de curation « Catalogue Adision » (ad-typ) et Ad ADM interrogent adision_dig
        en direct et doivent garder l'union master complète."""
        jwt_token = _extract_bearer(authorization, None)
        try:
            data = typ_service.search_catalogue(jwt_token, q=q, division=division, section=section, limit=limit)
        except typ_service.TypServiceError as e:
            raise _typ_err(e)
        items = data.get("items") or []
        by_code: dict = {}
        for it in items:                       # master listé d'abord, client ensuite
            code = it.get("code")
            prev = by_code.get(code)
            # client écrase master ; à source identique on garde le premier (stable)
            if prev is None or (prev.get("source") == "master" and it.get("source") == "client"):
                by_code[code] = it
        deduped = sorted(by_code.values(), key=lambda x: (x.get("code") or ""))
        total = data.get("total")
        if isinstance(total, int):
            total = max(total - (len(items) - len(deduped)), len(deduped))
        else:
            total = len(deduped)
        return {"total": total, "items": deduped}

    @router.post("/projets/{projet_id}/lignes/from-typ")
    def create_budget_ligne_from_typ(projet_id: int, data: dict,
                                     authorization: Optional[str] = Header(None),
                                     user=Depends(jwt_user)):
        """Pioche une ligne Ad TYP (par code) → crée une budget_ligne SNAPSHOT
        (aplatie MAT/MO/ST) + lien source_typ_code pour re-tarif ultérieure."""
        projet = _load_and_authorize_projet(get_conn, projet_id, user, "write")
        proj_org = projet.get("organization_id") if projet else None   # org du PROJET (isolation cross-org)
        code = (data.get("code") or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="code Ad TYP requis")
        # Défaut Ad BUD : une ligne neuve naît à QTÉ 0 (MO/ST = 0 ; prix_unitaire
        # par unité préservé → scaling à la saisie de la qté).
        qte = data.get("qte", 0)
        jwt_token = _extract_bearer(authorization, None)
        try:
            typ = typ_service.get_ligne(jwt_token, code, org=proj_org)
        except typ_service.TypServiceError as e:
            raise _typ_err(e)
        m = _map_typ_to_budget_cols(typ, qte)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute("""
                INSERT INTO ad_budget.budget_lignes
                (projet_id, section, description, unite, prix_unitaire, qte,
                 heures, taux_horaire, sous_traitant_montant,
                 ajust_materiaux, ajust_main_oeuvre, ajust_sous_traitant, actif,
                 source_typ_code, source_typ_snapshot_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,0,0,TRUE,%s,NOW())
                RETURNING *
            """, (projet_id, m["section"], m["description"], m["unite"], m["prix_unitaire"], qte,
                  m["heures"], m["taux_horaire"], m["sous_traitant_montant"], code))
            row = cur.fetchone()
            conn.commit()
        finally:
            cur.close(); conn.close()
        _mark_budget_dirty_if_emitted(projet_id)  # ajout depuis Ad TYP -> snapshot
        return {"status": "created", "ligne": row, "mo_flat": m["mo_flat"]}

    @router.post("/projets/{projet_id}/lignes/{ligne_id}/refresh-typ")
    def refresh_budget_ligne_from_typ(projet_id: int, ligne_id: int,
                                      authorization: Optional[str] = Header(None),
                                      user=Depends(jwt_user)):
        """Re-tarif d'une ligne liée Ad TYP : re-lit Ad TYP + re-mappe (snapshot
        rafraîchi). AUTORISÉ UNIQUEMENT si le projet est au statut 'brouillon'
        (« En cours ») — gelé sinon."""
        _load_and_authorize_projet(get_conn, projet_id, user, "write")
        jwt_token = _extract_bearer(authorization, None)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute("SELECT statut, organization_id FROM ad_budget.projets WHERE id = %s", (projet_id,))
            prow = cur.fetchone()
            statut = prow["statut"] if prow else None
            proj_org = prow["organization_id"] if prow else None   # org du PROJET (isolation cross-org)
            if statut != "brouillon":
                raise HTTPException(status_code=409,
                                    detail="Re-tarif Ad TYP bloquée : le budget n'est pas « En cours » (gelé).")
            cur.execute("SELECT id, source_typ_code, qte FROM ad_budget.budget_lignes "
                        "WHERE id = %s AND projet_id = %s", (ligne_id, projet_id))
            ligne = cur.fetchone()
            if not ligne:
                raise HTTPException(status_code=404, detail="Ligne introuvable")
            if not ligne["source_typ_code"]:
                raise HTTPException(status_code=400, detail="Ligne non liée à Ad TYP (pas de re-tarif)")
            try:
                typ = typ_service.get_ligne(jwt_token, ligne["source_typ_code"], org=proj_org)
            except typ_service.TypServiceError as e:
                raise _typ_err(e)
            m = _map_typ_to_budget_cols(typ, ligne["qte"])   # re-mappe à la qté courante
            # ⟳ EXPLICITE = acceptation user : réaligne le coût sur Ad TYP et
            # remet l'override à FALSE (ignore volontairement le flag).
            cur.execute("""
                UPDATE ad_budget.budget_lignes SET
                  section=%s, description=%s, unite=%s, prix_unitaire=%s,
                  heures=%s, taux_horaire=%s, sous_traitant_montant=%s,
                  source_typ_snapshot_at=NOW(), prix_unitaire_override=FALSE,
                  updated_at=NOW()
                WHERE id=%s AND projet_id=%s RETURNING *
            """, (m["section"], m["description"], m["unite"], m["prix_unitaire"],
                  m["heures"], m["taux_horaire"], m["sous_traitant_montant"], ligne_id, projet_id))
            row = cur.fetchone()
            conn.commit()
        except HTTPException:
            conn.rollback(); raise
        finally:
            cur.close(); conn.close()
        _mark_budget_dirty_if_emitted(projet_id)  # re-tarif Ad TYP -> snapshot
        return {"status": "refreshed", "ligne": row, "mo_flat": m["mo_flat"]}

    # ─── Onglet « MAJ » : détection + application groupée des MAJ Ad TYP ───
    # Remplace le ⟳ ligne-par-ligne par une vue d'ensemble. V1 = Ad TYP seul
    # (TIM remonte via TYP ; MAT = phase 2, non câblé). Forme « par module » pour
    # accueillir d'autres sources plus tard sans refonte. Helpers purs + colonnes
    # = niveau module (_maj_*, _MAJ_LINE_COLS) pour testabilité.

    @router.get("/projets/{projet_id}/maj-typ")
    def detect_maj_typ(projet_id: int,
                       authorization: Optional[str] = Header(None),
                       user=Depends(jwt_user)):
        """Détecte EN LOT les lignes divergentes de leur source, PAR MODULE : Ad TYP
        (via typ_service) ET Ad MAT (via mat_service), 1 appel cross-service chacun
        (anti-N+1). Lecture seule. INACTIF hors 'brouillon' → modules=[] (pas d'onglet,
        cohérent avec la garde 409 du ⟳). Une ligne est soit TYP soit MAT (exclusivité)
        → aucun double comptage."""
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        jwt_token = _extract_bearer(authorization, None)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute("SELECT statut, organization_id FROM ad_budget.projets WHERE id = %s", (projet_id,))
            prow = cur.fetchone()
            statut = prow["statut"] if prow else None
            # Org du PROJET (isolation cross-org Loi 25) : la résolution catalogue DOIT
            # se faire sur l'org du projet, pas du JWT (un super_admin ne contamine pas).
            proj_org = prow["organization_id"] if prow else None
            if statut != "brouillon":
                return {"statut": statut, "actif": False, "modules": [], "total": 0}
            cur.execute(f"SELECT {_MAJ_LINE_COLS} FROM ad_budget.budget_lignes "
                        "WHERE projet_id = %s AND source_typ_code IS NOT NULL "
                        "AND actif IS NOT FALSE", (projet_id,))
            typ_lignes = cur.fetchall()
            cur.execute(f"SELECT {_MAJ_MAT_LINE_COLS} FROM ad_budget.budget_lignes "
                        "WHERE projet_id = %s AND item_id_ad_mat IS NOT NULL "
                        "AND actif IS NOT FALSE", (projet_id,))
            mat_lignes = cur.fetchall()
        finally:
            cur.close(); conn.close()
        modules = []
        # ── Module Ad TYP ──
        if typ_lignes:
            codes = sorted({l["source_typ_code"] for l in typ_lignes if l["source_typ_code"]})
            try:
                srcmap = typ_service.get_batch(jwt_token, codes, org=proj_org)
            except typ_service.TypServiceError as e:
                raise _typ_err(e)
            tu = []
            for l in typ_lignes:
                src = srcmap.get(l["source_typ_code"])
                if not src:
                    continue  # source introuvable → pas « divergente »
                m = _map_typ_to_budget_cols(src, l["qte"])
                divs = _maj_compute_divergences(l, m)
                if divs:
                    tu.append({"ligne_id": l["id"], "code": l["source_typ_code"],
                               "description": l["description"], "qte": float(l["qte"] or 0),
                               "divergences": divs})
            tu.sort(key=lambda u: u["code"])
            if tu:
                modules.append({"module": "typ", "label": "Ad TYP", "updates": tu})
        # ── Module Ad MAT (phase 2) — résolution par (id, scope) ──
        if mat_lignes:
            refs = sorted({(l["item_id_ad_mat"], l["item_ad_mat_scope"] or "master")
                           for l in mat_lignes})
            try:
                matmap = mat_service.get_batch(jwt_token, refs, org=proj_org)
            except mat_service.MatServiceError as e:
                raise HTTPException(status_code=502 if e.status_code is None else e.status_code,
                                    detail=f"Ad MAT indisponible : {e.detail}")
            mu = []
            for l in mat_lignes:
                src = matmap.get((l["item_id_ad_mat"], l["item_ad_mat_scope"] or "master"))
                if not src:
                    continue
                divs = _maj_compute_mat_divergences(l, src)
                if divs:
                    mu.append({"ligne_id": l["id"], "code": l["section"] or "",
                               "description": l["description"], "qte": float(l["qte"] or 0),
                               "divergences": divs})
            mu.sort(key=lambda u: (u["code"] or "", u["ligne_id"]))
            if mu:
                modules.append({"module": "mat", "label": "Ad MAT", "updates": mu})
        total = sum(len(m["updates"]) for m in modules)
        return {"statut": statut, "actif": True, "modules": modules, "total": total}

    @router.post("/projets/{projet_id}/maj-typ/apply")
    def apply_maj_typ(projet_id: int, data: dict,
                      authorization: Optional[str] = Header(None),
                      user=Depends(jwt_user)):
        """Applique les MAJ Ad TYP sur les lignes COCHÉES (ligne_ids) : refresh
        depuis la source (même mapping que le ⟳) MAIS override-aware — ne touche
        PAS un champ en override volontaire (prix_unitaire_override → coût préservé ;
        heures_manuelles réel → heures/taux préservés). N'altère pas les flags
        d'override (≠ ⟳ qui les remet à FALSE). Bloqué 409 hors 'brouillon'."""
        _load_and_authorize_projet(get_conn, projet_id, user, "write")
        jwt_token = _extract_bearer(authorization, None)
        ids = data.get("ligne_ids") if isinstance(data, dict) else None
        if not isinstance(ids, list) or not ids:
            raise HTTPException(status_code=400, detail="ligne_ids (liste non vide) requis")
        try:
            ids = [int(x) for x in ids]
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="ligne_ids doivent être des entiers")
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        updated = []
        try:
            cur.execute("SELECT statut, organization_id FROM ad_budget.projets WHERE id = %s", (projet_id,))
            prow = cur.fetchone()
            proj_org = prow["organization_id"] if prow else None   # org du PROJET (isolation cross-org)
            if (prow["statut"] if prow else None) != "brouillon":
                raise HTTPException(status_code=409,
                                    detail="MAJ Ad TYP bloquée : le budget n'est pas « En cours » (gelé).")
            cur.execute(f"SELECT {_MAJ_LINE_COLS} FROM ad_budget.budget_lignes "
                        "WHERE projet_id = %s AND id = ANY(%s) "
                        "AND source_typ_code IS NOT NULL AND actif IS NOT FALSE",
                        (projet_id, ids))
            lignes = cur.fetchall()
            if not lignes:
                raise HTTPException(status_code=404, detail="Aucune ligne Ad TYP à mettre à jour")
            codes = sorted({l["source_typ_code"] for l in lignes if l["source_typ_code"]})
            try:
                srcmap = typ_service.get_batch(jwt_token, codes, org=proj_org)
            except typ_service.TypServiceError as e:
                raise _typ_err(e)
            for l in lignes:
                src = srcmap.get(l["source_typ_code"])
                if not src:
                    continue
                m = _map_typ_to_budget_cols(src, l["qte"])
                ovr_p = bool(l.get("prix_unitaire_override"))
                new_prix = l["prix_unitaire"] if ovr_p else m["prix_unitaire"]
                # V1 : on NE touche PAS heures/taux (MO). Le MO du projet (défaut col 17
                # ACQ ou saisie) est PRÉSERVÉ — cohérent avec l'exclusion MO de la
                # détection (taux à double source). Seuls desc/unité/coût(MAT)/ST descendent.
                cur.execute("""
                    UPDATE ad_budget.budget_lignes SET
                      section=%s, description=%s, unite=%s, prix_unitaire=%s,
                      sous_traitant_montant=%s, source_typ_snapshot_at=NOW(), updated_at=NOW()
                    WHERE id=%s AND projet_id=%s RETURNING *
                """, (m["section"], m["description"], m["unite"], new_prix,
                      m["sous_traitant_montant"], l["id"], projet_id))
                updated.append(cur.fetchone())
            conn.commit()
        except HTTPException:
            conn.rollback(); raise
        finally:
            cur.close(); conn.close()
        _mark_budget_dirty_if_emitted(projet_id)
        return {"status": "applied", "count": len(updated), "lignes": updated}

    @router.post("/projets/{projet_id}/maj-mat/apply")
    def apply_maj_mat(projet_id: int, data: dict,
                      authorization: Optional[str] = Header(None),
                      user=Depends(jwt_user)):
        """Applique les MAJ Ad MAT sur les lignes COCHÉES (ligne_ids) : descend le PRIX
        COURANT de l'item (+ description/unité) et RÉÉCRIT le snapshot dédié
        (source_mat_prix_snapshot + snapshot_at) → la ligne n'est plus divergente, et
        une future édition manuelle ne re-déclenchera pas le MAJ (le snapshot reste la
        référence catalogue). Le flag prix_unitaire_override n'est PAS un signal MAT
        (snapshot dédié) → non touché. Bloqué 409 hors 'brouillon'. Indépendant d'Ad TYP."""
        _load_and_authorize_projet(get_conn, projet_id, user, "write")
        jwt_token = _extract_bearer(authorization, None)
        ids = data.get("ligne_ids") if isinstance(data, dict) else None
        if not isinstance(ids, list) or not ids:
            raise HTTPException(status_code=400, detail="ligne_ids (liste non vide) requis")
        try:
            ids = [int(x) for x in ids]
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="ligne_ids doivent être des entiers")
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        updated = []
        try:
            cur.execute("SELECT statut, organization_id FROM ad_budget.projets WHERE id = %s", (projet_id,))
            prow = cur.fetchone()
            proj_org = prow["organization_id"] if prow else None   # org du PROJET (isolation cross-org)
            if (prow["statut"] if prow else None) != "brouillon":
                raise HTTPException(status_code=409,
                                    detail="MAJ Ad MAT bloquée : le budget n'est pas « En cours » (gelé).")
            cur.execute(f"SELECT {_MAJ_MAT_LINE_COLS} FROM ad_budget.budget_lignes "
                        "WHERE projet_id = %s AND id = ANY(%s) "
                        "AND item_id_ad_mat IS NOT NULL AND actif IS NOT FALSE",
                        (projet_id, ids))
            lignes = cur.fetchall()
            if not lignes:
                raise HTTPException(status_code=404, detail="Aucune ligne Ad MAT à mettre à jour")
            refs = sorted({(l["item_id_ad_mat"], l["item_ad_mat_scope"] or "master") for l in lignes})
            try:
                matmap = mat_service.get_batch(jwt_token, refs, org=proj_org)
            except mat_service.MatServiceError as e:
                raise HTTPException(status_code=502 if e.status_code is None else e.status_code,
                                    detail=f"Ad MAT indisponible : {e.detail}")
            for l in lignes:
                src = matmap.get((l["item_id_ad_mat"], l["item_ad_mat_scope"] or "master"))
                if not src:
                    continue
                cur_prix = src.get("prix_courant")
                new_desc = src.get("description") if src.get("description") is not None else l["description"]
                new_unite = src.get("unite") if src.get("unite") is not None else l["unite"]
                if cur_prix is not None:
                    # Descend le prix courant + réécrit le snapshot (= nouvelle référence).
                    cur.execute("""
                        UPDATE ad_budget.budget_lignes SET
                          description=%s, unite=%s, prix_unitaire=%s,
                          source_mat_prix_snapshot=%s, source_mat_snapshot_at=NOW(), updated_at=NOW()
                        WHERE id=%s AND projet_id=%s RETURNING *
                    """, (new_desc, new_unite, cur_prix, cur_prix, l["id"], projet_id))
                else:
                    # Prix courant NULL : on n'écrase pas le prix ; desc/unité seulement.
                    cur.execute("""
                        UPDATE ad_budget.budget_lignes SET
                          description=%s, unite=%s, source_mat_snapshot_at=NOW(), updated_at=NOW()
                        WHERE id=%s AND projet_id=%s RETURNING *
                    """, (new_desc, new_unite, l["id"], projet_id))
                updated.append(cur.fetchone())
            conn.commit()
        except HTTPException:
            conn.rollback(); raise
        finally:
            cur.close(); conn.close()
        _mark_budget_dirty_if_emitted(projet_id)
        return {"status": "applied", "count": len(updated), "lignes": updated}

    @router.post("/projets/{projet_id}/lignes/{ligne_id}/apply-typ")
    def apply_typ_to_ligne(projet_id: int, ligne_id: int, data: dict,
                           authorization: Optional[str] = Header(None),
                           user=Depends(jwt_user)):
        """Pioche Ad TYP DANS une ligne EXISTANTE (autocomplete de la cellule
        description) → SNAPSHOT aplati MAT/MO/ST sur cette ligne (UPDATE, pas
        INSERT comme from-typ). Pose source_typ_code (re-tarif ⟳ ultérieur) et
        EFFACE item_id_ad_mat (la ligne devient sourcée Ad TYP, plus Ad MAT).

        qte : la qté du body fait FOI (0 inclus). Sinon on PRÉSERVE la qté déjà
        saisie sur la ligne (> 0) ; sinon 0 (défaut Ad BUD : ligne neuve sans
        quantité — parité avec from-typ). Le mapping MO dépend de la qté (cf.
        _map_typ_to_budget_cols), donc heures est calculée à cette qté et stockée
        avec elle pour rester cohérent.
        """
        projet = _load_and_authorize_projet(get_conn, projet_id, user, "write")
        proj_org = projet.get("organization_id") if projet else None   # org du PROJET (isolation cross-org)
        code = (data.get("code") or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="code Ad TYP requis")
        jwt_token = _extract_bearer(authorization, None)
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                "SELECT id, qte, taux_horaire FROM ad_budget.budget_lignes "
                "WHERE id = %s AND projet_id = %s",
                (ligne_id, projet_id),
            )
            ligne = cur.fetchone()
            if not ligne:
                raise HTTPException(status_code=404, detail="Ligne introuvable")
            # Qté du body : si FOURNIE (0 inclus), elle fait FOI — le front
            # re-snapshot à la qté SAISIE par l'utilisateur (y compris 0 →
            # MO/ST = 0). Le 0 ne doit JAMAIS être ignoré, sinon la saisie « 0 »
            # rebondit à l'ancienne qté du snapshot. Si NON fournie, on préserve
            # une qté existante (> 0) sinon 0 (défaut Ad BUD : ligne neuve sans
            # quantité — plus de défaut implicite à 1).
            raw_qte = data.get("qte")
            if raw_qte not in (None, ""):
                try:
                    qte = max(0.0, float(raw_qte))
                except (TypeError, ValueError):
                    qte = ligne["qte"] if (ligne["qte"] or 0) > 0 else 0
            elif (ligne["qte"] or 0) > 0:
                qte = ligne["qte"]
            else:
                qte = 0
            try:
                typ = typ_service.get_ligne(jwt_token, code, org=proj_org)
            except typ_service.TypServiceError as e:
                raise _typ_err(e)
            m = _map_typ_to_budget_cols(typ, qte)
            # auto=True : re-snapshot AUTOMATIQUE déclenché par un changement de
            # qté (pas un clic d'acceptation). Dans ce cas, on NE re-pull PAS le
            # MAT (prix_unitaire est PAR UNITÉ et scale déjà via getRow) : on
            # recalcule SEULEMENT la part qté-dépendante (heures MO + montant ST,
            # stockés en total). Préserve prix_unitaire + override (Fix A+B :
            # jamais écraser une saisie manuelle sans acceptation explicite).
            auto = bool(data.get("auto"))
            # TAUX MO préservé sur resync AUTO (qté) : on ne le recalcule JAMAIS
            # depuis le catalogue Ad TYP (un assemblage SANS MO renvoyait taux=0 →
            # écrasait le taux posé par « Appliquer taux »/manuel). On garde le taux
            # EXISTANT — ou la saisie en cours (overlay `taux_horaire` du body) si
            # fournie. Seul le re-pull EXPLICITE (else, ⟳) réaligne sur Ad TYP.
            # (Symétrie avec prix_unitaire/override : pas d'écrasement sans clic.)
            _taux_ov = data.get("taux_horaire")
            taux_auto = (float(_taux_ov) if _taux_ov not in (None, "")
                         else float(ligne["taux_horaire"] or 0))
            # heures_manuelles posé TRUE UNIQUEMENT si l'assemblage Ad TYP a de vraies
            # heures (> 0, ex. rendement) → catalogue autoritaire. Un assemblage SANS
            # rendement (heures=0, ex. Mobilisation) ne doit PAS créer un faux override
            # à 0 sur une ligne hr (sinon MO=0 au lieu du défaut qté).
            _hm = bool((m["heures"] or 0) > 0)
            if auto and "prix_unitaire" in data:
                # Saisie de coût en cours (overlay non encore sauvé) au moment du
                # changement de qté : on la persiste + pose l'override pour la
                # protéger (sinon elle serait perdue à la purge de l'overlay).
                cur.execute("""
                    UPDATE ad_budget.budget_lignes SET
                      qte=%s, heures=%s, heures_manuelles=%s, taux_horaire=%s, sous_traitant_montant=%s,
                      prix_unitaire=%s, prix_unitaire_override=%s,
                      source_typ_snapshot_at=NOW(), updated_at=NOW()
                    WHERE id=%s AND projet_id=%s RETURNING *
                """, (qte, m["heures"], _hm, taux_auto, m["sous_traitant_montant"],
                      float(data.get("prix_unitaire") or 0),
                      bool(data.get("prix_unitaire_override", True)),
                      ligne_id, projet_id))
            elif auto:
                # Rescale qté pur : préserve prix_unitaire / override / section /
                # description / unité ; ne touche que heures / taux / ST.
                cur.execute("""
                    UPDATE ad_budget.budget_lignes SET
                      qte=%s, heures=%s, heures_manuelles=%s, taux_horaire=%s, sous_traitant_montant=%s,
                      source_typ_snapshot_at=NOW(), updated_at=NOW()
                    WHERE id=%s AND projet_id=%s RETURNING *
                """, (qte, m["heures"], _hm, taux_auto, m["sous_traitant_montant"],
                      ligne_id, projet_id))
            else:
                # Pioche EXPLICITE (autocomplete) : re-pull complet depuis Ad TYP ;
                # le coût vient d'Ad TYP -> override remis à FALSE.
                cur.execute("""
                    UPDATE ad_budget.budget_lignes SET
                      section=%s, description=%s, unite=%s, prix_unitaire=%s,
                      qte=%s, heures=%s, heures_manuelles=%s, taux_horaire=%s, sous_traitant_montant=%s,
                      source_typ_code=%s, source_typ_snapshot_at=NOW(),
                      item_id_ad_mat=NULL, prix_unitaire_override=FALSE, updated_at=NOW()
                    WHERE id=%s AND projet_id=%s RETURNING *
                """, (m["section"], m["description"], m["unite"], m["prix_unitaire"],
                      qte, m["heures"], _hm, m["taux_horaire"], m["sous_traitant_montant"],
                      code, ligne_id, projet_id))
            row = cur.fetchone()
            conn.commit()
        except HTTPException:
            conn.rollback(); raise
        finally:
            cur.close(); conn.close()
        _mark_budget_dirty_if_emitted(projet_id)  # apply Ad TYP sur ligne -> snapshot
        return {"status": "applied", "ligne": row, "mo_flat": m["mo_flat"]}

    @router.post("/projets/{projet_id}/lignes/import")
    def import_items_to_projet(projet_id: int, data: dict, user=Depends(jwt_user)):
        """
        Importe plusieurs items de la base principale vers un projet.
        data = { "item_ids": [1, 2, 3, ...] }
        """
        # PHASE 3A — authentification + autorisation (écriture) sur le projet parent.
        _load_and_authorize_projet(get_conn, projet_id, user, "write")
        item_ids = data.get("item_ids", [])
        if not item_ids:
            return {"error": "No item_ids provided"}

        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)

        # D5 — auto-fill du taux par défaut. Mapping division CSI -> taux
        # chargé UNE fois (pas par item) pour éviter le N+1 ; résolution en
        # mémoire dans la boucle.
        taux_default_map = _load_taux_default_map(conn)

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
            section = source["section"]
            taux_horaire = (
                taux_default_map.get(section.strip()[:2], 0) if section else 0
            )
            cur.execute("""
                INSERT INTO ad_budget.budget_lignes
                (projet_id, source_item_id, section, description, unite, prix_unitaire, taux_horaire)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                projet_id,
                item_id,
                section,
                source["description"],
                source["unite"],
                source["prix_unitaire"] or 0,
                taux_horaire,
            ))
            inserted += 1

        conn.commit()
        cur.close()
        conn.close()
        if inserted:
            _mark_budget_dirty_if_emitted(projet_id)  # import de lignes -> snapshot
        return {"status": "imported", "count": inserted}

    # Valeurs autorisées pour sous_traitant_type. None / "" = vide.
    SOUS_TRAITANT_TYPES = {"Budget", "Soumission", "BSDQ", "Allocation"}

    @router.put("/projets/{projet_id}/lignes/{ligne_id}")
    def update_budget_ligne(projet_id: int, ligne_id: int, data: dict,
                            user=Depends(jwt_user)):
        # PHASE 3A — authentification + autorisation (écriture) sur le projet
        # PARENT. La mutation ci-dessous reste scopée WHERE id=%s AND
        # projet_id=%s : un ligne_id appartenant à un autre projet ne matche
        # rien -> aucune ligne modifiée.
        _load_and_authorize_projet(get_conn, projet_id, user, "write")
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
            # Sprint 1.5 : liens logiques vers Ad MAT (cross-DB, pas de FK physique).
            "item_id_ad_mat", "ad_hub_pending_id",
            # Onglet MAJ phase 2 : scope (master|client, lève l'ambiguïté d'id) +
            # prix snapshoté à la pioche (référence de détection de dérive). Posés par
            # le front à la pioche MAT (appliquerMatSurLigne). snapshot_at = NOW()
            # serveur quand le snapshot-prix est fourni (cf. bloc après la boucle).
            "item_ad_mat_scope", "source_mat_prix_snapshot",
            # Provenance Ad TYP : exposée en écriture pour pouvoir l'EFFACER
            # (null) quand la ligne bascule sur une source Ad MAT — garantit
            # qu'une ligne ne porte jamais les deux provenances. (La pose d'une
            # provenance Ad TYP reste faite par apply-typ, pas par ce PUT.)
            "source_typ_code", "source_typ_snapshot_at",
            # Passerelle Ad RES → Ad BUD : lien logique vers app_central.contacts
            # (UUID, cross-DB). NULL = saisie libre / ligne déliée.
            "sous_traitant_contact_id",
            # Flag override coût manuel : posé par le front quand l'user édite le
            # coût à la main -> protège prix_unitaire contre le resync Ad TYP AUTO.
            "prix_unitaire_override",
            # Flag override HEURES : posé quand l'user saisit des heures à la main ->
            # la saisie prime sur le défaut « heures=qté » des lignes unité hr.
            "heures_manuelles",
        ]:
            if field in data:
                val = data[field]
                # contact_id : "" / 0 / null → NULL (UUID nullable ; évite un
                # cast "" → uuid qui planterait). Délier = renvoyer null.
                if field == "sous_traitant_contact_id":
                    val = val or None
                elif field in ("prix_unitaire_override", "heures_manuelles"):
                    val = bool(val)
                fields.append(f"{field} = %s")
                values.append(val)
        # Snapshot-prix MAT fourni → horodate côté serveur (source de vérité).
        if "source_mat_prix_snapshot" in data:
            fields.append("source_mat_snapshot_at = NOW()")
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
        _mark_budget_dirty_if_emitted(projet_id)  # mutation de ligne -> snapshot
        return {"status": "updated"}

    @router.patch("/projets/{projet_id}/lignes/{ligne_id}")
    def patch_budget_ligne(projet_id: int, ligne_id: int, data: dict,
                           user=Depends(jwt_user)):
        # Alias PATCH du PUT — même comportement, même whitelist de champs.
        # L'autorisation est faite par update_budget_ligne (on lui transmet user).
        return update_budget_ligne(projet_id, ligne_id, data, user)

    @router.get("/sous-traitants/suggestions")
    def get_sous_traitants_suggestions(user=Depends(jwt_user)):
        """Liste DISTINCT des noms de sous-traitants déjà saisis, pour
        autocomplétion dans l'UI.

        Scope (PHASE 3D) : par ORGANISATION quand l'utilisateur en a une — les
        sous-traitants sont un bassin partagé à l'échelle de la firme. Repli sur
        le scope user (comportement historique) quand organization_id est NULL,
        même logique de non-nullité que _authorize_projet. Tant que la PHASE 4
        n'a pas peuplé les organisations, tous les organization_id sont NULL :
        100% des users retombent sur le scope user et le comportement est
        STRICTEMENT identique à l'historique. Le changement est inerte d'ici là
        et s'activera de lui-même une fois les organisations renseignées.

        scope_clause est un littéral fixe (jamais d'entrée utilisateur) — pas
        d'injection ; seule la valeur passe en paramètre lié."""
        org_id = user.get("organization_id")
        if org_id is not None:
            scope_clause = "p.organization_id = %s"
            scope_value = org_id
        else:
            scope_clause = "p.user_id = %s"
            scope_value = user["id"]
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT DISTINCT bl.sous_traitant_nom
            FROM ad_budget.budget_lignes bl
            JOIN ad_budget.projets p ON p.id = bl.projet_id
            WHERE {scope_clause}
              AND bl.sous_traitant_nom IS NOT NULL
              AND bl.sous_traitant_nom <> ''
            ORDER BY bl.sous_traitant_nom
            """,
            (scope_value,),
        )
        rows = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows

    @router.delete("/projets/{projet_id}/lignes/{ligne_id}")
    def delete_budget_ligne(projet_id: int, ligne_id: int,
                            user=Depends(jwt_user)):
        # PHASE 3A — authentification + autorisation (écriture) sur le projet
        # PARENT. Le DELETE reste scopé WHERE id=%s AND projet_id=%s : un
        # ligne_id d'un autre projet ne matche rien.
        _load_and_authorize_projet(get_conn, projet_id, user, "write")
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM ad_budget.budget_lignes
            WHERE id = %s AND projet_id = %s
        """, (ligne_id, projet_id))
        conn.commit()
        cur.close()
        conn.close()
        _mark_budget_dirty_if_emitted(projet_id)  # suppression de ligne -> snapshot
        return {"status": "deleted"}

    @router.get("/projets/{projet_id}/total")
    def get_projet_total(projet_id: int, user=Depends(jwt_user)):
        """Retourne le total du budget d'un projet groupé par section."""
        # PHASE 3A — authentification + autorisation (lecture) sur le projet parent.
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
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