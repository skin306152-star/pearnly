"""Map an explicit, frozen environment snapshot to engine behavior (no os/env reads)."""
from decimal import Decimal, InvalidOperation
from typing import Mapping, NamedTuple, Optional

from pearnly_cowork.ocr.policy import InvoicePolicy
from pearnly_cowork.ocr.engine.contracts import EngineConfig
from pearnly_cowork.ocr.engine.errors import EngineError

# 看图直读可以选的模型（站点配置 OCR_DIRECT_MODEL）。
DIRECT_MODELS = ("gemini-3.1-flash-lite", "gemini-3.8-flash")
# 识别引擎（站点配置 OCR_ENGINE，ADR-017）：空 / gemini = 现行读法；claude = 看图直读交给 Claude、只读一遍。
GEMINI, CLAUDE = "gemini", "claude"
ENGINES = (GEMINI, CLAUDE)
CLAUDE_MODEL = "claude-sonnet-5-5"
MAX_PAGES = 50  # 引擎一份文件最多读的页数（``runner.run_invoice``）


class DocumentReading(NamedTuple):
    """对账文档（银行 / 总账 / 税报，``enterprise.run``）的读法。``model`` 为空 = 合同里的 3.8；``max_rereads`` 为空 =
    现行上限；``transcribe`` = 先用 Document AI 读字（本地拼行、带转写稿抽取）。"""
    model: Optional[str] = None
    max_rereads: Optional[int] = None
    transcribe: bool = True


def document_reading(environment: Mapping[str, str]) -> DocumentReading:
    """按站点引擎选对账文档的读法。现行：Document AI 读字 → 本地拼行 → 拼不平再 3.8 抽取、自核不过最多再读 10 页。
    Claude（ADR-017）：全部交给 Claude，每页只看图抽取一次（不调 Document AI、不再读），自核不过照旧标出来交人确认。"""
    if engine_of(environment) == CLAUDE:
        return DocumentReading(CLAUDE_MODEL, 0, False)
    return DocumentReading()


def engine_of(environment: Mapping[str, str]) -> str:
    """站点选的识别引擎；认不出的报配置错误（不悄悄回到某一家）。"""
    engine = str(environment.get("OCR_ENGINE", "") or "").strip().lower() or GEMINI
    if engine not in ENGINES:
        raise EngineError("ocr.engine_config_invalid", detail="OCR_ENGINE")
    return engine


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
    # Claude 引擎（ADR-017）：所有识别都交给 Claude、只读一遍、不升 3.8、不叫 Vision。每页都看图直读（带文字层的电子 PDF、
    # 页数多的文件也一样，上限即引擎的 50 页）；Word / Excel 表格没有图，文字交给 Claude（``layer2_model``）。
    # OCR_DIRECT_MODEL 照旧校验但不用，切回 gemini 时原样生效。
    claude = engine_of(environment) == CLAUDE
    return EngineConfig(
        enable_layer3=layer3 in {"1", "true", "yes", "on"} and not claude, single_read=claude,
        direct_model=CLAUDE_MODEL if claude else direct or selected.first_model,
        layer2_model=CLAUDE_MODEL if claude else selected.first_model,
        layer3_model=selected.l3_model, mode=selected.mode, provider=selected.backend,
        direct_enabled=selected.direct_enabled or claude,
        max_direct_pages=MAX_PAGES if claude else selected.direct_max_pages,
        double_read=selected.double_read, image_first=selected.image_first,
        direct_timeout_s=selected.direct_timeout_seconds,
        layer2_timeout_s=integer("OCR_L2_TIMEOUT_SECONDS", "60", low=1, high=120, blank_default=True),
        layer3_timeout_s=integer("OCR_L3_TIMEOUT_SECONDS", "10", low=1, high=120),
        rescue_timeout_s=integer("OCR_TOTALS_RESCUE_TIMEOUT_SECONDS", "8", low=1, high=120),
        confidence_threshold=float(decimal("OCR_PIPELINE_CONF_THRESHOLD", ".85", low=0, high=1)),
        amount_tolerance=decimal("OCR_PIPELINE_AMOUNT_TOL", ".5", low=0),
        auto_threshold=float(auto), review_threshold=float(review),
    )
