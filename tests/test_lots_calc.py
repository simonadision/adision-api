"""Tests pour modules/lots_calc.py — sous-totaux de lot et total projet.

Purs (aucune DB) : lancer avec
    python -m unittest tests.test_lots_calc
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.lots_calc import compute_lot_totals  # noqa: E402


def _ligne(lot_id=None, qte=1, prix_unitaire=100, actif=True, **kw):
    base = {
        "id": kw.pop("id", 1),
        "lot_id": lot_id,
        "qte": qte,
        "prix_unitaire": prix_unitaire,
        "ajust_materiaux": 0,
        "heures": 0,
        "taux_horaire": 0,
        "ajust_main_oeuvre": 0,
        "sous_traitant_montant": 0,
        "ajust_sous_traitant": 0,
        "ajustement_pct": 0,
        "unite": "un",
        "heures_manuelles": False,
        "actif": actif,
    }
    base.update(kw)
    return base


class TestComputeLotTotals(unittest.TestCase):
    def test_01_projet_sans_lot_aucune_regression(self):
        """Aucun lot défini, toutes les lignes hors-lot -> grand_total = somme
        des lignes, lots = [] (comportement identique à avant l'ajout des lots)."""
        lignes = [_ligne(id=1, qte=2, prix_unitaire=50), _ligne(id=2, qte=1, prix_unitaire=30)]
        r = compute_lot_totals(lignes, [])
        self.assertEqual(r["lots"], [])
        self.assertEqual(r["hors_lot"]["nb_lignes"], 2)
        self.assertEqual(r["hors_lot"]["sous_total"], 130.0)
        self.assertEqual(r["grand_total"], 130.0)

    def test_02_deux_lots_sous_totaux_independants(self):
        lots = [{"id": 10, "nom": "AB DEL", "nb_logements": 12, "ordre": 0},
                {"id": 20, "nom": "CD DEL", "nb_logements": 8, "ordre": 1}]
        lignes = [
            _ligne(id=1, lot_id=10, qte=10, prix_unitaire=100),   # 1000
            _ligne(id=2, lot_id=10, qte=5, prix_unitaire=20),     # 100
            _ligne(id=3, lot_id=20, qte=3, prix_unitaire=50),     # 150
        ]
        r = compute_lot_totals(lignes, lots)
        by_id = {l["id"]: l for l in r["lots"]}
        self.assertEqual(by_id[10]["sous_total"], 1100.0)
        self.assertEqual(by_id[10]["nb_lignes"], 2)
        self.assertEqual(by_id[20]["sous_total"], 150.0)
        self.assertEqual(r["hors_lot"]["sous_total"], 0.0)
        self.assertEqual(r["grand_total"], 1250.0)

    def test_03_lignes_hors_lot_comptent_dans_le_total_projet(self):
        lots = [{"id": 10, "nom": "AB DEL", "nb_logements": None, "ordre": 0}]
        lignes = [_ligne(id=1, lot_id=10, qte=1, prix_unitaire=1000),
                  _ligne(id=2, lot_id=None, qte=1, prix_unitaire=200)]
        r = compute_lot_totals(lignes, lots)
        self.assertEqual(r["hors_lot"]["sous_total"], 200.0)
        self.assertEqual(r["grand_total"], 1200.0)

    def test_04_ligne_inactive_exclue_du_sous_total_et_du_total(self):
        lots = [{"id": 10, "nom": "AB DEL", "nb_logements": None, "ordre": 0}]
        lignes = [_ligne(id=1, lot_id=10, qte=1, prix_unitaire=1000, actif=True),
                  _ligne(id=2, lot_id=10, qte=1, prix_unitaire=500, actif=False)]
        r = compute_lot_totals(lignes, lots)
        self.assertEqual(r["lots"][0]["sous_total"], 1000.0)
        self.assertEqual(r["lots"][0]["nb_lignes"], 1)
        self.assertEqual(r["grand_total"], 1000.0)

    def test_05_lot_vide_sous_total_zero(self):
        """Un lot fraîchement créé (aucune ligne) -> apparaît avec sous_total=0,
        pas absent de la liste (l'UI doit pouvoir afficher la section vide)."""
        lots = [{"id": 10, "nom": "CS H", "nb_logements": 4, "ordre": 0}]
        r = compute_lot_totals([], lots)
        self.assertEqual(len(r["lots"]), 1)
        self.assertEqual(r["lots"][0]["sous_total"], 0.0)
        self.assertEqual(r["lots"][0]["nb_lignes"], 0)
        self.assertEqual(r["grand_total"], 0.0)

    def test_06_tri_par_ordre_puis_id(self):
        lots = [{"id": 30, "nom": "CC-S", "nb_logements": None, "ordre": 2},
                {"id": 10, "nom": "AB DEL", "nb_logements": None, "ordre": 0},
                {"id": 20, "nom": "CD DEL", "nb_logements": None, "ordre": 1}]
        r = compute_lot_totals([], lots)
        self.assertEqual([l["id"] for l in r["lots"]], [10, 20, 30])

    def test_07_mat_mo_st_combines_comme_getRow_front(self):
        """Une ligne mixte materiel + main-d'oeuvre + sous-traitant doit
        contribuer la somme des 3 familles, exactement comme getRow() côté
        client (App.jsx) — pas seulement la colonne generee `total` (materiel
        seul) de budget_lignes."""
        lots = [{"id": 10, "nom": "AB DEL", "nb_logements": None, "ordre": 0}]
        lignes = [_ligne(
            id=1, lot_id=10, qte=2, prix_unitaire=10,          # mat: 20
            heures=5, taux_horaire=20,                          # mo: 100
            sous_traitant_montant=30,                           # st: 30
        )]
        r = compute_lot_totals(lignes, lots)
        self.assertEqual(r["lots"][0]["sous_total"], 150.0)

    def test_08_duplication_independante_modifier_une_copie_ne_change_pas_la_source(self):
        """Simule Phase 2 : dupliquer un lot copie les valeurs, mais les deux
        jeux de lignes sont ensuite INDÉPENDANTS -- modifier une ligne du lot
        copié ne doit rien changer au sous-total du lot source."""
        lots = [{"id": 10, "nom": "AB DEL", "nb_logements": 12, "ordre": 0},
                {"id": 11, "nom": "AB DEL (copie)", "nb_logements": 12, "ordre": 1}]
        source_ligne = _ligne(id=1, lot_id=10, qte=10, prix_unitaire=100)
        copie_ligne = dict(source_ligne)
        copie_ligne["id"] = 2
        copie_ligne["lot_id"] = 11
        lignes = [source_ligne, copie_ligne]

        r_avant = compute_lot_totals(lignes, lots)
        self.assertEqual(r_avant["lots"][0]["sous_total"], 1000.0)
        self.assertEqual(r_avant["lots"][1]["sous_total"], 1000.0)

        # Le client négocie un prix différent sur la COPIE seulement.
        copie_ligne["prix_unitaire"] = 60  # 10 * 60 = 600

        r_apres = compute_lot_totals(lignes, lots)
        self.assertEqual(r_apres["lots"][0]["sous_total"], 1000.0, "le lot source ne doit pas bouger")
        self.assertEqual(r_apres["lots"][1]["sous_total"], 600.0)


if __name__ == "__main__":
    unittest.main()
