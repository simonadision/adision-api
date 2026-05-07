"""Golden master tests pour modules/aggregates.py.

Portage 1-pour-1 des 10 tests JS dans
packages/aggregates/__tests__/computeAggregates.test.js (référence).

Lancer depuis la racine du backend :
    python -m unittest tests.test_aggregates
"""
import math
import os
import sys
import unittest

# Permettre l'import "from modules.aggregates" quand on lance ce fichier
# directement (python tests/test_aggregates.py) en plus de "python -m
# unittest tests.test_aggregates" depuis la racine.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.aggregates import compute_aggregates  # noqa: E402


def _round2(n):
    """Réplique du round2 JS (Math.round half-up vers +infini, pas banker's)."""
    return math.floor(n * 100 + 0.5) / 100


class TestComputeAggregates(unittest.TestCase):

    def test_01_projet_vide_totaux_a_zero_ratios_null(self):
        r = compute_aggregates([], {})
        self.assertEqual(r["totals"]["general"], 0)
        self.assertIsNone(r["ratios"]["materiaux_pct"])
        self.assertIsNone(r["ratios"]["main_oeuvre_pct"])
        self.assertIsNone(r["ratios"]["sous_traitance_pct"])
        self.assertEqual(r["by_section_csi"], [])
        self.assertEqual(r["counts"]["nb_lignes"], 0)
        self.assertEqual(r["counts"]["nb_sections_csi"], 0)
        self.assertEqual(r["schema_version"], 1)

    def test_02_une_ligne_100pct_materiaux(self):
        lines = [
            {"csi_code": "03", "mat": {"cout": 10, "qte": 10, "ajust_pct": 0}, "mo": {}, "st": {}}
        ]
        r = compute_aggregates(lines, {})
        self.assertEqual(r["totals"]["materiaux"], 100)
        self.assertEqual(r["totals"]["general"], 100)
        self.assertEqual(r["ratios"]["materiaux_pct"], 100)
        self.assertEqual(r["ratios"]["main_oeuvre_pct"], 0)
        self.assertEqual(r["ratios"]["sous_traitance_pct"], 0)

    def test_03_projet_equilibre_33_33_33(self):
        lines = [{
            "csi_code": "03",
            "mat": {"cout": 100, "qte": 1, "ajust_pct": 0},
            "mo": {"heures": 10, "taux": 10, "ajust_pct": 0},
            "st": {"montant": 100, "ajust_pct": 0},
        }]
        r = compute_aggregates(lines, {})
        self.assertAlmostEqual(r["ratios"]["materiaux_pct"], 33.33, places=1)
        self.assertAlmostEqual(r["ratios"]["main_oeuvre_pct"], 33.33, places=1)
        self.assertAlmostEqual(r["ratios"]["sous_traitance_pct"], 33.33, places=1)

    def test_04_ajustement_plus_10pct_sur_mat(self):
        lines = [
            {"csi_code": "03", "mat": {"cout": 100, "qte": 1, "ajust_pct": 10}, "mo": {}, "st": {}}
        ]
        r = compute_aggregates(lines, {})
        self.assertEqual(r["totals"]["materiaux"], 110)
        self.assertEqual(r["adjustments"]["materiaux_avg_pct"], 10)
        self.assertIsNone(r["adjustments"]["main_oeuvre_avg_pct"])
        self.assertIsNone(r["adjustments"]["sous_traitance_avg_pct"])

    def test_05_meme_division_csi_regroupee(self):
        lines = [
            {"csi_code": "03 31 00", "mat": {"cout": 50, "qte": 1, "ajust_pct": 0}, "mo": {}, "st": {}},
            {"csi_code": "03 30 00", "mat": {"cout": 50, "qte": 1, "ajust_pct": 0}, "mo": {}, "st": {}},
        ]
        r = compute_aggregates(lines, {})
        self.assertEqual(len(r["by_section_csi"]), 1)
        self.assertEqual(r["by_section_csi"][0]["code"], "03")
        self.assertEqual(r["by_section_csi"][0]["nom"], "Béton")
        self.assertEqual(r["by_section_csi"][0]["total"], 100)
        self.assertEqual(r["counts"]["nb_sections_csi"], 1)

    def test_06_ligne_sans_csi_code_regroupee_sous_00(self):
        lines = [
            {"mat": {"cout": 50, "qte": 1, "ajust_pct": 0}, "mo": {}, "st": {}}
        ]
        r = compute_aggregates(lines, {})
        self.assertEqual(r["by_section_csi"][0]["code"], "00")
        self.assertEqual(r["by_section_csi"][0]["nom"], "Non classé")

    def test_07_superficie_null_cout_par_pi2_null(self):
        lines = [
            {"csi_code": "03", "mat": {"cout": 100, "qte": 1, "ajust_pct": 0}, "mo": {}, "st": {}}
        ]
        r = compute_aggregates(lines, {"superficie_m2": None})
        self.assertIsNone(r["derived"]["cout_par_pi2"])

        r2 = compute_aggregates(lines, {})
        self.assertIsNone(r2["derived"]["cout_par_pi2"])

        r3 = compute_aggregates(lines, {"superficie_m2": 0})
        self.assertIsNone(r3["derived"]["cout_par_pi2"])

    def test_08_materiaux_zero_ratio_mo_sur_mat_null(self):
        lines = [
            {"csi_code": "03", "mat": {}, "mo": {"heures": 10, "taux": 10, "ajust_pct": 0}, "st": {}}
        ]
        r = compute_aggregates(lines, {})
        self.assertEqual(r["totals"]["materiaux"], 0)
        self.assertIsNone(r["derived"]["ratio_mo_sur_mat"])

    def test_09_by_type_st_somme_egale_totals_sous_traitance(self):
        lines = [
            {"csi_code": "23", "type_st": "soumission", "mat": {}, "mo": {}, "st": {"montant": 1000, "ajust_pct": 0}},
            {"csi_code": "23", "type_st": "soumission", "mat": {}, "mo": {}, "st": {"montant": 500, "ajust_pct": 0}},
            {"csi_code": "23", "type_st": "soumission", "mat": {}, "mo": {}, "st": {"montant": 200, "ajust_pct": 0}},
            {"csi_code": "26", "type_st": "bsdq", "mat": {}, "mo": {}, "st": {"montant": 800, "ajust_pct": 0}},
        ]
        r = compute_aggregates(lines, {})
        self.assertEqual(r["by_type_st"]["soumission"], 1700)
        self.assertEqual(r["by_type_st"]["bsdq"], 800)
        self.assertEqual(r["by_type_st"]["budget"], 0)
        self.assertEqual(r["by_type_st"]["allocation"], 0)
        total = sum(r["by_type_st"].values())
        self.assertLess(abs(total - r["totals"]["sous_traitance"]), 0.01)

    def test_10_invariants_50_lignes_mixtes(self):
        lines = []
        for i in range(50):
            lines.append({
                "csi_code": ["03", "23", "26", "07", "09"][i % 5],
                "type_st": ["budget", "soumission", "bsdq", "allocation"][i % 4],
                "mat": {"cout": 10 + i, "qte": 2, "ajust_pct": 5},
                "mo": {"heures": 5, "taux": 50, "ajust_pct": 8},
                "st": {"montant": 100 + i * 10, "ajust_pct": 3},
            })
        r = compute_aggregates(lines, {"superficie_m2": 200})

        sum_sec = sum(s["total"] for s in r["by_section_csi"])
        sum_type_st = sum(r["by_type_st"].values())
        self.assertLess(abs(sum_sec - r["totals"]["general"]), 0.01)
        self.assertLess(abs(sum_type_st - r["totals"]["sous_traitance"]), 0.01)
        self.assertEqual(r["derived"]["cout_par_pi2"], _round2(r["totals"]["general"] / 200))
        self.assertEqual(r["counts"]["nb_lignes"], 50)
        self.assertEqual(r["counts"]["nb_sections_csi"], 5)
        self.assertEqual(r["adjustments"]["materiaux_avg_pct"], 5)
        self.assertEqual(r["adjustments"]["main_oeuvre_avg_pct"], 8)
        self.assertEqual(r["adjustments"]["sous_traitance_avg_pct"], 3)


if __name__ == "__main__":
    unittest.main()
