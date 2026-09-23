"""Explicit per-Site invoice configuration, frozen before queue admission."""
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from pearnly_core import ocr
from pearnly_cowork.ocr.policy import invoice_policy
from pearnly_cowork.ocr.engine.config import config_from_environment

STRING_KEYS = {"OCR_ENGINE_MODE", "OCR_LLM_BACKEND", "OCR_FLASH_MODEL", "OCR_FLASHLITE_MODEL",
               "OCR_FALLBACK_MODEL", "OCR_ESCALATE_MODEL", "GCP_PROJECT", "GOOGLE_CLOUD_PROJECT",
               "VERTEX_PROJECT", "VERTEX_LOCATION", "VERTEX_LOCATION_25", "TZ"}
NUMBER_KEYS = set("""OCR_IMAGE_DIRECT OCR_IMAGE_DIRECT_MAX_PAGES OCR_IMAGE_FIRST OCR_FAST_PATH_ENABLED
OCR_DOUBLE_READ OCR_DOUBLE_READ_BIG_THB OCR_IMAGE_FIRST_ESCALATE_CONF OCR_PIPELINE_CONF_THRESHOLD
OCR_PIPELINE_AMOUNT_TOL OCR_CONF_AUTO OCR_CONF_REVIEW OCR_IMG_MAX_LONG_EDGE OCR_DIRECT_TIMEOUT_SECONDS
OCR_L1_TIMEOUT_SECONDS OCR_L2_TIMEOUT_SECONDS OCR_L3_TIMEOUT_SECONDS OCR_TRANSIENT_RETRY OCR_FEWSHOT_ENABLED
OCR_TOTALS_RESCUE_TIMEOUT_SECONDS""".split())


def target():
    return {"backend": ocr.backend(), "project": ocr.project(), "location": ocr.location(),
            "credentials_path": ocr.credentials_path(), "endpoint": ocr.endpoint()}


def snapshot(*, deterministic=False):
    environment = frappe.conf.get("pearnly_cowork_ocr_environment")
    policy = frappe.conf.get("pearnly_cowork_ocr_policy")
    if not isinstance(environment, dict) or not isinstance(policy, dict) or "mode" not in policy:
        frappe.throw(_("Cowork OCR requires an explicit invoice configuration."))
    if (set(environment) - STRING_KEYS - NUMBER_KEYS or any(not isinstance(v, str) for v in environment.values())
            or set(policy) - {"mode", "overrides_by_task", "defaults_by_plan"}):
        frappe.throw(_("Invalid Cowork OCR configuration."))
    environment, policy = deepcopy(environment), deepcopy(policy)
    overrides = policy.get("overrides_by_task", {})
    plans = policy.get("defaults_by_plan", {})
    if (not isinstance(policy.get("mode"), str) or not isinstance(overrides, dict)
            or not isinstance(plans, dict) or any(not isinstance(v, str) for v in (*overrides.values(), *plans.values()))):
        frappe.throw(_("Invalid Cowork OCR configuration."))
    # Plan-aware auto resolution is not wired to this admission yet. Do not
    # silently resolve an actual subscriber as plan 'none' or a typo as economy.
    effective_mode = environment.get("OCR_ENGINE_MODE", "").strip() or overrides.get("invoice") or policy["mode"]
    if effective_mode not in {"economy", "enterprise"}:
        frappe.throw(_("This Cowork OCR mode is not available on the new platform yet."))
    try:
        selected = invoice_policy(environment, policy)
        config_from_environment(environment, selected)
        today = datetime.now(ZoneInfo(environment.get("TZ") or "UTC")).date()
    except (ValueError, TypeError, KeyError) as exc:
        frappe.throw(_("Invalid Cowork OCR configuration."))
    if deterministic:
        return {"environment": environment, "policy": policy, "target": None,
                "mode": selected.mode, "billing_tier": "B" if selected.mode == "economy" else "A",
                "fewshot": False, "anchor_date": today.isoformat(), "schema": 1,
                "input_engine": "roundtrip_workbook"}
    if selected.mode not in {"economy", "enterprise"} or selected.backend != "vertex":
        frappe.throw(_("This Cowork OCR mode is not available on the new platform yet."))
    if selected.double_read or selected.image_first:
        frappe.throw(_("This Cowork OCR mode is not available on the new platform yet."))
    destination = target()
    if not destination["credentials_path"]:
        frappe.throw(_("Cowork OCR requires a configured provider credential reference."))
    if destination["backend"] != selected.backend:
        frappe.throw(_("The invoice configuration does not match the OCR connection."))
    project = environment.get("GCP_PROJECT") or environment.get("GOOGLE_CLOUD_PROJECT") or environment.get("VERTEX_PROJECT")
    if project and project != destination["project"]:
        frappe.throw(_("The invoice configuration does not match the OCR connection."))
    environment["GCP_PROJECT"] = destination["project"]
    environment.setdefault("VERTEX_LOCATION", destination["location"])
    if not 1 <= selected.direct_max_pages <= 50 or not 1 <= selected.direct_timeout_seconds <= 120:
        frappe.throw(_("Invalid Cowork OCR configuration."))
    return {"environment": environment, "policy": policy, "target": destination,
            "mode": selected.mode, "billing_tier": "B" if selected.mode == "economy" else "A",
            "fewshot": selected.fewshot_enabled, "anchor_date": today.isoformat(), "schema": 1}


def revalidate(saved):
    if (saved.get("schema") == 1 and saved.get("input_engine") == "roundtrip_workbook"
            and saved.get("target") is None and saved.get("fewshot") is False):
        return
    if saved.get("schema") != 1 or saved.get("target") != target():
        frappe.throw(_("The OCR connection changed after this task was accepted."))
