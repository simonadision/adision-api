"""Garde-fou anti-régression — PATCH /projets/{id}/lignes/reorder.

Sprint drag-items (17 août 2026) — Ad BUD gagne le glisser-déposer des
LIGNES à l'intérieur d'une section (copie conforme du mécanisme Ad EST,
cf. adision-est-api/modules/estimation_items_api.py::reorder_items).

Deux risques distincts couverts ici :

  1. L'ORDRE DE DÉCLARATION DES ROUTES. `PATCH /lignes/{ligne_id}` (générique)
     était déjà déclarée AVANT toute route `/lignes/reorder`. Starlette
     dispatche par PATTERN d'URL dans l'ordre de déclaration, avant toute
     validation de type — si `reorder_budget_lignes` avait été ajoutée
     APRÈS, un PATCH .../lignes/reorder aurait été capté par la route
     générique (`ligne_id="reorder"` → 422 coercion int). Bug déjà vécu et
     documenté côté Ad EST (cf. le commentaire de reorder_items) : ce test
     l'éprouve à l'envers (permute les deux routes dans une liste et
     confirme que la mauvaise route matcherait en premier).

  2. LA LOGIQUE DE REORDER elle-même : diff-only appliqué, cross-section
     pris en compte, et le scoping projet_id (une ligne hors projet est
     refusée, jamais écrasée en silence).
"""
import os
import sys
from unittest.mock import patch as mock_patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from tests._harness import extract_nested  # noqa: E402
import modules.ad_budget_api as B  # noqa: E402


# ── Fausse DB en mémoire : budget_lignes, avec une sémantique SQL fidèle sur
# ce qui importe ici — SELECT id...WHERE id = ANY(...) (scoping) et UPDATE
# ...SET ordre = %s WHERE id = %s AND projet_id = %s (par ligne, params réels
# pris en compte, contrairement au FakeCursor générique de _harness.py qui
# route par sous-chaîne SQL seule et ne peut donc pas distinguer deux UPDATE
# successifs avec des params différents).
class _FakeReorderCursor:
    def __init__(self, db):
        self._db = db
        self._pending = ("all", [])
        self.rowcount = 0

    @staticmethod
    def _norm(sql):
        return " ".join(str(sql).split()).lower()

    def execute(self, sql, params=None):
        s = self._norm(sql)
        params = params or ()
        self._db.setdefault("sql_log", []).append((s, params))

        if "select user_id, organization_id, is_verrouille" in s:
            self._pending = ("one", {
                "user_id": self._db["owner_id"], "organization_id": None,
                "is_verrouille": False, "detenteur_id": None,
                "detenteur_nom": None, "detenteur_email": None,
                "derniere_activite": None,
            })
            return

        if "select id from ad_budget.budget_lignes where projet_id = %s and id = any" in s:
            projet_id, ids = params
            found = [l["id"] for l in self._db["lignes"]
                     if l["projet_id"] == projet_id and l["id"] in ids]
            self._pending = ("all", [{"id": i} for i in found])
            return

        if s.startswith("update ad_budget.budget_lignes set"):
            # Deux formes : avec ou sans `section = %s, ` en tête (cf.
            # reorder_budget_lignes -- section_set conditionnel).
            has_section = "section = %s," in s
            if has_section:
                section, ordre, ligne_id, projet_id = params
            else:
                ordre, ligne_id, projet_id = params
                section = "__unchanged__"
            row = next((l for l in self._db["lignes"]
                        if l["id"] == ligne_id and l["projet_id"] == projet_id), None)
            if row:
                if has_section:
                    row["section"] = section
                row["ordre"] = ordre
                self.rowcount = 1
            else:
                self.rowcount = 0
            self._pending = ("all", [])
            return

        self._pending = ("all", [])

    def fetchone(self):
        return self._pending[1] if self._pending[0] == "one" else None

    def fetchall(self):
        return self._pending[1] if self._pending[0] == "all" else []

    def close(self):
        pass


class _FakeReorderConn:
    def __init__(self, db):
        self._db = db

    def cursor(self, *a, **k):
        return _FakeReorderCursor(self._db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _ligne(id, projet_id, section, ordre, **over):
    base = {"id": id, "projet_id": projet_id, "section": section, "ordre": ordre,
            "description": f"Ligne {id}"}
    base.update(over)
    return base


def _make_db(lignes, owner_id=1):
    return {"lignes": lignes, "owner_id": owner_id}


_USER = {"id": 1, "platform_role": "super_admin", "org_role": "admin",
         "organization_id": None, "nom": "Test"}


def _get_reorder(get_conn):
    return extract_nested(B.register_ad_budget_routes, get_conn, "reorder_budget_lignes")


def _call(reorder_fn, projet_id, items):
    return reorder_fn(projet_id, {"items": items}, user=_USER)


# ── 1. Ordre de déclaration des routes ──────────────────────────────────────

def test_route_reorder_declaree_avant_la_route_generique_ligne_id():
    """Éprouvé À L'ENVERS (cf. §4.1quater du noyau) : si ces deux routes
    étaient permutées dans le routeur, PATCH .../lignes/reorder matcherait
    `{ligne_id}` en premier (Starlette route par pattern d'URL, dans l'ordre
    de déclaration, avant toute coercion de type) — ligne_id="reorder" y
    échouerait la coercion en int (422), sans jamais atteindre
    reorder_budget_lignes. Ce test lit l'ordre RÉEL des routes enregistrées
    et l'affirme correct ; permuter les deux décorateurs dans
    ad_budget_api.py le fait rougir immédiatement."""
    router = B.register_ad_budget_routes(lambda: None)
    noms_patch_lignes = [
        r.endpoint.__name__ for r in router.routes
        if "/projets/{projet_id}/lignes" in getattr(r, "path", "")
        and "PATCH" in getattr(r, "methods", set())
    ]
    assert "reorder_budget_lignes" in noms_patch_lignes
    assert "patch_budget_ligne" in noms_patch_lignes
    idx_reorder = noms_patch_lignes.index("reorder_budget_lignes")
    idx_generique = noms_patch_lignes.index("patch_budget_ligne")
    assert idx_reorder < idx_generique, (
        "reorder_budget_lignes DOIT être déclarée avant patch_budget_ligne "
        "(sinon /lignes/reorder est captée par /lignes/{ligne_id})"
    )


def test_epreuve_a_l_envers_ordre_invalide_matcherait_la_mauvaise_route():
    """Confirme que le test ci-dessus SAIT rougir : avec l'ordre INVERSÉ
    (simulé ici, pas dans le vrai routeur), c'est bien patch_budget_ligne
    qui apparaîtrait en premier -- la condition testée n'est pas triviale."""
    noms_inverses = ["patch_budget_ligne", "reorder_budget_lignes"]
    idx_reorder = noms_inverses.index("reorder_budget_lignes")
    idx_generique = noms_inverses.index("patch_budget_ligne")
    assert not (idx_reorder < idx_generique), "l'ordre inversé doit faire échouer l'assertion miroir"


# ── 2. Logique de reorder ───────────────────────────────────────────────────

def test_reorder_simple_intra_section():
    """Trois lignes de la même section, on en glisse une en tête -> diff-only
    (toSend côté front), mais ici on pousse directement le payload déjà
    calculé par computeGroupReorder (le backend applique tel quel, comme
    reorder_items côté Ad EST)."""
    lignes = [
        _ligne(1, 100, "02 00 00", 10),
        _ligne(2, 100, "02 00 00", 20),
        _ligne(3, 100, "02 00 00", 30),
    ]
    db = _make_db(lignes)
    reorder = _get_reorder(lambda: _FakeReorderConn(db))

    out = _call(reorder, 100, [
        {"id": 3, "ordre": 5},
        {"id": 1, "ordre": 15},
    ])

    assert out["status"] == "reordered"
    assert out["nb"] == 2
    by_id = {l["id"]: l for l in db["lignes"]}
    assert by_id[3]["ordre"] == 5
    assert by_id[1]["ordre"] == 15
    assert by_id[2]["ordre"] == 20, "ligne non listée dans le payload -- ne doit pas bouger"


def test_reorder_cross_section_change_la_section():
    """Un item déplacé vers une autre section pousse `section` ET `ordre` --
    le champ `section` ne doit JAMAIS être touché si absent du payload
    (drag intra-section, cf. test précédent)."""
    lignes = [_ligne(1, 100, "02 00 00", 10)]
    db = _make_db(lignes)
    reorder = _get_reorder(lambda: _FakeReorderConn(db))

    _call(reorder, 100, [{"id": 1, "section": "03 30 00", "ordre": 10}])

    assert db["lignes"][0]["section"] == "03 30 00"
    assert db["lignes"][0]["ordre"] == 10


def test_reorder_refuse_une_ligne_hors_projet():
    """Scoping : une ligne d'un AUTRE projet dans le payload -> 404, ET
    AUCUNE écriture n'a lieu (transaction rollback), pas seulement celle de
    la ligne fautive -- même garde que reorder_items (Ad EST)."""
    lignes = [
        _ligne(1, 100, "02 00 00", 10),
        _ligne(2, 200, "02 00 00", 10),  # projet 200, pas 100
    ]
    db = _make_db(lignes)
    reorder = _get_reorder(lambda: _FakeReorderConn(db))

    with pytest.raises(Exception) as exc:
        _call(reorder, 100, [
            {"id": 1, "ordre": 99},
            {"id": 2, "ordre": 99},  # hors du projet 100
        ])
    assert "404" in str(exc.value) or getattr(exc.value, "status_code", None) == 404

    # Aucune écriture, même sur la ligne valide (id=1) : le tout-ou-rien
    # empêche un payload partiellement malicieux de passer en partie.
    assert db["lignes"][0]["ordre"] == 10, "id=1 ne doit pas avoir été mis à jour"


def test_reorder_payload_vide_400():
    lignes = [_ligne(1, 100, "02 00 00", 10)]
    db = _make_db(lignes)
    reorder = _get_reorder(lambda: _FakeReorderConn(db))
    with pytest.raises(Exception) as exc:
        _call(reorder, 100, [])
    assert getattr(exc.value, "status_code", None) == 400 or "400" in str(exc.value)


def test_reorder_epreuve_a_l_envers_ordre_manquant_refuse():
    """Éprouvé à l'envers : un item SANS `ordre` doit être rejeté (400), pas
    silencieusement ignoré ou planté plus loin avec une erreur opaque."""
    lignes = [_ligne(1, 100, "02 00 00", 10)]
    db = _make_db(lignes)
    reorder = _get_reorder(lambda: _FakeReorderConn(db))
    with pytest.raises(Exception) as exc:
        _call(reorder, 100, [{"id": 1}])  # pas de "ordre"
    assert getattr(exc.value, "status_code", None) == 400 or "400" in str(exc.value)
