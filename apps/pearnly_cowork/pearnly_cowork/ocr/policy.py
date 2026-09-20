"""Pure invoice engine selection from the verified legacy runtime contract.

Caller supplies configuration snapshots explicitly: no Frappe, env, database,
credentials or provider calls. This is not a production provider registration.
Source: ec2e5c957e6b engine_policy / gemini_models / providers.vertex.
"""
from dataclasses import dataclass
from typing import Mapping

_MODEL_MAPS = {
    "direct35": {},
    "enterprise": dict.fromkeys(("flash", "flash_lite", "fallback", "escalate"), "gemini-3.8-flash"),
    "economy": {"flash": "gemini-3.8-flash", "flash_lite": "gemini-3.1-flash-lite",
                "fallback": "gemini-3.8-flash", "escalate": "gemini-3.8-flash"},
    "selfhost": {}, "qwen": {},
}
_BACKENDS = {"aistudio", "vertex", "selfhost", "anthropic", "openai", "qwen"}
_MODE_BACKENDS = {"enterprise": "vertex", "selfhost": "selfhost", "qwen": "qwen"}
_DEFAULT_PLANS = dict.fromkeys(("none", "S", "M", "L", "exempt"), "economy")
_ENV_MODELS = {"flash": "OCR_FLASH_MODEL", "flash_lite": "OCR_FLASHLITE_MODEL",
               "fallback": "OCR_FALLBACK_MODEL", "escalate": "OCR_ESCALATE_MODEL"}


def resolve_mode(environment: Mapping[str, str], policy_value=None, *, plan_code=None, is_exempt=False):
    """Invoice only; the legacy setting's enabled flag does not affect selection."""
    forced = environment.get("OCR_ENGINE_MODE", "").strip()
    if forced in _MODEL_MAPS:
        return forced
    config = {"mode": "economy", "overrides_by_task": {}, "defaults_by_plan": _DEFAULT_PLANS}
    if isinstance(policy_value, dict):
        for key, default in config.items():
            if isinstance(policy_value.get(key), type(default)):
                config[key] = policy_value[key]
    mode = config["overrides_by_task"].get("invoice") or config["mode"] or "economy"
    if mode == "auto":
        plan = "exempt" if is_exempt else (plan_code or "none")
        mode = config["defaults_by_plan"].get(plan) or "economy"
    return mode if mode in _MODEL_MAPS else "economy"


def model_for(mode: str, tier: str, environment: Mapping[str, str]):
    """Gemini tier selection only; Qwen/selfhost models belong to their providers."""
    if mode not in _MODEL_MAPS or tier not in _ENV_MODELS:
        raise ValueError("Unknown OCR mode or tier")
    override = _MODEL_MAPS[mode].get(tier)
    if override:
        return override
    if tier == "escalate":
        return environment.get("OCR_ESCALATE_MODEL", "").strip() or model_for(mode, "fallback", environment)
    value = environment.get(_ENV_MODELS[tier], "gemini-3.5-flash").strip()
    return value if tier == "fallback" else value or "gemini-3.5-flash"


def vertex_location(model: str, environment: Mapping[str, str]):
    if model.lower().startswith("gemini-3.8"):
        return "global"
    if model.lower().startswith(("gemini-2.5", "gemini-3.1", "gemini-3.6")):
        return environment.get("VERTEX_LOCATION_25", "global").strip() or "global"
    return environment.get("VERTEX_LOCATION", "asia-southeast1").strip() or "asia-southeast1"


@dataclass(frozen=True)
class InvoicePolicy:
    mode: str
    backend: str
    first_model: str
    fallback_model: str
    l3_model: str
    first_location: str | None
    fallback_location: str | None
    l3_location: str | None
    direct_enabled: bool
    direct_max_pages: int
    direct_timeout_seconds: int
    text_fast_path: bool
    image_first: bool
    double_read: bool
    fewshot_enabled: bool

    def route(self, page_count: int, *, text_path_accepted: bool = False):
        if type(page_count) is not int or page_count < 1:
            raise ValueError("A positive physical page count is required")
        # PDF text extraction runs before image-direct. Caller must verify both
        # the text threshold and equality with the rendered physical page count.
        if self.text_fast_path and text_path_accepted:
            return "pipeline_v1"
        if self.direct_enabled and page_count <= self.direct_max_pages:
            return "qwen_direct" if self.mode == "qwen" else "image_direct"
        return "pipeline_v1"


def invoice_policy(environment: Mapping[str, str], policy_value=None, *, plan_code=None, is_exempt=False):
    """Describe the legacy choice; does not imply a provider is implemented/configured."""
    mode = resolve_mode(environment, policy_value, plan_code=plan_code, is_exempt=is_exempt)
    backend = (environment.get("OCR_LLM_BACKEND") or "aistudio").strip().lower()
    backend = _MODE_BACKENDS.get(mode) or (backend if backend in _BACKENDS else "aistudio")
    # Gemini mappings are not the native models of another provider.
    first = model_for(mode, "flash_lite", environment) if backend in {"vertex", "aistudio"} else ""
    upgrade = model_for(mode, "fallback", environment) if backend in {"vertex", "aistudio"} else ""
    l3 = (upgrade or model_for(mode, "flash", environment)) if backend in {"vertex", "aistudio"} else ""
    return InvoicePolicy(mode=mode, backend=backend, first_model=first, fallback_model=upgrade, l3_model=l3,
        first_location=vertex_location(first, environment) if backend == "vertex" else None,
        fallback_location=vertex_location(upgrade, environment) if backend == "vertex" and upgrade else None,
        l3_location=vertex_location(l3, environment) if backend == "vertex" else None,
        direct_enabled=environment.get("OCR_IMAGE_DIRECT", "1").strip().lower() not in {"0", "false", "no", "off"},
        direct_max_pages=int(environment.get("OCR_IMAGE_DIRECT_MAX_PAGES", "20")),
        direct_timeout_seconds=int(environment.get("OCR_DIRECT_TIMEOUT_SECONDS") or "60"),
        text_fast_path=environment.get("OCR_FAST_PATH_ENABLED", "false").strip().lower() == "true",
        image_first=environment.get("OCR_IMAGE_FIRST", "").strip().lower() in {"1", "true", "yes", "on"},
        double_read=environment.get("OCR_DOUBLE_READ", "0").strip().lower() not in {"0", "false", "no", "off"},
        # Legacy fewshot intentionally does not strip whitespace.
        fewshot_enabled=environment.get("OCR_FEWSHOT_ENABLED", "0").lower() in {"1", "true", "yes"})
