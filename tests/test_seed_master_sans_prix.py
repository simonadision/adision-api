"""TÉMOIN — le seed du squelette maître garde la STRUCTURE, met tout PRIX à zéro.

Décision 2026-07 (option 2) : ad_budget_prix_moyens est figée depuis le 5 mai
et ses prix ne valent plus rien ; on garde section/description/unité, on seede
les prix à 0. « Aucune valeur monétaire ne vient du vestige ».

_apply_master_template émet un INSERT … SELECT : les valeurs sont calculées
CÔTÉ SERVEUR, elles ne passent jamais par Python. On EXÉCUTE donc la fonction
avec un curseur qui capture le SQL émis, et on vérifie le contrat que ce SQL
impose aux lignes produites : structure conservée, tout montant à zéro, aucun
prix `pm.*` (le vestige). Réintroduire la copie d'un seul champ de prix casse
le test.
"""
from modules.ad_budget_api import _apply_master_template


class _CurseurCapteur:
    """Capture (sql, params) du seul cur.execute() de _apply_master_template."""
    def __init__(self):
        self.sql = None
        self.params = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params


def _colonnes_et_expressions(sql):
    """Découpe l'INSERT (col list) et les expressions du SELECT, appariées."""
    debut = sql.index("(projet_id")
    fin = sql.index(")", debut)
    cols = [c.strip() for c in sql[debut + 1:fin].split(",")]
    select = sql[sql.index("SELECT ") + 7: sql.index(" FROM ")]
    exprs, prof, cur = [], 0, ""
    for ch in select:
        if ch == "(":
            prof += 1
        elif ch == ")":
            prof -= 1
        if ch == "," and prof == 0:
            exprs.append(cur.strip())
            cur = ""
        else:
            cur += ch
    exprs.append(cur.strip())
    return cols, exprs


def _seed_sql():
    cur = _CurseurCapteur()
    _apply_master_template(cur, 277)   # projet jetable ; aucun accès DB (curseur factice)
    assert cur.sql, "la fonction n'a émis aucun SQL"
    return cur.sql


def test_structure_conservee():
    cols, exprs = _colonnes_et_expressions(_seed_sql())
    m = dict(zip(cols, exprs))
    # La STRUCTURE vient bien du vestige (c'est sa seule valeur restante).
    assert "pm.section" in m["section"]
    assert "pm.description" in m["description"]
    assert "pm.unite" in m["unite"]


def test_tout_montant_est_a_zero_et_aucun_prix_du_vestige():
    sql = _seed_sql()
    cols, exprs = _colonnes_et_expressions(sql)
    m = dict(zip(cols, exprs))
    # prix_unitaire : littéral 0, JAMAIS pm.prix_unitaire.
    assert m["prix_unitaire"] == "0", f"prix_unitaire devrait être 0, trouvé: {m['prix_unitaire']}"
    # qte / ajustement : déjà des zéros littéraux.
    assert m["qte"] == "0"
    assert m["ajustement_pct"] == "0"
    # ⚠ LA propriété qui décide : AUCUN prix du vestige nulle part dans le SQL.
    assert "pm.prix" not in sql, "un prix du vestige (pm.prix_*) a été réintroduit"


def test_taux_horaire_vient_de_la_table_vivante_pas_du_vestige():
    _, exprs = _colonnes_et_expressions(_seed_sql())
    cols, _ = _colonnes_et_expressions(_seed_sql())
    m = dict(zip(cols, exprs))
    # taux_horaire est monétaire MAIS vient de taux_horaires (t.taux_col17),
    # source vivante — identique à l'auto-fill d'une ligne manuelle. Pas pm.
    assert "t.taux_col17" in m["taux_horaire"]
    assert "pm." not in m["taux_horaire"]
