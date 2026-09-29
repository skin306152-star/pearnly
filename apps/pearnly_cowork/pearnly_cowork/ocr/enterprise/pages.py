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
# Paid basic OCR rate, deliberately no free-tier/credit deduction (deployed enterprise_reader).
USD_PER_PAGE = 0.0015
MAX_PAGES_PER_READ = 15


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


def processor_name(project: str, processor: str = PROCESSOR_ID, location: str = LOCATION) -> str:
    if not project or not processor or not location:
        raise ContentError("ocr.enterprise_not_configured")
    return f"projects/{project}/locations/{location}/processors/{processor}/processorVersions/{PROCESSOR_VERSION}"


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
