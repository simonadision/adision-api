"""
Ad BUD — présence légère (lot présence, août 2026, option A tranchée par
Simon : gabarit Ad TAK, fichiers propres, PAS de factorisation avec Ad EST
ni Ad TAK — voir `adision-tak-api/modules/tak_presence.py`, la jumelle dont
CE MODULE S'INSPIRE sans jamais l'importer).

════════════════════════════════════════════════════════════════════════════
POURQUOI UN SONDAGE REST ET PAS UN WEBSOCKET (à justifier, cf. le brief)
════════════════════════════════════════════════════════════════════════════
Ad TAK a un websocket parce qu'il diffuse DEUX choses qu'un sondage rendrait
lentes ou impossibles : la position du curseur souris (haute fréquence) et
« quelque chose a changé, va relire » (pour que plusieurs personnes annotant
LE MÊME plan en direct se voient sans attendre). Ad BUD n'a NI L'UN NI
L'AUTRE — un seul rédacteur écrit à la fois (détention, gate serveur dans
`ad_budget_api._load_and_authorize_projet`, NON TOUCHÉ ici), donc aucune
diffusion d'écriture n'est nécessaire : un visiteur qui veut voir la donnée
à jour recharge comme avant ce lot.

Il ne reste donc que « qui regarde, depuis quand » — un besoin que le FRONT
sert déjà avec une boucle de sondage existante : `App.jsx`, l'effet
« RAFRAÎCHISSEMENT DE LA PRÉSENCE » (~lignes 2025-2058) sonde déjà
`GET /budget/projets/{id}` toutes les 60 s + à la reprise de visibilité de
l'onglet, pour TOUT visiteur (détenteur ou non). Ce module s'appuie sur
CETTE MÊME boucle côté client (le hook `useBudPresence` expose un
`battre()` que l'effet existant appelle, il n'ouvre PAS un second
`setInterval`) plutôt que d'ouvrir un websocket pour un besoin que le
sondage à 60 s couvre très bien : un délai d'affichage d'une minute au pire
est un compromis très correct pour « qui regarde ce budget ».

════════════════════════════════════════════════════════════════════════════
ÉTAT EN MÉMOIRE — MÊME CAVEAT QUE tak_presence.py
════════════════════════════════════════════════════════════════════════════
`_PRESENCE` vit dans le processus. Correct AUJOURD'HUI : `Procfile` lance
`python -m uvicorn api:app` SANS `--workers`, un seul processus donc un seul
état. Le jour où ce service passe à plusieurs réplicas ou `--workers > 1`,
la présence se fragmente EN SILENCE (chaque processus ne verra que ses
propres battements). Rien ne plantera, la présence deviendra juste
incomplète — il faudra alors un canal partagé (Redis, LISTEN/NOTIFY). La
DÉTENTION, elle, reste en base côté hub et ne serait pas affectée.

════════════════════════════════════════════════════════════════════════════
SÉCURITÉ — MÊME PÉRIMÈTRE QUE LE REST, RIEN DE NOUVEAU
════════════════════════════════════════════════════════════════════════════
Les deux routes appellent `_load_and_authorize_projet(..., mode="read")`
AVANT de toucher au roster — la même fonction que les 28 autres endpoints
`ad_budget_api.py`. Import DIRECT (pas de duplication) : `ad_gabarits_api.py`
importe déjà `_load_and_authorize_projet` depuis `ad_budget_api` sans créer
de cycle (`ad_budget_api` n'importe rien de ces modules satellites), donc ce
module fait pareil. mode='read' n'est JAMAIS bloqué par la détention ni par
le verrou (`is_verrouille`) — un visiteur en lecture seule et un projet
verrouillé voient la présence normalement, exactement comme demandé.
Hors organisation → 404 « Projet introuvable » AVANT tout accès au roster :
un visiteur d'une autre org ne peut ni lire ni polluer la présence d'un
projet qui ne le concerne pas.
"""
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from modules.auth_jwt import make_jwt_deps
from modules.ad_budget_api import _load_and_authorize_projet

# Le client sonde à 60 s (App.jsx). Un seuil à 150 s (2,5×) tolère UN
# battement manqué (onglet en arrière-plan qui revient, réseau bref) sans
# faire clignoter le roster chez les autres — même logique que le seuil de
# détention (SEUIL_DETENTION), juste à une échelle bien plus courte parce
# que la présence n'a pas d'enjeu d'écriture à protéger.
SEUIL_PRESENCE_SECONDES = 150

# {projet_id: {user_id: {"nom", "email", "depuis", "vu_le"}}}
_PRESENCE: dict = {}


def _purger(projet_id) -> None:
    """Retire les entrées dont le dernier battement dépasse le seuil. Appelé
    à chaque lecture du roster — pas de tâche de fond, pas de cron : une
    présence expirée disparaît la prochaine fois que QUELQU'UN regarde,
    exactement le patron de la détention (expiration évaluée à la lecture)."""
    salon = _PRESENCE.get(projet_id)
    if not salon:
        return
    limite = time.time() - SEUIL_PRESENCE_SECONDES
    morts = [uid for uid, info in salon.items() if info.get("vu_le", 0) < limite]
    for uid in morts:
        salon.pop(uid, None)
    if not salon:
        _PRESENCE.pop(projet_id, None)


def _etat_presence(projet_id) -> list:
    _purger(projet_id)
    salon = _PRESENCE.get(projet_id) or {}
    presents = [
        {
            "user_id": uid,
            # Nom, repli courriel — jamais un roster avec une case vide.
            "nom": info.get("nom") or info.get("email") or f"Utilisateur {uid}",
            "email": info.get("email"),
            "depuis": info.get("depuis"),
            "vu_le": info.get("vu_le"),
        }
        for uid, info in salon.items()
    ]
    # Ordre stable : le plus ancien présent en premier (cohérent avec la
    # lecture « qui est là depuis le plus longtemps » que fera l'écran).
    presents.sort(key=lambda p: p["depuis"] or 0)
    return presents


def _enregistrer(projet_id, user: dict) -> list:
    """Upsert la présence de `user` sur `projet_id`. Rend le roster à jour
    (déjà purgé) pour éviter un aller-retour supplémentaire côté appelant."""
    uid = user.get("id")
    if uid is None:
        return _etat_presence(projet_id)
    salon = _PRESENCE.setdefault(projet_id, {})
    maintenant = time.time()
    info = salon.get(uid)
    if info is None:
        info = {"nom": user.get("nom"), "email": user.get("email"), "depuis": maintenant}
        salon[uid] = info
    info["vu_le"] = maintenant
    # Nom à jour à chaque battement (un changement de profil se reflète sans
    # attendre une reconnexion) — jamais écrasé par une valeur vide.
    info["nom"] = user.get("nom") or info.get("nom")
    info["email"] = user.get("email") or info.get("email")
    return _etat_presence(projet_id)


def _retirer(projet_id, user_id) -> None:
    salon = _PRESENCE.get(projet_id)
    if not salon or user_id not in salon:
        return
    salon.pop(user_id, None)
    if not salon:
        _PRESENCE.pop(projet_id, None)


def register_bud_presence_routes(get_conn):
    jwt_user, _jwt_user_or_token, _jwt_admin, _jwt_super_admin = make_jwt_deps(get_conn)
    router = APIRouter(prefix="/budget", tags=["Ad BUD — Présence"])

    @router.post("/projets/{projet_id}/presence/battement")
    def battement_presence(projet_id: int, user=Depends(jwt_user)):
        """Signale « je regarde ce projet, maintenant » et rend le roster à
        jour. Appelé par `useBudPresence.battre()`, lui-même déclenché par la
        boucle de sondage EXISTANTE de App.jsx (60 s + visibilitychange) —
        voir l'en-tête du module. mode='read' : jamais gêné par la détention
        ni par le verrou, un visiteur en lecture seule s'enregistre pareil
        qu'un détenteur."""
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        presents = _enregistrer(projet_id, user)
        return {"presents": presents}

    @router.delete("/projets/{projet_id}/presence")
    def quitter_presence(projet_id: int, user=Depends(jwt_user)):
        """Sortie explicite (fermeture d'onglet via `pagehide`, ou retour à
        la liste des projets). Best-effort : si l'appel n'arrive jamais
        (onglet tué, batterie à plat), la purge par âge (`_purger`) fait le
        ménage au plus tard `SEUIL_PRESENCE_SECONDES` après le dernier
        battement — jamais de fantôme permanent dans le roster."""
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        _retirer(projet_id, user.get("id"))
        return {"status": "parti"}

    return router
