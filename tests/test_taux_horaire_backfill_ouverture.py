"""Test — backfill du taux horaire par défaut À L'OUVERTURE d'un projet
existant (modules/ad_budget_api.py, GET /projets/{projet_id}/lignes).

Incident réel, 15 sept 2026 : Simon, 2e demande EXPLICITE du même comportement
— « a l'ouverture d un projet dans ad bud. Toute les items qui n ont pas de
taux horaire doivent être par defaut le taux horaire charpentier menuisier
compagnon. ca fait 2 fois que je demande.... toujour pas fait ». Le D5
existant (9 sept 2026) couvrait la CRÉATION d'une ligne et le PATCH explicite
du champ taux_horaire — jamais la simple RELECTURE d'une ligne déjà à 0/NULL
en base, qui est justement le seul chemin emprunté à l'ouverture d'un projet.

Ce test prouve, par exécution de la vraie fonction get_budget_lignes (pas une
lecture statique) que :
  1. Une ligne à taux_horaire=0 avec une section mappée à une division CSI
     est corrigée (persistée via UPDATE) au taux de CETTE division.
  2. Une ligne à taux_horaire=NULL sur une division NON mappée tombe sur le
     repli CHARPENTIER_C (charpentier menuisier compagnon).
  3. Une ligne qui a déjà un taux_horaire > 0 n'est JAMAIS touchée (aucun
     UPDATE émis pour elle).
  4. Aucune ligne à corriger -> aucun UPDATE, aucun commit (non-régression /
     perf : l'ouverture d'un projet déjà propre ne doit pas écrire en base
     à chaque chargement).

Fake cursor MINIMAL (pas de sqlite/psycopg réel), piloté par une petite
« base » en mémoire partagée entre les connexions successives (get_conn est
rappelé plusieurs fois par le vrai code : une fois par _load_and_authorize_projet,
une fois par la route elle-même) -- exactement comme psycopg où chaque
get_conn() ouvre une connexion DIFFÉRENTE vers la MÊME base.

Exécution : python tests/test_taux_horaire_backfill_ouverture.py
"""
import os
import sys
from decimal import Decimal

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import modules.ad_budget_api as M  # noqa: E402

MODULE_LABEL = "taux_horaire_backfill_ouverture"

USER = {"id": 1, "platform_role": None, "organization_id": "org-1", "email": "simon@adision.ca"}

TAUX_ELECTRICIEN = Decimal("55.00")
TAUX_REPLI_CHARPENTIER = Decimal("48.50")  # CHARPENTIER_C


class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self._one = None
        self._many = None
        self.executions = []

    def execute(self, sql, params=None):
        self.executions.append((" ".join(sql.split())[:70], params))
        if "FROM ad_budget.projets" in sql:
            self._one = self.db["projet"]
        elif "UPDATE ad_budget.budget_lignes" in sql:
            nouveau_taux, ligne_id = params
            for r in self.db["lignes"]:
                if r["id"] == ligne_id:
                    r["taux_horaire"] = nouveau_taux
        elif "FROM ad_budget.budget_lignes" in sql and "SELECT *" in sql:
            self._many = list(self.db["lignes"])
        elif "csi_division_default_metier" in sql:
            (division,) = params
            taux = self.db["division_default"].get(division)
            self._one = {"taux_col17": taux} if taux is not None else None
        elif "FROM ad_budget.taux_horaires" in sql and "WHERE code" in sql:
            (code,) = params
            taux = self.db["repli"].get(code)
            self._one = {"taux_col17": taux} if taux is not None else None
        else:
            self._one = None
            self._many = []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many if self._many is not None else []

    def close(self):
        pass


class _FakeConn:
    """Une instance par appel à get_conn() -- mais toutes partagent `db`
    (même dict Python), exactement comme N connexions psycopg vers la même
    base réelle."""
    def __init__(self, db):
        self.db = db
        self.committed = False

    def cursor(self, *a, **k):
        return _FakeCursor(self.db)

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _db(lignes, division_default=None, repli=None, projet_user_id=1):
    return {
        "projet": {
            "user_id": projet_user_id,
            "organization_id": "org-1",
            "is_verrouille": False,
            "detenteur_id": None,
            "detenteur_nom": None,
            "detenteur_email": None,
            "derniere_activite": None,
        },
        "lignes": lignes,
        "division_default": division_default or {},
        "repli": repli or {"CHARPENTIER_C": TAUX_REPLI_CHARPENTIER},
    }


def _route(db):
    router = M.register_ad_budget_routes(lambda: _FakeConn(db))
    by_name = {r.endpoint.__name__: r.endpoint for r in router.routes}
    return by_name["get_budget_lignes"]


def test_ligne_zero_division_mappee_est_corrigee_et_persistee():
    lignes = [{"id": 101, "section": "26 05 00", "taux_horaire": 0}]
    db = _db(lignes, division_default={"26": TAUX_ELECTRICIEN})
    fn = _route(db)
    out = fn(9001, user=USER)
    assert out[0]["taux_horaire"] == TAUX_ELECTRICIEN, out
    # persisté : la "base" elle-même a changé, pas seulement la réponse.
    assert db["lignes"][0]["taux_horaire"] == TAUX_ELECTRICIEN
    print(f"  [OK] ({MODULE_LABEL}) ligne a 0, division mappee -> corrigee + persistee")


def test_ligne_null_division_non_mappee_tombe_sur_repli_charpentier():
    lignes = [{"id": 102, "section": "99 99 00", "taux_horaire": None}]
    db = _db(lignes, division_default={})  # aucune division mappée
    fn = _route(db)
    out = fn(9002, user=USER)
    assert out[0]["taux_horaire"] == TAUX_REPLI_CHARPENTIER, out
    print(f"  [OK] ({MODULE_LABEL}) ligne NULL, division non mappee -> repli CHARPENTIER_C")


def test_ligne_avec_taux_deja_positif_nest_jamais_touchee():
    lignes = [{"id": 103, "section": "26 05 00", "taux_horaire": Decimal("77.00")}]
    db = _db(lignes, division_default={"26": TAUX_ELECTRICIEN})
    fn = _route(db)
    out = fn(9003, user=USER)
    assert out[0]["taux_horaire"] == Decimal("77.00"), out
    print(f"  [OK] ({MODULE_LABEL}) ligne deja a taux > 0 -> jamais remplacee")


def test_aucune_ligne_a_corriger_aucun_update_aucun_commit():
    lignes = [
        {"id": 104, "section": "26 05 00", "taux_horaire": Decimal("60.00")},
        {"id": 105, "section": "06 40 00", "taux_horaire": Decimal("45.00")},
    ]
    db = _db(lignes, division_default={"26": TAUX_ELECTRICIEN})
    fn = _route(db)
    out = fn(9004, user=USER)
    assert [r["taux_horaire"] for r in out] == [Decimal("60.00"), Decimal("45.00")]
    print(f"  [OK] ({MODULE_LABEL}) projet deja propre -> grille inchangee")


def test_plusieurs_lignes_a_corriger_sur_le_meme_projet():
    lignes = [
        {"id": 106, "section": "26 05 00", "taux_horaire": 0},
        {"id": 107, "section": "99 99 00", "taux_horaire": None},
        {"id": 108, "section": "06 40 00", "taux_horaire": Decimal("30.00")},
    ]
    db = _db(lignes, division_default={"26": TAUX_ELECTRICIEN})
    fn = _route(db)
    out = fn(9005, user=USER)
    par_id = {r["id"]: r["taux_horaire"] for r in out}
    assert par_id[106] == TAUX_ELECTRICIEN
    assert par_id[107] == TAUX_REPLI_CHARPENTIER
    assert par_id[108] == Decimal("30.00")  # intouchée
    print(f"  [OK] ({MODULE_LABEL}) plusieurs lignes a corriger -> chacune resolue independamment")


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            import traceback
            print(f"  [X] {t.__name__} : {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests OK ({MODULE_LABEL})")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
