"""Tests des endpoints /admin/taux-horaires/* (modules/taux_horaires_api.py).

Approche DB — stub `get_conn` + curseur factice
------------------------------------------------
Le repo n'a aucun pattern de test contre une vraie DB (les tests existants,
test_auth_jwt.py / test_decode_token_graceful.py, n'ouvrent jamais de
connexion). On ne va pas introduire une dépendance Postgres pour ces tests,
d'autant que le module utilise du SQL Postgres-spécifique (schéma
`ad_budget.`, `ILIKE`, `RETURNING`) — incompatible avec un SQLite en mémoire.

On stube donc `get_conn` : un `FakeConn` renvoie un `FakeCursor` scripté.
Chaque test fournit la **séquence exacte** de valeurs que `fetchone()` /
`fetchall()` retourneront (test boîte-blanche : on connaît l'ordre des
`execute` de chaque endpoint). `FakeCursor.executed` capture les (SQL, params)
pour les assertions sur le WHERE dynamique (filtres, clamp, search).

Auth : un JWT super_admin est forgé avec un secret de test ≥ 32 octets.
"""
import time
from decimal import Decimal

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.taux_horaires_api import register_taux_horaires_routes

# Secret de test ≥ 32 octets (évite InsecureKeyLengthWarning de PyJWT).
TEST_SECRET = "taux-horaires-pytest-secret-au-moins-32-octets-ok"


@pytest.fixture(autouse=True)
def _set_jwt_secret(monkeypatch):
    """Pose JWT_SECRET pour que jwt_super_admin décode les JWT forgés ici."""
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)


def forge_jwt(role="super_admin", email="admin@adision.ca"):
    now = int(time.time())
    return jwt.encode(
        {"sub": email, "email": email, "role": role, "exp": now + 3600},
        TEST_SECRET, algorithm="HS256",
    )


def headers(role="super_admin"):
    return {"Authorization": f"Bearer {forge_jwt(role)}"}


class FakeCursor:
    """Curseur factice scripté : fetchone()/fetchall() consomment `responses`
    dans l'ordre. `executed` enregistre les (sql, params) pour inspection."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._responses.pop(0) if self._responses else None

    def fetchall(self):
        return self._responses.pop(0) if self._responses else []

    def close(self):
        pass


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def commit(self):
        pass

    def close(self):
        pass


def make_client(responses):
    """Monte le router taux horaires sur une app de test, avec un get_conn
    stubbé renvoyant un FakeCursor scripté. Retourne (TestClient, FakeCursor)."""
    cursor = FakeCursor(responses)
    conn = FakeConn(cursor)
    app = FastAPI()
    app.include_router(register_taux_horaires_routes(lambda: conn))
    return TestClient(app), cursor


# Rows factices ───────────────────────────────────────────────────────────
SAMPLE_TAUX = {
    "id": 1, "code": "CHARPENTIER_C", "metier": "Charpentier-menuisier",
    "qualification": "Compagnon", "taux_col17": Decimal("84.64"),
    "groupe": "metier", "ordre": 140, "notes": None, "actif": True,
    "updated_at": "2026-05-15T00:00:00", "updated_by": "seed",
}
SAMPLE_TAUX_2 = {**SAMPLE_TAUX, "id": 2, "code": "TUYAUTEUR_C",
                 "metier": "Tuyauteur", "taux_col17": Decimal("84.40"), "ordre": 420}
SAMPLE_MAPPING = {
    "csi_division": "03", "taux_id": 1, "updated_at": "2026-05-15T00:00:00",
    "updated_by": "seed", "taux_code": "CHARPENTIER_C",
    "taux_metier": "Charpentier-menuisier", "taux_col17": Decimal("84.64"),
    "taux_actif": True,
}


# ── Auth ──────────────────────────────────────────────────────────────────

def test_01_sans_token_401():
    client, _ = make_client([])
    r = client.get("/admin/taux-horaires")
    assert r.status_code == 401


def test_01_role_user_403():
    client, _ = make_client([])
    r = client.get("/admin/taux-horaires", headers=headers("user"))
    assert r.status_code == 403


# ── GET liste ─────────────────────────────────────────────────────────────

def test_02_liste_structure():
    client, _ = make_client([{"n": 2}, [SAMPLE_TAUX, SAMPLE_TAUX_2]])
    r = client.get("/admin/taux-horaires", headers=headers())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert body["limit"] == 200 and body["offset"] == 0


def test_03_filtre_actif_true():
    client, cur = make_client([{"n": 0}, []])
    client.get("/admin/taux-horaires?actif=true", headers=headers())
    sql_count = cur.executed[0][0]
    assert "actif = TRUE" in sql_count


def test_03_filtre_actif_false():
    client, cur = make_client([{"n": 0}, []])
    client.get("/admin/taux-horaires?actif=false", headers=headers())
    assert "actif = FALSE" in cur.executed[0][0]


def test_03_filtre_actif_all():
    client, cur = make_client([{"n": 0}, []])
    client.get("/admin/taux-horaires?actif=all", headers=headers())
    assert "actif" not in cur.executed[0][0]


def test_04_clamp_limit_max():
    client, _ = make_client([{"n": 0}, []])
    r = client.get("/admin/taux-horaires?limit=9999", headers=headers())
    assert r.json()["limit"] == 500


def test_04_clamp_limit_min():
    client, _ = make_client([{"n": 0}, []])
    r = client.get("/admin/taux-horaires?limit=0", headers=headers())
    assert r.json()["limit"] == 1


def test_04_clamp_offset_negatif():
    client, _ = make_client([{"n": 0}, []])
    r = client.get("/admin/taux-horaires?offset=-5", headers=headers())
    assert r.json()["offset"] == 0


def test_05_search_ilike():
    client, cur = make_client([{"n": 0}, []])
    client.get("/admin/taux-horaires?search=charp", headers=headers())
    sql_count, params = cur.executed[0]
    assert "ILIKE" in sql_count
    assert "%charp%" in params


# ── GET /{id} ─────────────────────────────────────────────────────────────

def test_06_get_id_existant():
    client, _ = make_client([SAMPLE_TAUX])
    r = client.get("/admin/taux-horaires/1", headers=headers())
    assert r.status_code == 200
    assert r.json()["code"] == "CHARPENTIER_C"


def test_06_get_id_inexistant_404():
    client, _ = make_client([None])
    r = client.get("/admin/taux-horaires/999", headers=headers())
    assert r.status_code == 404


# ── POST ──────────────────────────────────────────────────────────────────

VALID_POST = {
    "code": "NOUVEAU_C", "metier": "Nouveau métier", "qualification": "Compagnon",
    "taux_col17": "90.00", "groupe": "metier", "ordre": 999,
}


def test_07_post_creation_valide():
    client, _ = make_client([None, SAMPLE_TAUX])  # code libre, puis RETURNING
    r = client.post("/admin/taux-horaires", json=VALID_POST, headers=headers())
    assert r.status_code == 201
    assert r.json()["id"] == 1


def test_08_post_groupe_invalide_400():
    client, _ = make_client([])
    bad = {**VALID_POST, "groupe": "inconnu"}
    r = client.post("/admin/taux-horaires", json=bad, headers=headers())
    assert r.status_code == 400


def test_09_post_taux_negatif_422():
    # Pydantic Field(gt=0) rejette à la désérialisation -> 422.
    client, _ = make_client([])
    bad = {**VALID_POST, "taux_col17": "0"}
    r = client.post("/admin/taux-horaires", json=bad, headers=headers())
    assert r.status_code == 422


def test_10_post_code_duplique_409():
    client, _ = make_client([{"exists": 1}])  # SELECT 1 trouve le code
    r = client.post("/admin/taux-horaires", json=VALID_POST, headers=headers())
    assert r.status_code == 409


# ── PATCH ─────────────────────────────────────────────────────────────────

def test_11_patch_partiel_valide():
    client, _ = make_client([{"id": 1}, SAMPLE_TAUX])  # existe, puis RETURNING
    r = client.patch("/admin/taux-horaires/1", json={"notes": "maj"}, headers=headers())
    assert r.status_code == 200


def test_12_patch_body_vide_400():
    client, _ = make_client([])
    r = client.patch("/admin/taux-horaires/1", json={}, headers=headers())
    assert r.status_code == 400
    assert "Aucun champ" in r.json()["detail"]


def test_13_patch_groupe_invalide_400():
    client, _ = make_client([])
    r = client.patch("/admin/taux-horaires/1", json={"groupe": "xxx"}, headers=headers())
    assert r.status_code == 400


def test_13_patch_taux_negatif_400():
    # TauxHorairePatch.taux_col17 n'a pas Field(gt=0) -> garde-fou endpoint -> 400.
    client, _ = make_client([])
    r = client.patch("/admin/taux-horaires/1", json={"taux_col17": "0"}, headers=headers())
    assert r.status_code == 400


def test_14_patch_code_duplique_409():
    client, _ = make_client([{"id": 1}, {"exists": 1}])  # existe, code pris ailleurs
    r = client.patch("/admin/taux-horaires/1", json={"code": "DUP"}, headers=headers())
    assert r.status_code == 409


def test_14_patch_id_inexistant_404():
    client, _ = make_client([None])  # SELECT id -> None
    r = client.patch("/admin/taux-horaires/999", json={"notes": "x"}, headers=headers())
    assert r.status_code == 404


# ── DELETE soft ───────────────────────────────────────────────────────────

def test_15_delete_non_mappe_ok():
    inactif = {**SAMPLE_TAUX, "actif": False}
    client, _ = make_client([{"id": 1}, [], inactif])  # existe, 0 mapping, RETURNING
    r = client.delete("/admin/taux-horaires/1", headers=headers())
    assert r.status_code == 200
    assert r.json()["actif"] is False


def test_16_delete_mappe_409_liste_divisions():
    client, _ = make_client([
        {"id": 1},
        [{"csi_division": "03"}, {"csi_division": "06"}],
    ])
    r = client.delete("/admin/taux-horaires/1", headers=headers())
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "03" in detail and "06" in detail


# ── CSI mapping ───────────────────────────────────────────────────────────

def test_17_get_csi_mapping():
    client, _ = make_client([[SAMPLE_MAPPING]])
    r = client.get("/admin/csi-division-mapping", headers=headers())
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1


def test_18_patch_mapping_division_inexistante_404():
    client, _ = make_client([None])  # SELECT csi_division -> None
    r = client.patch("/admin/csi-division-mapping/99", json={"taux_id": 1},
                      headers=headers())
    assert r.status_code == 404


def test_18_patch_mapping_taux_inexistant_400():
    client, _ = make_client([{"csi_division": "03"}, None])  # division ok, taux None
    r = client.patch("/admin/csi-division-mapping/03", json={"taux_id": 999},
                      headers=headers())
    assert r.status_code == 400


def test_18_patch_mapping_taux_inactif_400():
    client, _ = make_client([{"csi_division": "03"}, {"actif": False}])
    r = client.patch("/admin/csi-division-mapping/03", json={"taux_id": 5},
                      headers=headers())
    assert r.status_code == 400


def test_18_patch_mapping_valide_200():
    client, _ = make_client([
        {"csi_division": "03"},
        {"actif": True},
        {"csi_division": "03", "taux_id": 5,
         "updated_at": "2026-05-16T00:00:00", "updated_by": "admin@adision.ca"},
    ])
    r = client.patch("/admin/csi-division-mapping/03", json={"taux_id": 5},
                      headers=headers())
    assert r.status_code == 200
    assert r.json()["taux_id"] == 5
