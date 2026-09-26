# -*- coding: utf-8 -*-
"""T2 账套客户自选 + 锁定所选账套(端侧):去 DATAT 白名单 · 去 Owner 审批 · fail-safe。

闸 = 写盘目标须 == 客户所选账套(目录 + 账套名);缺所选 → 拒(没选不能瞎写)。
"""

import sys

import pytest

from src.companion.dbf_schema import path_ambiguity
from src.companion.dbf_writer import account_dir_allowed
from src.companion.purchase_adapter import (
    ERR_ACCOUNT_SET_NOT_ALLOWED,
    allowed_account_sets,
    validate_purchase_payload,
)

DATAT_DIR = r"\\accserver\ACCOUNT\70EXP\test"
OTHER_DIR = r"\\accserver\ACCOUNT\70EXP\58asiasp"


def _payload(account_set):
    return {
        "doctype": "RR",
        "account_set": account_set,
        "docdate_be": "581220",
        "vat_period_be": "581201",
        "ref_no": "X1",
        "supplier": {"code": "ป011", "name": "ปตท"},
        "base_amount": "100",
        "vat_amount": "7",
        "total_amount": "107",
        "lines": [
            {"acc": "11-04", "side": "D", "amount": "100"},
            {"acc": "11-05", "side": "D", "amount": "7"},
            {"acc": "21-02", "side": "C", "amount": "107"},
        ],
    }


# ── 所选账套名期望值 ─────────────────────────────────────────
def test_allowed_account_sets_is_selection_no_datat_default():
    assert allowed_account_sets("") == ()  # 缺所选 → 空(fail-safe·非回落 DATAT)
    assert allowed_account_sets("  ") == ()
    assert allowed_account_sets("58ASIASP") == ("58ASIASP",)  # 所选即唯一放行值


def test_validate_matches_selection():
    sel = allowed_account_sets("58ASIASP")
    assert validate_purchase_payload(_payload("58ASIASP"), allowed=sel).ok
    bad = validate_purchase_payload(_payload("DATAT"), allowed=sel)
    assert bad.error_code == ERR_ACCOUNT_SET_NOT_ALLOWED  # 选 58ASIASP 却来 DATAT → 拒


def test_validate_failsafe_when_no_selection():
    # 缺所选 → 任何账套都拒(默认 allowed=()),DATAT 也不再特殊放行
    assert (
        validate_purchase_payload(_payload("DATAT")).error_code
        == ERR_ACCOUNT_SET_NOT_ALLOWED
    )


# ── 写盘目录闸(C2)──────────────────────────────────────────
@pytest.mark.skipif(sys.platform != "win32", reason="Windows 路径 / 系统接口")
def test_account_dir_exact_match_to_selected():
    assert account_dir_allowed(OTHER_DIR, selected_dir=OTHER_DIR) is True
    # 归一:正斜杠/大小写差异仍匹配
    assert (
        account_dir_allowed(
            "//accserver/ACCOUNT/70EXP/58ASIASP", selected_dir=OTHER_DIR
        )
        is True
    )
    # 所选 X 却写 Y → 拒(治"选 A 写 B")
    assert account_dir_allowed(DATAT_DIR, selected_dir=OTHER_DIR) is False


def test_account_dir_failsafe_when_no_selection():
    assert (
        account_dir_allowed(DATAT_DIR) is False
    )  # 缺所选 → fail-safe 拒(无 DATAT 回落)
    assert account_dir_allowed(OTHER_DIR) is False


# ── 路径歧义闸:UNC 少一个反斜杠 = 静默写进本机影子目录 ────────────
SINGLE_SLASH = (
    r"\accserver\ACCOUNT\70EXP\TEST"  # 少一个反斜杠 → 系统当成 C:\accserver\...
)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 路径 / 系统接口")
def test_path_ambiguity_flags_single_backslash_unc():
    # 真 UNC:系统不会再解释 → 安全(返回空串)
    assert path_ambiguity(DATAT_DIR) == ""
    # 少一个反斜杠:系统改写成当前盘下的目录 → 必须报出真实落点
    resolved = path_ambiguity(SINGLE_SLASH)
    assert resolved and resolved.lower().endswith("accserver\\account\\70exp\\test")
    assert ":" in resolved  # 变成带盘符的本地路径(如 C:\accserver\...)


def test_account_dir_rejects_ambiguous_path_even_if_self_consistent():
    # 就算"目标==所选"完全一致,只要路径会被系统改写落点,也一律拒写。
    # 这是 2026-07-23 真实事故的防线:配置少写一个反斜杠 → 记账静默进本机假目录还报成功。
    assert account_dir_allowed(SINGLE_SLASH, selected_dir=SINGLE_SLASH) is False
