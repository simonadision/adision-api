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

        if "select id, ordre from ad_budget.budget_lignes" in s:
            projet_id, section = params
            voisines = [l for l in self._db["lignes"]
                        if l["projet_id"] == projet_id and l["section"] == section]
            voisines.sort(key=lambda l: (l["ordre"], l["id"]))
            self._pending = ("all", [{"id": l["id"], "ordre": l["ordre"]} for l in voisines])
            return

        if "update ad_budget.budget_lignes set ordre" in s:
            ordre, lid, projet_id = params
            for l in self._db["lignes"]:
                if l["id"] == lid and l["projet_id"] == projet_id:
                    l["ordre"] = ordre
            self._pending = ("all", [])
            return

        if "select 1 from ad_budget.budget_lignes where id = %s and projet_id = %s" in s:
            # Copie « avec les quantités » : l'origine doit être du même projet.
            lid, projet_id = params
            existe = any(l["id"] == lid and l["projet_id"] == projet_id for l in self._db["lignes"])
            self._pending = ("one", {"?column?": 1} if existe else None)
            return

        if "insert into ad_budget.budget_lignes" in s and "returning" in s:
            (projet_id, source_item_id, section, description, unite, prix_unitaire,
             qte, ajustement_pct, note, actif, item_id_ad_mat, ad_hub_pending_id,
             taux_horaire, lot_id, a_completer,
             heures, heures_manuelles, production_valeur, production_unite, ordre,
             quantites_liees_a) = params
            self._db["next_id"] += 1
            row = {
                "id": self._db["next_id"], "projet_id": projet_id,
                "source_item_id": source_item_id, "section": section,
                "description": description, "unite": unite, "prix_unitaire": prix_unitaire,
                "qte": qte, "ajustement_pct": ajustement_pct, "note": note, "actif": actif,
                "item_id_ad_mat": item_id_ad_mat, "ad_hub_pending_id": ad_hub_pending_id,
                "taux_horaire": taux_horaire, "lot_id": lot_id, "a_completer": a_completer,
                "heures": heures, "heures_manuelles": heures_manuelles,
                "production_valeur": production_valeur, "production_unite": production_unite,
                "ordre": ordre, "quantites_liees_a": quantites_liees_a,
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


def test_production_et_heures_persistees_a_la_creation():
    """Brief pont 2026-08-18 — modale « Nouvelle ligne » : Production /
    Unité de production / Heures saisis à la création ne doivent plus être
    perdus (avant ce correctif, seul le PUT ultérieur les persistait —
    l'INSERT ne portait pas ces colonnes)."""
    db = _make_db()
    create_ligne = _get_create_ligne(lambda: _FakeCreateLigneConn(db))
    out = _call(create_ligne, 290, {
        "section": "06 40 00.01", "taux_horaire": 0, "lot_id": 10,
        "heures": 2.5, "heures_manuelles": True,
        "production_valeur": 128, "production_unite": "pi2/h",
    })
    assert out["ligne"]["heures"] == 2.5, out
    assert out["ligne"]["heures_manuelles"] is True, out
    assert out["ligne"]["production_valeur"] == 128, out
    assert out["ligne"]["production_unite"] == "pi2/h", out


def test_production_et_heures_absents_par_defaut():
    """Non-régression : une création sans ces champs (comportement historique)
    ne pose ni production_valeur ni heures_manuelles."""
    db = _make_db()
    create_ligne = _get_create_ligne(lambda: _FakeCreateLigneConn(db))
    out = _call(create_ligne, 290, {"section": "06 40 00.01", "taux_horaire": 0})
    assert out["ligne"]["heures"] == 0, out
    assert out["ligne"]["heures_manuelles"] is False, out
    assert out["ligne"]["production_valeur"] is None, out
    assert out["ligne"]["production_unite"] is None, out


if __name__ == "__main__":
    test_a_completer_absent_par_defaut_false()
    test_a_completer_true_pose_uniquement_sur_la_copie_demandee()
    test_lot_id_bien_route_a_la_creation()
    test_production_et_heures_persistees_a_la_creation()
    test_production_et_heures_absents_par_defaut()
    print("5/5 PASS")


# ── RANG PERSISTÉ (16 sept 2026) ─────────────────────────────────────────
# Simon : « pourquoi au refresh l'ordre de mes items change, ca doit
# persister ». « + Ligne » posait la ligne sous sa référence à l'écran, mais
# l'INSERT la laissait à ordre = 0 : au rechargement (ORDER BY section, ordre,
# id) elle remontait en tête de section.

def _ordre_lu(db, section):
    """Ordre de lecture GET /lignes pour une section : (ordre, id)."""
    lignes = [l for l in db["lignes"] if l["section"] == section]
    return [l["description"] for l in sorted(lignes, key=lambda l: (l["ordre"], l["id"]))]


def test_plus_ligne_persiste_sous_sa_reference():
    db = _make_db()
    create = _get_create_ligne(lambda: _FakeCreateLigneConn(db))
    a = _call(create, 1, {"section": "06 00 00", "description": "A"})["ligne"]
    b = _call(create, 1, {"section": "06 00 00", "description": "B"})["ligne"]
    _call(create, 1, {"section": "06 00 00", "description": "C"})
    # Sous A, puis sous B : l'écran montre A, A2, B, B2, C.
    r = _call(create, 1, {"section": "06 00 00", "description": "A2", "apres_ligne_id": a["id"]})
    _call(create, 1, {"section": "06 00 00", "description": "B2", "apres_ligne_id": b["id"]})
    assert _ordre_lu(db, "06 00 00") == ["A", "A2", "B", "B2", "C"]
    # Les voisines renumérotées sont renvoyées au client.
    assert {"id": b["id"], "ordre": 30} in r["ordres"]


def test_sans_reference_la_ligne_va_en_fin_de_section():
    db = _make_db()
    create = _get_create_ligne(lambda: _FakeCreateLigneConn(db))
    for d in ("A", "B"):
        _call(create, 1, {"section": "06 00 00", "description": d})
    _call(create, 1, {"section": "09 00 00", "description": "autre section"})
    r = _call(create, 1, {"section": "06 00 00", "description": "Z"})
    assert _ordre_lu(db, "06 00 00") == ["A", "B", "Z"]
    assert r["ordres"] == []


def test_lignes_heritees_a_ordre_zero_restent_dans_l_ordre_affiche():
    # Projet 324 : six lignes déjà créées à ordre 0 (affichées par id), puis
    # des lignes importées à 10, 30… Insérer sous la 2e doit donner l'ordre
    # affiché, pas remonter ni descendre les autres.
    db = _make_db()
    db["lignes"] = [
        {"id": 5, "projet_id": 1, "section": "06 00 00", "description": "Import 10", "ordre": 10},
        {"id": 7, "projet_id": 1, "section": "06 00 00", "description": "Neuve 1", "ordre": 0},
        {"id": 8, "projet_id": 1, "section": "06 00 00", "description": "Neuve 2", "ordre": 0},
    ]
    db["next_id"] = 8
    create = _get_create_ligne(lambda: _FakeCreateLigneConn(db))
    _call(create, 1, {"section": "06 00 00", "description": "Sous neuve 1", "apres_ligne_id": 7})
    assert _ordre_lu(db, "06 00 00") == ["Neuve 1", "Sous neuve 1", "Neuve 2", "Import 10"]


def test_plan_ordre_insertion_reference_inconnue_ajoute_a_la_fin():
    assert B._plan_ordre_insertion([(1, 10), (2, 20)], 99) == (30, [])
    assert B._plan_ordre_insertion([], 3) == (10, [])


# ── QUANTITÉS LIÉES (18 sept 2026) ────────────────────────────────────────
# Simon : « copier l'item aussi dans le lot Rimini avec les quantités… copié
# mais sans les quantités » -- il les tape dans le tableau après la création.
# La copie naît liée à son origine ; le lien n'est posé que vers une ligne du
# MÊME projet.

def test_copie_avec_quantites_nait_liee_a_son_origine():
    db = _make_db()
    create = _get_create_ligne(lambda: _FakeCreateLigneConn(db))
    origine = _call(create, 7, {"section": "01 00 00", "description": "Frais de transport",
                                "taux_horaire": 50, "lot_id": 51})["ligne"]
    copie = _call(create, 7, {"section": "01 00 00", "description": "Frais de transport",
                              "taux_horaire": 50, "lot_id": 52,
                              "quantites_liees_a": origine["id"]})["ligne"]
    assert copie["quantites_liees_a"] == origine["id"]
    assert origine["quantites_liees_a"] is None


def test_lien_refuse_vers_une_ligne_d_un_autre_projet():
    db = _make_db()
    create = _get_create_ligne(lambda: _FakeCreateLigneConn(db))
    ailleurs = _call(create, 99, {"section": "01 00 00", "taux_horaire": 50})["ligne"]
    copie = _call(create, 7, {"section": "01 00 00", "taux_horaire": 50,
                              "quantites_liees_a": ailleurs["id"]})["ligne"]
    assert copie["quantites_liees_a"] is None


def test_sans_lien_demande_aucun_lien():
    db = _make_db()
    create = _get_create_ligne(lambda: _FakeCreateLigneConn(db))
    ligne = _call(create, 7, {"section": "01 00 00", "taux_horaire": 50})["ligne"]
    assert ligne["quantites_liees_a"] is None
