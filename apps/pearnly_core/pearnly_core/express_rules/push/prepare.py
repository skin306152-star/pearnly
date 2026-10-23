"""Final pure preparation decision; never creates a queue entry or success receipt."""
from dataclasses import dataclass
from typing import Any, Literal

from pearnly_core.express_rules.contracts.push import require_external_history, resolve_posting_kind
from pearnly_core.express_rules.push.common import ITEMS_INCOMPLETE, ITEMS_MISMATCH
from pearnly_core.express_rules.push.preflight import ExpressContext, Preflight, preflight_express


@dataclass(frozen=True)
class PreparedPush:
    disposition: Literal["ready", "manual", "disabled"]
    reason: str | None
    payload: dict[str, Any] | None
    preflight: Preflight


def prepare_express(endpoint: dict, history: dict, *, context: ExpressContext,
                    posting_kind: str | None = None) -> PreparedPush:
    """Preserve enqueue's internal-source guard and final item gate without IO.

    `ready` only describes the supplied snapshots. A native service must still
    authorize the actor and live target, reserve idempotently, and dispatch.
    """
    require_external_history(history)
    kind = resolve_posting_kind(posting_kind, history)
    result = preflight_express(endpoint, history, context=context, posting_kind=kind)
    if result.disabled:
        return PreparedPush("disabled", "ERR_EXPRESS_DISABLED", {"adapter": "express"}, result)
    if result.blocked:
        return PreparedPush("manual", result.reason, result.request_body, result)
    payload = result.payload
    if payload.get("items_status") in (ITEMS_MISMATCH, ITEMS_INCOMPLETE):
        return PreparedPush("manual", f"items_{payload['items_status']}", payload, result)
    return PreparedPush("ready", None, payload, result)
