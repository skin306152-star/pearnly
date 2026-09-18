# -*- coding: utf-8 -*-
"""DMS 推送层的公共部件：建适配器、连接测试、把识别结果转成建单载荷。

**DMS 不是发票推送目标**（老产品铁律）：发票历史绝不能误投到 DMS，所以 DMS 走自己
那条"身份证 → 客户 → 订车单"的路，与发票推送完全分开。

和老产品的一处实现差异：老产品从 endpoint 的 config dict 里取（可能是 Fernet 密文）
凭据；我们直接读 `Pearnly ERP Endpoint` 文档（密码在 `__Auth`，框架负责加密）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# 老产品同样有这个兜底地址（endpoint 没填 system_url 时用）。留着是为了行为一致。
DMS_DEFAULT_URL = "https://www.mrerp4sme.com/dms/index.php"


def build_adapter(endpoint_name: str):
    """由本站端点建一个 DMS 适配器。返回 `(adapter, None)` 或 `(None, 失败 dict)`。

    把各种"建不起来"翻译成错误码，而不是抛异常 —— 调用方（`run_logged_in`）要的是
    一个能落进推送日志的结果。
    """
    try:
        from pearnly_dms.dms_adapter import MrerpDmsAdapter
    except ImportError as exc:
        code = "ERR_PLAYWRIGHT_MISSING" if "playwright" in str(exc).lower() else "ERR_UNEXPECTED"
        return None, {"error_code": code, "raw": f"{type(exc).__name__}: {exc}"}

    try:
        adapter = MrerpDmsAdapter.from_endpoint(endpoint_name, headless=True)
    except ValueError as exc:
        # 端点不存在 / 停用 / 适配器不对 / 没配密码 —— 都算"没凭据"
        msg = str(exc)
        code = "ERR_NO_CREDS" if "密码" in msg else "ERR_UNEXPECTED"
        return None, {"error_code": code, "raw": msg}
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_adapter 失败")
        return None, {"error_code": "ERR_UNEXPECTED", "raw": f"{type(exc).__name__}: {exc}"}
    return adapter, None


def test_dms_endpoint(endpoint_name: str) -> Dict[str, Any]:
    """连接向导的健康检查。**从不抛异常**。

    成功回向导形状的 dict；失败回 `{ok: False, error_code, error_friendly, raw_error, masters: {}}`。
    同步函数（Playwright 同步 API），web 层要放到线程里调。
    """
    import time as _time

    from pearnly_dms.dms_errors import friendly

    t0 = _time.time()

    def _fail(code: str, raw: str = "") -> Dict[str, Any]:
        return {
            "ok": False,
            "adapter": "mrerp_dms",
            "elapsed_ms": int((_time.time() - t0) * 1000),
            "masters": {},
            "error_code": code,
            "error_friendly": friendly(code),
            "raw_error": (raw or "")[:300],
        }

    try:
        adapter, build_err = build_adapter(endpoint_name)
        if build_err:
            return _fail(build_err["error_code"], build_err["raw"])

        from pearnly_dms.dms_adapter import DmsAuthError, DmsTechnicalError

        try:
            with adapter:
                try:
                    adapter.login()
                    if adapter.concurrent_login_detected:
                        return _fail("ERR_DMS_CONCURRENT_LOGIN", adapter.last_dialog)
                    return adapter.test_connection()
                except (DmsAuthError, DmsTechnicalError):
                    if adapter.concurrent_login_detected:
                        return _fail("ERR_DMS_CONCURRENT_LOGIN", adapter.last_dialog)
                    raise
        except DmsAuthError as exc:
            return _fail("ERR_DMS_AUTH", f"{type(exc).__name__}: {exc}")
        except DmsTechnicalError as exc:
            return _fail("ERR_DMS_TECHNICAL", f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("test_dms_endpoint 意外失败")
        return _fail("ERR_UNEXPECTED", f"{type(exc).__name__}: {exc}")


def id_card_payload_from_dict(id_card: Dict[str, Any]):
    """识别结果 dict → `ThaiIdCardPayload` + `ThaiAddress`。

    地址主档 id 缺失就留空（v1：地址文本照写，府/县 id 映射算后续工作）。
    """
    from pearnly_dms.dms_models import ThaiAddress, ThaiIdCardPayload

    addr = id_card.get("address") or {}
    address = ThaiAddress(
        house_no=str(addr.get("house_no") or "").strip(),
        province_id=str(addr.get("province_id") or "").strip(),
        province_name=str(addr.get("province") or addr.get("province_name") or "").strip(),
        district_id=str(addr.get("district_id") or "").strip(),
        district_name=str(addr.get("district") or addr.get("district_name") or "").strip(),
        subdistrict_id=str(addr.get("subdistrict_id") or "").strip(),
        subdistrict_name=str(addr.get("subdistrict") or addr.get("subdistrict_name") or "").strip(),
        zipcode_id=str(addr.get("zipcode_id") or "").strip(),
        zipcode=str(addr.get("zipcode") or "").strip(),
        moo=str(addr.get("moo") or "").strip(),
        soi=str(addr.get("soi") or "").strip(),
        road=str(addr.get("road") or "").strip(),
    )
    return ThaiIdCardPayload(
        people_id=str(id_card.get("people_id") or "").strip(),
        first_name=str(id_card.get("first_name") or "").strip(),
        last_name=str(id_card.get("last_name") or "").strip(),
        birthday_be=str(id_card.get("birthday_be") or "").strip(),
        address=address,
        prefix_id=str(id_card.get("prefix_id") or "17").strip() or "17",
        prefix_name=str(id_card.get("prefix_name") or "").strip(),
        phone=str(id_card.get("phone") or "0800000000").strip() or "0800000000",
    )


def booking_defaults_from_endpoint(endpoint_name: str):
    """从端点文档读出建单默认值（`booking_defaults` 存成 JSON）。"""
    import frappe

    from pearnly_dms.dms_models import BookingDefaults

    raw = frappe.db.get_value("Pearnly ERP Endpoint", endpoint_name, "booking_defaults")
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    return BookingDefaults.from_config({"booking_defaults": raw or {}})


__all__ = [
    "DMS_DEFAULT_URL",
    "build_adapter",
    "test_dms_endpoint",
    "id_card_payload_from_dict",
    "booking_defaults_from_endpoint",
]
