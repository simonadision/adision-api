# -*- coding: utf-8 -*-
"""TEMOIN — les pastilles Ad BUD suivent le classement UNIQUE du hub.

16 septembre 2026, Simon : « Quand un projet est deplacer, il est mise a jour
deplacer dans tous les modules. Donc si un projet en soumission est deplacé
dans projet fermé dans le hub, il sera deplacé fermé dans tous les modules ou
il apparait... ».

Structurel + correspondances (pas de base) :
  - lecture : GET /budget/projets range la carte d'un budget lie selon le
    `classement` renvoye par revision-meta ;
  - ecriture : glisser une carte liee appelle PATCH .../classement au hub
    AVANT d'ecrire localement (fail-closed).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "modules", "ad_budget_api.py")


def _src():
    return open(SRC, encoding="utf-8").read()


def test_correspondances_aller_retour():
    from modules import ad_budget_api as A
    for classement, categorie in A._CLASSEMENT_HUB_VERS_CATEGORIE.items():
        assert A._CATEGORIE_VERS_CLASSEMENT_HUB[categorie] == classement
    # Les trois pastilles visibles couvrent les trois classements du hub.
    assert set(A._CLASSEMENT_HUB_VERS_CATEGORIE) == {"soumission", "obtenu", "ferme"}
    assert set(A._CATEGORIE_VERS_CLASSEMENT_HUB) <= A.ALLOWED_CATEGORIES_AFFICHAGE


def test_la_liste_lit_le_classement_du_hub():
    fonction = _src().split("def get_projets(", 1)[1].split("@router.", 1)[0]
    assert 'm.get("classement")' in fonction, (
        "la liste Ad BUD ne range plus ses cartes sur le classement du hub : "
        "un projet deplace dans Ad HUB resterait dans son ancienne pastille"
    )


def test_glisser_ecrit_au_hub_avant_la_base_locale():
    fonction = _src().split("def update_projet(", 1)[1].split("@router.", 1)[0]
    i_hub = fonction.find("hub_service.set_project_classement(")
    i_update = fonction.find("UPDATE ad_budget.projets SET")
    assert i_hub != -1, "glisser une carte liee n'ecrit plus le classement au hub"
    assert i_update != -1 and i_hub < i_update, (
        "le hub doit etre ecrit AVANT la base locale (fail-closed)"
    )


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
