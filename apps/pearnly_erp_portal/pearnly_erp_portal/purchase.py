# -*- coding: utf-8 -*-
"""兼容入口：采购业务已迁到 `purchasing` 域。

这里只保留原来的公开名称，供既有调用方（网页、LINE、测试脚本）继续解析；内部实现
全部在 `pearnly_erp_portal.purchasing`。新代码请直接用 `purchasing.service` /
`purchasing.api`，不要再往本文件里加业务。
"""

from pearnly_erp_portal.purchasing import service
from pearnly_erp_portal.purchasing.api import (
    api_create_purchase,
    api_get_purchase,
    api_get_settings,
    api_list_purchases,
    api_list_suppliers,
    api_pay_purchase,
    api_set_payment_status,
    api_submit_purchase,
    api_summary,
    api_update_purchase,
    api_update_settings,
    api_void_purchase,
    api_create_supplier,
    api_update_supplier,
)

# 旧的函数名。返回格式刻意保持原样：建单返回**编号字符串**（不是 dict），
# 列表继续接受 from_date / to_date 这两个旧参数名。
def create_purchase(**kwargs):
    return service.create_purchase(**kwargs)["name"]


def list_purchases(*, from_date=None, to_date=None, **kwargs):
    if from_date:
        kwargs.setdefault("date_from", from_date)
    if to_date:
        kwargs.setdefault("date_to", to_date)
    return service.list_purchases(**kwargs)


def summary(*, from_date=None, to_date=None, **kwargs):
    if from_date:
        kwargs.setdefault("date_from", from_date)
    if to_date:
        kwargs.setdefault("date_to", to_date)
    return service.summary(**kwargs)


get_purchase = service.get_purchase
update_purchase = service.update_purchase
