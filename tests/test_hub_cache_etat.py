# -*- coding: utf-8 -*-
"""TEMOIN -- la porte par laquelle Ad BUD DECLARE son etat au hub.

Simon, 1er oct 2026 : « le hub est la source » (20:01), « Hub est la verite »
(20:08), « Systeme bi directionnel qui defini la structure ad Flo » (20:30).

CE QUE CES CAS VERROUILLENT, ET POURQUOI CHACUN EST LA.

Le soir du 1er octobre, **17 projets sur 23 portaient un statut different
entre le hub et Ad BUD** -- dont un chantier de 3 M$ que le hub savait gagne
et qu'Ad BUD croyait encore en soumission. L'ecart a dure des semaines SANS
QUE RIEN NE LE SIGNALE. Cette route est la moitie « Ad BUD » de ce qui le
signalera : le hub l'interroge, compare a sa verite, et compte les ecarts.

ELLE EXPOSE L'ETAT DE TOUS LES PROJETS D'AD BUD. C'est exactement ce qu'il ne
faut pas laisser ouvert, d'ou les trois premiers cas : **secret absent = 503**
(regle de Simon du 1er oct 08:59 -- une porte sans serrure n'est pas une
porte), secret faux = 401, et le refus doit passer AVANT toute lecture.

AUTONOME, SANS PYTEST, et c'est voulu : la CI de ce depot lance les bancs par
`python tests/fichier.py`. Un fichier qui n'aurait tourne que sous pytest
aurait ete cable dans la CI et n'y aurait rien verifie -- exactement le defaut
qu'on vient de trouver sur 22 fichiers de ce depot.
"""
import os
import sys

_RACINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _RACINE)

# `api.py` EXIGE DATABASE_URL DES L'IMPORT (api.py:44, « fallback en dur
# retire -- Chantier 1, hygiene des secrets »). L'adresse ne sert a RIEN :
# aucun cas n'ouvre de connexion, `get_conn` est remplace partout. Elle doit
# seulement exister pour que l'import passe.
os.environ.setdefault("DATABASE_URL", "postgresql://banc:banc@127.0.0.1:1/banc")

SRC_API = os.path.join(_RACINE, "api.py")

import api  # noqa: E402


class FauxCurseur:
    def __init__(self, lignes):
        self.lignes = lignes
        self.sql = []
        self.ferme = False

    def execute(self, sql, params=()):
        self.sql.append(sql)

    def fetchall(self):
        return self.lignes

    def close(self):
        self.ferme = True


class FausseConnexion:
    def __init__(self, cur):
        self._cur = cur
        self.ferme = False

    def cursor(self, **kw):
        return self._cur

    def close(self):
        self.ferme = True


def _appeler(secret_env, secret_entete, conn_factory):
    """Pose l'environnement, remplace get_conn, appelle, puis REMET TOUT.

    Le remise en etat est dans un `finally` : un cas qui leve ne doit pas
    laisser une variable d'environnement ou un get_conn truque derriere lui,
    sinon le cas SUIVANT mesure autre chose que ce qu'il croit.
    """
    avant_env = os.environ.get("INTERNAL_SERVICE_SECRET")
    avant_conn = api.get_conn
    try:
        if secret_env is None:
            os.environ.pop("INTERNAL_SERVICE_SECRET", None)
        else:
            os.environ["INTERNAL_SERVICE_SECRET"] = secret_env
        api.get_conn = conn_factory
        return api.internal_hub_cache_etat(x_internal_secret=secret_entete)
    finally:
        if avant_env is None:
            os.environ.pop("INTERNAL_SERVICE_SECRET", None)
        else:
            os.environ["INTERNAL_SERVICE_SECRET"] = avant_env
        api.get_conn = avant_conn


def _attendre_http(code, secret_env, secret_entete, conn_factory, quoi):
    try:
        _appeler(secret_env, secret_entete, conn_factory)
    except api.HTTPException as e:
        assert e.status_code == code, f"{quoi} : attendu {code}, recu {e.status_code}"
        return
    raise AssertionError(f"{quoi} : aucune HTTPException levee, attendu {code}")


def _conn_interdite():
    raise AssertionError("la base ne doit PAS etre ouverte : le refus passe avant")


# ══════════════════════════════════════════════════════════════════════
# LA PORTE
# ══════════════════════════════════════════════════════════════════════

def test_secret_absent_donne_503_et_ne_lit_rien():
    """Secret non configure : 503, et AUCUNE lecture.

    Le refus doit passer AVANT d'ouvrir la connexion. Sinon une porte mal
    configuree lirait toute la base avant de refuser : le refus serait vrai
    et la fuite aussi.
    """
    _attendre_http(503, None, "peu importe", _conn_interdite, "secret absent")


def test_secret_faux_donne_401():
    _attendre_http(401, "le-bon", "le-mauvais", _conn_interdite, "secret faux")


def test_entete_manquante_donne_401():
    """En-tete absente : 401, pas une erreur de programme.

    `x_internal_secret` vaut None quand l'appelant ne pose pas l'en-tete.
    compare_digest leverait un TypeError sur None -- d'ou la garde
    `not x_internal_secret` AVANT la comparaison.
    """
    _attendre_http(401, "le-bon", None, _conn_interdite, "en-tete absente")


# ══════════════════════════════════════════════════════════════════════
# CE QU'ELLE DECLARE
# ══════════════════════════════════════════════════════════════════════

def test_declare_hub_id_projet_id_et_statut():
    cur = FauxCurseur([
        {"hub_id": 771, "projet_id": 290, "statut": "en_cours"},
        {"hub_id": 790, "projet_id": 324, "statut": "perdu"},
    ])
    res = _appeler("s", "s", lambda: FausseConnexion(cur))
    assert res["module"] == "ad_bud"
    assert res["total"] == 2
    assert res["projets"][0] == {"hub_id": 771, "projet_id": 290, "statut": "en_cours"}
    assert res["projets"][1] == {"hub_id": 790, "projet_id": 324, "statut": "perdu"}


def test_les_trois_exclusions_sont_dans_le_sql():
    """Les exclusions vivent dans le SQL, donc c'est le SQL qu'on verifie.

    - `ad_hub_project_id IS NOT NULL` : un budget sans lien n'a aucune verite
      a comparer ; le declarer ferait un faux ecart a chaque reconciliation.
    - `supprime_le IS NULL` : le hub ne peut rien dire d'un budget jete.
    - `ORDER BY` : deux releves successifs doivent etre comparables ligne a
      ligne. Sans ordre stable, la comparaison du hub signalerait du bruit.
    """
    cur = FauxCurseur([])
    _appeler("s", "s", lambda: FausseConnexion(cur))
    sql = " ".join(cur.sql)
    assert "ad_hub_project_id IS NOT NULL" in sql
    assert "supprime_le IS NULL" in sql
    assert "ORDER BY" in sql


def test_ferme_curseur_et_connexion():
    """La reconciliation appellera cette route en boucle : elle ne doit pas
    fuir une connexion a chaque passage."""
    cur = FauxCurseur([])
    conn = FausseConnexion(cur)
    _appeler("s", "s", lambda: conn)
    assert cur.ferme is True
    assert conn.ferme is True


# ══════════════════════════════════════════════════════════════════════
# CONTRAT DE SOURCE -- la route NE DOIT PAS ECRIRE
# ══════════════════════════════════════════════════════════════════════

def test_la_route_ne_contient_aucune_ecriture():
    """Elle ne sait QUE lire, et ca doit le rester.

    On lit le SOURCE de la fonction plutot que son comportement : un cas de
    comportement ne couvrirait que le chemin qu'on a pense a emprunter.
    """
    src = open(SRC_API, encoding="utf-8").read()
    deb = src.index("def internal_hub_cache_etat")
    fin = src.index('@app.post("/internal/budget-figer-photo', deb)
    corps = src[deb:fin]
    for interdit in ("UPDATE ", "INSERT ", "DELETE ", "commit("):
        assert interdit not in corps, f"la route ecrit : {interdit!r}"


if __name__ == "__main__":
    cas = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in cas:
        f()
        print(f"  OK  {f.__name__}")
    print(f"{len(cas)} cas au vert — porte /internal/hub-cache/etat")
