# -*- coding: utf-8 -*-
"""TEMOIN -- ouvrir un budget Ad BUD dans Ad EST : UNE fabrique, DEUX portes.

Simon, 7 oct 2026 08 h 08 : « j'aimerais pouvoir ouvrir un projet fait dans
ad bud dans ad est.. c est possible ? » ; 08 h 10 : « **1-copier le contenue
vivent separéememt** » ; 15 h 44, capture a l'appui : « **je ne vois pas
d'option** ».

CE QUE CE BANC TIENT

1. **Le calcul du payload n'existe qu'UNE fois.** Deux chemins qui calculent
   le meme total finissent toujours par diverger -- c'est exactement ce qui a
   servi a Ad CON **474 fausses « lignes modifiees » sur 1 209** le 7 octobre
   au matin, parce qu'une route lisait la colonne `total` brute pendant que
   l'autre passait par `_lignes_pour_version`. Ici, les deux portes appellent
   `_payload_export_budget` et ne touchent a aucune table elles-memes.

2. **La garde de statut diffère, et seulement elle.**
   * Ad CON suit un CHANTIER -> le budget doit etre GAGNE
     (`EXPORT_CON_STATUTS`) : rien n'est a batir sur une soumission ;
   * Ad EST reprend un budget pour ESTIMER -> le cas normal est un projet
     **en soumission**. Mesure du 7 oct, organisation Contracta : **6 projets
     en soumission sur 25**, tous refuses par la garde d'Ad CON. Appliquer la
     meme garde ici interdirait l'usage demande dans tous les cas normaux.

3. **Ad CON ne doit RIEN perdre.** Son chemin, son nom et sa garde sont
   verifies tels quels : c'est du code en production.

ON INSPECTE L'AST, PAS DU TEXTE DECOUPE. Une recherche entre deux chaines
attrape les commentaires voisins -- et l'en-tete de ce correctif CITE les
noms qu'on cherche. Un banc qui lit les commentaires comme du code ment dans
les deux sens.
"""
import ast
import io
import os

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(RACINE, "modules", "ad_budget_api.py")

FABRIQUE = "_payload_export_budget"
PORTES = {
    "export_projet_for_con": "/projets/{projet_id}/export-for-con",
    "export_projet_for_est": "/projets/{projet_id}/export-for-est",
}


@pytest.fixture(scope="module")
def arbre():
    return ast.parse(io.open(SRC, encoding="utf-8").read())


def _fonctions(arbre, nom):
    return [n for n in ast.walk(arbre)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nom]


def _chemins_decorateurs(fn):
    """Les chemins des @router.get(...) poses sur cette fonction."""
    out = []
    for d in fn.decorator_list:
        if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr in ("get", "post")):
            for a in d.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    out.append(a.value)
    return out


def _appels(fn, nom):
    return [n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == nom]


def _kwarg(appel, nom):
    for kw in appel.keywords:
        if kw.arg == nom:
            return kw
    return None


# ══════════════════════════════════════════════════════════════════════════
# 1. UNE SEULE FABRIQUE
# ══════════════════════════════════════════════════════════════════════════

def test_la_fabrique_existe_une_seule_fois(arbre):
    n = len(_fonctions(arbre, FABRIQUE))
    assert n == 1, (
        "%d definition(s) de %s : le calcul du payload doit etre unique, "
        "sinon les deux modules divergeront sans que rien ne le dise." % (n, FABRIQUE))


def test_la_fabrique_nest_PAS_une_route(arbre):
    """Elle est interne : l'exposer donnerait une troisieme porte sans garde."""
    fn = _fonctions(arbre, FABRIQUE)[0]
    assert _chemins_decorateurs(fn) == [], (
        "la fabrique est exposee comme une route : une porte sans garde de plus")


# ══════════════════════════════════════════════════════════════════════════
# 2. DEUX PORTES, CHACUNE A SON CHEMIN
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("nom,chemin", sorted(PORTES.items()))
def test_chaque_porte_existe_a_son_chemin(arbre, nom, chemin):
    fns = _fonctions(arbre, nom)
    assert len(fns) == 1, "%d definition(s) de %s" % (len(fns), nom)
    assert chemin in _chemins_decorateurs(fns[0]), (
        "%s n'est plus servie a %s -- le module qui l'appelle recevrait un 404"
        % (nom, chemin))


@pytest.mark.parametrize("nom", sorted(PORTES))
def test_chaque_porte_DELEGUE_le_calcul(arbre, nom):
    fn = _fonctions(arbre, nom)[0]
    assert _appels(fn, FABRIQUE), "%s ne passe pas par %s" % (nom, FABRIQUE)


@pytest.mark.parametrize("nom", sorted(PORTES))
def test_aucune_porte_ne_REFAIT_le_calcul(arbre, nom):
    """Une porte qui touche la base a recopie la fabrique."""
    fn = _fonctions(arbre, nom)[0]
    source = ast.dump(fn)
    for interdit in ("SELECT", "cur.execute", "get_conn"):
        assert interdit not in source, (
            "%s contient « %s » : le calcul y est refait au lieu d'etre delegue"
            % (nom, interdit))


# ══════════════════════════════════════════════════════════════════════════
# 3. LA GARDE DE STATUT -- LE SEUL POINT QUI DIFFERE
# ══════════════════════════════════════════════════════════════════════════

def test_ad_con_exige_TOUJOURS_un_projet_gagne(arbre):
    """Code en production : Ad CON ne doit rien perdre."""
    fn = _fonctions(arbre, "export_projet_for_con")[0]
    kw = _kwarg(_appels(fn, FABRIQUE)[0], "statuts_permis")
    assert kw is not None, "export-for-con ne passe plus statuts_permis"
    assert isinstance(kw.value, ast.Name) and kw.value.id == "EXPORT_CON_STATUTS", (
        "export-for-con ne borne plus aux statuts gagnants : un budget en "
        "soumission ouvrirait un suivi de chantier")


def test_ad_est_na_PAS_de_garde_de_statut(arbre):
    """LE POINT DU CORRECTIF. Mutant qui doit rougir : passer
    EXPORT_CON_STATUTS ici aussi -- Ad EST ne pourrait alors reprendre AUCUN
    des 6 projets en soumission de Contracta, c'est-a-dire le cas normal."""
    fn = _fonctions(arbre, "export_projet_for_est")[0]
    kw = _kwarg(_appels(fn, FABRIQUE)[0], "statuts_permis")
    assert kw is not None, "export-for-est ne passe pas statuts_permis explicitement"
    assert isinstance(kw.value, ast.Constant) and kw.value.value is None, (
        "export-for-est impose une garde de statut : un budget en soumission "
        "-- le cas NORMAL pour une estimation -- serait refuse en 403")


def test_la_garde_est_CONDITIONNELLE_dans_la_fabrique(arbre):
    """Sans le `is not None`, `statuts_permis=None` ferait `x not in None` ->
    TypeError, donc un 500 au lieu d'un passage."""
    fn = _fonctions(arbre, FABRIQUE)[0]
    gardes = [n for n in ast.walk(fn)
              if isinstance(n, ast.Compare)
              and isinstance(n.left, ast.Name) and n.left.id == "statuts_permis"
              and any(isinstance(o, ast.IsNot) for o in n.ops)]
    assert gardes, (
        "la fabrique ne verifie plus `statuts_permis is not None` : avec None "
        "elle leverait un TypeError au lieu de laisser passer")


# ══════════════════════════════════════════════════════════════════════════
# ANTI-VACUITE : le detecteur doit savoir dire NON
# ══════════════════════════════════════════════════════════════════════════

def test_une_fonction_INVENTEE_est_introuvable(arbre):
    assert _fonctions(arbre, "_export_qui_nexiste_pas_du_tout") == []


def test_un_chemin_INVENTE_nest_pas_trouve(arbre):
    fn = _fonctions(arbre, "export_projet_for_est")[0]
    assert "/projets/{projet_id}/export-for-la-lune" not in _chemins_decorateurs(fn)
