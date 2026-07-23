"""TÉMOIN — les 4 routes d'écriture /item sont supprimées, /admin/items restent.

Recensement des 2026-07 : POST /item, PUT /item/{id}, DELETE /item/{id},
DELETE /items étaient du code mort (aucun appelant dans les 16 apps, aucun repo
serveur, aucun script) et NON autorisées au-delà de jwt_user, sur la table
maître globale ad_budget_prix_moyens que leur jumelle /admin/items réserve à
jwt_admin. Supprimées.

Ce test INSPECTE LA TABLE DE ROUTES de l'app (pas le source) : réintroduire une
route /item d'écriture le fait échouer. Il vérifie aussi que /admin/items et les
LECTURES du maître (GET /search, GET /item/{id}) sont intactes.
"""
from fastapi import FastAPI

from modules.ad_budget_api import register_ad_budget_routes

METHODES = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _routes():
    # get_conn factice : construire le router n'ouvre aucune connexion (les
    # handlers ne sont pas appelés, seules les routes sont enregistrées).
    app = FastAPI()
    app.include_router(register_ad_budget_routes(lambda: None))
    paires = set()
    for r in app.routes:
        for m in getattr(r, "methods", set()) or set():
            if m in METHODES:
                paires.add((m, r.path))
    return paires


def test_les_quatre_routes_item_ecriture_sont_absentes():
    routes = _routes()
    assert ("POST", "/budget/item") not in routes
    assert ("PUT", "/budget/item/{item_id}") not in routes
    assert ("DELETE", "/budget/item/{item_id}") not in routes
    assert ("DELETE", "/budget/items") not in routes


def test_admin_items_toujours_presentes_et_gatees():
    routes = _routes()
    # Mêmes opérations, même table — mais gardées par jwt_admin. Inchangées.
    assert ("POST", "/budget/admin/items") in routes
    assert ("PATCH", "/budget/admin/items/{item_id}") in routes
    assert ("DELETE", "/budget/admin/items/{item_id}") in routes


def test_les_lectures_du_maitre_sont_preservees():
    routes = _routes()
    # Ce lot ne touche QUE l'écriture : les lectures restent.
    assert ("GET", "/budget/item/{item_id}") in routes
    assert ("GET", "/budget/search") in routes
