"""Mapping CSI MasterFormat (FR) - port Python de
packages/aggregates/src/csi-divisions.js v1.1.0. Liste figée, pas de logique.
"""

CSI_DIVISIONS = {
    "00": "Non classé",
    "01": "Exigences générales",
    "02": "Conditions existantes",
    "03": "Béton",
    "04": "Maçonnerie",
    "05": "Métaux",
    "06": "Bois et plastiques",
    "07": "Isolation et étanchéité",
    "08": "Ouvertures",
    "09": "Finitions",
    "10": "Spécialités",
    "11": "Équipements",
    "12": "Ameublement",
    "13": "Construction spéciale",
    "14": "Équipement de transport",
    "21": "Protection incendie",
    "22": "Plomberie",
    "23": "CVAC",
    "26": "Électricité",
    "27": "Communications",
    "28": "Sécurité électronique",
    "31": "Terrassement",
    "32": "Aménagement extérieur",
    "33": "Services publics",
}


def get_csi_nom(code: str) -> str:
    """Nom de la division CSI ou fallback 'Division NN' (mêmes règles que JS)."""
    return CSI_DIVISIONS.get(code, f"Division {code}")
