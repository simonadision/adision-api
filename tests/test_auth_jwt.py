"""Tests de la dependency jwt_super_admin (D2.A étape 2).

jwt_super_admin est testée en l'appelant directement (c'est une fonction
Python ordinaire) plutôt que via une route FastAPI : on vérifie le payload
retourné et les HTTPException levées.

MIS À JOUR le 2 oct 2026 (PC2) -- le banc ne tournait nulle part et ROUGISSAIT
pour deux changements de NOTRE code qu'il n'avait jamais suivis (pas une
bibliothèque) :
  - HOTFIX SÉCURITÉ a12d743 (2 juil. 2026) : la garde lit `platform_role`,
    plus le `role` legacy. Le banc forgeait role="super_admin" seul -> 403.
    Ce rouge était la PREUVE que le correctif tient ; il devient ici un test
    qui le VERROUILLE (test_role_legacy_seul_refuse).
  - SSO 18fe2db (27 juin) : paramètre `session_cookie = Cookie(None, ...)`.
    Appelée DIRECTEMENT, la dépendance reçoit l'objet Cookie() par défaut au
    lieu de None (en vraie requête, FastAPI injecte None). D'où
    session_cookie=None, passé explicitement par _appel().
"""
import os
import time

import pytest
import jwt as pyjwt
from fastapi import HTTPException

# Le secret DOIT être posé avant tout import qui déclenche _get_jwt_secret().
TEST_SECRET = "pytest-secret-key-do-not-use-in-prod"
os.environ["JWT_SECRET"] = TEST_SECRET

from modules.auth_jwt import make_jwt_deps


def _fake_get_conn():
    # jwt_super_admin ne doit JAMAIS toucher la DB (pas de _provision_user).
    raise AssertionError("jwt_super_admin ne doit pas ouvrir de connexion DB")


# make_jwt_deps retourne (jwt_user, jwt_user_or_token, jwt_admin, jwt_super_admin)
_, _, _, jwt_super_admin = make_jwt_deps(_fake_get_conn)


def forge_jwt(role, platform_role=None, sub="test@adision.ca", email=None,
              expired=False, modules=None):
    """Forge un JWT HS256 signé avec le secret de test. `platform_role` est
    la claim que la garde LIT depuis le 2 juillet ; `role` est le legacy."""
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": email or sub,
        "role": role,
        "modules": modules or [],
        "exp": now - 3600 if expired else now + 3600,
    }
    if platform_role is not None:
        payload["platform_role"] = platform_role
    return pyjwt.encode(payload, TEST_SECRET, algorithm="HS256")


def _appel(authorization):
    # session_cookie=None EXPLICITE : appelée hors requête, la dépendance
    # recevrait sinon l'objet Cookie(...) par défaut.
    return jwt_super_admin(authorization=authorization, token=None, session_cookie=None)


def test_jwt_super_admin_role_ok():
    tok = forge_jwt("super_admin", platform_role="super_admin")
    result = _appel(f"Bearer {tok}")
    assert result["platform_role"] == "super_admin"
    assert result["email"] == "test@adision.ca"
    assert result["sub"] == "test@adision.ca"


def test_role_legacy_seul_refuse():
    """VERROU du hotfix du 2 juillet : un jeton qui ne porte QUE le role
    legacy « super_admin » est refusé. Mesuré par PC3 le 2 oct : aucune ligne
    de app_central.users n'a platform_role NULL, donc un tel jeton ne peut
    plus être émis -- rien, jusqu'ici, ne disait que la porte était fermée."""
    tok = forge_jwt("super_admin", platform_role=None)
    with pytest.raises(HTTPException) as exc:
        _appel(f"Bearer {tok}")
    assert exc.value.status_code == 403


def test_role_legacy_super_admin_ne_suffit_pas_si_platform_role_client():
    # Les DEUX claims présentes : c'est bien platform_role qui décide.
    tok = forge_jwt("super_admin", platform_role="client")
    with pytest.raises(HTTPException) as exc:
        _appel(f"Bearer {tok}")
    assert exc.value.status_code == 403


def test_jwt_super_admin_role_admin_403():
    # platform_role présent : on teste le RÔLE, pas l'absence de claim.
    tok = forge_jwt("admin", platform_role="staff")
    with pytest.raises(HTTPException) as exc:
        _appel(f"Bearer {tok}")
    assert exc.value.status_code == 403
    assert "super_admin" in exc.value.detail


def test_jwt_super_admin_role_user_403():
    tok = forge_jwt("user", platform_role="client")
    with pytest.raises(HTTPException) as exc:
        _appel(f"Bearer {tok}")
    assert exc.value.status_code == 403


def test_jwt_super_admin_no_jwt_401():
    with pytest.raises(HTTPException) as exc:
        _appel(None)
    assert exc.value.status_code == 401


def test_jwt_super_admin_expired_401():
    tok = forge_jwt("super_admin", platform_role="super_admin", expired=True)
    with pytest.raises(HTTPException) as exc:
        _appel(f"Bearer {tok}")
    assert exc.value.status_code == 401
