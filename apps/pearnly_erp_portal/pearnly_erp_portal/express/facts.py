"""一份 Express 账套最近一次读回的整套主档与账套资料（按需请求 ``express_masters`` 的结果，ADR-025）。

存在站点私有目录 ``private/erp_express_facts/<账套键>.json.gz``：主档同步落进 ERP 主档用它，准备写账载荷也用它（对方档、存货、
科目表、部门、存货科目组，写账规则据此核编码、问要人选的项）。换成写账规则认的 ``reported_*`` 时与 Cowork 心跳同一套净化
（共用的 `cc_desktop.wire.report_rules`）：CC 桌面版 1.11.0 起结果里带 ``facts``（与心跳上报同形状的目录、科目表、默认科目）；
旧版只有主档，就从主档里取对方档、存货、部门，没有科目表时写账规则不核科目（同 Cowork 对旧版小助手的口径）。
"""
import gzip
import json
import os
import re

import frappe

from pearnly_core.cc_desktop.contracts import MAPPING_KEYS
from pearnly_core.cc_desktop.wire import report_rules

FOLDER = "erp_express_facts"
_KEY = re.compile(r"^[0-9a-f]{40}$")


def _path(key):
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("invalid account set key")
    return frappe.get_site_path("private", FOLDER, key + ".json.gz")


def save(key, data):
    path = _path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".part"
    with open(temporary, "wb") as handle:
        handle.write(gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")))
    os.replace(temporary, path)


def load(key):
    """读回来的整份（没读过回 None）。"""
    try:
        with open(_path(key), "rb") as handle:
            return json.loads(gzip.decompress(handle.read()))
    except (OSError, ValueError):
        return None


def reported(data) -> dict:
    """整份结果 → 写账规则认的账套资料（``reported_*``，与 Cowork 心跳的净化同一套）。"""
    if not isinstance(data, dict):
        return {}
    out = {
        "reported_customers": report_rules.sanitize_catalog(data.get("customers") or [], report_rules.CUSTOMER_KEYS),
        "reported_suppliers": report_rules.sanitize_catalog(data.get("suppliers") or [], report_rules.SUPPLIER_KEYS),
        "reported_products": report_rules.sanitize_catalog(data.get("products") or [], report_rules.PRODUCT_KEYS),
        "reported_departments": report_rules.sanitize_catalog(data.get("departments") or [],
                                                              report_rules.DEPARTMENT_KEYS),
    }
    facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
    catalog = facts.get("catalog") if isinstance(facts.get("catalog"), dict) else {}
    if facts.get("accounts") is not None:
        out["reported_accounts"] = report_rules.sanitize_accounts(facts["accounts"])
    fingerprint = report_rules.sanitize_fingerprint(catalog.get("fingerprint"))
    if fingerprint:
        out["catalog_fingerprint"] = fingerprint
    if catalog.get("isacc_candidates") is not None:
        out["reported_stock_acc_groups"] = report_rules.sanitize_stock_acc_groups(catalog["isacc_candidates"])
    return out


def mapping(data) -> dict:
    """1.11.0 起结果里另带这份账套 ISINFO 的 6 个默认科目（心跳的账套行也带，这里是读主档那一刻的）。"""
    facts = data.get("facts") if isinstance(data, dict) and isinstance(data.get("facts"), dict) else {}
    value = facts.get("mapping")
    if not isinstance(value, dict):
        return {}
    return {key: str(value.get(key) or "").strip()[:40] for key in MAPPING_KEYS if str(value.get(key) or "").strip()}
