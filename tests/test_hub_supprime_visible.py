# -*- coding: utf-8 -*-
"""TEMOIN — un budget dont le projet hub est SUPPRIME doit rester visible,
avec une mention, et ne jamais etre confondu avec une revision perimee.

INCIDENT, mesure en base de production le 11 septembre 2026. Cinq budgets
Ad BUD vivants pointaient vers un projet hub supprime :

    318 -> hub 784 (supprime le 9 sept, 1222 lignes)   273 -> hub 688
    192 -> hub 540   193 -> hub 541                    295 -> hub 773

Aucun n'apparaissait dans la liste. La liste SQL ne les excluait pourtant
pas (`p.supprime_le IS NULL` seulement) : c'est l'ENRICHISSEMENT qui les
tuait. Le hub rendait `est_revision_active = (est_revision_active AND
deleted_at IS NULL)`, donc false pour un projet supprime, et le front
masquait sur ce seul booleen — le meme geste que pour une revision perimee.
Du travail reel disparaissait sans un mot.

Ce fichier verrouille les trois decisions :
  1. la CAUSE remonte au front (`hub_supprime`), distincte de l'effet ;
  2. hub muet => on n'invente PAS une suppression (best-effort) ;
  3. AUCUNE cascade : la liste ne filtre toujours que `p.supprime_le`.
"""
import os
import re

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "modules", "ad_budget_api.py")


def _get_projets():
    src = open(SRC, encoding="utf-8").read()
    debut = src.index('@router.get("/projets")')
    return src[debut:src.index('@router.get("/projects/mine")')]


def test_la_cause_de_suppression_remonte_au_front():
    corps = _get_projets()
    assert 'r["hub_supprime"] = bool(m.get("supprime"))' in corps, (
        "hub_supprime ne remonte plus : le front ne peut plus distinguer un "
        "projet supprime d'une revision perimee, et masque les deux"
    )
    assert 'r["hub_supprime_le"] = m.get("supprime_le")' in corps


def test_hub_muet_ninvente_pas_une_suppression():
    """Best-effort : sans reponse du hub, un budget reste un budget normal."""
    corps = _get_projets()
    branche = corps[corps.index("# Pas de lien hub OU hub indisponible"):]
    branche = branche[:branche.index("return rows")]
    assert 'r["hub_supprime"] = False' in branche, (
        "la branche hub-muet doit poser hub_supprime a False, jamais a True"
    )


def test_aucune_cascade_de_suppression():
    """C du brief : on rend VISIBLE, on ne supprime/archive/masque rien."""
    corps = _get_projets()
    assert corps.count("supprime_le IS NULL") == 1, (
        "la liste doit toujours ne filtrer que la corbeille LOCALE Ad BUD"
    )
    for interdit in ("DELETE FROM", "UPDATE ad_budget.projets SET supprime_le"):
        assert interdit not in corps, (
            "GET /projets ne doit rien detruire : %r trouve" % interdit
        )
    # Aucun filtre qui exclurait un budget sur l'etat du projet hub.
    assert not re.search(r"if\s+.*hub_supprime.*:\s*\n\s*continue", corps), (
        "un budget au hub supprime ne doit pas etre retire de la reponse"
    )


def test_la_provenance_dune_copie_est_nommee():
    """B du brief : « copie de <nom>, <date> » sous le titre."""
    corps = _get_projets()
    assert 'r["duplique_de_nom"]' in corps and 'r["duplique_le"]' in corps, (
        "la provenance d'une copie ne remonte plus au front"
    )
    # Le nom de la source passe par le MEME batch hub (pas un appel de plus).
    assert "hub_ids += [h for h in sources.values() if h is not None]" in corps, (
        "le nom de la source doit entrer dans le batch revision-meta existant"
    )


def test_le_front_naffiche_pas_une_copie_sans_source_connue():
    """Hub muet : on dit « copie », on ne fabrique pas un nom."""
    corps = _get_projets()
    bloc = corps[corps.index('r["duplique_de_nom"] = None'):]
    bloc = bloc[:bloc.index('hid = r.get("ad_hub_project_id")')]
    assert 'r["duplique_de_nom"] = (msrc or {}).get("name")' in bloc, (
        "le nom de la source doit rester None quand le hub ne le rend pas"
    )
