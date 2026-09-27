"""Pure boundary contracts; adapters own IO, durable checkpoints and transport errors."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, Sequence

from pearnly_cowork.ocr.schema import Page, PipelineResult


@dataclass(frozen=True)
class Frame:
    image_bytes: bytes
    mime_type: str
    vision_bytes: bytes | None = None
    vision_mime_type: str | None = None


class PageSource(Protocol):
    page_count: int
    text_pages: Sequence[Page] | None

    def frame(self, page_number: int) -> Frame:
        """Render one physical page, 1-based; repeatable for whole-file fallback."""
        ...


@dataclass(frozen=True)
class ModelRequest:
    stage: str
    page_number: int
    attempt: int
    model: str
    prompt: str
    images: tuple[tuple[bytes, str], ...] = ()
    max_output_tokens: int = 8192
    timeout_s: float = 60
    temperature: float = 0.0
    response_mime: bool = True
    tier: str = ""
    thinking: str = ""  # 空＝按模型的默认档；复核 / 带原文再读用 "LOW"（ADR-013）


@dataclass(frozen=True)
class ModelResponse:
    text: str
    raw: dict[str, Any]
    input_tokens: int
    output_tokens: int
    model: str
    elapsed_ms: int = 0
    finish_reason: str = ""
    checkpoint_key: str = ""


@dataclass(frozen=True)
class VisionResponse:
    page: Page
    raw: dict[str, Any]
    elapsed_ms: int = 0
    checkpoint_key: str = ""


class ModelCall(Protocol):
    def __call__(self, request: ModelRequest) -> ModelResponse: ...


class VisionCall(Protocol):
    def __call__(self, frame: Frame, *, stage: str, page_number: int) -> VisionResponse: ...


@dataclass(frozen=True)
class CallEvidence:
    stage: str
    page_number: int
    attempt: int
    model: str
    raw: dict[str, Any]
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0
    finish_reason: str = ""
    checkpoint_key: str = ""


@dataclass(frozen=True)
class EngineResult:
    pipeline: PipelineResult
    calls: tuple[CallEvidence, ...]


@dataclass(frozen=True)
class EngineConfig:
    """Frozen admission config, explicit model names; never read env inside a replay."""
    direct_model: str
    layer2_model: str
    layer3_model: str
    document_type: str = "invoice"
    mode: str = "economy"
    provider: str = "vertex"
    direct_enabled: bool = True
    max_direct_pages: int = 20
    double_read: bool = False
    image_first: bool = False
    enable_layer3: bool = True  # 3.8 视觉复核（L3）；按站点配置默认关（ADR-013），见 config_from_environment
    fallback_to_layer2: bool = True
    max_escalations: int | None = None
    direct_timeout_s: float = 60
    layer2_timeout_s: float = 60
    layer3_timeout_s: float = 10
    rescue_timeout_s: float = 8
    json_retries: int = 1
    confidence_threshold: float = .85
    amount_tolerance: Decimal = Decimal("0.5")
    auto_threshold: float = .98
    review_threshold: float = .90


class ProviderFailure(Exception):
    """A definite provider refusal with a received result; never Deferred/Uncertain.

    Adapter subclasses this contract only when retry cannot conceal an unknown
    paid result. Authentication is never converted into content fallback.
    """
    def __init__(self, code: str, *, http_status: int | None = None,
                 provider_code: int | None = None):
        self.code = code
        self.http_status = http_status
        self.provider_code = provider_code
        super().__init__(code)
