"""format_item NE DOIT PAS bouger l'empreinte du budget.

POURQUOI CE BANC EXISTE (demande de PC4, 2026-09-29) : le champ `format_item`
(le format d'achat d'un item -- la boite de 12) a ete ajoute a la ligne de
budget. Il n'entre NI dans `sous_total`, NI dans `total`, NI dans l'empreinte.

On pourrait s'en convaincre en LISANT `_condense`, qui ne retient que
(id, total en cents). C'est un RAISONNEMENT, pas une mesure -- et PC4 a eu
raison de demander qu'on le fige. Le jour ou quelqu'un elargira `_condense`
ou `_line_total`, ce banc tombera en ROUGE et lui dira ce qu'il casse, au
lieu de laisser une derive d'empreinte se manifester en production par une
fausse alerte sur un devis deja emis.

CE QUE CE BANC NE DIT PAS : il ne verifie pas que format_item est PERSISTE
(c'est le role des routes), ni qu'il vaut > 0 (c'est la garde 422 et le CHECK
en base). Il ne verrouille QUE la neutralite du champ sur l'empreinte.

Lancer :  py -3 tests/test_budget_fingerprint_format_item.py
     ou :  pytest tests/test_budget_fingerprint_format_item.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from modules import budget_fingerprint as FP  # noqa: E402


PROJET = {"arrondi_dollar": False, "pct_admin": 15, "pct_profit": 0}

# Des lignes VARIEES a dessein : materiaux purs, heures, sous-traitance,
# ajustements. Si format_item devait un jour contaminer un calcul, il le
# ferait probablement par l'une de ces voies plutot que sur une ligne triviale.
LIGNES = [
    {"id": 1, "qte": 320, "prix_unitaire": 12.5, "ajust_materiaux": 0},
    {"id": 2, "qte": 0, "prix_unitaire": 0, "heures": 24, "taux_horaire": 85.14,
     "ajust_main_oeuvre": 10},
    {"id": 3, "qte": 1, "prix_unitaire": 0, "sous_traitant_montant": 58365,
     "ajust_sous_traitant": 15},
    {"id": 4, "qte": 5000, "prix_unitaire": 1.25, "ajustement_pct": -5},
]

# Valeurs plausibles ET hostiles : un entier, un decimal a 4 chiffres (la
# precision exacte de NUMERIC(12,4)), un tres grand, un tres petit.
FORMATS = [12, 32.5, 0.0001, 999999.9999, 1]


def _empreinte(projet, lignes):
    # fingerprint_from_current_state plutot que compute_budget_fingerprint :
    # elle RECALCULE le montant par _prix_vente_total avant de hacher. C'est le
    # chemin reel du serveur a l'emission, et surtout le seul qui eprouve aussi
    # une contamination du MONTANT -- passer un montant fixe, comme je l'avais
    # d'abord ecrit, aurait masque exactement le defaut qu'on cherche a exclure.
    return FP.fingerprint_from_current_state(projet, lignes)


def principal():
    reference = _empreinte(PROJET, copy.deepcopy(LIGNES))
    print("empreinte de reference (sans format_item) : %s" % reference)

    echecs = []

    # 1. Poser un format sur CHAQUE ligne ne doit rien changer.
    avec = copy.deepcopy(LIGNES)
    for ligne, fmt in zip(avec, FORMATS):
        ligne["format_item"] = fmt
    obtenue = _empreinte(PROJET, avec)
    print("empreinte avec un format sur chaque ligne  : %s" % obtenue)
    if obtenue != reference:
        echecs.append("poser format_item sur toutes les lignes a change l'empreinte")

    # 2. Le poser sur UNE SEULE ligne, l'une apres l'autre.
    for i in range(len(LIGNES)):
        une = copy.deepcopy(LIGNES)
        une[i]["format_item"] = FORMATS[i]
        if _empreinte(PROJET, une) != reference:
            echecs.append("format_item sur la seule ligne id=%s a change l'empreinte"
                          % une[i]["id"])

    # 3. NULL explicite, et le champ absent, doivent etre indiscernables.
    nul = copy.deepcopy(LIGNES)
    for ligne in nul:
        ligne["format_item"] = None
    if _empreinte(PROJET, nul) != reference:
        echecs.append("format_item=None a change l'empreinte")

    # 4. Le MEME essai avec arrondi_dollar : l'arrondi est le chemin de code
    #    le plus susceptible d'absorber une valeur parasite.
    projet_arrondi = dict(PROJET, arrondi_dollar=True)
    ref_arrondi = _empreinte(projet_arrondi, copy.deepcopy(LIGNES))
    avec_arrondi = copy.deepcopy(LIGNES)
    for ligne, fmt in zip(avec_arrondi, FORMATS):
        ligne["format_item"] = fmt
    if _empreinte(projet_arrondi, avec_arrondi) != ref_arrondi:
        echecs.append("en mode arrondi_dollar, format_item a change l'empreinte")

    # 5. TEMOIN POSITIF -- sans lui, ce banc passerait meme si l'empreinte
    #    etait devenue une constante. Changer une vraie valeur DOIT la bouger.
    temoin = copy.deepcopy(LIGNES)
    temoin[0]["qte"] = 321
    if _empreinte(PROJET, temoin) == reference:
        echecs.append("TEMOIN : changer qte n'a PAS bouge l'empreinte -- "
                      "ce banc ne prouve plus rien, reparer avant de le croire")

    print()
    if echecs:
        for e in echecs:
            print("ECHEC : %s" % e)
        return 1
    print("OK -- format_item est neutre sur l'empreinte (5 cas, temoin positif compris)")
    return 0


def test_format_item_neutre_sur_empreinte():
    assert principal() == 0


if __name__ == "__main__":
    sys.exit(principal())
