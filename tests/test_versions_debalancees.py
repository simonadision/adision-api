# -*- coding: utf-8 -*-
"""TEMOIN — une version debalancee ne touche JAMAIS l'original, et le serveur
verifie la promesse au lieu de croire l'ecran.

Simon, 5 oct 2026 : « les versions debalancees existent en VERSION… et doivent
etre accessibles dans AD CON EN TEMPS REEL », et « **rien ne peut toucher a
l'original** ».

CE QUE CES CAS VERROUILLENT.

1. **AUCUNE ECRITURE DANS `budget_lignes`.** C'est la demande, mot pour mot.
   Une version porte des FACTEURS ; ses montants se recalculent a la lecture.

2. **LE SERVEUR VERIFIE LE GRAND TOTAL.** L'ecran calcule et envoie ; sans
   controle, une faute dans son calcul enregistrerait de l'argent faux avec la
   benediction du serveur. « Le grand total ne bouge pas » EST la fonction.

3. **LE SEUIL VAUT A L'ECRITURE, JAMAIS A LA LECTURE.** Une version DERIVE des
   qu'une ligne debalancee change de montant — c'est le regime NORMAL, pas
   l'exception. Appliquer le seuil a la lecture ferait cesser Ad CON
   d'afficher un budget parce qu'il a derive de 25 $.

4. **L'EXCLUSION EST CALCULEE PAR LE SERVEUR.** PC4 m'a retourne mon propre
   principe — « on ne fait pas confiance, on compare ». Un apercu perime
   marquerait « non exclue » une ligne passee en « % » entre-temps, et on
   l'etirerait : la boucle que l'exclusion existe pour empecher (meme famille
   que les 346 680 $ du 1er octobre).

5. **LA TOLERANCE SUR `total_base`.** L'ecran arrondit AU CENT ; comparer au
   centieme exact rejetterait des apercus parfaitement a jour.

AUTONOME, SANS PYTEST.
"""
import os
import re
import sys

_RACINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _RACINE)

SRC = os.path.join(_RACINE, "modules", "ad_budget_api.py")
SQL = os.path.join(_RACINE, "migrations", "sprint_versions_debalancees.sql")

from modules.ad_budget_api import (  # noqa: E402
    _ligne_hors_debalancement, _regle_au_mille,
)


def _source():
    with open(SRC, encoding="utf-8") as f:
        return f.read()


def _code_seul():
    """Sans les commentaires : l'en-tete de ce chantier CITE ce que les cas
    cherchent. Deux bancs m'ont menti ainsi le 5 oct."""
    return "\n".join(l for l in _source().splitlines()
                     if not l.strip().startswith("#"))


def _bloc(nom):
    code = _code_seul()
    d = code.index("def %s(" % nom)
    suite = re.search(r"\n    @router\.", code[d:])
    return code[d:d + suite.start()] if suite else code[d:]


def _rouge(msg):
    raise AssertionError(msg)


def test_le_filtre_de_commentaires_MORD():
    if len(_code_seul()) >= len(_source()):
        _rouge("le filtre ne retire rien")


# ══════════════════════════════════════════════════════════════════════════
# L'EXCLUSION — miroir de ligneHorsDebalancement (@adision/aggregates)
# ══════════════════════════════════════════════════════════════════════════

def test_exclusion_unite_pourcentage():
    for u in ("%", " % ", "%"):
        if not _ligne_hors_debalancement({"unite": u, "description": "x"}):
            _rouge("l'unite « %s » n'est pas reconnue comme « %% »" % u)


def test_exclusion_cautionnement_et_assurance():
    for d in ("Cautionnement et assurances", "CAUTIONNEMENTS",
              "Assurance chantier", "assurances"):
        if not _ligne_hors_debalancement({"unite": "m2", "description": d}):
            _rouge("« %s » n'est pas reconnue comme ligne au mille" % d)


def test_exclusion_ne_mord_PAS_sur_un_mot_PARTIEL():
    """LE PIEGE. `\\b` dans le JS : « Rassurance » ne doit PAS passer pour une
    assurance. Sans la frontiere de mot, on exclurait des lignes ordinaires du
    debalancement — et la compensation porterait sur une assiette trop
    petite, donc des facteurs trop gros."""
    for d in ("Rassurance du client", "Precautionnement", "assurancement"):
        if _ligne_hors_debalancement({"unite": "m2", "description": d}):
            _rouge("« %s » est exclue a tort : la frontiere de mot a saute" % d)


def test_exclusion_ignore_les_ACCENTS_et_la_CASSE():
    if _regle_au_mille("CAUTIONNEMENT") != "avecTaxes":
        _rouge("la casse n'est pas normalisee")
    if _regle_au_mille("Assurances") != "avantTaxes":
        _rouge("l'assurance n'est pas reconnue")


def test_une_ligne_ORDINAIRE_n_est_PAS_exclue():
    """Temoin NEGATIF : sans lui, une fonction qui rend toujours True
    passerait tous les cas ci-dessus."""
    for d in ("Béton coulé en place", "Main-d'oeuvre", None):
        if _ligne_hors_debalancement({"unite": "m2", "description": d}):
            _rouge("« %s » est exclue alors qu'elle est ordinaire" % d)


# ══════════════════════════════════════════════════════════════════════════
# LE SERVEUR NE TOUCHE JAMAIS L'ORIGINAL
# ══════════════════════════════════════════════════════════════════════════

def test_AUCUNE_route_de_version_n_ecrit_dans_budget_lignes():
    """LA DEMANDE DE SIMON, MOT POUR MOT : « rien ne peut toucher a
    l'original »."""
    for nom in ("creer_version", "maj_version", "supprimer_version",
                "_ecrire_version", "lister_versions", "lire_version"):
        b = _bloc(nom)
        for interdit in ("UPDATE ad_budget.budget_lignes",
                         "INSERT INTO ad_budget.budget_lignes",
                         "DELETE FROM ad_budget.budget_lignes"):
            if interdit in b:
                _rouge("%s ecrit dans budget_lignes (« %s ») — l'original "
                       "DOIT rester intact" % (nom, interdit))


def test_le_grand_total_est_VERIFIE_avant_d_enregistrer():
    b = _bloc("_ecrire_version")
    if "VERSION_SEUIL_CENTS" not in b:
        _rouge("aucun seuil : le serveur CROIT l'ecran au lieu de le verifier")
    if "avant" not in b or "apres" not in b:
        _rouge("le total n'est pas mesure des DEUX cotes")


def test_le_seuil_n_est_PAS_applique_a_la_LECTURE():
    """Une version DERIVE des qu'une ligne change : c'est le regime normal.
    Refuser de la LIRE ferait cesser Ad CON d'afficher un budget."""
    for nom in ("lister_versions", "lire_version"):
        if "VERSION_SEUIL_CENTS" in _bloc(nom):
            _rouge("%s applique le seuil a la lecture : un budget derive de "
                   "25 $ cesserait de s'afficher" % nom)


def test_l_exclusion_du_CLIENT_n_est_qu_un_CONTROLE():
    b = _bloc("_ecrire_version")
    if "_ligne_hors_debalancement(lignes[lid])" not in b:
        _rouge("le serveur ne recalcule pas l'exclusion : il croirait un "
               "apercu perime et etirerait une ligne « %% »")
    if "exclusion_perimee" not in b:
        _rouge("le desaccord d'exclusion ne produit pas de refus nomme")


def test_la_base_du_CLIENT_tolere_le_demi_cent():
    b = _bloc("_ecrire_version")
    if "VERSION_TOLERANCE_BASE" not in b:
        _rouge("la base est comparee au centieme exact : l'ecran arrondit au "
               "cent, donc TOUT apercu a jour serait refuse")


def test_le_total_d_une_ligne_vient_de_la_SOURCE_UNIQUE():
    """⚠ LA COLONNE `total` NE PORTE QUE LES MATERIAUX.

    Mesure du 6 oct. 2026 sur « Renovation interieure d'unites de logements »
    (1 209 lignes actives), trouvee par PC4 :

        Somme de la colonne `total` ...... 643 902,95 $
        materiaux bruts .................. 643 902,95 $   <- IDENTIQUE
        main-d'oeuvre .................... 701 941,67 $   <- ABSENTE
        sous-traitants ................. 1 438 171,00 $   <- ABSENTE
        887 lignes sur 1 209 valent 0

    Le vrai total est **2 785 515 $**, soit 4,3 fois plus.

    Tant que la route lisait cette colonne, AUCUNE version ne pouvait etre
    creee : le client envoie le vrai total de ligne, le serveur comparait a la
    part materiaux, et repondait 409 « ont change depuis votre apercu » sur
    presque toutes les lignes. Le seuil du grand total etait verifie sur le
    quart du montant, et Ad CON aurait affiche une fausse derive.

    **Une colonne dont le NOM promet plus que son CONTENU est un piege qui ne
    leve jamais.** On passe donc par `budget_fingerprint._line_total`, la
    fonction que garde verifier_source_unique.py et qu'appliquent l'ecran
    (getRow) et compute_budget_totals."""
    b = _bloc("_lignes_pour_version")
    # ⚠ ON CHERCHE L'AFFECTATION, PAS LE NOM.
    # Ma premiere version cherchait « _line_total » n'importe ou dans le bloc.
    # Un mutant qui RETIRAIT l'appel et laissait l'import la passait au VERT.
    # C'est la troisieme fois le meme matin que je mesure un NOM au lieu d'un
    # COMPORTEMENT : un nom present dans un fichier ne dit pas ce que le code
    # FAIT.
    if '["total"] = _line_total(' not in b:
        _rouge("le total de ligne n'est pas AFFECTE depuis _line_total : il "
               "resterait la part MATERIAUX seule (643 903 $ au lieu de "
               "2 785 515 $ sur le projet 290), et aucune version ne pourrait "
               "etre creee")
    if "SELECT id, total," in b:
        _rouge("la colonne `total` est relue comme total de ligne : elle ne "
               "porte que les materiaux (643 903 $ au lieu de 2 785 515 $ sur "
               "le projet 290)")
    # _line_total a besoin de TOUS ces champs : en oublier un rendrait un
    # total trop petit, SANS erreur.
    for champ in ("qte", "prix_unitaire", "heures", "heures_manuelles",
                  "production_valeur", "taux_horaire", "sous_traitant_montant",
                  "ajust_materiaux", "ajust_main_oeuvre", "ajust_sous_traitant",
                  "ajustement_pct", "qte_facteur"):
        if champ not in b:
            _rouge("« %s » n'est pas selectionne : _line_total le lirait a "
                   "None et rendrait un total trop petit, en silence" % champ)
    if "arrondi_dollar" not in b:
        _rouge("l'option arrondi_dollar du projet n'est pas lue : le total "
               "differerait de celui de l'ecran au cent pres")


def test_les_lignes_sont_VERROUILLEES_pendant_l_ecriture():
    if "FOR UPDATE" not in _bloc("_lignes_pour_version"):
        _rouge("pas de FOR UPDATE : deux enregistrements simultanes liraient "
               "des bases differentes")


def test_la_suppression_est_DOUCE_et_le_GET_sert_les_supprimees():
    b = _bloc("supprimer_version")
    if "supprimee_le = NOW()" not in b:
        _rouge("suppression dure : un rapport emis pointant cette version "
               "ferait mentir une reimpression")
    if "supprimee_le IS NULL" in _bloc("lire_version"):
        _rouge("le GET refuse une version supprimee : une reimpression "
               "sortirait autre chose que ce qui est parti chez le client")


def test_la_migration_declare_la_table_et_le_nom_unique():
    with open(SQL, encoding="utf-8") as f:
        sql = "\n".join(l for l in f.read().splitlines()
                        if not l.strip().startswith("--"))
    for attendu in ("ad_budget.versions_debalancees", "facteurs",
                    "supprimee_le", "uniq_version_nom_par_projet"):
        if attendu not in sql:
            _rouge("la migration ne declare pas %s" % attendu)
    if "ad_budget.projets" not in sql:
        _rouge("la table ne reference pas les projets")


# ══════════════════════════════════════════════════════════════════════════
# LE CARACTERE NUL, ET LA VERSION IMPRIMEE
# (deux points trouves par PC4 le 6 oct. 2026 en branchant son client)
# ══════════════════════════════════════════════════════════════════════════

def test_le_caractere_nul_est_REFUSE_en_422_et_la_garde_est_BRANCHEE():
    """PostgreSQL ne stocke pas \\u0000, ni en TEXT ni en JSONB : il repond
    500, qui ne nomme rien. PC4 y a perdu ses saisies le matin meme.

    ⚠ LE POINT QUI COMPTE EST LE SECOND : une garde DEFINIE mais JAMAIS
    APPELEE ne garde rien, et se relit comme une protection. Les deux routes
    d'ecriture doivent l'invoquer."""
    code = _code_seul()
    if "def _refuser_caractere_nul" not in code:
        _rouge("aucune garde sur le caractere nul : un client qui en envoie un "
               "recoit un 500 muet et perd sa saisie")
    for nom in ("creer_version", "maj_version"):
        if "_refuser_caractere_nul(data)" not in _bloc(nom):
            _rouge("%s n'APPELLE pas _refuser_caractere_nul : la garde existe "
                   "mais ne protege pas cette route" % nom)
    if "status_code=422" not in _bloc("_refuser_caractere_nul"):
        _rouge("la garde ne rend pas 422 : un refus doit nommer sa cause")


def test_emit_DECLARE_version_id_et_version_nom():
    """FastAPI IGNORE un parametre non declare : le client enverrait, le
    serveur repondrait 200, et la donnee disparaitrait EN SILENCE. Un 200 qui
    perd ce qu'on lui confie est pire qu'un refus."""
    code = _code_seul()
    d = code.index("def emit_recap_to_hub(")
    signature = code[d:code.index("):", d)]
    for p in ("version_id", "version_nom"):
        if p not in signature:
            _rouge("emit_recap_to_hub ne declare pas « %s » : FastAPI l'ignore, "
                   "le client croit l'avoir transmis, et une reimpression ne "
                   "saura jamais quelle version est partie chez le client" % p)
    corps = code[d:d + 6000]
    if "snapshot_data" not in corps or "version_id" not in corps.split("snapshot_data")[1][:400]:
        _rouge("la version declaree n'est pas ENREGISTREE avec le rapport : "
               "la declarer sans la ranger ne change rien")


# ══════════════════════════════════════════════════════════════════════════
# AD CON SUIT UNE VERSION (decision de Simon, 6 oct. 2026)
# « Le chantier SUIT une version » : contrat convenu avec PC4.
# ══════════════════════════════════════════════════════════════════════════

def test_export_for_con_accepte_version_id():
    b = _bloc("export_projet_for_con")
    if "version_id" not in b[:900]:
        _rouge("export-for-con ne declare pas version_id : FastAPI l'ignorerait "
               "et Ad CON afficherait l'original en croyant suivre une version")


def test_une_version_INCONNUE_donne_404_jamais_l_original():
    """LA REGLE QUI COMPTE. Servir l'original a la place d'une version
    demandee, sans rien dire, c'est le defaut le plus cher de la semaine :
    le chantier croit suivre V.1 et travaille sur autre chose."""
    b = _bloc("export_projet_for_con")
    if "status_code=404" not in b:
        _rouge("une version inconnue ne donne pas 404 : l'original serait "
               "servi en silence")
    if "AND projet_id = %s" not in b:
        _rouge("la version n'est pas bornee au projet : la version d'un AUTRE "
               "projet serait appliquee")


def test_la_version_SUPPRIMEE_reste_lisible():
    """Un chantier qui suit une version ne doit pas perdre son budget parce
    que quelqu'un a range. Coherent avec la suppression douce."""
    b = _bloc("export_projet_for_con")
    d = b.index("FROM ad_budget.versions_debalancees")
    if "supprimee_le IS NULL" in b[d:d + 300]:
        _rouge("export-for-con refuse une version supprimee : le chantier "
               "perdrait son budget du jour au lendemain")


def test_le_facteur_ne_touche_QUE_les_sous_totaux():
    """Un debalancement deplace des MONTANTS. Il ne change ni un prix
    unitaire, ni un taux horaire, ni un nombre d'heures — multiplier le taux
    ferait mentir la feuille de production sur ce que coute une heure."""
    b = _bloc("export_projet_for_con")
    for interdit in ("prix_u *=", "taux *=", "heures *=", "qty_eff *="):
        if interdit in b:
            _rouge("le facteur de version s'applique a une valeur UNITAIRE "
                   "(%s) : seuls les sous-totaux doivent bouger" % interdit)
    for attendu in ("mat_subtotal *=", "mo_subtotal *=", "st_subtotal *="):
        if attendu not in b:
            _rouge("le facteur ne s'applique pas a %s" % attendu)


def test_l_exclusion_est_RECALCULEE_dans_l_export():
    b = _bloc("export_projet_for_con")
    if "_ligne_hors_debalancement" not in b:
        _rouge("l'export lit l'exclusion de la version au lieu de la "
               "recalculer : une ligne « %% » serait etiree, et c'est la "
               "boucle des 346 680 $ du 1er octobre")


def test_la_cle_version_est_TOUJOURS_servie():
    """Ne la servir que lorsqu'il y a derive empecherait Ad CON de distinguer
    « pas de derive » de « j'ai oublie de demander la version »."""
    b = _bloc("export_projet_for_con")
    if '"version": (None if version_info is None' not in b:
        _rouge("la cle « version » n'est pas toujours presente : une absence "
               "voudrait dire deux choses a la fois")


# ══════════════════════════════════════════════════════════════════════════
# ANTI-VACUITE
# ══════════════════════════════════════════════════════════════════════════

def test_le_decoupage_par_bloc_MORD():
    b = _bloc("supprimer_version")
    if len(b) >= len(_code_seul()) / 2:
        _rouge("le decoupage ne borne plus rien (%d caracteres)" % len(b))
    if "creer_version" in b:
        _rouge("le bloc deborde sur une autre route")


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
