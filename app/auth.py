"""
Authentication dependency — validates a Supabase JWT or an API key.

When Supabase is not configured (SUPABASE_URL empty) every request is
authenticated as a local development organization. The application refuses
to start in that state when APP_ENV=production (see app.main).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import jwt
from fastapi import Request

from app.config import get_settings
from app.errors import UnauthorizedError
from app.logging_config import org_id_var
from app.services import supabase

logger = logging.getLogger(__name__)

# Audience claim of Supabase access tokens for signed-in users
JWT_AUDIENCE = "authenticated"


@dataclass
class OrgContext:
    org_id: str
    org_name: str
    tier: str
    rate_limit_hour: int
    predictions_limit_month: int


_DEV_ORG = OrgContext(
    org_id="dev",
    org_name="Development",
    tier="professional",
    rate_limit_hour=1000,
    predictions_limit_month=10000,
)


async def get_current_org(request: Request) -> OrgContext:
    """FastAPI dependency that returns the authenticated org context."""
    settings = get_settings()

    # Development bypass — no Supabase configured
    if not settings.supabase_url:
        org_id_var.set("dev")
        return _DEV_ORG

    # Try Bearer JWT first
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return await _validate_jwt(token, settings.supabase_jwt_secret)

    # Try API key
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        return await _validate_api_key(api_key)

    raise UnauthorizedError("Missing authentication: provide Authorization Bearer or X-API-Key header")


async def _validate_jwt(token: str, secret: str) -> OrgContext:
    """Decode JWT, extract org_id from app_metadata, fetch org from DB."""
    if not secret:
        # Never verify HMAC signatures against an empty key.
        logger.error("SUPABASE_JWT_SECRET is not set — rejecting bearer token")
        raise UnauthorizedError("Bearer authentication is not available")

    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"], audience=JWT_AUDIENCE)
    except jwt.InvalidTokenError as exc:
        logger.info("JWT rejected: %s", exc)
        raise UnauthorizedError("Invalid or expired token") from exc

    app_metadata = payload.get("app_metadata", {})
    org_id = app_metadata.get("org_id", "")
    if not org_id:
        raise UnauthorizedError("JWT missing org_id in app_metadata")

    return await _fetch_org(org_id)


async def _validate_api_key(key: str) -> OrgContext:
    """Hash the key, look up in api_keys table, return org context."""
    key_hash = hashlib.sha256(key.encode()).hexdigest()

    row = await supabase.select(
        "api_keys",
        params={
            "key_hash": f"eq.{key_hash}",
            "is_active": "eq.true",
            "select": "organization_id",
        },
        single=True,
    )

    if row is None:
        raise UnauthorizedError("Invalid API key")

    return await _fetch_org(row["organization_id"])


async def _fetch_org(org_id: str) -> OrgContext:
    """Fetch organization details from Supabase."""
    row = await supabase.select(
        "organizations",
        params={"id": f"eq.{org_id}", "select": "*"},
        single=True,
    )

    if row is None:
        raise UnauthorizedError("Organization not found")

    org = OrgContext(
        org_id=row["id"],
        org_name=row["name"],
        tier=row["tier"],
        rate_limit_hour=row.get("rate_limit_hour", 100),
        predictions_limit_month=row.get("predictions_limit_month", 1000),
    )
    org_id_var.set(org.org_id)
    return org
