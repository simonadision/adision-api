# -*- coding: utf-8 -*-
"""TEMOIN — un correctif fait dans un budget remonte au catalogue Ad TYP de
l'ORGANISATION (7 oct. 2026, Simon : « ça fait plusieurs reprises que je
change l'unité de plâtrage de gallon à global… et ça revient toujours »).

Ad TYP est simulé (httpx.MockTransport) : on vérifie les APPELS réellement
faits, pas un nom de fonction.
  - le cas réel : Plâtrage 09 20 00.01 servi depuis la copie Ad FLO de l'org
    → duplication dans la couche custom PUIS PATCH unite=global ;
  - item déjà custom → PATCH direct, aucune duplication ;
  - champ identique (l'écran renvoie toute la ligne) → aucun appel ;
  - ligne sans code Ad TYP → rien ;
  - budget d'une AUTRE organisation que le jeton → rien écrit, et dit ;
  - Ad TYP qui refuse → statut « echec » avec la cause, jamais d'exception.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

import modules.catalogue_writeback as W  # noqa: E402
import modules.ad_budget_api as B  # noqa: E402

_VRAI_CLIENT = httpx.Client


def _brancher(items, patch_status=200):
    appels = []

    def handler(req):
        appels.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if req.method == "GET" and req.url.path.endswith("/typ/custom/catalogue"):
            return httpx.Response(200, json={"items": items})
        if req.method == "POST" and "/from-adflo/" in req.url.path:
            return httpx.Response(200, json={"id": "c-neuf", "origine": "derive"})
        if req.method == "PATCH":
            return httpx.Response(patch_status, json={"updated": True})
        return httpx.Response(404)

    W.httpx.Client = lambda **kw: _VRAI_CLIENT(transport=httpx.MockTransport(handler), **kw)
    return appels


def _debrancher():
    W.httpx.Client = _VRAI_CLIENT


ORG = "org-contracta"
USER = {"organization_id": ORG}
PROJET = {"organization_id": ORG}
AVANT = {"source_typ_code": "09 20 00.01", "unite": "gallon", "description": "Plâtrage"}


def test_platrage_copie_adflo_dupliquee_puis_corrigee():
    appels = _brancher([{"code": "09 20 00.01", "origine": "adflo", "master_id": "m-1", "custom_id": None}])
    try:
        r = B._remonter_au_catalogue(AVANT, {"unite": "global", "description": "Plâtrage"}, PROJET, USER, "jwt")
    finally:
        _debrancher()
    assert r["statut"] == "corrige", r
    assert [(m, p.split("/typ/custom")[-1]) for m, p, _ in appels] == [
        ("GET", "/catalogue"),
        ("POST", "/assemblages/from-adflo/m-1"),
        ("PATCH", "/assemblages/c-neuf"),
    ]
    assert appels[-1][2] == {"unite": "global"}     # seulement ce qui a changé


def test_item_deja_custom_patch_direct():
    appels = _brancher([{"code": "09 20 00.01", "origine": "derive", "master_id": "m-1", "custom_id": "c-7"},
                        {"code": "09 20 00.011", "origine": "custom", "custom_id": "c-autre"}])
    try:
        r = B._remonter_au_catalogue(AVANT, {"unite": "global"}, PROJET, USER, "jwt")
    finally:
        _debrancher()
    assert r["statut"] == "corrige"
    assert [m for m, _, _ in appels] == ["GET", "PATCH"]
    assert appels[-1][1].endswith("/assemblages/c-7")          # le code EXACT, pas l'homonyme


def test_champ_identique_aucun_appel():
    appels = _brancher([])
    try:
        r = B._remonter_au_catalogue(AVANT, {"unite": "gallon", "description": "Plâtrage", "qte": 3}, PROJET, USER, "jwt")
    finally:
        _debrancher()
    assert r is None and appels == []


def test_ligne_manuelle_ou_changement_d_item_rien():
    appels = _brancher([])
    try:
        assert B._remonter_au_catalogue({"unite": "gallon"}, {"unite": "global"}, PROJET, USER, "jwt") is None
        assert B._remonter_au_catalogue(AVANT, {"unite": "global", "source_typ_code": "09 21 00.01"},
                                        PROJET, USER, "jwt") is None
    finally:
        _debrancher()
    assert appels == []


def test_budget_d_une_autre_organisation_rien_ecrit_et_dit():
    appels = _brancher([])
    try:
        r = B._remonter_au_catalogue(AVANT, {"unite": "global"}, {"organization_id": "org-client"},
                                     {"organization_id": ORG}, "jwt")
    finally:
        _debrancher()
    assert r["statut"] == "ignore" and appels == []


def test_refus_d_ad_typ_rapporte_jamais_leve():
    _brancher([{"code": "09 20 00.01", "custom_id": "c-7"}], patch_status=403)
    try:
        r = B._remonter_au_catalogue(AVANT, {"unite": "global"}, PROJET, USER, "jwt")
    finally:
        _debrancher()
    assert r["statut"] == "echec" and "403" in r["message"]


def test_route_branchee_apres_le_commit():
    # La remontée part APRÈS conn.commit() : le budget est enregistré quoi
    # qu'il arrive au catalogue.
    src = open(B.__file__, encoding="utf-8").read()
    i = src.index('def update_budget_ligne(')
    corps = src[i:src.index('return {"status": "updated", "copies": copies, "catalogue": catalogue}', i)]
    assert corps.index("conn.commit()") < corps.index("_remonter_au_catalogue(")


if __name__ == "__main__":
    import inspect
    n = 0
    for nom, f in sorted(inspect.getmembers(sys.modules[__name__], inspect.isfunction)):
        if nom.startswith("test_"):
            f()
            print("OK  ", nom)
            n += 1
    print(f"{n}/{n} verts")
