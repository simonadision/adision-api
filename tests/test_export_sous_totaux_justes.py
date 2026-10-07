# -*- coding: utf-8 -*-
"""TEMOIN -- l'export d'Ad BUD applique les MEMES trois regles que l'ecran.

TROUVE PAR PC2 LE 7 OCT 2026, en mesurant tout autre chose : la parite du pont
Ad BUD -> Ad EST. Le defaut etait EN AMONT, dans l'export lui-meme, et
**Ad CON le recevait depuis le Sprint 1.A**.

LA SOURCE UNIQUE `compute_budget_totals` (l. ~1392) -- celle qui alimente
l'ECRAN, le PDF et le push hub -- applique trois choses que l'export ignorait :

  1. `quantite_effective(qte, unite, qte_facteur)` : l'unite « % » et le
     FACTEUR d'unite ;
  2. `heures_effectives(...)` : l'unite « hr » et la MO par PRODUCTION ;
  3. `ajustement_pct` : l'ajustement de LIGNE.

CE QUE CA DONNAIT (les cinq cas ci-dessous sont ceux-la, chiffres) :
    contingence 2 % a 1 010 098 $ : 20 201,96 -> 2 020 196,00  (x100)
    unite « hr », heures = qte    :       960 -> 0
    MO par production, 10 u a 4/h :       200 -> 0
    facteur 1 139 plin x 6 mois   :    34 170 -> 5 695
    ajustement de ligne 5 %       :       525 -> 500
`contract_initial_amount` -- le CONTRAT INITIAL d'un chantier -- etait donc
faux des qu'un budget portait un de ces cas.

CE BANC TIENT DEUX CHOSES, ET PAS UNE DE PLUS :
  A. les trois fonctions PURES donnent bien les chiffres de l'ecran (on les
     importe, on les appelle : aucun oracle recopie) ;
  B. le CALCUL de l'export passe par elles (inspection de l'AST du bloc).

POURQUOI PAS UNE PARITE DE BOUT EN BOUT : `_payload_export_budget` ouvre une
connexion et interroge trois tables. La faire tourner demanderait une base ;
aucun connecteur n'en atteint une depuis la CI. **On ne simule pas** -- une
fixture qui ment fait accuser le code.

POURQUOI L'AST ET PAS UNE RECHERCHE DE TEXTE : l'en-tete du correctif CITE les
noms qu'on cherche, dans un commentaire de trente lignes. Un banc qui lit les
commentaires comme du code serait vert quoi qu'il arrive.
"""
import ast
import io
import os

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(RACINE, "modules", "ad_budget_api.py")

from modules.aggregates import heures_effectives, quantite_effective  # noqa: E402

FABRIQUE = "_payload_export_budget"


# ══════════════════════════════════════════════════════════════════════════
# A. LES CHIFFRES DE L'ECRAN -- fonctions pures, appelees pour de vrai
# ══════════════════════════════════════════════════════════════════════════

def test_unite_pourcent_une_contingence_ne_vaut_pas_cent_fois_trop():
    """Le cas de Simon du 1er oct : qte 2, unite %, PU 1 010 098 $."""
    montant = quantite_effective(2, "%", None) * 1010098
    assert round(montant, 2) == 20201.96


def test_facteur_dunite_les_mois_comptent():
    """Cloture de chantier : 1 139 plin, facteur 6 mois, 5 $/unite."""
    montant = quantite_effective(1139, "plin", 6) * 5
    assert round(montant, 2) == 34170.00


def test_unite_hr_les_heures_valent_la_quantite():
    assert heures_effectives("hr", 0, False, 12, None) == 12


def test_mo_par_production_dix_unites_a_quatre_a_lheure():
    assert heures_effectives("un", 0, False, 10, 4) == 2.5


def test_production_ECRASE_des_heures_tapees_a_la_main():
    """Regle confirmee par Simon le 14 aout 2026 : production renseignee =
    priorite absolue, `heures_manuelles` est SANS EFFET. C'est ce qui empeche
    une colonne `heures` desynchronisee de mentir au serveur (incident du
    20 aout, projet 290 : 39 219 $ et 463 h d'ecart)."""
    assert heures_effectives("un", 999, True, 10, 4) == 2.5


def test_le_pourcent_ne_touche_JAMAIS_les_heures():
    """Des lignes « Contremaitre » en % portent des heures : 320 h ne doivent
    pas devenir 3,2 h. C'est pourquoi quantite_effective ne sert qu'aux
    MONTANTS -- et pourquoi le correctif ne l'applique qu'aux materiaux."""
    assert heures_effectives("%", 320, True, 2, None) == 320


# ══════════════════════════════════════════════════════════════════════════
# B. L'EXPORT PASSE PAR CES FONCTIONS  (AST)
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def fabrique():
    arbre = ast.parse(io.open(SRC, encoding="utf-8").read())
    for n in ast.walk(arbre):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == FABRIQUE:
            return n
    raise AssertionError("%s introuvable : le banc n'a plus de sujet" % FABRIQUE)


def _appelle(noeud, nom):
    for n in ast.walk(noeud):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id == nom:
                return True
            if isinstance(f, ast.Attribute) and f.attr == nom:
                return True
    return False


def _lit_colonne(noeud, colonne):
    """`row.get("colonne")` quelque part dans le bloc."""
    for n in ast.walk(noeud):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" and n.args
                and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == colonne):
            return True
    return False


def test_lexport_applique_lunite_et_le_facteur(fabrique):
    """Mutant qui doit rougir : revenir a `qty_eff * prix_u`."""
    assert _appelle(fabrique, "quantite_effective"), (
        "l'export ne passe pas par quantite_effective : une contingence en %% "
        "part CENT FOIS trop grande vers Ad CON")
    assert _lit_colonne(fabrique, "qte_facteur"), (
        "l'export ne lit pas qte_facteur : 1 139 plin x 6 mois vaudraient "
        "5 695 $ au lieu de 34 170 $")


def test_lexport_applique_les_heures_effectives(fabrique):
    """Mutant qui doit rougir : revenir a `float(row.get("heures") or 0)`."""
    assert _appelle(fabrique, "_heures_effectives"), (
        "l'export ne passe pas par heures_effectives : l'unite « hr » et la MO "
        "par production tomberaient a 0")
    assert _lit_colonne(fabrique, "production_valeur"), (
        "l'export ne lit pas production_valeur : la MO calculee par rendement "
        "serait nulle")
    assert _lit_colonne(fabrique, "heures_manuelles"), (
        "l'export ne lit pas heures_manuelles : heures_effectives ne pourrait "
        "pas distinguer une saisie d'un report")


def test_lexport_applique_lajustement_de_ligne(fabrique):
    """Mutant qui doit rougir : retirer le bloc `if _adj:`."""
    assert _lit_colonne(fabrique, "ajustement_pct"), (
        "l'export ignore l'ajustement de ligne : 525 $ partiraient a 500 $")


def test_le_pourcent_ne_sapplique_PAS_aux_heures_dans_lexport(fabrique):
    """ANTI-SUR-CORRECTION. Le defaut inverse existe et il a deja coute une
    capture de Simon (16 sept. : main-d'oeuvre a 5 416,96 $ dans Ad CON contre
    41 049,00 $ dans Ad BUD). On verifie que le resultat de
    `quantite_effective` ne multiplie PAS le sous-total de main-d'oeuvre."""
    cibles = [n for n in ast.walk(fabrique)
              if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "mo_subtotal" for t in n.targets)]
    assert cibles, "mo_subtotal n'est plus affecte : le banc n'a plus de sujet"
    for n in cibles:
        assert not _appelle(n.value, "quantite_effective"), (
            "quantite_effective entre dans mo_subtotal : 320 h en %% "
            "deviendraient 3,2 h")


# ══════════════════════════════════════════════════════════════════════════
# ANTI-VACUITE
# ══════════════════════════════════════════════════════════════════════════

def test_une_colonne_INVENTEE_nest_pas_trouvee(fabrique):
    assert not _lit_colonne(fabrique, "colonne_qui_nexiste_pas_du_tout")


def test_une_fonction_INVENTEE_nest_pas_trouvee(fabrique):
    assert not _appelle(fabrique, "fonction_qui_nexiste_pas_du_tout")
