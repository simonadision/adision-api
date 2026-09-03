"""Garde-fou anti-régression — duplication des LOTS lors d'une copie de
budget (POST /projets/{id}/dupliquer et POST /projets/{id}/reviser-projet).

Simon, en direct, 3 sept 2026, capture à l'appui (projet "Rénovation
intérieure d'unités de logements", 9 lots nommés — AB DEL, CD DEL,
MODÈLE 231-29 GARCEAU, MODÈLE/STYLE A/B/C-C/C-S/CC-S/H) : "j'ai dupliquer
mon projet. La copie n'est pas exact. Je ne vois pas mes lots dans la
copie dupliquée." Les MONTANTS de la copie étaient déjà exacts (aucune
ligne perdue, aucun total faux) — le défaut était structurel : les deux
endpoints qui copient un budget excluaient délibérément `lot_id` de la
copie de `budget_lignes`, sans jamais dupliquer les LOTS eux-mêmes ni
réassigner chaque ligne à son nouveau lot.

Ce test couvre directement `_dupliquer_lots_et_lignes` (fonction PARTAGÉE
par les deux endpoints, extraite précisément pour qu'un correctif futur ne
puisse plus diverger entre les deux copies-collées comme c'est déjà
arrivé une fois aujourd'hui pour les montants sous-traitant).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import modules.ad_budget_api as B  # noqa: E402


# ── Fausse DB en mémoire : ad_budget.lots + ad_budget.budget_lignes, avec
# une sémantique SQL fidèle sur ce qui importe ici -- la correspondance
# ancien_lot_id -> nouveau_lot_id doit être exacte, pas approximative.
class _FakeDupCursor:
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

        # 1. Lecture des lots de la source, dans l'ordre d'affichage.
        if s.startswith("select id, nom, nb_logements, ordre from ad_budget.lots"):
            (projet_id_source,) = params
            trouves = sorted(
                (l for l in self._db["lots"] if l["projet_id"] == projet_id_source),
                key=lambda l: (l["ordre"], l["id"]),
            )
            self._pending = ("all", [dict(l) for l in trouves])
            return

        # 2. Un lot à la fois, RETURNING id -- construit la correspondance.
        if s.startswith("insert into ad_budget.lots"):
            new_id, nom, nb_logements, ordre = params
            self._db["_next_lot_id"] += 1
            nid = self._db["_next_lot_id"]
            self._db["lots"].append({
                "id": nid, "projet_id": new_id, "nom": nom,
                "nb_logements": nb_logements, "ordre": ordre,
            })
            self._pending = ("one", {"id": nid})
            return

        # 3. Copie de budget_lignes -- reproduit CHAQUE ligne de la source
        #    telle quelle, sauf projet_id (nouveau) et lot_id (remappé via
        #    la correspondance encodée dans le CASE -- extraite ici de
        #    `params` plutôt que ré-analysée depuis le texte SQL).
        if s.startswith("insert into ad_budget.budget_lignes"):
            new_id = params[0]
            projet_id_source = params[-1]
            paires = params[1:-1]
            lot_map = {paires[i]: paires[i + 1] for i in range(0, len(paires), 2)}
            copiees = 0
            for l in self._db["lignes"]:
                if l["projet_id"] != projet_id_source:
                    continue
                self._db["_next_ligne_id"] += 1
                nouvelle = dict(l)
                nouvelle["id"] = self._db["_next_ligne_id"]
                nouvelle["projet_id"] = new_id
                nouvelle["lot_id"] = lot_map.get(l.get("lot_id"))
                self._db["lignes"].append(nouvelle)
                copiees += 1
            self.rowcount = copiees
            self._pending = ("all", [])
            return

        self._pending = ("all", [])

    def fetchone(self):
        return self._pending[1] if self._pending[0] == "one" else None

    def fetchall(self):
        return self._pending[1] if self._pending[0] == "all" else []

    def close(self):
        pass


def _make_db(lots, lignes):
    return {
        "lots": [dict(l) for l in lots],
        "lignes": [dict(l) for l in lignes],
        "_next_lot_id": max([l["id"] for l in lots], default=0),
        "_next_ligne_id": max([l["id"] for l in lignes], default=0),
    }


def _lot(id, projet_id, nom, nb_logements=None, ordre=0):
    return {"id": id, "projet_id": projet_id, "nom": nom,
            "nb_logements": nb_logements, "ordre": ordre}


def _ligne(id, projet_id, lot_id, description):
    return {"id": id, "projet_id": projet_id, "lot_id": lot_id,
            "description": description, "source_item_id": None,
            "section": "01 00 00", "unite": "pi2", "prix_unitaire": 1.0,
            "qte": 1.0}


def test_lots_dupliques_avec_correspondance_exacte():
    """9 lots nommés dans la source (cas réel de Simon, réduit à 2 pour la
    lisibilité du test) -> la copie doit porter le MÊME nombre de lots,
    les MÊMES noms, dans le MÊME ordre -- pas un lot renommé ou permuté."""
    db = _make_db(
        lots=[
            _lot(11, 100, "AB DEL", nb_logements=4, ordre=0),
            _lot(12, 100, "CD DEL", nb_logements=6, ordre=1),
        ],
        lignes=[],
    )
    cur = _FakeDupCursor(db)
    B._dupliquer_lots_et_lignes(cur, 100, 200)

    lots_copie = [l for l in db["lots"] if l["projet_id"] == 200]
    assert len(lots_copie) == 2
    lots_copie.sort(key=lambda l: l["ordre"])
    assert lots_copie[0]["nom"] == "AB DEL"
    assert lots_copie[0]["nb_logements"] == 4
    assert lots_copie[1]["nom"] == "CD DEL"
    assert lots_copie[1]["nb_logements"] == 6
    # Les lots de la SOURCE n'ont pas bougé.
    assert len([l for l in db["lots"] if l["projet_id"] == 100]) == 2


def test_lignes_reassignees_au_bon_nouveau_lot():
    """Une ligne de la source dans AB DEL doit se retrouver dans le AB DEL
    de la COPIE -- jamais dans CD DEL, jamais hors-lot. C'est le coeur du
    bug rapporté : avant ce correctif, TOUTES les lignes tombaient en
    Hors Lot, peu importe leur lot d'origine."""
    db = _make_db(
        lots=[
            _lot(11, 100, "AB DEL", ordre=0),
            _lot(12, 100, "CD DEL", ordre=1),
        ],
        lignes=[
            _ligne(1, 100, lot_id=11, description="Trait de scie AB"),
            _ligne(2, 100, lot_id=12, description="Béton dalle CD"),
            _ligne(3, 100, lot_id=None, description="Ligne hors lot"),
        ],
    )
    cur = _FakeDupCursor(db)
    nb = B._dupliquer_lots_et_lignes(cur, 100, 200)

    assert nb == 3
    lignes_copie = {l["description"]: l for l in db["lignes"] if l["projet_id"] == 200}
    assert set(lignes_copie) == {"Trait de scie AB", "Béton dalle CD", "Ligne hors lot"}

    nouveau_ab_del = next(l for l in db["lots"] if l["projet_id"] == 200 and l["nom"] == "AB DEL")
    nouveau_cd_del = next(l for l in db["lots"] if l["projet_id"] == 200 and l["nom"] == "CD DEL")

    assert lignes_copie["Trait de scie AB"]["lot_id"] == nouveau_ab_del["id"]
    assert lignes_copie["Béton dalle CD"]["lot_id"] == nouveau_cd_del["id"]
    assert lignes_copie["Ligne hors lot"]["lot_id"] is None, (
        "une ligne hors-lot dans la source doit rester hors-lot dans la copie"
    )
    # Éprouvé à l'envers implicitement : si le remap échouait silencieusement
    # (retombait toujours sur None), les deux premières assertions ci-dessus
    # rougiraient -- ce ne sont pas des tautologies.
    assert lignes_copie["Trait de scie AB"]["lot_id"] != lignes_copie["Béton dalle CD"]["lot_id"]


def test_projet_sans_lot_reste_tout_hors_lot():
    """Un projet SANS lot (comportement d'avant le concept "Lot") continue
    de fonctionner exactement pareil -- aucune régression sur le cas
    majoritaire."""
    db = _make_db(
        lots=[],
        lignes=[_ligne(1, 100, lot_id=None, description="Seule ligne")],
    )
    cur = _FakeDupCursor(db)
    nb = B._dupliquer_lots_et_lignes(cur, 100, 200)

    assert nb == 1
    assert [l for l in db["lots"] if l["projet_id"] == 200] == []
    copiee = next(l for l in db["lignes"] if l["projet_id"] == 200)
    assert copiee["lot_id"] is None


def test_lot_id_source_ne_fuite_jamais_tel_quel_vers_un_autre_projet():
    """Éprouvé à l'envers du bug d'origine : un id de lot de la SOURCE (11)
    ne doit JAMAIS se retrouver tel quel comme lot_id d'une ligne copiée --
    il pointerait vers le lot d'un AUTRE projet (celui de la source),
    exactement le risque que l'exclusion pure évitait avant ce correctif,
    et que le remap doit éviter aussi."""
    db = _make_db(
        lots=[_lot(11, 100, "AB DEL", ordre=0)],
        lignes=[_ligne(1, 100, lot_id=11, description="Trait de scie AB")],
    )
    cur = _FakeDupCursor(db)
    B._dupliquer_lots_et_lignes(cur, 100, 200)

    copiee = next(l for l in db["lignes"] if l["projet_id"] == 200)
    assert copiee["lot_id"] != 11
    nouveau_lot = next(l for l in db["lots"] if l["projet_id"] == 200)
    assert copiee["lot_id"] == nouveau_lot["id"]
