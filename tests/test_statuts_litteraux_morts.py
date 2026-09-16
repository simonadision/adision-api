# -*- coding: utf-8 -*-
"""TEMOIN — aucun statut de projet ne compare a une valeur disparue.

16 septembre 2026. Le regroupement des statuts du 9 septembre (brouillon ->
en_soumission, adjuge/complet -> en_cours, + en_execution) avait laisse
derriere lui des litteraux « brouillon » qui ne levaient rien :

  - POST /budget/projets passait « brouillon » a l'INSERT quand le client
    omettait le statut : `projets_statut_chk` refusait la ligne. Le front
    envoyait toujours un statut, la panne restait invisible.
  - Les quatre gardes du catalogue (⟳ refresh-typ, GET maj-typ, POST
    maj-typ/apply, POST maj-mat/apply) exigeaient `statut == "brouillon"` :
    409 ou onglet MAJ absent pour les 23 projets de la base, sans exception.

C'est la troisieme porte de cette famille (apres export-for-con et
_ensure_schema, 11 septembre). Ce fichier verrouille :

  1. le vocabulaire Python == la contrainte CHECK de la derniere migration ;
  2. une creation sans statut passe la contrainte (fausse base qui l'applique) ;
  3. PAR EXECUTION des vraies routes, pour CHACUN des cinq statuts, que les
     quatre gardes s'ouvrent en soumission et se ferment partout ailleurs ;
  4. qu'aucun code Python ni SQL ne compare plus un statut a une valeur hors
     ALLOWED_STATUTS, et qu'aucun ancien nom ne survit en litteral.

Execution : python tests/test_statuts_litteraux_morts.py
"""
import ast
import glob
import os
import re
import sys
import textwrap

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import HTTPException  # noqa: E402

import modules.ad_budget_api as M  # noqa: E402

SRC_BUDGET = os.path.join(_ROOT, "modules", "ad_budget_api.py")
ANCIENS_STATUTS = {"brouillon", "adjuge", "complet"}
# Ecrit en dur, PAS lu depuis M : les tests d'execution ci-dessous ne doivent
# pas valider la constante contre elle-meme.
CATALOGUE_OUVERT = {"en_soumission"}
USER = {"id": 1, "platform_role": None, "organization_id": "org-1", "email": "simon@adision.ca"}


class CheckViolation(Exception):
    """Ce que psycopg leverait sur `projets_statut_chk`."""


def _statuts_du_check():
    """Valeurs du DERNIER `projets_statut_chk` pose, dans l'ordre ou le runner
    joue les migrations (api.py : sorted(glob('*.sql')))."""
    dernier = None
    for chemin in sorted(glob.glob(os.path.join(_ROOT, "migrations", "*.sql"))):
        sql = open(chemin, encoding="utf-8").read()
        for m in re.finditer(r"ADD CONSTRAINT projets_statut_chk\s+CHECK\s*\(\s*statut IN \(([^)]*)\)", sql):
            dernier = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert dernier, "aucune migration ne pose projets_statut_chk"
    return dernier


STATUTS_CHECK = _statuts_du_check()


# ── Fausse base ──────────────────────────────────────────────────────────
class _Cur:
    def __init__(self, db):
        self.db = db
        self._one = None
        self._all = []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self._one, self._all = None, []
        if s.startswith("INSERT INTO ad_budget.projets"):
            statut = params[2]
            if statut not in STATUTS_CHECK:
                raise CheckViolation(f"projets_statut_chk refuse {statut!r}")
            self.db["insere"] = statut
            self._one = {"id": 501, "statut": statut}
        elif "FROM ad_budget.projets" in s:
            self._one = dict(self.db["projet"])
        # budget_lignes : aucune ligne -> la route s'arrete juste APRES la garde.

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all

    def close(self):
        pass


class _Conn:
    def __init__(self, db):
        self.db = db

    def cursor(self, *a, **k):
        return _Cur(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _routes(statut="en_soumission"):
    db = {"projet": {
        "user_id": USER["id"], "organization_id": "org-1", "statut": statut,
        "is_verrouille": False, "detenteur_id": None, "detenteur_nom": None,
        "detenteur_email": None, "derniere_activite": None,
    }}
    router = M.register_ad_budget_routes(lambda: _Conn(db))
    return {r.endpoint.__name__: r.endpoint for r in router.routes}, db


AUTH = {"authorization": "Bearer jeton-test", "session_cookie": None, "user": USER}


def _statut_http(fn):
    try:
        return 200, fn()
    except HTTPException as e:
        return e.status_code, e.detail


# ── 1. Vocabulaire ───────────────────────────────────────────────────────
def test_allowed_statuts_egal_contrainte_check():
    assert M.ALLOWED_STATUTS == STATUTS_CHECK, (M.ALLOWED_STATUTS, STATUTS_CHECK)
    assert M.STATUT_PAR_DEFAUT in STATUTS_CHECK
    assert M.RESYNC_CATALOGUE_STATUTS <= STATUTS_CHECK
    assert M.RESYNC_CATALOGUE_STATUTS == CATALOGUE_OUVERT, (
        "le catalogue ne doit suivre le budget que pendant le chiffrage : "
        "un budget gagne, en chantier ou clos est gele"
    )


# ── 2. Creation ──────────────────────────────────────────────────────────
def test_creation_sans_statut_passe_la_contrainte():
    routes, db = _routes()
    out = routes["create_projet"]({}, **AUTH)
    assert db["insere"] == "en_soumission", db
    assert out["projet"]["statut"] == "en_soumission"


def test_creation_statut_explicite_respecte():
    routes, db = _routes()
    routes["create_projet"]({"statut": "en_cours"}, **AUTH)
    assert db["insere"] == "en_cours", db


# ── 3. Les quatre gardes du catalogue, statut par statut ─────────────────
def _appels(routes):
    return {
        "refresh-typ": lambda: routes["refresh_budget_ligne_from_typ"](7, 42, **AUTH),
        "maj-typ/apply": lambda: routes["apply_maj_typ"](7, {"ligne_ids": [42]}, **AUTH),
        "maj-mat/apply": lambda: routes["apply_maj_mat"](7, {"ligne_ids": [42]}, **AUTH),
    }


def test_gardes_ecriture_ouvertes_en_soumission_seulement():
    for statut in sorted(STATUTS_CHECK):
        routes, _ = _routes(statut)
        for nom, appel in _appels(routes).items():
            code, detail = _statut_http(appel)
            if statut in CATALOGUE_OUVERT:
                # Garde franchie : la route cherche ses lignes (aucune) -> 404.
                assert code == 404, (nom, statut, code, detail)
            else:
                assert code == 409, (nom, statut, code, detail)
                assert M.LIBELLE_STATUT[statut] in detail, (nom, statut, detail)
                assert "Projet en soumission" in detail, (nom, statut, detail)


def test_detection_maj_active_en_soumission_seulement():
    for statut in sorted(STATUTS_CHECK):
        routes, _ = _routes(statut)
        out = routes["detect_maj_typ"](7, **AUTH)
        attendu = statut in CATALOGUE_OUVERT
        assert out["actif"] is attendu, (statut, out)


# ── 4. Garde-fou source ──────────────────────────────────────────────────
def _docstrings(tree):
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
                ids.add(id(n.body[0].value))
    return ids


def _nom(n):
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        return n.attr
    if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant):
        return str(n.slice.value)
    return ""


def _valeurs(n):
    """Chaines designees par un noeud : litteral, ensemble litteral, ou nom
    de constante du module (resolu a l'execution). None = indeterminable."""
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return {n.value}
    if isinstance(n, (ast.Set, ast.Tuple, ast.List)):
        if all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in n.elts):
            return {e.value for e in n.elts}
    if isinstance(n, ast.Name) and n.id.isupper() and hasattr(M, n.id):
        v = getattr(M, n.id)
        return {v} if isinstance(v, str) else set(v)
    return None


def test_comparaisons_de_statut_dans_le_vocabulaire():
    tree = ast.parse(open(SRC_BUDGET, encoding="utf-8").read())
    fautes = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare):
            cotes = [n.left] + n.comparators
            if any("statut" in _nom(c).lower() for c in cotes):
                for c in cotes:
                    v = _valeurs(c)
                    if v and not v <= M.ALLOWED_STATUTS:
                        fautes.append((n.lineno, ast.unparse(n)))
        # data.get("statut", <defaut>)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
                and len(n.args) == 2 and isinstance(n.args[0], ast.Constant) and n.args[0].value == "statut"):
            v = _valeurs(n.args[1])
            if v and not v <= M.ALLOWED_STATUTS:
                fautes.append((n.lineno, ast.unparse(n)))
        # SQL : statut = 'x' / <> 'x' / IN ('x', ...)
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            for m in re.finditer(r"statut\s*(?:=|<>|!=|(?:NOT\s+)?IN)\s*\(?\s*((?:'[^']*'\s*,?\s*)+)", n.value, re.I):
                v = set(re.findall(r"'([^']*)'", m.group(1)))
                if not v <= M.ALLOWED_STATUTS:
                    fautes.append((n.lineno, m.group(0)))
    assert not fautes, "statut compare a une valeur hors ALLOWED_STATUTS : %r" % fautes


def test_aucun_ancien_statut_en_litteral():
    fichiers = (glob.glob(os.path.join(_ROOT, "*.py"))
                + glob.glob(os.path.join(_ROOT, "modules", "*.py"))
                + glob.glob(os.path.join(_ROOT, "scripts", "*.py")))
    motif = re.compile(r"'(%s)'" % "|".join(sorted(ANCIENS_STATUTS)))
    fautes = []
    for chemin in fichiers:
        # dedent : from_viu_v2_endpoint.py est un fragment de routeur indente.
        tree = ast.parse(textwrap.dedent(open(chemin, encoding="utf-8").read()))
        docs = _docstrings(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                if n.value in ANCIENS_STATUTS or motif.search(n.value):
                    fautes.append((os.path.relpath(chemin, _ROOT), n.lineno, n.value[:60]))
    assert not fautes, "ancien statut encore en litteral : %r" % fautes


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
