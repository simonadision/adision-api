"""
Ad BUD — Constantes metier partagees.

Source de verite pour les valeurs hardcodees referencees a plusieurs endroits
du code (endpoints d'import Ad VIU, generation de squelette CSI, frontend
filter via API). Eviter de dupliquer ces listes — toute modification doit
passer par ce fichier.
"""
from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────────────
# Divisions CSI autorisees pour le scope Ad BUD.
#
# Decision produit (mai 2026, Simon) : Ad BUD couvre uniquement les divisions
# techniques transversales qu'un sous-module specifique (architecture Ad VIU
# par exemple) ne traite pas. Les items pousses depuis Ad VIU avec une
# division HORS de cette liste sont marques `hors_scope=true` a l'import,
# conserves pour audit/traceabilite mais affiches separement dans l'UI.
#
#   01     = frais generaux / conditions generales
#   20-23  = mecanique / CVAC / plomberie
#   25     = controles automatises
#   26-28  = electrique / communications / securite
#   31     = excavation / pieux
#
# Le `is_in_scope(section)` helper compare les 2 premiers chiffres de la
# section (apres normalisation des espaces) a ce set. Tuple (immutable)
# pour eviter les modifications accidentelles + signal "constante".
# ─────────────────────────────────────────────────────────────────────────────

AUTHORIZED_DIVISIONS: tuple[str, ...] = (
    "01", "20", "21", "22", "23", "25", "26", "27", "28", "31",
)


def get_division_code(section: str | None) -> str | None:
    """Extrait le code division (2 chiffres) d'une section CSI.

    Le format BD typique est '23 05 00' (avec espaces) mais on est tolerant
    aux variations : '23-05-00', '230500', '  23 05 00' → tous renvoient '23'.

    Retourne None si section vide ou format non reconnu (ne commence pas par
    2 chiffres apres normalisation).
    """
    if not section:
        return None
    # Strip + retire espaces internes (codes parfois saisis avec/sans espaces)
    normalized = section.strip().replace(" ", "").replace("-", "")
    if len(normalized) < 2 or not normalized[:2].isdigit():
        return None
    return normalized[:2]


def is_in_scope(section: str | None) -> bool:
    """True si la section CSI est dans une division autorisee Ad BUD.

    Une section avec format non reconnu (None, vide, ne commence pas par
    chiffres) est consideree HORS scope par defaut (conservatif : evite
    d'inclure du junk dans le scope par accident)."""
    code = get_division_code(section)
    return code is not None and code in AUTHORIZED_DIVISIONS
