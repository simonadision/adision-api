# -*- coding: utf-8 -*-
"""TEMOIN — le serveur et l'ecran rendent les MEMES nombres sur une version
debalancee.

CE QU'IL PROTEGE. `derive_version` (Python) et `appliquerVersion` (JS,
@adision/aggregates) calculent la meme chose des deux cotes de la frontiere.
S'ils divergent, Ad CON affiche une derive que le serveur ne voit pas, ou
l'inverse — et personne ne sait lequel a raison.

POURQUOI IL N'EXISTAIT PAS. La fixture
`packages/aggregates/src/__fixtures__/versionsDebalancees.json` avait ete
convenue avec PC4 le 5 octobre. **Aucun test ne l'ouvrait.** Pas par oubli :
`derive_version` s'appelait `_derive` et vivait a l'interieur de
`register_ad_budget_routes`, donc elle n'etait IMPORTABLE PAR AUCUN TEST. La
fixture avait l'air d'une garantie et n'en etait pas une.

POURQUOI UNE COPIE LOCALE. Le precedent `test_invariant_b_ligne_python_js.py`
lit la fixture dans le depot FRERE et se SAUTE quand il est absent — donc en
CI, c'est-a-dire la ou ca compte. Un banc qui ne tourne pas ne protege rien.
Ici, la fixture est copiee dans `tests/fixtures/` et lue TOUJOURS ; un controle
supplementaire, actif seulement quand le frere est la, verifie que les deux
copies sont IDENTIQUES. La divergence est donc attrapee avant le push, et la
parite est verifiee a chaque passage.

AUTONOME, SANS PYTEST.
"""
import hashlib
import json
import os
import sys

_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _RACINE)

FIXTURE = os.path.join(_RACINE, "tests", "fixtures", "versionsDebalancees.json")
FRERE = os.path.join(os.path.dirname(_RACINE), "adision-monorepo", "packages",
                     "aggregates", "src", "__fixtures__",
                     "versionsDebalancees.json")

from modules.ad_budget_api import derive_version  # noqa: E402


def _rouge(msg):
    raise AssertionError(msg)


def _fixture():
    if not os.path.isfile(FIXTURE):
        # Le banc se NOMME au lieu de se sauter : une fixture absente est une
        # panne du banc, pas une raison de passer au vert.
        _rouge("fixture introuvable : %s — le banc ne peut rien verifier" % FIXTURE)
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def _lignes(cas):
    """La fixture donne une LISTE ; le serveur travaille sur une carte par id,
    telle que `_lignes_pour_version` la rend."""
    return {l["id"]: l for l in cas["lignes"]}


def _facteurs(cas):
    """La fixture donne des facteurs nus ; la version STOCKE, pour chaque
    ligne, le facteur, la base du moment et l'exclusion constatee alors."""
    bases = cas.get("bases") or {}
    sortie = {}
    for lid, f in (cas.get("facteurs") or {}).items():
        entree = {"facteur": f}
        if lid in bases:
            entree["base"] = bases[lid]
        sortie[str(lid)] = entree
    return sortie


# ══════════════════════════════════════════════════════════════════════════
# LA PARITE, CAS PAR CAS
# ══════════════════════════════════════════════════════════════════════════

def test_la_fixture_a_des_CAS():
    """Anti-vacuite : une fixture vide ferait passer tous les cas suivants."""
    cas = _fixture().get("cas") or []
    if len(cas) < 5:
        _rouge("la fixture n'a que %d cas : le banc ne couvre rien" % len(cas))


def test_chaque_cas_rend_la_DERIVE_attendue():
    for i, cas in enumerate(_fixture()["cas"]):
        attendu = (cas.get("attendu") or {}).get("derive")
        if not attendu:
            continue
        obtenu = derive_version(_lignes(cas), _facteurs(cas))
        for cle, valeur in attendu.items():
            if cle not in obtenu:
                _rouge("cas %d « %s » : le serveur ne rend pas « %s »"
                       % (i, cas["nom"], cle))
            if abs(float(obtenu[cle]) - float(valeur)) > 0.005:
                _rouge(
                    "cas %d « %s » : %s vaut %s cote serveur, %s cote ecran.\n"
                    "   Les deux cotes de la frontiere ne comptent pas pareil : "
                    "Ad CON afficherait une derive que le serveur ne voit pas, "
                    "ou l'inverse."
                    % (i, cas["nom"], cle, obtenu[cle], valeur))


def test_le_cas_des_lignes_EXCLUES_ne_derive_pas():
    """LE CAS QUI A MOTIVE CE BANC, ET LE CHIFFRE EXACT.

    Le cas « lignes « %% » et au mille » porte un facteur 2 sur « cont »
    (500 $, unite « %% ») et « caut » (120 $, cautionnement), A DESSEIN. Le JS
    attend des totaux INCHANGES et un ecart de 0.

    Avant correction, `_derive` appliquait le facteur stocke sans regarder
    l'exclusion et rendait **+620,00 $** — soit 500 + 120, les deux lignes
    doublees. Trouve par PC4 le 6 octobre 2026.
    """
    cas = [c for c in _fixture()["cas"] if "mille" in c["nom"]]
    if not cas:
        _rouge("le cas des lignes exclues a disparu de la fixture : "
               "ce banc ne verifie plus ce pour quoi il a ete ecrit")
    d = derive_version(_lignes(cas[0]), _facteurs(cas[0]))
    if abs(d["ecart_courant"]) > 0.005:
        _rouge("une ligne « %% » ou au mille a ete mise a l'echelle : ecart "
               "%+.2f $ au lieu de 0. L'etirer cree la boucle meme que "
               "l'exclusion existe pour empecher — la famille des 346 680 $ "
               "du 1er octobre." % d["ecart_courant"])


def test_une_ligne_EXCLUE_sans_facteur_n_est_pas_comptee():
    """Cote JS, une ligne exclue n'est jamais « sans facteur » : elle n'avait
    pas a en avoir un. La compter ferait dire a l'ecran qu'il manque des
    facteurs alors que tout est en ordre."""
    lignes = {1: {"total": 1000, "unite": "u", "description": "Beton"},
              2: {"total": 500, "unite": "%", "description": "Contingence"},
              3: {"total": 120, "unite": "u", "description": "Cautionnement"}}
    d = derive_version(lignes, {"1": {"facteur": 1, "base": 1000}})
    if d["lignes_sans_facteur"] != 0:
        _rouge("%d ligne(s) comptee(s) « sans facteur » alors que les deux "
               "concernees sont EXCLUES du debalancement"
               % d["lignes_sans_facteur"])


# ══════════════════════════════════════════════════════════════════════════
# ANTI-VACUITE ET DERIVE DE LA FIXTURE
# ══════════════════════════════════════════════════════════════════════════

def test_le_banc_MORD_sur_une_vraie_divergence():
    """Sans ce cas, une comparaison cassee (ou une fixture sans « attendu »)
    laisserait tout passer au vert."""
    lignes = {1: {"total": 1000, "unite": "u", "description": "Beton"}}
    d = derive_version(lignes, {"1": {"facteur": 2, "base": 1000}})
    if abs(d["ecart_courant"] - 1000.0) > 0.005:
        _rouge("une ligne ORDINAIRE doublee devrait creuser l'ecart de 1000 $ ; "
               "obtenu %+.2f. Le calcul lui-meme est faux." % d["ecart_courant"])


def test_la_copie_locale_porte_l_EMPREINTE_CONVENUE():
    """L'EMPREINTE EST FIGEE DANS LES DEUX DEPOTS, et c'est PC4 qui l'a exige.

    Ma premiere version ne comparait les deux fixtures que si le depot frere
    etait present — donc jamais en CI, puisque je dis moi-meme qu'il y est
    absent. **La copie de tests/fixtures/ pouvait donc deriver sans que rien
    ne rougisse, et ce banc serait reste vert sur une fixture perimee.**
    C'est exactement la faute que je venais de corriger ailleurs : une garde
    ecrite mais jamais appelee.

    Avec une empreinte figee, modifier la fixture OBLIGE a changer la
    constante DES DEUX COTES. Celui qui ne met a jour qu'un depot voit la CI
    rougir — et c'est le seul signal qui traverse la frontiere entre deux
    depots qui ne se voient pas.
    """
    EMPREINTE = "0d883d7e566358d1bf58333bf10105e2"
    a = hashlib.md5(open(FIXTURE, "rb").read()).hexdigest()
    if a != EMPREINTE:
        _rouge(
            "la fixture de tests/fixtures/ a change (%s au lieu de %s).\n"
            "   Si c'est voulu : recopiez-la DANS LES DEUX DEPOTS et mettez a\n"
            "   jour la constante EMPREINTE ici ET dans le controle du\n"
            "   monorepo. Sinon la parite verifiee ici ne serait plus celle\n"
            "   que l'ecran applique." % (a[:8], EMPREINTE[:8]))


def test_la_copie_locale_est_IDENTIQUE_a_celle_du_monorepo():
    """Controle SUPPLEMENTAIRE, quand le depot frere est la (poste de travail) :
    il attrape la divergence AVANT le push, sans attendre la CI. Il ne
    remplace pas l'empreinte figee ci-dessus — il la double de maniere plus
    directe quand c'est possible."""
    if not os.path.isfile(FRERE):
        print("     (depot frere absent — l'empreinte figee prend le relais)")
        return
    a = hashlib.md5(open(FIXTURE, "rb").read()).hexdigest()
    b = hashlib.md5(open(FRERE, "rb").read()).hexdigest()
    if a != b:
        _rouge("la copie de tests/fixtures/ a DIVERGE de celle du monorepo "
               "(%s vs %s). La parite verifiee ici ne serait plus celle que "
               "l'ecran applique : recopiez la fixture." % (a[:8], b[:8]))


if __name__ == "__main__":
    import traceback
    cas = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    rouges = 0
    for f in cas:
        try:
            f()
            print("  OK  %s" % f.__name__)
        except AssertionError:
            rouges += 1
            print("  ROUGE  %s" % f.__name__)
            traceback.print_exc()
    print("%d cas, %d rouge(s)" % (len(cas), rouges))
    sys.exit(1 if rouges else 0)
