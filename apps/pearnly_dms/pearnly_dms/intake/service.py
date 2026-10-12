"""Cached identity previews and live, independently verified customer writes.

Manually read, independent directory datasets serve lookup/editor reads. They never authorize an
external write; final save retains native identity, address and readback checks.
The historical run_logged_in/err imports remain as thin compatibility exports.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from pearnly_dms.intake.customer_diff import SAME_NAME_RATIO, diff_customer_fields, master_name_if_close  # noqa: F401
from pearnly_dms.line.errors import friendly
from pearnly_dms.contracts.endpoint import endpoint_name
from pearnly_dms.adapter.push import build_adapter

logger = logging.getLogger(__name__)


def _merge_geo_rows(base: Optional[List[Any]], extra: Optional[List[Any]]) -> List[Any]:
    """把两支地址的级联表并成一张（按 id 去重，保持 base 的顺序）。

    差异卡要用它把"主档那套地址"的 id 也映成名字；只多几行，不影响选择。
    """
    out: List[Any] = list(base or [])
    seen = {str(row[0]) for row in out if row and row[0] is not None}
    for row in extra or []:
        if not row or row[0] is None:
            continue
        if str(row[0]) in seen:
            continue
        out.append(row)
        seen.add(str(row[0]))
    return out


from pearnly_dms.adapter.operations import err, run_logged_in


def score_candidates(
    rows: List[Dict[str, str]], *, people_id: str, name: str
) -> List[Dict[str, Any]]:
    """给相似候选打分（0-100）：**身份证号完全一致 = 100**；否则姓名相似为主、
    证号相似为辅。按分降序。

    为什么证号一致直接满分：证号是唯一身份，重名在泰国很常见，靠姓名相似度永远不够。
    """
    import difflib

    out: List[Dict[str, Any]] = []
    for r in rows:
        rname = r.get("name", "")
        rpid = r.get("people_id", "")
        name_ratio = difflib.SequenceMatcher(None, name or "", rname).ratio()
        score = name_ratio
        if people_id and rpid:
            if people_id == rpid:
                score = 1.0
            else:
                id_ratio = difflib.SequenceMatcher(None, people_id, rpid).ratio()
                score = max(score, 0.6 * name_ratio + 0.4 * id_ratio)
        out.append({**r, "score": round(score * 100)})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def incoming_for_diff(name: str, addr, resolved, phone: str = "") -> Dict[str, str]:
    """识别侧要拿去比对的"新值"。地址用**解析后的主档 id**（与 DMS 现值同口径才能比）。

    `phone` 是操作员手输的（身份证上没有），**非空才参与比对** —— 否则改了电话也测不出
    差异，更新时会被静默丢掉。`prefix`/`birthday` 这个入口没有信息，缺键即不参与。
    """
    out = {
        "name": name,
        "house_no": addr.house_no,
        "moo": addr.moo,
        "soi": addr.soi,
        "road": addr.road,
        "province_id": resolved.province_id,
        "district_id": resolved.district_id,
        "subdistrict_id": resolved.subdistrict_id,
        "zipcode_id": resolved.zipcode_id,
    }
    if (phone or "").strip():
        out["phone"] = str(phone).strip()
    return out


#: 识别姓名与主档姓名（去空格）的相似度达到这一值就按主档。ris 50 张实测：flash-lite
#: 误读（漏元音/声调、ช/ซ 这类形近字）都 ≥ 0.83；拿每张去比其余 49 位客户的姓名都 < 0.7。
def recognize_lookup(
    endpoint: Any,
    *,
    people_id: str,
    name: str = "",
    ocr_address: Dict[str, Any],
    phone: str = "",
    fallback_customer_ids: Optional[List[str]] = None,
    attempt: int = 1,
) -> Dict[str, Any]:
    """识别之后：查客户 + 解析地址级联 + 选项 + 称谓 + 相似候选。

    `scenario` 三种：`exact`（证号命中）/ `similar`（按姓名找到候选）/ `none`（都没有）。
    """

    def _do(cl, adapter):
        from pearnly_dms.contracts.models import ThaiAddress

        # 全量快照按完整证号匹配并拒绝歧义；台账候选若未同步到本代，
        # 明确待同步而非新客。这里不按台账号现场读取 DMS。
        look = cl.lookup_customer(people_id, fallback_customer_ids=fallback_customer_ids)
        # A complete immutable local generation is a preview, not authorization
        # to write. Repeating the same snapshot cannot improve freshness.
        prefixes = cl.list_prefixes()
        a = ocr_address or {}
        addr = ThaiAddress(
            house_no=str(a.get("house_no") or ""),
            moo=str(a.get("moo") or ""),
            soi=str(a.get("soi") or ""),
            road=str(a.get("road") or ""),
            province_name=str(a.get("province") or a.get("province_name") or ""),
            district_name=str(a.get("district") or a.get("district_name") or ""),
            subdistrict_name=str(a.get("subdistrict") or a.get("subdistrict_name") or ""),
            zipcode=str(a.get("zipcode") or ""),
        )
        resolved = cl.resolve_address(addr)
        geo = {
            "provinces": cl.list_geo("provinces"),
            "districts": (
                cl.list_geo("districts", resolved.province_id) if resolved.province_id else []
            ),
            "subdistricts": (
                cl.list_geo("subdistricts", resolved.district_id) if resolved.district_id else []
            ),
            "zipcodes": (
                cl.list_geo("zipcodes", resolved.subdistrict_id) if resolved.subdistrict_id else []
            ),
            "selected": {
                "province_id": resolved.province_id,
                "district_id": resolved.district_id,
                "subdistrict_id": resolved.subdistrict_id,
                "zipcode_id": resolved.zipcode_id,
            },
            "text": {
                "house_no": addr.house_no,
                "moo": addr.moo,
                "soi": addr.soi,
                "road": addr.road,
            },
        }
        # 差异卡的**旧值**来自 DMS 主档，而那套地址可能是**另一个省**的：
        # 卡上的地址名是靠上面这张选项表反查的，表里只有「新地址那一支」的
        # 县/镇/邮编，主档那几个 id 查不到就会把裸 id 印给用户（实测：151/1082/1084
        # 本来该显示 ขลุง/ขลุง/22110）。省表是全量的所以省不犯这个毛病。
        # 只补本地每日目录中旧地址的层级选项，不产生外部请求。
        master = (look.get("fields") or {}) if look.get("found") else {}
        for level, parent_field, taken_parent in (
            ("districts", "province_id", resolved.province_id),
            ("subdistricts", "district_id", resolved.district_id),
            ("zipcodes", "subdistrict_id", resolved.subdistrict_id),
        ):
            parent = str(master.get(parent_field) or "")
            if not parent or parent == str(taken_parent or ""):
                continue
            geo[level] = _merge_geo_rows(geo[level], cl.list_geo(level, parent))
        taken_name = name
        if look["found"]:
            scenario = "exact"
            fields = look["fields"]
            taken_name = master_name_if_close(fields.get("name"), name)
            field_diffs = diff_customer_fields(
                fields, incoming_for_diff(taken_name, addr, resolved, phone)
            )
            candidates = [
                {
                    "customer_id": look["customer_id"],
                    "cuscode": fields.get("cuscode", ""),
                    "name": fields.get("name", ""),
                    "people_id": fields.get("people_id", "") or people_id,
                    "score": 100,
                }
            ]
        else:
            field_diffs = []
            rows = cl.search_customers_detailed(name) if (name or "").strip() else []
            candidates = score_candidates(rows, people_id=people_id, name=name or "")
            for candidate in candidates:
                candidate["baseline"] = cl.read_customer(str(candidate["customer_id"]))
            scenario = "similar" if candidates else "none"

        return {
            "ok": True,
            "scenario": scenario,
            "name": taken_name,
            "match": {
                "found": look["found"],
                "customer_id": look["customer_id"],
                "current_fields": look["fields"],
            },
            "field_diffs": field_diffs,
            "candidates": candidates,
            "geo": geo,
            "prefixes": prefixes,
        }

    # 完整快照来自同一权威读取范围；不能混入销售被裁剪的目录。
    from pearnly_dms.snapshots.directory import Directory
    from pearnly_dms.contracts.client_base import DmsClientError
    from pearnly_dms.adapter.snapshot_writeback import with_customer_detail

    def local():
        directory = Directory(endpoint)
        result = _do(directory, None)
        result["snapshot"] = directory.metadata()
        return result

    try:
        return with_customer_detail(endpoint, local)
    except DmsClientError as exc:
        return err(exc.error_code)


def customer_fields(endpoint: Any, *, customer_id: str) -> Dict[str, Any]:
    """载入指定客户的全字段（相似场景选定候选后填充表单）。
    返回 `current_fields`（含三套地址 + 下拉选中标签）+ 府选项 + 称谓。"""

    from pearnly_dms.snapshots.directory import Directory
    from pearnly_dms.contracts.client_base import DmsClientError
    from pearnly_dms.adapter.snapshot_writeback import with_customer_detail

    def local():
        cl = Directory(endpoint)
        return {
            "ok": True,
            "customer_id": customer_id,
            "current_fields": cl.read_customer(customer_id),
            "provinces": cl.list_geo("provinces"),
            "prefixes": cl.list_prefixes(),
        }

    try:
        return with_customer_detail(endpoint, local)
    except DmsClientError as exc:
        return err(exc.error_code)


def geo_options(endpoint: Any, *, level: str, parent_id: str = "") -> Dict[str, Any]:
    """Address options from the published complete local tree; no DMS login."""
    from pearnly_dms.snapshots.directory import Directory
    from pearnly_dms.contracts.client_base import DmsClientError
    try:
        return {"ok": True, "options": Directory(endpoint).list_geo(level, parent_id)}
    except DmsClientError as exc:
        return err(exc.error_code)


def push_idcard_fields(
    endpoint: Any,
    *,
    fields: Dict[str, Any],
    mode: str,
    customer_id: Optional[str],
    addresses: Optional[Dict[str, Dict[str, Any]]] = None,
    attempt: int = 1,
    baseline: Optional[dict] = None,
    request_id: str = "",
    actor: str = "",
    assert_current=None,
) -> Dict[str, Any]:
    """面板最终字段 → 建/改客户（**只写 DMS 客户库，不建订车单**）。

    `mode`：`create`（新建）/ `overwrite`（覆盖可映射的）/ `update`（只写差异字段）。
    `update` 与 `overwrite` 在写库层是同一件事（载现表单 + 键存在即写），差别在界面
    给过来的字段集：`update` 只传选了"新值"的字段 → 其余保留 DMS 现值
    （传空串 = 显式清空）。
    """
    t0 = time.time()
    save_mode = "create" if mode == "create" else "overwrite"
    from pearnly_dms.intake.customer_attempt import Attempt
    from pearnly_dms.contracts.client_base import DmsClientError
    marker = Attempt(endpoint, request_id, actor, fields=fields, mode=mode,
                     customer_id=customer_id, addresses=addresses, baseline=baseline)
    blocked = False
    try:
        previous = marker.previous()
    except DmsClientError as exc:
        if exc.error_code != "ERR_DMS_CUSTOMER_UNCONFIRMED":
            return err(exc.error_code, response_body=exc.response_body)
        # 一次结果未确认的写入挡着这位客户（可能就是本请求自己上次那一笔）：进同一个会话，
        # 先只读核对它，再决定（见 `_checked`）。
        blocked, previous = True, None
    if previous:
        return previous
    # 本请求自己登记过发出：这一趟只做核对，不会再写（`begin` 也会拒），收尾不能改写那一行。
    own_dispatch = blocked and marker.dispatched()
    from pearnly_dms.adapter import snapshot_writeback
    cache_scope = snapshot_writeback.capture_scope(endpoint)

    def _checked(cl):
        """被挡时：用管理员会话只读核对挡着的写入（绝不重发），再看一次本请求。

        核对为本请求已写入 → 直接给成功（像正常路径一样把客户写回本地缓存）；本请求确定没写进去
        → 带 `reconciled: not_written` 交回界面重新查重；挡着的已放行 → None，接着按正常路径写
        （实时查重、三方合并照旧）；仍然不明 → 抛出原来的"未确认"。"""
        marker.reconcile(cl)
        try:
            done = marker.previous()
        except DmsClientError:
            if own_dispatch and marker.not_written():
                return {**err("ERR_DMS_CUSTOMER_SAVE"), "reconciled": "not_written"}
            raise
        if not done:
            return None
        cached = snapshot_writeback.customer(endpoint, cl, done["customer_id"], expected_scope=cache_scope)
        cuscode = str(done.get("cuscode") or (cached.get("cuscode") if isinstance(cached, dict) else "") or "")
        return {**done, "cuscode": cuscode, "cache_updated": bool(cached),
                "elapsed_ms": int((time.time() - t0) * 1000)}

    def _do(cl, adapter):
        if blocked:
            checked = _checked(cl)
            if checked is not None:
                return checked

        def recheck():
            if assert_current:
                import frappe
                frappe.db.commit()
                assert_current()

        def begin(verify):
            recheck()
            marker.begin(verify)
        # 同一笔建档里客户编号被另一个人占用、换号再发之前，客户端用它重核绑定与会话
        # （`client_intake._still_current`，写法同订车顺号的 `assert_current`）。
        begin.assert_current = recheck
        cid, converted = cl.save_customer(
            fields=fields, mode=save_mode, customer_id=customer_id, addresses=addresses,
            baseline=baseline, on_attempt=begin,
        )
        # Keep the client tuple contract; create collisions now require a new
        # explicit review and never silently convert into an overwrite.
        actual = "update" if converted else mode
        cached = snapshot_writeback.customer(endpoint, cl, cid, expected_scope=cache_scope)
        # `customer_id` 是 DMS 内部行号（表单 `idsel`），界面上看不到；给人看的客户编号是
        # 客户档的 `txtcuscode`（DMS 客户列表第一列）。它来自上面那次写后回读，不另读 DMS。
        cuscode = str(cached.get("cuscode") or "") if isinstance(cached, dict) else ""
        return {
            "ok": True,
            "success": True,
            "customer_id": cid,
            "cuscode": cuscode,
            "mode": actual,
            "cache_updated": bool(cached),
            "elapsed_ms": int((time.time() - t0) * 1000),
            "response_body": {
                "adapter": "mrerp_dms",
                "customer_id": cid,
                "cuscode": cuscode,
                "mode": actual,
            },
        }

    # `catch_up=True`：这是操作员自己发起的一笔，动作做完顺手把 DMS 使用记录里的
    # 增量补回缓存（同一个会话，不多登一次、不多占一个账号）。
    # `line_write`：登录页都没打开时同一任务自动用下一档再试一次（结果带 `login_attempt`）；
    # 任务时限到点报可重试的网络异常，已经发出的写入由 `marker.finish` 改判"结果未确认"。
    out = run_logged_in(endpoint, _do, attempt=attempt, catch_up=True, line_write=True)
    if own_dispatch:
        out = {**out, "customer_write_log": marker.name}  # that row already records this request
    else:
        out = marker.finish(out)
    if not out.get("ok"):
        out["success"] = False
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
    return out
