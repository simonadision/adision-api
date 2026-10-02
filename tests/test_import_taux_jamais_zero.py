# -*- coding: utf-8 -*-
"""TEMOIN -- les imports en lot ne posent plus jamais un taux horaire a 0.

Regle de Simon (9 sept 2026, #35 ; redemandee le 15 sept) : une ligne sans
taux prend celui du charpentier-menuisier compagnon. Les deux imports en lot
-- POST /budget/projects/from-viu-v2 et import_items_to_projet -- ecrivaient
encore `carte.get(section[:2], 0)` : 0 $/h pour une division non mappee ou
une section absente, rattrape seulement a l'ouverture suivante (#61).
Trouve par PC2 le 2 oct en preparant le banc taux_horaires_api, confirme
par PC3.

Les ATTENTES viennent du banc METIER cable test_taux_horaire_backfill_
ouverture.py (point 2 : « une division NON mappee tombe sur le repli
CHARPENTIER_C » ; TAUX_REPLI_CHARPENTIER = 48.50), pas de l'implementation.

Eprouve contre origin/main AVANT le correctif : ce fichier y echoue (les
fonctions n'existent pas, les deux sites ecrivent `, 0)`).
"""
import io
import os
import sys
from decimal import Decimal

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
os.environ.setdefault("JWT_SECRET", "test-import-taux")

from modules.taux_horaires_api import (  # noqa: E402
    METIER_REPLI, _load_taux_repli, taux_defaut_depuis_carte,
)

TAUX_REPLI_CHARPENTIER = Decimal("48.50")  # meme valeur que le banc metier
CARTE = {"06": Decimal("84.64"), "22": Decimal("80.00")}
SOURCE = io.open(os.path.join(RACINE, "modules", "ad_budget_api.py"), encoding="utf-8").read()


class FauxCurseur:
    def __init__(self, ligne):
        self.ligne, self.executees = ligne, []

    def execute(self, sql, params=()):
        self.executees.append((sql, params))

    def fetchone(self):
        return self.ligne

    def close(self):
        pass


class FausseConn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self, row_factory=None):
        return self.cur


def test_division_mappee_prend_son_taux():
    assert taux_defaut_depuis_carte("06 40 00", CARTE, TAUX_REPLI_CHARPENTIER) == Decimal("84.64")


def test_division_non_mappee_tombe_sur_le_repli_jamais_zero():
    assert taux_defaut_depuis_carte("99 10 0", CARTE, TAUX_REPLI_CHARPENTIER) == TAUX_REPLI_CHARPENTIER


def test_section_absente_tombe_sur_le_repli_jamais_zero():
    for section in (None, "", "   "):
        assert taux_defaut_depuis_carte(section, CARTE, TAUX_REPLI_CHARPENTIER) == TAUX_REPLI_CHARPENTIER, section


def test_section_sans_zero_de_tete_lue_comme_sa_division():
    # « 6 11 00.01 » : ecriture reelle trouvee en production. section[:2]
    # donnait « 6 » -> aucune division -> 0.
    assert taux_defaut_depuis_carte("6 11 00.01", CARTE, TAUX_REPLI_CHARPENTIER) == Decimal("84.64")


def test_zero_seulement_si_le_repli_lui_meme_manque():
    assert taux_defaut_depuis_carte("99 10 0", CARTE, None) == 0


def test_repli_charge_en_une_requete_sur_le_metier_de_repli():
    cur = FauxCurseur({"taux_col17": TAUX_REPLI_CHARPENTIER})
    assert _load_taux_repli(FausseConn(cur)) == TAUX_REPLI_CHARPENTIER
    assert len(cur.executees) == 1
    assert cur.executees[0][1] == (METIER_REPLI,) == ("CHARPENTIER_C",)


def test_les_deux_imports_utilisent_la_regle_et_plus_le_zero():
    assert "taux_default_map.get(" not in SOURCE, \
        "un import ecrit encore carte.get(..., 0) : 0 $/h pour une division non mappee"
    assert SOURCE.count("taux_defaut_depuis_carte(") == 2


def test_le_repli_est_charge_une_fois_avant_la_boucle_jamais_par_ligne():
    # Une requete par import, pas par item (1 000 items = 1 000 allers-retours
    # sinon -- cf. les 1 209 recherches a l'ouverture d'Ad BUD).
    lignes = SOURCE.splitlines()
    sites = [i for i, l in enumerate(lignes) if "taux_repli = _load_taux_repli(conn)" in l]
    assert len(sites) == 2
    for i in sites:
        voisinage = "\n".join(lignes[max(0, i - 4):i])
        assert "_load_taux_default_map(conn)" in voisinage, \
            "le repli doit se charger a cote de la carte, avant la boucle"


def test_seul_le_taux_change_jamais_les_heures():
    # La regle porte sur le TAUX : le resultat n'est assigne qu'a taux_horaire.
    for ligne in SOURCE.splitlines():
        if "taux_defaut_depuis_carte(" in ligne:
            assert ligne.strip().startswith("taux_horaire = "), ligne


if __name__ == "__main__":
    echecs = 0
    for nom, fn in sorted(globals().items()):
        if nom.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", nom)
            except Exception as e:
                echecs += 1
                print("FAIL", nom, ":", repr(e))
    print("---", "0 echec" if not echecs else "%d echec(s)" % echecs)
    sys.exit(1 if echecs else 0)
