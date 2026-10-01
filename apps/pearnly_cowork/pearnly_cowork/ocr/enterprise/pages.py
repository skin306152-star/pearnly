"""Pinned Document AI request body and page-text extraction (the pure half of the deployed provider).

Source: ec2e5c957e6b ``services/ai_gateway/providers/enterprise.py``: ``PROCESSOR_VERSION``,
``request_body`` and ``page_texts`` kept as deployed; the HTTP call moved to the Cowork provider
client (``ocr.providers.client.ProviderClient.document_ai_call``) so it runs inside the durable
OCR Work with its checkpoint, budget and deferral. The deployed ``Layer1Error`` for an invalid
response becomes a content error of this engine.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from pearnly_cowork.ocr.engine.errors import ContentError

PROCESSOR_VERSION = "pretrained-ocr-v2.1.1-2025-01-31"
# The deployed processor (project ``pearnly``, Singapore); the environment may override both.
PROCESSOR_ID = "6c7dfffac937fcd9"
LOCATION = "asia-southeast1"
# 三个区域按顺序用（用户 2026-09-30：「遇到限流马上转区域，不要原地重试，三个区域灵活调用」）：同一项目、同一份凭据。
# 每个区域一个额度桶，``rpm`` 按控制台当前配额（每个单区域 10 次 / 分钟；新加坡、孟买已申请提额）。
# 只用有 v2.1.1 的区域（2026-10-01 实测：新加坡、孟买、悉尼、法兰克福、伦敦、蒙特利尔有；美国 / 欧洲多区域只有 v2.1）：
# 高峰时转到旧版引擎，同一份账单读出来和低峰时不一样（pB 回归 B07 期初读错一位、B08 少行），用户定读字一致优先。
# 顺序按离泰国由近到远。站点配置 ``pearnly_document_ai_regions``（common_site_config，同样格式的列表）可整体替换，
# 提额后改配置不用发版。
REGIONS = (
    {"location": "asia-southeast1", "processor": "6c7dfffac937fcd9", "version": PROCESSOR_VERSION, "rpm": 10},
    {"location": "asia-south1", "processor": "51ea584b4184e9d7", "version": PROCESSOR_VERSION, "rpm": 10},
    {"location": "australia-southeast1", "processor": "edd296d007f4856c", "version": PROCESSOR_VERSION, "rpm": 10},
    {"location": "europe-west3", "processor": "307623ffb61cf473", "version": PROCESSOR_VERSION, "rpm": 10},
    {"location": "europe-west2", "processor": "535bbd3eff7304aa", "version": PROCESSOR_VERSION, "rpm": 10},
    {"location": "northamerica-northeast1", "processor": "fe5aede8b81c6a04", "version": PROCESSOR_VERSION, "rpm": 10},
)
_REGION_TEXT = __import__("re").compile(r"^[a-z0-9-]{1,40}$")
_VERSION_TEXT = __import__("re").compile(r"^[a-z0-9.-]{1,80}$")
# Paid basic OCR rate, deliberately no free-tier/credit deduction (deployed enterprise_reader).
USD_PER_PAGE = 0.0015
# Deployed 15. Core reads at most 8 MB of a provider response (anything larger ends "outcome unknown"
# with the page paid), and a dense scanned statement page returns about 3 MB with every field or about
# 0.9 MB with ``FIELD_MASK`` (SCB 3-page scan, 2026-09-29: 9.6 MB → 2.8 MB). Denser scans (GIZE BAY, 40
# rows a page) still passed 8 MB at five pages, so two; Document AI bills per page, so smaller reads cost
# the same.
MAX_PAGES_PER_READ = 2
# Only what page_texts and the local reconstruction read; symbols, paragraphs and blocks (enableSymbol in
# the deployed body) made up two thirds of the response. Document AI's own output varies slightly between
# identical calls, with or without the mask.
FIELD_MASK = "text,pages.pageNumber,pages.dimension,pages.layout,pages.lines,pages.tokens,pages.tables"


@dataclass(frozen=True)
class OcrPage:
    number: int
    text: str


def request_body(content: bytes, mime: str) -> dict:
    return {
        "rawDocument": {
            "mimeType": mime,
            "content": base64.b64encode(content).decode("ascii"),
        },
        "imagelessMode": True,
        "processOptions": {
            "ocrConfig": {
                "enableNativePdfParsing": False,
                "enableImageQualityScores": False,
                "enableSymbol": True,
                "premiumFeatures": {
                    "computeStyleInfo": False,
                    "enableMathOcr": False,
                    "enableSelectionMarkDetection": False,
                },
            }
        },
    }


def processor_name(project: str, processor: str = PROCESSOR_ID, location: str = LOCATION,
                   version: str = PROCESSOR_VERSION) -> str:
    if not project or not processor or not location or not version:
        raise ContentError("ocr.enterprise_not_configured")
    return f"projects/{project}/locations/{location}/processors/{processor}/processorVersions/{version}"


def regions(configured=None, environment=None) -> tuple:
    """要用的区域（按优先顺序）。站点配置的列表格式不对就整体不用、回到默认三个区域（不能因为配置写错就读不了字）。
    没有站点列表、但环境变量单独指定了处理器（旧的单区域覆盖 ``ENTERPRISE_OCR_LOCATION`` / ``_PROCESSOR_ID``）：只用那一个。"""
    if isinstance(configured, (list, tuple)) and configured:
        chosen, seen = [], set()
        for item in configured:
            if not isinstance(item, dict):
                return REGIONS
            location, processor = str(item.get("location") or ""), str(item.get("processor") or "")
            version, rpm = str(item.get("version") or PROCESSOR_VERSION), item.get("rpm", 10)
            if (not _REGION_TEXT.match(location) or not _REGION_TEXT.match(processor) or not _VERSION_TEXT.match(version)
                    or type(rpm) is not int or rpm < 1 or location in seen):
                return REGIONS
            seen.add(location)
            chosen.append({"location": location, "processor": processor, "version": version, "rpm": rpm})
        return tuple(chosen)
    environment = environment or {}
    location = str(environment.get("ENTERPRISE_OCR_LOCATION") or "").strip()
    processor = str(environment.get("ENTERPRISE_OCR_PROCESSOR_ID") or "").strip()
    if location or processor:
        return ({"location": location or LOCATION, "processor": processor or PROCESSOR_ID,
                 "version": PROCESSOR_VERSION, "rpm": 10},)
    return REGIONS


def page_texts(payload: dict) -> list[OcrPage]:
    document = payload.get("document") or {}
    text = document.get("text") or ""
    pages = document.get("pages") or []
    result = []
    for index, page in enumerate(pages, 1):
        segments = (page.get("layout") or {}).get("textAnchor", {}).get("textSegments", [])
        chunks = []
        for segment in segments:
            start, end = int(segment.get("startIndex", 0)), int(segment["endIndex"])
            if start < 0 or end < start or end > len(text):
                raise ContentError("ocr.enterprise_invalid_anchor")
            chunks.append(text[start:end])
        # A single-image response is exactly the transcript used by the baseline.
        transcript = text if len(pages) == 1 else "".join(chunks)
        result.append(OcrPage(int(page.get("pageNumber", index)), transcript))
    if not result:
        raise ContentError("ocr.enterprise_no_pages")
    return result
