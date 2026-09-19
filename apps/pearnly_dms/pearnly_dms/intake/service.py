# -*- coding: utf-8 -*-
"""DMS 身份证「识别 → 可编辑面板 → 确认推送」两步流的服务封装。

四个动作：

| 动作 | 干什么 |
|---|---|
| `recognize_lookup` | 登录一次 → 查 DMS 客户 + 把识别的地址解析成主档 id + 府/县/区/邮编选项 + 称谓 → 一次性喂面板 |
| `customer_fields` | 载入指定客户的全字段（相似候选选中后填充表单） |
| `geo_options` | 面板改地址时的级联代理 |
| `push_idcard_fields` | 用面板编辑后的字段建/改客户（**只写客户，不建订车单**） |

三个关键设计（照搬老产品）：

1. **`run_logged_in` 从不抛异常** —— 所有失败都变成带错误码的 dict，因为它要落进
   推送日志；抛出去就只剩一条没上下文的堆栈。
2. **每次动作都真登录一次**，不缓存 cookie 复用：适配器退出时会显式访问 logout 页注销
   浏览器会话，复用那种 cookie 只会拿到**已注销会话**（HTTP 200 的空壳正文或静默空的
   级联列表 —— 这种"看起来成功其实什么都没有"最难查）。
3. **台账兜底先于退避重试**：DMS 的搜索对新客户有"隐身期"（刚建/刚改的客户对新会话
   隐身数分钟，老产品 2026-07-19 四案实锤）。所以拿本租户最近推过的同尾号客户**按客户号
   直读**核对全证号 —— 直读不经搜索缓存，零等待。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from pearnly_dms.intake.customer_diff import diff_customer_fields
from pearnly_dms.line.errors import friendly
from pearnly_dms.contracts.id_validate import normalize_thai_id
from pearnly_dms.adapter.push import build_adapter

logger = logging.getLogger(__name__)


def endpoint_name(endpoint: Any) -> str:
    """端点标识：收字符串（文档名）/ dict / Frappe 文档。"""
    if isinstance(endpoint, str):
        return endpoint
    if isinstance(endpoint, dict):
        return str(endpoint.get("name") or endpoint.get("id") or "")
    return str(getattr(endpoint, "name", "") or "")


def err(code: str, raw: str = "") -> Dict[str, Any]:
    """失败结果。带 `response_body` 是有意的：**光有 error_code 分不清**
    "DMS 拒了"还是"我们复核没查到"，这两件事的处理方式完全不同。"""
    return {
        "ok": False,
        "error_code": code,
        "error_friendly": friendly(code),
        "raw_error": (raw or "")[:300],
        "response_body": {"raw_error": (raw or "")[:300]},
    }


def run_logged_in(endpoint: Any, fn, *, authoritative_read: bool = False):
    """建适配器 → 登录 → `fn(client, adapter)`。**从不抛异常**。

    会话随 adapter 退出即注销，不缓存 cookie 复用。

    `authoritative_read=True`：**整块 DMS 读取**借管理员会话（配了独立管理员凭据组时），
    销售的挑选/匹配因此看到的是**权威映射**，而不是自己的不完整视图。整块只解析一次
    管理员 transport；未配管理员照样走销售；配了但管理员登录失败 →
    `ERR_DMS_ADMIN_AUTH` **明确失败关闭**，不静默降级。**写操作（上传/提交）不走这个入口。**
    """
    name = endpoint_name(endpoint)
    adapter, build_err = build_adapter(name)
    if build_err:
        return err(build_err["error_code"], build_err["raw"])
    try:
        from pearnly_dms.adapter.session import DmsAdminAuthError, DmsAuthError, DmsTechnicalError
        from pearnly_dms.contracts.client_base import DmsClientError

        try:
            with adapter:
                try:
                    adapter.login()
                except (DmsAuthError, DmsTechnicalError):
                    if adapter.concurrent_login_detected:
                        return err("ERR_DMS_CONCURRENT_LOGIN", adapter.last_dialog)
                    raise
                try:
                    client = adapter.client()
                    if authoritative_read:
                        from pearnly_dms.adapter.admin_read import authoritative_read_session

                        with authoritative_read_session(client):
                            out = fn(client, adapter)
                    else:
                        out = fn(client, adapter)
                except DmsClientError as exc:
                    # 提交类失败带着"已经发出去了"的证据（`response_body.submitted`）：
                    # 这时候报"被别人顶下线"会误导 —— 单可能已经建出来了。
                    if adapter.concurrent_login_detected and not getattr(
                        exc, "response_body", {}
                    ).get("submitted"):
                        return err("ERR_DMS_CONCURRENT_LOGIN", adapter.last_dialog)
                    return err(exc.error_code or "ERR_DMS_TECHNICAL", str(exc))
                # 收尾再查一次：中途被别人顶下线，结果不能算数。
                # 但**已经拿到单号**的结果算数 —— 单确实建出来了。
                if adapter.concurrent_login_detected and not (
                    isinstance(out, dict) and out.get("booking_id")
                ):
                    return err("ERR_DMS_CONCURRENT_LOGIN", adapter.last_dialog)
                return out
        except DmsAdminAuthError as exc:
            # admin 凭据组登录失败（≠ 用户会话）—— **子类必须先于基类捕获**
            return err("ERR_DMS_ADMIN_AUTH", f"{type(exc).__name__}: {exc}")
        except DmsAuthError as exc:
            return err("ERR_DMS_AUTH", f"{type(exc).__name__}: {exc}")
        except DmsTechnicalError as exc:
            return err("ERR_DMS_TECHNICAL", f"{type(exc).__name__}: {exc}")
        except DmsClientError as exc:
            return err(exc.error_code or "ERR_DMS_TECHNICAL", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("DMS 操作意外失败")
        return err("ERR_UNEXPECTED", f"{type(exc).__name__}: {exc}")


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


def recognize_lookup(
    endpoint: Any,
    *,
    people_id: str,
    name: str = "",
    ocr_address: Dict[str, Any],
    phone: str = "",
    fallback_customer_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """识别之后：查客户 + 解析地址级联 + 选项 + 称谓 + 相似候选。

    `scenario` 三种：`exact`（证号命中）/ `similar`（按姓名找到候选）/ `none`（都没有）。
    """

    def _do(cl, adapter):
        from pearnly_dms.contracts.models import ThaiAddress

        look = cl.lookup_customer(people_id)
        if not look["found"]:
            # **台账兜底先行**：本租户最近推过的同尾号客户，按客户号直读核对全证号 ——
            # 直读不经搜索缓存，权威且零等待。"新客隐身期"的案例全在台账里，
            # 先直读能省掉下面的退避重试。
            pid_norm = normalize_thai_id(str(people_id))
            for cid in fallback_customer_ids or []:
                f = cl.read_customer(str(cid))
                if pid_norm and normalize_thai_id(str(f.get("people_id") or "")) == pid_norm:
                    look = {"found": True, "customer_id": str(cid), "fields": f}
                    break
        # 台账也没有才退避重试：只剩"DMS 原生老客户 + 搜索偶发空返"这个窄场景。
        # 真新客户会为它多付约 3 秒 —— 但**误标新客的代价更高**，所以保守保留两次。
        for _ in range(2):
            if look["found"] or not (people_id or "").strip():
                break
            time.sleep(1.5)
            look = cl.lookup_customer(people_id)

        form_html = cl._post_text("cus/form.php", {"status": "n"})
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
        resolved = cl._resolve_address_geo(addr, form_html)
        geo = {
            "provinces": cl._select_options(form_html, "selprovinces"),
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
        if look["found"]:
            scenario = "exact"
            fields = look["fields"]
            field_diffs = diff_customer_fields(
                fields, incoming_for_diff(name, addr, resolved, phone)
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
            scenario = "similar" if candidates else "none"

        return {
            "ok": True,
            "scenario": scenario,
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

    return run_logged_in(endpoint, _do)


def customer_fields(endpoint: Any, *, customer_id: str) -> Dict[str, Any]:
    """载入指定客户的全字段（相似场景选定候选后填充表单）。
    返回 `current_fields`（含三套地址 + 下拉选中标签）+ 府选项 + 称谓。"""

    def _do(cl, adapter):
        page = cl._post_text("cus/form.php", {"status": "e", "id": customer_id})
        data = cl._parse_form_defaults(page)
        return {
            "ok": True,
            "customer_id": customer_id,
            "current_fields": cl._extract_customer_fields(data, page),
            "provinces": cl._select_options(page, "selprovinces"),
            "prefixes": cl.list_prefixes(),
        }

    return run_logged_in(endpoint, _do)


def geo_options(endpoint: Any, *, level: str, parent_id: str = "") -> Dict[str, Any]:
    """地址级联选项。走真实 Playwright 会话（**不复用已注销的 cookie**）。"""

    def _do(cl, adapter):
        return {"ok": True, "options": cl.list_geo(level, parent_id)}

    return run_logged_in(endpoint, _do)


def push_idcard_fields(
    endpoint: Any,
    *,
    fields: Dict[str, Any],
    mode: str,
    customer_id: Optional[str],
    addresses: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """面板最终字段 → 建/改客户（**只写 DMS 客户库，不建订车单**）。

    `mode`：`create`（新建）/ `overwrite`（覆盖可映射的）/ `update`（只写差异字段）。
    `update` 与 `overwrite` 在写库层是同一件事（载现表单 + 键存在即写），差别在界面
    给过来的字段集：`update` 只传选了"新值"的字段 → 其余保留 DMS 现值
    （传空串 = 显式清空）。
    """
    t0 = time.time()
    save_mode = "create" if mode == "create" else "overwrite"

    def _do(cl, adapter):
        cid, converted = cl.save_customer(
            fields=fields, mode=save_mode, customer_id=customer_id, addresses=addresses
        )
        # create 被幂等/撞码转成 overwrite 时**如实回 update** —— 回执不谎称"新建"。
        actual = "update" if converted else mode
        return {
            "ok": True,
            "success": True,
            "customer_id": cid,
            "mode": actual,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "response_body": {
                "adapter": "mrerp_dms",
                "customer_id": cid,
                "mode": actual,
            },
        }

    out = run_logged_in(endpoint, _do)
    if not out.get("ok"):
        out["success"] = False
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
    return out
