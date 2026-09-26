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
    assert check_payload_version({"payload_version": 2}) == ERR_PAYLOAD_VERSION


def test_supported_set_is_exactly_v1_today():
    assert SUPPORTED_PAYLOAD_VERSIONS == frozenset({1})
