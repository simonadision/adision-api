"""Tests de la rotation gracieuse de _decode_token (adision-api / Ad BUD).

JWT_SECRET et JWT_SECRET_OLD sont lus dynamiquement (os.environ.get) à
chaque appel — `monkeypatch.setenv` / `delenv` suffit à les contrôler.

_decode_token traduit les erreurs en HTTPException 401 — les tests
vérifient la HTTPException et son `detail`.
"""
import time

import jwt
import pytest
from fastapi import HTTPException

from modules.auth_jwt import _decode_token

SECRET_CURRENT = "current-secret-pour-tests-au-moins-32-octets-ok"
SECRET_OLD = "old-secret-rotation-precedente-au-moins-32-octets"
SECRET_THIRD = "secret-tiers-inconnu-ni-courant-ni-old-32-octets"


def make_token(secret: str, exp_offset: int = 3600) -> str:
    now = int(time.time())
    return jwt.encode(
        {"user_id": 1, "email": "test@adision.ca", "role": "admin",
         "exp": now + exp_offset},
        secret, algorithm="HS256",
    )


def test_1_token_secret_courant(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET_CURRENT)
    monkeypatch.delenv("JWT_SECRET_OLD", raising=False)
    payload = _decode_token(make_token(SECRET_CURRENT))
    assert payload["email"] == "test@adision.ca"


def test_2_fallback_secret_old_configure(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET_CURRENT)
    monkeypatch.setenv("JWT_SECRET_OLD", SECRET_OLD)
    payload = _decode_token(make_token(SECRET_OLD))
    assert payload["email"] == "test@adision.ca"


def test_3_token_old_mais_secret_old_absent(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET_CURRENT)
    monkeypatch.delenv("JWT_SECRET_OLD", raising=False)
    with pytest.raises(HTTPException) as exc:
        _decode_token(make_token(SECRET_OLD))
    assert exc.value.status_code == 401
    assert "invalide" in exc.value.detail.lower()


def test_4_token_expire_jamais_de_retry(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET_CURRENT)
    monkeypatch.setenv("JWT_SECRET_OLD", SECRET_OLD)
    with pytest.raises(HTTPException) as exc:
        _decode_token(make_token(SECRET_CURRENT, exp_offset=-3600))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Token expiré, reconnexion nécessaire"


def test_5_token_malforme(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET_CURRENT)
    monkeypatch.setenv("JWT_SECRET_OLD", SECRET_OLD)
    with pytest.raises(HTTPException) as exc:
        _decode_token("abc.def.ghi")
    assert exc.value.status_code == 401
    assert "invalide" in exc.value.detail.lower()


def test_6_token_secret_tiers_inconnu(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET_CURRENT)
    monkeypatch.setenv("JWT_SECRET_OLD", SECRET_OLD)
    with pytest.raises(HTTPException) as exc:
        _decode_token(make_token(SECRET_THIRD))
    assert exc.value.status_code == 401
    assert "invalide" in exc.value.detail.lower()
