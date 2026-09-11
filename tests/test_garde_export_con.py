# -*- coding: utf-8 -*-
"""La garde de GET /budget/projets/{id}/export-for-con parle-t-elle encore le
vocabulaire des statuts d'Ad BUD ?

POURQUOI CE FICHIER EXISTE (10 septembre 2026). La garde comparait
litteralement a 'adjuge'. Le renommage du 9 septembre a efface cette valeur de
la base : plus un seul projet ne pouvait la porter. Mesure de production ce
matin -- en_soumission 20, archive 1, 'adjuge' ZERO. Donc export-for-con
repondait 403 a TOUS les projets, sans exception, et Ad CON avalait ce 403 en
silence : Simon ouvrait un chantier et voyait un ecran vide, sans un mot.
Personne n'a rien vu pendant une journee parce que RIEN ne rejouait la
decision de cette garde.

Ce fichier la rejoue. Il n'ouvre aucune connexion, ne monte pas l'app : il LIT
la constante et la condition telles qu'elles sont ecrites dans le module (par
AST, pas par import -- ad_budget_api tire fastapi/psycopg), puis rejoue la
decision statut par statut. Un futur renommage qui oublierait de repasser ici
rougit.

Eprouve a l'envers le jour de son ecriture : garde remise en `!=` -> 1 echec ;
'en_execution' retire de STATUTS_EXPORT_CON et de DEFINITIVE_STATUSES ->
4 echecs. Une assertion qui n'a jamais echoue ne prouve rien.
"""
import ast
import io
import os

import pytest

_MODULE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "modules",
    "ad_budget_api.py",
)


def _constantes_et_garde():
    tree = ast.parse(io.open(_MODULE, encoding="utf-8").read())
    consts = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            cible = node.targets[0]
            if isinstance(cible, ast.Name) and cible.id in (
                "STATUTS_EXPORT_CON",
                "ALLOWED_STATUTS",
                "DEFINITIVE_STATUSES",
            ):
                consts[cible.id] = ast.literal_eval(node.value)

    # La condition reelle de la garde, dans export-for-con :
    #     if projet["statut"] not in STATUTS_EXPORT_CON:
    operateur = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)):
            continue
        c = node.test
        gauche = c.left
        if (
            isinstance(gauche, ast.Subscript)
            and isinstance(gauche.value, ast.Name)
            and gauche.value.id == "projet"
            and isinstance(gauche.slice, ast.Constant)
            and gauche.slice.value == "statut"
            and len(c.ops) == 1
            and isinstance(c.comparators[0], ast.Name)
            and c.comparators[0].id == "STATUTS_EXPORT_CON"
        ):
            operateur = type(c.ops[0]).__name__
    return consts, operateur


CONSTS, OPERATEUR = _constantes_et_garde()


def test_la_garde_lit_un_ensemble_pas_une_valeur():
    """Une egalite sur UN statut est exactement le defaut d'origine : elle ne
    survit pas au premier renommage, et elle refuse le second statut gagnant."""
    assert OPERATEUR == "NotIn", (
        "la garde de export-for-con ne compare plus projet['statut'] "
        "NOT IN STATUTS_EXPORT_CON (trouve : %r)" % OPERATEUR
    )


def test_les_statuts_exportables_sont_les_deux_faces_d_un_projet_gagne():
    assert CONSTS["STATUTS_EXPORT_CON"] == {"en_cours", "en_execution"}


def test_aucun_statut_exportable_hors_whitelist():
    """Un statut exportable absent d'ALLOWED_STATUTS serait inatteignable :
    l'API refuserait de l'ecrire, donc la garde ne s'ouvrirait jamais."""
    assert CONSTS["STATUTS_EXPORT_CON"] <= CONSTS["ALLOWED_STATUTS"]


def test_adjuge_a_disparu_des_whitelists():
    """La valeur n'existe plus en base depuis la migration du 9 septembre."""
    assert "adjuge" not in CONSTS["ALLOWED_STATUTS"]
    assert "adjuge" not in CONSTS["STATUTS_EXPORT_CON"]


def test_en_execution_fige_aussi_le_budget_dans_un_snapshot():
    """Sans lui, un projet passe droit de la soumission au chantier n'aurait
    jamais son snapshot, et Ad ANA perdrait ce budget-la."""
    assert "en_execution" in CONSTS["DEFINITIVE_STATUSES"]


@pytest.mark.parametrize(
    "statut,doit_refuser",
    [
        ("en_soumission", True),   # rien n'est encore gagne
        ("en_cours", False),
        ("en_execution", False),
        ("perdu", True),           # on ne suit pas les couts d'un chantier qu'on ne fera pas
        ("archive", True),         # un projet range n'est pas un chantier qui demarre
    ],
)
def test_decision_statut_par_statut(statut, doit_refuser):
    refuse = statut not in CONSTS["STATUTS_EXPORT_CON"]
    assert refuse is doit_refuser


def test_les_cinq_statuts_de_production_sont_tous_couverts():
    """Si un sixieme statut apparait sans passer ici, ce test le dit -- au lieu
    de le laisser tomber en silence du mauvais cote de la garde."""
    assert CONSTS["ALLOWED_STATUTS"] == {
        "en_soumission", "en_cours", "en_execution", "perdu", "archive",
    }
