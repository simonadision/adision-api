"""Verrou serveur — la colonne `heures` ne peut plus mentir au serveur quand
`production_valeur` est renseignée.

Incident 2026-08-20 (projet 290) : le serveur calculait un coûtant total de
2 796 604 $ contre 2 757 369 $ à l'écran — écart de 39 219 $, entièrement en
main-d'œuvre (8 334 h serveur vs 7 871 h écran). Cause : 16 lignes dont la
colonne `heures` contredisait le ratio de production (`heures` désynchronisée
de qté/production), lue TELLE QUELLE côté serveur (`_heures_effectives`
ignorait `production_valeur`). Les données ont été corrigées en production ;
ce fichier verrouille la RÈGLE côté serveur pour qu'un autre projet ne puisse
plus reproduire l'écart.

Règle (miroir App.jsx::getRow, décision Simon 14 août 2026) : production
renseignée (`production_valeur` > 0) -> PRIORITÉ ABSOLUE sur `heures` ET
`heures_manuelles`. heures = round(qté / production, 1), demi vers le haut.

Couvre les 4 chemins de calcul CONVERGÉS sur la même fonction canonique
(modules.aggregates.heures_effectives) :
  - compute_budget_totals   (ad_budget_api.py)  — récap financier / PDF / hub
  - _line_total             (budget_fingerprint.py) — empreinte devis
  - compute_lot_totals      (lots_calc.py)       — délègue à _line_total

Lancer : python -m pytest tests/test_verrou_production_heures.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from modules.aggregates import heures_effectives  # noqa: E402
from modules.ad_budget_api import compute_budget_totals  # noqa: E402
from modules.budget_fingerprint import _line_total  # noqa: E402
from modules.lots_calc import compute_lot_totals  # noqa: E402


_PROJET = {"arrondi_dollar": False}  # aucun pct_admin_* -> A&P nul (comparaison directe)


def _ligne(**over):
    l = {
        "id": 1, "section": "07 21 00", "description": "Ligne test",
        "unite": "pi2", "qte": 0, "prix_unitaire": 0,
        "ajust_materiaux": 0, "heures": 0, "heures_manuelles": False, "taux_horaire": 0,
        "ajust_main_oeuvre": 0, "sous_traitant_montant": 0, "ajust_sous_traitant": 0,
        "ajustement_pct": 0, "production_valeur": None, "actif": True,
    }
    l.update(over)
    return l


# ── 1. Cas RÉEL isolant (projet 290) : `heures` mentait, le ratio gagne ──────
def test_isolant_heures_incoherente_ratio_gagne():
    # 1 800 pi² à 96 pi²/h -> 18,75 h -> 18,8 h. La colonne `heures` stockait
    # 149,40 h (rendement implicite ~12 pi²/h, incohérent) : DOIT être ignorée.
    h = heures_effectives("pi2", 149.40, False, 1800, 96)
    assert h == pytest.approx(18.8)


def test_isolant_via_compute_budget_totals():
    ligne = _ligne(description="Isolant en panneaux rigides-plafond", qte=1800,
                   heures=149.40, taux_horaire=50, unite="pi2", production_valeur=96)
    totals = compute_budget_totals(_PROJET, [ligne])
    assert totals["heures_mo"] == pytest.approx(18.8)
    assert totals["snap_mo"] == pytest.approx(18.8 * 50)


# ── 2. Cas RÉEL charpenterie : `heures` = 0, le ratio gagne ──────────────────
def test_charpenterie_heures_zero_ratio_gagne():
    h = heures_effectives("un", 0, False, 2, 0.125)
    assert h == pytest.approx(16.0)


def test_charpenterie_via_compute_lot_totals():
    ligne = _ligne(id=9, description="Charpenterie de bois", qte=2, heures=0,
                   taux_horaire=80, unite="un", production_valeur=0.125)
    result = compute_lot_totals([ligne], [], arrondi_dollar=False)
    assert result["grand_total"] == pytest.approx(16 * 80)  # 2 x 8 h/unité x 80 $/h


# ── 3. production_valeur ET heures_manuelles=True -> le ratio gagne QUAND MÊME ──
def test_production_ecrase_override_manuel():
    h = heures_effectives("hr", 999, True, 10, 5)
    assert h == pytest.approx(2.0)


# ── 4. Pas de production, unité hr, pas d'override -> heures = qté (inchangé) ──
def test_sans_production_unite_hr_sans_override_heures_egale_qte():
    for prod in (None, 0):
        h = heures_effectives("hr", 3, False, 7.5, prod)
        assert h == pytest.approx(7.5)


# ── 5. Pas de production, unité hr, override réel -> heures = colonne (inchangé) ──
def test_sans_production_unite_hr_override_reel_heures_egale_colonne():
    for prod in (None, 0):
        h = heures_effectives("hr", 6, True, 7.5, prod)
        assert h == pytest.approx(6.0)


# ── 6. Pas de production, unité non horaire -> heures = colonne (inchangé) ──────
def test_sans_production_unite_non_horaire_heures_egale_colonne():
    for prod in (None, 0):
        h = heures_effectives("m2", 4.2, False, 10, prod)
        assert h == pytest.approx(4.2)


def test_compute_budget_totals_no_op_sans_production():
    # Ligne HISTORIQUE (pas de production_valeur) : comportement inchangé.
    ligne = _ligne(qte=0, heures=8, heures_manuelles=True, taux_horaire=40, unite="hr")
    totals = compute_budget_totals(_PROJET, [ligne])
    assert totals["heures_mo"] == pytest.approx(8.0)


# ── 7. Arrondi : demi vers le HAUT, jamais le round() natif (banker's) ──────
def test_arrondi_705_sur_96_donne_7_3_pas_7_34():
    h = heures_effectives("pi2", 0, False, 705, 96)
    assert h == pytest.approx(7.3)
    assert h != pytest.approx(7.34)


def test_arrondi_demi_haut_tie_exacte():
    # 29 / 4 = 7,25 EXACTEMENT (dyadique -> aucune imprécision flottante). Le
    # round() natif Python (half-to-even) donnerait 7,2 (documenté ci-dessous) ;
    # _js_round (demi vers le haut, miroir JS Math.round) DOIT donner 7,3.
    assert round(7.25, 1) == 7.2  # round() natif Python = INTERDIT ici (piège banker's)
    h = heures_effectives("pi2", 0, False, 29, 4)
    assert h == pytest.approx(7.3)


# ── Convergence entre chemins : compute_budget_totals et _line_total doivent
#    produire EXACTEMENT le même total de ligne pour une ligne « production ». ──
def test_line_total_converge_avec_compute_budget_totals():
    ligne = _ligne(id=7, qte=1800, prix_unitaire=2.5, heures=149.40, taux_horaire=50,
                   unite="pi2", production_valeur=96)
    totals = compute_budget_totals(_PROJET, [ligne])
    assert _line_total(False, ligne) == pytest.approx(totals["sous_total_avant_taxes"])
