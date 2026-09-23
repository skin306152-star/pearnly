# -*- coding: utf-8 -*-
"""票据字段的确定性清洗（纯函数，部署版口径）。

来源：ec2e5c957e6b ``services/purchase/field_clean.py`` 的 ``clean_tax_id`` 与
``clean_branch_no``。旧侧这两个函数是**共用**的：``routes/supplier_posting_routes.py``
（供应商过账档案）与 ``services/workspace/client_import.py``（客户名录导入）各自 import
同一份，本仓因此也只留一份，两边 import 这里。

· ``clean_tax_id``：剥掉所有非数字后**恰好 13 位**才算合法，否则空串（短号/日期/含字母噪声全弃）；
· ``clean_branch_no``：最多 5 位数字、左补零到 5 位；**空或超 5 位返回空串**（未提供必须留空，
  归一成总店 ``00000`` 会把分店票配到总店头上）。

本模块不碰 Frappe、数据库与时钟。旧文件里的 ``clean_seller`` / ``clean_invoice_no`` /
``to_decimal`` 服务的是识别字段的呈现（属 OCR 那一家），不在本模块。
"""

import re
from typing import Any


def clean_tax_id(raw: Any) -> str:
    """泰国税号：剥掉所有非数字后**恰好 13 位**才算合法，否则空串。"""
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits if len(digits) == 13 else ""


def clean_branch_no(raw: Any) -> str:
    """分店号：最多 5 位数字，左补零；空或超 5 位 → 空串（未提供不是总店）。"""
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits or len(digits) > 5:
        return ""
    return digits.zfill(5)
