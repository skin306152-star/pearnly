# -*- coding: utf-8 -*-
"""订车草稿的**浏览器编辑器**（LINE 内 LIFF 页）后端：load / paints / geo / save。

照搬老产品 `services/line_dms/booking_edit.py`（线上基准 `501589f0`），async → 同步，
多 OA 那层（`binding_guard`）去掉 —— 新平台一站一渠道。

## 这一层在干什么

LINE 里点预览卡的 `[แก้ไข]`（编辑）→ 打开本站点网页编辑器 → 改完保存 → **新版预览卡
发回 LINE**。所以：

  · 每个请求只登录 DMS **一次**：主档、当前客户、五类银行目录、选中车型颜色、四级地址
    标签都吃 `dms_edit_snapshot.read_edit_snapshot` 的同一份请求级新鲜快照。
  · `save` **只有在 LINE 确认收到新版预览卡之后才算成功** —— 页面先关、卡没发出去，
    是最难查的一种"看起来成功"。
  · 校验全部按**当前** DMS 权威主档判（称谓/地点/车型/条件/登记/银行都可能被 12h 快照
    带偏），拿不到权威快照就 fail closed（`master_unavailable`），绝不拿旧缓存顶上。

## 会话态与 nonce

订车草稿的会话态是 `booking_review`，nonce 存在 `payload.nonce` 里。load/paints/geo
**只校验不消费**（用户可能来回改几次）；save 用 `replace_review_payload` 换一份新
payload 并**轮换 nonce**（旧卡随之失效）。
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pearnly_core import line_session as store
from pearnly_dms.booking import booking_payments as booking_payments
from pearnly_dms.editor import edit_link as dms_edit_link
from pearnly_dms.line import out as _out
from pearnly_dms.booking.conversation import qa_cards as qa_cards
from pearnly_dms.masters.company_banks import (
    MANUAL_BANK_KEYS,
    PAYMENT_BANK_MASTERS,
    company_bank_label,
    manual_bank_allowed_for_rows,
    sort_bank_rows,
)
from pearnly_dms.editor.edit_snapshot import read_edit_snapshot
from pearnly_dms.contracts.id_validate import is_valid_thai_id, normalize_thai_id
from pearnly_dms.contracts.client_base import parse_be_date
from pearnly_dms.intake.service import geo_options as _geo_options
from pearnly_dms.masters.master_contract import MasterSyncError, build_paint_snapshot, build_snapshot
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
from pearnly_dms.booking.conversation.qa_util import car_label, find_row, row_name

logger = logging.getLogger(__name__)

STATE = "booking_review"

MASTER_FIELDS = {
    "place": "place_books",
    "car": "cars",
    "term": "term_sales",
    "regis": "regis_behalfs",
}
CUSTOMER_FIELDS = (
    "people_id",
    "prefix_id",
    "name",
    "birthday_be",
    "phone",
    "house_no",
    "building",
    "floor",
    "room",
    "village",
    "moo",
    "soi",
    "road",
    "province_id",
    "province_name",
    "district_id",
    "district_name",
    "subdistrict_id",
    "subdistrict_name",
    "zipcode_id",
    "zipcode",
)
# "客户资料被改过"的判据：名字类字段是从主档回填的派生值，邮编是展示值 —— 都不算改动。
CUSTOMER_DIRTY_FIELDS = tuple(
    field for field in CUSTOMER_FIELDS if not field.endswith("_name") and field != "zipcode"
)


class DmsBookingEditError(ValueError):
    """编辑器可读的错误码（前端按码取话术，不匹配字符串）。"""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


# ── 绑定 / 会话守卫 ───────────────────────────────────────────────────────
def _binding(line_user_id: str) -> dict:
    """编辑页的请求必须来自**已绑定的那个 LINE 用户**（解绑后立刻失效）。"""
    import frappe

    from pearnly_core import line_binding

    row = line_binding.binding_of(str(line_user_id or ""))
    if not row or not row.get("user"):
        raise DmsBookingEditError("dms_booking.not_bound", 403)
    return {
        "line_user_id": str(line_user_id),
        "user_id": str(row.get("user")),
        "tenant_id": frappe.local.site,
    }


def _review(line_user_id: str, nonce: str, state: str = STATE) -> Tuple[dict, dict, dict]:
    """校验绑定 + 会话态 + nonce（**只校验不消费**），返回 (binding, payload, endpoint)。"""
    binding = _binding(line_user_id)
    sess = store.get_session(line_user_id)
    if not store.verify_nonce(sess, nonce, state):
        raise DmsBookingEditError("dms_booking.expired", 409)
    payload = (sess or {}).get("payload") or {}
    qa = payload.get("qa") or {}
    endpoint = resolve_dms_endpoint(binding["user_id"], qa.get("endpoint_id"))
    if not endpoint:
        raise DmsBookingEditError("dms_booking.no_endpoint", 409)
    return binding, payload, endpoint


# ── 选项成形 ──────────────────────────────────────────────────────────────
def _option(row: list, label=None) -> dict:
    return {"id": str(row[0]), "label": (label or row_name)(row)}


def _options(rows: Iterable[list], label=None) -> List[dict]:
    return [_option(row, label) for row in rows if row and row[0] is not None]


def _payment_bank_options(key: str, rows: list) -> List[dict]:
    """银行目录 → 下拉选项。公司收款账户额外带账号/支行（编辑器要显示给客户看）。"""
    rows = sort_bank_rows(rows)
    if key != "company_banks":
        return _options(rows, company_bank_label)
    return [
        {
            **_option(row, company_bank_label),
            "account_no": (
                "" if len(row) <= 4 or str(row[4] or "").strip() == "00" else str(row[4]).strip()
            ),
            "branch_name": (
                "" if len(row) <= 3 or str(row[3] or "").strip() == "00" else str(row[3]).strip()
            ),
        }
        for row in rows
    ]


def _manual_bank_keys(masters: dict) -> List[str]:
    """目录权威为空的四类付款银行 —— 编辑器据此把下拉换成可填写的银行名称输入框。

    `company_banks`（公司收款账户）永不入列。读取失败（masters 里该 key 不是 list）
    不算空目录，由 `_masters` 抛 `master_unavailable` fail closed。
    """
    return [key for key in MANUAL_BANK_KEYS if manual_bank_allowed_for_rows(key, masters.get(key))]


def _form(qa: dict) -> dict:
    """编辑器首屏要回显的东西：客户资料 + 答案 + 付款 + 附件两行 + 顾问。"""
    draft = dict(qa.get("draft") or {})
    draft["name"] = str((qa.get("customer") or {}).get("name") or draft.get("name") or "")
    return {
        "customer": {key: str(draft.get(key) or "") for key in CUSTOMER_FIELDS},
        "answers": qa.get("answers") or {},
        "payments": qa.get("payments") or [],
        "files": {
            "id_card": bool((qa.get("files") or {}).get("id_card_mid")),
            "slip": bool((qa.get("files") or {}).get("slip_mid")),
        },
        "advisor": qa.get("advisor") or {},
    }


def _snapshot(
    endpoint: dict,
    *,
    car_ids: Iterable[str] = (),
    customer: Optional[dict] = None,
    customer_id: str = "",
) -> dict:
    """本请求唯一一次权威登录取回的快照；读不到就 fail closed（不拿旧缓存冒充实时）。"""
    kwargs = {"car_ids": car_ids, "customer": customer}
    if customer_id:
        kwargs["customer_id"] = customer_id
    snapshot = read_edit_snapshot(endpoint, **kwargs)
    if not snapshot:
        raise DmsBookingEditError("dms_booking.master_unavailable", 503)
    return snapshot


def _masters(snapshot: dict) -> dict:
    """快照里的主档必须完整可映射（半个目录判不出「这个选项还在不在」）。"""
    masters = snapshot.get("masters") or {}
    try:
        build_snapshot(masters)
    except MasterSyncError as exc:
        raise DmsBookingEditError("dms_booking.master_unavailable", 503) from exc
    if not masters.get("prefixes"):
        raise DmsBookingEditError("dms_booking.master_unavailable", 503)
    return masters


def _snapshot_paints(snapshot: dict, car_id: str) -> list:
    """该车型的颜色行 —— 只认本请求快照里读到的。

    缺键（没请求这个车型）或 None（这次没读到）都是读失败，不当「这车没颜色」：
    空表才是权威结论。
    """
    paints = snapshot.get("paints") or {}
    key = str(car_id or "")
    rows = paints.get(key)
    if not isinstance(rows, list):
        raise DmsBookingEditError("dms_booking.master_unavailable", 503)
    return rows


# ── load ─────────────────────────────────────────────────────────────────
def load(line_user_id: str, nonce: str) -> dict:
    _, payload, endpoint = _review(line_user_id, nonce)
    qa = payload.get("qa") or {}
    # 编辑页展示的是用户即将确认的主档，不能让 12 小时前的银行/车型快照继续占位：
    # 主档 + 本次要展示的车型颜色在**同一次登录**里读回（不再为同一车型颜色登第二次）。
    car_id = str(((qa.get("answers") or {}).get("car") or {}).get("id") or "")
    form = _form(qa)
    # An explicitly saved form is complete, including intentional blanks and
    # corrected identity fields. Do not refill it with the still-unsubmitted
    # external customer record when reopening the editor.
    customer_id = "" if qa.get("customer_edited") else str((qa.get("customer") or {}).get("id") or "")
    snapshot = _snapshot(
        endpoint,
        car_ids=(car_id,) if car_id else (),
        customer=form["customer"],
        customer_id=customer_id,
    )
    # 身份证草稿已填的值优先；缺失的称谓/邮编/地址 id 从同一次管理员权威直读补齐。
    resolved_customer = snapshot.get("resolved_customer") or {}
    form["customer"].update({key: str(resolved_customer.get(key) or "") for key in CUSTOMER_FIELDS})
    masters = _masters(snapshot)
    prefix_rows = masters.get("prefixes") or []
    return {
        "form": form,
        "masters": {
            "places": _options(masters.get("place_books") or []),
            "cars": _options(masters.get("cars") or [], car_label),
            "paints": (_options(_snapshot_paints(snapshot, car_id)) if car_id else []),
            "terms": _options(masters.get("term_sales") or []),
            "regis": _options(masters.get("regis_behalfs") or []),
            **{
                key: _payment_bank_options(key, masters.get(key) or [])
                for key in PAYMENT_BANK_MASTERS
            },
            "prefixes": _options(prefix_rows or []),
        },
        # 与 LINE 对话同一条规则：这几类目录权威为空 → 银行名称手工填，hidden id 留空。
        "manual_banks": _manual_bank_keys(masters),
        # 首屏直接复用同一次权威快照，避免移动端再为四级地址重复登录 DMS 四次。
        "geo": {key: _options(rows) for key, rows in (snapshot.get("geo") or {}).items()},
    }


def paints(line_user_id: str, nonce: str, car_id: str) -> List[dict]:
    _, _, endpoint = _review(line_user_id, nonce)
    # 颜色选项同 load：映射当前 DMS 主档，不拿 12h 快照（旧色会错配已下架车型）。
    snapshot = _snapshot(endpoint, car_ids=(car_id,))
    masters = _masters(snapshot)
    if find_row(masters.get("cars"), car_id) is None:
        raise DmsBookingEditError("dms_booking.invalid_master")
    return _options(_snapshot_paints(snapshot, car_id))


def geo(line_user_id: str, nonce: str, level: str, parent_id: str = "") -> List[dict]:
    _, _, endpoint = _review(line_user_id, nonce)
    if level not in {"provinces", "districts", "subdistricts", "zipcodes"}:
        raise DmsBookingEditError("dms_booking.invalid_geo")
    result = _geo_options(endpoint, level=level, parent_id=parent_id)
    if not result.get("ok"):
        raise DmsBookingEditError("dms_booking.geo_unavailable", 503)
    return _options(result.get("options") or [])


# ── save ─────────────────────────────────────────────────────────────────
def _required(value: Any, code: str, limit: int = 160) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise DmsBookingEditError(code)
    return text


def _customer(raw: dict) -> dict:
    """提交上来的客户资料：逐项校验**在写之前**做完，不把半份资料写进草稿。"""
    out = {key: str(raw.get(key) or "").strip() for key in CUSTOMER_FIELDS}
    out["people_id"] = normalize_thai_id(out["people_id"])
    if not is_valid_thai_id(out["people_id"]):
        raise DmsBookingEditError("dms_booking.invalid_people_id")
    out["name"] = _required(out["name"], "dms_booking.invalid_name")
    out["phone"] = _required(out["phone"], "dms_booking.invalid_phone", 30)
    try:
        parse_be_date(out["birthday_be"])
    except ValueError as exc:
        raise DmsBookingEditError("dms_booking.invalid_birthday") from exc
    for field in ("house_no", "province_id", "district_id", "subdistrict_id", "zipcode_id"):
        _required(out[field], f"dms_booking.invalid_{field}")
    return out


def _customer_master_labels(snapshot: dict, customer: dict) -> Optional[dict]:
    """客户称谓 + 四级地址标签：全部来自本请求同一份快照（零额外登录）。

    快照里查不到某个 id（主档被删/改了）→ None，调用方按 `invalid_master` 如实报错。
    """
    geo = snapshot.get("geo") or {}
    levels = (
        ("prefix_id", "prefix_name", snapshot.get("prefixes") or []),
        ("province_id", "province_name", geo.get("provinces") or []),
        ("district_id", "district_name", geo.get("districts") or []),
        ("subdistrict_id", "subdistrict_name", geo.get("subdistricts") or []),
        ("zipcode_id", "zipcode", geo.get("zipcodes") or []),
    )
    labels = {}
    for id_field, name_field, rows in levels:
        row = find_row(rows, customer[id_field])
        if row is None:
            return None
        labels[name_field] = row_name(row)
    return labels


def _pick(masters: dict, key: str, rid: Any) -> dict:
    row = find_row(masters.get(MASTER_FIELDS[key]) or [], str(rid or ""))
    if row is None:
        raise DmsBookingEditError("dms_booking.invalid_master")
    label = car_label(row) if key == "car" else row_name(row)
    return {"id": str(row[0]), "label" if key == "car" else "name": label}


def _payments(rows: list, masters: dict) -> List[dict]:
    try:
        return booking_payments.normalize_editor_payments(rows, masters)
    except booking_payments.PaymentValidationError as exc:
        raise DmsBookingEditError(exc.code) from exc


def save(line_user_id: str, nonce: str, submitted: dict) -> str:
    """保存草稿并**同步确认**新版预览卡已送达 LINE；返回轮换后的新 nonce。"""
    binding, payload, endpoint = _review(line_user_id, nonce)
    qa = dict(payload.get("qa") or {})
    original_customer = _form(qa)["customer"]
    customer = _customer(dict(submitted.get("customer") or {}))
    raw_answers = dict(submitted.get("answers") or {})
    car_id = str(raw_answers.get("car_id") or "")
    # 保存校验按当前 DMS 主档判（称谓/地点/车型/条件/登记/银行都可能被 12h 快照带偏），
    # 而主档、银行目录、选中车型颜色、客户四级地址标签**共用同一次登录**的一份新鲜快照。
    snapshot = _snapshot(endpoint, car_ids=(car_id,), customer=customer)
    masters = _masters(snapshot)
    customer_changed = any(
        str(customer.get(field) or "") != str(original_customer.get(field) or "")
        for field in CUSTOMER_DIRTY_FIELDS
    )
    labels = _customer_master_labels(snapshot, customer)
    if labels is None:
        raise DmsBookingEditError("dms_booking.invalid_master")
    customer.update(labels)
    car = _pick(masters, "car", car_id)
    paint_rows = _snapshot_paints(snapshot, car["id"])
    # 换车型后颜色必须属于**新**车型：拿老选项提交会被这里挡下。
    paint_row = find_row(paint_rows, str(raw_answers.get("paint_id") or ""))
    if paint_row is None:
        raise DmsBookingEditError("dms_booking.invalid_master")
    delivery = _required(raw_answers.get("delivery_date_be"), "dms_booking.invalid_delivery")
    try:
        parse_be_date(delivery)
    except ValueError as exc:
        raise DmsBookingEditError("dms_booking.invalid_delivery") from exc
    qa["draft"] = {**(qa.get("draft") or {}), **customer}
    qa["customer"] = {**(qa.get("customer") or {}), "name": customer["name"]}
    qa["summary"] = qa_cards._fallback_summary(qa)
    # 网页端改过客户资料 → 记一条，后续"资料有差异"的分支按它走。
    qa["customer_dirty"] = bool(qa.get("customer_dirty")) or customer_changed
    qa["customer_edited"] = True
    qa["answers"] = {
        "place": _pick(masters, "place", raw_answers.get("place_id")),
        "car": car,
        "paint": {"id": str(paint_row[0]), "name": row_name(paint_row)},
        "delivery_date_be": delivery,
        "term": _pick(masters, "term", raw_answers.get("term_id")),
        "regis": _pick(masters, "regis", raw_answers.get("regis_id")),
        "regis_name": _required(raw_answers.get("regis_name"), "dms_booking.invalid_regis_name"),
    }
    qa["master_snapshot"] = build_snapshot(masters)
    qa["paint_snapshots"] = {car["id"]: build_paint_snapshot(car["id"], paint_rows)}
    qa.pop("masters_synced", None)
    qa["payments"] = _payments(list(submitted.get("payments") or []), masters)
    files = dict(qa.get("files") or {})
    keep = dict(submitted.get("keep_files") or {})
    if not keep.get("id_card", True):
        files["id_card_mid"] = None
    if not keep.get("slip", True):
        files["slip_mid"] = None
    has_transfer = any(payment["channel"] == "transfer" for payment in qa["payments"])
    has_slip = bool(files.get("slip_mid"))
    # 转账必须有凭证、凭证必须对应转账：任一落空都不许存，否则建单时才炸，用户已经离开页面了。
    if has_transfer and not has_slip:
        raise DmsBookingEditError("dms_booking.slip_required")
    if has_slip and not has_transfer:
        raise DmsBookingEditError("dms_booking.slip_without_transfer")
    qa["files"] = files
    qa.setdefault("audit", []).append({"step": "browser_edit", "input": "saved"})
    new_nonce = secrets.token_hex(8)
    new_payload = {**payload, "qa": qa, "nonce": new_nonce}
    if not store.replace_review_payload(line_user_id, nonce, new_payload):
        raise DmsBookingEditError("dms_booking.expired", 409)
    if not _dispatch_preview(line_user_id, new_nonce, qa):
        # 卡没发出去也没落队：恢复旧 payload/nonce，如实报错（用户还能重试同一份草稿）。
        store.replace_review_payload(line_user_id, new_nonce, payload)
        raise DmsBookingEditError("dms_booking.preview_send_failed", 503)
    return new_nonce


def _dispatch_preview(line_user_id: str, nonce: str, qa: dict) -> bool:
    """同步取得 LINE 200 回执；失败时调用方恢复旧 nonce，编辑页保持可重试。"""
    try:
        return bool(_out.send(line_user_id, qa_cards.preview_card(qa, nonce)))
    except Exception:
        logger.warning("[dms] booking preview send failed", exc_info=True)
        return False


__all__ = [
    "CUSTOMER_FIELDS",
    "DmsBookingEditError",
    "STATE",
    "geo",
    "load",
    "paints",
    "save",
]
