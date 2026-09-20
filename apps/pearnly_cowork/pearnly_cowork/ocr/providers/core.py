"""Bind invoice adapters to Core's credentials, budget and durable paid-call log."""
import frappe

from pearnly_core import ocr
from pearnly_core.execution import budget, provider, work
from pearnly_cowork.ocr.providers.client import ProviderClient, ProviderConfigurationError


def core_identity():
    """References only. Never return an access token or read credential contents."""
    return {"backend": ocr.backend(), "project": ocr.project(), "location": ocr.location(),
            "credentials_path": ocr.credentials_path()}


def build_client(environment, *, core_identity):
    """Use a frozen nonsecret environment and admission-time Core references.

    Only Vertex is migrated in this slice. No model or feature selection is
    re-read from Site config; the engine supplies the frozen actual model.
    A caller must still recheck source/actor access immediately before callback.
    """
    frozen = dict(core_identity)
    if (frozen.get("backend") != "vertex" or not frozen.get("project")
            or not frozen.get("location") or not frozen.get("credentials_path")):
        raise ProviderConfigurationError("ocr.provider_not_configured")

    def require_context():
        name = frappe.flags.get("pearnly_ocr_work")
        if not isinstance(name, str) or not name:
            raise ProviderConfigurationError("ocr.durable_work_required")
        current = {"backend": ocr.backend(), "project": ocr.project(), "location": ocr.location(),
                   "credentials_path": ocr.credentials_path()}
        if current != frozen:
            raise ProviderConfigurationError("ocr.provider_configuration_changed")

    def terminal_read(key):
        saved = work.checkpoint("cowork.terminal:" + key)
        if saved is None:
            # Core commits its rejection before this adapter can commit full
            # refusal evidence. A worker may die in that small gap. Never pay
            # again for that completed, non-retryable response merely because
            # the product envelope was not yet stored.
            checkpoint = work.checkpoint(key)
            if checkpoint and checkpoint.get("state") == "rejected":
                body = checkpoint.get("body") or {}
                status = body.get("http")
                if status not in (429, 503) and not body.get("deferred"):
                    return {"refusal": {"code": "ocr.provider_invalid_response" if status == 200 else
                            "ocr.provider_auth" if status in (401, 403) else "ocr.provider_http_error",
                            "http_status": status, "provider_code": body.get("provider_code")},
                            "raw": None, "recovered_from_core_rejection": True}
            return None
        if saved.get("state") != "terminal":
            raise ProviderConfigurationError("ocr.invalid_provider_checkpoint")
        return saved["body"]

    return ProviderClient(environment, project=frozen["project"], post=provider.post,
        checkpoint_key=work.checkpoint_key,
        reject_checkpoint=lambda key, body: work.checkpoint(key, "rejected", body),
        terminal_read=terminal_read,
        terminal_write=lambda key, body: work.checkpoint("cowork.terminal:" + key, "terminal", body),
        feedback=budget.feedback, scope=budget.scope, access_token=ocr.access_token,
        require_context=require_context)
