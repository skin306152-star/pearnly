"""Compose source evidence and account selection over an injected access result."""
from copy import deepcopy
import json

from .choice import endpoint_with_account_choice
from .contracts import FrozenTarget, TargetAccess, TargetEvidence, TargetSelectionError
from .identity import normalize_express_account_key, reported_account_set_roots
from .selection import require_catalog_evidence, resolve_account_choice

_ACCOUNT_FIELDS = (
    "comidyear", "seldb", "account_set", "account_dir", "account_company", "account_set_row", "express_root",
    "revenue_acc", "ar_acc", "vat_output_acc", "fallback_acc", "ap_acc", "vat_input_acc",
)


def _require_consistent_root(endpoint, catalogue, trusted, proof, config):
    """Do not freeze a display-inferred directory as selected-root evidence."""
    proven = normalize_express_account_key(proof.get("root_key"))
    selected = normalize_express_account_key(config.get("express_root"))
    if proof["proof_required"] is False:
        # Source bound_default intentionally requires no directory refresh and
        # may leave proof.root_key empty. Its explicit server configuration is
        # authoritative for this default only; never infer from account_dir.
        bound = normalize_express_account_key((endpoint.get("config") or {}).get("express_root"))
        if not bound:
            proven = ""
        elif proven and proven != bound:
            raise TargetSelectionError(409, detail={"code": "catalog_refresh_invalid", "reason": "frozen_root_mismatch"})
        else:
            proven = proven or bound
    if not proven or not selected:
        reason = "frozen_root_missing"
    elif proven != selected:
        reason = "frozen_root_mismatch"
    else:
        reason = ""
    if not reason and proof["proof_required"]:
        key = normalize_express_account_key(proof["account_set_key"])
        snapshot_root = ""
        for row in catalogue.get("account_sets") or []:
            if isinstance(row, dict) and normalize_express_account_key(row.get("source_id")) == key:
                attributes = row.get("attributes")
                if isinstance(attributes, dict):
                    snapshot_root = normalize_express_account_key(attributes.get("root"))
                break
        reported_root = reported_account_set_roots((endpoint.get("config") or {}).get("reported_account_sets")).get(key, "")
        trusted_root = normalize_express_account_key(trusted.get("root_key")) if isinstance(trusted, dict) else ""
        if proven not in (snapshot_root, reported_root, trusted_root):
            reason = "frozen_root_unproven"
    if reason:
        raise TargetSelectionError(409, detail={"code": "catalog_refresh_invalid", "reason": reason})
    return proven


def freeze_target(endpoint: dict, *, access: TargetAccess, evidence: TargetEvidence,
                  account_set_key=None, trusted_account_config=None,
                  request_id=None, revision=None) -> FrozenTarget:
    """Freeze one source target after current authorization and proof checks.

    This does not probe, refresh, reserve, acquire an external-write lock or
    establish a live Companion device. trusted_account_config has the source
    meaning: a server-validated LINE choice, never arbitrary request config.
    """
    if access.authorized is not True:
        raise TargetSelectionError(403, detail="erp.target_access_denied")
    context = (access.tenant_id, access.user_id, access.endpoint_id)
    if (not all(isinstance(value, str) and value.strip() for value in context)
            or context != (evidence.tenant_id, evidence.user_id, evidence.endpoint_id)
            or str(endpoint.get("id") or "") != access.endpoint_id):
        raise TargetSelectionError(409, detail="erp.target_context_invalid")
    if endpoint.get("enabled") is not True:
        raise TargetSelectionError(409, detail="erp.endpoint_disabled")
    # Only legacy bindings (generation 0) exist; the never-enabled managed
    # shared-agent branch is retired (D9) and never freezes a target.
    if int(endpoint.get("binding_generation") or 0) != 0:
        raise TargetSelectionError(409, detail="erp.target_context_invalid")
    adapter = str(endpoint.get("adapter") or "").strip().lower()
    if adapter not in {"express", "mrerp"}:
        raise TargetSelectionError(409, detail="erp.account_set_unavailable")

    # Both decisions consume one detached read. Changes to caller-owned objects
    # after return cannot alter this receipt; no hidden mutable IO dependency.
    endpoint = deepcopy(endpoint)
    snapshot = deepcopy(evidence.snapshot)
    values = {"tenant_id": access.tenant_id, "user_id": access.user_id,
              "account_set_key": account_set_key, "trusted_account_config": deepcopy(trusted_account_config)}
    proof = require_catalog_evidence(endpoint, **values, request_id=request_id, revision=revision,
                                     latest_refresh=deepcopy(evidence.latest_refresh), snapshot=snapshot)
    catalogue = snapshot or {}
    # The source evidence query accepts raw JSON, while load_state._json
    # decodes the same column before selected_account consumes it.
    if isinstance(catalogue.get("account_sets"), str) and (proof["proof_required"] or trusted_account_config):
        catalogue["account_sets"] = json.loads(catalogue["account_sets"])
    choice = resolve_account_choice(endpoint, **values, state={"snapshot": catalogue})
    selected = endpoint_with_account_choice(endpoint, choice)
    config = selected.get("config") or {}
    root_key = proof.get("root_key")
    if adapter == "mrerp":
        account = f"{str(config.get('comidyear') or '6').strip()}:{str(config.get('seldb') or '1').strip()}"
    else:
        account = str(config.get("account_set") or config.get("account_dir") or "").strip()
    if not account:
        raise TargetSelectionError(409, detail="erp.account_set_unavailable")
    proven_account = str(proof.get("account_set_key") or "").strip()
    selected_account = account.strip()
    if adapter == "express":
        proven_account = normalize_express_account_key(proven_account)
        selected_account = normalize_express_account_key(selected_account)
    if not proven_account or proven_account != selected_account:
        raise TargetSelectionError(409, detail={"code": "catalog_refresh_invalid", "reason": "frozen_account_mismatch"})
    if adapter == "express":
        root_key = _require_consistent_root(endpoint, catalogue, values["trusted_account_config"], proof, config)
    account_config = {key: config[key] for key in _ACCOUNT_FIELDS if key in config}
    return FrozenTarget(
        tenant_id=access.tenant_id, user_id=access.user_id, endpoint_id=access.endpoint_id,
        workspace_client_id=access.workspace_client_id, adapter=adapter, account_set=account,
        root_key=root_key,
        binding_generation=0,
        bound_profile_key=str(endpoint.get("bound_profile_key") or ""),
        catalog_refresh_request_id=proof.get("request_id"),
        catalog_projection_revision=proof.get("revision"), proof_required=proof["proof_required"],
        account_config_json=json.dumps(account_config, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
