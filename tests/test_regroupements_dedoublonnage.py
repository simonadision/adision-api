"""Ad BUD — les SOUS-TOTAUX ne peuvent plus compter un montant deux fois.

Un test par défaut RÉEL trouvé dans la revue du 9 septembre 2026 sur la
fonctionnalité « Sous-total » (bouton + modale de sélection des divisions).

CE QUI A DÉCLENCHÉ CETTE REVUE. Deux montants faux d'affilée dans une
SOUMISSION CLIENT, tous deux vus par Simon en direct :
  - PR #100 — les sous-totaux natifs n'atteignaient jamais le moteur PDF,
    donc absents du rapport alors que visibles à l'écran ;
  - PR #101 — le regroupement « Sous-total finition » du projet 309 portait
    en base six ALIAS de la même division (["09.1","09.2","09.3","09.4",
    "09.4.1","09"], tous réduits à "09" par les lecteurs) et le moteur PDF
    sommait chaque entrée : 197 627 $ × 6 = 1 185 762 $. La grille, elle,
    dédoublonnait déjà. Simon : « C'est très dangereux, si on envoie une
    soumission qui manquent des montants. »
Les deux fois, la DONNÉE stockée était corrompue et seule la charité d'un
lecteur la rendait juste. Ce fichier verrouille le correctif de fond :
le dédoublonnage se fait À L'ÉCRITURE, une fois, pour tous les lecteurs.

Aucune base, aucun réseau : _normaliser_regroupements est une fonction pure
extraite de la closure du routeur (tests/_harness.extract_nested), et les
deux tests d'endpoint utilisent une fausse DB en mémoire.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import extract_nested  # noqa: E402
import modules.ad_gabarits_api as G  # noqa: E402


def _normaliser():
    """La vraie fonction de production, telle qu'appelée par le PUT."""
    return extract_nested(
        G.register_ad_gabarits_routes, lambda: None, "_normaliser_regroupements"
    )


# ── DÉFAUT 1 — alias de division resommés (la racine de la PR #101) ────────
def test_les_six_alias_du_projet_309_deviennent_un_seul_membre():
    """Le cas EXACT qui a produit 1 185 762 $ au lieu de 197 627 $."""
    out = _normaliser()([{
        "nom": "Sous-total finition",
        "divisions": ["09.1", "09.2", "09.3", "09.4", "09.4.1", "09"],
    }])
    assert len(out) == 1
    assert out[0]["divisions"] == ["09.1"], (
        "les alias de la division 09 doivent être réduits à UN seul membre, "
        f"reçu {out[0]['divisions']}"
    )


def test_un_lecteur_sans_dedoublonnage_donne_maintenant_le_bon_montant():
    """La preuve par le montant, avec les vrais chiffres du projet 309.

    On somme les membres SANS Set — exactement ce que faisait le moteur PDF
    avant la PR #101, et ce que ferait n'importe quel futur lecteur qui
    oublierait de dédoublonner. Sur la donnée normalisée, le montant est
    juste malgré cet oubli : c'est tout l'intérêt de nettoyer à la source."""
    totaux_par_division = {"09": 197627.0}
    entree = _normaliser()([{
        "nom": "Sous-total finition",
        "divisions": ["09.1", "09.2", "09.3", "09.4", "09.4.1", "09"],
    }])[0]
    total = sum(
        totaux_par_division.get(G._cle_division_membre(d), 0.0)
        for d in entree["divisions"]
    )
    assert total == 197627.0, f"attendu 197 627 $, reçu {total}"


def test_des_divisions_reellement_differentes_sont_toutes_gardees():
    """Le dédoublonnage ne doit RIEN manger de légitime — sans ce test, un
    dédoublonnage trop large ferait DISPARAÎTRE des montants, soit l'autre
    moitié exacte de ce que Simon appelle dangereux."""
    out = _normaliser()([{
        "nom": "Système électromécanique et autres",
        "divisions": ["22 00 00", "23 00 00", "26 00 00", "27 00 00"],
    }])
    assert out[0]["divisions"] == ["22 00 00", "23 00 00", "26 00 00", "27 00 00"]


def test_divers_nest_jamais_tronque_comme_un_code():
    """« Divers » n'a que 6 lettres et jamais 2 chiffres CSI. Le tronquer le
    ferait entrer en collision avec une division « Di… » et le confondrait
    avec elle — même exception que les trois copies JS de cette règle."""
    out = _normaliser()([{"nom": "Mixte", "divisions": ["Divers", "09", "Divers"]}])
    assert out[0]["divisions"] == ["Divers", "09"]


# ── DÉFAUT 2 — sections répétées comptées deux fois ───────────────────────
def test_une_section_repetee_ne_compte_quune_fois():
    """buildReportRows additionne `sections` SANS Set (contrairement à
    `divisions`) : un code répété y compte deux fois. Jamais corrigé côté
    lecture — ici il ne peut plus être écrit."""
    out = _normaliser()([{
        "nom": "Sous-total sélection",
        "sections": ["09 91", "09 91", "02 56"],
    }])
    assert out[0]["sections"] == ["09 91", "02 56"]


def test_deux_codes_de_section_voisins_restent_deux_membres():
    """« 09 91 » et « 09 91 00 » sont deux entrées DISTINCTES du dictionnaire
    de totaux par sous-section. Les replier l'une sur l'autre ferait PERDRE
    un montant — c'est pourquoi `sections` se dédoublonne sur le code exact
    et non sur une clé tronquée, contrairement à `divisions`."""
    out = _normaliser()([{"nom": "Voisins", "sections": ["09 91", "09 91 00"]}])
    assert out[0]["sections"] == ["09 91", "09 91 00"]


# ── DÉFAUT 3 — un membre numérique faisait tomber la route en 500 ─────────
def test_un_code_envoye_comme_nombre_ne_fait_plus_tomber_lecriture():
    """`divisions: [9]` levait AttributeError (int.strip) → 500 sur le PUT,
    donc PERTE de tout l'enregistrement des sous-totaux, pas seulement de
    l'entrée fautive. Le contrat de la fonction est d'ÉCARTER une entrée à
    moitié remplie, jamais de faire tomber la route."""
    out = _normaliser()([{"nom": "Numérique", "divisions": [9], "sections": [2]}])
    assert out[0]["divisions"] == ["9"]
    assert out[0]["sections"] == ["2"]


def test_un_membre_dun_type_impossible_est_ecarte_pas_propage():
    out = _normaliser()([
        {"nom": "Objet", "divisions": [{"code": "09"}]},
        {"nom": "Bon", "divisions": ["09"]},
    ])
    assert len(out) == 1, "l'entrée sans AUCUN membre exploitable doit être écartée"
    assert out[0]["nom"] == "Bon"


def test_les_regles_dorigine_tiennent_toujours():
    """Non-régression du contrat d'avant : sans nom ou sans aucun membre,
    l'entrée est écartée ; un membre SEUL suffit, quelle que soit sa
    granularité ; `apres` et `division_liee` survivent."""
    out = _normaliser()([
        {"nom": "", "divisions": ["09"]},
        {"nom": "Sans membre", "divisions": [], "sections": []},
        {"nom": "Sections seules", "sections": ["09 91"], "apres": "08",
         "division_liee": "09"},
    ])
    assert len(out) == 1
    assert out[0]["nom"] == "Sections seules"
    assert out[0]["apres"] == "08"
    assert out[0]["division_liee"] == "09"


# ── DÉFAUT 4 — la duplication d'un gabarit recopiait les alias tels quels ──
class _FauxCurseur:
    """Fausse DB : route par sous-chaîne SQL et ENREGISTRE les écritures."""

    def __init__(self, gabarit, ecritures):
        self._gabarit = gabarit
        self.ecritures = ecritures
        self._pending = ("all", [])

    @staticmethod
    def _norm(sql):
        return " ".join(str(sql).split()).lower()

    def execute(self, sql, params=None):
        s = self._norm(sql)
        self.ecritures.append((s, params))
        if "from ad_budget.gabarits where id" in s:
            self._pending = ("one", dict(self._gabarit))
        elif "insert into ad_budget.gabarits" in s:
            self._pending = ("one", {"id": 77})
        elif "select source_gabarit_id, regroupements" in s:
            self._pending = ("one", dict(self._gabarit))
        elif "select regroupements from ad_budget.gabarits" in s:
            self._pending = ("one", dict(self._gabarit))
        else:
            self._pending = ("all", [])

    def fetchone(self):
        return self._pending[1] if self._pending[0] == "one" else None

    def fetchall(self):
        return self._pending[1] if self._pending[0] == "all" else []

    def close(self):
        pass


class _FausseConn:
    def __init__(self, gabarit, ecritures):
        self._gabarit = gabarit
        self.ecritures = ecritures

    def cursor(self, *a, **k):
        return _FauxCurseur(self._gabarit, self.ecritures)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


ALIAS_309 = ["09.1", "09.2", "09.3", "09.4", "09.4.1", "09"]


def test_dupliquer_un_gabarit_nettoie_les_alias_au_lieu_de_les_recopier():
    """Dupliquer est une ÉCRITURE. Recopier la colonne telle quelle ferait
    naître la copie déjà corrompue — et le correctif d'écriture ne rattrape
    que ce qui passe par lui."""
    ecritures = []
    gabarit = {
        "id": 12, "organization_id": "org-test", "nom": "Bordereau",
        "description": None,
        "regroupements": [{"nom": "Finition", "divisions": list(ALIAS_309)}],
    }
    dupliquer = extract_nested(
        G.register_ad_gabarits_routes,
        lambda: _FausseConn(gabarit, ecritures),
        "duplicate_gabarit",
    )
    dupliquer(gabarit_id=12, user={"organization_id": "org-test", "id": 1,
                                   "email": "simon@adision.ca"})
    inserts = [p for s, p in ecritures if "insert into ad_budget.gabarits" in s]
    assert inserts, "aucun INSERT de copie observé"
    stocke = json.loads(inserts[0][3])
    assert stocke[0]["divisions"] == ["09.1"], (
        f"la copie hérite encore des alias : {stocke[0]['divisions']}"
    )


# ── DÉFAUT 5 — les alias DÉJÀ en base ressortaient bruts en lecture ───────
def test_la_lecture_normalise_aussi_les_alias_deja_stockes(monkeypatch):
    """Le correctif d'écriture ne nettoie que ce qui est écrit APRÈS lui. Les
    projets qui portent déjà les alias de la PR #101 doivent ressortir
    propres, sinon un futur lecteur les resommerait."""
    monkeypatch.setattr(G, "_load_and_authorize_projet",
                        lambda *a, **k: None, raising=True)
    ligne = {
        "id": 309, "source_gabarit_id": None,
        "regroupements": [{"nom": "Sous-total finition",
                           "divisions": list(ALIAS_309)}],
    }
    lire = extract_nested(
        G.register_ad_gabarits_routes,
        lambda: _FausseConn(ligne, []),
        "get_projet_regroupements",
    )
    out = lire(projet_id=309, user={"organization_id": "org-test", "id": 1})
    assert out["regroupements"][0]["divisions"] == ["09.1"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
