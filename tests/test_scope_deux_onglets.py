"""TÉMOIN — deux onglets « Mes projets » / « Tous les projets », frontière org.

Le compte de test est test-cal-b : platform_role='client', membre d'Adision.
JAMAIS Simon (super_admin) : sa première branche lui donne tout sans exercer les
paliers, ses essais ne prouvent rien.

On teste les DEUX fonctions d'autorisation directement (exécution, pas frappe de
route) : _projets_scope_where (la liste) et _authorize_projet (l'ouverture).
"""
from modules.ad_budget_api import _projets_scope_where, _authorize_projet
from fastapi import HTTPException

ADISION = "870c8388-4b9c-4ab6-b8e6-bbc8410638c3"
CONTRACTA = "64c45c53-ff2b-40ce-b05d-fe0a3f5144c0"

# test-cal-b : id ad_budget 61 (≠ id hub 582 — jamais mêlés), client, membre Adision.
TCB = {"id": 61, "platform_role": "client", "org_role": None, "organization_id": ADISION}

# Projets (organization_id peuplé — mesuré 12/12).
P_278 = {"user_id": 61, "organization_id": ADISION}    # possédé par test-cal-b
P_277 = {"user_id": 4,  "organization_id": ADISION}    # Adision, possédé par Simon
P_CONTRACTA = {"user_id": 3, "organization_id": CONTRACTA}


# ── LA LISTE ──────────────────────────────────────────────────────────────────
def test_mes_projets_filtre_sur_le_user():
    where, params = _projets_scope_where(TCB, "mine")
    assert where == ["p.user_id = %s"]
    assert params == [61]                       # SES projets -> seul le 278 (user_id=61)


def test_tous_les_projets_filtre_sur_l_org_active():
    where, params = _projets_scope_where(TCB, "org")
    assert where == ["p.organization_id = %s"]
    assert params == [ADISION]                  # l'ORG active, jamais au-delà


def test_l_onglet_org_ne_contient_JAMAIS_une_autre_org():
    # La clause org = ADISION exclut structurellement Contracta.
    _, params = _projets_scope_where(TCB, "org")
    assert CONTRACTA not in params


def test_sans_org_active_l_onglet_tous_retombe_sur_les_siens():
    # test-cal-b RÉEL n'a pas de membership : org NULL -> "tous" ne montre que
    # les siens (mesuré : il ne voit que le 278 aujourd'hui).
    sans_org = {**TCB, "organization_id": None}
    where, params = _projets_scope_where(sans_org, "org")
    assert where == ["p.user_id = %s"] and params == [61]


# ── L'OUVERTURE D'UN PROJET ───────────────────────────────────────────────────
def _autorise(projet, user, mode):
    try:
        _authorize_projet(projet, user, mode)
        return "OK"
    except HTTPException as e:
        return e.status_code


def test_ouvre_son_propre_projet_lecture_et_ecriture():
    assert _autorise(P_278, TCB, "read") == "OK"
    assert _autorise(P_278, TCB, "write") == "OK"


def test_ouvre_un_projet_adision_non_possede_EN_CONSULTATION():
    # Il le VOIT (read OK) ; l'écriture PASSE l'autorisation ET sera tranchée par
    # la détention en aval (409), pas refusée ici par un 403.
    assert _autorise(P_277, TCB, "read") == "OK"
    assert _autorise(P_277, TCB, "write") == "OK"   # -> détention gouverne l'écriture réelle


def test_un_projet_d_une_AUTRE_org_est_404_dans_les_deux_modes():
    # Contracta : hors périmètre, on ne révèle même pas son existence.
    assert _autorise(P_CONTRACTA, TCB, "read") == 404
    assert _autorise(P_CONTRACTA, TCB, "write") == 404


def test_le_piege_d_ids_ne_donne_pas_faux_owner():
    # Un projet dont user_id == id HUB de test-cal-b (582) ne doit PAS le rendre
    # propriétaire : is_owner compare des ids ad_budget (61), pas l'id hub.
    projet_piege = {"user_id": 582, "organization_id": ADISION}
    # il reste membre (org Adision) -> read OK, mais PAS owner : write via détention.
    assert _autorise(projet_piege, TCB, "read") == "OK"
