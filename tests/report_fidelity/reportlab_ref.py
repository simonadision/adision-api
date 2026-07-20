"""Renderer reportlab de REFERENCE pour le harnais de fidelite du RAPPORT.

Pilote le VRAI `_build_projet_report` (celui servi, ad_budget_api.py) sur un
fichier temoin JSON { projet, lignes, options }, via un FakeDB (make_get_conn) —
SANS DB, SANS reseau (jwt_token=None -> aucun appel hub, identite vide, logo
neutre). Le PDF produit est le « master » que le moteur jsPDF (@adision/report-pdf)
doit egaler dans la tolerance (8 pt / 3 %).

Chaque temoin porte ses OPTIONS de rendu (orientation, avec_prix, colonnes,
sous_totaux/admin_profits, niveaux d'affichage, bandes) -> le master et le
candidat recoivent EXACTEMENT la meme config.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests._harness import make_get_conn, extract_nested  # noqa: E402


def _opt(options, key, default):
    v = options.get(key)
    return default if v is None else v


class _DateShim:
    """date.today() fige (determinisme du harnais). Delegue le reste a datetime.date."""
    def __init__(self, d):
        self._d = d

    def today(self):
        return self._d


def render_reportlab(fixture):
    """Retourne les octets PDF reportlab pour un temoin { projet, lignes, options }."""
    import datetime
    import modules.ad_budget_api as B

    projet = dict(fixture["projet"])
    lignes = list(fixture["lignes"])
    projet_id = int(fixture.get("projet_id") or projet.get("id") or 1)
    projet.setdefault("id", projet_id)
    o = dict(fixture.get("options") or {})

    # « Date du jour » : figee sur fixture.today pour un rendu deterministe
    # (le master reportlab et le candidat jsPDF doivent porter la MEME date).
    _today = fixture.get("today")
    _saved_date = getattr(B, "date", None)
    if _today:
        y, m, d = (int(x) for x in str(_today).split("-")[:3])
        B.date = _DateShim(datetime.date(y, m, d))
    try:
        return _render(B, projet, projet_id, lignes, o)
    finally:
        if _today and _saved_date is not None:
            B.date = _saved_date


def _render(B, projet, projet_id, lignes, o):

    results = {
        "ad_budget.projets": ("one", projet),
        "ad_budget.budget_lignes": ("all", lignes),
    }
    build = extract_nested(B.register_ad_budget_routes, make_get_conn(results), "_build_projet_report")

    buf, _snapshot = build(
        projet_id,
        _opt(o, "actifs_seulement", True),
        _opt(o, "avec_prix", True),
        _opt(o, "sections", ""),
        _opt(o, "colonnes", ""),
        o.get("sous_totaux"),         # None = tous
        o.get("admin_profits"),       # None = tous
        _opt(o, "avec_sous_total_avant_taxes", True),
        _opt(o, "avec_tps", True),
        _opt(o, "avec_tvq", True),
        _opt(o, "orientation", "portrait"),
        _opt(o, "mobilisation", 0),
        _opt(o, "surface_plancher", 0),
        _opt(o, "hauteur_cloisons", 0),
        _opt(o, "longueur_cloisons", 0),
        _opt(o, "avec_heures", True),
        jwt_token=None,
        inclure_ligne_groupe=_opt(o, "inclure_ligne_groupe", False),
        inclure_ligne_titres=_opt(o, "inclure_ligne_titres", True),
        afficher_lignes_detail=_opt(o, "afficher_lignes_detail", True),
        afficher_sous_totaux=_opt(o, "afficher_sous_totaux", True),
        afficher_totaux_section=_opt(o, "afficher_totaux_section", True),
        afficher_entete_section=_opt(o, "afficher_entete_section", True),
        afficher_entete_soussection=_opt(o, "afficher_entete_soussection", True),
        csi_div_labels=_opt(o, "csi_div_labels", ""),
        csi_sec_labels=_opt(o, "csi_sec_labels", ""),
    )
    return buf.getvalue()
