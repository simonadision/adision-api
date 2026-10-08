"""Les champs NEUTRES ne doivent JAMAIS bouger l'empreinte du budget.

POURQUOI CE BANC EXISTE : certains champs de la ligne de budget decrivent
l'item sans participer a son prix. Ils n'entrent NI dans `sous_total`, NI dans
`total`, NI dans l'empreinte. Il y en a deux depuis fin septembre, plus les
trois comptes par nature (`compte_mat`, `compte_mo`, `compte_st`) depuis le
8 oct. 2026 (migrations/sprint_comptes_par_nature.sql) :

  * `format_item` (demande de PC4, 2026-09-29) -- le format d'achat de l'item,
    la boite de 12 ;
  * `compte_metier_force` (Simon, 2026-09-30) -- le compte comptable force
    d'une ligne, pour le pont QuickBooks. UN NUMERO DE COMPTE N'EST PAS UN
    MONTANT, et c'est precisement le genre de valeur qu'on additionne par
    accident parce qu'elle ressemble a un nombre.

On pourrait s'en convaincre en LISANT `_condense`, qui ne retient que
(id, total en cents). C'est un RAISONNEMENT, pas une mesure -- et PC4 a eu
raison de demander qu'on le fige. Le jour ou quelqu'un elargira `_condense`
ou `_line_total`, ce banc tombera en ROUGE et lui dira ce qu'il casse, au
lieu de laisser une derive d'empreinte se manifester en production par une
fausse alerte sur un devis deja emis.

CE QUE CE BANC NE DIT PAS : il ne verifie pas que ces champs sont PERSISTES
(c'est le role des routes), ni qu'ils sont bien formes (c'est la garde 422 et
les CHECK en base). Il ne verrouille QUE leur neutralite sur l'empreinte.

POUR AJOUTER UN CHAMP NEUTRE : une entree dans CHAMPS_NEUTRES suffit.

Lancer :  py -3 tests/test_budget_fingerprint_champs_neutres.py
     ou :  pytest tests/test_budget_fingerprint_champs_neutres.py
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
# ajustements. Si un champ neutre devait un jour contaminer un calcul, il le
# ferait probablement par l'une de ces voies plutot que sur une ligne triviale.
LIGNES = [
    {"id": 1, "qte": 320, "prix_unitaire": 12.5, "ajust_materiaux": 0},
    {"id": 2, "qte": 0, "prix_unitaire": 0, "heures": 24, "taux_horaire": 85.14,
     "ajust_main_oeuvre": 10},
    {"id": 3, "qte": 1, "prix_unitaire": 0, "sous_traitant_montant": 58365,
     "ajust_sous_traitant": 15},
    {"id": 4, "qte": 5000, "prix_unitaire": 1.25, "ajustement_pct": -5},
]

# Pour chaque champ : des valeurs plausibles ET hostiles, une par ligne.
CHAMPS_NEUTRES = {
    # Un entier, un decimal a 4 chiffres (la precision exacte de
    # NUMERIC(12,4)), un tres petit, un tres grand.
    "format_item": [12, 32.5, 0.0001, 999999.9999],
    # Des numeros de compte REELS de la charte de Contracta, plus deux pieges :
    # un zero de tete, que personne ne doit « normaliser » en entier, et une
    # valeur qui ressemble a un MONTANT. Si l'empreinte bougeait ici, c'est
    # qu'un numero de compte serait entre dans un calcul.
    "compte_metier_force": ["56035", "04010", "58065", "1826132.90"],
    # Les trois comptes PAR NATURE (Simon, 8 oct. 2026 : « une colonne charte
    # Q pour chacune des sections matériaux, main d'oeuvre, sous-traitant »).
    # Memes pieges : zero de tete, et une valeur qui ressemble a un montant.
    # Enregistres sur la ligne, ils restent HORS EMPREINTE comme leur aine.
    "compte_mat": ["55010", "04010", "56100", "2031.36"],
    "compte_mo": ["50005", "05000", "51001", "338.56"],
    "compte_st": ["55005", "09998", "59998", "58365.00"],
}


def _empreinte(projet, lignes):
    # fingerprint_from_current_state plutot que compute_budget_fingerprint :
    # elle RECALCULE le montant par _prix_vente_total avant de hacher. C'est le
    # chemin reel du serveur a l'emission, et surtout le seul qui eprouve aussi
    # une contamination du MONTANT -- passer un montant fixe, comme je l'avais
    # d'abord ecrit, aurait masque exactement le defaut qu'on cherche a exclure.
    return FP.fingerprint_from_current_state(projet, lignes)


def _eprouver_champ(champ, valeurs, reference, echecs):
    """Les memes epreuves pour n'importe quel champ neutre."""
    # 1. Poser une valeur sur CHAQUE ligne ne doit rien changer.
    avec = copy.deepcopy(LIGNES)
    for ligne, v in zip(avec, valeurs):
        ligne[champ] = v
    obtenue = _empreinte(PROJET, avec)
    print("  %-22s toutes lignes : %s" % (champ, obtenue))
    if obtenue != reference:
        echecs.append("%s pose sur toutes les lignes a change l'empreinte" % champ)

    # 2. Le poser sur UNE SEULE ligne, l'une apres l'autre.
    for i in range(len(LIGNES)):
        une = copy.deepcopy(LIGNES)
        une[i][champ] = valeurs[i]
        if _empreinte(PROJET, une) != reference:
            echecs.append("%s sur la seule ligne id=%s a change l'empreinte"
                          % (champ, une[i]["id"]))

    # 3. NULL explicite, et le champ absent, doivent etre indiscernables.
    nul = copy.deepcopy(LIGNES)
    for ligne in nul:
        ligne[champ] = None
    if _empreinte(PROJET, nul) != reference:
        echecs.append("%s=None a change l'empreinte" % champ)

    # 4. Le MEME essai avec arrondi_dollar : l'arrondi est le chemin de code
    #    le plus susceptible d'absorber une valeur parasite.
    projet_arrondi = dict(PROJET, arrondi_dollar=True)
    ref_arrondi = _empreinte(projet_arrondi, copy.deepcopy(LIGNES))
    avec_arrondi = copy.deepcopy(LIGNES)
    for ligne, v in zip(avec_arrondi, valeurs):
        ligne[champ] = v
    if _empreinte(projet_arrondi, avec_arrondi) != ref_arrondi:
        echecs.append("en mode arrondi_dollar, %s a change l'empreinte" % champ)


def principal():
    reference = _empreinte(PROJET, copy.deepcopy(LIGNES))
    print("empreinte de reference (aucun champ neutre) : %s\n" % reference)

    echecs = []
    for champ, valeurs in CHAMPS_NEUTRES.items():
        assert len(valeurs) == len(LIGNES), (
            "CHAMPS_NEUTRES[%r] doit porter une valeur par ligne" % champ)
        _eprouver_champ(champ, valeurs, reference, echecs)

    # 5. TOUS LES CHAMPS NEUTRES A LA FOIS. Un champ peut etre neutre seul et
    #    cesser de l'etre en presence d'un autre -- par exemple si quelqu'un
    #    les concatene un jour dans une cle.
    ensemble = copy.deepcopy(LIGNES)
    for champ, valeurs in CHAMPS_NEUTRES.items():
        for ligne, v in zip(ensemble, valeurs):
            ligne[champ] = v
    if _empreinte(PROJET, ensemble) != reference:
        echecs.append("les champs neutres poses ENSEMBLE ont change l'empreinte")

    # 6. TEMOIN POSITIF -- sans lui, ce banc passerait meme si l'empreinte
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
    print("OK -- %d champs neutres verrouilles, ensemble et temoin positif compris"
          % len(CHAMPS_NEUTRES))
    return 0


def test_champs_neutres_sur_empreinte():
    assert principal() == 0


if __name__ == "__main__":
    sys.exit(principal())
