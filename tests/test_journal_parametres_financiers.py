# -*- coding: utf-8 -*-
"""Journal des parametres financiers (7 oct. 2026, Simon : « go journal »).

Apres l'enquete 7,887 % du projet 290 (aucune trace possible : la base ne
gardait aucun historique), update_projet journalise chaque changement REEL
d'un parametre qui fait le prix. Ce banc eprouve la regle pure
(_changements_journal / _texte_journal), sans base ni reseau :
  - 8 et 8,000 ne sont PAS un changement ; 8 -> 7,887 en est un ;
  - un champ non envoye n'est pas journalise ;
  - NULL (categorie qui herite) <-> valeur est un changement ;
  - un champ hors liste (nom, statut…) n'est jamais journalise ;
  - la liste couvre exactement les 18 champs (16 pct_admin_*, mode, arrondi).
Autonome : python tests/test_journal_parametres_financiers.py
"""
import os
import sys
from decimal import Decimal

_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _RACINE)

from modules.ad_budget_api import (  # noqa: E402
    CHAMPS_JOURNAL_FINANCIER, _changements_journal, _texte_journal)


def test_la_liste_couvre_les_18_champs():
    assert len(CHAMPS_JOURNAL_FINANCIER) == 18, len(CHAMPS_JOURNAL_FINANCIER)
    assert "pct_admin_mode" in CHAMPS_JOURNAL_FINANCIER and "arrondi_dollar" in CHAMPS_JOURNAL_FINANCIER
    assert sum(1 for c in CHAMPS_JOURNAL_FINANCIER if c.startswith("pct_admin_") and c != "pct_admin_mode") == 16


def test_meme_valeur_autrement_ecrite_n_est_pas_un_changement():
    assert _changements_journal({"pct_admin_conditions": Decimal("8.000")}, {"pct_admin_conditions": 8}) == []
    assert _changements_journal({"arrondi_dollar": True}, {"arrondi_dollar": True}) == []


def test_le_cas_du_290_est_journalise():
    avant = {"pct_admin_conditions": Decimal("8.000"), "pct_admin_architecture": Decimal("8.000")}
    assert _changements_journal(avant, {"pct_admin_conditions": 7.89, "pct_admin_architecture": 7.887}) == [
        ("pct_admin_conditions", "8", "7.89"), ("pct_admin_architecture", "8", "7.887")]


def test_champ_non_envoye_ou_hors_liste_jamais_journalise():
    assert _changements_journal({"pct_admin_mecanique": 5}, {"nom": "X", "statut": "perdu"}) == []


def test_null_herite_vers_valeur_est_un_changement():
    assert _changements_journal({"pct_admin_conditions_mat": None}, {"pct_admin_conditions_mat": 12}) == [
        ("pct_admin_conditions_mat", None, "12")]


def test_texte_journal():
    assert _texte_journal(None) is None
    assert _texte_journal(False) == "false"
    assert _texte_journal("7,887") == "7.887"
    assert _texte_journal("ventile") == "ventile"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("OK  ", t.__name__)
    print("%d/%d verts" % (len(tests), len(tests)))
