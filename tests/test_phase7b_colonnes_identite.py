"""Phase 7B — aucune SQL vivante ne doit nommer une colonne d'identité DROPpée.

migrations/sprint_phase7b_drop_identity.sql a supprimé 19 colonnes d'identité
de `ad_budget.projets` : l'identité projet vit dans Ad HUB (source unique,
Phase 6), relue via `ad_hub_project_id` / `hub_identity_snapshot`. Toute SQL
qui nomme encore une de ces colonnes est du code qui NE PEUT PAS s'exécuter —
`psycopg.errors.UndefinedColumn` au premier appel.

Ce verrou existe parce que le problème s'est manifesté deux fois, des deux
côtés :

  - Du code MORT décrivant le schéma disparu (les corps inertes derrière un
    `raise HTTPException(410)` de /duplicate et de /projects/from-viu) a fait
    conclure, le 9 septembre 2026, que « la duplication de projet est cassée en
    production » — alors que l'endpoint vivant (/dupliquer) était correct
    depuis le 2 septembre. Une SQL fausse mais jamais exécutée se lit quand
    même comme une spécification.

  - La liste blanche de `PUT /budget/projets/{id}` énumérait toujours les 19
    champs. Ils étaient bien interceptés en amont (403 `_HUB_OWNED_BUD_FIELDS`),
    donc inoffensifs — mais un seul assouplissement de ce 403 aurait suffi à
    fabriquer un `UPDATE ad_budget.projets SET nom = %s` sur une colonne
    inexistante.

Le test lit le SOURCE (aucune base, aucun réseau) : il tourne en CI comme au
pre-push. Il ne remplace pas le 403 — les deux gardes protègent des choses
différentes : le 403 protège la RÈGLE (l'identité s'édite dans Ad HUB), ce
test protège le SCHÉMA (ces colonnes n'existent plus).
"""
import ast
import io
import pathlib
import re

import pytest

# Les 19 colonnes de migrations/sprint_phase7b_drop_identity.sql, verbatim.
COLONNES_DROPPEES = (
    "nom", "nom_client", "client", "type_batiment", "region",
    "date_adjudication", "superficie_m2", "date_debut", "date_fin",
    "adresse", "description", "numero_projet",
    "contact_client", "email_client", "telephone_client",
    "contact_entrepreneur", "email_entrepreneur", "telephone_entrepreneur",
    "logo_base64",
)

RACINE = pathlib.Path(__file__).resolve().parent.parent

# Modules qui parlent à ad_budget.projets. Les scripts/ et tools/ (jetables,
# hors chemin de requête) sont volontairement hors périmètre.
MODULES = (
    "modules/ad_budget_api.py",
    "modules/ad_devis_api.py",
    "modules/ad_gabarits_api.py",
    "modules/ad_budget_matlink_api.py",
    "api.py",
)

# Nom de colonne NON qualifié : `nom` est un défaut, `u.nom` (table users) et
# `gabarit_nom` (alias) n'en sont pas. Le lookbehind `(?<![\w.])` écarte les
# deux, `\b` ferme à droite (`sous_traitant_nom` n'est pas `nom`).
_MOTIF = re.compile(
    r"(?<![\w.])(" + "|".join(sorted(COLONNES_DROPPEES, key=len, reverse=True)) + r")\b"
)
_EST_SQL = re.compile(r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b", re.I)


def _litteraux_sql_projets(chemin):
    """(ligne, sql, colonnes fautives) pour chaque littéral SQL du fichier qui
    cible ad_budget.projets. Passe par `ast` et non par une lecture ligne à
    ligne : une SQL sur trois lignes doit être jugée d'un seul tenant."""
    source = io.open(RACINE / chemin, encoding="utf-8").read()
    for noeud in ast.walk(ast.parse(source)):
        if not (isinstance(noeud, ast.Constant) and isinstance(noeud.value, str)):
            continue
        sql = noeud.value
        if "ad_budget.projets" not in sql or not _EST_SQL.search(sql):
            continue
        fautives = sorted(set(_MOTIF.findall(sql)))
        if fautives:
            yield noeud.lineno, " ".join(sql.split()), fautives


@pytest.mark.parametrize("chemin", MODULES)
def test_aucune_sql_ne_nomme_une_colonne_droppee(chemin):
    fautes = list(_litteraux_sql_projets(chemin))
    assert not fautes, "\n".join(
        f"{chemin}:{ligne} nomme {cols} — colonnes DROPpées en Phase 7B "
        f"(l'identité se lit dans Ad HUB) :\n    {sql[:300]}"
        for ligne, sql, cols in fautes
    )


def test_liste_blanche_du_put_projet_sans_champ_identite():
    """La liste blanche de PUT /budget/projets/{id} ne doit plus énumérer les
    champs d'identité : ils n'ont plus de colonne à mettre à jour."""
    source = io.open(RACINE / "modules/ad_budget_api.py", encoding="utf-8").read()
    # Le `for field in [ ... ]:` qui construit le SET de l'UPDATE projet.
    debut = source.index('        for field in [\n            "statut",')
    fin = source.index("]:", debut)
    champs = set(re.findall(r'"([a-z0-9_]+)"', source[debut:fin]))
    interdits = champs & set(COLONNES_DROPPEES)
    assert not interdits, (
        f"PUT /budget/projets/{{id}} énumère encore {sorted(interdits)} : "
        "ces colonnes n'existent plus (Phase 7B). L'identité s'édite dans Ad HUB."
    )


def test_le_403_identite_couvre_bien_les_19_colonnes():
    """Le filet AMONT (403) et le filet SCHÉMA (ce fichier) doivent parler des
    mêmes 19 champs — sinon l'un des deux laisse passer ce que l'autre croit
    couvert."""
    from modules.ad_budget_api import _HUB_OWNED_BUD_FIELDS

    assert _HUB_OWNED_BUD_FIELDS == set(COLONNES_DROPPEES)
