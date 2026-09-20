"""Deployed Thai catalog-name skeleton (not OCR field normalization)."""
import re

_THAI_MARKS = re.compile(r"[ัิ-ฺ็-๎\s]")


def skeleton(text: str) -> str:
    """表头骨架:小写 + 剥泰文组合符号与空白。PDF 文字层的声调错位只动符号不动辅音骨架。"""
    return _THAI_MARKS.sub("", str(text or "").lower())
