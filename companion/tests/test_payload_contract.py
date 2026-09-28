# -*- coding: utf-8 -*-
"""载荷版本协商契约:v1 放行/缺失视同 v1 放行/未来版本诚实拒绝。"""

from src.companion.payload_contract import (
    ERR_PAYLOAD_VERSION,
    SUPPORTED_PAYLOAD_VERSIONS,
    check_payload_version,
)


def test_v1_supported():
    assert check_payload_version({"payload_version": 1}) is None


def test_missing_version_defaults_to_v1_supported():
    # 兼容后端未升级期下发的旧载荷:没有这个键 → 视同 1。
    assert check_payload_version({"direction": "purchase"}) is None


def test_future_version_rejected():
    assert check_payload_version({"payload_version": 3}) == ERR_PAYLOAD_VERSION


def test_supported_set_is_v1_and_v2_today():
    # v2(2026-09-28):明细 `code`(选定的存货按码落账)+ 单头 `depcod`(部门码)
    assert SUPPORTED_PAYLOAD_VERSIONS == frozenset({1, 2})
    assert check_payload_version({"payload_version": 2}) is None
