import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.auth import AuthError, TokenValidator, get_validator
from app.main import app

TENANT = "11111111-1111-1111-1111-111111111111"
AUDIENCE = "api://donations-test"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=_key.public_key())


def make_token(key=_key, alg="RS256", **overrides):
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": AUDIENCE,
        "tid": TENANT,
        "sub": "user-1",
        "iat": now,
        "nbf": now,
        "exp": now + 600,
        "roles": ["Donations.Admin"],
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": "test"})


@pytest.fixture
def validator():
    return TokenValidator(TENANT, [AUDIENCE], jwks_client=FakeJwks())


def test_valid_v2_token(validator):
    assert validator.validate(make_token())["sub"] == "user-1"


def test_valid_v1_issuer(validator):
    validator.validate(make_token(iss=f"https://sts.windows.net/{TENANT}/"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "api://someone-else"},
        {"iss": "https://login.microsoftonline.com/other-tenant/v2.0"},
        {"tid": "22222222-2222-2222-2222-222222222222"},
        {"exp": int(time.time()) - 10},
        {"nbf": int(time.time()) + 3600},
    ],
)
def test_rejects_bad_claims(validator, overrides):
    with pytest.raises(AuthError):
        validator.validate(make_token(**overrides))


def test_rejects_wrong_signature(validator):
    with pytest.raises(AuthError):
        validator.validate(make_token(key=_other_key))


def test_rejects_hs256(validator):
    token = make_token(key="x" * 32, alg="HS256")
    with pytest.raises(AuthError):
        validator.validate(token)


@pytest.fixture
def client():
    app.dependency_overrides[get_validator] = lambda: TokenValidator(
        TENANT, [AUDIENCE], jwks_client=FakeJwks()
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_admin_endpoint_requires_token(client):
    assert client.get("/api/donations").status_code == 401


def test_admin_endpoint_requires_role(client):
    headers = {"Authorization": f"Bearer {make_token(roles=[])}"}
    assert client.get("/api/donations", headers=headers).status_code == 403


def test_admin_endpoint_with_role(client):
    headers = {"Authorization": f"Bearer {make_token()}"}
    response = client.get("/api/donations", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)
