# -*- coding: utf-8 -*-
"""CORRECTIFS D'UN BUDGET → CATALOGUE DE L'ORGANISATION (7 oct. 2026).

Simon, 14 h 14 : « ça fait plusieurs fois que je corrige des items
manuellement dans BUD… quand j'applique des correctifs ces correctifs doivent
être enregistrés… ça fait plusieurs reprises que je change l'unité de plâtrage
de gallon à global… et ça revient toujours ».

Mesuré en lecture seule : « Plâtrage » est l'item Ad TYP 09 20 00.01, unité
« gallon » au catalogue. Le gabarit le réinsère dans chaque nouveau budget
(320, 323, 325 : gallon) et le ⟳ réécrit l'unité depuis le catalogue. La
correction ne vivait que dans le budget où elle était faite.

Choix de Simon : « au catalogue, toujours », et « catalogue de mon org » --
jamais le maître d'Adision, qui toucherait toutes les organisations clientes.

Ad TYP sert d'abord la couche CUSTOM de l'organisation
(GET /typ/catalogue/{code} : assemblage_typique_custom avant le maître). Une
correction écrite là est donc lue par les prochaines insertions ET par le ⟳.
Un item encore servi depuis la copie Ad FLO de l'org est d'abord dupliqué
dans la couche custom (POST /typ/custom/assemblages/from-adflo/{master_id}),
puis corrigé (PATCH /typ/custom/assemblages/{id}).

Champs remontés : description et unité. Le PRIX d'un assemblage Ad TYP est
calculé par ses composants (déclencheur) : il ne se corrige pas d'un champ ;
il n'est pas remonté ici, et l'écran le dit.

Jamais bloquant : une correction de catalogue qui échoue n'annule pas
l'enregistrement du budget -- elle est RAPPORTÉE (statut + message), pour que
l'écran le dise au lieu de se taire.
"""
import logging

import httpx

from modules.typ_service import TYP_API_URL, _headers

logger = logging.getLogger(__name__)

CHAMPS_REMONTES_TYP = ("description", "unite")
TIMEOUT_S = 8.0


def champs_a_remonter(avant: dict, data: dict) -> dict:
    """Les champs remontables RÉELLEMENT changés par cette saisie (l'écran
    renvoie souvent toute la ligne : un champ identique n'est pas une
    correction)."""
    out = {}
    for k in CHAMPS_REMONTES_TYP:
        if k not in data:
            continue
        nouveau = (data.get(k) or "").strip()
        ancien = ((avant or {}).get(k) or "").strip()
        if nouveau and nouveau != ancien:
            out[k] = nouveau
    return out


def _trouver(client, jwt_token, code):
    r = client.get(f"{TYP_API_URL}/typ/custom/catalogue", params={"q": code},
                   headers=_headers(jwt_token))
    if r.status_code != 200:
        raise RuntimeError(f"lecture du catalogue refusée (HTTP {r.status_code})")
    items = (r.json() or {}).get("items") or []
    exacts = [i for i in items if (i.get("code") or "").strip() == code]
    # La couche custom (dérivée ou native) prime : c'est elle qui est servie.
    exacts.sort(key=lambda i: 0 if i.get("custom_id") else 1)
    return exacts[0] if exacts else None


def corriger_typ(jwt_token: str, code: str, champs: dict) -> dict:
    """Écrit `champs` (description / unite) sur l'item `code` du catalogue Ad
    TYP de l'organisation du jeton. Rend {statut, message}."""
    code = (code or "").strip()
    if not code or not champs:
        return {"statut": "rien", "message": ""}
    try:
        with httpx.Client(timeout=TIMEOUT_S) as client:
            item = _trouver(client, jwt_token, code)
            if not item:
                return {"statut": "echec",
                        "message": f"« {code} » introuvable dans le catalogue Ad TYP de l'organisation."}
            cid = item.get("custom_id")
            if not cid:
                master_id = item.get("master_id")
                if not master_id:
                    return {"statut": "echec", "message": f"« {code} » : aucune copie modifiable."}
                r = client.post(f"{TYP_API_URL}/typ/custom/assemblages/from-adflo/{master_id}",
                                json={}, headers=_headers(jwt_token))
                if r.status_code == 409:      # dupliqué entre-temps : on relit
                    item = _trouver(client, jwt_token, code)
                    cid = item and item.get("custom_id")
                elif r.status_code in (200, 201):
                    cid = (r.json() or {}).get("id")
                else:
                    return {"statut": "echec",
                            "message": f"copie de « {code} » dans le catalogue refusée (HTTP {r.status_code})."}
            if not cid:
                return {"statut": "echec", "message": f"« {code} » : copie modifiable introuvable."}
            r = client.patch(f"{TYP_API_URL}/typ/custom/assemblages/{cid}", json=champs,
                             headers=_headers(jwt_token))
            if r.status_code != 200:
                return {"statut": "echec",
                        "message": f"correction de « {code} » refusée par Ad TYP (HTTP {r.status_code})."}
        libelles = {"unite": "unité", "description": "description"}
        quoi = " et ".join(f"{libelles[k]} « {v} »" for k, v in champs.items())
        return {"statut": "corrige", "code": code,
                "message": f"Catalogue Ad TYP corrigé : {code} — {quoi}."}
    except (httpx.HTTPError, RuntimeError) as e:
        logger.warning("correctif catalogue %s échoué : %s", code, e)
        return {"statut": "echec", "message": f"Ad TYP injoignable : {e}"}
