"""Ad BUD — présence légère (lot présence, août 2026).

Teste les fonctions PURES du roster (`modules/bud_presence.py`) directement
sur le dict en mémoire — aucune base, aucun réseau, même patron que
`test_scope_deux_onglets.py` pour `_authorize_projet` : le périmètre
sécurité (org / propriétaire / lecture jamais bloquée) est déjà couvert par
`_load_and_authorize_projet`, testé ailleurs et REÇU en dépendance par ce
module sans y toucher. Ce qui reste à prouver ICI est propre à ce module :
upsert, repli nom→courriel, tri par ancienneté, purge par âge, sortie
explicite.

Le module tient son état dans un dict GLOBAL (`_PRESENCE`) — chaque test le
vide en `setup_function` pour rester indépendant des autres.
"""
from modules import bud_presence as p


def setup_function(_fn):
    p._PRESENCE.clear()


def test_enregistrer_ajoute_et_rend_le_roster():
    presents = p._enregistrer(101, {"id": 1, "nom": "Simon", "email": "simon@adision.ca"})
    assert len(presents) == 1
    assert presents[0]["user_id"] == 1
    assert presents[0]["nom"] == "Simon"
    assert presents[0]["depuis"] is not None


def test_repli_nom_sur_courriel_puis_sur_id():
    # Nom absent -> repli courriel (règle explicite du brief).
    presents = p._enregistrer(101, {"id": 2, "nom": None, "email": "collegue@adision.ca"})
    assert presents[0]["nom"] == "collegue@adision.ca"
    # Ni nom ni courriel -> repli sur l'id, jamais une case vide dans le roster.
    p._PRESENCE.clear()
    presents = p._enregistrer(101, {"id": 3, "nom": None, "email": None})
    assert presents[0]["nom"] == "Utilisateur 3"


def test_deuxieme_battement_du_meme_user_ne_duplique_pas():
    p._enregistrer(101, {"id": 1, "nom": "Simon", "email": "simon@adision.ca"})
    presents = p._enregistrer(101, {"id": 1, "nom": "Simon", "email": "simon@adision.ca"})
    assert len(presents) == 1  # un deuxième onglet du même compte n'est pas une deuxième présence


def test_deux_users_distincts_se_voient_tous_les_deux():
    p._enregistrer(101, {"id": 1, "nom": "Simon", "email": "simon@adision.ca"})
    presents = p._enregistrer(101, {"id": 2, "nom": "Collègue", "email": "c@adision.ca"})
    ids = {row["user_id"] for row in presents}
    assert ids == {1, 2}


def test_roster_isole_par_projet():
    p._enregistrer(101, {"id": 1, "nom": "Simon", "email": "simon@adision.ca"})
    p._enregistrer(202, {"id": 2, "nom": "Collègue", "email": "c@adision.ca"})
    assert [row["user_id"] for row in p._etat_presence(101)] == [1]
    assert [row["user_id"] for row in p._etat_presence(202)] == [2]


def test_tri_par_anciennete_le_plus_ancien_dabord(monkeypatch):
    horloge = [1000.0]
    monkeypatch.setattr(p.time, "time", lambda: horloge[0])
    p._enregistrer(101, {"id": 1, "nom": "Arrivé tôt", "email": "a@x.ca"})
    horloge[0] = 1050.0
    presents = p._enregistrer(101, {"id": 2, "nom": "Arrivé après", "email": "b@x.ca"})
    assert [row["user_id"] for row in presents] == [1, 2]


def test_purge_apres_le_seuil(monkeypatch):
    horloge = [1000.0]
    monkeypatch.setattr(p.time, "time", lambda: horloge[0])
    p._enregistrer(101, {"id": 1, "nom": "Simon", "email": "simon@adision.ca"})
    # Juste avant le seuil : encore là.
    horloge[0] = 1000.0 + p.SEUIL_PRESENCE_SECONDES - 1
    assert len(p._etat_presence(101)) == 1
    # Juste après : purgé, et le salon lui-même disparaît (pas de fantôme
    # vide qui traînerait dans _PRESENCE).
    horloge[0] = 1000.0 + p.SEUIL_PRESENCE_SECONDES + 1
    assert p._etat_presence(101) == []
    assert 101 not in p._PRESENCE


def test_battement_renouvelle_le_delai_de_purge(monkeypatch):
    horloge = [1000.0]
    monkeypatch.setattr(p.time, "time", lambda: horloge[0])
    p._enregistrer(101, {"id": 1, "nom": "Simon", "email": "simon@adision.ca"})
    horloge[0] = 1000.0 + p.SEUIL_PRESENCE_SECONDES - 1
    p._enregistrer(101, {"id": 1, "nom": "Simon", "email": "simon@adision.ca"})  # nouveau battement
    horloge[0] = 1000.0 + 2 * p.SEUIL_PRESENCE_SECONDES - 1
    assert len(p._etat_presence(101)) == 1  # toujours là : le battement a repoussé l'échéance


def test_retirer_sortie_explicite():
    p._enregistrer(101, {"id": 1, "nom": "Simon", "email": "simon@adision.ca"})
    p._enregistrer(101, {"id": 2, "nom": "Collègue", "email": "c@adision.ca"})
    p._retirer(101, 1)
    assert [row["user_id"] for row in p._etat_presence(101)] == [2]


def test_retirer_dernier_nettoie_le_salon():
    p._enregistrer(101, {"id": 1, "nom": "Simon", "email": "simon@adision.ca"})
    p._retirer(101, 1)
    assert 101 not in p._PRESENCE


def test_retirer_user_absent_est_un_noop():
    p._enregistrer(101, {"id": 1, "nom": "Simon", "email": "simon@adision.ca"})
    p._retirer(101, 999)  # jamais présent — ne doit pas lever
    assert len(p._etat_presence(101)) == 1


def test_nom_mis_a_jour_sans_jamais_devenir_vide():
    p._enregistrer(101, {"id": 1, "nom": "Simon", "email": "simon@adision.ca"})
    presents = p._enregistrer(101, {"id": 1, "nom": None, "email": "simon@adision.ca"})
    assert presents[0]["nom"] == "Simon"  # un battement sans nom ne vide pas le nom déjà connu
