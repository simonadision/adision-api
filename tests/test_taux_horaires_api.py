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

from modules.ad_budget_api import register_ad_budget_routes
from modules.taux_horaires_api import (
    _load_taux_default_map,
    _resolve_taux_default,
    register_taux_horaires_routes,
)

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


def forge_jwt_user(email="estimateur@adision.ca"):
    """JWT d'un user normal Ad BUD : role 'user' + module ad_bud (requis par
    jwt_user._check_module). Utilisé pour /budget/taux-horaires."""
    now = int(time.time())
    return jwt.encode(
        {"sub": email, "email": email, "role": "user",
         "modules": ["ad_bud"], "exp": now + 3600},
        TEST_SECRET, algorithm="HS256",
    )


def headers_user(email="estimateur@adision.ca"):
    return {"Authorization": f"Bearer {forge_jwt_user(email)}"}


class FakeCursor:
    """Curseur factice scripté : fetchone()/fetchall() consomment `responses`
    dans l'ordre. `executed` enregistre les (sql, params) pour inspection."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.executed = []
        # Certains endpoints (ex. from-viu-v2) lisent cur.rowcount après un
        # DELETE/INSERT. Le FakeCursor n'exécute rien -> 0 suffit.
        self.rowcount = 0

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

    def rollback(self):
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

# Row renvoyé par _provision_user (jwt_user) : user local ad_budget.users.
# Non-None -> jwt_user ne déclenche pas l'INSERT d'auto-provision.
SAMPLE_USER_ROW = {
    "id": 7, "nom": "Estimateur", "email": "estimateur@adision.ca",
    "role": "user", "created_at": "2026-01-01T00:00:00",
}
# Row taux tel que renvoyé par GET /budget/taux-horaires : 6 champs only.
PUBLIC_TAUX = {
    "id": 1, "code": "CHARPENTIER_C", "metier": "Charpentier-menuisier",
    "qualification": "Compagnon", "taux_col17": Decimal("84.64"),
    "groupe": "metier",
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


# ── GET /budget/taux-horaires — lecture user (D4.0) ───────────────────────
# Le flux jwt_user consomme d'abord 1 fetchone (SAMPLE_USER_ROW, via
# _provision_user — non-None pour éviter l'INSERT), puis l'endpoint consomme
# 1 fetchall (la liste des taux).

def _taux_sql(cur):
    """Extrait le (sql, params) du SELECT sur ad_budget.taux_horaires —
    le 1er execute (SELECT users de _provision_user) est ignoré."""
    return next((s, p) for s, p in cur.executed
                if "ad_budget.taux_horaires" in s)


def test_19_budget_taux_jwt_user_200():
    """Un user normal (role=user + module ad_bud) accède à la lecture."""
    client, _ = make_client([SAMPLE_USER_ROW, [PUBLIC_TAUX]])
    r = client.get("/budget/taux-horaires", headers=headers_user())
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["code"] == "CHARPENTIER_C"


def test_19_budget_taux_sans_token_401():
    client, _ = make_client([])
    r = client.get("/budget/taux-horaires")
    assert r.status_code == 401


def test_20_budget_taux_6_champs_seulement():
    """Le SELECT ne projette que les 6 champs du picker — pas updated_*/notes."""
    client, cur = make_client([SAMPLE_USER_ROW, [PUBLIC_TAUX]])
    client.get("/budget/taux-horaires", headers=headers_user())
    sql, _ = _taux_sql(cur)
    select_part = sql.split("FROM")[0]
    for col in ("id", "code", "metier", "qualification", "taux_col17", "groupe"):
        assert col in select_part
    assert "notes" not in select_part
    assert "updated_by" not in select_part
    assert "updated_at" not in select_part


def test_20_budget_taux_filtre_actif():
    """La lecture user ne renvoie que les taux actifs."""
    client, cur = make_client([SAMPLE_USER_ROW, []])
    client.get("/budget/taux-horaires", headers=headers_user())
    sql, _ = _taux_sql(cur)
    assert "actif = TRUE" in sql


def test_21_budget_taux_filtre_groupe():
    client, cur = make_client([SAMPLE_USER_ROW, []])
    client.get("/budget/taux-horaires?groupe=occupation", headers=headers_user())
    sql, params = _taux_sql(cur)
    assert "groupe = %s" in sql
    assert "occupation" in params


def test_21_budget_taux_search_ilike():
    client, cur = make_client([SAMPLE_USER_ROW, []])
    client.get("/budget/taux-horaires?search=charp", headers=headers_user())
    sql, params = _taux_sql(cur)
    assert "ILIKE" in sql
    assert "%charp%" in params


def test_22_jwt_user_403_sur_admin():
    """Le même user normal qui lit /budget/taux-horaires reste refusé (403)
    sur les endpoints d'administration /admin/* (dependency jwt_super_admin)."""
    client, _ = make_client([])
    r = client.get("/admin/taux-horaires", headers=headers_user())
    assert r.status_code == 403


# ── D5 — helper _load_taux_default_map ────────────────────────────────────

def test_23_load_taux_map_construit_dict():
    """fetchall des mappings -> dict { csi_division: taux_col17 }."""
    cur = FakeCursor([[
        {"csi_division": "06", "taux_col17": Decimal("84.64")},
        {"csi_division": "22", "taux_col17": Decimal("80.00")},
    ]])
    m = _load_taux_default_map(FakeConn(cur))
    assert m == {"06": Decimal("84.64"), "22": Decimal("80.00")}


def test_23_load_taux_map_vide():
    """Aucun mapping -> dict vide."""
    assert _load_taux_default_map(FakeConn(FakeCursor([[]]))) == {}


def test_24_load_taux_map_sql_filtre_actif_join():
    """La requête filtre les taux actifs et joint les 2 tables — c'est le SQL
    qui exclut division non mappée / taux inactif. Le FakeCursor n'exécute
    rien : on vérifie donc que le filtre est bien présent dans la requête."""
    cur = FakeCursor([[]])
    _load_taux_default_map(FakeConn(cur))
    sql = cur.executed[0][0]
    assert "t.actif = TRUE" in sql
    assert "ad_budget.csi_division_default_metier" in sql
    assert "JOIN ad_budget.taux_horaires" in sql


# ── D5 — helper _resolve_taux_default ─────────────────────────────────────

def test_25_resolve_taux_section_mappee():
    """Section dont la division est mappée -> taux_col17 retourné ; la
    requête est paramétrée sur les 2 premiers caractères de la section."""
    cur = FakeCursor([{"taux_col17": Decimal("77.00")}])
    assert _resolve_taux_default("06 40 0", FakeConn(cur)) == Decimal("77.00")
    assert cur.executed[0][1] == ("06",)


def test_25_resolve_taux_division_non_mappee():
    """fetchone -> None (division absente du mapping) -> None."""
    assert _resolve_taux_default("99 99 9", FakeConn(FakeCursor([None]))) is None


def test_25_resolve_taux_section_vide_ou_courte():
    """Section None / vide / < 2 caractères -> None, sans aucune requête."""
    cur = FakeCursor([])
    assert _resolve_taux_default(None, FakeConn(cur)) is None
    assert _resolve_taux_default("", FakeConn(FakeCursor([]))) is None
    assert _resolve_taux_default("0", FakeConn(FakeCursor([]))) is None
    assert cur.executed == []


# ── D5 — site D : auto-fill à l'ajout manuel d'une ligne ──────────────────
# create_budget_ligne (POST /budget/projets/{id}/lignes, modules/ad_budget_api).
# Flux des requêtes consommées sur le FakeCursor partagé : 1) jwt_user ->
# _provision_user (SELECT users) ; 2) éventuel _resolve_taux_default ;
# 3) INSERT ... RETURNING *.

def make_budget_client(responses):
    """Monte le router Ad BUD sur une app de test, get_conn stubbé.
    Retourne (TestClient, FakeCursor)."""
    cursor = FakeCursor(responses)
    conn = FakeConn(cursor)
    app = FastAPI()
    app.include_router(register_ad_budget_routes(lambda: conn))
    return TestClient(app), cursor


def _insert_budget_ligne(cur):
    """(sql, params) de l'INSERT budget_lignes RETURNING * (site D)."""
    return next((s, p) for s, p in cur.executed
                if "INSERT INTO ad_budget.budget_lignes" in s)


def test_26_site_d_absence_declenche_resolution():
    """Sans taux_horaire fourni : _resolve_taux_default est appelé et son
    résultat est inséré comme dernier paramètre de l'INSERT."""
    client, cur = make_budget_client([
        SAMPLE_USER_ROW,                       # _provision_user
        {"taux_col17": Decimal("77.00")},      # _resolve_taux_default
        {"id": 1, "taux_horaire": Decimal("77.00")},  # INSERT RETURNING *
    ])
    r = client.post("/budget/projets/1/lignes",
                     json={"section": "06 40 0", "description": "Test"},
                     headers=headers_user())
    assert r.status_code == 200
    assert any("csi_division_default_metier" in s for s, _ in cur.executed)
    _, params = _insert_budget_ligne(cur)
    assert params[-1] == Decimal("77.00")


def test_26_site_d_division_non_mappee_zero():
    """Sans taux fourni et division non mappée (_resolve -> None) -> 0 inséré."""
    client, cur = make_budget_client([
        SAMPLE_USER_ROW,
        None,                                  # _resolve_taux_default -> None
        {"id": 1, "taux_horaire": 0},
    ])
    r = client.post("/budget/projets/1/lignes",
                     json={"section": "99 99 9", "description": "Test"},
                     headers=headers_user())
    assert r.status_code == 200
    _, params = _insert_budget_ligne(cur)
    assert params[-1] == 0


def test_27_site_d_taux_explicite_non_ecrase():
    """Si taux_horaire est fourni explicitement : il n'est PAS écrasé et
    _resolve_taux_default n'est PAS appelé."""
    client, cur = make_budget_client([
        SAMPLE_USER_ROW,
        {"id": 1, "taux_horaire": 95.5},       # pas de réponse de résolution
    ])
    r = client.post("/budget/projets/1/lignes",
                     json={"section": "06 40 0", "description": "Test",
                           "taux_horaire": 95.5},
                     headers=headers_user())
    assert r.status_code == 200
    assert not any("csi_division_default_metier" in s for s, _ in cur.executed)
    _, params = _insert_budget_ligne(cur)
    assert params[-1] == 95.5


# ── D5 — site C : auto-fill au push from-viu-v2 ───────────────────────────
# projects_from_viu_v2 (POST /budget/projects/from-viu-v2). En mode=existing,
# le FakeCursor sert dans l'ordre : 1) _provision_user (SELECT users) ;
# 2) SELECT projets ; 3) counts_before ; 4) samples post-DELETE (fetchall) ;
# 5) sections existantes (fetchall) ; 6) _load_taux_default_map (fetchall).
# Les INSERT de la boucle ne consomment rien (pas de RETURNING).

def test_28_site_c_from_viu_v2_autofill():
    """Push from-viu-v2 : le taux par défaut est résolu par division CSI,
    le mapping est chargé une seule fois, chaque INSERT reçoit son taux."""
    client, cur = make_budget_client([
        SAMPLE_USER_ROW,                                  # _provision_user
        {"id": 1, "nom": "Projet test", "user_id": 7},    # SELECT projets
        {"total": 0, "non_blindspot": 0,                  # counts_before
         "viu_items": 0, "blindspot_skeleton": 0},
        [],                                               # samples post-DELETE
        [],                                               # sections existantes
        [{"csi_division": "06",                           # _load_taux_default_map
          "taux_col17": Decimal("84.64")}],
    ])
    r = client.post("/budget/projects/from-viu-v2", json={
        "mode": "existing",
        "project_id": 1,
        "source_analysis_id": 18,
        "items": [
            {"csi_section": "06 40 0", "description": "Item mappé", "id": 1},
            {"csi_section": "99 10 0", "description": "Item non mappé", "id": 2},
            {"description": "Item sans section", "id": 3},
        ],
    }, headers=headers_user())
    assert r.status_code == 200

    # _load_taux_default_map appelé UNE seule fois (pas par item) : son JOIN
    # sur csi_division_default_metier ne doit apparaître qu'une fois.
    map_queries = [s for s, _ in cur.executed
                   if "csi_division_default_metier" in s]
    assert len(map_queries) == 1

    # Chaque INSERT budget_lignes (viu_v2) reçoit le bon taux_horaire en
    # dernier paramètre : division mappée -> 84.64 ; non mappée -> 0 ;
    # section absente -> 0.
    inserts = [p for s, p in cur.executed
               if "INSERT INTO ad_budget.budget_lignes" in s and "viu_v2" in s]
    assert len(inserts) == 3
    assert [p[-1] for p in inserts] == [Decimal("84.64"), 0, 0]
