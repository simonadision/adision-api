"""
Ad BUD — Constantes metier partagees.

Source de verite pour les valeurs hardcodees referencees a plusieurs endroits
du code (endpoint d'import Ad VIU notamment). Eviter de dupliquer ces listes
— toute modification doit passer par ce fichier.
"""
from __future__ import annotations

import re


# ─────────────────────────────────────────────────────────────────────────────
# Divisions CSI = "angles morts" Ad VIU.
#
# Ad VIU analyse les plans architecturaux et structuraux ; il ne lit PAS :
#   01     = frais generaux / conditions generales
#   20-23  = mecanique / CVAC / plomberie
#   25     = controles automatises
#   26-28  = electrique / communications / securite
#   31-33  = genie civil / excavation / amenagement exterieur / utilites
#
# Lors d'un push Ad VIU -> Ad BUD, le backend DELETE toutes les lignes
# existantes du projet SAUF celles de ces 12 divisions (preservees parce
# qu'Ad VIU n'a aucune autorite dessus), puis INSERT tous les items
# detectes par Ad VIU sans filtre. Resultat : le squelette mecanique/civil
# Ad BUD reste intact, l'architectural est remplace par la detection IA.
#
# Tuple (immutable) pour signaler "constante" + eviter modifications
# accidentelles. Le frontend Ad VIU n'utilise pas cette liste (filtre
# pur backend, le frontend envoie tous les items sans pre-tri).
# ─────────────────────────────────────────────────────────────────────────────

AD_VIU_BLINDSPOT_DIVISIONS: tuple[str, ...] = (
    "01", "20", "21", "22", "23", "25", "26", "27", "28", "31", "32", "33",
)


_NON_DIGIT_RE = re.compile(r"\D")


def get_division_code(section: str | None) -> str | None:
    """Extrait le code division (2 chiffres) d'une section CSI.

    Strip TOUS les caracteres non-chiffres (regex \\D), puis prend les 2
    premiers chiffres restants. Robuste a tout format de saisie :
      '23 05 00'              → '23'
      '23-05-00'              → '23'
      '230500'                → '23'
      '02 - Site Preparation' → '02'   (texte suffix toleré)
      'Division 02 — Sitework'→ '02'   (texte prefix toleré aussi)
      '\\u00a002 21 13'       → '02'   (NBSP/tab/whitespace exotique OK)

    Retourne None si la section ne contient AUCUN chiffre (ex: 'TBD',
    None, vide).

    SQL miroir (a garder synchro) :
        LEFT(REGEXP_REPLACE(COALESCE(section, ''), '\\D', '', 'g'), 2)
    """
    if not section:
        return None
    digits_only = _NON_DIGIT_RE.sub("", section)
    if len(digits_only) < 2:
        return None
    return digits_only[:2]


def is_ad_viu_blindspot(section: str | None) -> bool:
    """True si la section appartient a une division qu'Ad VIU n'analyse pas.

    Utilise par le endpoint /budget/projects/from-viu-v2 pour determiner
    quelles lignes preserver lors du DELETE (REPLACE behavior) : les lignes
    dans une division "blindspot" (mecanique, civil, conditions generales)
    sont conservees ; les autres (architectural) sont effacees pour faire
    place aux items detectes par Ad VIU.

    Format non reconnu (None, vide, ne commence pas par chiffres) -> False
    (conservatif : on prefere DELETE une ligne au format douteux plutot
    que la garder a tort dans le squelette preserve)."""
    code = get_division_code(section)
    return code is not None and code in AD_VIU_BLINDSPOT_DIVISIONS
