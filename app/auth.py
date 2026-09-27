from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings


class AuthError(Exception):
    pass


class TokenValidator:
    """Validates Microsoft Entra ID (Azure AD) access tokens, v1 or v2."""

    def __init__(self, tenant_id: str, audiences: list[str], jwks_client: Any = None):
        self.tenant_id = tenant_id
        self.audiences = audiences
        self.issuers = [
            f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            f"https://sts.windows.net/{tenant_id}/",
        ]
        self.jwks_client = jwks_client or jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
            cache_keys=True,
        )

    def validate(self, token: str) -> dict[str, Any]:
        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self.audiences,
                issuer=self.issuers,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(str(exc)) from exc
        if claims.get("tid") != self.tenant_id:
            raise AuthError("Token was issued for a different tenant")
        return claims


@lru_cache
def get_validator() -> TokenValidator:
    s = get_settings()
    if not s.azure_tenant_id or not s.audiences:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Azure AD is not configured")
    return TokenValidator(s.azure_tenant_id, s.audiences)


_bearer = HTTPBearer(auto_error=False)


def require_admin(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    validator: TokenValidator = Depends(get_validator),
) -> dict[str, Any]:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if creds is None:
        raise unauthorized
    try:
        claims = validator.validate(creds.credentials)
    except AuthError:
        raise unauthorized
    if get_settings().azure_admin_role not in claims.get("roles", []):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing required role")
    return claims
