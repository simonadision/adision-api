"""Garde-fou — POST /projets/{id}/lots/from-hors-lot (Phase A, workflow
avancé Lot).

Couvre le geste central de la Phase A : convertir « Hors lot » en premier
lot réel doit réassigner TOUTES les lignes hors-lot EN UNE SEULE REQUÊTE
(UPDATE en masse, pas ligne par ligne côté Python) -- et ne JAMAIS toucher
aux lignes déjà dans un autre lot. Même style de fausse DB en mémoire que
tests/test_duplicate_lot_endpoint.py (sémantique SQL fidèle sur les
comparaisons à NULL, seul point qui compte ici).
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import extract_nested  # noqa: E402
import modules.ad_budget_api as B  # noqa: E402


class _FakeConvertCursor:
    def __init__(self, db):
        self._db = db
        self._pending = ("all", [])
        self.rowcount = 0

    @staticmethod
    def _norm(sql):
        return " ".join(str(sql).split()).lower()

    def execute(self, sql, params=None):
        s = self._norm(sql)
        params = params or ()

        if "select user_id, organization_id, is_verrouille" in s:
            # Autorisation déjà couverte ailleurs (scoping) -- projet permissif ici.
            self._pending = ("one", {
                "user_id": self._db["owner_id"], "organization_id": None,
                "is_verrouille": False, "detenteur_id": None,
                "detenteur_nom": None, "detenteur_email": None,
                "derniere_activite": None,
            })
            return

        if "select coalesce(min(ordre), 0) - 1 as n from ad_budget.lots" in s:
            (projet_id,) = params
            ordres = [l["ordre"] for l in self._db["lots"] if l["projet_id"] == projet_id]
            n = (min(ordres) if ordres else 0) - 1
            self._pending = ("one", {"n": n})
            return

        if "insert into ad_budget.lots" in s and "returning" in s:
            projet_id, nom, nb_logements, ordre = params
            self._db["next_lot_id"] += 1
            new_lot = {"id": self._db["next_lot_id"], "projet_id": projet_id,
                       "nom": nom, "nb_logements": nb_logements, "ordre": ordre}
            self._db["lots"].append(new_lot)
            self._pending = ("one", dict(new_lot))
            return

        if "update ad_budget.budget_lignes set lot_id = %s" in s:
            # WHERE projet_id = %s AND lot_id IS NULL -- SÉMANTIQUE SQL RÉELLE :
            # ne doit matcher QUE les lignes hors-lot, jamais celles d'un autre lot.
            new_lot_id, projet_id = params
            touched = 0
            for l in self._db["lignes"]:
                if l["projet_id"] == projet_id and l["lot_id"] is None:
                    l["lot_id"] = new_lot_id
                    touched += 1
            self.rowcount = touched
            self._pending = ("all", [])
            return

        self._pending = ("all", [])

    def fetchone(self):
        return self._pending[1] if self._pending[0] == "one" else None

    def fetchall(self):
        return self._pending[1] if self._pending[0] == "all" else []

    def close(self):
        pass


class _FakeConvertConn:
    def __init__(self, db):
        self._db = db

    def cursor(self, *a, **k):
        return _FakeConvertCursor(self._db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _make_db(lots, lignes, owner_id=1):
    next_lot_id = max([l["id"] for l in lots], default=0)
    return {"lots": lots, "lignes": lignes, "owner_id": owner_id, "next_lot_id": next_lot_id}


def _ligne(id, projet_id, lot_id, **over):
    base = {"id": id, "projet_id": projet_id, "lot_id": lot_id, "description": "Ligne"}
    base.update(over)
    return base


def _get_convert(get_conn):
    return extract_nested(B.register_ad_budget_routes, get_conn, "create_lot_from_hors_lot")


_USER = {"id": 1, "platform_role": "super_admin", "org_role": "admin",
         "organization_id": None, "nom": "Test"}


def _call(convert, projet_id, data):
    with patch.object(B, "_push_budget_snapshot", lambda *a, **k: None):
        return convert(projet_id, data, user=_USER)


def test_reassignation_en_masse_touche_seulement_les_lignes_hors_lot():
    """LE geste central de la Phase A : les lignes hors-lot basculent vers le
    nouveau lot, celles déjà dans un autre lot ne bougent PAS."""
    lots = [{"id": 10, "projet_id": 290, "nom": "AUTRE LOT", "nb_logements": 2, "ordre": 0}]
    lignes = [
        _ligne(1, 290, None, description="Hors-lot A"),
        _ligne(2, 290, None, description="Hors-lot B"),
        _ligne(3, 290, 10, description="Déjà dans AUTRE LOT"),
    ]
    db = _make_db(lots, lignes)
    convert = _get_convert(lambda: _FakeConvertConn(db))

    out = _call(convert, 290, {"nom": "AB DEL"})

    assert out["status"] == "created"
    assert out["nb_lignes_reassignees"] == 2, out
    nouveau_lot_id = out["lot"]["id"]
    assert nouveau_lot_id != 10
    assert db["lignes"][0]["lot_id"] == nouveau_lot_id
    assert db["lignes"][1]["lot_id"] == nouveau_lot_id
    assert db["lignes"][2]["lot_id"] == 10, "la ligne déjà dans un autre lot ne doit pas bouger"


def test_aucune_ligne_hors_lot_conversion_quand_meme_possible():
    """« Hors lot » vide au moment de la conversion (déjà tout assigné) --
    le lot se crée quand même, juste 0 ligne reclassée (idempotent, jamais
    une erreur)."""
    lots = []
    lignes = [_ligne(1, 290, 5, description="Déjà ailleurs")]
    db = _make_db(lots, lignes)
    convert = _get_convert(lambda: _FakeConvertConn(db))

    out = _call(convert, 290, {"nom": "Premier lot"})

    assert out["nb_lignes_reassignees"] == 0
    assert db["lignes"][0]["lot_id"] == 5


def test_lot_converti_se_place_toujours_en_premier():
    """ordre = MIN(ordre existant) - 1 : le lot converti passe devant des
    lots créés APRÈS coup, qu'ils existent déjà ou non au moment du clic
    « Renommer » sur Hors lot (brief Simon : Hors lot EST en pratique le
    premier lot réel)."""
    lots = [
        {"id": 10, "projet_id": 290, "nom": "CD DEL", "nb_logements": 2, "ordre": 0},
        {"id": 11, "projet_id": 290, "nom": "CS H", "nb_logements": 3, "ordre": 1},
    ]
    db = _make_db(lots, [])
    convert = _get_convert(lambda: _FakeConvertConn(db))

    out = _call(convert, 290, {"nom": "AB DEL"})

    assert out["lot"]["ordre"] == -1, out["lot"]


def test_nom_vide_rejete_400():
    db = _make_db([], [])
    convert = _get_convert(lambda: _FakeConvertConn(db))
    try:
        _call(convert, 290, {"nom": "   "})
        assert False, "devait lever HTTPException 400"
    except Exception as e:
        assert getattr(e, "status_code", None) == 400, e


if __name__ == "__main__":
    test_reassignation_en_masse_touche_seulement_les_lignes_hors_lot()
    test_aucune_ligne_hors_lot_conversion_quand_meme_possible()
    test_lot_converti_se_place_toujours_en_premier()
    test_nom_vide_rejete_400()
    print("4/4 PASS")
