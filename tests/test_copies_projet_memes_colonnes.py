"""Ad BUD — DUPLIQUER et RÉVISER copient EXACTEMENT les mêmes colonnes.

Les deux routes font la même chose : créer un nouveau budget à partir d'un
budget existant. Elles portent chacune leur propre `INSERT INTO
ad_budget.projets`, et rien ne les tenait en phase — deux listes de colonnes
écrites à la main, à deux endroits, à deux moments.

CE QUE CETTE DIVERGENCE A DÉJÀ COÛTÉ :
  - 2 sept 2026, Simon, capture à l'appui, sur un projet DUPLIQUÉ : « les
    montant sous traitant n'est pas calculer. le total est erroné. » Cause :
    la liste omettait plusieurs drapeaux `_override` et `prix_unitaire_st`.
  - 9 sept 2026, en revue : `client_id` était copié par la DUPLICATION et
    PAS par la RÉVISION. Chaque révision naissait déliée de son client, et
    le PDF de cette révision perdait le logo du client (rang 1 de
    _resolve_logo_b64) — sur un document envoyé au client.

Le défaut n'est jamais dans la colonne : il est dans le fait que deux listes
censées être identiques puissent diverger sans que rien ne le dise. Ce test
compare les deux listes DANS LE CODE SOURCE et échoue à la première colonne
ajoutée d'un seul côté — y compris pour une colonne qui n'existe pas encore.

Aucune base, aucun réseau : lecture du source des deux endpoints.
"""
import inspect
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import extract_nested  # noqa: E402
import modules.ad_budget_api as B  # noqa: E402

_INSERT_PROJETS = re.compile(
    r"INSERT\s+INTO\s+ad_budget\.projets\s*\(([^)]*)\)", re.IGNORECASE
)


def _colonnes_insert(nom_endpoint):
    """Colonnes de l'INSERT INTO ad_budget.projets de cet endpoint."""
    fn = extract_nested(B.register_ad_budget_routes, lambda: None, nom_endpoint)
    listes = _INSERT_PROJETS.findall(inspect.getsource(fn))
    assert len(listes) == 1, (
        f"{nom_endpoint} : {len(listes)} INSERT INTO ad_budget.projets trouvé(s), "
        "1 attendu — ce test ne sait plus lequel comparer, le mettre à jour."
    )
    return {c.strip() for c in listes[0].replace("\n", " ").split(",") if c.strip()}


def test_dupliquer_et_reviser_copient_les_memes_colonnes():
    dup = _colonnes_insert("dupliquer_projet")
    rev = _colonnes_insert("reviser_projet_version")
    assert dup, "aucune colonne lue pour dupliquer_projet"
    manquantes_dans_revision = sorted(dup - rev)
    manquantes_dans_duplication = sorted(rev - dup)
    assert not manquantes_dans_revision, (
        "colonnes copiées par la DUPLICATION mais pas par la RÉVISION — une "
        f"révision naîtra amputée de : {manquantes_dans_revision}"
    )
    assert not manquantes_dans_duplication, (
        "colonnes copiées par la RÉVISION mais pas par la DUPLICATION — une "
        f"copie naîtra amputée de : {manquantes_dans_duplication}"
    )


def test_le_lien_client_est_copie_des_deux_cotes():
    """Le défaut nommé du 9 septembre 2026, verrouillé explicitement : sans
    client_id, le PDF d'une révision perd le logo du client."""
    for endpoint in ("dupliquer_projet", "reviser_projet_version"):
        assert "client_id" in _colonnes_insert(endpoint), (
            f"{endpoint} ne copie pas client_id — le budget créé sera délié "
            "de son client (logo client perdu dans son PDF)."
        )


def test_les_sous_totaux_natifs_sont_copies_des_deux_cotes():
    """`regroupements` porte les sous-totaux du bordereau. Absent d'un côté,
    la copie perdrait des lignes de sous-total visibles dans la soumission."""
    for endpoint in ("dupliquer_projet", "reviser_projet_version"):
        assert "regroupements" in _colonnes_insert(endpoint), (
            f"{endpoint} ne copie pas regroupements — les sous-totaux du "
            "bordereau disparaîtraient de la copie."
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
