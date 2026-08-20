"""
PREUVE (net-zéro) — Vague 2 Phase C : verrou Ad BUD réconcilié avec le hub.

A) hub_service.toggle_project_verrou (helper) : httpx mocké → déballe {project},
   propage le code HTTP (403 gestionnaire). Aucun réseau réel.
B) route PATCH /budget/projets/{id}/verrou : lié → PROXY hub + miroir local ;
   refus hub → propagé ; budget autonome → fallback local (hub PAS appelé).
   hub_service.toggle_project_verrou monkeypatché + get_conn factice → net-zéro.
"""
import os
import sys
import json

os.environ.setdefault("PYTHONUTF8", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0


def check(label, got, expected):
    global PASS, FAIL
    ok = got == expected
    print(("  OK   " if ok else "  FAIL ") + f"{label}: {got!r}" + ("" if ok else f" (attendu {expected!r})"))
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ════════════════════════════════════════════════════════════════════
# A) helper hub_service.toggle_project_verrou — httpx mocké
# ════════════════════════════════════════════════════════════════════
import modules.hub_service as hs

print("\n[A] hub_service.toggle_project_verrou (httpx mocké) :")
_RESP = [None]


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload
        self.text = json.dumps(payload)
        self.content = b"x"
    def json(self):
        return self._p


class FakeClient:
    def __init__(self, *a, **k):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def request(self, method, url, headers=None, json=None):
        return _RESP[0]


_orig_httpx_client = hs.httpx.Client
hs.httpx.Client = FakeClient

_RESP[0] = FakeResp(200, {"project": {"id": 304, "is_verrouille": True,
                                      "verrouille_par": None, "verrouille_le": "2026-06-21T17:00:00+00:00"}})
proj = hs.toggle_project_verrou("tok", 304, True)
check("200 → déballe project.is_verrouille", proj.get("is_verrouille"), True)
check("200 → verrouille_le présent", bool(proj.get("verrouille_le")), True)

_RESP[0] = FakeResp(403, {"detail": "Seul un gestionnaire peut verrouiller."})
try:
    hs.toggle_project_verrou("tok", 304, True)
    check("403 → HubServiceError levée", False, True)
except hs.HubServiceError as e:
    check("403 → status_code", e.status_code, 403)

# RESTORE httpx.Client (sinon le TestClient de starlette, qui hérite de
# httpx.Client, est cassé par le FakeClient ci-dessus).
hs.httpx.Client = _orig_httpx_client

# ════════════════════════════════════════════════════════════════════
# B) route toggle_projet_verrou — proxy + fallback
# ════════════════════════════════════════════════════════════════════
from fastapi import FastAPI
from fastapi.testclient import TestClient
import modules.ad_budget_api as abm

print("\n[B] PATCH /budget/projets/{id}/verrou (route) :")

CFG = {"hub": 304}
CALLS = []


def fake_toggle(jwt, hub_id, lock):
    CALLS.append((hub_id, lock))
    if CFG.get("raise403"):
        raise hs.HubServiceError(403, "Seul un gestionnaire peut verrouiller.")
    return {"id": hub_id, "is_verrouille": bool(lock), "verrouille_par": None,
            "verrouille_le": "2026-06-21T17:00:00+00:00" if lock else None}


hs.toggle_project_verrou = fake_toggle


class FakeCursor:
    def __init__(self):
        self.s = ""; self.p = None
    def execute(self, sql, params=None):
        self.s = sql; self.p = params
    def fetchone(self):
        s = self.s
        if "SELECT user_id, organization_id, is_verrouille" in s:   # _load_and_authorize_projet
            return {"user_id": 7, "organization_id": "ORG-1", "is_verrouille": False}
        if "SELECT * FROM ad_budget.projets WHERE id" in s:         # toggle_projet_verrou (full row)
            pid = self.p[0] if self.p else None
            return {"id": pid, "ad_hub_project_id": CFG["hub"], "organization_id": "ORG-1",
                    "statut": "brouillon", "mobilisation": None, "surface_plancher": None,
                    "hauteur_cloisons": None, "longueur_cloisons": None,
                    "hub_identity_snapshot": None}
        if "UPDATE ad_budget.projets" in s and "RETURNING" in s:
            lock = "is_verrouille = TRUE" in s
            return {"is_verrouille": lock, "verrouille_par": ("simon@adision.ca" if lock else None),
                    "verrouille_le": ("2026-06-21T17:00:00+00:00" if lock else None)}
        if "INSERT INTO app_ana.project_snapshots" in s and "RETURNING id" in s:
            return {"id": 999}
        return None
    def fetchall(self):
        if "SELECT * FROM ad_budget.budget_lignes" in self.s:
            return []   # net-zéro : aucune ligne, _create_snapshot doit rester net-zéro aussi
        return []
    def close(self):
        pass


class FakeConn:
    def cursor(self, *a, **k):
        return FakeCursor()
    def commit(self):
        pass
    def rollback(self):
        pass
    def close(self):
        pass


def fake_get_conn():
    return FakeConn()


FAKE_USER = {"id": 7, "platform_role": "super_admin",
             "organization_id": "ORG-1", "org_role": "admin", "email": "simon@adision.ca"}


def fake_make_jwt_deps(get_conn):
    def jwt_user():
        return FAKE_USER
    return jwt_user, jwt_user, jwt_user, jwt_user  # BUD : 4-tuple


abm.make_jwt_deps = fake_make_jwt_deps

# _create_snapshot appelle resolve_hub_identity_with_fallback -> réseau réel (fetch_project)
# si non mocké. Net-zéro : identité vide, source "none" (chemin dégradé déjà géré en prod).
abm.hub_service.resolve_hub_identity_with_fallback = lambda projet_row, jwt_token, cache: ({}, "none")

app = FastAPI()
app.include_router(abm.register_ad_budget_routes(fake_get_conn))
client = TestClient(app)
H = {"Authorization": "Bearer tok"}

# B1 — budget LIÉ → proxy hub + miroir + snapshot au verrouillage
CFG["hub"] = 304
CFG.pop("raise403", None)
CALLS.clear()
r = client.request("PATCH", "/budget/projets/152/verrou", json={"verrouille": True}, headers=H)
check("lié + verrouille → 200", r.status_code, 200)
check("  → miroir is_verrouille true", r.json().get("is_verrouille"), True)
check("  → hub appelé (304, True)", CALLS, [(304, True)])
check("  → snapshot créé au verrouillage", r.json().get("verrou_snapshot_id"), 999)

# B2 — hub refuse (gestionnaire) → propagé
CFG["raise403"] = True
r = client.request("PATCH", "/budget/projets/152/verrou", json={"verrouille": True}, headers=H)
check("hub refuse → 403 propagé", r.status_code, 403)

# B3 — budget AUTONOME (sans lien hub) → fallback local, hub PAS appelé
CFG["hub"] = None
CFG.pop("raise403", None)
CALLS.clear()
r = client.request("PATCH", "/budget/projets/8/verrou", json={"verrouille": True}, headers=H)
check("autonome + verrouille → 200", r.status_code, 200)
check("  → verrou local is_verrouille true", r.json().get("is_verrouille"), True)
check("  → hub PAS appelé", CALLS, [])

# B4 — ÉPREUVE À L'ENVERS (URGENT 20 août) : budget AUTONOME + membre NON-gestionnaire
# → doit ÉCHOUER en 403. Avant ce correctif, ce chemin n'avait AUCUN gate de rôle et
# répondait 200 : c'est exactement le trou fermé par cette PR. Si ce test repasse au
# vert (200), le gate a été défait — régression critique.
FAKE_USER["org_role"] = "member"
FAKE_USER["platform_role"] = "client"
CFG["hub"] = None
CFG.pop("raise403", None)
CALLS.clear()
r = client.request("PATCH", "/budget/projets/8/verrou", json={"verrouille": False}, headers=H)
check("autonome + membre (non-gestionnaire) → 403", r.status_code, 403)
FAKE_USER["org_role"] = "admin"
FAKE_USER["platform_role"] = "super_admin"  # restaure l'état des cas précédents

print(f"\n=== {PASS} OK / {FAIL} FAIL ===")
sys.exit(1 if FAIL else 0)
