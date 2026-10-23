"""Prior-write evidence keys; the caller authorizes and resolves them, never this layer."""


def prior_history_id(history, fields=None):
    """Roundtrip original key precedes the new import history, as in production.

    This is an untrusted lookup candidate, not permission to read that history.
    Tenant/client/company authorization remains mandatory before resolving it.
    """
    return str((fields or {}).get("history_id") or "").strip() or (history or {}).get("id")


def attach_prior_docnum(payload, prior_docnum):
    """Attach only the authorized ledger result supplied by the service."""
    if prior_docnum:
        payload["prior_docnum"] = prior_docnum
    return payload
