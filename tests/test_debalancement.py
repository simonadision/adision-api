# -*- coding: utf-8 -*-
"""TEMOIN — le debalancement ne peut pas faire bouger le grand total, et son
annulation ne peut pas ecraser une saisie faite depuis.

Simon, 5 oct 2026 13 h 51 : « option debalancement… une colonne cible a
atteindre. Ex. 10 000 en electricite, cible 12 500 = +2 500, **mais le grand
total reste le meme** ».

CE QUE CES CAS VERROUILLENT.

1. **LE SERVEUR VERIFIE LA PROMESSE, IL NE LA CROIT PAS.** L'ecran calcule les
   facteurs et envoie les NOUVELLES valeurs. Sans controle, une faute dans ce
   calcul ecrirait de l'argent faux avec la benediction du serveur, et
   « Balance 0 $ » ne serait verifie par personne d'autre que celui qui l'a
   calcule. On somme les totaux AVANT et APRES dans la MEME transaction, et on
   ROLLBACK au-dela du seuil.

2. **LA PHOTO PORTE DEUX VALEURS PAR LIGNE.** `avant` restaure ; `pose`
   detecte qu'on peut restaurer SANS ECRASER quelqu'un. Garder seulement
   `avant` rendrait l'annulation aveugle — elle ecraserait en silence une
   saisie faite entre-temps.

3. **ON N'ANNULE QUE LE DERNIER, ET UNE SEULE FOIS.** Sinon on restaure des
   ajustements perimes par-dessus du travail posterieur.

4. **LE VERROU PROJET S'APPLIQUE.** Ces routes ecrivent dans le budget : elles
   passent par `_load_and_authorize_projet(..., "write")`, le garde-fou central.

ON LIT LE SOURCE : exercer ces routes demanderait la base d'Ad BUD et un JWT.
Ce qu'on verifie est une propriete du CODE.

AUTONOME, SANS PYTEST.
"""
import os
import re
import sys

_RACINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = os.path.join(_RACINE, "modules", "ad_budget_api.py")
SQL = os.path.join(_RACINE, "migrations", "sprint_debalancements.sql")


def _source():
    with open(SRC, encoding="utf-8") as f:
        return f.read()


def _code_seul():
    """Sans les lignes de commentaire : l'en-tete de ce correctif CITE ce que
    les cas cherchent. Deux bancs m'ont deja menti ainsi le 5 oct."""
    return "\n".join(l for l in _source().splitlines()
                     if not l.strip().startswith("#"))


def _bloc(nom_fonction):
    """Le corps d'une route, borne a la PROCHAINE def de meme niveau — pas a
    un nombre de caracteres. Une fenetre fixe deborderait sur la route
    suivante et validerait le mauvais code."""
    code = _code_seul()
    d = code.index("def %s(" % nom_fonction)
    suite = re.search(r"\n    @router\.", code[d:])
    return code[d:d + suite.start()] if suite else code[d:]


def _rouge(msg):
    raise AssertionError(msg)


def test_le_filtre_de_commentaires_MORD():
    if len(_code_seul()) >= len(_source()):
        _rouge("le filtre ne retire rien : les cas lisent les commentaires")


def test_les_TROIS_routes_existent():
    code = _code_seul()
    for chemin in ('@router.post("/projets/{projet_id}/debalancements")',
                   '@router.get("/projets/{projet_id}/debalancements/dernier")',
                   '@router.post("/projets/{projet_id}/debalancements/{dbl_id}/annuler")'):
        if chemin not in code:
            _rouge("route absente : %s — l'ecran recevrait un 404" % chemin)


# ══════════════════════════════════════════════════════════════════════════
# 1. LE SERVEUR VERIFIE LE GRAND TOTAL
# ══════════════════════════════════════════════════════════════════════════

def test_le_total_est_mesure_AVANT_et_APRES():
    b = _bloc("creer_debalancement")
    if b.count("_somme_totaux(cur, projet_id)") < 2:
        _rouge("le grand total n'est pas mesure DEUX fois : sans un avant et "
               "un apres, le serveur CROIT l'ecran au lieu de le verifier. "
               "« Le grand total ne bouge pas » EST la fonction.")


def test_un_ecart_trop_grand_ANNULE_TOUT():
    b = _bloc("creer_debalancement")
    if "DEBALANCEMENT_SEUIL_CENTS" not in b:
        _rouge("aucun seuil : l'ecart n'est pas juge")
    i = b.find("DEBALANCEMENT_SEUIL_CENTS")
    if "rollback" not in b[i:i + 400]:
        _rouge("l'ecart depasse le seuil et RIEN n'est annule : de l'argent "
               "faux serait ecrit, et la balance afficherait 0 $ en mentant")


def test_les_montants_sont_en_DECIMAL_jamais_en_float():
    """Un ecart de virgule flottante ferait echouer une application pourtant
    juste — ou pire, en laisserait passer une fausse."""
    b = _bloc("creer_debalancement")
    if "Decimal(" not in b:
        _rouge("les montants ne passent pas par Decimal")
    if re.search(r"float\(\s*(apres_total|avant_total)", b):
        _rouge("un total est converti en float avant comparaison")


# ══════════════════════════════════════════════════════════════════════════
# 2. LA PHOTO PORTE « avant » ET « pose »
# ══════════════════════════════════════════════════════════════════════════

def test_la_photo_porte_les_DEUX_valeurs():
    b = _bloc("creer_debalancement")
    if '"avant"' not in b or '"pose"' not in b:
        _rouge("la photo ne porte pas « avant » ET « pose » : l'annulation "
               "serait AVEUGLE et ecraserait une saisie faite depuis")


def test_la_migration_declare_les_DEUX_valeurs():
    with open(SQL, encoding="utf-8") as f:
        sql = "\n".join(l for l in f.read().splitlines()
                        if not l.strip().startswith("--"))
    for attendu in ("ad_budget.debalancements", "lignes", "annule_le", "annulation"):
        if attendu not in sql:
            _rouge("la migration ne declare pas %s" % attendu)


# ══════════════════════════════════════════════════════════════════════════
# 3. L'ANNULATION : LE DERNIER, UNE SEULE FOIS, ET JAMAIS PAR-DESSUS
# ══════════════════════════════════════════════════════════════════════════

def test_on_n_annule_QUE_le_dernier():
    b = _bloc("annuler_debalancement")
    if "ORDER BY cree_le DESC LIMIT 1" not in b:
        _rouge("l'annulation ne verifie pas que c'est le DERNIER : restaurer "
               "un ancien ecraserait le travail fait depuis")
    if 'dernier["id"] != dbl["id"]' not in b:
        _rouge("le dernier est lu mais pas COMPARE a celui qu'on annule")


def test_une_annulation_ne_se_tente_QU_UNE_FOIS():
    b = _bloc("annuler_debalancement")
    if 'dbl["annule_le"] is not None' not in b:
        _rouge("un deuxieme essai est permis : il restaurerait « avant » "
               "par-dessus des lignes re-modifiees entre les deux")
    if "annule_le = NOW()" not in b:
        _rouge("la tentative n'est pas enregistree : l'annulation resterait "
               "rejouable indefiniment")


def test_une_ligne_TOUCHEE_DEPUIS_est_sautee_ET_NOMMEE():
    b = _bloc("annuler_debalancement")
    if "courant != pose" not in b:
        _rouge("l'annulation ecrit sans verifier que la ligne porte encore ce "
               "qu'on y avait pose : elle ecraserait une saisie en silence")
    if "sautees.append" not in b or "description" not in b:
        _rouge("les lignes sautees ne sont pas NOMMEES : une annulation "
               "silencieusement partielle est pire qu'un refus")


# ══════════════════════════════════════════════════════════════════════════
# 4. LE VERROU ET LE PERIMETRE
# ══════════════════════════════════════════════════════════════════════════

def test_les_deux_ECRITURES_passent_par_la_garde_en_mode_write():
    for nom in ("creer_debalancement", "annuler_debalancement"):
        b = _bloc(nom)
        if '_load_and_authorize_projet(get_conn, projet_id, user, "write")' not in b:
            _rouge("%s n'appelle pas la garde en mode write : le verrou projet "
                   "et le perimetre d'organisation seraient contournes" % nom)


def test_la_LECTURE_ne_demande_que_read():
    b = _bloc("dernier_debalancement")
    if '"read"' not in b:
        _rouge("la lecture exige le mode write : un projet verrouille ne "
               "pourrait plus meme CONSULTER son dernier debalancement")


def test_les_lignes_visees_sont_VERROUILLEES_le_temps_de_la_transaction():
    b = _bloc("creer_debalancement")
    if "FOR UPDATE" not in b:
        _rouge("les lignes ne sont pas verrouillees : deux debalancements "
               "simultanes s'entrelaceraient et le total ne balancerait plus")


def test_une_ligne_ABSENTE_refuse_TOUT():
    b = _bloc("creer_debalancement")
    if "manquantes" not in b:
        _rouge("une ligne visee qui n'existe plus passerait : un debalancement "
               "partiel ne balance pas")


# ══════════════════════════════════════════════════════════════════════════
# ANTI-VACUITE
# ══════════════════════════════════════════════════════════════════════════

def test_le_decoupage_par_bloc_MORD():
    """Si `_bloc` rendait tout le fichier, chaque cas serait vert pour des
    raisons qui n'ont rien a voir avec la route visee."""
    b = _bloc("dernier_debalancement")
    if len(b) >= len(_code_seul()) / 2:
        _rouge("le bloc fait %d caracteres : le decoupage ne borne plus rien"
               % len(b))
    if "annuler_debalancement" in b:
        _rouge("le bloc deborde sur la route suivante")


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
