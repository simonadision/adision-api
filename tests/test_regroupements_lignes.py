"""_normaliser_regroupements — le membre `lignes` (lasso, 14 sept. 2026).

Simon : « j'aimerais ajouter l'outils selection des ligne avec lasso...
je peux creer un sous total pour les ligne selectionner ». `divisions`
groupe des divisions entières, `sections` (9 sept. 2026) des sections
entières ; ni l'un ni l'autre ne peut exprimer « exactement ces 3 lignes
sur les 10 d'une section » — d'où `lignes`, une troisième liste qui
COEXISTE avec les deux premières dans la même entrée.

Testé via PUT /budget/projets/{id}/regroupements (la route réelle), pas
la fonction interne — _normaliser_regroupements est une fermeture de
register_ad_gabarits_routes, jamais exposée telle quelle.

Lancer : pytest tests/test_regroupements_lignes.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import modules.ad_gabarits_api as M  # noqa: E402


class _FakeCur:
    def execute(self, sql, params=None): pass
    def close(self): pass


class _FakeConn:
    def cursor(self, *a, **k): return _FakeCur()
    def commit(self): pass
    def close(self): pass


def _appel(payload):
    # get_conn est capturé par CLOSURE dans register_ad_gabarits_routes(get_conn)
    # -- pas une variable de module, contrairement à _load_and_authorize_projet.
    # Le passer directement ici (plutôt que rafistoler M.get_conn après coup,
    # comme dans test_categorie_affichage.py côté Ad TAK) est donc obligatoire.
    router = M.register_ad_gabarits_routes(lambda: _FakeConn())
    ep = next(r.endpoint for r in router.routes
              if getattr(r.endpoint, "__name__", "") == "set_projet_regroupements")

    orig = M._load_and_authorize_projet
    M._load_and_authorize_projet = lambda *a, **k: {"id": 7}
    try:
        return ep(7, {"regroupements": payload}, user={"id": 1})
    finally:
        M._load_and_authorize_projet = orig


def test_lignes_seules_suffisent_a_garder_lentree():
    # Ni divisions ni sections -- avant ce chantier, une entrée comme
    # celle-ci aurait été SILENCIEUSEMENT écartée (aucun membre reconnu).
    out = _appel([{"nom": "Mon lasso", "lignes": [101, 102, 103]}])
    assert len(out["regroupements"]) == 1, out
    r = out["regroupements"][0]
    assert r["nom"] == "Mon lasso"
    assert r["lignes"] == [101, 102, 103]
    assert r["divisions"] == []
    assert r["sections"] == []


def test_lignes_coexistent_avec_divisions_et_sections():
    out = _appel([{
        "nom": "Mixte", "divisions": ["09"], "sections": ["22.1"], "lignes": [55],
    }])
    r = out["regroupements"][0]
    assert r["divisions"] == ["09"]
    assert r["sections"] == ["22.1"]
    assert r["lignes"] == [55]


def test_lignes_deduplique_trie_et_filtre_les_non_entiers():
    out = _appel([{
        "nom": "Bruit",
        "lignes": [5, 3, 5, "abc", None, -1, 0, True, 3.0, 3.9],
    }])
    r = out["regroupements"][0]
    # 5, 3 (dédupliqués et triés) ; "abc"/None/booléen rejetés ; -1 et 0
    # rejetés (pas > 0) ; 3.0 et 3.9 -> int(3) = 3, déjà présent.
    assert r["lignes"] == [3, 5], r["lignes"]


def test_entree_sans_aucun_membre_est_ecartee_comme_avant():
    out = _appel([{"nom": "Vide"}, {"nom": "Toujours vide", "lignes": []}])
    assert out["regroupements"] == []


def test_non_regression_divisions_seules_inchangees():
    # Le cas d'avant ce chantier -- ne doit PAS changer de forme.
    out = _appel([{"nom": "Structure", "divisions": ["03", "05"], "apres": "02"}])
    r = out["regroupements"][0]
    assert r["divisions"] == ["03", "05"]
    assert r["lignes"] == []
    assert r["apres"] == "02"


def test_apres_ligne_id_ancre_precise():
    # Simon : « ce que j'aimerais c'est de pouvoir placer mon sous total
    # n'importe ou » -- ancre plus fine que `apres` (division), pointant
    # une ligne précise du projet.
    out = _appel([{"nom": "Caution et assurance", "lignes": [55], "apres_ligne_id": 12345}])
    r = out["regroupements"][0]
    assert r["apres_ligne_id"] == 12345
    assert r["lignes"] == [55]


def test_apres_ligne_id_coexiste_avec_apres_division():
    # `apres` reste la position de repli si la ligne ancrée disparaît un
    # jour -- les deux champs coexistent, jamais l'un n'efface l'autre.
    out = _appel([{
        "nom": "Repli", "divisions": ["09"], "apres": "05", "apres_ligne_id": 777,
    }])
    r = out["regroupements"][0]
    assert r["apres"] == "05"
    assert r["apres_ligne_id"] == 777


def test_apres_ligne_id_rejette_non_entier_ou_negatif():
    out = _appel([{
        "nom": "Bruit", "lignes": [1], "apres_ligne_id": "abc",
    }, {
        "nom": "Bruit negatif", "lignes": [2], "apres_ligne_id": -5,
    }, {
        "nom": "Bruit zero", "lignes": [3], "apres_ligne_id": 0,
    }])
    for r in out["regroupements"]:
        assert r["apres_ligne_id"] is None, r


def test_apres_ligne_id_absent_reste_none():
    # Cas courant : un sous-total créé via la modale (division seule) n'a
    # jamais d'ancre de ligne -- ne doit jamais planter ni inventer 0.
    out = _appel([{"nom": "Classique", "divisions": ["09"]}])
    assert out["regroupements"][0]["apres_ligne_id"] is None
