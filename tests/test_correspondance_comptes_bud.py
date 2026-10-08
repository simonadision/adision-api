# -*- coding: utf-8 -*-
"""Le compte charte Q de chaque nature d'une ligne d'Ad BUD — banc du PORT.

8 oct. 2026 (PC1). modules/correspondance_comptes.py est un port de
adision-con-api/modules/correspondance_comptes.py (origin/main 4c2d101). Ce
banc REPREND PAR LEUR NOM les cas du banc d'Ad CON
(adision-con-api/tests/test_comptes_par_nature.py) : quand un cas porte le
même nom des deux côtés, il dit la même chose — c'est la seule façon, d'ici
l'issue monorepo #1089 (fichier de cas partagé
packages/aggregates/cas/charte-q-natures.json, pas encore écrit), de voir
deux implémentations diverger.

Ce qui change par rapport à Ad CON, et que les cas propres à Ad BUD épinglent :
  - les natures se lisent sur les MONTANTS d'Ad BUD (`montants_par_nature`),
    pas sur des sous-totaux stockés ; la main-d'œuvre passe par
    `heures_effectives` (unité « hr », production) — jamais recopiée ;
  - le code CSI est `section` ; l'ancien compte unique est
    `compte_metier_force`.

Banc PUR : aucune base, aucun FastAPI, aucun réseau (`charger` est éprouvé
avec un faux urlopen).
    python tests/test_correspondance_comptes_bud.py
"""
import json
import os
import sys
import urllib.request

_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _RACINE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from modules import correspondance_comptes as cc  # noqa: E402
from modules import hub_service  # noqa: E402

ECHECS = []


def _rouge(msg):
    ECHECS.append(msg)
    print("  ROUGE  " + msg)


def _vert(msg):
    print("  ok     " + msg)


def _egal(obtenu, attendu, msg):
    if obtenu == attendu:
        _vert(msg)
    else:
        _rouge("%s — obtenu %r, attendu %r" % (msg, obtenu, attendu))


ORG = "64c45c53-ff2b-40ce-b05d-fe0a3f5144c0"
AUTRE_ORG = "870c8388-4b9c-4ab6-b8e6-bbc8410638c3"

# Mêmes chartes que le banc d'Ad CON.
CORR = {
    "organization_id": ORG,
    "paires": {"06 00 00|MAT": "56100", "06 00 00|ST": "55005"},
    "regles_nature": {"MO": "50005", "ST": "59998"},
    "n_paires": 2, "n_regles": 2,
}
PREF = {"organization_id": ORG, "paires": {
    "03 11 00|MAT": "56035",      # groupe : couvre 03 11 13, 03 11 00.01…
    "03 30 00|MAT": "56050",
    "02|MAT": "56015",            # repli de DIVISION, écrit explicitement
    "02 82|MAT": "56020",         # plus précis que « 02 »
    "04 00 00|MAT": "56070",      # EXACT : ne couvre pas 04 21 13
    "01 00 00.07|MAT": "50100",   # code d'ITEM
}, "regles_nature": {"MO": "50005", "ST": "59998"}}


def _ligne(section=None, mat=0, mo=0, st=0, **kw):
    """Une ligne d'Ad BUD dont les natures valent `mat`, `mo`, `st` $.
    MAT = qte × prix ; MO = heures × taux (unité neutre) ; ST = montant
    (qte > 0 exigée par la règle #2 — qte vaut 1 dès qu'une nature existe)."""
    l = {"section": section, "unite": "u", "qte": 1 if (mat or mo or st) else 0,
         "prix_unitaire": mat, "heures": 1 if mo else 0, "taux_horaire": mo,
         "sous_traitant_montant": st, "compte_metier_force": None,
         "compte_mat": None, "compte_mo": None, "compte_st": None}
    l.update(kw)
    return l


# ══════════════════════════════════════════════════════════════════════════
# LES NATURES PRÉSENTES, LUES SUR LES MONTANTS D'AD BUD (propre à ce port).
# ══════════════════════════════════════════════════════════════════════════

def test_natures_presentes_depuis_les_montants_bud():
    _egal(cc.natures_de_la_ligne(_ligne(st=500)), ["ST"],
          "une ligne qui n'a QU'UN montant ST n'a que la nature ST")
    _egal(cc.natures_de_la_ligne(_ligne(mat=10)), ["MAT"], "matériaux seuls -> MAT")
    _egal(cc.natures_de_la_ligne(_ligne(mat=1, mo=2, st=3)), ["MAT", "MO", "ST"],
          "trois montants -> trois natures")
    _egal(cc.natures_de_la_ligne(_ligne()), [], "aucun montant -> aucune nature")


def test_un_montant_NEGATIF_compte_aussi():
    _egal(cc.natures_de_la_ligne(_ligne(mat=-250)), ["MAT"],
          "un crédit est un montant (cas d'Ad CON)")


def test_la_MO_passe_par_heures_effectives():
    """Unité « hr » : heures = qté ; production : heures = qté / production.
    Si le port recopiait la règle au lieu de l'importer, l'un des deux
    tomberait — et tools/verifier_source_unique.py le dirait aussi."""
    hr = {"section": "01", "unite": "hr", "qte": 8, "heures": 0, "taux_horaire": 50}
    _egal(cc.natures_de_la_ligne(hr), ["MO"], "unité hr, heures vides -> MO présente")
    prod = {"section": "01", "unite": "p2", "qte": 128, "heures": 0,
            "production_valeur": 32, "taux_horaire": 50}
    _egal(cc.natures_de_la_ligne(prod), ["MO"], "MO par production -> présente")
    _egal(cc.montants_par_nature(prod)["MO"], 200.0, "128 / 32 = 4 h × 50 $ = 200 $")


def test_un_TAUX_seul_ne_fait_PAS_exister_la_MO():
    """8 oct. 2026 (PC3, relayé par PC4) : dans Ad EST, la MO était comptée dès
    que le taux était > 0 -- et Ad EST remplit ce taux tout seul : 50005 et
    59998 s'affichaient sur des lignes à 0 $. Une nature existe par son
    MONTANT : un taux sans heures effectives n'en fait pas une."""
    _egal(cc.natures_de_la_ligne({"qte": 3, "unite": "pi2", "prix_unitaire": 0,
                                  "heures": 0, "taux_horaire": 85}), [],
          "taux 85 $/h, 0 h, prix 0 -> aucune nature")
    _egal(cc.natures_de_la_ligne({"qte": 4, "unite": "hr", "taux_horaire": 85}), ["MO"],
          "unité « hr » : les heures sont dans la quantité -> MO")
    _egal(cc.natures_de_la_ligne({"qte": 50, "unite": "pi2", "production_valeur": 10,
                                  "taux_horaire": 85}), ["MO"],
          "production : heures = quantité / production -> MO")


def test_ST_a_quantite_nulle_n_existe_pas():
    """Règle #2 de Simon (17 août 2026) : QTÉ=0 exclut le montant ST."""
    _egal(cc.natures_de_la_ligne({"qte": 0, "sous_traitant_montant": 900}), [],
          "ST à qté 0 -> aucune nature")


def test_la_quantite_effective_sert_aux_materiaux():
    """Unité « % » : qté / 100 ; facteur d'unité : × facteur."""
    pct = {"unite": "%", "qte": 2, "prix_unitaire": 1010098}
    _egal(round(cc.montants_par_nature(pct)["MAT"], 2), 20201.96,
          "contingence 2 % à 1 010 098 $ -> 20 201,96 $")


# ══════════════════════════════════════════════════════════════════════════
# L'ORDRE DE RÉSOLUTION — a0 > a > b > b' > c > d (noms du banc d'Ad CON).
# ══════════════════════════════════════════════════════════════════════════

def test_a0_chaque_nature_garde_SON_compte_enregistre():
    l = _ligne("06 00 00", mat=9000, mo=15574, compte_mat="55010", compte_mo="50005")
    _egal(cc.comptes_par_nature(l, CORR), {"MAT": "55010", "MO": "50005"},
          "(a0) chaque nature garde son compte enregistré")


def test_a0_le_compte_enregistre_bat_l_ancien_compte_unique_ET_la_paire():
    l = _ligne("06 00 00", st=100, compte_metier_force="11111", compte_st="22222")
    _egal(cc.comptes_par_nature(l, CORR).get("ST"), "22222",
          "(a0) bat (a) l'ancien compte unique ET (b) la paire")


def test_a0_nature_sans_compte_enregistre_retombe_sur_la_charte():
    l = _ligne("06 00 00", mat=1, mo=1, compte_mat="55010")
    _egal(cc.comptes_par_nature(l, CORR), {"MAT": "55010", "MO": "50005"},
          "(a0) une nature enregistrée ne fige pas les autres")


def test_a0_un_compte_enregistre_pour_une_nature_ABSENTE_n_est_pas_servi():
    l = _ligne("06 00 00", mat=1, compte_st="59998")
    _egal("ST" in cc.comptes_par_nature(l, CORR), False,
          "(a0) un compte pour une nature absente n'est pas servi")


def test_une_ligne_a_NATURE_UNIQUE_garde_SON_compte():
    """« 01 00 00 Frais de transport » : MO seule, compte 51001 (cas d'Ad CON)."""
    l = _ligne("01 00 00", mo=5078.40, compte_metier_force="51001")
    _egal(cc.comptes_par_nature(l, CORR), {"MO": "51001"},
          "(a) l'ancien compte unique va à la nature UNIQUE, pas à la règle")


def test_la_ligne_a_nature_unique_gagne_AUSSI_contre_une_paire():
    l = _ligne("06 00 00", st=100, compte_metier_force="99999")
    _egal(cc.comptes_par_nature(l, CORR).get("ST"), "99999", "(a) bat (b)")


def test_PLUSIEURS_natures_le_compte_de_la_ligne_revient_au_MATERIAU():
    l = _ligne("06 00 00", mat=2031.36, mo=338.56, compte_metier_force="56100")
    _egal(cc.comptes_par_nature(l, CORR), {"MAT": "56100", "MO": "50005"},
          "(a) plusieurs natures : l'ancien compte au MATÉRIAU seul")
    l2 = _ligne("99 99 99", mo=1, st=1, compte_metier_force="77777")
    _egal(cc.comptes_par_nature(l2, CORR), {"MO": "50005", "ST": "59998"},
          "(a) plusieurs natures sans MAT : l'ancien compte ne va NULLE PART")


def test_la_PAIRE_passe_avant_la_REGLE_de_nature():
    l = _ligne("06 00 00", mat=1, st=2)
    _egal(cc.comptes_par_nature(l, CORR).get("ST"), "55005", "(b) avant (c)")


def test_la_REGLE_de_nature_sert_quand_il_n_y_a_PAS_de_paire():
    r = cc.comptes_par_nature(_ligne("99 99 99", mat=1, mo=2), CORR)
    _egal(r, {"MAT": None, "MO": "50005"}, "(c) la règle, et MAT sans règle reste None")


def test_un_code_SANS_correspondance_reste_NON_ASSOCIE_et_y_RESTE():
    _egal(cc.comptes_par_nature(_ligne("99 99 99", mat=1), CORR), {"MAT": None},
          "(d) jamais de compte fourre-tout")


def test_une_ligne_SANS_aucun_montant_ne_rend_RIEN():
    _egal(cc.comptes_par_nature(_ligne("06 00 00"), CORR), {}, "aucune nature, aucun compte")


def test_SANS_correspondance_tout_tombe_en_non_associe_sans_lever():
    _egal(cc.comptes_par_nature(_ligne("06 00 00", mat=1, mo=2), None),
          {"MAT": None, "MO": None}, "hub muet : None partout, rien d'inventé")
    l = _ligne("06 00 00", mat=1, compte_mat="55010")
    _egal(cc.comptes_par_nature(l, None), {"MAT": "55010"},
          "hub muet : le compte ENREGISTRÉ reste servi (export-for-con)")


def test_le_code_CSI_est_DETOURE_avant_comparaison():
    _egal(cc.comptes_par_nature(_ligne("  06 00 00  ", mat=1), CORR).get("MAT"), "56100",
          "espaces autour de la section")


# ── Le plus long préfixe posé, et `_morceaux` ──────────────────────────────

def test_le_SUFFIXE_d_item_ne_cache_plus_la_paire():
    _egal(cc.comptes_par_nature(_ligne("03 11 00.01", mat=1), PREF)["MAT"], "56035",
          "« 03 11 00.01 » trouve « 03 11 00 »")


def test_une_paire_DD_DD_00_couvre_son_GROUPE():
    _egal(cc.comptes_par_nature(_ligne("03 11 13.02", mat=1), PREF)["MAT"], "56035",
          "« 03 11 00 » couvre 03 11 13")


def test_le_PLUS_LONG_prefixe_gagne():
    _egal(cc.comptes_par_nature(_ligne("02 82 13.01", mat=1), PREF)["MAT"], "56020",
          "« 02 82 » bat « 02 » (le plus long préfixe)")
    _egal(cc.comptes_par_nature(_ligne("02 41 19.01", mat=1), PREF)["MAT"], "56015",
          "« 02 » sert de repli de division")


def test_DD_00_00_reste_EXACT_et_n_avale_pas_la_division():
    _egal(cc.comptes_par_nature(_ligne("04 00 00.03", mat=1), PREF)["MAT"], "56070",
          "« 04 00 00 » couvre 04 00 00.03")
    _egal(cc.comptes_par_nature(_ligne("04 21 13", mat=1), PREF)["MAT"], None,
          "« 04 00 00 » ne couvre PAS 04 21 13")
    _egal(cc._morceaux("04 00 00", paire=True), ("04", "00", "00"), "_morceaux DD 00 00 exact")
    _egal(cc._morceaux("03 11 00", paire=True), ("03", "11"), "_morceaux DD DD 00 -> groupe")
    _egal(cc._morceaux("03 11 00"), ("03", "11", "00"), "_morceaux côté LIGNE : jamais réduit")


def test_un_code_d_ITEM_ne_couvre_que_cet_item():
    _egal(cc.comptes_par_nature(_ligne("01 00 00.07", mat=1), PREF)["MAT"], "50100", "item exact")
    _egal(cc.comptes_par_nature(_ligne("01 00 00.11", mat=1), PREF)["MAT"], None, "autre item")


def test_un_code_qui_n_est_pas_CSI_ne_correspond_a_rien():
    for c in ("Divers", "0", "", "03 1", "03 11 00 00"):
        _egal(cc.comptes_par_nature(_ligne(c, mat=1), PREF)["MAT"], None, "code %r" % c)


# ── (b') les interrupteurs MO / ST vers le compte du métier ────────────────

def test_l_INTERRUPTEUR_envoie_MO_et_ST_au_compte_du_METIER():
    corr = dict(PREF, options={"mo_vers_metier": True})
    _egal(cc.comptes_par_nature(_ligne("03 11 00.01", mat=1, mo=1, st=1), corr),
          {"MAT": "56035", "MO": "56035", "ST": "59998"}, "(b') mo_vers_metier")
    _egal(cc.comptes_par_nature(_ligne("03 11 00.01", mat=1, mo=1, st=1), corr,
                                options_projet={"st_vers_metier": True, "mo_vers_metier": False}),
          {"MAT": "56035", "MO": "50005", "ST": "56035"}, "(b') st_vers_metier par projet")


def test_l_INTERRUPTEUR_ne_passe_JAMAIS_devant_le_compte_pose_a_la_main():
    corr = dict(PREF, options={"mo_vers_metier": True})
    l = _ligne("03 11 00.01", mo=5, compte_metier_force="51001")
    _egal(cc.comptes_par_nature(l, corr)["MO"], "51001", "(a) avant (b')")


def test_l_interrupteur_sans_compte_metier_retombe_sur_la_regle():
    corr = dict(PREF, options={"mo_vers_metier": True})
    _egal(cc.comptes_par_nature(_ligne("99 99 99", mo=1), corr)["MO"], "50005", "(b') puis (c)")


# ══════════════════════════════════════════════════════════════════════════
# `charger` : ne lève jamais, garde 036, contrôle de type, cache, JWT, URL.
# ══════════════════════════════════════════════════════════════════════════

class _FausseReponse:
    def __init__(self, charge, ctype="application/json"):
        self._octets = json.dumps(charge).encode("utf-8")
        self.headers = {"Content-Type": ctype}

    def read(self):
        return self._octets

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _avec_urlopen(remplacant, fn):
    vrai = urllib.request.urlopen
    urllib.request.urlopen = remplacant
    try:
        return fn()
    finally:
        urllib.request.urlopen = vrai


def test_charger_NE_LEVE_JAMAIS_si_le_hub_est_muet():
    cc.vider_le_cache()

    def explose(*_a, **_k):
        raise OSError("connexion refusee")
    try:
        r = _avec_urlopen(explose, lambda: cc.charger("jeton", ORG))
        _egal(r, None, "hub muet -> None, sans lever")
    except Exception as e:  # noqa: BLE001
        _rouge("charger a LEVÉ : %r" % e)


def test_la_charte_d_une_AUTRE_organisation_est_REJETEE():
    """LA GARDE 036 : le hub sert une autre organisation que celle du projet."""
    cc.vider_le_cache()
    intruse = dict(CORR, organization_id=AUTRE_ORG)
    r = _avec_urlopen(lambda *a, **k: _FausseReponse(intruse), lambda: cc.charger("jeton", ORG))
    _egal(r, None, "036 : la charte d'une autre entreprise est rejetée")
    _egal(ORG in cc._CACHE, False, "036 : et elle n'est PAS mise en cache")


def test_une_reponse_HTML_est_NOMMEE_et_refusee():
    cc.vider_le_cache()
    r = _avec_urlopen(lambda *a, **k: _FausseReponse(CORR, ctype="text/html"),
                      lambda: cc.charger("jeton", ORG))
    _egal(r, None, "réponse HTML (front SPA) refusée")


def test_la_bonne_charte_est_SERVIE_et_MISE_EN_CACHE():
    """Témoin POSITIF : sans lui, un `charger` qui rendrait toujours None
    ferait passer les cas ci-dessus au vert sans rien vérifier."""
    cc.vider_le_cache()
    appels = []

    def faux(req, timeout=None):
        appels.append((req.full_url, req.get_header("Authorization"), timeout))
        return _FausseReponse(CORR)
    r1 = _avec_urlopen(faux, lambda: cc.charger("jeton-de-simon", ORG))
    _egal(bool(r1) and r1.get("n_paires"), 2, "la bonne charte est servie")
    r2 = _avec_urlopen(faux, lambda: cc.charger("jeton-de-simon", ORG))
    _egal((r2 is not None, len(appels)), (True, 1), "deuxième appel : le cache répond")
    if appels:
        url, auth, timeout = appels[0]
        _egal(url, hub_service.HUB_API_URL + "/api/codification/correspondance-comptes",
              "l'adresse vient de hub_service.HUB_API_URL")
        _egal(auth, "Bearer jeton-de-simon", "le JWT de l'appelant est transmis")
        _egal(timeout, 3.0, "délai de 3 s")


def test_sans_organisation_on_ne_demande_RIEN():
    cc.vider_le_cache()

    def interdit(*_a, **_k):
        raise AssertionError("le hub a été appelé sans organisation")
    try:
        _egal(_avec_urlopen(interdit, lambda: cc.charger("jeton", None)), None,
              "sans organisation : None, aucun appel")
    except AssertionError as e:
        _rouge(str(e))


def test_l_adresse_du_hub_vient_de_hub_service_et_PAS_d_une_constante_locale():
    _egal(cc._url_hub(), hub_service.HUB_API_URL, "une seule adresse de hub dans le service")


if __name__ == "__main__":
    for nom, f in list(globals().items()):
        if nom.startswith("test_") and callable(f):
            print(nom)
            f()
    cc.vider_le_cache()
    if ECHECS:
        print("\n%d échec(s)" % len(ECHECS))
        sys.exit(1)
    print("\ncorrespondance_comptes (Ad BUD) : tout est vert")
