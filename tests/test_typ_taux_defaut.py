# -*- coding: utf-8 -*-
"""TEMOIN — une ligne Ad TYP sans vrai taux prend le taux par defaut.

16 septembre 2026, Simon (ligne « Portes extérieures en aluminium » a 3 $/h) :
« Taux horaire mauvais doit etre menuisier compagnon par defaut ». L'assemblage
n'a pas de rendement : le mapping posait le MONTANT MO par unite (3 $) comme
taux horaire. 29 lignes dans 13 projets portaient ce pseudo-taux.
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


class _Cur:
    def __init__(self, taux):
        self.taux = taux
        self.params = []

    def execute(self, sql, params=None):
        self.params.append(params)

    def fetchone(self):
        return {"taux_col17": self.taux} if self.taux is not None else None

    def close(self):
        pass


class _Conn:
    def __init__(self, taux):
        self.cur = _Cur(taux)

    def cursor(self, *a, **k):
        return self.cur


def _A():
    from modules import ad_budget_api as A
    return A


PORTES = {"code": "08 11 16.01", "description": "Portes extérieures en aluminium",
          "prix_mat": 1500, "prix_mo": 3, "prix_st": 0, "heures_unit": 0}


def test_mo_sans_rendement_prend_le_taux_par_defaut_et_garde_le_montant():
    A = _A()
    m = A._map_typ_to_budget_cols(PORTES, 2, _Conn(Decimal("84.64")))
    assert m["mo_flat"] is True
    assert m["taux_horaire"] == 84.64
    # MO Ad TYP conservée : heures × taux = 3 $ × 2.
    assert abs(m["heures"] * m["taux_horaire"] - 6) < 0.01


def test_aucune_mo_prend_le_taux_par_defaut():
    A = _A()
    sans_mo = dict(PORTES, prix_mo=0)
    m = A._map_typ_to_budget_cols(sans_mo, 5, _Conn(Decimal("84.64")))
    assert m["taux_horaire"] == 84.64
    assert m["heures"] == 0


def test_rendement_reel_inchange():
    A = _A()
    avec_rendement = dict(PORTES, prix_mo=169.28, heures_unit=2)
    m = A._map_typ_to_budget_cols(avec_rendement, 1, _Conn(Decimal("84.27")))
    assert m["mo_flat"] is False
    assert m["taux_horaire"] == 84.64      # prix_mo / heures_unit, pas le défaut
    assert m["heures"] == 2


def test_sans_connexion_comportement_historique():
    A = _A()
    m = A._map_typ_to_budget_cols(PORTES, 2)
    assert m["taux_horaire"] == 3 and m["heures"] == 2


def test_jamais_de_pseudo_taux_dans_les_appelants_qui_ecrivent_le_taux():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "modules",
                            "ad_budget_api.py"), encoding="utf-8").read()
    # from-typ (création), refresh-typ (⟳) et apply-typ passent la connexion.
    assert "m = _map_typ_to_budget_cols(typ, qte, conn)" in src
    assert 'm = _map_typ_to_budget_cols(typ, ligne["qte"], conn)' in src
    gab = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "modules",
                            "ad_gabarits_api.py"), encoding="utf-8").read()
    assert "_map_typ_to_budget_cols(typ, 0, cur.connection)" in gab


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
