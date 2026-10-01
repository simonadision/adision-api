# -*- coding: utf-8 -*-
"""Copies d'un meme projet par deux personnes (1er oct 2026, Simon).

Olivier et Simon ont copie « Oslo Traiteur » a 7 s d'intervalle : deux
« Oslo Traiteur (copie) », et chacun a travaille seul. La garde d'idempotence
ne regardait que le MEME utilisateur.

Ce que ce temoin tient :
  - le CLOISONNEMENT : la copie se fait dans l'organisation de la source, sans
    exception super_admin (meme famille que app-api#99) ;
  - l'AVERTISSEMENT entre collegues : 409 a `detail` OBJET, jamais bloquant
    (confirmer=true passe) ;
  - la NUMEROTATION par rang de provenance (duplique_de_projet_id), jamais par
    le nom ;
  - le VERROU sur la source (FOR UPDATE), pris AVANT les gardes et tenu
    jusqu'au commit : sans lui, deux copies simultanees liraient le meme rang
    (objection de PC1).

Statique pour l'endpoint (meme raison que test_dupliquer_idempotence.py : pas
de vraie base en CI), fonctionnel pour les deux fonctions pures.
"""
import io
import os
import sys
from datetime import datetime, timedelta, timezone

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
SOURCE = os.path.join(RACINE, "modules", "ad_budget_api.py")

from modules import ad_budget_api as A  # noqa: E402


def _corps():
    s = io.open(SOURCE, encoding="utf-8").read()
    debut = s.index('@router.post("/projets/{projet_id}/dupliquer")')
    fin = s.index('@router.post("/projets/{projet_id}/reviser-projet")', debut)
    return s[debut:fin]


def test_nom_de_copie_numerote_a_partir_de_la_deuxieme():
    assert A.nom_de_copie("Oslo Traiteur", 1) == "Oslo Traiteur (copie)"
    assert A.nom_de_copie("Oslo Traiteur", 2) == "Oslo Traiteur (copie 2)"
    assert A.nom_de_copie("Oslo Traiteur", 3) == "Oslo Traiteur (copie 3)"


def test_copies_recentes_age_en_secondes_et_plafond():
    maintenant = datetime(2026, 10, 1, 12, 0, 7, tzinfo=timezone.utc)
    rows = [{"id": 300 + i, "created_at": maintenant - timedelta(seconds=7 + i),
             "createur_id": 9, "createur_nom": "Olivier"} for i in range(8)]
    out = A.copies_recentes_de_collegues(rows, maintenant)
    assert len(out) == A.COPIE_AVERTIR_MAX == 5
    assert out[0] == {"budget_id": 300, "createur_id": 9, "createur_nom": "Olivier",
                      "cree_le": rows[0]["created_at"].isoformat(), "il_y_a_secondes": 7}
    sans_nom = A.copies_recentes_de_collegues(
        [{"id": 1, "created_at": maintenant, "createur_id": None, "createur_nom": None}], maintenant)
    assert sans_nom[0]["createur_nom"] == "un collègue"


def test_la_fenetre_est_un_jugement_ecrit_comme_tel():
    assert A.COPIE_AVERTIR_FENETRE_SECONDES == 86400
    s = io.open(SOURCE, encoding="utf-8").read()
    assert "UN JUGEMENT, PAS UNE MESURE" in s


def test_cloisonnement_sans_exception_super_admin():
    corps = _corps()
    assert 'src.get("organization_id") != user.get("organization_id")' in corps, \
        "la copie doit refuser une source d'une AUTRE organisation"
    i_cloison = corps.index('src.get("organization_id") != user.get("organization_id")')
    assert "super_admin" not in corps[i_cloison - 400:i_cloison + 200], \
        "aucune exception super_admin implicite autour du cloisonnement"
    assert corps.index("Projet introuvable", i_cloison) < corps.index("hub_service.create_project(")


def test_verrou_avant_les_gardes_et_avant_le_hub():
    corps = _corps()
    i_verrou = corps.index('"SELECT id FROM ad_budget.projets WHERE id = %s FOR UPDATE"')
    assert i_verrou < corps.index("AND user_id = %s"), "le verrou doit couvrir la garde anti double clic"
    assert i_verrou < corps.index("AND p.user_id <> %s"), "le verrou doit couvrir l'avertissement"
    assert i_verrou < corps.index("hub_service.create_project("), "le verrou doit couvrir la creation"
    # La copie s'ecrit sur la MEME connexion que le verrou : aucune nouvelle
    # connexion entre le verrou et l'INSERT, sinon le verrou ne protege rien.
    entre = corps[i_verrou:corps.index("INSERT INTO ad_budget.projets")]
    assert "get_conn()" not in entre, "une 2e connexion entre le verrou et l'INSERT annule le verrou"


def test_avertissement_collegues_non_bloquant():
    corps = _corps()
    assert '"motif": "copies_recentes"' in corps
    assert "if _collegues and not _confirmer:" in corps, "confirmer=true doit laisser passer"
    assert "AND p.organization_id = %s" in corps, "l'avertissement reste dans l'organisation"
    assert corps.index('"motif": "copies_recentes"') < corps.index("hub_service.create_project(")


def test_rang_par_provenance_jamais_par_le_nom():
    corps = _corps()
    i = corps.index("SELECT count(*) AS n FROM ad_budget.projets")
    assert "duplique_de_projet_id = %s" in corps[i:i + 200]
    assert "nom_de_copie(nom_source, rang_copie)" in corps
    assert '"rang_copie": rang_copie' in corps


if __name__ == "__main__":
    echecs = 0
    for nom, fn in sorted(globals().items()):
        if nom.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", nom)
            except Exception as e:
                echecs += 1
                print("FAIL", nom, ":", repr(e))
    print("---", "0 echec" if not echecs else "%d echec(s)" % echecs)
    sys.exit(1 if echecs else 0)
