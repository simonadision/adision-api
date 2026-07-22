#!/usr/bin/env python3
"""Filet de PARITÉ du seuil d'inactivité de la détention (étape 1).

POURQUOI CE SCRIPT EXISTE
─────────────────────────
Le seuil vit dans DEUX services, deux dépôts, deux processus Python :

  • adision-app-api/modules/projects_api.py  → SEUIL_DETENTION_MINUTES
    tranche la PRISE de la détention (le WHERE de l'UPDATE atomique) ;
  • adision-api/modules/ad_budget_api.py     → SEUIL_DETENTION_MINUTES
    tranche l'ÉCRITURE (le gate _load_and_authorize_projet).

Ils ne peuvent pas partager littéralement la constante. Le premier jet posait
un commentaire « garder aligné » — c'est exactement le motif écarté à l'étape 3 :
une règle de sécurité dupliquée est une règle qui divergera, et un commentaire
documente la dette sans la retenir.

CE QUE LA DIVERGENCE COÛTERAIT, ET POURQUOI ELLE SERAIT SILENCIEUSE
Si le hub disait 15 min et Ad BUD 30 min : le hub accorderait la prise à un
repreneur au bout de 15 min, pendant qu'Ad BUD continuerait de refuser ses
écritures pendant 15 minutes de plus. L'utilisateur verrait « projet repris »
puis « projet détenu par vous-même » sur chaque sauvegarde. Aucune exception,
aucun log : juste des écritures refusées sans raison lisible.
Dans l'autre sens, Ad BUD laisserait passer des écritures d'un utilisateur que
le hub ne considère plus comme détenteur — deux personnes écrivant en même
temps, ce que tout le modèle existe pour empêcher.

Sortie : 0 si les deux valeurs concordent, 1 sinon. Aucun accès base, aucun
réseau, aucun secret — lisible à tout moment, y compris au pré-push.
"""
import os
import re
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(os.path.dirname(ICI))  # …/Adision RD (tests/ est à la racine du dépôt)

CIBLES = [
    ("hub    (prise)",
     os.path.join(RACINE, "adision-app-api", "modules", "projects_api.py")),
    ("Ad BUD (écriture)",
     os.path.join(RACINE, "adision-api", "modules", "ad_budget_api.py")),
]

MOTIF = re.compile(r"^SEUIL_DETENTION_MINUTES\s*=\s*(\d+)\s*$", re.MULTILINE)


def lire(chemin):
    """Valeur déclarée, ou None si le fichier ou la constante manque."""
    if not os.path.exists(chemin):
        return None, "fichier introuvable"
    with open(chemin, encoding="utf-8") as f:
        trouve = MOTIF.findall(f.read())
    if not trouve:
        return None, "SEUIL_DETENTION_MINUTES absent"
    if len(trouve) > 1:
        # Deux déclarations dans le même fichier : on ne devine pas laquelle
        # fait foi. C'est un défaut en soi.
        return None, f"{len(trouve)} déclarations — ambigu"
    return int(trouve[0]), None


def main():
    print("Parité du seuil de détention (étape 1 — verrou + miroir)")
    valeurs, echec = [], False
    for libelle, chemin in CIBLES:
        v, err = lire(chemin)
        if err:
            print(f"  [ÉCHEC] {libelle} : {err}\n           {chemin}")
            echec = True
        else:
            print(f"  [OK]    {libelle} : {v} minutes")
            valeurs.append(v)

    if echec:
        print("PARITÉ DU SEUIL : ÉCHEC — une déclaration manque ou est ambiguë.")
        return 1
    if len(set(valeurs)) != 1:
        print(f"PARITÉ DU SEUIL : ÉCHEC — valeurs divergentes {valeurs}.")
        print("  Le hub accorderait la prise à un moment où Ad BUD refuse encore")
        print("  les écritures (ou l'inverse). Défaut SILENCIEUX : aucune")
        print("  exception, juste des écritures refusées sans raison lisible.")
        return 1
    print(f"PARITÉ DU SEUIL OK — {valeurs[0]} minutes des deux côtés.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
