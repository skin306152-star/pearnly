# -*- coding: utf-8 -*-
"""四语层:TH/ZH/EN/JA 全 key 齐备 · 占位格式化 · 无甩锅/口语(红线)· 默认语言合法。"""
from src.companion.i18n import LANGS, _M, default_lang, normalize, t


def test_all_keys_have_every_language():
    assert LANGS == ("th", "zh", "en", "ja")
    for key, texts in _M.items():
        assert len(texts) == 4 and all(s.strip() for s in texts), f"{key} 缺文案"


def test_each_language_picks_its_own_column():
    assert t("btn_cancel", "zh") == "取消"
    assert t("btn_cancel", "th") == "ยกเลิก"
    assert t("btn_cancel", "en") == "Cancel"
    assert t("btn_cancel", "ja") == "キャンセル"
    assert t("express_roots_more", "ja", path=r"S:\EXP68", n=3) == r"S:\EXP68 ほか 3 年度"


def test_placeholder_format():
    assert t("express_roots_more", "zh", path=r"S:\EXP68", n=3) == r"S:\EXP68 等 3 个年度"
    assert "X" in t("pair_fail", "zh", err="X")


def test_no_blame_or_colloquial_copy():
    banned = ["是你的责任", "自动干活", "选你自家那个", "你不用碰", "瞎"]
    for key, (zh, *_rest) in _M.items():
        for b in banned:
            assert b not in zh, f"{key} 含甩锅/口语: {b}"


def test_normalize_and_default():
    assert normalize("zh") == "zh" and normalize("th") == "th"
    assert normalize("en") == "en" and normalize("ja") == "ja"
    assert normalize("xx") in LANGS  # 非法 → 默认语言
    assert default_lang() in LANGS


def test_missing_key_returns_key():
    assert t("no_such_key", "zh") == "no_such_key"
