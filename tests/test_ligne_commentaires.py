"""Garde-fou — commentaires sur une ligne de budget (18 septembre 2026).

Simon : « on peut ajouter des commentaires, modifier, supprimer, date et nom
de l'éditeur ». On vérifie : l'auteur est pris du JETON (jamais du client) et
figé ; seul l'auteur modifie ou supprime ; un commentaire ne vise qu'une ligne
du projet de la route ; le compte par ligne alimente le « C » rouge.
"""
import os
import sys
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import extract_nested  # noqa: E402
import modules.ad_ligne_commentaires_api as M  # noqa: E402


class _Cur:
    def __init__(self, db):
        self._db = db
        self._res = None

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split()).lower()
        p = list(params or ())
        self._res = None
        if s.startswith("select 1 from ad_budget.budget_lignes"):
            lid, pid = p
            ok = any(l["id"] == lid and l["projet_id"] == pid for l in self._db["lignes"])
            self._res = ("one", (1,) if ok else None)
        elif s.startswith("select ligne_id, count(*)"):
            (pid,) = p
            compte = {}
            for c in self._db["commentaires"]:
                if c["projet_id"] == pid:
                    compte[c["ligne_id"]] = compte.get(c["ligne_id"], 0) + 1
            self._res = ("all", list(compte.items()))
        elif s.startswith("select id, ligne_id") and "where ligne_id = %s" in s:
            lid, pid = p
            self._res = ("all", [dict(c) for c in self._db["commentaires"]
                                 if c["ligne_id"] == lid and c["projet_id"] == pid])
        elif s.startswith("select id, ligne_id") and "where id = %s" in s:
            cid, pid = p
            c = next((c for c in self._db["commentaires"]
                      if c["id"] == cid and c["projet_id"] == pid), None)
            self._res = ("one", dict(c) if c else None)
        elif s.startswith("insert into ad_budget.ligne_commentaires"):
            lid, pid, uid, nom, corps = p
            self._db["seq"] += 1
            c = {"id": self._db["seq"], "ligne_id": lid, "projet_id": pid, "auteur_id": uid,
                 "auteur_nom": nom, "corps": corps, "created_at": datetime(2026, 9, 18),
                 "updated_at": None}
            self._db["commentaires"].append(c)
            self._res = ("one", dict(c))
        elif s.startswith("update ad_budget.ligne_commentaires"):
            corps, cid = p
            c = next(c for c in self._db["commentaires"] if c["id"] == cid)
            c["corps"], c["updated_at"] = corps, datetime(2026, 9, 19)
            self._res = ("one", dict(c))
        elif s.startswith("delete from ad_budget.ligne_commentaires"):
            (cid,) = p
            self._db["commentaires"] = [c for c in self._db["commentaires"] if c["id"] != cid]

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


SIMON = {"id": 1, "nom": "Simon Hachey", "email": "simon@adision.ca"}
AUTRE = {"id": 2, "nom": "Estimateur", "email": "est@adision.ca"}


@pytest.fixture
def db():
    return {"lignes": [{"id": 10, "projet_id": 7}, {"id": 11, "projet_id": 7},
                       {"id": 99, "projet_id": 8}],
            "commentaires": [], "seq": 0}


def _route(db, nom):
    return extract_nested(M.register_ad_ligne_commentaires_routes, lambda: _Conn(db), nom)


@pytest.fixture(autouse=True)
def _acces_permis():
    with patch.object(M, "_load_and_authorize_projet", lambda *a, **k: None):
        yield


def test_ajout_prend_l_auteur_du_jeton_et_le_fige(db):
    c = _route(db, "ajouter")(7, 10, M.CommentaireSaisie(corps="  Vérifier le prix  "), user=SIMON)["commentaire"]
    assert c["auteur_nom"] == "Simon Hachey"
    assert c["auteur_id"] == 1
    assert c["corps"] == "Vérifier le prix"
    assert c["created_at"] is not None


def test_une_ligne_d_un_autre_projet_est_refusee(db):
    with pytest.raises(HTTPException) as e:
        _route(db, "ajouter")(7, 99, M.CommentaireSaisie(corps="x"), user=SIMON)
    assert e.value.status_code == 404


def test_seul_l_auteur_modifie_ou_supprime(db):
    c = _route(db, "ajouter")(7, 10, M.CommentaireSaisie(corps="à revoir"), user=SIMON)["commentaire"]
    with pytest.raises(HTTPException) as e:
        _route(db, "modifier")(7, c["id"], M.CommentaireSaisie(corps="piraté"), user=AUTRE)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        _route(db, "supprimer")(7, c["id"], user=AUTRE)
    assert e.value.status_code == 403
    maj = _route(db, "modifier")(7, c["id"], M.CommentaireSaisie(corps="revu"), user=SIMON)["commentaire"]
    assert maj["corps"] == "revu" and maj["updated_at"] is not None
    assert _route(db, "supprimer")(7, c["id"], user=SIMON)["ok"] is True
    assert db["commentaires"] == []


def test_le_compte_par_ligne_alimente_le_c_rouge(db):
    ajouter = _route(db, "ajouter")
    ajouter(7, 10, M.CommentaireSaisie(corps="un"), user=SIMON)
    ajouter(7, 10, M.CommentaireSaisie(corps="deux"), user=AUTRE)
    ajouter(7, 11, M.CommentaireSaisie(corps="trois"), user=SIMON)
    assert _route(db, "compter")(7, user=SIMON) == {"compte": {"10": 2, "11": 1}}
    fil = _route(db, "lister")(7, 10, user=SIMON)["commentaires"]
    assert [c["corps"] for c in fil] == ["un", "deux"]
