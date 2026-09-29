"""带文字层的电子 PDF 不付费识别：用文字层的字和坐标拼出与 Document AI 同形的页，交给企业档同一套本地拼行。

为什么（2026-09-30 压测 B02 TTB）：有的银行电子账单文字层不按行输出——日期、摘要、金额、余额各是一块，按行解析
一行都拼不出，于是整份 29 页每次都走付费识别（Document AI + 3.8 逐页抽取），负载下还读超时。字与坐标在文字层里
都是准的，按坐标拼行正是企业档本地拼行做的事；余额链算得平才用（与识别路同一个判据），算不平返回 None，
照旧走识别。旧线上这类文件也是付费识别（文字行解析行数不足就交模型），这是有意改进。

只读传入的 bytes：不调服务商、不打网络、不花钱。
"""
from __future__ import annotations

import time
from typing import Optional

from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.enterprise import pipeline

MAX_PAGES = 200


def payload(content: bytes) -> Optional[tuple]:
    """PDF 文字层 → ``(Document AI 形状的响应, 页数)``；没有文字层、打不开或页数过多回 None。"""
    import pymupdf

    if b"%PDF" not in content[:1024]:
        return None  # 照片等：不当 PDF 打开
    try:
        document = pymupdf.open(stream=content, filetype="pdf")
    except Exception:
        return None
    with document:
        if not 0 < document.page_count <= MAX_PAGES:
            return None
        text, pages = [], []
        cursor = 0
        for page in document:
            width, height = float(page.rect.width) or 1.0, float(page.rect.height) or 1.0
            tokens, lines = [], {}
            for x0, y0, x1, y1, word, block, line, _number in page.get_text("words", sort=True):
                word = str(word or "").strip()
                if not word:
                    continue
                start = cursor
                text.append(word + " ")
                cursor += len(word) + 1
                segment = {"startIndex": start, "endIndex": start + len(word)}
                vertices = [{"x": x0 / width, "y": y0 / height}, {"x": x1 / width, "y": y0 / height},
                            {"x": x1 / width, "y": y1 / height}, {"x": x0 / width, "y": y1 / height}]
                tokens.append({"layout": {"textAnchor": {"textSegments": [segment]},
                                          "boundingPoly": {"normalizedVertices": vertices}, "confidence": 1.0}})
                lines.setdefault((block, line), []).append((segment, vertices))
            text.append("\n")
            cursor += 1
            page_lines = []
            for members in lines.values():
                xs = [vertex["x"] for _segment, vertices in members for vertex in vertices]
                ys = [vertex["y"] for _segment, vertices in members for vertex in vertices]
                page_lines.append({"layout": {"textAnchor": {"textSegments": [segment for segment, _v in members]},
                                              "boundingPoly": {"normalizedVertices": [
                                                  {"x": min(xs), "y": min(ys)}, {"x": max(xs), "y": min(ys)},
                                                  {"x": max(xs), "y": max(ys)}, {"x": min(xs), "y": max(ys)}]}}})
            pages.append({"dimension": {"width": width, "height": height}, "tokens": tokens, "lines": page_lines})
        if not any(page["tokens"] for page in pages):
            return None
        return {"document": {"text": "".join(text), "pages": pages}}, len(pages)


def read(content: bytes, document_type: str) -> Optional[dict]:
    """文字层按坐标拼行、余额链算得平：回与识别同形的旧线上结果（``text_layer`` 标着没花钱）；否则 None。"""
    category = pipeline.CATEGORIES.get(document_type)
    if category not in ("bank", "gl"):
        return None
    built = payload(content)
    if built is None:
        return None
    body, page_count = built
    started = time.monotonic()
    try:
        result = pipeline.local_only([body], category, page_count)
    except ContentError:
        return None
    if result is None:
        return None
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    legacy = pipeline.to_legacy(result)
    legacy["text_layer"] = True
    return legacy
