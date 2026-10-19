"""PDF 文字层的纯判据（数字会计员第 3 期第 2 条「文字层 PDF 才能进 A / B」）：不连库、不依赖框架，生产（`history.text_layer`）
与离线回放（`scripts/da-replay.py`）同一条。"""
from __future__ import annotations

import io
import logging
import re
import threading
import unicodedata
from typing import Any, Dict, List, Optional, Sequence

# 内容流里的隐形文字（文字绘制模式 3 Tr）：扫描件、手机扫描 App、复合机「可搜索 PDF」叠在整页图片上的识别文字层
_INVISIBLE_TEXT = re.compile(rb"(?<![\w.])3\s+Tr(?![\w])")

# 坏文字层（第 3 期 E11，BakeLab 电子税票第 2 页起的副本页）：字体的 ToUnicode 映射坏了（十六进制串长度为奇数），pypdf 记一条
# 「Skipping broken line … Odd-length string」、把整行映射丢掉，没映射上的字形码原样当字符给出（\x03\x04…），单号、金额、税号
# 一个都比不上；pymupdf 读同一页数字、单号、税号完好。对得上 Unicode 的文字层里不会有控制字符（制表、换行、换页除外），
# 所以一页里控制字符占可见字符的 `BROKEN_SHARE` 以上（整页 / 整个字体坏：测试窗口实测的副本页几乎全是控制字符，造的样张约 60%；
# 一页里个别没映射的符号字形在 2% 以下），或这份原件 pypdf 报过 ToUnicode 解码错误且这一页有控制字符，就算这一页坏了。
BROKEN_SHARE = 0.1
_CMAP_LOGGER = "pypdf._cmap"
_CMAP_ERRORS = ("Skipping broken line", "Got invalid hex string")
_TEXT_CONTROLS = "\t\n\r\f\v"


def invisible_text(content: Any) -> bool:
    """PDF 一页的内容流（bytes）里有隐形文字（``3 Tr``）：这一页是扫描图片上叠的识别文字层，不算文字层 PDF 的页。"""
    return isinstance(content, (bytes, bytearray)) and bool(_INVISIBLE_TEXT.search(bytes(content)))


def page_text(page: Any) -> str:
    """pypdf 的一页 → 它的文字层；带隐形文字的页给空串（按图片算）。读不出内容流时照常取文字。"""
    try:
        content = page.get_contents()
        raw = content.get_data() if content is not None else b""
    except Exception:  # noqa: BLE001 — 内容流读不出不判隐形文字
        raw = b""
    return "" if invisible_text(raw) else (page.extract_text() or "")


def _control(char: str) -> bool:
    return unicodedata.category(char) == "Cc" and char not in _TEXT_CONTROLS


def control_share(text: Optional[str]) -> float:
    """文字层里控制字符（Unicode Cc 类，制表、换行、回车、换页、竖制表除外）占可见字符（非空白字符加控制字符）的比例；空文字 0。"""
    text = text or ""
    controls = sum(1 for char in text if _control(char))
    if not controls:
        return 0.0
    visible = sum(1 for char in text if _control(char) or not char.isspace())
    return controls / visible


def broken(text: Optional[str], decode_error: bool = False) -> bool:
    """这一页 pypdf 抽出的文字层坏了：控制字符占比到 `BROKEN_SHARE`，或（``decode_error``：这份原件 pypdf 报过 ToUnicode 解码
    错误）这一页有控制字符。正常的页一个控制字符都没有，永远不算坏。"""
    share = control_share(text)
    return share >= BROKEN_SHARE or (decode_error and share > 0)


class _DecodeErrors(logging.Handler):
    """收这个线程里 pypdf 报的 ToUnicode 解码错误（`pypdf._cmap` 的 warning；日志被调高到 warning 以上时收不到，只靠占比判）。"""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.thread, self.seen = threading.get_ident(), False

    def emit(self, record):
        if record.thread == self.thread and str(record.msg).startswith(_CMAP_ERRORS):
            self.seen = True


def _mupdf_texts(content: bytes, numbers: Sequence[int]) -> Dict[int, str]:
    """pymupdf 抽这几页（从 1 数）的文字层；库没有、打不开、某页抽不出都只少那几页，不报错。"""
    found: Dict[int, str] = {}
    try:
        import pymupdf

        with pymupdf.open(stream=content, filetype="pdf") as document:
            for number in numbers:
                try:
                    if 1 <= number <= document.page_count:
                        found[number] = document[number - 1].get_text() or ""
                except Exception:  # noqa: BLE001 — 这一页抽不出就照旧用 pypdf 的
                    continue
    except Exception:  # noqa: BLE001 — pymupdf 不可用 / 打不开就照旧用 pypdf 的
        return found
    return found


def page_texts(content: bytes, numbers: Optional[Sequence[int]] = None,
               fallen: Optional[List[int]] = None) -> List[str]:
    """PDF 原件这几页（``numbers`` 从 1 数、超出页数的不要；不给为全部页）各自的文字层，按 ``numbers`` 的顺序。

    逐页用 pypdf（`page_text`，带隐形文字的页给空串）；只有坏了的页（`broken`）改用 pymupdf 抽同一页，且 pymupdf 抽出的
    有字、控制字符占比更低才换（页码记进 ``fallen``）。pymupdf 不可用或抽不出时这一页照旧是 pypdf 的结果。正常的页不碰
    pymupdf，结果与以前逐字相同。pypdf 打不开原件照旧抛错（调用方按读不到处理）。"""
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(content))
    total = len(reader.pages)
    wanted = [number for number in (range(1, total + 1) if numbers is None else numbers) if 1 <= number <= total]
    watch, logger = _DecodeErrors(), logging.getLogger(_CMAP_LOGGER)
    logger.addHandler(watch)
    try:
        found = [page_text(reader.pages[number - 1]) for number in wanted]
    finally:
        logger.removeHandler(watch)
    bad = [index for index, text in enumerate(found) if broken(text, watch.seen)]
    if not bad:
        return found
    other = _mupdf_texts(content, [wanted[index] for index in bad])
    for index in bad:
        text = other.get(wanted[index]) or ""
        if text.strip() and control_share(text) < control_share(found[index]):
            found[index] = text
            if fallen is not None:
                fallen.append(wanted[index])
    return found


__all__ = ["BROKEN_SHARE", "broken", "control_share", "invisible_text", "page_text", "page_texts"]
