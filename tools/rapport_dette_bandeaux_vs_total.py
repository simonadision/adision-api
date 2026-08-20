"""Dette d'arrondi : Σ(bandeaux Division) vs grand total, sur projets REELS.

RÈGLE : en mode arrondi_dollar, quand l'A&P est DISTRIBUÉ (ventilé, ou global avec
A&P masqué → factor=1+pct/100), le bandeau Division somme R(tot_real × factor) PAR
LIGNE, tandis que le grand total (compute_budget_totals) somme R(tot_real) par ligne
puis arrondit l'A&P UNE FOIS par groupe. Les deux ne se réconcilient pas.

On mesure Σ bandeaux − grand_total (= sous_total_avant_taxes), par projet.
  - Projets VENTILÉS : c'est l'écart RÉEL (leur rapport a toujours la dette).
  - Projets GLOBAUX : écart POTENTIEL (si rendu avec A&P distribué / masqué).
Read-only. Lancer : railway run --service Postgres python <ce fichier>
"""
import os
import sys

sys.path.insert(0, os.getcwd())
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import psycopg
from psycopg.rows import dict_row
from modules.aggregates import _js_round
from modules.ad_budget_api import (
    _prix_vente_total, _heures_effectives, _effective_qte,
    BUDGET_GROUPS_PDF, _group_key_for, _section_prefix_n, _is_ventile_mode,
)


def _tot_real(l):
    qte = _effective_qte(l, 0, 0, 0, 0)
    heures = _heures_effectives(l.get("unite"), l.get("heures"), l.get("heures_manuelles"), qte,
                                l.get("production_valeur"))
    taux = float(l.get("taux_horaire") or 0)
    stm = float(l.get("sous_traitant_montant") or 0)
    if qte <= 0 and not (heures > 0 and taux > 0) and not (stm > 0):
        return None
    prix = float(l.get("prix_unitaire") or 0)
    ajm = float(l.get("ajust_materiaux") or 0)
    ajmo = float(l.get("ajust_main_oeuvre") or 0)
    ajst = float(l.get("ajust_sous_traitant") or 0)
    adj = float(l.get("ajustement_pct") or 0)
    st = qte * prix * (1 + ajm / 100) + heures * taux * (1 + ajmo / 100) + stm * (1 + ajst / 100)
    return st * (1 + adj / 100)


def _sigma_bandeaux(projet, lignes):
    """Σ des totaux de bandeaux Division = Σ_ligne R(tot_real × factor), factor
    en mode DISTRIBUÉ (A&P ventilé dans les lignes)."""
    arr = bool(projet.get("arrondi_dollar"))
    R = (lambda v: float(_js_round(v))) if arr else (lambda v: v)
    factor = {}
    for key, _, pct_field, _ in BUDGET_GROUPS_PDF:
        factor[key] = 1 + float(projet.get(pct_field) or 0) / 100
    s = 0.0
    for l in lignes:
        tr = _tot_real(l)
        if tr is None:
            continue
        gkey = _group_key_for(_section_prefix_n(l.get("section")))
        f = factor.get(gkey, 1) if gkey is not None else 1
        td = R(tr * f)
        if td == 0:
            continue
        s += td
    return s


def main():
    url = os.environ.get("DATABASE_PUBLIC_URL")
    if not url:
        print("DATABASE_PUBLIC_URL absent (railway run --service Postgres)"); return 2
    conn = psycopg.connect(url, connect_timeout=20, row_factory=dict_row)
    cur = conn.cursor()
    cur.execute("SELECT * FROM ad_budget.projets ORDER BY id")
    projets = cur.fetchall()
    rows = []
    for p in projets:
        cur.execute("SELECT * FROM ad_budget.budget_lignes WHERE projet_id=%s AND actif=TRUE", (p["id"],))
        lignes = cur.fetchall()
        if not lignes:
            continue
        grand = _prix_vente_total(p, lignes)
        if grand == 0:
            continue
        sigma = _sigma_bandeaux(p, lignes)
        gap = round(sigma - grand, 2)
        # A&P décomposé (pct_admin_<disc>_<cat> non-NULL) ? + %discipline posés ?
        disc = ("conditions", "architecture", "mecanique", "excavation")
        decomp = any(p.get(f"pct_admin_{d}_{c}") is not None for d in disc for c in ("mat", "mo", "st"))
        pcts = [float(p.get(f"pct_admin_{d}") or 0) for d in disc]
        rows.append({
            "id": p["id"], "arrondi": bool(p.get("arrondi_dollar")),
            "mode": p.get("pct_admin_mode") or "global", "ventile": _is_ventile_mode(p),
            "grand": grand, "sigma_bandeaux": round(sigma, 2), "gap": gap, "nl": len(lignes),
            "decomp": decomp, "pct_max": max(pcts),
        })
    cur.close(); conn.close()

    print(f"=== {len(rows)} projets avec budget (ecart = Sigma bandeaux distribues - grand total) ===\n")
    print(f"{'proj':>6} {'arr':>4} {'decomp':>7} {'nl':>4} {'grand_total':>13} {'Sigma_band':>13} {'ECART $':>11}")
    for r in sorted(rows, key=lambda x: -abs(x["gap"])):
        if abs(r["gap"]) >= 0.01:
            print(f"{r['id']:>6} {('OUI' if r['arrondi'] else 'non'):>4} {('OUI' if r['decomp'] else 'non'):>7} "
                  f"{r['nl']:>4} {r['grand']:>13,.2f} {r['sigma_bandeaux']:>13,.2f} {r['gap']:>+11.2f}")
    # Séparer les 2 effets :
    #  - ARRONDI PUR : arrondi ON, PAS de decomposition -> le factor discipline = A&P grand total,
    #    ecart = accumulation d'arrondi par ligne.
    #  - MISMATCH DECOMPOSE : decomposition ON -> factor discipline (bandeau) != A&P decompose (grand total).
    arr_pur = [r for r in rows if r["arrondi"] and not r["decomp"]]
    decomp = [r for r in rows if r["decomp"]]
    print("\n--- Effet 1 : ARRONDI PUR (arrondi ON, A&P NON decompose) ---")
    for r in arr_pur:
        print(f"  proj {r['id']}: ecart = {r['gap']:+.2f} $ sur {r['nl']} lignes (grand {r['grand']:,.0f})")
    print(f"  -> magnitude arrondi pur : max {max((abs(r['gap']) for r in arr_pur), default=0):.2f} $")
    print("\n--- Effet 2 : MISMATCH A&P DECOMPOSE (factor=%discipline vs grand total=%decompose) ---")
    for r in decomp:
        print(f"  proj {r['id']}: ecart = {r['gap']:+.2f} $ ({r['gap']/r['grand']*100:+.2f} % du total), arrondi={r['arrondi']}")
    print(f"  -> magnitude decompose : max {max((abs(r['gap']) for r in decomp), default=0):.2f} $")
    print(f"\nN.B. Ecart mesure en mode DISTRIBUE simule (A&P dans les bandeaux). En config DEFAUT")
    print(f"(A&P affiche en rangees separees, factor=1), les bandeaux montrent le COUTANT pre-A&P")
    print(f"et se reconcilient avec le grand total -> dette LATENTE, ne mord qu'en ventile / A&P masque.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
