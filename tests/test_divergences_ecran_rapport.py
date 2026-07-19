"""Divergences ÉCRAN ↔ RAPPORT connues (D3, D4) — tests ATTENDUS-EN-ÉCHEC (xfail).

Contexte : diagnostic read-only (rapport ≡ écran, Δ=0 sur le parc réel). Deux
défauts RÉELS subsistent dans le code mais AUCUNE donnée ne les déclenche
aujourd'hui. On ne les corrige pas — on les VERROUILLE :

  D3 — mode « ventilé » : l'écran (App.jsx) arrondit l'administration & profit
       UNE fois sur la somme totale (R(grandTotal + Σ lineAP)), alors que le
       rapport serveur (compute_budget_totals) l'arrondit PAR DISCIPLINE puis
       somme. « Somme d'arrondis ≠ arrondi de somme » → écart possible si un
       projet est À LA FOIS ventilé ET arrondi au dollar (intersection vide
       aujourd'hui : 1 projet ventilé, non arrondi).

  D4 — ST « computed » : l'écran affiche qté × prix_unitaire_st EN DIRECT, alors
       que le rapport lit la colonne stockée sous_traitant_montant. Divergence si
       le stocké n'est pas resynchronisé (0 des 62 lignes PU_ST désync aujourd'hui).

Chaque test construit DÉLIBÉRÉMENT le cas déclencheur et compare le VRAI chemin
rapport (compute_budget_totals importé) à une transcription FIDÈLE du sommaire
écran (App.jsx). L'assertion naturelle « écran == rapport » ÉCHOUE aujourd'hui :
c'est le but. Le runner traite cet échec comme ATTENDU (XFAIL, suite verte). Le
jour où le calcul est unifié (défaut corrigé), l'assertion PASSERA → XPASS →
exit non-zéro, pour forcer le retrait du marqueur.

Lancer :  python tests/test_divergences_ecran_rapport.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.aggregates import _js_round  # noqa: E402
from modules.ad_budget_api import compute_budget_totals  # noqa: E402


# ── RÉFÉRENCE « ÉCRAN » — transcription fidèle du sommaire Ad BUD (App.jsx) ──
# Réfs : getRow (App.jsx ~L2220), groupAdminProfit (~L3996), lineAdminProfit
# (~L4028), sousTotalAvantTaxes (~L4063). C'est la vérité affichée à l'écran ;
# on la compare au chemin serveur compute_budget_totals (= PDF + hub).
_HEURE = {"hr", "hre", "heure", "heures", "h"}
_PCTFIELD = {
    "conditions": "pct_admin_conditions", "architecture": "pct_admin_architecture",
    "mecanique": "pct_admin_mecanique", "excavation": "pct_admin_excavation",
}
_GROUPS = [
    ("conditions", lambda n: n == 1), ("architecture", lambda n: 2 <= n <= 14),
    ("mecanique", lambda n: 20 <= n <= 28), ("excavation", lambda n: n == 31),
]


def _prefix_n(s):
    m = re.match(r"^(\d+)", (s or "").strip())
    return int(m.group(1)[:2]) if m else None


def _gkey(s):
    n = _prefix_n(s)
    if n is None:
        return None
    for k, rng in _GROUPS:
        if rng(n):
            return k
    return None


def screen_sous_total_avant_taxes(projet, lignes):
    """Sous-total avant taxes tel que l'écran l'affiche (App.jsx)."""
    arr = bool(projet.get("arrondi_dollar"))
    R = (lambda v: float(_js_round(v))) if arr else (lambda v: v)  # JS Math.round
    is_ventile = (projet.get("pct_admin_mode") or "global") == "ventile"

    def is_heure(u):
        return str(u or "").strip().lower() in _HEURE

    def rawcat(k, c):
        return projet.get(f"{_PCTFIELD[k]}_{c}")

    def _all_null(k):
        return all(rawcat(k, c) in (None, "") for c in ("mat", "mo", "st"))

    def _eff(k, c, disc):
        r = rawcat(k, c)
        return disc if r in (None, "") else float(r)

    rows = []
    for l in lignes:
        qte = float(l.get("qte") or 0)
        prix = float(l.get("prix_unitaire") or 0)
        ajustementPct = float(l.get("ajustement_pct") or 0)
        ajustMat = float(l.get("ajust_materiaux") or 0)
        ajustMo = float(l.get("ajust_main_oeuvre") or 0)
        ajustSt = float(l.get("ajust_sous_traitant") or 0)
        heuresRaw = float(l.get("heures") or 0)
        heuresManuelles = (l.get("heures_manuelles") is True)
        isHre = is_heure(l.get("unite"))
        overrideReel = heuresManuelles and abs(heuresRaw) > 1e-9
        heures = qte if (isHre and not overrideReel) else heuresRaw
        taux = float(l.get("taux_horaire") or 0)
        pu_st = float(l.get("prix_unitaire_st") or 0)
        st_raw = float(l.get("sous_traitant_montant") if l.get("sous_traitant_montant") is not None
                       else (l.get("cout_sous_traitant") or 0))
        # ÉCRAN : montant ST = qté × PU_ST EN DIRECT quand PU_ST > 0 (≠ stocké → D4)
        stMontant = qte * pu_st if pu_st > 0 else st_raw
        stMatVal = qte * prix * (1 + ajustMat / 100)
        stMoVal = heures * taux * (1 + ajustMo / 100)
        stStVal = stMontant * (1 + ajustSt / 100)
        if not (stMatVal > 0 or stMoVal > 0 or stStVal > 0):
            continue
        total = (stMatVal + stMoVal + stStVal) * (1 + ajustementPct / 100)
        rows.append((l.get("section"), stMatVal, stMoVal, stStVal, total))

    grandTotal = sum(R(t) for (_s, _m, _mo, _st, t) in rows)
    groups = {}
    for (sec, mat, mo, st, t) in rows:
        k = _gkey(sec)
        if k:
            g = groups.setdefault(k, {"subtotal": 0.0, "subMat": 0.0, "subMo": 0.0, "subSt": 0.0})
            g["subtotal"] += R(t); g["subMat"] += mat; g["subMo"] += mo; g["subSt"] += st

    def group_ap(k, g):
        disc = float(projet.get(_PCTFIELD[k]) or 0)
        if _all_null(k):
            return R(g["subtotal"] * disc / 100)
        return R(g["subMat"] * _eff(k, "mat", disc) / 100
                 + g["subMo"] * _eff(k, "mo", disc) / 100
                 + g["subSt"] * _eff(k, "st", disc) / 100)

    def line_ap(sec, total, mat, mo, st):
        k = _gkey(sec)
        if not k:
            return 0.0
        disc = float(projet.get(_PCTFIELD[k]) or 0)
        if _all_null(k):
            return total * disc / 100
        return (mat * _eff(k, "mat", disc) / 100 + mo * _eff(k, "mo", disc) / 100
                + st * _eff(k, "st", disc) / 100)

    if is_ventile:
        # ÉCRAN VENTILÉ : A&P arrondi UNE fois sur la somme (App.jsx L4063-4065).
        ap_sum = sum(line_ap(sec, t, mat, mo, st) for (sec, mat, mo, st, t) in rows)
        return R(grandTotal + ap_sum)
    tot = grandTotal
    for k, g in groups.items():
        tot += group_ap(k, g)  # A&P arrondi PAR GROUPE
    return tot


def _screen_vs_report(projet, lignes):
    screen = round(screen_sous_total_avant_taxes(projet, lignes), 2)
    report = round(compute_budget_totals(projet, lignes)["sous_total_avant_taxes"], 2)
    return screen, report


# ── Cas déclencheurs (assertion NATURELLE écran == rapport ; échoue aujourd'hui) ──
def test_d3_ventile_et_arrondi():
    """Projet VENTILÉ + ARRONDI, 2 disciplines dont l'A&P tombe sur .5 → l'écran
    (arrondi 1× sur la somme) et le rapport (arrondi par discipline) divergent."""
    projet = {"arrondi_dollar": True, "pct_admin_mode": "ventile",
              "pct_admin_architecture": 10, "pct_admin_mecanique": 10}
    lignes = [
        {"section": "03 30 00", "qte": 0, "unite": "global", "sous_traitant_montant": 1005},
        {"section": "23 05 00", "qte": 0, "unite": "global", "sous_traitant_montant": 1005},
    ]
    screen, report = _screen_vs_report(projet, lignes)
    assert screen == report, f"ecran {screen} != rapport {report} (delta={round(report - screen, 2)})"


def test_d4_st_computed_desynchronise():
    """Ligne ST « computed » : PU_ST=10 × qté=100 = 1000 à l'écran, mais la colonne
    stockée sous_traitant_montant=900 (désync) → le rapport lit 900."""
    projet = {"arrondi_dollar": False, "pct_admin_mode": "global"}
    lignes = [
        {"section": "50 00 00", "qte": 100, "unite": "global",
         "prix_unitaire_st": 10, "sous_traitant_montant": 900},
    ]
    screen, report = _screen_vs_report(projet, lignes)
    assert screen == report, f"ecran {screen} != rapport {report} (delta={round(screen - report, 2)})"


# ── Runner autonome à sémantique XFAIL STRICTE ────────────────────────────────
_XFAIL = [
    ("test_d3_ventile_et_arrondi", test_d3_ventile_et_arrondi,
     "D3 — ventilé : écran arrondit l'A&P 1× sur la somme, rapport par discipline"),
    ("test_d4_st_computed_desynchronise", test_d4_st_computed_desynchronise,
     "D4 — ST computed : écran qté×PU_ST live vs rapport sous_traitant_montant stocké"),
]


def _run_all():
    xpass = 0
    print("[divergences ecran<->rapport] tests ATTENDUS-EN-ECHEC (D3, D4) :")
    for name, fn, reason in _XFAIL:
        try:
            fn()
        except AssertionError as e:
            print(f"  [XFAIL] {name} — défaut attendu présent : {e}")
            continue
        # aucune exception → écran == rapport → le défaut a DISPARU (calcul unifié)
        print(f"  [XPASS] {name} — DÉFAUT DISPARU, retirer le marqueur xfail ({reason})")
        xpass += 1
    if xpass:
        print(f"\n{xpass} XPASS inattendu(s) — un défaut documenté a été corrigé : "
              "mettre à jour ce fichier (retirer le xfail).")
        return 1
    print(f"\n{len(_XFAIL)}/{len(_XFAIL)} XFAIL comme attendu (défauts D3/D4 documentés, non déclenchés en prod).")
    return 0


if __name__ == "__main__":
    sys.exit(_run_all())
