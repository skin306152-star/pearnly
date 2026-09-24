"""Manual groups: complete native forms plus compatible LINE projections."""
import logging
import math
import time

import frappe

from pearnly_dms.adapter.operations import run_logged_in
from pearnly_dms.client.catalogue_reader import start_catalogues, advance_catalogues
from pearnly_dms.client.snapshot_reader import (
    advance_customers, start_customers, collect_vehicle_options, collect_company_options, SnapshotLimits,
)
from pearnly_dms.masters.company_banks import fetch_payment_bank_masters
from pearnly_dms.snapshots import manual, store

logger = logging.getLogger(__name__)


def refresh(endpoint, *, request_id=None, dataset="directory", params=None):
    # Old scheduled jobs and the retired three-group contract cannot acquire IO.
    if not request_id or dataset not in {"vehicles", "company", "customers"}:
        return {"ok": False, "retired": True}
    manual.assert_active(endpoint, dataset, request_id)
    expected = store.scope(endpoint)
    started = time.time()
    checkpoint = store.stage(endpoint, dataset)
    if checkpoint is not None and (not isinstance(checkpoint, dict)
            or checkpoint.get("version") != 2
            or not {"version", "started_at", "phase", "tree"} <= set(checkpoint)
            or set(checkpoint) - {"version", "started_at", "phase", "tree", "customers"}
            or checkpoint["phase"] not in {"catalogues", "projection"}
            or isinstance(checkpoint["started_at"], bool)
            or not isinstance(checkpoint["started_at"], (int, float))
            or not math.isfinite(checkpoint["started_at"])
            or not 0 < checkpoint["started_at"] <= started + 1
            or started - checkpoint["started_at"] > manual.REQUEST_AGE):
        checkpoint = None
    # 单步读取租约。车型颜色是逐个车型读的（rayong 有 457 个车型），收尾投影要 30 多秒，
    # 旧的 30 秒必然把它掐断；队列给这一步的硬上限是 420 秒，取 120 秒足够又安全。
    limits = SnapshotLimits(max_seconds=120)

    def capture(client, adapter):
        nonlocal checkpoint
        if checkpoint is None:
            checkpoint = {"version": 2, "started_at": started, "phase": "catalogues",
                "tree": start_catalogues(client, "customer_aux" if dataset == "customers" else dataset,
                                         limits=limits)}
        stage = dict(checkpoint)
        if stage["phase"] == "catalogues":
            tree = advance_catalogues(client, stage["tree"], max_nodes=100, limits=limits)
            stage["tree"] = tree
            if tree["done"]:
                stage["phase"] = "projection"
            return {"ok": True, "stage": stage, "processed": tree["processed"], "total": tree["total"]}
        # A projection resumes in a new process: revalidate the completed private
        # catalogue before trusting it. A done checkpoint performs no HTTP here.
        checked = advance_catalogues(client, stage["tree"], max_nodes=1, limits=limits)
        if not checked["done"] or checked["dataset"] != ("customer_aux" if dataset == "customers" else dataset):
            raise store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_INCOMPLETE")
        catalogues = checked["data"]["catalogues"]
        if dataset == "vehicles":
            projection = collect_vehicle_options(client, catalogues=catalogues, limits=limits)
        elif dataset == "company":
            projection = collect_company_options(client, catalogues=catalogues,
                bank_reader=lambda: fetch_payment_bank_masters(adapter, client=client), limits=limits)
        else:
            if "customers" not in stage:
                stage["customers"] = start_customers(client,
                    extra_customer_ids=store.overlay_customer_ids(endpoint), limits=limits)
            # 每步尽量多做：一次队列 job 要付一次完整登录（6–16 秒），20 个客户一步
            # 意味着 15,135 个客户要 757 步、光登录就两个多小时（2026-09-25 实测）。
            # 100 是一步能取的最大块，30 秒租约内读得完。
            tree = advance_customers(client, stage["customers"], max_nodes=100, limits=limits)
            stage["customers"] = tree
            if tree["phase"] != "done":
                return {"ok": True, "stage": stage, "processed": len(tree["customers"]), "total": tree["count"]}
            prefixes = [[cid, row["fields"]["txtprefixname"]] for cid, row in catalogues["prefix"].items()]
            if not prefixes:
                raise store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_INCOMPLETE")
            projection = {"customers": tree["customers"], "prefixes": prefixes}
        return {"ok": True, "data": {"catalogues": catalogues, **projection},
                "count": sum(stage["tree"]["counts"].values()) + len(projection.get("customers", {}))}

    result = run_logged_in(endpoint, capture, authoritative_read=True, background=True, read_only_batch=True)
    frappe.db.commit()
    with manual.publish_guard(endpoint, dataset, request_id):
        if not result.get("ok"):
            if result.get("error_code") == "ERR_DMS_SNAPSHOT_BUSY":
                return {**result, "busy": True}
            code = result.get("error_code")
            # 只有"这一代不算数"的失败才丢断点。可续读的失败（传输抖动、单步时间
            # 租约用完）保留检查点，下一步接着读；否则数据量大的租户永远读不完。
            if code == "ERR_DMS_SNAPSHOT_INCOMPLETE":
                store.checkpoint(endpoint, None, dataset=dataset, expected_scope=expected)
            # 留痕：以前失败只留一个笼统的 error_code，读不通时看不出卡在哪一步。
            # 只记计数与阶段，不记响应体、也不记客户字段。
            tree = (checkpoint or {}).get("tree") or {}
            logger.warning(
                "DMS snapshot read failed dataset=%s endpoint=%s code=%s phase=%s done=%s "
                "cursor=%s requests=%s seconds=%.1f resumes=%s",
                dataset, endpoint, code, tree.get("phase"), tree.get("done"), tree.get("cursor"),
                (tree.get("usage") or {}).get("requests"),
                float((tree.get("usage") or {}).get("seconds") or 0),
                code != "ERR_DMS_SNAPSHOT_INCOMPLETE")
            store.failed(endpoint, dataset, code, expected_scope=expected)
            frappe.db.commit()
            return result
        complete = "data" in result
        if complete:
            store.publish(endpoint, dataset, result["data"], checkpoint["started_at"], expected_scope=expected)
            store.checkpoint(endpoint, None, dataset=dataset, expected_scope=expected)
        else:
            store.checkpoint(endpoint, result["stage"], dataset=dataset, expected_scope=expected)
    frappe.db.commit()
    count = result.get("count", result.get("processed", 0))
    return {"ok": True, "complete": complete, "count": count, "processed": count,
            "total": count if complete else result.get("total")}
