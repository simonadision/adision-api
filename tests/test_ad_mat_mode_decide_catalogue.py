# -*- coding: utf-8 -*-
"""TEMOIN -- le MODE de la ligne decide du catalogue. JAMAIS LES DEUX.

REGLE DE SIMON, 7 oct. 2026 15 h 17 : « **Ad MAT par defaut partout ; le MODE
decide du catalogue ; JAMAIS les deux.** » Puis, le 8 oct. 10 h 11 :
« go ad mat ».

CE QUI ETAIT DEPLOYE ET LA CONTREDISAIT. `_remonter_au_catalogue` se
declenchait sur **une seule condition** : `source_typ_code` non vide. **Le mode
n'etait JAMAIS regarde.** Une ligne LIEE A UN ITEM AD MAT mais nee d'Ad TYP
(elle garde son code d'origine) voyait donc ses corrections d'unite et de
description partir dans **Ad TYP**, pour toute l'organisation, alors que son
catalogue est **Ad MAT**.

POURQUOI C'EST GRAVE ET NON POURQUOI C'EST LAID. Une ecriture qui part dans
les DEUX catalogues cree **deux verites pour un meme article**. C'est la
famille de defauts qu'on passe la semaine a retirer, et elle ne se signale
jamais : les deux catalogues restent coherents AVEC EUX-MEMES.

MESURE DU 8 OCT (base d'Ad BUD, 4 939 lignes) :
    les DEUX liens a la fois .....  27
    Ad TYP seul .................. 3 795
    Ad MAT seul ..................   241
    ni l'un ni l'autre ...........   876
Ce sont ces **27** que la garde protege. ⛔ Leur sort -- les laisser, les
basculer -- reste la **decision de Simon**, pas un effet de bord d'un
correctif (consigne de PC4, 7 oct.).

IL N'Y A PAS DE CHAMP « MODE », ET C'EST MESURE. `type_source` en avait l'air :
elle est **NULL sur les 4 939 lignes**, colonne morte. Le mode se LIT sur le
lien -- un item Ad MAT attache, c'est le mode Ad MAT.

ON TESTE LA FONCTION, PAS SA DESCRIPTION. `_remonter_au_catalogue` est
appelable directement : ses quatre premieres gardes ne touchent ni la base ni
le reseau. On s'arrete avant `corriger_typ`, qui, lui, sort du processus.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.ad_budget_api import _remonter_au_catalogue  # noqa: E402

ORG = "11111111-1111-1111-1111-111111111111"
PROJET = {"organization_id": ORG}
USER = {"organization_id": ORG, "active_organization_id": ORG}
JETON = "jeton-de-banc"


def _ligne(**extra):
    base = {"source_typ_code": "09 20 00.01", "unite": "gallon",
            "description": "Platrage", "item_id_ad_mat": None}
    base.update(extra)
    return base


# ══════════════════════════════════════════════════════════════════════════
# LA REGLE
# ══════════════════════════════════════════════════════════════════════════

def test_une_ligne_LIEE_A_AD_MAT_ne_remonte_PAS_dans_ad_typ():
    """LE CAS DU CORRECTIF. Mutant qui doit rougir : retirer la garde
    `item_id_ad_mat is not None`."""
    r = _remonter_au_catalogue(_ligne(item_id_ad_mat=4242), {"unite": "global"},
                               PROJET, USER, JETON)
    assert r is not None, "la ligne passe sans un mot : la correction part dans Ad TYP"
    assert r.get("statut") == "ignore"


def test_et_le_REFUS_SE_VOIT():
    """Un refus muet serait pire que le defaut : l'utilisateur croirait sa
    correction partie au catalogue. Le message doit NOMMER Ad MAT."""
    r = _remonter_au_catalogue(_ligne(item_id_ad_mat=4242), {"unite": "global"},
                               PROJET, USER, JETON)
    assert "Ad MAT" in (r.get("message") or "")


def test_une_ligne_AD_TYP_SEULE_remonte_TOUJOURS():
    """ANTI-SUR-CORRECTION, et c'est le cas des 3 795 lignes. La garde ne doit
    pas eteindre `adision-api#117`, qui repond a une vraie demande de Simon
    (« ca fait plusieurs reprises que je change l'unite de platrage »).
    On s'arrete juste avant l'appel reseau : la fonction va jusqu'a
    `corriger_typ`, donc elle ne rend NI None NI un refus de mode."""
    r = _remonter_au_catalogue(_ligne(), {"unite": "global"}, PROJET, USER, JETON)
    assert not (isinstance(r, dict) and "Ad MAT" in (r.get("message") or "")), (
        "une ligne Ad TYP pure est refusee : le correctif #117 est eteint")


# ══════════════════════════════════════════════════════════════════════════
# CE QUI NE DOIT PAS AVOIR BOUGE
# ══════════════════════════════════════════════════════════════════════════

def test_sans_code_ad_typ_rien_ne_remonte():
    assert _remonter_au_catalogue(_ligne(source_typ_code=None), {"unite": "global"},
                                  PROJET, USER, JETON) is None


def test_changer_d_item_ne_remonte_rien():
    """`source_typ_code` dans le patch = on change d'item, pas on corrige."""
    assert _remonter_au_catalogue(
        _ligne(), {"source_typ_code": "09 20 00.02", "unite": "global"},
        PROJET, USER, JETON) is None


def test_aucun_champ_remontable_touche():
    assert _remonter_au_catalogue(_ligne(), {"qte": 5}, PROJET, USER, JETON) is None


def test_le_cloisonnement_par_organisation_tient_TOUJOURS():
    """La couche custom d'Ad TYP ecrit dans l'org DU JETON : corriger SON
    catalogue pour le projet d'un autre serait faux."""
    autre = {"organization_id": "22222222-2222-2222-2222-222222222222",
             "active_organization_id": "22222222-2222-2222-2222-222222222222"}
    r = _remonter_au_catalogue(_ligne(), {"unite": "global"}, PROJET, autre, JETON)
    assert r and r.get("statut") == "ignore"
    assert "organisation" in (r.get("message") or "").lower()


def test_le_mode_est_regarde_AVANT_l_organisation():
    """Ordre voulu : une ligne Ad MAT dans un projet d'une autre organisation
    doit s'entendre dire la VRAIE raison -- son mode -- et pas une raison
    d'organisation qui l'enverrait chercher un probleme de droits."""
    autre = {"organization_id": "22222222-2222-2222-2222-222222222222",
             "active_organization_id": "22222222-2222-2222-2222-222222222222"}
    r = _remonter_au_catalogue(_ligne(item_id_ad_mat=4242), {"unite": "global"},
                               PROJET, autre, JETON)
    assert "Ad MAT" in (r.get("message") or "")


@pytest.mark.parametrize("lien", [0, "0", ""])
def test_un_lien_FAUX_MAIS_PRESENT_brid_quand_meme(lien):
    """`is not None` et pas une verite JavaScript : un id 0 reste un lien posé.
    Mieux vaut refuser de remonter que remonter dans le mauvais catalogue."""
    r = _remonter_au_catalogue(_ligne(item_id_ad_mat=lien), {"unite": "global"},
                               PROJET, USER, JETON)
    assert isinstance(r, dict) and "Ad MAT" in (r.get("message") or "")


# ══════════════════════════════════════════════════════════════════════════
# LA GARDE DOIT POUVOIR MORDRE -- ET ÇA NE SE VOIT PAS DANS LA FONCTION
# ══════════════════════════════════════════════════════════════════════════
# AJOUTE APRES QU'UN MUTANT A SURVECU. J'ai retire `item_id_ad_mat` de la
# requete SQL qui remplit `avant` : les onze cas sont restes VERTS, parce
# qu'ils passent le dict a la main. En production la garde n'aurait JAMAIS
# mordu -- `avant.get("item_id_ad_mat")` aurait toujours valu None, et chaque
# correction serait repartie dans Ad TYP, en silence.
#
# Un mutant qui SURVIT est une information, pas un ennui : c'est ce que PC2
# m'a rappele le 7 octobre en demontant son propre diagnostic.
#
# On inspecte l'AST et pas le texte : l'en-tete de ce banc CITE le nom de la
# colonne une dizaine de fois.
import ast  # noqa: E402
import io   # noqa: E402

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "modules", "ad_budget_api.py")


def _chaines_de(fn):
    return [n.value for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_la_requete_qui_remplit_avant_LIT_le_lien_ad_mat():
    """Mutant qui doit rougir : retirer `item_id_ad_mat` du SELECT."""
    arbre = ast.parse(io.open(SRC, encoding="utf-8").read())
    fns = [n for n in ast.walk(arbre)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
           and any("quantites_liees_a, description, unite, source_typ_code" in c
                   for c in _chaines_de(n))]
    assert fns, "la requete qui remplit `avant` est introuvable : le banc n'a plus de sujet"
    # ON REGARDE LA CHAINE DU SELECT, PAS TOUTE LA FONCTION. Premiere version
    # de ce cas : « la fonction contient-elle item_id_ad_mat ? » -- elle le
    # contient a dix endroits sans rapport, et le mutant a SURVECU une
    # deuxieme fois. Python fusionne les litteraux adjacents (`"a" "b"`) en UNE
    # constante : la ligne du SELECT est donc une seule chaine, et c'est elle
    # qu'on interroge.
    selects = [c for fn in fns for c in _chaines_de(fn)
               if "quantites_liees_a, description, unite, source_typ_code" in c]
    assert selects, "le SELECT est introuvable : le banc n'a plus de sujet"
    for sel in selects:
        assert "item_id_ad_mat" in sel, (
            "le SELECT qui remplit `avant` ne lit pas item_id_ad_mat : la garde "
            "de mode ne mordra JAMAIS en production, et aucun autre cas ne le dira")
