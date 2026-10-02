"""Map an explicit, frozen environment snapshot to engine behavior (no os/env reads)."""
from decimal import Decimal, InvalidOperation
from typing import Mapping

from pearnly_cowork.ocr.policy import InvoicePolicy
from pearnly_cowork.ocr.engine.contracts import EngineConfig
from pearnly_cowork.ocr.engine.errors import EngineError

# 看图直读可以选的模型（站点配置 OCR_DIRECT_MODEL）。
DIRECT_MODELS = ("gemini-3.1-flash-lite", "gemini-3.8-flash")


def config_from_environment(environment: Mapping[str, str], selected: InvoicePolicy) -> EngineConfig:
    """Only migrated invoice paths are accepted; unsupported switches fail at admission.

    IO consumes L1 timeout and image edge; Core replaces the old whole-page
    transient retry. An explicit nondefault retry count is not silently ignored.
    Double-read/image-first suboptions have no effect while their parent is off,
    exactly as in the source; enabled parent modes are rejected.
    """
    def decimal(key, default, *, low=None, high=None):
        try:
            value = Decimal(environment.get(key, default))
        except (ValueError, TypeError, InvalidOperation) as exc:
            raise EngineError("ocr.engine_config_invalid", detail=key) from exc
        if not value.is_finite() or low is not None and value < low or high is not None and value > high:
            raise EngineError("ocr.engine_config_invalid", detail=key)
        return value

    def integer(key, default, *, low=None, high=None, blank_default=False):
        try:
            raw = environment.get(key, default)
            value = int((raw or default) if blank_default else raw)
        except (ValueError, TypeError) as exc:
            raise EngineError("ocr.engine_config_invalid", detail=key) from exc
        if low is not None and value < low or high is not None and value > high:
            raise EngineError("ocr.engine_config_invalid", detail=key)
        return value

    if (selected.mode not in ("economy", "enterprise") or selected.backend != "vertex"
            or selected.double_read or selected.image_first):
        raise EngineError("ocr.engine_mode_not_supported")
    if integer("OCR_TRANSIENT_RETRY", "1") != 1:
        raise EngineError("ocr.engine_mode_not_supported", detail="OCR_TRANSIENT_RETRY")
    # IO options validated here before admission, consumed by their owning adapters.
    integer("OCR_L1_TIMEOUT_SECONDS", "60", low=1, high=120, blank_default=True)
    integer("OCR_IMG_MAX_LONG_EDGE", "2400", low=0)
    auto = decimal("OCR_CONF_AUTO", "0.98", low=0, high=1)
    review = decimal("OCR_CONF_REVIEW", "0.90", low=0, high=1)
    if review > auto or not 1 <= selected.direct_max_pages <= 50 or not 1 <= selected.direct_timeout_seconds <= 120:
        raise EngineError("ocr.engine_config_invalid")
    # 3.8 视觉复核（L3 与其金额补读）：ADR-013 起默认关，站点显式 OCR_LAYER3_ENABLED=1 才开（用户 2026-09-27）。
    layer3 = environment.get("OCR_LAYER3_ENABLED", "0").strip().lower()
    if layer3 not in {"", "0", "1", "false", "true", "no", "yes", "off", "on"}:
        raise EngineError("ocr.engine_config_invalid", detail="OCR_LAYER3_ENABLED")
    # 看图直读用的模型（站点配置 OCR_DIRECT_MODEL，只认登记的；不设照旧用策略的第一档）。2026-10-02 用户同意扫描件 / 照片改用
    # 3.8-flash（eval7b 金标四项达标）：先只在 testcowork 打开压测，全过再逐站打开；回退＝把站点配置改回去。电子 PDF 走文字层不受影响。
    direct = environment.get("OCR_DIRECT_MODEL", "").strip()
    if direct and direct not in DIRECT_MODELS:
        raise EngineError("ocr.engine_config_invalid", detail="OCR_DIRECT_MODEL")
    return EngineConfig(
        enable_layer3=layer3 in {"1", "true", "yes", "on"},
        direct_model=direct or selected.first_model, layer2_model=selected.first_model,
        layer3_model=selected.l3_model, mode=selected.mode, provider=selected.backend,
        direct_enabled=selected.direct_enabled, max_direct_pages=selected.direct_max_pages,
        double_read=selected.double_read, image_first=selected.image_first,
        direct_timeout_s=selected.direct_timeout_seconds,
        layer2_timeout_s=integer("OCR_L2_TIMEOUT_SECONDS", "60", low=1, high=120, blank_default=True),
        layer3_timeout_s=integer("OCR_L3_TIMEOUT_SECONDS", "10", low=1, high=120),
        rescue_timeout_s=integer("OCR_TOTALS_RESCUE_TIMEOUT_SECONDS", "8", low=1, high=120),
        confidence_threshold=float(decimal("OCR_PIPELINE_CONF_THRESHOLD", ".85", low=0, high=1)),
        amount_tolerance=decimal("OCR_PIPELINE_AMOUNT_TOL", ".5", low=0),
        auto_threshold=float(auto), review_threshold=float(review),
    )
