#!/usr/bin/env python3
"""COUCHE 3 — SOURCE UNIQUE OUTILLÉE (anti-duplication des calculs), côté Python.

Port du même contrôle côté monorepo (adision-monorepo/scripts/verifier-source-unique.mjs).
Contexte (issue #9 monorepo, 20 août 2026) : TROIS incidents distincts, un seul
défaut. La règle des heures effectives (priorité ABSOLUE à `production_valeur`
sur `heures`/`heures_manuelles`, cf. a07c72b — modules/aggregates.py::heures_effectives)
a été copiée-collée en local dans plusieurs fichiers JS au lieu de déléguer à
une source unique (budgetFingerprint.js, adaptBudgetLines.js — PR #12 ;
computeReportModel.js — issue #11). Côté Python, modules/aggregates.py est LA
source depuis toujours (a07c72b) et tous les appelants connus délèguent déjà
(ad_budget_api.py, budget_fingerprint.py) — mais rien n'empêchait structurellement
qu'un futur module en porte une copie locale. Ce contrôle ferme ce trou.

CE QUE CE SCRIPT CHERCHE (mêmes 2 signaux que la version JS, cf. son en-tête
pour le détail du raisonnement) :
  A. Le littéral _HEURE_UNITS copié-collé — `{"hr", "hre", "heure", "heures", "h"}`
     (ou une liste équivalente), verbatim depuis modules/aggregates.py.
  B. Le triptyque `heures_manuelles` + `qte` + `production_valeur` réunis dans
     une même fenêtre de code AVEC de l'arithmétique (round(, math.floor(, ou
     une division entre deux identifiants).

CE QUI EXEMPTE UN FICHIER : modules/aggregates.py lui-même (LA source, liste
blanche CANONICAL_FILES), ou tout fichier qui appelle/importe `heures_effectives`
(la fonction canonique) — dans ce cas toute mention locale de `production_valeur`/
`heures_manuelles` n'est qu'un ARGUMENT passé à la source unique.

PÉRIMÈTRE : modules/*.py + api.py (le code RÉELLEMENT servi). scripts/ (diagnostics
ponctuels, souvent des recalculs volontaires à des fins de comparaison — cf.
scripts/diag_ecart_coutant_habva.py) et tests/ sont hors périmètre, même
décision que côté monorepo (apps/*/src + packages/*/src, pas scripts/ ni tests/).

Usage :
    python tools/verifier_source_unique.py              # scan complet (prod)
    python tools/verifier_source_unique.py <dossier...>  # scan ciblé (tests)
"""
import os
import re
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ─── Liste blanche — LA source canonique, pas une copie ─────────────────────
CANONICAL_FILES = {"modules/aggregates.py"}

# ─── Signal de délégation — exempte tout appelant légitime ──────────────────
# \b des deux côtés : sans ça, un nom de fonction comme `heures_effectives_copie_locale`
# (un `_` n'est PAS une frontière de mot pour \b) compterait à tort comme un
# appel à la fonction canonique — trouvé par le filet (test_verifier_source_unique.py)
# avant même la première livraison, cf. son commentaire d'en-tête.
RE_DELEGATION = re.compile(r"\bheures_effectives\b")


def delegue(contenu: str) -> bool:
    return bool(RE_DELEGATION.search(contenu))


def est_canonique(chemin_relatif_posix: str) -> bool:
    return chemin_relatif_posix in CANONICAL_FILES


# ─── Signal A — littéral _HEURE_UNITS copié-collé ────────────────────────────
JETONS_UNITE_HEURE = ["hr", "hre", "heure", "heures", "h"]
FENETRE_UNITES = 220  # caractères
RE_JETON_UNITE = re.compile(r"""['"](hr|hre|heure|heures|h)['"]""")


def trouve_litteral_unites_heures(contenu: str) -> int:
    occurrences = [(m.group(1), m.start()) for m in RE_JETON_UNITE.finditer(contenu)]
    for i in range(len(occurrences)):
        debut = occurrences[i][1]
        presents = set()
        j = i
        while j < len(occurrences) and occurrences[j][1] <= debut + FENETRE_UNITES:
            presents.add(occurrences[j][0])
            j += 1
        if all(jeton in presents for jeton in JETONS_UNITE_HEURE):
            return debut
    return -1


# ─── Signal B — triptyque heures_manuelles + qte + production_valeur ────────
# + arithmétique (round(/math.floor(, ou division entre deux identifiants)
# dans une même fenêtre de code.
FENETRE_TRIADE = 400  # caractères de part et d'autre de heures_manuelles
RE_MANUELLES = re.compile(r"heures_manuelles")
RE_QTE = re.compile(r"\bqte\b")
RE_PRODUCTION = re.compile(r"production_valeur")
RE_ARITHMETIQUE = re.compile(r"round\(|math\.floor\(|[a-zA-Z_]\w*\s*/\s*[a-zA-Z_]")


def trouve_triade_champs_avec_arithmetique(contenu: str) -> int:
    for m in RE_MANUELLES.finditer(contenu):
        debut = max(0, m.start() - FENETRE_TRIADE // 2)
        fin = min(len(contenu), m.start() + FENETRE_TRIADE // 2)
        fenetre = contenu[debut:fin]
        if RE_QTE.search(fenetre) and RE_PRODUCTION.search(fenetre) and RE_ARITHMETIQUE.search(fenetre):
            return m.start()
    return -1


def _numero_ligne(contenu: str, index: int) -> int:
    return contenu.count("\n", 0, index) + 1


def analyser_fichier(chemin_relatif_posix: str, contenu: str):
    """Retourne None si rien de suspect, sinon (ligne, motif)."""
    if est_canonique(chemin_relatif_posix):
        return None
    if delegue(contenu):
        return None

    idx_a = trouve_litteral_unites_heures(contenu)
    if idx_a != -1:
        return (_numero_ligne(contenu, idx_a), "littéral _HEURE_UNITS (\"hr\"/\"hre\"/\"heure\"/\"heures\"/\"h\") copié-collé")

    idx_b = trouve_triade_champs_avec_arithmetique(contenu)
    if idx_b != -1:
        return (_numero_ligne(contenu, idx_b), "heures_manuelles + qte + production_valeur réunis avec de l'arithmétique")

    return None


# ─── Marche sur le système de fichiers ───────────────────────────────────────
EXCLURE_DOSSIERS = {"__pycache__", ".git", "venv", ".venv", "node_modules"}


def _lister_fichiers(dossier):
    fichiers = []
    for racine, dirs, noms in os.walk(dossier):
        dirs[:] = [d for d in dirs if d not in EXCLURE_DOSSIERS and not d.startswith(".")]
        for nom in noms:
            if nom.endswith(".py"):
                fichiers.append(os.path.join(racine, nom))
    return fichiers


def _dossiers_par_defaut():
    dossiers = [os.path.join(RACINE, "modules")]
    api_py = os.path.join(RACINE, "api.py")
    return dossiers, [api_py] if os.path.isfile(api_py) else []


def scanner(dossiers, fichiers_supplementaires=None):
    fichiers = []
    for d in dossiers:
        fichiers.extend(_lister_fichiers(d))
    fichiers.extend(fichiers_supplementaires or [])
    fichiers = sorted(set(fichiers))

    suspects = []
    for chemin in fichiers:
        try:
            with open(chemin, "r", encoding="utf-8") as fh:
                contenu = fh.read()
        except OSError:
            continue
        chemin_relatif = os.path.relpath(chemin, RACINE).replace("\\", "/")
        resultat = analyser_fichier(chemin_relatif, contenu)
        if resultat:
            ligne, motif = resultat
            suspects.append((chemin_relatif, ligne, motif))
    return suspects


def main():
    args = sys.argv[1:]
    if args:
        dossiers = args
        fichiers_supplementaires = []
    else:
        dossiers, fichiers_supplementaires = _dossiers_par_defaut()

    print("[source unique] Recherche de copies locales de la règle des heures effectives…\n")
    suspects = scanner(dossiers, fichiers_supplementaires)

    if suspects:
        print("[source unique] ÉCHEC — copie(s) suspecte(s) de la règle des heures détectée(s) :\n", file=sys.stderr)
        for chemin, ligne, motif in suspects:
            print(f"  ✖ {chemin}:{ligne} — {motif}", file=sys.stderr)
        print("", file=sys.stderr)
        print("[source unique] Déléguer à la source canonique au lieu de porter une copie locale :", file=sys.stderr)
        print("  from modules.aggregates import heures_effectives", file=sys.stderr)
        print("", file=sys.stderr)
        print(
            "[source unique] Historique (côté JS, même défaut) : budgetFingerprint.js et adaptBudgetLines.js "
            "(PR #12), computeReportModel.js (issue #11) portaient chacun leur propre copie, désynchronisée du "
            "correctif production_valeur (a07c72b) — écart mesuré : 6 530 $.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"[source unique] OK — aucune copie locale détectée ({len(dossiers)} dossier(s) + {len(fichiers_supplementaires)} fichier(s) scannés).")


if __name__ == "__main__":
    main()
