"""完整地址目录的采集入口：一次读完四张表，整代成功才发布。

2026-09-24 换成**列表端点**读取（`client/geo_list_reader`）。原因写在那个模块里：
详情页是"一条一次请求"，全国 15,729 条要约 33 分钟，且整代完整才允许发布——中途
一次 30 秒超时就让整代作废，真实环境实测在第 31 轮被打断，结构上几乎不可能成功。
列表端点同一棵树只要几十个请求、约 8 秒。

**不再有分片断点**：一次调用内读完全部四张表；读不完就失败、保留上一代。旧版留下的
分片暂存（详情协议的形状）已不再兼容，这里显式清掉，避免被当成可续跑的残留。

发布契约不变：完整树通过 `store.publish` 的校验（`complete_tree`）才落地；失败的代
绝不覆盖上一代成功数据。
"""
import time

import frappe

from pearnly_dms.adapter.operations import run_logged_in
from pearnly_dms.client.geo_list_reader import GeoListLimits, read as read_address_tables
from pearnly_dms.snapshots import manual, store


GEO_LIMITS = GeoListLimits()


def refresh(endpoint, *, request_id=None, dataset="geo", params=None):
    if not request_id:
        return {"ok": False, "retired": True}
    manual.assert_active(endpoint, dataset, request_id)
    key = store.scope(endpoint)

    def capture(client, adapter):
        started = time.time()
        tree, tables, usage = read_address_tables(client, GEO_LIMITS)
        return {"ok": True, "geo": tree, "catalogues": tables, "started_at": started,
                "count": usage.rows, "requests": usage.requests, "bytes": usage.bytes}

    result = run_logged_in(endpoint, capture, authoritative_read=True, background=True,
                           read_only_batch=True)
    frappe.db.commit()
    with manual.publish_guard(endpoint, dataset, request_id):
        if not result.get("ok"):
            if result.get("error_code") == "ERR_DMS_SNAPSHOT_BUSY":
                return {**result, "busy": True}
            # 一次性读取：没有可续跑的暂存，旧版分片格式也一并清掉。
            store.checkpoint(endpoint, None, expected_scope=key)
            store.failed(endpoint, "geo", result.get("error_code"), expected_scope=key)
            frappe.db.commit()
            return result
        # `catalogues` 是原始行（含各级显示名），只进私有快照，不对外展示。
        published = store.publish(endpoint, "geo", {**result["geo"], "catalogues": result["catalogues"]},
                                  result["started_at"], expected_scope=key)
        store.checkpoint(endpoint, None, expected_scope=key)
    frappe.db.commit()
    return {"ok": True, "complete": True, "published": bool(published),
            "count": result["count"], "processed": result["count"], "total": result["count"],
            "requests": result["requests"]}
