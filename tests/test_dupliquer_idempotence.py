# -*- coding: utf-8 -*-
"""La garde d'idempotence de POST /budget/projets/{id}/dupliquer.

Ce test est STATIQUE (il lit le source), et il l'assume : monter le router
complet exigerait la vraie base et un JWT de production, que la CI n'a pas
(cf. noyau, section « la ligne de partage »). Ce qu'il prouve quand meme,
et qui a reellement manque le 2 sept 2026 : la garde existe, elle refuse en
409, et elle s'execute AVANT que quoi que ce soit ne soit cree au hub --
une garde posee apres create_project laisserait une coquille dans Ad HUB a
chaque rejeu, ce qui est exactement le defaut qu'on ferme.

Eprouve a l'envers : retirer le bloc `if _recente:` fait rougir test_garde
et test_garde_avant_creation_hub ; retirer duplique_de_projet_id de l'INSERT
fait rougir test_provenance.
"""
import io
import os
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(RACINE, "modules", "ad_budget_api.py")


def _corps_endpoint():
    s = io.open(SOURCE, encoding="utf-8").read()
    debut = s.index('@router.post("/projets/{projet_id}/dupliquer")')
    fin = s.index('@router.post("/projets/{projet_id}/reviser-projet")', debut)
    return s[debut:fin]


def test_garde():
    corps = _corps_endpoint()
    assert "duplique_de_projet_id = %s" in corps, \
        "la garde ne cherche plus les copies recentes de la meme source"
    assert "status_code=409" in corps, \
        "une duplication rejouee doit etre refusee en 409, pas acceptee"
    assert "supprime_le IS NULL" in corps, \
        "une copie supprimee ne doit pas bloquer une nouvelle duplication"


def test_garde_avant_creation_hub():
    corps = _corps_endpoint()
    i_garde = corps.find("status_code=409")
    i_hub = corps.find("hub_service.create_project")
    assert i_garde >= 0, "la garde 409 a disparu de l'endpoint"
    assert i_hub >= 0, "create_project a disparu : ce test ne mesure plus rien"
    assert i_garde < i_hub, \
        "la garde doit refuser AVANT de creer le projet au hub, sinon chaque " \
        "rejeu laisse une coquille dans Ad HUB"


def test_provenance():
    corps = _corps_endpoint()
    i_insert = corps.find("INSERT INTO ad_budget.projets")
    assert i_insert >= 0, "l'INSERT du projet copie a disparu"
    bloc = corps[i_insert:i_insert + 1200]
    assert "duplique_de_projet_id" in bloc, \
        "la copie doit enregistrer de quel projet elle est la copie"


def test_migration_idempotente():
    chemin = os.path.join(RACINE, "migrations", "sprint_dupliquer_idempotence.sql")
    sql = io.open(chemin, encoding="utf-8").read()
    assert "ADD COLUMN IF NOT EXISTS duplique_de_projet_id" in sql
    assert "pg_constraint" in sql, \
        "la contrainte doit etre gardee, sinon le rejeu depuis une base vide casse"
    assert "CREATE INDEX IF NOT EXISTS" in sql


if __name__ == "__main__":
    echecs = 0
    for nom, fn in sorted(globals().items()):
        if nom.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", nom)
            except AssertionError as e:
                echecs += 1
                print("FAIL", nom, ":", e)
    sys.exit(1 if echecs else 0)
