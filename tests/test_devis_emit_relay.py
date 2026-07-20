"""Émission RELAIS du devis (Option B) — logique serveur, SANS prod, SANS hub.

Vérifie sans AUCUNE écriture vers le hub (post_report monkeypatché) :
  - contrôle de bon sens : octets non-PDF -> 400 clair (jamais 500) ;
  - budget INCHANGÉ (empreinte == courante) -> émission relais des OCTETS CLIENT
    (reportlab NON appelé), empreinte conservée dans les fields ;
  - budget CHANGÉ (empreinte != courante) sans force -> divergence_required, on
    N'ÉMET PAS ;
  - avec force=True -> émission quand même, budget_fingerprint (client) +
    budget_fingerprint_current (serveur) + budget_divergent=True conservés.

Lancer :  python tests/test_devis_emit_relay.py    (autonome)
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from fastapi import HTTPException  # noqa: E402
from tests._harness import make_get_conn, make_projet, make_devis, extract_nested  # noqa: E402

PDF_OK = b"%PDF-1.4\n" + b"x" * 1200  # PDF plausible (> 1000 o)
SERVER_FP = "server_fingerprint_courant"


class _Upload:
    """Stub minimal d'UploadFile : l'endpoint ne fait que pdf.file.read()."""
    def __init__(self, data):
        self.file = io.BytesIO(data)


def _setup(server_fp=SERVER_FP):
    import modules.ad_devis_api as D
    import modules.hub_service as H
    import modules.budget_fingerprint as BF

    D._load_and_authorize_projet = lambda *a, **k: None
    H.resolve_hub_identity_with_fallback = lambda projet, jwt, cache: (
        {"nom": "Projet", "contact_client": "Contact"}, "hub")
    H.fetch_organization = lambda *a, **k: {"name": "Ent"}
    # Empreinte serveur COURANTE contrôlée (pas de recalcul réel ici).
    BF.fingerprint_from_current_state = lambda projet, lignes: server_fp

    captured = {}

    def _post_report(jwt, hub_pid, pdf_bytes, fields, **k):
        captured["pdf_bytes"] = pdf_bytes
        captured["fields"] = fields
        return {"version": {"id": 9}}
    H.post_report = _post_report

    results = {
        "ad_budget.projets": ("one", make_projet(ad_hub_project_id=307)),
        "ad_budget.devis": ("one", make_devis()),
        "ad_budget.budget_lignes": ("all", []),
    }
    emit = extract_nested(D.register_ad_devis_routes, make_get_conn(results), "emit_devis_to_hub")
    return emit, captured


def _user():
    return {"id": 1, "nom": "Simon", "email": "s@x.ca", "organization_id": "org", "platform_role": "super_admin"}


def _call(emit, pdf_bytes=PDF_OK, empreinte=None, force=False):
    return emit(1, user=_user(), authorization="Bearer TOK", session_cookie=None,
                pdf=_Upload(pdf_bytes), montant=125000.0, couleur="adision",
                revision_choice=None, date_devis="2026-07-20", empreinte=empreinte, force=force)


def test_octets_non_pdf_rejetes_400():
    emit, _cap = _setup()
    try:
        _call(emit, pdf_bytes=b"NOT A PDF at all ...." + b"x" * 1200)
        raise AssertionError("aurait dû lever 400")
    except HTTPException as e:
        assert e.status_code == 400 and "PDF" in e.detail
    print("  [OK] octets non-PDF -> 400 clair (pas de 500)")


def test_taille_implausible_400():
    emit, _cap = _setup()
    try:
        _call(emit, pdf_bytes=b"%PDF-1.4\n")  # trop petit (< 1000 o)
        raise AssertionError("aurait dû lever 400")
    except HTTPException as e:
        assert e.status_code == 400
    print("  [OK] PDF trop petit -> 400 clair")


def test_budget_inchange_emet_les_octets_client():
    emit, cap = _setup()
    out = _call(emit, empreinte=SERVER_FP)  # empreinte == courante
    assert out.get("emitted") is True, out
    assert out.get("budget_divergent") is False
    assert cap["pdf_bytes"] == PDF_OK, "les OCTETS CLIENT doivent être relayés tels quels"
    f = cap["fields"]
    assert f["report_type"] == "proposition_devis"
    assert f["budget_fingerprint"] == SERVER_FP
    assert f["budget_fingerprint_current"] == SERVER_FP
    assert f["budget_divergent"] is False
    print("  [OK] budget inchangé -> relais des octets client, empreinte conservée")


def test_budget_change_sans_force_divergence_sans_emettre():
    emit, cap = _setup()
    out = _call(emit, empreinte="empreinte_ancienne_client")  # != SERVER_FP
    assert out.get("divergence_required") is True, out
    assert out.get("empreinte_courante") == SERVER_FP
    assert "pdf_bytes" not in cap, "NE DOIT PAS émettre tant que non forcé"
    print("  [OK] budget changé sans force -> divergence_required, aucune émission")


def test_budget_change_avec_force_emet_et_conserve_empreinte():
    emit, cap = _setup()
    out = _call(emit, empreinte="empreinte_ancienne_client", force=True)
    assert out.get("emitted") is True and out.get("budget_divergent") is True, out
    f = cap["fields"]
    assert f["budget_fingerprint"] == "empreinte_ancienne_client", "empreinte de l'ÉTAT DU PDF conservée"
    assert f["budget_fingerprint_current"] == SERVER_FP
    assert f["budget_divergent"] is True
    print("  [OK] force -> émet quand même, empreinte de l'ancien état conservée + état courant")


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
    print(f"\n{len(tests) - failed}/{len(tests)} tests émission relais OK")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
