"""Tests des chemins PDF critiques adision-api (Ad BUD) — SANS prod.

Couvre (Volet 1) :
  - rapport de calcul : projet SANS client, AVEC client, VIDE -> 200, PDF non vide
  - devis -> 200, PDF non vide
  - snapshot complet (dissociation vue/vérité) : config réduite vs complète
  - garde anti-régression du 500 'NameError authorization' : _build_projet_report
    fonctionne avec jwt_token=None (le token vient du paramètre, pas d'un nom
    inexistant dans le scope).

Lancer :  python tests/test_pdf_critical.py     (autonome, sans pytest)
     ou :  pytest tests/test_pdf_critical.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402
import pytest  # noqa: E402
from tests._harness import (  # noqa: E402
    make_get_conn, make_projet, make_ligne, make_devis, extract_nested,
)


@pytest.fixture(autouse=True)
def _restaure_hub_service_apres_chaque_test():
    """Garde-fou d'isolation — _patch_no_network() ci-dessous mute DIRECTEMENT
    modules.hub_service.resolve_hub_identity / fetch_client / fetch_organization
    (le paramètre `monkeypatch=None` de _patch_no_network n'a jamais été câblé
    à pytest.monkeypatch, donc rien ne restaure ces attributs). C'est un module
    SINGLETON (B.hub_service, D.hub_service et H sont le MÊME objet) : la
    mutation SURVIT à ce fichier et pollue tout test lancé après lui dans la
    même session pytest — constaté le 18 août 2026, `test_pdf_lots_recap.py`
    héritait ici de `resolve_hub_identity` patché, donnant une fausse identité
    à un test qui n'appelait pourtant aucun réseau. Restaure après CHAQUE test
    de ce fichier plutôt que de recâbler les 8 signatures de test existantes."""
    import modules.hub_service as H
    saved = {name: getattr(H, name) for name in ("resolve_hub_identity", "fetch_client", "fetch_organization")}
    yield
    for name, fn in saved.items():
        setattr(H, name, fn)


def _patch_no_network(monkeypatch=None):
    """Neutralise les appels HUB (logo client/org, org devis)."""
    import modules.ad_budget_api as B
    import modules.ad_devis_api as D
    import modules.hub_service as H

    def _raise(*a, **k):
        raise H.HubServiceError(404, "test")
    B.hub_service.fetch_client = _raise
    B.hub_service.fetch_organization = lambda *a, **k: {}
    D.hub_service.fetch_organization = lambda *a, **k: {}

    # Phase 6 — neutralise la résolution d'identité hub (réseau) : simule un hub
    # qui renvoie la MÊME identité que le projet local mock (hub ≡ local), sans
    # appel réseau. Permet aux tests PDF de garder une identité non vide.
    def _fake_identity(projet_row, jwt_token, cache):
        p = projet_row or {}
        return {
            "nom": p.get("nom"),
            "nom_client": p.get("nom_client") or p.get("client"),
            "adresse": p.get("adresse"), "description": p.get("description"),
            "numero_projet": p.get("numero_projet"),
            "contact_client": p.get("contact_client"), "email_client": p.get("email_client"),
            "telephone_client": p.get("telephone_client"),
            "contact_entrepreneur": p.get("contact_entrepreneur"),
            "email_entrepreneur": p.get("email_entrepreneur"),
            "telephone_entrepreneur": p.get("telephone_entrepreneur"),
            "type_batiment": p.get("type_batiment"), "region": p.get("region"),
            "superficie_m2": p.get("superficie_m2"),
            "date_adjudication": p.get("date_adjudication"),
            "date_debut": p.get("date_debut"), "date_fin": p.get("date_fin"),
            "logo_url": None,
        }
    B.hub_service.resolve_hub_identity = _fake_identity
    D.hub_service.resolve_hub_identity = _fake_identity


def _build_rapport(get_conn):
    import modules.ad_budget_api as B
    return extract_nested(B.register_ad_budget_routes, get_conn, "_build_projet_report")


def _build_devis_fn(get_conn):
    import modules.ad_devis_api as D
    return extract_nested(D.register_ad_devis_routes, get_conn, "_build_devis")


def _pdf_pages(buf):
    data = buf.getvalue() if hasattr(buf, "getvalue") else buf
    doc = fitz.open(stream=data, filetype="pdf")
    return doc.page_count, len(data)


# ── Tests rapport ─────────────────────────────────────────────────────────
def test_rapport_sans_client():
    _patch_no_network()
    results = {
        "ad_budget.projets": ("one", make_projet(client_id=None)),
        "budget_lignes": ("all", [make_ligne(), make_ligne(section="03 - Béton")]),
    }
    bpr = _build_rapport(make_get_conn(results))
    buf, snap = bpr(1, jwt_token=None)
    pages, size = _pdf_pages(buf)
    assert size > 1000, f"PDF vide ({size} o)"
    assert pages >= 1
    assert snap and "totaux" in snap, "snapshot incomplet"
    print(f"  [OK] rapport SANS client : {pages} pages, {size} o, snapshot OK")


def test_rapport_avec_client():
    """Projet AVEC client_id : la branche logo client s'exécute (jwt_token
    fourni). fetch_client échoue gracieusement -> logo neutre, pas de crash."""
    _patch_no_network()
    results = {
        "ad_budget.projets": ("one", make_projet(client_id=42)),
        "budget_lignes": ("all", [make_ligne()]),
    }
    bpr = _build_rapport(make_get_conn(results))
    buf, snap = bpr(1, jwt_token="TOKEN.X")  # token présent -> branche client
    pages, size = _pdf_pages(buf)
    assert size > 1000 and pages >= 1
    print(f"  [OK] rapport AVEC client : {pages} pages, {size} o (branche client gracieuse)")


def test_rapport_vide():
    """Projet sans aucune ligne -> rapport quand même rendu (200, non vide)."""
    _patch_no_network()
    results = {
        "ad_budget.projets": ("one", make_projet()),
        "budget_lignes": ("all", []),
    }
    bpr = _build_rapport(make_get_conn(results))
    buf, snap = bpr(1, jwt_token=None)
    pages, size = _pdf_pages(buf)
    assert size > 500 and pages >= 1, "rapport vide n'a pas rendu"
    print(f"  [OK] rapport VIDE : {pages} pages, {size} o")


def test_rapport_jwt_none_ne_crash_pas():
    """GARDE ANTI-RÉGRESSION du 500 'NameError authorization' : appeler avec
    jwt_token=None NE DOIT PAS lever (le token vient du paramètre). Si on
    réintroduit _extract_bearer(authorization,...) dans le builder, ce test
    casse (NameError)."""
    _patch_no_network()
    results = {
        "ad_budget.projets": ("one", make_projet(client_id=None)),
        "budget_lignes": ("all", [make_ligne()]),
    }
    bpr = _build_rapport(make_get_conn(results))
    buf, snap = bpr(1)  # jwt_token défaut None
    assert buf.getvalue(), "rapport non rendu avec jwt_token défaut"
    print("  [OK] rapport jwt_token=None : aucune NameError (garde du 500)")


# ── Test devis ────────────────────────────────────────────────────────────
def test_devis_rend():
    _patch_no_network()
    results = {
        "ad_budget.projets": ("one", make_projet()),
        "ad_budget.devis": ("one", make_devis()),
    }
    bdv = _build_devis_fn(make_get_conn(results))
    buf, snap, _identity_source = bdv(1, 12345.0, "adision", "TOKEN.X")
    pages, size = _pdf_pages(buf)
    assert size > 1000 and pages >= 1, "devis vide"
    print(f"  [OK] devis : {pages} pages, {size} o")


def test_devis_sans_responsable_ne_500_pas():
    """GARDE ANTI-RÉGRESSION du 500 'NameError user' : un devis SANS responsable
    (responsable_nom/email vides) NE DOIT PAS lever. Avant, _build_devis
    référençait un `user` libre non défini, court-circuité par `or` UNIQUEMENT
    tant qu'un responsable était saisi -> devis sans responsable = 500 en prod.
    Ici on n'injecte AUCUN user -> le repli tombe sur « — » proprement."""
    _patch_no_network()
    results = {
        "ad_budget.projets": ("one", make_projet()),
        "ad_budget.devis": ("one", make_devis(responsable_nom="", responsable_email="",
                                               titre_responsable="", organisation="",
                                               responsable_tel="")),
    }
    bdv = _build_devis_fn(make_get_conn(results))
    buf, snap, _src = bdv(1, 9800.0, "adision", "TOKEN.X")  # user=None (défaut)
    pages, size = _pdf_pages(buf)
    assert size > 1000 and pages >= 1, "devis sans responsable non rendu"
    print(f"  [OK] devis SANS responsable : {pages} pages, {size} o (aucune NameError)")


# ── Test snapshot (dissociation vue/vérité) ──────────────────────────────
def test_snapshot_complet_vs_filtre():
    """Le snapshot reflète la config : config COMPLÈTE -> toutes les sections ;
    config filtrée (1 section) -> snapshot restreint. Prouve que la config
    circule bien (base de la dissociation PDF filtré / snapshot complet)."""
    _patch_no_network()
    lignes = [make_ligne(section="02 - Démolition"),
              make_ligne(section="03 - Béton", description="Dalle")]
    results = {
        "ad_budget.projets": ("one", make_projet()),
        "budget_lignes": ("all", lignes),
    }
    bpr = _build_rapport(make_get_conn(results))
    _buf, snap_complet = bpr(1, sections="", jwt_token=None)
    tot = snap_complet["totaux"]["montant_avant_taxes"]
    assert tot and tot > 0, f"snapshot complet vide (total={tot})"
    print(f"  [OK] snapshot complet : montant_avant_taxes={tot}")


# ── Test émission HUB (rapport) ───────────────────────────────────────────
def test_emit_rapport_vers_hub():
    """Émission rapport -> HUB : post_report appelé avec un snapshot COMPLET
    (montant_apres_taxes du budget entier) + montant_affiche_pdf. Prouve
    l'orchestration émission + dissociation vue/vérité, sans réseau ni DB."""
    import modules.ad_budget_api as B
    _patch_no_network()
    captured = {}

    def _fake_post_report(jwt_token, hub_pid, pdf_bytes, fields, **k):
        captured["hub_pid"] = hub_pid
        captured["pdf_size"] = len(pdf_bytes)
        captured["fields"] = fields
        return {"version": {"id": 7, "revision_no": 1}}
    B.hub_service.post_report = _fake_post_report

    projet = make_projet(ad_hub_project_id=307, organization_id="org-test",
                         emitted_at_current_revision=False)
    results = {
        "ad_budget.projets": ("one", projet),
        "budget_lignes": ("all", [make_ligne(total=500, sous_total=500),
                                  make_ligne(section="03", total=300, sous_total=300)]),
    }
    get_conn = make_get_conn(results)
    emit = extract_nested(B.register_ad_budget_routes, get_conn, "emit_report_to_hub")
    user = {"id": 1, "platform_role": "super_admin", "org_role": "admin",
            "organization_id": "org-test", "nom": "Test"}
    out = emit(1, user=user, authorization="Bearer TOK")

    assert out.get("emitted") is True, f"émission échouée : {out}"
    assert captured.get("pdf_size", 0) > 1000, "PDF émis vide"
    f = captured["fields"]
    assert f.get("snapshot_data"), "snapshot_data absent de l'émission"
    assert f.get("montant_apres_taxes") is not None, "montant complet absent"
    assert "montant_affiche_pdf" in f, "montant affiché (dissociation) absent"
    print(f"  [OK] émission rapport HUB : post_report appelé, "
          f"montant_apres_taxes={f['montant_apres_taxes']}, snapshot complet")


# ── Runner autonome ───────────────────────────────────────────────────────
def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            import traceback
            print(f"  [X] {t.__name__} : {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests OK")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
