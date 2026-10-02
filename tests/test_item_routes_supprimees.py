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
from modules.ad_budget_api import register_ad_budget_routes

METHODES = {"GET", "POST", "PUT", "PATCH", "DELETE"}

# Plancher de plausibilité : le routeur /budget compte ~69 routes (2 oct 2026).
# Sous ce seuil, l'énumération est cassée -- pas le routeur.
ROUTES_MINIMUM = 40


def _routes():
    """Paires (méthode, chemin) lues SUR LE ROUTEUR, jamais sur app.routes.

    2 oct 2026 (PC2, après le signalement de PC3) : avec fastapi 0.142,
    app.include_router n'aplatit plus les routes dans app.routes (il n'y reste
    que /docs, /openapi.json, /redoc et un _IncludedRouter). Ce banc voyait un
    ensemble VIDE : ses deux tests de présence rougissaient, et -- pire -- son
    test d'ABSENCE restait VERT PAR VACUITÉ, il aurait laissé revenir POST
    /budget/item sans broncher. Les chemins du routeur portent déjà /budget.

    get_conn factice : construire le routeur n'ouvre aucune connexion."""
    routeur = register_ad_budget_routes(lambda: None)
    paires = set()
    for r in routeur.routes:
        for m in getattr(r, "methods", set()) or set():
            if m in METHODES:
                paires.add((m, r.path))
    # GARDE ANTI-VACUITÉ : un test d'absence n'a de sens que si l'ensemble
    # où l'on cherche est vraiment peuplé. Règle (PC1) : tout test d'absence
    # porte un témoin de présence dans le MÊME ensemble.
    assert ("GET", "/budget/search") in paires and len(paires) >= ROUTES_MINIMUM, (
        f"énumération des routes cassée ({len(paires)} vues) : un test "
        "d'absence passerait par vacuité")
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
