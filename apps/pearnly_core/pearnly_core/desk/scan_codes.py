"""扫到 / 输入的码是不是商品码（多产品共用：ERP、Cowork 按商品码认物料的入口都走这一份）。

网址不是商品码（岳父 11-01 扫到过泰国工业标准认证 TIS 码 appdb.tisi.go.th/Q/i.php?d=…）：认码的入口在认码那一层
一律不查、不建，提示「这不是商品条码」（:data:`NOT_PRODUCT_CODE`，词条在 core 术语表，每个站点都装）。
按台的车架号 / 序列号不会是网址，不受影响。纯函数，不依赖 Frappe。
"""
from __future__ import annotations

WEB_ADDRESS_PREFIXES = ("http://", "https://")
NOT_PRODUCT_CODE = "This is not a product barcode"


def web_address(value) -> bool:
    return str(value or "").strip().lower().startswith(WEB_ADDRESS_PREFIXES)
