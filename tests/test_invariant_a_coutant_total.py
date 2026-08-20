"""Invariant A — le total est le même partout (dans une seule langue).

Chantier « tests d'invariant sur les chiffres que Simon voit » (20 août 2026) :
la somme des sous-totaux par LOT (compute_lot_totals, groupement PHYSIQUE) doit
égaler la somme des sous-totaux par REGROUPEMENT CSI (compute_budget_totals,
groupement par DIVISION), qui doit égaler la somme des trois familles de coût
(matériaux + main-d'œuvre + sous-traitants) — trois façons INDÉPENDANTES de
sommer LES MÊMES lignes actives (aucune des trois n'est dérivée des deux
autres dans ce test : compute_lot_totals délègue à _line_total en groupant par
lot_id, compute_budget_totals recalcule from scratch en groupant par division
CSI ET en accumulant séparément les familles mat/mo/st).

⚠ Attention arrondi_dollar : l'arrondi (R()/_js_round) se fait PAR LIGNE, pas
sur le total — la somme de lignes arrondies ne vaut PAS forcément le total
qu'on obtiendrait en arrondissant la somme brute. compute_lot_totals ET
compute_budget_totals arrondissent TOUTES LES DEUX ligne par ligne (même
_line_total / R()) : leurs deux sommes doivent donc concorder EXACTEMENT,
arrondi ou pas. La comparaison avec snap_mat+snap_mo+snap_st (accumulée AVANT
l'arrondi par ligne) tolère, elle, un écart borné par le nombre de lignes
contributives — c'est la règle explicite que ce test encode, pas une
simplification.

Lancer : pytest tests/test_invariant_a_coutant_total.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from modules.ad_budget_api import compute_budget_totals  # noqa: E402
from modules.lots_calc import compute_lot_totals  # noqa: E402


def _ligne(**over):
    l = {
        "id": 1, "section": "07 21 00", "description": "Ligne test",
        "unite": "un", "qte": 0, "prix_unitaire": 0, "lot_id": None, "actif": True,
        "ajust_materiaux": 0, "heures": 0, "heures_manuelles": False, "taux_horaire": 0,
        "ajust_main_oeuvre": 0, "sous_traitant_montant": 0, "ajust_sous_traitant": 0,
        "ajustement_pct": 0, "production_valeur": None,
    }
    l.update(over)
    return l


# Jeu de lignes réaliste : 3 familles, 2 lots + 1 ligne hors-lot, une ligne
# inactive à exclure PARTOUT, une ligne calquée sur le cas réel de l'incident
# 2026-08-20 (isolant, production_valeur=96 pi²/h).
_LIGNES = [
    _ligne(id=1, section="03 30 00", description="Béton", unite="m3", qte=45, prix_unitaire=185.50,
           lot_id=1, heures=12, taux_horaire=65),
    _ligne(id=2, section="07 21 00", description="Isolant en panneaux rigides-plafond", unite="pi2",
           qte=1800, prix_unitaire=2.5, lot_id=1, heures=149.40, taux_horaire=50, production_valeur=96),
    _ligne(id=3, section="09 20 00", description="Gypse", unite="pi2", qte=12000, prix_unitaire=1.15,
           lot_id=2, heures=210, taux_horaire=42, ajust_materiaux=3),
    _ligne(id=4, section="23 10 00", description="Plomberie (S-T)", unite="un", qte=1, prix_unitaire=0,
           lot_id=2, sous_traitant_montant=45000, ajust_sous_traitant=1.5),
    _ligne(id=5, section="26 00 00", description="Électricité (S-T, hors-lot)", unite="un", qte=1,
           prix_unitaire=0, lot_id=None, sous_traitant_montant=32500),
    _ligne(id=6, section="31 00 00", description="Ligne INACTIVE — doit être exclue partout",
           unite="un", qte=999, prix_unitaire=999, lot_id=1, actif=False),
]
_LOTS = [{"id": 1, "nom": "Lot A", "nb_logements": 6, "ordre": 0},
         {"id": 2, "nom": "Lot B", "nb_logements": 4, "ordre": 1}]


def _actives(lignes):
    return [l for l in lignes if l.get("actif", True)]


def test_lot_vs_csi_vs_familles_convergent_sans_arrondi():
    lignes_actives = _actives(_LIGNES)
    projet = {"arrondi_dollar": False}
    lot = compute_lot_totals(_LIGNES, _LOTS, arrondi_dollar=False)
    budget = compute_budget_totals(projet, lignes_actives)

    total_csi = budget["non_grouped_total"] + sum(budget["group_subtotals"].values())
    total_familles = budget["snap_mat"] + budget["snap_mo"] + budget["snap_st"]

    # Groupement par LOT vs groupement par DIVISION CSI — deux découpages
    # orthogonaux des mêmes lignes actives, doivent tomber pile au même total.
    assert lot["grand_total"] == pytest.approx(total_csi, abs=1e-6), (
        f"grand_total (par lot) = {lot['grand_total']} != total CSI = {total_csi}")

    # Total (n'importe lequel des deux groupements) vs somme des 3 familles de
    # coût — troisième découpage indépendant (accumulateurs séparés, pas dérivés).
    assert total_csi == pytest.approx(total_familles, abs=1e-6), (
        f"total = {total_csi} != mat+mo+st = {total_familles}")


def test_ligne_inactive_exclue_de_toutes_les_sommes():
    # Preuve directe (pas seulement "le total est petit") : le total AVEC la
    # ligne inactive retirée de la liste == le total obtenu en la laissant
    # dedans (compute_lot_totals la filtre lui-même ; compute_budget_totals
    # ne voit que ce que l'appelant lui passe, donc _actives() en amont).
    projet = {"arrondi_dollar": False}
    lignes_actives = _actives(_LIGNES)
    lot_avec_inactive = compute_lot_totals(_LIGNES, _LOTS, arrondi_dollar=False)
    lot_sans_inactive = compute_lot_totals(lignes_actives, _LOTS, arrondi_dollar=False)
    assert lot_avec_inactive["grand_total"] == pytest.approx(lot_sans_inactive["grand_total"], abs=1e-6)

    budget = compute_budget_totals(projet, lignes_actives)
    total_csi = budget["non_grouped_total"] + sum(budget["group_subtotals"].values())
    # 999 × 999 (la ligne inactive) dépasserait de très loin ce total si elle
    # avait fuité dans compute_budget_totals.
    assert total_csi < 999 * 999


def test_lot_vs_csi_convergent_avec_arrondi_dollar_tolerance_sur_familles():
    # compute_lot_totals et compute_budget_totals arrondissent TOUTES LES DEUX
    # ligne par ligne (même R()/_js_round) : elles DOIVENT tomber pile au même
    # total même avec arrondi_dollar=True — c'est la comparaison qui NE
    # TOLÈRE AUCUN écart, contrairement à celle avec les familles ci-dessous.
    projet = {"arrondi_dollar": True}
    lignes_actives = _actives(_LIGNES)
    lot = compute_lot_totals(_LIGNES, _LOTS, arrondi_dollar=True)
    budget = compute_budget_totals(projet, lignes_actives)
    total_csi = budget["non_grouped_total"] + sum(budget["group_subtotals"].values())
    assert lot["grand_total"] == pytest.approx(total_csi, abs=1e-6)

    # Vs la somme des familles (snap_mat/mo/st, NON arrondie ligne par ligne) :
    # tolérance explicitement bornée par le nombre de lignes contributives (au
    # plus 0,50 $ d'écart par ligne — l'arrondi se fait par ligne, pas sur le
    # total, cf. l'avertissement en tête de fichier).
    total_familles = budget["snap_mat"] + budget["snap_mo"] + budget["snap_st"]
    tolerance = 0.5 * len(lignes_actives)
    ecart = abs(total_csi - total_familles)
    assert ecart <= tolerance, (
        f"écart {ecart:.2f} $ > tolérance {tolerance:.2f} $ ({len(lignes_actives)} lignes) — "
        "un écart AU-DELÀ de cette borne serait une vraie divergence, pas un artefact d'arrondi")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
