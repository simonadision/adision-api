"""Port Python de @adision/aggregates v1.1.0.

Référence de vérité : packages/aggregates/src/computeAggregates.js (JS).
Doit produire le MÊME output que le JS pour les MÊMES inputs (golden master).
Utilisé pour persister aggregates_jsonb dans les snapshots app_ana.project_snapshots
(Sprint B). Ad ANA consommera ces snapshots pour des analyses cross-projets.

Format de ligne d'entrée attendu (mat/mo/st nested) :
    {
      id, section, description, csi_code, type_st,
      mat: { cout, qte, ajust_pct },
      mo:  { heures, taux, ajust_pct },
      st:  { montant, ajust_pct }
    }

Si tu pars de rows BD bruts (shape flat), passe d'abord par adapt_budget_lines().
"""
import math
from datetime import datetime, timezone
from typing import Optional

from modules.csi_divisions import get_csi_nom


# JS Math.round = "half rounds toward +infinity". Python round() = banker's
# rounding (half to even). Différence critique pour le golden master :
# Math.round(0.5) === 1 mais round(0.5) == 0 en Python.
def _js_round(n: float) -> int:
    return math.floor(n + 0.5)


def _round2(n: float) -> float:
    return _js_round(n * 100) / 100


def _round1(n: float) -> float:
    return _js_round(n * 10) / 10


def _round3(n: float) -> float:
    return _js_round(n * 1000) / 1000


def _round4(n: float) -> float:
    return _js_round(n * 10000) / 10000


# Mapping types S-T capitalisés (BD) vers les clés lowercase du format
# aggregates_jsonb v1. Toute valeur hors whitelist devient None.
_TYPE_ST_MAP = {
    "Budget": "budget",
    "Soumission": "soumission",
    "BSDQ": "bsdq",
    "Allocation": "allocation",
}

# Unités « heures de MO » : la qté représente des heures -> heures = qté par défaut
# (miroir EXACT de getRow App.jsx + adaptBudgetLines.js). Sans ça, l'agrégat (snapshot
# hub + Ad ANA) divergeait du « Coûtant total » affiché dans le récap Ad BUD.
_HEURE_UNITS = {"hr", "hre", "heure", "heures", "h"}


def _is_heure_unit(unite) -> bool:
    return str(unite or "").strip().lower() in _HEURE_UNITS


def adapt_budget_lines(raw_lines) -> list:
    """Adapte les rows BD (shape flat) au format ligne attendu par
    compute_aggregates (mat/mo/st nested). Équivalent Python du JS
    apps/ad-bud/src/utils/adaptBudgetLines.js.

    Filtre les lignes inactives (actif=False).
    """
    if not isinstance(raw_lines, list):
        return []
    result = []
    for ln in raw_lines:
        if ln.get("actif") is False:
            continue
        sous_traitant_type = ln.get("sous_traitant_type")
        type_st = _TYPE_ST_MAP.get(sous_traitant_type) if sous_traitant_type else None
        # sous_traitant_montant remplace cout_sous_traitant ; fallback sur
        # l'ancienne colonne pour les projets antérieurs à la migration.
        montant_st = ln.get("sous_traitant_montant")
        if montant_st is None:
            montant_st = ln.get("cout_sous_traitant") or 0
        # MO — base heures alignée sur le récap : ligne « hr » SANS heures saisies
        # manuellement -> heures = qté ; sinon heures saisies (override réel).
        qte_v = float(ln.get("qte") or 0)
        # RÈGLE #2 (2026-08-17, décision Simon) : QTÉ=0 exclut TOUJOURS le
        # montant ST des agrégats (push hub `budget_st`, Ad ANA) — même règle
        # que compute_budget_totals (ad_budget_api.py) et effectiveStMontant
        # (computeReportModel.js). Gaté ICI, à la source, pour que
        # compute_aggregates n'ait pas à connaître qte à part.
        if qte_v <= 0:
            montant_st = 0
        heures_raw = float(ln.get("heures") or 0)
        override_reel = (ln.get("heures_manuelles") is True) and abs(heures_raw) > 1e-9
        heures_eff = qte_v if (_is_heure_unit(ln.get("unite")) and not override_reel) else heures_raw
        result.append({
            "id": ln.get("id"),
            "section": ln.get("section"),
            "description": ln.get("description"),
            "csi_code": str(ln.get("section") or "").strip(),
            "type_st": type_st,
            "mat": {
                "cout": float(ln.get("prix_unitaire") or 0),
                "qte": qte_v,
                "ajust_pct": float(ln.get("ajust_materiaux") or 0),
            },
            "mo": {
                "heures": heures_eff,
                "taux": float(ln.get("taux_horaire") or 0),
                "ajust_pct": float(ln.get("ajust_main_oeuvre") or 0),
            },
            "st": {
                "montant": float(montant_st or 0),
                "ajust_pct": float(ln.get("ajust_sous_traitant") or 0),
            },
        })
    return result


def _now_iso() -> str:
    """ISO 8601 avec millisecondes + Z final (compat JS new Date().toISOString())."""
    n = datetime.now(timezone.utc)
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}Z"


def compute_aggregates(budget_lines, project_meta: Optional[dict] = None) -> dict:
    """Port Python de computeAggregates JS v1.1.0.

    Lignes attendues : format aggregates v1 (mat/mo/st nested). Si tu pars
    de rows BD bruts, passe d'abord par adapt_budget_lines().
    Renvoie un objet conforme au format aggregates_jsonb v1.
    """
    if project_meta is None:
        project_meta = {}
    lines = budget_lines if isinstance(budget_lines, list) else []

    # 1. Calcul ligne par ligne + accumulateurs
    total_mat = 0.0
    total_mo = 0.0
    total_st = 0.0
    weighted_mat_num = 0.0
    weighted_mat_den = 0.0
    weighted_mo_num = 0.0
    weighted_mo_den = 0.0
    weighted_st_num = 0.0
    weighted_st_den = 0.0

    # dict Python 3.7+ préserve l'ordre d'insertion (équivalent JS Map).
    sections_map = {}
    type_st_map = {"budget": 0.0, "soumission": 0.0, "bsdq": 0.0, "allocation": 0.0}

    for ln in lines:
        mat = ln.get("mat") or {}
        mo = ln.get("mo") or {}
        st = ln.get("st") or {}

        mat_base = (mat.get("cout") or 0) * (mat.get("qte") or 0)
        mo_base = (mo.get("heures") or 0) * (mo.get("taux") or 0)
        st_base = st.get("montant") or 0

        mat_adj = mat.get("ajust_pct") or 0
        mo_adj = mo.get("ajust_pct") or 0
        st_adj = st.get("ajust_pct") or 0

        st_mat = mat_base * (1 + mat_adj / 100)
        st_mo = mo_base * (1 + mo_adj / 100)
        st_st = st_base * (1 + st_adj / 100)

        total_mat += st_mat
        total_mo += st_mo
        total_st += st_st

        weighted_mat_num += mat_base * mat_adj
        weighted_mat_den += mat_base
        weighted_mo_num += mo_base * mo_adj
        weighted_mo_den += mo_base
        weighted_st_num += st_base * st_adj
        weighted_st_den += st_base

        # Section CSI : 2 premiers caractères, ou "00" si absent
        raw_code = str(ln.get("csi_code") or "").strip()
        code = raw_code[:2] if len(raw_code) >= 2 else "00"

        if code not in sections_map:
            sections_map[code] = {"mat": 0.0, "mo": 0.0, "st": 0.0}
        sec = sections_map[code]
        sec["mat"] += st_mat
        sec["mo"] += st_mo
        sec["st"] += st_st

        # by_type_st : ventile uniquement la part S-T (matche le JS)
        type_st_value = ln.get("type_st")
        if st_st > 0 and type_st_value and type_st_value in type_st_map:
            type_st_map[type_st_value] += st_st

    total_gen = total_mat + total_mo + total_st

    # 2. Ratios
    if total_gen == 0:
        ratios = {
            "materiaux_pct": None,
            "main_oeuvre_pct": None,
            "sous_traitance_pct": None,
        }
    else:
        ratios = {
            "materiaux_pct": _round2(total_mat / total_gen * 100),
            "main_oeuvre_pct": _round2(total_mo / total_gen * 100),
            "sous_traitance_pct": _round2(total_st / total_gen * 100),
        }

    # 3. by_section_csi (tri par total DESC, sort stable comme JS V8)
    by_section = []
    for code, vals in sections_map.items():
        total = vals["mat"] + vals["mo"] + vals["st"]
        by_section.append({
            "code": code,
            "nom": get_csi_nom(code),
            "materiaux": _round2(vals["mat"]),
            "main_oeuvre": _round2(vals["mo"]),
            "sous_traitance": _round2(vals["st"]),
            "total": _round2(total),
            "pct_general": None if total_gen == 0 else _round2(total / total_gen * 100),
        })
    by_section.sort(key=lambda s: s["total"], reverse=True)

    # 4. by_type_st (arrondi)
    by_type_st = {
        "budget": _round2(type_st_map["budget"]),
        "soumission": _round2(type_st_map["soumission"]),
        "bsdq": _round2(type_st_map["bsdq"]),
        "allocation": _round2(type_st_map["allocation"]),
    }

    # 5. adjustments (moyennes pondérées par valeur)
    adjustments = {
        "materiaux_avg_pct": (
            None if weighted_mat_den == 0
            else _round1(weighted_mat_num / weighted_mat_den)
        ),
        "main_oeuvre_avg_pct": (
            None if weighted_mo_den == 0
            else _round1(weighted_mo_num / weighted_mo_den)
        ),
        "sous_traitance_avg_pct": (
            None if weighted_st_den == 0
            else _round1(weighted_st_num / weighted_st_den)
        ),
    }

    # 6. derived
    superficie = project_meta.get("superficie_m2")
    derived = {
        "cout_par_pi2": (
            None if (not superficie or superficie == 0)
            else _round2(total_gen / superficie)
        ),
        "ratio_mo_sur_mat": (
            None if total_mat == 0 else _round3(total_mo / total_mat)
        ),
        "ratio_st_sur_total": (
            None if total_gen == 0 else _round4(total_st / total_gen)
        ),
    }

    # 7. Résultat (ordre des clés cohérent avec le JS)
    result = {
        "schema_version": 1,
        "computed_at": _now_iso(),
        "totals": {
            "materiaux": _round2(total_mat),
            "main_oeuvre": _round2(total_mo),
            "sous_traitance": _round2(total_st),
            "general": _round2(total_gen),
        },
        "ratios": ratios,
        "counts": {
            "nb_lignes": len(lines),
            "nb_sections_csi": len(sections_map),
        },
        "by_section_csi": by_section,
        "by_type_st": by_type_st,
        "adjustments": adjustments,
        "derived": derived,
    }

    # 8. Validation des invariants (warn-only, ne raise jamais — comme JS)
    sum_by_type = sum(by_type_st.values())
    sum_by_section = sum(s["total"] for s in by_section)
    if abs(result["totals"]["general"] - (
        result["totals"]["materiaux"]
        + result["totals"]["main_oeuvre"]
        + result["totals"]["sous_traitance"]
    )) > 0.01:
        print(
            "[compute_aggregates] Invariant cassé: totals.general != Mat+MO+ST",
            flush=True,
        )
    has_any_type_st = any(
        ln.get("type_st") and ln.get("type_st") in type_st_map for ln in lines
    )
    if has_any_type_st and abs(sum_by_type - result["totals"]["sous_traitance"]) > 0.01:
        print(
            f"[compute_aggregates] Invariant cassé: SUM(by_type_st) != totals.sous_traitance "
            f"(sumByType={sum_by_type}, sousTraitance={result['totals']['sous_traitance']})",
            flush=True,
        )
    if abs(sum_by_section - result["totals"]["general"]) > 0.01:
        print(
            "[compute_aggregates] Invariant cassé: SUM(by_section_csi) != totals.general",
            flush=True,
        )

    return result
