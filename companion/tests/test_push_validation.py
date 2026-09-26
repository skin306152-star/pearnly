from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion.push_validation import (  # noqa: E402
    ERR_INVALID_DATE,
    ERR_MAPPING_MISSING_CUSTOMER,
    ERR_MAPPING_MISSING_ITEM,
    validate_express_payload,
)


def _payload() -> dict:
    return {
        "customer_code": "K001",
        "invoice_date": "19/05/69",
        "items": [{"code": "4200-10", "quantity": "1.0"}],
    }


def test_validate_express_payload_accepts_ready_task():
    assert validate_express_payload(_payload()).ok is True


def test_validate_express_payload_blocks_missing_customer_mapping():
    payload = _payload()
    payload["customer_code"] = ""
    res = validate_express_payload(payload)
    assert res.ok is False
    assert res.error_code == ERR_MAPPING_MISSING_CUSTOMER


def test_validate_express_payload_blocks_missing_item_mapping():
    payload = _payload()
    payload["items"][0]["code"] = ""
    res = validate_express_payload(payload)
    assert res.ok is False
    assert res.error_code == ERR_MAPPING_MISSING_ITEM


def test_validate_express_payload_blocks_bad_date():
    payload = _payload()
    payload["invoice_date"] = "2026-05-30"
    res = validate_express_payload(payload)
    assert res.ok is False
    assert res.error_code == ERR_INVALID_DATE

