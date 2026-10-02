"""Le FACTEUR D'UNITÉ doit bouger l'empreinte du budget (2 oct. 2026).

Contrairement aux champs NEUTRES (test_budget_fingerprint_champs_neutres.py),
qte_facteur ENTRE dans le montant : 1139 plin/mois × 6 mois × 5 $. Si un jour
il cessait d'entrer dans _line_total, un devis émis ne se régénérerait plus
après un changement de facteur — et personne ne le verrait. Ce banc ÉPROUVE la
chose (même ligne, facteur 1 -> 6, l'empreinte DOIT changer), au lieu de
vérifier seulement que la colonne est lue. Condition de PC3, 2 oct. 2026.

Lancer :  pytest tests/test_budget_fingerprint_qte_facteur.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import budget_fingerprint as FP  # noqa: E402

PROJET = {"arrondi_dollar": False, "pct_admin": 15, "pct_profit": 0}
CLOTURE = {"id": 1, "qte": 1139, "unite": "plin/mois", "prix_unitaire": 5, "ajust_materiaux": 0}


def test_le_facteur_multiplie_le_total_de_ligne():
    sans = FP._line_total(False, dict(CLOTURE))
    avec = FP._line_total(False, {**CLOTURE, "qte_facteur": 6})
    assert round(sans, 2) == 5695.00
    assert round(avec, 2) == 34170.00  # 1139 × 6 × 5 $


def test_changer_le_facteur_change_l_empreinte():
    base = [dict(CLOTURE)]
    six = [{**CLOTURE, "qte_facteur": 6}]
    assert FP.compute_budget_fingerprint(PROJET, base, 0) != FP.compute_budget_fingerprint(PROJET, six, 0)


def test_facteur_null_ou_un_ne_change_rien():
    base = FP.compute_budget_fingerprint(PROJET, [dict(CLOTURE)], 0)
    for f in (None, 1, "1"):
        lignes = [copy.deepcopy({**CLOTURE, "qte_facteur": f})]
        assert FP.compute_budget_fingerprint(PROJET, lignes, 0) == base


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
