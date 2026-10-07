# -*- coding: utf-8 -*-
"""TEMOIN — « Compléter depuis le gabarit » (7 oct. 2026).

Simon, 13 h 32 : « pourquoi mon gabarit n'est pas comme celui par défaut ».
Le budget 328 (créé 12 h 43) n'avait en 02 que les 2 lignes Ad TYP ; le
gabarit 7 « Général Simon », enrichi ensuite, en porte 7. Ce banc fige la
règle de correspondance :

  - le cas réel du 328 : 5 lignes manuelles manquantes, les 2 Ad TYP non ;
  - Ad TYP reconnu par code, manuelle par description (casse, accents,
    espaces ignorés), SANS regarder la section (une ligne déplacée ne revient
    pas en double) ;
  - multi-ensemble : deux fois le même item au gabarit, une fois au budget
    => une manquante, pas deux ;
  - une ligne désactivée compte comme présente ; les lignes vides ne comptent
    pas ;
  - la clé d'une manquante est stable d'un aperçu à l'autre.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import modules.ad_gabarits_api as G  # noqa: E402


def _gabarit_02():
    return [{
        "numero": "02 00 00", "nom_section": "Conditions existantes",
        "sous_sections": [{"code_csi": "02 00 00", "lignes": [
            {"type": "ad_typ", "description": "Démolition sélective", "code_typ": "02 03 01.01"},
            {"type": "manuelle", "description": "Démolition architecture", "code_typ": None},
            {"type": "manuelle", "description": "Démolition structure", "code_typ": None},
            {"type": "manuelle", "description": "Sciage et forage", "code_typ": None},
            {"type": "ad_typ", "description": "Décontamination", "code_typ": "02 56 13.01"},
            {"type": "manuelle", "description": "Amiante", "code_typ": None},
            {"type": "manuelle", "description": "Peinture au plomb", "code_typ": None},
        ]}],
    }]


def test_le_cas_du_budget_328():
    budget = [
        {"description": "Démolition sélective", "source_typ_code": "02 03 01.01"},
        {"description": "Décontamination", "source_typ_code": "02 56 13.01"},
    ]
    m = G.lignes_gabarit_manquantes(_gabarit_02(), budget)
    assert [x["description"] for x in m] == [
        "Démolition architecture", "Démolition structure", "Sciage et forage",
        "Amiante", "Peinture au plomb"]
    assert all(x["type"] == "manuelle" and x["section"] == "02 00 00" for x in m)


def test_rien_ne_manque_quand_tout_y_est():
    budget = [{"description": l["description"], "source_typ_code": l["code_typ"] if l["type"] == "ad_typ" else None}
              for l in _gabarit_02()[0]["sous_sections"][0]["lignes"]]
    assert G.lignes_gabarit_manquantes(_gabarit_02(), budget) == []


def test_manuelle_par_description_casse_accents_espaces_ignores_et_section_ignoree():
    budget = [
        {"description": "  DEMOLITION   architecture ", "source_typ_code": None, "section": "99 00 00"},
        {"description": "Démolition sélective", "source_typ_code": "02 03 01.01"},
        {"description": "Décontamination", "source_typ_code": "02 56 13.01"},
    ]
    m = G.lignes_gabarit_manquantes(_gabarit_02(), budget)
    assert "Démolition architecture" not in [x["description"] for x in m]
    assert len(m) == 4


def test_ad_typ_par_code_meme_si_la_description_a_change():
    budget = [{"description": "Démo renommée", "source_typ_code": "02 03 01.01"}]
    m = G.lignes_gabarit_manquantes(_gabarit_02(), budget)
    assert "02 03 01.01" not in [x["code_typ"] for x in m]
    assert "02 56 13.01" in [x["code_typ"] for x in m]


def test_multi_ensemble():
    gab = [{"numero": "03 00 00", "sous_sections": [{"code_csi": "03 11 00", "lignes": [
        {"type": "ad_typ", "description": "Coffrage", "code_typ": "03 11 00.01"},
        {"type": "ad_typ", "description": "Coffrage", "code_typ": "03 11 00.01"},
    ]}]}]
    m = G.lignes_gabarit_manquantes(gab, [{"description": "Coffrage", "source_typ_code": "03 11 00.01"}])
    assert len(m) == 1
    assert m[0]["cle"] == "typ|03 11 00.01|1"


def test_lignes_vides_ignorees_des_deux_cotes():
    gab = [{"numero": "05 00 00", "sous_sections": [{"code_csi": "05 00 00", "lignes": [
        {"type": "manuelle", "description": "", "code_typ": None},
        {"type": "manuelle", "description": "Acier", "code_typ": None},
    ]}]}]
    m = G.lignes_gabarit_manquantes(gab, [{"description": "", "source_typ_code": None}])
    assert [x["description"] for x in m] == ["Acier"]


def test_cle_stable_d_un_apercu_a_l_autre():
    a = G.lignes_gabarit_manquantes(_gabarit_02(), [])
    b = G.lignes_gabarit_manquantes(_gabarit_02(), [])
    assert [x["cle"] for x in a] == [x["cle"] for x in b]
    assert len({x["cle"] for x in a}) == len(a) == 7


def test_insert_et_completer_partagent_la_meme_insertion():
    # Une ligne complétée doit naître EXACTEMENT comme une ligne insérée :
    # les deux routes appellent _inserer_ligne_gabarit, aucune ne réécrit
    # l'INSERT à sa façon.
    src = open(G.__file__, encoding="utf-8").read()
    appels = [l for l in src.splitlines() if "_inserer_ligne_gabarit(cur, projet_id," in l and "def " not in l]
    assert len(appels) == 2
    assert src.count("INSERT INTO ad_budget.budget_lignes") == 2  # Ad TYP + _insert_manual


def test_jumelle_manuelle_d_une_ligne_ad_typ_n_est_pas_un_manque():
    # Budget 328 réel : Assurances / Cautionnement en MANUELLES, le gabarit
    # les porte en Ad TYP (01 00 00.07). Ce n'est pas un manque.
    gab = [{"numero": "01 00 00", "sous_sections": [{"code_csi": "01 00 00", "lignes": [
        {"type": "ad_typ", "description": "Assurances", "code_typ": "01 00 00.07"},
        {"type": "ad_typ", "description": "Cautionnement", "code_typ": "01 00 00.07"},
        {"type": "manuelle", "description": "Frais de gardiennage", "code_typ": None},
    ]}]}]
    budget = [{"description": "Assurances", "source_typ_code": None},
              {"description": "Cautionnement", "source_typ_code": None}]
    m = G.lignes_gabarit_manquantes(gab, budget)
    assert [x["description"] for x in m] == ["Frais de gardiennage"]


def test_le_code_exact_passe_avant_la_jumelle_par_description():
    # Une ligne Ad TYP du budget au BON code ne doit pas être « prise » par
    # une ligne manuelle homonyme du gabarit, laissant l'Ad TYP en manque.
    gab = [{"numero": "02 00 00", "sous_sections": [{"code_csi": "02 00 00", "lignes": [
        {"type": "manuelle", "description": "Décontamination", "code_typ": None},
        {"type": "ad_typ", "description": "Décontamination", "code_typ": "02 56 13.01"},
    ]}]}]
    budget = [{"description": "Décontamination", "source_typ_code": "02 56 13.01"}]
    m = G.lignes_gabarit_manquantes(gab, budget)
    assert [(x["type"], x["description"]) for x in m] == [("manuelle", "Décontamination")]


if __name__ == "__main__":
    import inspect
    n = 0
    for nom, f in sorted(inspect.getmembers(sys.modules[__name__], inspect.isfunction)):
        if nom.startswith("test_"):
            f()
            print("OK  ", nom)
            n += 1
    print(f"{n}/{n} verts")
