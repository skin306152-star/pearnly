"""Endpoint references at API boundaries. No database or framework dependency."""

from typing import Any


def endpoint_name(endpoint: Any) -> str:
    if isinstance(endpoint, str):
        return endpoint.strip()
    if isinstance(endpoint, dict):
        return str(endpoint.get("name") or endpoint.get("id") or "").strip()
    return str(getattr(endpoint, "name", "") or "").strip()
