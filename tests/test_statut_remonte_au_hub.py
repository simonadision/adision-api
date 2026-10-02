# -*- coding: utf-8 -*-
"""TEMOIN -- un changement de STATUT dans Ad BUD remonte au hub.

Simon, 1er oct 2026 : « Hub est la verite » (20:08), « Modif dans Bud remonte
au hub . Modif dans hub remonte dans Bud.... Systeme bi directionnel qui
defini la structure ad Flo » (20:30).

CE QUE CE CAS VERROUILLE, ET CE QUE SON ABSENCE A COUTE.

Le garde du classement unique existe depuis le 16 septembre, mais il ne
regardait QUE `categorie_affichage` -- la pastille. **Un PATCH qui ecrivait
`statut` directement passait a cote** : Ad BUD l'enregistrait chez lui, le hub
n'en savait rien.

Mesure du 1er octobre : **17 projets sur 23 portaient un statut different
entre le hub et Ad BUD** -- dont un chantier de 3 M$ que le hub savait GAGNE
et qu'Ad BUD croyait en soumission pendant que le charge de projet y
travaillait. Et comme la photo Ad ANA se declenche sur le statut LOCAL,
aucune photo n'a ete figee : on a cru pendant des jours qu'aucun projet
n'avait ete gagne, alors qu'Ad BUD ne le savait pas.

ON LIT LE SOURCE, et c'est voulu. Rejouer la vraie route demanderait une base,
un hub et un jeton. Ce qu'on verifie ici est une propriete du CODE : que la
derivation du classement regarde AUSSI le statut, et qu'elle ne le fasse que
s'il CHANGE. Un cas de comportement ne couvrirait que le chemin qu'on aurait
pense a emprunter.

AUTONOME, SANS PYTEST : la CI de ce depot lance les bancs par
`python tests/fichier.py`.
"""
import os
import re
import sys

_RACINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _RACINE)

SRC = os.path.join(_RACINE, "modules", "ad_budget_api.py")


def _bloc_du_garde():
    """Le morceau qui derive le classement et appelle le hub."""
    src = open(SRC, encoding="utf-8").read()
    deb = src.index("CLASSEMENT UNIQUE (16 sept 2026)")
    fin = src.index("fields = []", deb)
    return src[deb:fin]


def _code_seul(bloc):
    """Le bloc SANS ses commentaires.

    Sans ca, un cas passerait parce que le mot cherche est dans une phrase
    d'explication -- un banc vert dont le sujet a disparu.
    """
    return "\n".join(l for l in bloc.split("\n") if not l.strip().startswith("#"))


def test_le_statut_entre_dans_la_derivation_du_classement():
    """LE DEFAUT DU 1er OCTOBRE, en un cas.

    Avant : `_CATEGORIE_VERS_CLASSEMENT_HUB.get(data.get("categorie_affichage"))`.
    Le statut n'y etait pas, donc il ne remontait jamais.
    """
    code = _code_seul(_bloc_du_garde())
    assert 'data.get("statut")' in code, \
        "le statut ne participe pas a la derivation : il ne remontera pas au hub"
    assert 'data.get("categorie_affichage")' in code, \
        "la pastille doit continuer de remonter : on n'echange pas un trou contre un autre"


def test_on_n_appelle_le_hub_QUE_SI_le_statut_change():
    """Un PATCH qui reecrit le statut COURANT ne doit pas appeler le hub.

    Le front renvoie souvent le projet entier : sans cette condition, une
    modification qui ne concerne pas le statut (une note, un pourcentage)
    dependrait de la disponibilite du hub pour aboutir.
    """
    code = _code_seul(_bloc_du_garde())
    assert 'existing.get("statut")' in code, \
        "rien ne compare le statut demande a l'actuel : le hub serait appele pour rien"
    assert re.search(r"_statut_change\s*=", code), \
        "la condition de changement n'est pas nommee"
    assert "_statut_change" in code.split("_source_classement")[1][:200], \
        "la condition de changement ne sert pas a la derivation"


def test_le_refus_reste_FRANC_si_le_hub_ne_repond_pas():
    """Fail-closed : rien n'est ecrit localement quand le hub echoue.

    C'est la garantie qui empeche un nouvel ecart de naitre. Elle existait
    deja pour la pastille ; elle doit couvrir le statut par le meme chemin.
    """
    bloc = _bloc_du_garde()
    code = _code_seul(bloc)
    assert "hub_service.set_project_classement" in code
    assert "HubServiceError" in code
    assert "raise HTTPException" in code
    # Aucune ecriture locale AVANT l'appel au hub : le UPDATE vient apres.
    assert "UPDATE ad_budget.projets" not in code, \
        "une ecriture locale precede l'appel au hub : l'ecart pourrait naitre"


def test_la_table_de_correspondance_couvre_les_cinq_statuts():
    """Les cinq valeurs d'ALLOWED_STATUTS doivent avoir un classement.

    Un statut sans correspondance rendrait None, le garde ne se declencherait
    pas, et ce statut-la remonterait en silence a l'ancien comportement --
    un trou qui ne se verrait pas.
    """
    src = open(SRC, encoding="utf-8").read()
    deb = src.index("_CATEGORIE_VERS_CLASSEMENT_HUB = {")
    table = src[deb:src.index("}", deb)]
    for statut in ("en_soumission", "en_cours", "en_execution", "perdu", "archive"):
        assert f'"{statut}"' in table, f"{statut} n'a aucun classement : il ne remontera pas"


if __name__ == "__main__":
    cas = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in cas:
        f()
        print(f"  OK  {f.__name__}")
    print(f"{len(cas)} cas au vert — le statut d'Ad BUD remonte au hub")
