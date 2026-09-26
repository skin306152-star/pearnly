# -*- coding: utf-8 -*-
"""双语层:TH/ZH 全 key 齐备 · 占位格式化 · 无甩锅/口语(红线)· 默认语言合法。"""
from src.companion.i18n import LANGS, _M, default_lang, normalize, t


def test_all_keys_have_both_languages():
    for key, (zh, th) in _M.items():
        assert zh.strip() and th.strip(), f"{key} 缺文案"


def test_placeholder_format():
    assert "5" in t("status_found", "zh", n=5)
    assert "5" in t("status_found", "th", n=5)
    assert "X" in t("pair_fail", "zh", err="X")


def test_no_blame_or_colloquial_copy():
    banned = ["是你的责任", "自动干活", "选你自家那个", "你不用碰", "瞎"]
    for key, (zh, th) in _M.items():
        for b in banned:
            assert b not in zh, f"{key} 含甩锅/口语: {b}"


def test_normalize_and_default():
    assert normalize("zh") == "zh" and normalize("th") == "th"
    assert normalize("xx") in LANGS  # 非法 → 默认语言
    assert default_lang() in LANGS


def test_missing_key_returns_key():
    assert t("no_such_key", "zh") == "no_such_key"
