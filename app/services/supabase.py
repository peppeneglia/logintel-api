"""
Supabase PostgREST async client.

Thin wrapper over the shared httpx client to interact with Supabase's
PostgREST API. Follows the same init-at-startup pattern as cache.py.

Uses the service_role key (bypasses RLS), so it must only ever run server-side.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.services.http_client import get_client

logger = logging.getLogger(__name__)

_base_url: str = ""
_headers: dict[str, str] = {}
_configured: bool = False


def init_supabase() -> None:
    """Configure PostgREST base URL and auth headers from settings."""
    global _base_url, _headers, _configured

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_key:
        logger.warning("SUPABASE_URL or SUPABASE_SERVICE_KEY not set — Supabase disabled")
        _configured = False
        return

    _base_url = settings.supabase_url.rstrip("/") + "/rest/v1"
    _headers = {
        "apikey": settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    _configured = True
    logger.info("Supabase PostgREST client initialised")


def is_configured() -> bool:
    return _configured


async def select(
    table: str,
    params: dict[str, str] | None = None,
    single: bool = False,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """
    SELECT from a PostgREST table.

    If *single* is True, returns a single dict or None.
    Otherwise returns a list of dicts.
    """
    headers = dict(_headers)
    headers["Accept"] = "application/vnd.pgrst.object+json" if single else "application/json"

    resp = await get_client().get(f"{_base_url}/{table}", params=params or {}, headers=headers)

    # PostgREST answers 406 when a single object was requested but no row matched.
    if single and resp.status_code == 406:
        return None
    resp.raise_for_status()
    return resp.json()


async def count(table: str, params: dict[str, str] | None = None) -> int:
    """Return the exact number of rows matching *params* without transferring them."""
    headers = dict(_headers)
    headers["Prefer"] = "count=exact"

    query = {"select": "id", "limit": "1", **(params or {})}
    resp = await get_client().get(f"{_base_url}/{table}", params=query, headers=headers)
    resp.raise_for_status()

    # Content-Range looks like "0-0/42" (or "*/0" when empty).
    content_range = resp.headers.get("Content-Range", "")
    total = content_range.rsplit("/", 1)[-1]
    return int(total) if total.isdigit() else 0


async def insert(table: str, data: dict[str, Any]) -> dict[str, Any]:
    """INSERT a row into a PostgREST table and return the created row."""
    resp = await get_client().post(f"{_base_url}/{table}", json=data, headers=_headers)
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if isinstance(rows, list) else rows
