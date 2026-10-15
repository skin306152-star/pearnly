"""Transport-free target inputs. These values do not perform authorization."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


class TargetSelectionError(ValueError):
    """Keep source conflict status/detail as metadata, without an HTTP runtime."""

    def __init__(self, status_code=409, *, detail):
        self.status_code = status_code
        self.detail = detail
        self.code = detail.get("code") if isinstance(detail, dict) else detail
        self.reason = detail.get("reason", "") if isinstance(detail, dict) else ""
        super().__init__(self.code)


@dataclass(frozen=True)
class TargetAccess:
    """Result of a current, server-side permission check; never request JSON.

    The future adapter must check Site/product/feature/actor, document scope,
    endpoint grants and enabled state before constructing this value.
    """

    tenant_id: str
    user_id: str
    endpoint_id: str
    workspace_client_id: int | str | None
    authorized: bool


@dataclass(frozen=True)
class TargetEvidence:
    """Rows from one scoped authoritative read, not a user-supplied catalogue.

    latest_refresh has the source latest endpoint-refresh row. snapshot has
    the requested revision plus current head aliases (snapshot_id,
    head_snapshot_id/head_revision/head_status). The adapter must obtain both
    in the same transaction that reserves the target, with its binding locked.
    """

    tenant_id: str
    user_id: str
    endpoint_id: str
    latest_refresh: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None


@dataclass(frozen=True)
class FrozenTarget:
    """Immutable target receipt; no credential, capability or write success.

    Legacy keys are retained. binding_generation is always 0 and
    bound_profile_key is a read-only compatibility copy of the connection
    field; the managed branch they once described is retired (D9).
    account_config_json is a private, immutable serialization of only the
    selected account fields/mapping slots; each account_config() call returns
    a detached value.
    """

    tenant_id: str
    user_id: str
    endpoint_id: str
    workspace_client_id: int | str | None
    adapter: str
    account_set: str
    root_key: str | None
    binding_generation: int
    bound_profile_key: str
    catalog_refresh_request_id: str | None
    catalog_projection_revision: int | None
    proof_required: bool
    account_config_json: str

    def account_config(self) -> dict:
        return json.loads(self.account_config_json)
