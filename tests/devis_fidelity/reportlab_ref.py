"""Renderer reportlab de REFERENCE pour le harnais de fidelite du devis.

Pilote le VRAI `_build_devis` (celui servi a l'utilisateur, ad_devis_api.py) sur
un fichier temoin JSON, en injectant identite / entreprise / logo par monkeypatch
— SANS DB, SANS reseau. Le PDF produit est le « master » que le moteur jsPDF
(@adision/devis-pdf) doit egaler dans la tolerance.

Le logo injecte est le MEME base64 que le cote jsPDF (harness/assets/logo.b64.txt)
=> comparaison a armes egales. Logo bord-a-bord => rognage reportlab = no-op.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests._harness import make_get_conn, extract_nested  # noqa: E402


def _projet_row(fx):
    return {
        "id": 1,
        "nom": fx.get("nomProjet") or "",
        "organization_id": "org-test",
        "ad_hub_project_id": 307,
        "revision_no": 1,
        "revision_label": fx.get("revisionLabel") or "Originale",
        "arrondi_dollar": False,
        "hub_identity_snapshot": {} if fx.get("identitySource") == "snapshot" else None,
        "hub_identity_snapshot_at": fx.get("identitySnapshotAt"),
        "emitted_at_current_revision": False,
        "budget_modifie_depuis_emission": False,
    }


def _devis_row(fx):
    sig = fx.get("signature") or {}
    return {
        "projet_id": 1,
        "presentation": fx.get("presentation") or "",
        "travaux_inclus": fx.get("travauxInclus") or "",
        "travaux_non_inclus": fx.get("travauxNonInclus") or "",
        "responsable_nom": sig.get("nom") or "",
        "titre_responsable": sig.get("titre") or "",
        "organisation": sig.get("organisation") or "",
        "responsable_email": sig.get("courriel") or "",
        "responsable_tel": sig.get("telephone") or "",
        "documents": fx.get("documents") or [],
        "couleur": fx.get("couleur") or "adision",
    }


def _ident(fx):
    cli = fx.get("client") or {}
    return {
        "nom": fx.get("nomProjet") or "",
        "nom_client": cli.get("nom") or "",
        "contact_client": cli.get("contact") or "",
        "email_client": cli.get("courriel") or "",
        "telephone_client": cli.get("telephone") or "",
    }


def render_reportlab(fixture, logo_b64=None):
    """Retourne les octets PDF reportlab pour un fichier temoin donne."""
    import modules.ad_devis_api as D
    import modules.ad_budget_api as B
    import modules.hub_service as H

    entreprise = dict(fixture.get("entreprise") or {})
    entreprise.setdefault("neq", "")
    has_logo = bool(fixture.get("logoAsset")) and bool(logo_b64)
    entreprise["logo_url"] = "https://logo.test/proxy" if has_logo else None

    ident = _ident(fixture)
    source = fixture.get("identitySource") or "hub"
    if source == "none":
        ident = {}

    # ── Monkeypatch : identite, entreprise, logo (aucun reseau) ──────────────
    H.fetch_organization = lambda *a, **k: entreprise
    H.resolve_hub_identity_with_fallback = lambda projet, jwt, cache: (ident, source)

    def _logo_flowable(org, max_width=150):
        if has_logo:
            return B.build_pdf_logo(logo_b64, max_width=max_width)
        return ""
    D._logo_flowable_for_org = _logo_flowable

    # CORRECTIF INCIDENT 20 août 2026 — _build_devis ne prend plus le montant
    # tel quel : il relit ad_budget.budget_lignes et le recalcule via
    # compute_budget_totals (même chemin que le rapport de calcul), pour que
    # les deux documents ne puissent plus diverger. Ce harnais de fidélité
    # compare le RENDU (jsPDF vs reportlab) pour un montant DONNÉ par le
    # fichier témoin — il ne teste pas le calcul lui-même (couvert ailleurs,
    # côté compute_budget_totals). Monkeypatch pour continuer à piloter le
    # montant depuis le fichier témoin, sans fabriquer de lignes de budget
    # synthétiques dont la somme devrait retomber pile sur cette valeur.
    D._montant_avant_taxes_serveur = lambda *a, **k: float(fixture.get("montant") or 0)

    results = {
        "ad_budget.projets": ("one", _projet_row(fixture)),
        "ad_budget.devis": ("one", _devis_row(fixture)),
    }
    bdv = extract_nested(D.register_ad_devis_routes, make_get_conn(results), "_build_devis")
    buf, _snapshot, _src = bdv(
        1,
        float(fixture.get("montant") or 0),
        fixture.get("couleur") or "adision",
        "TOKEN.X",
        fixture.get("dateDevis"),
    )
    return buf.getvalue()
