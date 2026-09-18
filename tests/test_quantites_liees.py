"""Garde-fou — QUANTITÉS LIÉES entre lots (18 septembre 2026).

Simon : « j'ai choisi l'option de copier l'item aussi dans le lot Rimini avec
les quantités ; l'item est bien copié mais sans les quantités » -- il tape les
quantités dans le tableau APRÈS la création. Règle choisie par Simon :
« suivre, jusqu'à retouche ».

  - modifier la quantité de l'ORIGINE -> ses copies liées suivent ;
  - modifier la quantité d'une COPIE -> elle devient indépendante ;
  - renvoyer la même quantité (l'écran renvoie toute la ligne à chaque
    sauvegarde) -> ni l'un ni l'autre.

Fausse base en mémoire, même style que test_create_ligne_a_completer.py :
elle interprète seulement les requêtes de update_budget_ligne.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import extract_nested  # noqa: E402
import modules.ad_budget_api as B  # noqa: E402


def _ligne(lid, lot_id, qte, liee=None):
    return {
        "id": lid, "projet_id": 7, "lot_id": lot_id, "quantites_liees_a": liee,
        "qte": qte, "heures": qte * 10, "heures_manuelles": False,
        "production_valeur": None, "production_unite": None, "production_auto": True,
        "sous_traitant_montant": 0,
    }


class _Cur:
    def __init__(self, db):
        self._db = db
        self._res = None

    def _trouver(self, lid):
        return next((l for l in self._db["lignes"] if l["id"] == lid), None)

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split()).lower()
        params = list(params or ())
        self._res = None
        if "select user_id, organization_id, is_verrouille" in s:
            self._res = ("one", {
                "user_id": 1, "organization_id": None, "is_verrouille": False,
                "detenteur_id": None, "detenteur_nom": None,
                "detenteur_email": None, "derniere_activite": None,
            })
            return
        if s.startswith("select quantites_liees_a,"):
            lid, _pid = params
            l = self._trouver(lid)
            self._res = ("one", dict(l) if l else None)
            return
        if s.startswith("update ad_budget.budget_lignes set") and "where id = %s and projet_id = %s" in s:
            lid, _pid = params[-2], params[-1]
            l = self._trouver(lid)
            ensemble = s.split(" set ", 1)[1].split(" where ", 1)[0]
            valeurs = iter(params[:-2])
            for morceau in ensemble.split(", "):
                col, expr = [x.strip() for x in morceau.split("=", 1)]
                if expr == "%s":
                    l[col] = next(valeurs)
                elif expr == "null":
                    l[col] = None
            self._db["updates"].append(s)
            return
        if s.startswith("update ad_budget.budget_lignes c set"):
            origine = self._trouver(params[0])
            copies = []
            for l in self._db["lignes"]:
                if l["quantites_liees_a"] == origine["id"]:
                    for c in B.CHAMPS_QUANTITES_LIEES:
                        l[c] = origine[c]
                    copies.append(dict(l))
            self._res = ("all", copies)
            return

    def fetchone(self):
        return self._res[1] if self._res and self._res[0] == "one" else None

    def fetchall(self):
        return self._res[1] if self._res and self._res[0] == "all" else []

    def close(self):
        pass


class _Conn:
    def __init__(self, db):
        self._db = db

    def cursor(self, *a, **k):
        return _Cur(self._db)

    def commit(self):
        pass

    def close(self):
        pass


_USER = {"id": 1, "platform_role": "super_admin", "org_role": "admin",
         "organization_id": None, "nom": "Test"}


def _maj(db, ligne_id, data):
    update = extract_nested(B.register_ad_budget_routes, lambda: _Conn(db), "update_budget_ligne")
    with patch.object(B, "_push_budget_snapshot", lambda *a, **k: None):
        return update(7, ligne_id, dict(data), user=_USER, authorization=None, session_cookie=None)


def _db():
    return {
        "lignes": [
            _ligne(1, 51, 0),              # origine, lot Grande ligne
            _ligne(2, 52, 0, liee=1),      # copie liée, lot Rimini
            _ligne(3, 53, 0, liee=1),      # autre copie liée
            _ligne(4, 53, 0),              # ligne sans rapport
        ],
        "updates": [],
    }


def _l(db, lid):
    return next(l for l in db["lignes"] if l["id"] == lid)


def test_la_quantite_de_l_origine_se_propage_aux_copies_liees():
    db = _db()
    rep = _maj(db, 1, {"qte": 4, "heures": 60})
    assert _l(db, 2)["qte"] == 4 and _l(db, 3)["qte"] == 4
    assert _l(db, 2)["heures"] == 60
    assert {c["id"] for c in rep["copies"]} == {2, 3}
    # La propagation ne détache pas les copies.
    assert _l(db, 2)["quantites_liees_a"] == 1
    # Une ligne sans lien n'est jamais touchée.
    assert _l(db, 4)["qte"] == 0


def test_retoucher_une_copie_la_rend_independante():
    db = _db()
    _maj(db, 2, {"qte": 1})
    assert _l(db, 2)["qte"] == 1
    assert _l(db, 2)["quantites_liees_a"] is None
    # L'origine change ensuite : la copie retouchée ne suit plus, l'autre oui.
    _maj(db, 1, {"qte": 4})
    assert _l(db, 2)["qte"] == 1
    assert _l(db, 3)["qte"] == 4


def test_la_meme_quantite_renvoyee_ne_change_rien():
    db = _db()
    rep = _maj(db, 2, {"qte": 0, "description": "Frais de transport"})
    assert _l(db, 2)["quantites_liees_a"] == 1
    rep = _maj(db, 1, {"qte": 0, "description": "Frais de transport"})
    assert rep["copies"] == []


def test_valeur_change_ignore_les_ecritures_identiques():
    assert not B._valeur_change(4, "4")
    assert not B._valeur_change(None, "")
    assert B._valeur_change(0, 4)
    assert B._valeur_change("0.0125", 0.0667)
