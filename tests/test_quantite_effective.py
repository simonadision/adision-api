"""Unité « % » (1er oct. 2026, Simon : « l'unité % est censée affecter la
quantité en pourcentage »). Miroir de packages/aggregates/__tests__/
quantiteEffective.test.js (monorepo) : mêmes cas."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.aggregates import quantite_effective  # noqa: E402
from modules.budget_fingerprint import _line_total  # noqa: E402


def test_unite_pourcent_est_un_pourcentage():
    assert quantite_effective(2, "%") == 0.02
    assert quantite_effective("4", " % ") == 0.04


def test_autres_unites_inchangees():
    assert quantite_effective(2, "global") == 2
    assert quantite_effective(2, None) == 2


def test_cas_de_simon_contingence():
    # Ligne Contingence, qté 2, unité %, coût unitaire 1 010 098 $.
    t = _line_total(False, {"qte": 2, "unite": "%", "prix_unitaire": 1010098})
    assert abs(t - 20201.96) < 1e-6, t


def test_contremaitre_en_pourcent_les_heures_ne_bougent_pas():
    # Budget 318 (copie) : trois « Contremaître » en unité %, 320 h chacune
    # au plus — leurs heures ne doivent JAMAIS être divisées par 100.
    t = _line_total(False, {"qte": 320, "unite": "%", "heures": 320, "taux_horaire": 80})
    assert t == 320 * 80, t


def test_facteur_d_unite_multiplie_la_quantite():
    # Clôture de chantier : 1139 plin/mois, 6 mois.
    assert quantite_effective(1139, "plin/mois", 6) == 6834
    assert quantite_effective("1139", "plin/mois", "6") == 6834


def test_facteur_absent_nul_ou_negatif_vaut_un():
    for f in (None, "", 0, -3, "abc"):
        assert quantite_effective(1139, "plin/mois", f) == 1139


def test_facteur_et_pourcentage_se_composent():
    assert quantite_effective(2, "%", 3) == 0.06
