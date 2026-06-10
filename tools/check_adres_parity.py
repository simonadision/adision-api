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
import sys

# Features qui DOIVENT être présentes dans les 2 vues Ad RES (marqueur -> sens).
REQUIRED_IN_BOTH = [
    ("useBulkRemoveSelection(", "sélection multi + retrait en masse (hook partagé)"),
    ("Retirer la sélection", "bouton d'action de masse"),
    ("data-rowindex", "lasso drag-select (rows indexées)"),
]


def _monorepo(parent):
    return os.path.join(parent, "adision-monorepo")


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

    if problems:
        print("\nDIVERGENCE Ad RES (une feature dans une vue seulement) :")
        print("\n".join(problems))
        print("\n-> Appliquer la feature aux DEUX vues (idéalement via un hook @adision/ui partagé).")
        return 1
    print("\nPARITÉ Ad RES OK — les 2 modules consomment les mêmes features partagées.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
