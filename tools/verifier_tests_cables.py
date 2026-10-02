# -*- coding: utf-8 -*-
"""GARDE-FOU -- aucun fichier de test ne reste hors de la CI sans qu'on le sache.

1er oct 2026 : 27 fichiers sur 49 dans tests/ n'etaient cites NULLE PART dans
.github/workflows/ (hors commentaires). C'est le chiffre que CET outil
reproduit, mesure par PC3 sur 3bc7e70, EXCLUSIONS videes. Les « 22 sur 46 »
puis « 21 sur 47 » du recensement initial venaient d'un grep sur le NOM, qui
comptait comme cites les 6 fichiers « depot frere » nommes seulement dans un
COMMENTAIRE de ci.yml (21 + 6 = 27). Meme mot, deux instruments : c'est
l'outil qui fait foi. La CI les liste un par un,
sans motif de collecte : un banc ajoute et oublie ne tournait jamais, et l'on
se croyait couvert. Parmi eux :
  - test_dupliquer_idempotence.py, la garde des copies, muette depuis le
    11 sept -- c'est exactement le defaut qu'elle devait empecher qui a
    produit les deux « Oslo Traiteur (copie) » ;
  - test_quantite_effective.py, le banc du « % » livre le jour meme.

Un banc qu'on n'execute pas est pire qu'un banc absent : on se croit couvert.

REGLE : tout tests/test_*.py et tests/check_*.py est CITE dans un workflow
(ligne active, pas un commentaire), par son chemin ou par un motif qui le
couvre (tests/test_*.py, tests/), OU figure dans EXCLUSIONS ci-dessous avec
sa raison ecrite.

LA PORTE DE SORTIE EST NOMMEE, jamais implicite : sans elle, on contournerait
le garde-fou en renommant un fichier, et personne ne le saurait. Une exclusion
PERIMEE (fichier disparu, ou desormais cite) fait echouer aussi -- la liste
ne peut pas pourrir en silence.

Modele : adision-monorepo/scripts/verifier-tests-cables.mjs.
Usage : python tools/verifier_tests_cables.py [racine]   (exit 1 si un trou)
"""
import fnmatch
import glob
import os
import re
import sys

# Fichier -> raison. Chaque entree est une DECISION, pas un oubli.
EXCLUSIONS = {
    # ── Besoin du depot frere adision-monorepo + node : pre-push local
    #    seulement (decision deja actee en tete de ci.yml).
    "tests/test_devis_fidelity.py": "depot frere adision-monorepo requis -- pre-push local (cf. tete de ci.yml)",
    "tests/test_report_fidelity.py": "depot frere adision-monorepo requis -- pre-push local (cf. tete de ci.yml)",
    "tests/check_detention_seuil.py": "lit le seuil cote monorepo -- pre-push local (cf. tete de ci.yml)",
    "tests/test_budget_fingerprint_agreement.py": "accord empreinte Python<->JS, depot frere requis -- pre-push local",
    "tests/test_invariant_b_ligne_python_js.py": "Invariant B Python==JS, depot frere requis -- pre-push local",
    "tests/test_budget_fingerprint_production_valeur_discovery.py": "meme contrainte que l'Invariant B -- pre-push local",

    # ── BANCS PERIMES (mesure PC3, 1er oct 2026). Ce ne sont PAS des defauts
    #    de production ; ils sont ROUGES et ne doivent pas etre affaiblis pour
    #    passer. Chacun est une tache nommee a reprendre, puis a retirer d'ici.
    "tests/test_taux_horaires_api.py": "PERIME -- 32 echecs 403 « super_admin plateforme requis » : la route a gagne une garde que le banc ne fournit pas",
    "tests/test_auth_jwt.py": "PERIME -- 2 echecs AttributeError 'Cookie'.strip : changement d'API de bibliotheque",

}

MOTIFS_TESTS = ("tests/test_*.py", "tests/check_*.py")
# Un jeton qui designe des tests : un chemin sous tests/, eventuellement avec
# des jokers (tests/test_*.py) ou le dossier entier (tests/). Le mot NU
# « tests » ne compte PAS : c'est aussi le nom du job (« tests: ») -- le
# compter couvrait tout le banc et rendait le garde-fou aveugle (vu au premier
# essai). Pour collecter tout le dossier, ecrire « tests/ ».
_JETON = re.compile(r"(?<![\w./-])((?:\./)?tests/[\w.*?\[\]/-]*)(?![\w./-])")


def lignes_actives(texte):
    """Lignes d'un workflow SANS les commentaires YAML : un fichier cite en
    commentaire (« NE TOURNE PAS ICI ») n'est pas un fichier qui tourne."""
    for ligne in texte.splitlines():
        brut = ligne.strip()
        if not brut or brut.startswith("#"):
            continue
        # Commentaire en fin de ligne : « # » precede d'un blanc.
        yield re.split(r"\s#", ligne, maxsplit=1)[0]


def jetons_cites(textes_workflows):
    jetons = set()
    for texte in textes_workflows:
        for ligne in lignes_actives(texte):
            for m in _JETON.finditer(ligne):
                jetons.add(m.group(1)[2:] if m.group(1).startswith("./") else m.group(1))
    return jetons


def est_cite(fichier, jetons):
    for j in jetons:
        if j.endswith("/") and fichier.startswith(j):
            return True
        if fichier == j or fnmatch.fnmatchcase(fichier, j):
            return True
    return False


def verifier(racine):
    """Retourne (orphelins, exclusions_perimees), deux listes triees."""
    fichiers = sorted({
        os.path.relpath(p, racine).replace(os.sep, "/")
        for motif in MOTIFS_TESTS
        for p in glob.glob(os.path.join(racine, motif))
    })
    textes = []
    for p in sorted(glob.glob(os.path.join(racine, ".github", "workflows", "*.y*ml"))):
        with open(p, encoding="utf-8") as f:
            textes.append(f.read())
    jetons = jetons_cites(textes)

    orphelins = [f for f in fichiers if not est_cite(f, jetons) and f not in EXCLUSIONS]
    perimees = []
    for f, raison in sorted(EXCLUSIONS.items()):
        if f not in fichiers:
            perimees.append((f, "le fichier n'existe plus -- retirer l'exclusion"))
        elif est_cite(f, jetons):
            perimees.append((f, "le fichier est maintenant cite en CI -- retirer l'exclusion"))
    return orphelins, perimees


def main(argv):
    racine = argv[1] if len(argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    orphelins, perimees = verifier(racine)
    for f in orphelins:
        print(f"ORPHELIN  {f} -- cite nulle part dans .github/workflows/ et absent de EXCLUSIONS")
    for f, pourquoi in perimees:
        print(f"PERIMEE   {f} -- {pourquoi}")
    if orphelins or perimees:
        print(f"\nECHEC : {len(orphelins)} test(s) orphelin(s), {len(perimees)} exclusion(s) perimee(s).")
        print("Cablez le test dans .github/workflows/ci.yml, ou nommez-le dans EXCLUSIONS avec sa raison.")
        return 1
    print(f"OK -- aucun test orphelin ({len(EXCLUSIONS)} exclusion(s) nommee(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
