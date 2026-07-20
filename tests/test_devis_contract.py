"""Test de CONTRAT sur GET /devis (option b) — SANS prod, SANS réseau.

Motif : le bug du logo venait d'un champ que le backend POSSÉDAIT
(fetch_organization().logo_url) mais que GET /devis n'EXPOSAIT PAS. Le harnais de
fidélité ne pouvait pas l'attraper : il fournit les MÊMES données aux deux
moteurs, donc il valide le RENDU, pas la PLOMBERIE (quel endpoint, quelle org,
quels champs atteignent le moteur client). Ce test garde la plomberie.

Vérifie que GET /devis :
  1. expose entreprise.logo_base64 non-null quand l'org du PROJET a un logo_url,
     et qu'il est IDENTIQUE à _org_logo_base64(org) (« champ non exposé » +
     « source divergente ») ;
  2. expose les champs d'identité attendus (entreprise: name/rbq/courriel/
     telephone ; client: nom/contact/courriel/telephone) ;
  3. interroge l'org du PROJET (projet.organization_id), PAS l'org active du JWT
     (c'était la moitié de la cause du bug) ;
  4. org sans logo_url -> logo_base64 = None, sans erreur.

Lancer :  python tests/test_devis_contract.py     (autonome)
     ou :  pytest tests/test_devis_contract.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_get_conn, make_projet, make_devis, extract_nested  # noqa: E402

PROJET_ORG = "ORG-DU-PROJET"
JWT_ORG = "ORG-ACTIVE-DIFFERENTE"


def _png_bytes():
    from PIL import Image
    img = Image.new("RGB", (48, 24), (30, 58, 138))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _setup(has_logo=True):
    """Monkeypatch identité / org / logo (aucun réseau). Retourne (D, org, called)."""
    import modules.ad_devis_api as D
    import modules.hub_service as H

    D._load_and_authorize_projet = lambda *a, **k: None  # bypass auth (testé ailleurs)

    ident = {
        "nom": "Anode de carbone", "nom_client": "Université Laval",
        "contact_client": "Etienne Drouin", "email_client": "etienne.drouin@si.ulaval.ca",
        "telephone_client": "418-905-0267",
    }
    H.resolve_hub_identity_with_fallback = lambda projet, jwt, cache: (ident, "hub")

    org = {
        "name": "Contracta", "rbq": "5707-5517-04", "neq": "1170000000",
        "courriel": "info@contracta.ca", "telephone": "418-931-0686",
        "logo_url": ("https://hub.test/api/organization/logo?organization_id=X" if has_logo else None),
    }
    called = {}

    def _fetch_org(jwt, organization_id=None):
        called["org_id"] = organization_id
        return org
    H.fetch_organization = _fetch_org
    H.fetch_project_documents = lambda *a, **k: None

    png = _png_bytes()

    class _Resp:
        status_code = 200
        content = png
    D.httpx.get = lambda url, timeout=10.0: _Resp()

    return D, org, called


def _call_get_devis(D):
    results = {
        "ad_budget.projets": ("one", make_projet(organization_id=PROJET_ORG, ad_hub_project_id=None)),
        "ad_budget.devis": ("one", make_devis()),
    }
    get_devis = extract_nested(D.register_ad_devis_routes, make_get_conn(results), "get_devis")
    user = {"id": 1, "nom": "Simon", "email": "simon@adision.ca",
            "organization_id": JWT_ORG, "platform_role": "super_admin"}
    return get_devis(1, user=user, authorization="Bearer TOK", session_cookie=None)


# ── Tests ────────────────────────────────────────────────────────────────
def test_contrat_logo_expose_et_identique_a_org_logo_base64():
    D, org, called = _setup(has_logo=True)
    resp = _call_get_devis(D)
    ent = resp["entreprise"]
    assert ent.get("logo_base64"), "logo_base64 ABSENT alors que l'org du projet a un logo_url (champ non exposé)"
    expected = D._org_logo_base64(org)
    assert ent["logo_base64"] == expected, "logo_base64 != _org_logo_base64(org) (source divergente PDF/JSON)"
    print(f"  [OK] logo_base64 exposé et IDENTIQUE à _org_logo_base64 ({len(ent['logo_base64'])} car.)")


def test_contrat_org_interrogee_est_celle_du_projet():
    D, org, called = _setup(has_logo=True)
    _call_get_devis(D)
    assert called.get("org_id") == PROJET_ORG, (
        f"fetch_organization appelé avec {called.get('org_id')!r} — attendu l'org du "
        f"PROJET {PROJET_ORG!r}, pas l'org active du JWT {JWT_ORG!r}")
    print(f"  [OK] org interrogée = org du PROJET ({PROJET_ORG}), pas l'org active du JWT ({JWT_ORG})")


def test_contrat_champs_identite_presents():
    D, org, called = _setup(has_logo=True)
    resp = _call_get_devis(D)
    ent, cli = resp["entreprise"], resp["client"]
    for f in ("name", "rbq", "courriel", "telephone"):
        assert ent.get(f), f"entreprise.{f} absent de GET /devis"
    for f in ("nom", "contact", "courriel", "telephone"):
        assert cli.get(f), f"client.{f} absent de GET /devis"
    print("  [OK] champs identité présents (entreprise: name/rbq/courriel/telephone ; client: nom/contact/courriel/telephone)")


def test_contrat_org_sans_logo_pas_d_erreur():
    D, org, called = _setup(has_logo=False)
    resp = _call_get_devis(D)
    assert resp["entreprise"].get("logo_base64") is None, "logo_base64 devrait être None quand l'org n'a pas de logo_url"
    print("  [OK] org sans logo_url -> logo_base64=None (pas d'erreur, placeholder neutre)")


# ── Runner autonome ───────────────────────────────────────────────────────
def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"  [X] {t.__name__} : {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests de contrat OK")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
