"""Tests de la dependency jwt_super_admin (D2.A étape 2).

jwt_super_admin est testée en l'appelant directement (c'est une fonction
Python ordinaire) plutôt que via une route FastAPI : on vérifie le payload
retourné et les HTTPException levées.
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


def forge_jwt(role, sub="test@adision.ca", email=None, expired=False, modules=None):
    """Forge un JWT HS256 signé avec le secret de test."""
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": email or sub,
        "role": role,
        "modules": modules or [],
        "exp": now - 3600 if expired else now + 3600,
    }
    return pyjwt.encode(payload, TEST_SECRET, algorithm="HS256")


def test_jwt_super_admin_role_ok():
    tok = forge_jwt("super_admin")
    result = jwt_super_admin(authorization=f"Bearer {tok}", token=None)
    assert result["role"] == "super_admin"
    assert result["email"] == "test@adision.ca"
    assert result["sub"] == "test@adision.ca"


def test_jwt_super_admin_role_admin_403():
    tok = forge_jwt("admin")
    with pytest.raises(HTTPException) as exc:
        jwt_super_admin(authorization=f"Bearer {tok}", token=None)
    assert exc.value.status_code == 403
    assert "super_admin" in exc.value.detail


def test_jwt_super_admin_role_user_403():
    tok = forge_jwt("user")
    with pytest.raises(HTTPException) as exc:
        jwt_super_admin(authorization=f"Bearer {tok}", token=None)
    assert exc.value.status_code == 403


def test_jwt_super_admin_no_jwt_401():
    with pytest.raises(HTTPException) as exc:
        jwt_super_admin(authorization=None, token=None)
    assert exc.value.status_code == 401


def test_jwt_super_admin_expired_401():
    tok = forge_jwt("super_admin", expired=True)
    with pytest.raises(HTTPException) as exc:
        jwt_super_admin(authorization=f"Bearer {tok}", token=None)
    assert exc.value.status_code == 401
