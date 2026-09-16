# -*- coding: utf-8 -*-
"""TEMOIN — une ligne piochee dans Ad TYP porte un code CSI, jamais Uniformat.

16 septembre 2026, Simon : « aucun rapport ce code CSI bug ». L'assemblage
« Contreplaqué CB Fir-5/8-4x8 » (code Uniformat B30110305, section CSI
06 14 00) arrivait dans Ad BUD sous le code « B30110305 », range en « 30 11 ».
472 des 577 assemblages Ad TYP ont un code Uniformat ; leur CSI vit dans
csi_section_code / csi_division_code.

Sans base : la regle de _section_csi_typ + les UPDATE qui gardent la section
quand l'assemblage n'a aucun CSI.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "modules", "ad_budget_api.py")


def _A():
    from modules import ad_budget_api as A
    return A


def test_code_csi_garde_tel_quel():
    A = _A()
    typ = {"code": "06 40 00.01", "csi_section_code": "06 40 00", "csi_division_code": "06"}
    assert A._section_csi_typ(typ) == "06 40 00.01"
    assert A._section_csi_typ({"code": "09 29 01"}) == "09 29 01"


def test_code_uniformat_prend_la_section_csi():
    A = _A()
    typ = {"code": "B30110305", "description": "Contreplaqué CB Fir-5/8-4x8",
           "csi_section_code": "06 14 00", "csi_division_code": "06"}
    assert A._section_csi_typ(typ) == "06 14 00.01"
    m = A._map_typ_to_budget_cols(typ, 0)
    assert m["section"] == "06 14 00.01"


def test_code_uniformat_sans_section_prend_la_division():
    A = _A()
    typ = {"code": "C102102021", "csi_section_code": None, "csi_division_code": "09"}
    assert A._section_csi_typ(typ) == "09 00 00.01"


def test_aucun_csi_ne_pose_jamais_le_code_uniformat():
    A = _A()
    typ = {"code": "G40110601", "csi_section_code": None, "csi_division_code": None}
    assert A._section_csi_typ(typ) is None
    assert A._map_typ_to_budget_cols(typ, 0)["section"] is None


def test_updates_gardent_la_section_sans_csi():
    src = open(SRC, encoding="utf-8").read()
    # Toute ecriture de section issue du mapping Ad TYP dans un UPDATE doit
    # garder la section existante quand le mapping n'en donne aucune.
    nus = re.findall(r"UPDATE ad_budget\.budget_lignes SET\s+section=%s", src)
    assert not nus, "UPDATE budget_lignes SET section=%s sans COALESCE : %d" % len(nus)
    assert src.count("section=COALESCE(%s, section)") >= 3


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
