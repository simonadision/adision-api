"""Garde-fou anti-régression — POST /projets/{id}/lots/{lot_id}/duplicate.

Contexte : bug rapporté en prod (projet 290, CDC, 2026-08-13) — dupliquer un
lot VIDE (0 ligne) aurait fait apparaître une ligne fantôme dans le projet,
non rattachée à aucun des deux lots, dupliquant ~27 000 $ de sous-traitant
d'une ligne existante.

Investigation (code + preuve TXN contre la vraie base prod, cf.
scripts/proof_duplicate_lot_lot_vide_txn.py, et forensique horodatage sur le
projet 290 réel) : le bug NE REPRODUIT PAS sur ce endpoint. La seule ligne
apparue dans la fenêtre du test avait été créée ~90s AVANT le clic
« Dupliquer » (donc par une action distincte), avec une valeur ($7 000) qui
ne correspond pas au delta rapporté (+27 000 $) — et aucune ligne n'a
d'horodatage coïncidant avec la transaction de duplication elle-même.

Ce test FIGE la preuve en garde-fou tracké (contrairement à
scripts/proof_*_txn.py, gitignoré par convention de ce dépôt) : il appelle
le VRAI endpoint (extrait de sa closure, cf. tests/_harness.py) contre une
fausse DB en mémoire qui respecte la sémantique SQL réelle de
`WHERE lot_id = %s` (une comparaison à NULL ne matche jamais rien — c'était
l'hypothèse de bug à vérifier), donc SANS réseau ni Postgres.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import extract_nested  # noqa: E402
import modules.ad_budget_api as B  # noqa: E402


# ── Fausse DB en mémoire : lots + budget_lignes, avec une sémantique SQL
# fidèle sur le point qui importe (filtrage par lot_id, y compris NULL) —
# le FakeCursor générique de _harness.py ignore les params et ne peut donc
# PAS distinguer "0 ligne pour ce lot" de "N lignes pour ce lot".
class _FakeLignesCursor:
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
            # _load_and_authorize_projet : projet permissif (pas de verrou,
            # pas de détenteur) -- l'autorisation est déjà couverte par
            # test_scope_deux_onglets.py / le script de scoping serveur ;
            # ce test-ci porte sur la logique de copie, pas sur le scope.
            self._pending = ("one", {
                "user_id": self._db["owner_id"], "organization_id": None,
                "is_verrouille": False, "detenteur_id": None,
                "detenteur_nom": None, "detenteur_email": None,
                "derniere_activite": None,
            })
            return

        if "select id, nom, nb_logements, ordre from ad_budget.lots where" in s:
            lot_id, projet_id = params
            row = next((l for l in self._db["lots"]
                        if l["id"] == lot_id and l["projet_id"] == projet_id), None)
            self._pending = ("one", dict(row) if row else None)
            return

        if "update ad_budget.lots set ordre = ordre + 1" in s:
            projet_id, ordre = params
            for l in self._db["lots"]:
                if l["projet_id"] == projet_id and l["ordre"] > ordre:
                    l["ordre"] += 1
            self._pending = ("all", [])
            return

        if "insert into ad_budget.lots" in s and "returning" in s:
            projet_id, nom, nb_logements, ordre = params
            self._db["next_lot_id"] += 1
            new_lot = {"id": self._db["next_lot_id"], "projet_id": projet_id,
                       "nom": nom, "nb_logements": nb_logements, "ordre": ordre}
            self._db["lots"].append(new_lot)
            self._pending = ("one", dict(new_lot))
            return

        if "insert into ad_budget.budget_lignes" in s and "select" in s:
            # Params de duplicate_lot : (projet_id, new_lot_id, projet_id, src_lot_id)
            projet_id, new_lot_id, projet_id2, src_lot_id = params
            # SÉMANTIQUE SQL RÉELLE : `lot_id = NULL` ne matche JAMAIS rien
            # (c'était l'hypothèse de bug à vérifier : une comparaison qui
            # dégénère et ramasse les lignes hors-lot ou d'un autre lot).
            if src_lot_id is None:
                matches = []
            else:
                matches = [l for l in self._db["lignes"]
                           if l["projet_id"] == projet_id2 and l["lot_id"] == src_lot_id]
            copies = []
            for l in matches:
                self._db["next_ligne_id"] += 1
                c = dict(l)
                c["id"] = self._db["next_ligne_id"]
                c["projet_id"] = projet_id
                c["lot_id"] = new_lot_id
                copies.append(c)
            self._db["lignes"].extend(copies)
            self.rowcount = len(copies)
            self._pending = ("all", [])
            return

        # Défaut (ex. UPDATE ad_budget.projets de _mark_budget_dirty_if_emitted) :
        # no-op inoffensif, comme le FakeCursor générique de _harness.py.
        self._pending = ("all", [])

    def fetchone(self):
        return self._pending[1] if self._pending[0] == "one" else None

    def fetchall(self):
        return self._pending[1] if self._pending[0] == "all" else []

    def close(self):
        pass


class _FakeLignesConn:
    def __init__(self, db):
        self._db = db

    def cursor(self, *a, **k):
        return _FakeLignesCursor(self._db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _make_db(lots, lignes, owner_id=1):
    next_lot_id = max([l["id"] for l in lots], default=0)
    next_ligne_id = max([l["id"] for l in lignes], default=0)
    return {"lots": lots, "lignes": lignes, "owner_id": owner_id,
            "next_lot_id": next_lot_id, "next_ligne_id": next_ligne_id}


def _ligne(id, projet_id, lot_id, **over):
    base = {"id": id, "projet_id": projet_id, "lot_id": lot_id,
            "source_item_id": None, "section": "06 40 00.01", "description": "Ligne",
            "unite": "un", "prix_unitaire": 0, "qte": 1, "ajustement_pct": 0, "note": "",
            "actif": True, "source_file": None, "type_source": None,
            "heures": 0, "taux_horaire": 0, "cout_sous_traitant": 0, "sous_traitant_nom": "",
            "ajust_materiaux": 0, "ajust_main_oeuvre": 0, "ajust_sous_traitant": 0,
            "sous_traitant_type": None, "sous_traitant_montant": 0,
            "source_viu_analysis_id": None, "source_viu_item_id": None, "item_id_ad_mat": None,
            "ad_hub_pending_id": None, "sous_traitant_contact_id": None,
            "source_typ_code": None, "source_typ_snapshot_at": None,
            "prix_unitaire_override": False, "heures_manuelles": False, "item_ad_mat_scope": None,
            "source_mat_prix_snapshot": None, "source_mat_snapshot_at": None,
            "qte_override": False, "taux_horaire_override": False,
            "ajust_materiaux_override": False, "ajust_main_oeuvre_override": False,
            "ajust_sous_traitant_override": False, "sous_traitant_montant_override": False,
            "prix_unitaire_st": 0, "prix_unitaire_st_override": False}
    base.update(over)
    return base


def _get_duplicate_lot(get_conn):
    return extract_nested(B.register_ad_budget_routes, get_conn, "duplicate_lot")


_USER = {"id": 1, "platform_role": "super_admin", "org_role": "admin",
         "organization_id": None, "nom": "Test"}


def _call(duplicate_lot, projet_id, lot_id):
    with patch.object(B, "_push_budget_snapshot", lambda *a, **k: None):
        return duplicate_lot(projet_id, lot_id, data=None, user=_USER,
                              authorization=None, session_cookie=None)


def test_lot_vide_ne_copie_aucune_ligne_meme_avec_des_lignes_hors_lot_et_dans_un_autre_lot():
    """LE cas rapporté en prod : dupliquer un lot à 0 ligne, dans un projet
    qui a par ailleurs des lignes hors-lot ET des lignes dans un autre lot
    (les deux pièges où une jointure/filtre mal borné pourrait aller piocher
    au lieu de ne rien copier)."""
    lots = [
        {"id": 10, "projet_id": 290, "nom": "LOT-AVEC-LIGNES", "nb_logements": 2, "ordre": 0},
        {"id": 20, "projet_id": 290, "nom": "LOT-VIDE", "nb_logements": 3, "ordre": 1},
    ]
    lignes = [
        _ligne(1, 290, None, description="HORS-LOT TEMOIN", sous_traitant_montant=27000),
        _ligne(2, 290, 10, description="DANS LOT-AVEC-LIGNES"),
    ]
    db = _make_db(lots, lignes)
    duplicate_lot = _get_duplicate_lot(lambda: _FakeLignesConn(db))

    out = _call(duplicate_lot, 290, 20)

    assert out["nb_lignes_copiees"] == 0, out
    assert len(db["lignes"]) == 2, "aucune ligne fantôme ne doit apparaître"
    assert db["lignes"][0]["lot_id"] is None, "la ligne hors-lot témoin ne doit pas bouger"
    assert sum(1 for l in db["lignes"] if l["lot_id"] == 10) == 1, "le lot voisin non vide n'est pas siphonné"
    assert sum(1 for l in db["lignes"] if l["lot_id"] == out["lot"]["id"]) == 0


def test_lot_non_vide_copie_bien_ses_lignes_independamment():
    """Non-régression : un lot avec des lignes continue de les copier (copie
    profonde indépendante — cf. tests/test_lots_calc.py::test_08)."""
    lots = [{"id": 10, "projet_id": 290, "nom": "AB DEL", "nb_logements": 12, "ordre": 0}]
    lignes = [
        _ligne(1, 290, 10, description="A", prix_unitaire=100, qte=10),
        _ligne(2, 290, 10, description="B", prix_unitaire=20, qte=5),
    ]
    db = _make_db(lots, lignes)
    duplicate_lot = _get_duplicate_lot(lambda: _FakeLignesConn(db))

    out = _call(duplicate_lot, 290, 10)

    assert out["nb_lignes_copiees"] == 2, out
    copies = [l for l in db["lignes"] if l["lot_id"] == out["lot"]["id"]]
    assert len(copies) == 2
    assert {l["description"] for l in copies} == {"A", "B"}
    # Indépendance : modifier la copie ne doit rien changer à la source.
    copies[0]["prix_unitaire"] = 999
    source = [l for l in db["lignes"] if l["lot_id"] == 10]
    assert {l["prix_unitaire"] for l in source} == {100, 20}, "la source a bougé -- pas indépendant"


if __name__ == "__main__":
    test_lot_vide_ne_copie_aucune_ligne_meme_avec_des_lignes_hors_lot_et_dans_un_autre_lot()
    test_lot_non_vide_copie_bien_ses_lignes_independamment()
    print("2/2 PASS")
