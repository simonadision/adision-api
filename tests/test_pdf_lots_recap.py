"""Garde-fou — GET /projets/{id}/pdf-lots avec RÉCAP FINANCIER en en-tête.

Brief Simon, 18 août 2026 : le PDF « Ventilation par lot » doit reproduire le
bandeau écran (Matériaux / Main-d'œuvre / Sous-traitant, Coûtant total,
Administration et profit, Sous-total avant taxes, TPS/TVQ, Total avec taxes)
AVANT la table Lot -> sous-total.

Ce test NE RE-TESTE PAS compute_budget_totals ni compute_lot_totals (déjà
couverts par tests/test_divergences_ecran_rapport.py et tests/test_lots_calc.py)
— il prouve seulement le CÂBLAGE : que le PDF réellement produit par la route
contient, en texte, les chiffres que ces deux fonctions partagées calculent
sur le MÊME jeu de lignes. Même style de fausse DB en mémoire que
test_convert_hors_lot_endpoint.py (tests/_harness.py), sans réseau ni Postgres.

Extraction du texte PDF via PyMuPDF (fitz), déjà utilisé par
tests/report_fidelity (aucune nouvelle dépendance).

Lancer : python -m pytest tests/test_pdf_lots_recap.py -q
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402 — PyMuPDF, déjà dépendance du harnais de fidélité
import pytest  # noqa: E402

from tests._harness import extract_nested  # noqa: E402
import modules.ad_budget_api as B  # noqa: E402
from modules.lots_calc import compute_lot_totals  # noqa: E402


class _FakeCursor:
    def __init__(self, db):
        self._db = db
        self._pending = ("all", [])

    @staticmethod
    def _norm(sql):
        return " ".join(str(sql).split()).lower()

    def execute(self, sql, params=None):
        s = self._norm(sql)

        if "select user_id, organization_id, is_verrouille" in s:
            # Autorisation permissive (scoping déjà couvert ailleurs) — le
            # propriétaire matche l'appelant, mode="read" ne vérifie rien de
            # plus.
            self._pending = ("one", {
                "user_id": self._db["owner_id"], "organization_id": None,
                "is_verrouille": False, "detenteur_id": None,
                "detenteur_nom": None, "detenteur_email": None,
                "derniere_activite": None,
            })
            return

        if "select * from ad_budget.projets" in s:
            self._pending = ("one", dict(self._db["projet"]))
            return

        if "from ad_budget.lots" in s:
            self._pending = ("all", [dict(l) for l in self._db["lots"]])
            return

        if "from ad_budget.budget_lignes" in s:
            self._pending = ("all", [dict(l) for l in self._db["lignes"]])
            return

        self._pending = ("all", [])

    def fetchone(self):
        return self._pending[1] if self._pending[0] == "one" else None

    def fetchall(self):
        return self._pending[1] if self._pending[0] == "all" else []

    def close(self):
        pass


class _FakeConn:
    def __init__(self, db):
        self._db = db

    def cursor(self, *a, **k):
        return _FakeCursor(self._db)

    def commit(self):
        pass

    def close(self):
        pass


def _make_get_conn(projet, lots, lignes, owner_id=1):
    db = {"projet": projet, "lots": lots, "lignes": lignes, "owner_id": owner_id}
    return lambda: _FakeConn(db)


def _get_route(get_conn):
    return extract_nested(B.register_ad_budget_routes, get_conn, "export_projet_pdf_lots")


_USER = {"id": 1, "platform_role": "super_admin", "org_role": "admin",
          "organization_id": None, "nom": "Test"}


def _response_bytes(response):
    """StreamingResponse.body_iterator est un GÉNÉRATEUR ASYNC (Starlette enrobe
    tout itérable synchrone via iterate_in_threadpool) — pas consommable par un
    simple `for`/`.read()`. On le vide dans une boucle asyncio dédiée."""
    async def _collect():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, (bytes, bytearray)) else chunk.encode())
        return b"".join(chunks)
    return asyncio.run(_collect())


def _pdf_text(raw):
    doc = fitz.open(stream=raw, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return text


def test_recap_financier_chiffres_dans_le_pdf():
    projet = {
        "id": 42, "ad_hub_project_id": None, "arrondi_dollar": False,
        "revision_label": "Originale", "pct_admin_mode": "global",
        "pct_admin_conditions": 0, "pct_admin_architecture": 10,
        "pct_admin_mecanique": 20, "pct_admin_excavation": 0,
    }
    lots = [
        {"id": 1, "nom": "AB DEL", "nb_logements": 12, "ordre": 0},
        {"id": 2, "nom": "CD DEL", "nb_logements": 8, "ordre": 10},
    ]
    lignes = [
        # Matériaux, groupe "architecture" (division 03) — 10 * 100 = 1000 $, lot 1.
        {"id": 1, "section": "03 30 00", "description": "Béton", "a_completer": False,
         "lot_id": 1, "actif": True, "qte": 10, "prix_unitaire": 100,
         "ajust_materiaux": 0, "heures": 0, "taux_horaire": 0, "ajust_main_oeuvre": 0,
         "sous_traitant_montant": 0, "ajust_sous_traitant": 0, "ajustement_pct": 0,
         "unite": "m2", "heures_manuelles": False},
        # Main-d'œuvre, groupe "mecanique" (division 23) — 20 h * 50 $/h = 1000 $, lot 1.
        {"id": 2, "section": "23 05 00", "description": "Tuyauterie", "a_completer": False,
         "lot_id": 1, "actif": True, "qte": 0, "prix_unitaire": 0,
         "ajust_materiaux": 0, "heures": 20, "taux_horaire": 50, "ajust_main_oeuvre": 0,
         "sous_traitant_montant": 0, "ajust_sous_traitant": 0, "ajustement_pct": 0,
         "unite": "hr", "heures_manuelles": True},
        # Sous-traitant, hors regroupement (division 50) — 1000 $, lot 2.
        {"id": 3, "section": "50 00 00", "description": "Divers ST", "a_completer": False,
         "lot_id": 2, "actif": True, "qte": 1, "prix_unitaire": 0,
         "ajust_materiaux": 0, "heures": 0, "taux_horaire": 0, "ajust_main_oeuvre": 0,
         "sous_traitant_montant": 1000, "ajust_sous_traitant": 0, "ajustement_pct": 0,
         "unite": "global", "heures_manuelles": False},
        # Ligne INACTIVE — ne doit apparaître NULLE PART dans le récap (ni le
        # coûtant, ni le sous-total avant taxes), même filtre que l'écran.
        {"id": 4, "section": "03 30 00", "description": "Ignorée (inactif)",
         "a_completer": False, "lot_id": 1, "actif": False, "qte": 999,
         "prix_unitaire": 999, "ajust_materiaux": 0, "heures": 0, "taux_horaire": 0,
         "ajust_main_oeuvre": 0, "sous_traitant_montant": 0, "ajust_sous_traitant": 0,
         "ajustement_pct": 0, "unite": "m2", "heures_manuelles": False},
    ]

    get_conn = _make_get_conn(projet, lots, lignes)
    route = _get_route(get_conn)
    response = route(42, user=_USER, authorization=None, session_cookie=None)

    raw = _response_bytes(response)
    assert raw[:5] == b"%PDF-"
    text = _pdf_text(raw)

    # ── Attendu, calculé via le MÊME chemin partagé que la route (pas
    #    recalculé à la main : on prouve le câblage, pas l'arithmétique). ──
    active = [l for l in lignes if l["actif"]]
    result = compute_lot_totals(lignes, lots, projet["arrondi_dollar"])
    totals = B.compute_budget_totals(projet, active)
    coutant_total = result["grand_total"]
    admin_profit = totals["sous_total_avant_taxes"] - coutant_total
    assert coutant_total == pytest.approx(3000.0)
    assert admin_profit == pytest.approx(300.0)  # 1000*10% + 1000*20%
    assert totals["snap_mat"] == pytest.approx(1000.0)
    assert totals["snap_mo"] == pytest.approx(1000.0)
    assert totals["snap_st"] == pytest.approx(1000.0)
    assert totals["heures_mo"] == pytest.approx(20.0)

    for expected in (
        B._frca_num(totals["snap_mat"]),      # Matériaux
        B._frca_num(totals["snap_mo"]),       # Main-d'œuvre
        B._frca_num(totals["snap_st"]),       # Sous-traitant
        B._frca_num(coutant_total),           # Coûtant total
        B._frca_num(admin_profit),            # Administration et profit
        B._frca_num(totals["sous_total_avant_taxes"]),  # Sous-total avant taxes
        B._frca_num(totals["tps"]),           # TPS
        B._frca_num(totals["tvq"]),           # TVQ
        B._frca_num(totals["total_general"]), # TOTAL avec taxes
    ):
        assert expected in text, f"'{expected}' absent du PDF -- récap financier incomplet.\n{text}"

    assert "RÉCAP FINANCIER" in text
    assert "20 h" in text  # heures MO (aucun contremaître ici)
    # La ligne inactive (999 $) ne doit apparaître nulle part dans le récap.
    assert B._frca_num(999) not in text

    # La table Lot -> sous-total existante reste intacte, en dessous du récap.
    assert "AB DEL" in text and "CD DEL" in text
    assert "GRAND TOTAL" in text


def test_projet_sans_lignes_pas_de_recap():
    """Aucune ligne -> pas de bloc récap (même condition que l'écran,
    lignes.length > 0), mais la table (vide) et le PDF restent valides."""
    projet = {
        "id": 7, "ad_hub_project_id": None, "arrondi_dollar": False,
        "revision_label": "Originale", "pct_admin_mode": "global",
    }
    get_conn = _make_get_conn(projet, [], [])
    route = _get_route(get_conn)
    response = route(7, user=_USER, authorization=None, session_cookie=None)
    raw = _response_bytes(response)
    assert raw[:5] == b"%PDF-"
    text = _pdf_text(raw)
    assert "RÉCAP FINANCIER" not in text
    assert "GRAND TOTAL" in text
