"""Harnais de tests PDF adision-api — SANS prod, SANS DB réelle.

Fournit :
  - un fake get_conn (FakeDB) qui route les requêtes par table et renvoie des
    lignes synthétiques configurables ;
  - l'extraction des fonctions imbriquées _build_projet_report / _build_devis
    depuis les routeurs (via __closure__) ;
  - des fabriques de projet/lignes/devis valides.

Aucun réseau : hub_service est monkeypatché par les tests.
"""
import io


# ── Fake DB ───────────────────────────────────────────────────────────────
class FakeCursor:
    """Curseur factice : renvoie des résultats canned selon la table visée
    dans le SQL. `results` = dict {mot_clé_sql: (mode, valeur)} où mode est
    'one' (fetchone) ou 'all' (fetchall)."""
    def __init__(self, results):
        self._results = results
        self._pending = None  # ('one'|'all', value)

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split()).lower()
        for key, (mode, val) in self._results.items():
            if key in s:
                self._pending = (mode, val)
                return
        # défaut : aucune ligne
        self._pending = ("all", [])

    def fetchone(self):
        if self._pending and self._pending[0] == "one":
            return self._pending[1]
        return None

    def fetchall(self):
        if self._pending and self._pending[0] == "all":
            return self._pending[1]
        return []

    def close(self):
        pass


class FakeConn:
    def __init__(self, results):
        self._results = results
    def cursor(self, *a, **k):
        return FakeCursor(self._results)
    def commit(self):
        pass
    def close(self):
        pass


def make_get_conn(results):
    """results : dict {sous-chaîne SQL (lower): ('one'|'all', valeur)}."""
    return lambda: FakeConn(results)


# ── Fabriques de données synthétiques ────────────────────────────────────
def make_projet(**over):
    """Projet ad_budget.projets synthétique complet (tous champs lus par les
    builders). client_id=None par défaut (= projet SANS client)."""
    p = {
        "id": 1, "nom": "Projet Test", "numero_projet": "P-001",
        "notes": "", "organization_id": "org-test", "user_id": 1,
        "logo_base64": "", "client_id": None,
        "client": None, "nom_client": "Client Test", "contact_client": "Contact",
        "email_client": "c@x.ca", "telephone_client": "514-000-0000",
        "contact_entrepreneur": "Entr", "email_entrepreneur": "e@x.ca",
        "telephone_entrepreneur": "514-111-1111",
        "date_debut": None, "date_fin": None,
        "ad_hub_project_id": None,
        "revision_no": 1, "revision_label": "Originale",
        "emitted_at_current_revision": False, "budget_modifie_depuis_emission": False,
        "arrondi_dollar": False,
    }
    p.update(over)
    return p


def make_ligne(**over):
    """Ligne ad_budget.budget_lignes synthétique (colonnes du SELECT rapport)."""
    l = {
        "section": "02 - Démolition", "description": "Démolition cloison",
        "unite": "m²", "qte": 10, "prix_unitaire": 50, "ajustement_pct": 0,
        "heures": 0, "taux_horaire": 0, "cout_sous_traitant": 0,
        "sous_traitant_nom": None, "ajust_materiaux": 0, "ajust_main_oeuvre": 0,
        "ajust_sous_traitant": 0, "sous_traitant_type": None,
        "sous_traitant_montant": 0, "sous_total": 500, "total": 500, "note": None,
        "actif": True,
    }
    l.update(over)
    return l


def make_devis(**over):
    """Ligne ad_budget.devis synthétique (PDF devis)."""
    d = {
        "projet_id": 1, "presentation": "À l'attention du client,\n\nProposition.",
        "travaux_inclus": "", "travaux_non_inclus": "",
        "responsable_nom": "Resp", "titre_responsable": "Estimateur",
        "organisation": "Ad FLO", "responsable_email": "r@x.ca",
        "responsable_tel": "514-222-2222", "documents": [], "couleur": "adision",
    }
    d.update(over)
    return d


# ── Extraction des builders imbriqués ────────────────────────────────────
def extract_nested(register_fn, get_conn, name):
    """Récupère la fonction imbriquée `name` depuis la closure des routes
    renvoyées par register_fn(get_conn)."""
    router = register_fn(get_conn)
    # 1) endpoint direct portant ce nom (route décorée).
    for route in getattr(router, "routes", []):
        if getattr(route.endpoint, "__name__", "") == name:
            return route.endpoint
    # 2) sinon, fonction imbriquée dans la closure d'un endpoint.
    for route in getattr(router, "routes", []):
        for cell in getattr(route.endpoint, "__closure__", None) or ():
            try:
                v = cell.cell_contents
            except ValueError:
                continue
            if callable(v) and getattr(v, "__name__", "") == name:
                return v
    raise AssertionError(f"{name} introuvable dans la closure")
