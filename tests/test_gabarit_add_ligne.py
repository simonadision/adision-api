# -*- coding: utf-8 -*-
"""TEMOIN — clic droit « Ajouter au gabarit » (POST /budget/gabarits/add-ligne).

28 septembre 2026 : une ligne du budget Ad BUD s'ajoute au gabarit actuel, à
tous les gabarits, ou à des gabarits choisis — structure seule, sans valeurs.

  - la sous-section existante est retrouvée même rangée sous une autre
    division (gabarit 7 réel : « 01 52 00 » est sa propre division) ;
  - division et sous-section manquantes sont créées, rangées par code ;
  - même description (casse/espaces ignorés) = doublon, rien n'est ajouté ;
  - un gabarit d'une autre organisation est ignoré en silence.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import modules.ad_gabarits_api as G  # noqa: E402

ORG = "org-a"
_USER = {"id": 1, "organization_id": ORG, "nom": "Test"}


def test_index_insertion_par_code():
    codes = ["01 00 00", "01 52 00", "01 31 00", None, "09 00 00"]
    assert G._index_insertion_par_code(codes, "05 00 00") == 3
    assert G._index_insertion_par_code(codes, "00 10 00") == 0
    assert G._index_insertion_par_code(codes, "33 00 00") == 5
    assert G._index_insertion_par_code([], "05 00 00") == 0


# ── Fausse base : gabarits → sections → sous-sections → lignes ─────────────
class _Cur:
    def __init__(self, db):
        self.db = db
        self._res = ("all", [])

    @staticmethod
    def _n(sql):
        return " ".join(str(sql).split()).lower()

    def _nid(self):
        self.db["next_id"] += 1
        return self.db["next_id"]

    def execute(self, sql, params=None):
        s, p, db = self._n(sql), params or (), self.db
        if s.startswith("select id from ad_budget.gabarits where organization_id = %s and id = any"):
            org, ids = p
            self._res = ("all", [{"id": g["id"]} for g in db["gabarits"]
                                 if g["org"] == org and g["id"] in ids])
        elif s.startswith("select id from ad_budget.gabarits where organization_id"):
            (org,) = p
            self._res = ("all", [{"id": g["id"]} for g in db["gabarits"] if g["org"] == org])
        elif s.startswith("select ss.id from ad_budget.gabarit_sous_sections"):
            gid, code = p
            secs = sorted((x for x in db["sections"] if x["gabarit_id"] == gid),
                          key=lambda x: (x["ordre"], x["id"]))
            for sec in secs:
                for ss in sorted((y for y in db["sous"] if y["section_id"] == sec["id"]),
                                 key=lambda y: (y["ordre"], y["id"])):
                    if (ss["code_csi"] or "").strip() == code:
                        self._res = ("one", {"id": ss["id"]})
                        return
            self._res = ("one", None)
        elif s.startswith("select id, numero, ordre from ad_budget.gabarit_sections"):
            (gid,) = p
            rows = sorted((x for x in db["sections"] if x["gabarit_id"] == gid),
                          key=lambda x: (x["ordre"], x["id"]))
            self._res = ("all", [{"id": x["id"], "numero": x["numero"], "ordre": x["ordre"]} for x in rows])
        elif s.startswith("update ad_budget.gabarit_sections set ordre"):
            gid, o = p
            for x in db["sections"]:
                if x["gabarit_id"] == gid and x["ordre"] >= o:
                    x["ordre"] += 1
        elif s.startswith("insert into ad_budget.gabarit_sections"):
            gid, nom, numero, ordre = p
            i = self._nid()
            db["sections"].append({"id": i, "gabarit_id": gid, "nom": nom, "numero": numero, "ordre": ordre})
            self._res = ("one", {"id": i})
        elif s.startswith("select code_csi, ordre from ad_budget.gabarit_sous_sections"):
            (sid,) = p
            rows = sorted((y for y in db["sous"] if y["section_id"] == sid),
                          key=lambda y: (y["ordre"], y["id"]))
            self._res = ("all", [{"code_csi": y["code_csi"], "ordre": y["ordre"]} for y in rows])
        elif s.startswith("update ad_budget.gabarit_sous_sections set ordre"):
            sid, o = p
            for y in db["sous"]:
                if y["section_id"] == sid and y["ordre"] >= o:
                    y["ordre"] += 1
        elif s.startswith("insert into ad_budget.gabarit_sous_sections"):
            sid, code, lib, ordre = p
            i = self._nid()
            db["sous"].append({"id": i, "section_id": sid, "code_csi": code, "libelle": lib, "ordre": ordre})
            self._res = ("one", {"id": i})
        elif s.startswith("select description from ad_budget.gabarit_lignes"):
            (ssid,) = p
            self._res = ("all", [{"description": l["description"]} for l in db["lignes"] if l["ss_id"] == ssid])
        elif s.startswith("insert into ad_budget.gabarit_lignes"):
            ssid, typ, desc, code, _ = p
            ordres = [l["ordre"] for l in db["lignes"] if l["ss_id"] == ssid]
            db["lignes"].append({"id": self._nid(), "ss_id": ssid, "type": typ, "description": desc,
                                 "code_typ": code, "ordre": (max(ordres) + 1) if ordres else 0})
        elif s.startswith("update ad_budget.gabarits set updated_at"):
            db["touches"].append(p[0])
        else:
            raise AssertionError(f"SQL inattendu : {s[:90]}")

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
        self.db["commits"] += 1

    def rollback(self):
        pass

    def close(self):
        pass


def _db():
    # Gabarit 7 : copie réduite du vrai (01 52 00 rangé comme sa propre division).
    return {
        "next_id": 1000, "commits": 0, "touches": [],
        "gabarits": [{"id": 7, "org": ORG}, {"id": 8, "org": ORG}, {"id": 99, "org": "org-b"}],
        "sections": [
            {"id": 1, "gabarit_id": 7, "numero": "01 00 00", "nom": "Exigences générales", "ordre": 0},
            {"id": 2, "gabarit_id": 7, "numero": "01 52 00", "nom": "Installations temporaires", "ordre": 1},
            {"id": 3, "gabarit_id": 7, "numero": "09 00 00", "nom": "Finitions", "ordre": 2},
        ],
        "sous": [
            {"id": 10, "section_id": 1, "code_csi": "01 00 00", "libelle": "Exigences", "ordre": 0},
            {"id": 11, "section_id": 2, "code_csi": "01 52 00", "libelle": "Installations", "ordre": 0},
            {"id": 12, "section_id": 3, "code_csi": "09 91 00", "libelle": "Peinture", "ordre": 0},
        ],
        "lignes": [
            {"id": 20, "ss_id": 11, "type": "manuelle", "description": "Toilette chimique", "code_typ": None, "ordre": 0},
        ],
    }


def _route(db):
    router = G.register_ad_gabarits_routes(lambda: _Conn(db))
    return next(r.endpoint for r in router.routes if r.path == "/budget/gabarits/add-ligne")


def _body(**kw):
    b = {"gabarit_ids": [7], "code_csi": "01 52 00", "libelle_csi": "Installations",
         "code_division": "01 00 00", "libelle_division": "Exigences générales",
         "description": "Clôture de chantier", "type": "manuelle", "code_typ": None}
    b.update(kw)
    return b


def test_sous_section_existante_sous_une_autre_division():
    db = _db()
    r = _route(db)(_body(), user=_USER)
    assert r == {"nb_gabarits": 1, "nb_ajoutes": 1, "nb_doublons": 0, "ajoutes": [7], "doublons": []}
    assert len(db["sous"]) == 3 and len(db["sections"]) == 3  # rien de créé
    ajout = db["lignes"][-1]
    assert (ajout["ss_id"], ajout["ordre"], ajout["description"]) == (11, 1, "Clôture de chantier")
    assert db["touches"] == [7] and db["commits"] == 1


def test_doublon_casse_et_espaces_ignores():
    db = _db()
    r = _route(db)(_body(description="  toilette CHIMIQUE "), user=_USER)
    assert (r["nb_ajoutes"], r["nb_doublons"], r["doublons"]) == (0, 1, [7])
    assert len(db["lignes"]) == 1


def test_division_et_sous_section_creees_rangees_par_code():
    db = _db()
    r = _route(db)(_body(code_csi="05 12 00", libelle_csi="Charpente métallique",
                         code_division="05 00 00", libelle_division="Métaux",
                         description="Poutre W", type="ad_typ", code_typ="TYP-1"), user=_USER)
    assert r["nb_ajoutes"] == 1
    div = next(x for x in db["sections"] if x["numero"] == "05 00 00")
    ordre = [x["numero"] for x in sorted((x for x in db["sections"] if x["gabarit_id"] == 7),
                                         key=lambda x: x["ordre"])]
    assert ordre == ["01 00 00", "01 52 00", "05 00 00", "09 00 00"]
    ss = next(y for y in db["sous"] if y["section_id"] == div["id"])
    assert (ss["code_csi"], ss["libelle"], ss["ordre"]) == ("05 12 00", "Charpente métallique", 0)
    ln = db["lignes"][-1]
    assert (ln["type"], ln["code_typ"], ln["ordre"]) == ("ad_typ", "TYP-1", 0)


def test_sous_section_creee_dans_division_existante_a_sa_place():
    db = _db()
    db["sous"].append({"id": 13, "section_id": 3, "code_csi": "09 30 00", "libelle": "Carrelage", "ordre": 1})
    _route(db)(_body(code_csi="09 60 00", code_division="09 00 00", description="Vinyle"), user=_USER)
    codes = [y["code_csi"] for y in sorted((y for y in db["sous"] if y["section_id"] == 3),
                                           key=lambda y: y["ordre"])]
    assert codes == ["09 91 00", "09 30 00", "09 60 00"]  # après le dernier <= 09 60 00


def test_ad_typ_sans_code_devient_manuelle():
    db = _db()
    _route(db)(_body(type="ad_typ", code_typ=""), user=_USER)
    assert (db["lignes"][-1]["type"], db["lignes"][-1]["code_typ"]) == ("manuelle", None)


def test_all_et_autre_organisation_ignoree():
    db = _db()
    r = _route(db)(_body(gabarit_ids="all"), user=_USER)
    assert (r["nb_gabarits"], r["ajoutes"]) == (2, [7, 8])  # jamais le 99 (org-b)
    r2 = _route(_db())(_body(gabarit_ids=[99]), user=_USER)
    assert r2["nb_gabarits"] == 0 and r2["nb_ajoutes"] == 0


def test_division_derivee_si_absente():
    db = _db()
    _route(db)(_body(gabarit_ids=[8], code_csi="03 30 00", code_division=None,
                     libelle_division="", description="Dalle"), user=_USER)
    assert [x["numero"] for x in db["sections"] if x["gabarit_id"] == 8] == ["03 00 00"]


@pytest.mark.parametrize("champ,valeur", [("code_csi", " "), ("description", ""), ("gabarit_ids", [])])
def test_entrees_invalides(champ, valeur):
    with pytest.raises(HTTPException) as e:
        _route(_db())(_body(**{champ: valeur}), user=_USER)
    assert e.value.status_code == 400
