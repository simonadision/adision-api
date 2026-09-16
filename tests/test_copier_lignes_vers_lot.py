# -*- coding: utf-8 -*-
"""TEMOIN — clic droit « Copier dans le lot » (POST /lignes/copier-vers-lot).

16 septembre 2026, Simon : « j'aimerais ajouter une fonction a clic droit.
Pour me permettre de copier coller automatiquement des items dans d'autre
lot ».

  - copie profonde : MEME liste de colonnes que duplicate_lot (sinon une
    colonne ajoutee a l'un et oubliee a l'autre perdrait une valeur, deja
    arrive : production_valeur, prix_unitaire_st) ;
  - les copies vont en fin de section, dans l'ordre affiche des sources ;
  - lot et lignes doivent appartenir au projet.
"""
import inspect
import os
import re
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from tests._harness import extract_nested  # noqa: E402
import modules.ad_budget_api as B  # noqa: E402

_USER = {"id": 1, "platform_role": "super_admin", "org_role": "admin",
         "organization_id": None, "nom": "Test"}


def test_plan_copies_en_fin_de_section_dans_l_ordre():
    sources = [(5, "06 00 00"), (7, "06 00 00"), (9, "08 00 00")]
    plan = B._plan_copie_lignes(sources, {"06 00 00": 60, "08 00 00": None})
    assert plan == [(5, 70), (7, 80), (9, 10)]


def test_colonnes_identiques_a_la_duplication_de_lot():
    src = inspect.getsource(B.register_ad_budget_routes)
    debut = src.index('@router.post("/projets/{projet_id}/lots/{lot_id}/duplicate")')
    bloc = src[debut:]
    m = re.search(r"INSERT INTO ad_budget\.budget_lignes\s*\((.*?)\)\s*SELECT", bloc, re.S)
    assert m, "INSERT de duplicate_lot introuvable"
    colonnes_dup = {c.strip() for c in m.group(1).split(",")} - {"projet_id", "lot_id"}
    assert colonnes_dup == set(B._COLONNES_COPIE_LIGNE)


# ── Fausse base : lots + budget_lignes ─────────────────────────────────────
class _Cur:
    def __init__(self, db):
        self.db = db
        self._res = ("all", [])

    @staticmethod
    def _n(sql):
        return " ".join(str(sql).split()).lower()

    def execute(self, sql, params=None):
        s = self._n(sql)
        params = params or ()
        if "select user_id, organization_id, is_verrouille" in s:
            self._res = ("one", {"user_id": 1, "organization_id": None, "is_verrouille": False,
                                 "detenteur_id": None, "detenteur_nom": None,
                                 "detenteur_email": None, "derniere_activite": None})
        elif s.startswith("select id, nom from ad_budget.lots"):
            lot_id, projet_id = params
            lot = next((l for l in self.db["lots"] if l["id"] == lot_id and l["projet_id"] == projet_id), None)
            self._res = ("one", lot)
        elif s.startswith("select id, section from ad_budget.budget_lignes"):
            projet_id, ids = params
            rows = [l for l in self.db["lignes"] if l["projet_id"] == projet_id and l["id"] in ids]
            rows.sort(key=lambda l: (l["section"], l["ordre"], l["id"]))
            self._res = ("all", [{"id": l["id"], "section": l["section"]} for l in rows])
        elif s.startswith("select section, max(ordre)"):
            (projet_id,) = params
            mx = {}
            for l in self.db["lignes"]:
                if l["projet_id"] == projet_id:
                    mx[l["section"]] = max(mx.get(l["section"], 0), l["ordre"])
            self._res = ("all", [{"section": k, "max_ordre": v} for k, v in mx.items()])
        elif s.startswith("insert into ad_budget.budget_lignes"):
            lot_id, ordre, source_id, projet_id = params
            src = next(l for l in self.db["lignes"] if l["id"] == source_id and l["projet_id"] == projet_id)
            self.db["next_id"] += 1
            copie = dict(src, id=self.db["next_id"], lot_id=lot_id, ordre=ordre)
            self.db["lignes"].append(copie)
            self._res = ("one", dict(copie))
        else:
            self._res = ("all", [])

    def fetchone(self):
        return self._res[1] if self._res[0] == "one" else None

    def fetchall(self):
        return self._res[1] if self._res[0] == "all" else []

    def close(self):
        pass


class _Conn:
    def __init__(self, db):
        self.db = db

    def cursor(self, *a, **k):
        return _Cur(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _db():
    return {
        "next_id": 100,
        "lots": [{"id": 1, "projet_id": 324, "nom": "Grande Ligne"},
                 {"id": 2, "projet_id": 324, "nom": "Rimini"},
                 {"id": 3, "projet_id": 999, "nom": "Autre projet"}],
        "lignes": [
            {"id": 10, "projet_id": 324, "lot_id": 1, "section": "08 00 00", "ordre": 10,
             "description": "Portes extérieures en aluminium", "qte": 1, "prix_unitaire": 1500,
             "production_valeur": 0.3333},
            {"id": 11, "projet_id": 324, "lot_id": 1, "section": "08 00 00", "ordre": 20,
             "description": "Fenêtres extérieures", "qte": 1, "prix_unitaire": 750},
            {"id": 12, "projet_id": 324, "lot_id": 2, "section": "08 00 00", "ordre": 30,
             "description": "Vitrage", "qte": 0, "prix_unitaire": 0},
        ],
    }


def _appel(db, data):
    fn = extract_nested(B.register_ad_budget_routes, lambda: _Conn(db), "copier_lignes_vers_lot")
    with patch.object(B, "_push_budget_snapshot", lambda *a, **k: None):
        return fn(324, data, user=_USER, authorization=None, session_cookie=None)


def test_copie_dans_un_autre_lot_valeurs_et_rang():
    db = _db()
    r = _appel(db, {"ligne_ids": [11, 10], "lot_id": 2})
    assert r["nb"] == 2
    copies = r["lignes"]
    # Ordre affiché des sources (10 puis 11), en fin de section (après 30).
    assert [c["description"] for c in copies] == ["Portes extérieures en aluminium", "Fenêtres extérieures"]
    assert [c["ordre"] for c in copies] == [40, 50]
    assert all(c["lot_id"] == 2 for c in copies)
    assert copies[0]["production_valeur"] == 0.3333 and copies[0]["prix_unitaire"] == 1500
    # Les sources ne bougent pas.
    assert next(l for l in db["lignes"] if l["id"] == 10)["lot_id"] == 1


def test_copie_hors_lot():
    db = _db()
    r = _appel(db, {"ligne_ids": [12], "lot_id": None})
    assert r["lignes"][0]["lot_id"] is None


def test_lot_d_un_autre_projet_refuse():
    with pytest.raises(HTTPException) as e:
        _appel(_db(), {"ligne_ids": [10], "lot_id": 3})
    assert e.value.status_code == 404


def test_ligne_hors_projet_refusee():
    with pytest.raises(HTTPException) as e:
        _appel(_db(), {"ligne_ids": [10, 4242], "lot_id": 2})
    assert e.value.status_code == 404


def test_payload_vide_refuse():
    with pytest.raises(HTTPException) as e:
        _appel(_db(), {"ligne_ids": [], "lot_id": 2})
    assert e.value.status_code == 400
