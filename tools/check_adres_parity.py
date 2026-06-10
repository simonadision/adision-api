#!/usr/bin/env python3
"""Parité Ad RES — backstop anti-divergence des 2 modules.

Ad RES existe en DEUX implémentations (règle : toute feature fonctionnelle
s'applique aux DEUX) :
  - HUB client-facing : adision-monorepo/apps/adision-app/src/pages/AdRes.jsx
  - admin super_admin : adision-monorepo/apps/ad-adm/src/pages/AppelsOffres.jsx

La LOGIQUE risquée (sélection multi + retrait en masse) est factorisée en hook
headless partagé `useBulkRemoveSelection` (@adision/ui) -> single-source, pas de
divergence de comportement. Ce check garantit que les DEUX vues CONSOMMENT bien
les features partagées : si une vue a une feature et l'autre non, exit 1.

Usage (depuis n'importe où) :
    python adision-api/tools/check_adres_parity.py
"""
import os
import re
import sys

# Features qui DOIVENT être présentes dans les 2 vues Ad RES (marqueur -> sens).
REQUIRED_IN_BOTH = [
    ("useBulkRemoveSelection(", "sélection multi + retrait en masse (hook partagé)"),
    ("Retirer la sélection", "bouton d'action de masse"),
    ("data-rowindex", "lasso drag-select (rows indexées)"),
    # Groupe A — champs formulaire appel d'offres (pièces partagées @adision/ui).
    ("CsiSectionsSelect", "dropdown multi sections CSI (catalogue partagé)"),
    ("mapTypeContratAdRes", "type de contrat mappé (lu d'Ad HUB)"),
    ("AO_INSTRUCTIONS_DEFAULT", "instructions par défaut pré-remplies"),
    ("adHubProjectDocsUrl", "lien GED documents du projet"),
    # Groupe B — dates/heures/SEAO/BSDQ + modale ajustable.
    ("bsdqLimitDate", "calcul date BSDQ (3 j. ouvrables CCQ, helper partagé)"),
    ("heure_limite", "heure limite entrepreneurs généraux"),
    ("numero_seao", "champ # SEAO"),
    ("numero_bsdq", "champ # BSDQ"),
    ("resizable", "modale d'édition redimensionnable"),
    # Bloc 1 — confirmation sécurité avant envoi (accès docs destinataire).
    ("confirm_docs", "checkbox de confirmation avant envoi (sécurité GED)"),
    # Option A — aperçu EXACT des documents partagés dans la modale d'envoi.
    ("shared-documents", "aperçu des documents visibles par le destinataire (modale d'envoi)"),
    # Règle « toute entité créable est supprimable » — soft-delete AO + confirmation modale.
    ("Supprimer l'appel d'offres", "suppression (soft-delete) d'un appel d'offres + confirmation modale"),
    # Badge = réponse réelle du destinataire (Soumissionne/Décline/Indécis), source unique.
    ("adResInvitationBadge", "badge dérivé de la réponse réelle du destinataire"),
    # Retrait d'un invité = soft-delete (réponse conservée) + confirmation modale.
    ("Retirer l'invité", "retrait (soft-delete) d'un invité + confirmation modale"),
]


def _monorepo(parent):
    return os.path.join(parent, "adision-monorepo")


def _check_default_instructions(srcs, repo):
    """Garde-fou DUR du pré-remplissage des INSTRUCTIONS par défaut à la CRÉATION
    d'un AO (régression nuit 2026-06-09 : le champ s'est vidé sur un nouvel AO).

    Vérifie : (1) la constante partagée AO_INSTRUCTIONS_DEFAULT porte un vrai
    texte ; (2) chaque vue initialise EMPTY_AO.instructions sur cette constante.
    Imprime les [OK] inline ; RETOURNE la liste des régressions (chaînes [X])."""
    out = []
    ao_file = os.path.join(repo, "packages", "ui", "src", "adResAo.js")
    if not os.path.isfile(ao_file):
        return [f"  [X] adResAo.js introuvable ({ao_file})"]
    ao_src = open(ao_file, encoding="utf-8").read()
    m = re.search(r"AO_INSTRUCTIONS_DEFAULT\s*=\s*(.+?)(?:\nexport\b|\nconst\b|\nfunction\b|\Z)",
                  ao_src, re.S)
    literal_len = sum(len(s) for s in re.findall(r'"([^"]*)"', m.group(1))) if m else 0
    if literal_len < 60:
        out.append(f"  [X] AO_INSTRUCTIONS_DEFAULT vide/trop court ({literal_len} car.) dans "
                   "adResAo.js -> le texte par défaut a disparu (régression).")
    else:
        print(f"  [OK] AO_INSTRUCTIONS_DEFAULT présent ({literal_len} car.) dans adResAo.js")
    for name, src in srcs.items():
        if "instructions: AO_INSTRUCTIONS_DEFAULT" in src:
            print(f"  [OK] {name} : EMPTY_AO pré-remplit instructions par défaut à la création")
        else:
            out.append(f"  [X] {name} : EMPTY_AO sans 'instructions: AO_INSTRUCTIONS_DEFAULT' "
                       "-> nouvel AO créé SANS le texte par défaut (régression).")
    return out


def main():
    parent = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    repo = _monorepo(parent)
    views = {
        "HUB (adision-app/AdRes.jsx)":
            os.path.join(repo, "apps", "adision-app", "src", "pages", "AdRes.jsx"),
        "ad-adm (AppelsOffres.jsx)":
            os.path.join(repo, "apps", "ad-adm", "src", "pages", "AppelsOffres.jsx"),
    }
    srcs = {}
    for name, path in views.items():
        if not os.path.isfile(path):
            print(f"[?] Vue introuvable : {path}")
            return 2
        srcs[name] = open(path, encoding="utf-8").read()

    problems = []
    for marker, sense in REQUIRED_IN_BOTH:
        present = {name: (marker in src) for name, src in srcs.items()}
        if all(present.values()):
            print(f"  [OK] {sense} — présent dans les 2 vues")
        elif not any(present.values()):
            print(f"  [--] {sense} — absent des 2 (cohérent, pas de divergence)")
        else:
            has = [n for n, p in present.items() if p]
            miss = [n for n, p in present.items() if not p]
            problems.append(f"  [X] {sense} : présent dans {has} mais PAS dans {miss} -> DIVERGENCE")

    # Garde-fou DUR (au-delà de la parité) : pré-remplissage instructions par défaut.
    problems += _check_default_instructions(srcs, repo)

    if problems:
        print("\nPROBLÈME(S) Ad RES (divergence entre vues et/ou pré-remplissage par défaut) :")
        print("\n".join(problems))
        print("\n-> Corriger sur les DEUX vues (idéalement via un hook/const @adision/ui partagé).")
        return 1
    print("\nPARITÉ Ad RES OK — features partagées + pré-remplissage instructions par défaut intacts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
