# -*- coding: utf-8 -*-
"""TEMOIN -- la photo Ad ANA se fige au CLASSEMENT (1er oct 2026).

Simon : « geste des pastilles ». Depuis le classement unique du 16 sept, le
geste reel ecrit le classement au hub et ne touche plus le statut local d'Ad
BUD ; la photo ne se prenait que sur ce statut local -> Ad ANA s'assechait.

Sans base : un faux curseur rejoue ad_budget.projets et app_ana.project_snapshots,
et _create_snapshot est remplace par une doublure qui note ce qu'on lui passe.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from modules import ad_budget_api as A  # noqa: E402

SRC_API = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api.py")


class FauxCurseur:
    def __init__(self, budgets, derniers_motifs):
        self.budgets = budgets                  # liste de dicts ad_budget.projets
        self.derniers = dict(derniers_motifs)   # projet_id -> trigger_event is_latest
        self._res = []
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        if "FROM ad_budget.projets" in sql and "FOR UPDATE" in sql:
            self._res = [b for b in self.budgets if b["ad_hub_project_id"] == params[0]]
        elif "SELECT trigger_event" in sql:
            m = self.derniers.get(params[0])
            self._res = [{"trigger_event": m}] if m else []
        elif sql.startswith("UPDATE app_ana.project_snapshots SET is_latest = FALSE"):
            ids = [b["id"] for b in self.budgets if b["ad_hub_project_id"] == params[0]]
            self._res = [{"id": i} for i in ids if i in self.derniers]
            for i in ids:
                self.derniers.pop(i, None)
        else:
            self._res = []

    def fetchall(self):
        return self._res

    def fetchone(self):
        return self._res[0] if self._res else None


def _doublure(cur):
    appels = []

    def fake(c, row, motif, ident, statut_fige=None):
        appels.append({"projet": row["id"], "motif": motif, "statut": statut_fige})
        cur.derniers[row["id"]] = motif
        return 1000 + len(appels)
    return appels, fake


def _budget(i, hub=790, statut="en_soumission"):
    return {"id": i, "ad_hub_project_id": hub, "statut": statut}


def _avec(cur, fn):
    appels, fake = _doublure(cur)
    orig = A._create_snapshot
    A._create_snapshot = fake
    try:
        return fn(), appels
    finally:
        A._create_snapshot = orig


def test_les_classements_sont_nommes():
    # Un 5e classement doit poser la question ici, pas passer en silence.
    assert A.CLASSEMENTS_CONNUS == ("soumission", "obtenu", "perdu", "ferme")
    assert A.CLASSEMENTS_QUI_FIGENT == ("obtenu", "perdu")
    assert A.CLASSEMENTS_QUI_RETIRENT == ("soumission",)
    # « ferme » : reconnu, INACTIF tant que Simon n'a pas dit s'il vaut gagne.
    assert "ferme" not in A.CLASSEMENTS_QUI_FIGENT


def test_prediction_soumission_vers_obtenu_puis_rejeu():
    cur = FauxCurseur([_budget(324)], {})
    res, appels = _avec(cur, lambda: A.figer_photos_au_classement(
        cur, 790, "obtenu", {"name": "Huttes"}, "en_cours"))
    assert res["creees"] == 1 and res["ignorees"] == 0
    assert appels == [{"projet": 324, "motif": "classement_vers_obtenu", "statut": "en_cours"}]
    # Rejeu du meme geste : toujours UNE photo.
    res2, appels2 = _avec(cur, lambda: A.figer_photos_au_classement(
        cur, 790, "obtenu", {}, "en_cours"))
    assert res2["creees"] == 0 and res2["ignorees"] == 1 and appels2 == []


def test_la_photo_porte_le_statut_du_hub_pas_le_statut_local():
    # Piege principal : le statut local reste en_soumission (la pastille ne
    # le change jamais). La photo doit porter en_cours, sinon Ad ANA la
    # compte non gagnee.
    cur = FauxCurseur([_budget(1, statut="en_soumission")], {})
    _, appels = _avec(cur, lambda: A.figer_photos_au_classement(cur, 790, "obtenu", {}, "en_cours"))
    assert appels[0]["statut"] == "en_cours"


def test_statut_hub_absent_ou_invalide_repli_sur_la_table_unique():
    for statut_hub in (None, "", "obtenu", "n_importe_quoi"):
        cur = FauxCurseur([_budget(1)], {})
        _, appels = _avec(cur, lambda: A.figer_photos_au_classement(cur, 790, "perdu", {}, statut_hub))
        assert appels[0]["statut"] == A._CLASSEMENT_HUB_VERS_CATEGORIE["perdu"] == "perdu"


def test_ferme_est_inactif():
    cur = FauxCurseur([_budget(1)], {})
    res, appels = _avec(cur, lambda: A.figer_photos_au_classement(cur, 790, "ferme", {}, "archive"))
    assert res.get("inactif") is True and appels == []


def test_retour_en_soumission_retire_la_photo_sans_en_creer():
    cur = FauxCurseur([_budget(1)], {1: "classement_vers_obtenu"})
    res, appels = _avec(cur, lambda: A.figer_photos_au_classement(cur, 790, "soumission", {}, "en_soumission"))
    assert res["retirees"] == 1 and appels == []
    # ... puis reclasser obtenu refige (plus de derniere photo a ignorer).
    res2, appels2 = _avec(cur, lambda: A.figer_photos_au_classement(cur, 790, "obtenu", {}, "en_cours"))
    assert res2["creees"] == 1


def test_obtenu_perdu_obtenu_fait_trois_photos():
    cur = FauxCurseur([_budget(1)], {})
    total = 0
    for c, st in (("obtenu", "en_cours"), ("perdu", "perdu"), ("obtenu", "en_cours")):
        res, _ = _avec(cur, lambda c=c, st=st: A.figer_photos_au_classement(cur, 790, c, {}, st))
        total += res["creees"]
    assert total == 3


def test_seuls_les_budgets_du_projet_hub_sont_figes():
    cur = FauxCurseur([_budget(1, hub=790), _budget(2, hub=791)], {})
    _, appels = _avec(cur, lambda: A.figer_photos_au_classement(cur, 790, "obtenu", {}, "en_cours"))
    assert [a["projet"] for a in appels] == [1]
    assert any("statut <> 'archive'" in q and "FOR UPDATE" in q for q in cur.sql)


def test_classement_inconnu_leve():
    cur = FauxCurseur([], {})
    try:
        A.figer_photos_au_classement(cur, 790, "fermé", {}, None)
    except ValueError:
        return
    raise AssertionError("un classement inconnu doit lever")


def test_create_snapshot_ecrit_le_statut_fige():
    src = open(os.path.join(os.path.dirname(SRC_API), "modules", "ad_budget_api.py"), encoding="utf-8").read()
    corps = src.split("def _create_snapshot(", 1)[1].split(chr(10) + "def ", 1)[0]
    assert 'statut_fige or projet_row["statut"]' in corps, (
        "la photo ecrirait le statut LOCAL : un projet obtenu au hub serait fige en_soumission")


def test_route_interne_protegee_par_le_secret_de_service():
    src = open(SRC_API, encoding="utf-8").read()
    bloc = src.split('@app.post("/internal/budget-figer-photo/{hub_id}")', 1)[1].split("@app.", 1)[0]
    assert "secrets.compare_digest(x_internal_secret, expected)" in bloc
    assert "figer_photos_au_classement" in bloc
    assert "conn.commit()" in bloc


if __name__ == "__main__":
    echecs = 0
    for nom, fn in sorted(globals().items()):
        if nom.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", nom)
            except Exception as e:
                echecs += 1
                print("FAIL", nom, ":", repr(e))
    print("---", "0 echec" if not echecs else "%d echec(s)" % echecs)
    sys.exit(1 if echecs else 0)
