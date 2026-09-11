# -*- coding: utf-8 -*-
"""LE CHEMIN DU RAPPORT EXCLUT-IL LES LIGNES DESACTIVEES ?

POURQUOI CE FICHIER EXISTE. Mesure du 10 septembre 2026 : en remplacant, dans
`modules/ad_budget_api.py`, le filtre

    raw_lignes_actives = [l for l in lignes_calc if l.get("actif", True)]
par
    raw_lignes_actives = list(lignes_calc)

— autrement dit en faisant compter au rapport les lignes que Simon a
DESACTIVEES — aucun controle lancable ne rougissait :

    test_invariant_a_coutant_total.py    3 passed   VERT
    test_pdf_critical.py                18 passed   VERT

Le contraste avec le chemin des LOTS etait net : la meme mutation sur
`modules/lots_calc.py` (`if not l.get("actif", True):` -> `if False:`) fait
rougir `test_invariant_a` 3/3 et `test_lots_calc` 2/13. Le chemin des lots
etait garde, le chemin du RAPPORT ne l'etait pas — et c'est le rapport que
Simon regarde.

CE QUE CE FICHIER GARDE. `_fetch_lot_ventilation_calc` est le chargement +
calcul PARTAGE de la ventilation par lot : un seul SELECT, utilise par le PDF
« Ventilation par lot », par la trappe reportlab, et par `emit_report_to_hub`.
C'est lui qui applique le filtre `actif`. On l'appelle directement avec une
fausse connexion (aucune base, aucun reseau) et on exige que le montant de la
ligne desactivee ne soit NULLE PART dans le recap financier.

Lancer : python tests/test_rapport_ligne_inactive.py
         pytest tests/test_rapport_ligne_inactive.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_get_conn, make_projet, make_ligne  # noqa: E402
from modules.ad_budget_api import _fetch_lot_ventilation_calc  # noqa: E402

# Montant reconnaissable, choisi pour ne ressembler a aucun autre total du jeu :
# 7 x 11 000 = 77 000 $ de materiaux sur la SEULE ligne desactivee.
QTE_INACTIVE = 7
PRIX_INACTIVE = 11000
MONTANT_INACTIVE = QTE_INACTIVE * PRIX_INACTIVE  # 77 000 $

# Deux lignes actives, petites et distinctes.
ACTIVE_1 = 10 * 50     # 500 $
ACTIVE_2 = 4 * 25      # 100 $
TOTAL_ACTIF = ACTIVE_1 + ACTIVE_2  # 600 $


def _donnees():
    lignes = [
        make_ligne(id=1, section="02 00 00", qte=10, prix_unitaire=50,
                   lot_id=None, actif=True, production_valeur=0,
                   heures_manuelles=False, a_completer=False),
        make_ligne(id=2, section="03 00 00", qte=4, prix_unitaire=25,
                   lot_id=None, actif=True, production_valeur=0,
                   heures_manuelles=False, a_completer=False),
        make_ligne(id=3, section="09 00 00", description="LIGNE DESACTIVEE",
                   qte=QTE_INACTIVE, prix_unitaire=PRIX_INACTIVE,
                   lot_id=None, actif=False, production_valeur=0,
                   heures_manuelles=False, a_completer=False),
    ]
    return {
        "ad_budget.projets": ("one", make_projet(arrondi_dollar=False)),
        "ad_budget.lots": ("all", []),
        "budget_lignes": ("all", lignes),
    }


def _recap():
    _projet, _lignes, _lot, budget_totals = _fetch_lot_ventilation_calc(
        make_get_conn(_donnees()), 1)
    return budget_totals


def test_le_recap_ignore_la_ligne_desactivee():
    """Le sous-total avant taxes vaut EXACTEMENT les lignes actives."""
    recap = _recap()
    sous_total = float(recap["sous_total_avant_taxes"])
    assert abs(sous_total - TOTAL_ACTIF) < 1e-6, (
        "sous-total avant taxes attendu %.2f (les 2 lignes actives), obtenu "
        "%.2f — la ligne desactivee de %d $ a fui dans le rapport"
        % (TOTAL_ACTIF, sous_total, MONTANT_INACTIVE))


def test_le_montant_desactive_n_apparait_dans_aucun_champ_du_recap():
    """Preuve par balayage : le montant de la ligne morte n'est NULLE PART.

    Le sous-total seul ne suffirait pas — une fuite pourrait n'atteindre que
    le detail materiaux, ou le total APRES taxes, sans bouger le champ
    verifie ci-dessus."""
    recap = _recap()
    coupables = []
    for cle, val in recap.items():
        if isinstance(val, (int, float)) and abs(float(val)) >= MONTANT_INACTIVE:
            coupables.append("%s = %s" % (cle, val))
    assert not coupables, (
        "le montant de la ligne desactivee (%d $) transparait dans le recap "
        "financier : %s" % (MONTANT_INACTIVE, " ; ".join(coupables)))


def test_la_ligne_desactivee_est_bien_dans_les_lignes_chargees():
    """Garde-fou du garde-fou. Si le SELECT cessait de RAMENER la ligne
    desactivee, les deux tests ci-dessus passeraient au vert sans rien
    prouver : ils verifieraient l'absence d'une ligne qui n'a jamais ete la.
    C'est la troisieme famille du noyau — l'assertion rendue inatteignable
    par la fixture."""
    _projet, lignes_calc, _lot, _recap_ = _fetch_lot_ventilation_calc(
        make_get_conn(_donnees()), 1)
    inactives = [l for l in lignes_calc if not l.get("actif", True)]
    assert len(inactives) == 1, (
        "le jeu de test doit charger EXACTEMENT une ligne desactivee, "
        "sinon les autres tests ne prouvent rien (trouve : %d)" % len(inactives))


if __name__ == "__main__":
    echecs = 0
    for fn in (test_le_recap_ignore_la_ligne_desactivee,
               test_le_montant_desactive_n_apparait_dans_aucun_champ_du_recap,
               test_la_ligne_desactivee_est_bien_dans_les_lignes_chargees):
        try:
            fn()
            print("  [OK] %s" % fn.__name__)
        except AssertionError as e:
            echecs += 1
            print("  [ERREUR] %s\n         -> %s" % (fn.__name__, e))
    print("\n%d/3 tests rapport vs ligne desactivee OK" % (3 - echecs))
    sys.exit(1 if echecs else 0)
