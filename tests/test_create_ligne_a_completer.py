"""Garde-fou — POST /projets/{id}/lignes : passthrough `a_completer` et
`lot_id` (Phase C, workflow avancé Lot).

Couvre exactement le geste ajouté en Phase C : une ligne créée par
application multi-lot SANS copier la quantité doit être persistée avec
a_completer=TRUE (jamais sur la ligne d'origine, jamais par défaut) ; et une
ligne créée DANS un lot doit persister son lot_id (le routage manquant que
la Phase C corrige -- avant, une ligne créée depuis une section de lot
restait hors-lot).

Même style de fausse DB en mémoire que test_duplicate_lot_endpoint.py /
test_convert_hors_lot_endpoint.py, taux_horaire toujours fourni explicitement
pour ne pas avoir à faire semblant de _resolve_taux_default.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import extract_nested  # noqa: E402
import modules.ad_budget_api as B  # noqa: E402


class _FakeCreateLigneCursor:
    def __init__(self, db):
        self._db = db
        self._pending = ("all", [])

    @staticmethod
    def _norm(sql):
        return " ".join(str(sql).split()).lower()

    def execute(self, sql, params=None):
        s = self._norm(sql)
        params = params or ()

        if "select user_id, organization_id, is_verrouille" in s:
            # _load_and_authorize_projet : projet permissif (pas de verrou,
            # pas de détenteur) -- ce test porte sur le passthrough des
            # champs, pas sur le scope, déjà couvert ailleurs.
            self._pending = ("one", {
                "user_id": 1, "organization_id": None, "is_verrouille": False,
                "detenteur_id": None, "detenteur_nom": None,
                "detenteur_email": None, "derniere_activite": None,
            })
            return

        if "insert into ad_budget.budget_lignes" in s and "returning" in s:
            (projet_id, source_item_id, section, description, unite, prix_unitaire,
             qte, ajustement_pct, note, actif, item_id_ad_mat, ad_hub_pending_id,
             taux_horaire, lot_id, a_completer) = params
            self._db["next_id"] += 1
            row = {
                "id": self._db["next_id"], "projet_id": projet_id,
                "source_item_id": source_item_id, "section": section,
                "description": description, "unite": unite, "prix_unitaire": prix_unitaire,
                "qte": qte, "ajustement_pct": ajustement_pct, "note": note, "actif": actif,
                "item_id_ad_mat": item_id_ad_mat, "ad_hub_pending_id": ad_hub_pending_id,
                "taux_horaire": taux_horaire, "lot_id": lot_id, "a_completer": a_completer,
            }
            self._db["lignes"].append(row)
            self._pending = ("one", dict(row))
            return

        self._pending = ("all", [])

    def fetchone(self):
        return self._pending[1] if self._pending[0] == "one" else None

    def fetchall(self):
        return self._pending[1] if self._pending[0] == "all" else []

    def close(self):
        pass


class _FakeCreateLigneConn:
    def __init__(self, db):
        self._db = db

    def cursor(self, *a, **k):
        return _FakeCreateLigneCursor(self._db)

    def commit(self):
        pass

    def close(self):
        pass


def _make_db():
    return {"lignes": [], "next_id": 0}


def _get_create_ligne(get_conn):
    return extract_nested(B.register_ad_budget_routes, get_conn, "create_budget_ligne")


def _call(create_ligne, projet_id, data):
    # _mark_budget_dirty_if_emitted est une fermeture interne (pas un
    # attribut du module, donc non patchable) -- son UPDATE tombe dans la
    # branche par défaut (no-op) de _FakeCreateLigneCursor, comme le reste
    # du SQL non reconnu.
    with patch.object(B, "_push_budget_snapshot", lambda *a, **k: None):
        return create_ligne(projet_id, data, authorization=None, session_cookie=None, user=_USER)


_USER = {"id": 1, "platform_role": "super_admin", "org_role": "admin",
         "organization_id": None, "nom": "Test"}


def test_a_completer_absent_par_defaut_false():
    """Non-régression : une création normale (comme avant Phase C, sans le
    paramètre) ne pose JAMAIS le badge."""
    db = _make_db()
    create_ligne = _get_create_ligne(lambda: _FakeCreateLigneConn(db))
    out = _call(create_ligne, 290, {"section": "06 40 00.01", "taux_horaire": 0, "lot_id": 10})
    assert out["ligne"]["a_completer"] is False, out


def test_a_completer_true_pose_uniquement_sur_la_copie_demandee():
    """Copie multi-lot SANS copier la quantité -> a_completer=TRUE persisté,
    et distinct par appel (jamais posé sur un appel qui ne le demande pas)."""
    db = _make_db()
    create_ligne = _get_create_ligne(lambda: _FakeCreateLigneConn(db))
    origine = _call(create_ligne, 290, {
        "section": "06 40 00.01", "description": "Gypse 1/2", "taux_horaire": 0,
        "lot_id": 10, "qte": 5,
    })
    copie = _call(create_ligne, 290, {
        "section": "06 40 00.01", "description": "Gypse 1/2", "taux_horaire": 0,
        "lot_id": 11, "qte": 0, "a_completer": True,
    })
    assert origine["ligne"]["a_completer"] is False, "la ligne d'origine ne porte jamais le badge"
    assert copie["ligne"]["a_completer"] is True, copie


def test_lot_id_bien_route_a_la_creation():
    """Phase C — le routage manquant que cette phase corrige : une ligne
    créée avec lot_id explicite doit être persistée DANS ce lot, pas
    hors-lot."""
    db = _make_db()
    create_ligne = _get_create_ligne(lambda: _FakeCreateLigneConn(db))
    out = _call(create_ligne, 290, {"section": "06 40 00.01", "taux_horaire": 0, "lot_id": 42})
    assert out["ligne"]["lot_id"] == 42, out


if __name__ == "__main__":
    test_a_completer_absent_par_defaut_false()
    test_a_completer_true_pose_uniquement_sur_la_copie_demandee()
    test_lot_id_bien_route_a_la_creation()
    print("3/3 PASS")
