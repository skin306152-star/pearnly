# -*- coding: utf-8 -*-
"""DMS LINE 对话流**状态机**：拍身份证 + 输手机号 → 三分支 → 确认 → 写客户档。

**确定性状态机，零自由文本理解**（老产品的原话）：用户每一步要么发图、要么发手机号、
要么点卡片按钮，没有"猜他想干什么"的地方。

状态只有两个：`collecting`（集料）→ `reviewing`（候审）。会话态存在
`Pearnly LINE Session`（见 `pearnly_core/line_session.py`）。

### 三处与老产品的**已标出的差异**（都是"那块还没搬"，不是重新设计规则）

1. **菜单层未搬**：老产品建档后会按 `mode` 分叉（菜单1 只建档 / 菜单2 接着订车）。
   现在统一走"只建档"的收尾；`exact` 且无差异时出"用旧资料"卡（老产品直拍路径的行为）。
2. **审批流未搬**：老产品有差异且销售无改写权时走"提交审核"。现在按
   `has_admin` 决定主按钮（有 → 更新；没有 → 提示先配管理员）。**审批策略接上时，
   这里换回 `exact_diff_card` 即可，其余代码不用动。**
3. **订车逐问搬完但未接线**：老产品建档后可在 LINE 里接着订车。状态机与它的七个分件
   都已落地并验过（`dms_booking_qa` / `dms_booking_qa_start|sync|pages|payment|transfer`
   + `dms_out`），缺的是**入口**：老产品靠菜单层决定"建档之后接着订车"，而菜单层
   （`menu_cards` / `menu_flow` / `menu_sync`）还没搬。接线之前这一层不会被调用；
   建单本身也早已搬完并验过（`dms_booking`）。

重活（识别 / DMS 登录 / 写库）一律**丢后台任务**，webhook 不占着回复令牌等它
（老产品是 spawn 后台 + start_loading + push 结果，同一个思路）。
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any, Dict, List, Optional

import frappe

from pearnly_core import line
from pearnly_core import line_session as store
from pearnly_dms.line import cards as cards
from pearnly_dms.intake import draft as draft
from pearnly_dms.intake import service as dms_intake
from pearnly_dms.ocr import ocr_review as ocr_review
from pearnly_dms.ocr import ocr_service as _id_ocr
from pearnly_dms.line import out as out

logger = logging.getLogger(__name__)

# 建单/新建时写满的地址块键（与网页确认页逐键同形）
_ADDR_BLOCK_KEYS = (
    "house_no",
    "building",
    "floor",
    "room",
    "village",
    "moo",
    "soi",
    "road",
    "province_id",
    "district_id",
    "subdistrict_id",
    "zipcode_id",
)
# 新建客户的身份字段键（与网页 create 分支同形）
_CREATE_ID_KEYS = ("prefix_id", "name", "people_id", "tax_id", "birthday_be", "phone")


# ── LINE 出口小工具（老产品的 _out 那一层） ────────────────────────────────
def _push(line_user_id: str, text: str) -> None:
    line.push(line_user_id, [{"type": "text", "text": text}])


def _reply(reply_token: str, text: str) -> None:
    line.reply(reply_token, [{"type": "text", "text": text}])


def _send(line_user_id: str, message: Dict[str, Any]) -> None:
    line.push(line_user_id, [message])


def _enqueue(job: str, fn, *args) -> None:
    """把重活丢后台。webhook 必须立刻返回，否则 LINE 会超时重发。

    ⚠️ **参数必须按形参名传**。后台 worker 是 `method(**kwargs)`（frappe 的
    `background_jobs.execute_job`），这里原来写成 `arg0/arg1/...`，看着像位置参数，
    实际会在 worker 里 `TypeError` —— webhook 这边只看到"已入队"，用户那边表现为
    **发完照片一直没有回卡**，从日志之外看不出是谁的错。所以按函数签名映射。

    实现在 `dms_out.enqueue`（LINE 出口那一层，老产品的 `_out.make_spawn` 同位置），
    这样别的流（订车执行体）用同一套，不必各写一份。
    """
    try:
        out.enqueue(job, fn, *args)
    except Exception:
        # 入队失败不能静默：用户会一直等一张永远不来的卡
        logger.exception("[dms flow] 后台任务入队失败")
        raise


# ── webhook 入口 ───────────────────────────────────────────────────────────
def handle_image(binding: dict, line_user_id: str, message_id: str) -> None:
    """绑定用户发图片 → 后台走识别。"""
    if not message_id:
        return
    _enqueue("image", process_image, binding, line_user_id, message_id)


def handle_postback(binding: dict, line_user_id: str, reply_token: str, postback: dict) -> None:
    """确认按钮：核对 nonce → 清 nonce（防双写）→ 执行。"""
    from urllib.parse import parse_qs

    pb = {k: v[0] for k, v in parse_qs(postback.get("data") or "").items()}
    action = pb.get("action")

    # 逐问按钮（`data="qa:<action>[:<value>]"`）直接归逐问状态机；
    # datetimepicker 选中的日期在 `postback.params` 里，一并带过去。
    if str(postback.get("data") or "").startswith("qa:"):
        from pearnly_dms.booking.conversation import booking_qa as booking_qa

        booking_qa.handle_postback(
            binding["tenant_id"],
            line_user_id,
            postback.get("data") or "",
            postback.get("params") or {},
            reply_token,
        )
        return

    # 订车阶段的预览确认 / 取消 / 重试归建单执行体（客户档的写档动作留本文件）。
    from pearnly_dms.booking.conversation import booking_flow as booking_flow

    if action in booking_flow.BOOKING_ACTIONS:
        booking_flow.handle_postback(binding, line_user_id, reply_token, action, pb)
        return

    sess = store.get_session(line_user_id)
    payload = (sess or {}).get("payload") or {}

    # 查询那一族（含菜单第 5 项）：自己的状态与权限门都在 `query_flow` 里。
    from pearnly_dms.query import query_flow as query_flow

    if action in query_flow.QUERY_ACTIONS:
        query_flow.handle_postback(binding, line_user_id, reply_token, action, pb, sess)
        return

    if action == cards.ACT_RESET:
        store.clear_session(line_user_id)
        _reply(reply_token, cards.TXT_RESET)
        return

    # 菜单层：选菜单项 / 建档后继续订车 / 重拍。**不消费 nonce**，须在下方的写档守卫之前。
    from pearnly_dms.menu import menu_flow as menu_flow

    if action in menu_flow.MENU_ACTIONS:
        menu_flow.handle_postback(binding, line_user_id, reply_token, action, pb, sess)
        return

    # 审批那一族（提审 / 选人 / 改派 / 批 / 拒）：一次性由存储的原子认领保证，
    # 所以这里**不消费 nonce**，但必须排在下方的写档守卫之前。
    from pearnly_dms.approval import approval_flow as approval_flow

    if action in approval_flow.APPROVAL_ACTIONS:
        approval_flow.handle_postback(binding, line_user_id, reply_token, action, pb, sess)
        return

    if action == cards.ACT_KEEP:
        # 零写入不等于零后果：它会接着开订车逐问。nonce **只校验不消费**，
        # 否则翻聊天记录点任意一张旧卡都能白开一轮会话。
        if not store.verify_nonce(sess, pb.get("nonce")):
            _reply(reply_token, cards.TXT_EXPIRED)
            return
        _reply(reply_token, cards.TXT_KEEP)
        # 保留旧数据 = **零写入**。差异卡进订车时必须把 DMS 主档快照传下去，
        # 不能展示本次输入的新值、最终建单却重读旧值。
        menu_flow.after_customer_saved(
            binding,
            line_user_id,
            endpoint_id=str(payload.get("endpoint_id") or ""),
            customer_id=str(payload.get("customer_id") or ""),
            draft=payload.get("master_draft") or payload.get("draft") or {},
            mode=str(payload.get("mode") or ""),
            same_data=True,
            summary=payload.get("master_summary") or payload.get("summary"),
        )
        return

    # 编辑：线上早把"LINE 里逐字段改"退役了 —— 点 [แก้ไข] 就是**打开浏览器编辑页**。
    # 与 `ACT_KEEP` 同理，**只校验不消费 nonce**（用户可能来回点几次），所以也必须排在
    # 下面的写档守卫之前。
    if action == cards.ACT_EDIT:
        _open_browser_editor(line_user_id, reply_token, pb, sess)
        return

    # 写动作：确认守卫（reviewing 态 + nonce 吻合）**原子清 nonce**；不符/过期 → 绝不写。
    # 清掉之后同一个 nonce 的第二次点击必然 mismatch（防双击双写）。
    payload = store.consume_nonce(line_user_id, "reviewing", pb.get("nonce"))
    if payload is None:
        _reply(reply_token, cards.TXT_EXPIRED)
        return

    if action == cards.ACT_CREATE:
        _enqueue("create", write_create, binding, line_user_id, payload)
    elif action == cards.ACT_UPDATE:
        _enqueue("update", write_update, binding, line_user_id, payload)
    elif action == cards.ACT_PICK:
        cid = pb.get("cid") or ""
        valid = {str(c.get("customer_id")) for c in (payload.get("candidates") or [])}
        if cid not in valid:
            _reply(reply_token, cards.TXT_EXPIRED)
            return
        # 相似认领 = 无差异的 overwrite，认领到所选客户号
        _enqueue("update", write_update, binding, line_user_id, {**payload, "customer_id": cid})
    else:
        _reply(reply_token, cards.TXT_EXPIRED)


# ── 编辑入口（线上行为：开浏览器编辑页，不再逐字段问） ─────────────────────
def _open_browser_editor(
    line_user_id: str, reply_token: str, pb: Dict[str, Any], sess: Optional[dict]
) -> None:
    """回一个"打开编辑页"的按钮；历史编辑态先退役再开页。

    照搬老产品 `services/line_dms/customer_edit_entry.py`：

    · 只有 `reviewing`（当前）与 `editing`（**老产品的历史态**）两张卡认；
      别的态/过期 nonce → 说过期，绝不静默当成功。
    · `editing` 态进来时**保留草稿、只把 nonce 换掉**再开页：用户在 LINE 里打了一半的
      文字不能因为"这条链退役了"就丢掉（那是老产品的原话与做法）。
    · 拿不到编辑页地址（没配 LIFF 也没站点地址）→ **说一句"打不开，请重来"**，
      不给一个按不动的按钮。
    """
    state = (sess or {}).get("state")
    nonce = str(pb.get("nonce") or "")
    if state not in {"reviewing", "editing"} or not store.verify_nonce(sess, nonce, state):
        _reply(reply_token, cards.TXT_EXPIRED)
        return
    if state == "editing":
        payload = {
            key: value
            for key, value in ((sess or {}).get("payload") or {}).items()
            if key != "editing_field"
        }
        payload["nonce"] = secrets.token_hex(8)
        store.set_session(line_user_id, "reviewing", payload)
        nonce = str(payload["nonce"])

    from pearnly_dms.editor import edit_link as dms_edit_link

    if not dms_edit_link.url(nonce, customer=True):
        _reply(reply_token, cards.TXT_EDIT_UNAVAILABLE)
        return
    line.reply(reply_token, [cards.edit_entry_card(nonce)])


# ── collecting：识别 ───────────────────────────────────────────────────────
def process_image(binding: dict, line_user_id: str, message_id: str) -> None:
    """下载 + 识别身份证。成功存 id_card；材料齐了自动查重。"""
    user_id = binding["user_id"]
    content = line.download_message_content(message_id)
    if not content:
        _push(line_user_id, cards.TXT_BLURRY)
        return
    try:
        ep, ocr, _ = _id_ocr.recognize_id_card(
            user_id, content, f"line_{message_id}.jpg", "", None,
            billing_reference=f"idcard:line:{message_id}",
        )
    except _id_ocr.DmsOcrError as exc:
        _push(line_user_id, ocr_error_text(exc))
        return

    if ocr.get("needs_review"):
        # 读不清 / 校验位对不上 → 打回，按**真实原因**说话；不查重
        _push(line_user_id, ocr_review.review_text(ocr.get("missing_fields") or []))
        return

    id_card = _id_ocr.editable_id_card(ocr["id_card"])
    payload = merge_session(
        binding,
        line_user_id,
        {"id_card": id_card, "endpoint_id": str(ep.get("id") or ""), "id_card_mid": message_id},
        keep=("phone", "mode"),
    )
    if payload.get("phone"):
        run_dedup(binding, line_user_id, id_card, payload["phone"], str(ep.get("id") or ""))
    else:
        _push(line_user_id, cards.TXT_ASK_PHONE)


# ── collecting → reviewing：查重四分支 ─────────────────────────────────────
def run_dedup(
    binding: dict, line_user_id: str, id_card: dict, phone: str, endpoint_id: Optional[str]
) -> None:
    user_id = binding["user_id"]
    sess = store.get_session(line_user_id)
    sess_payload = (sess or {}).get("payload") or {}
    mode = str(sess_payload.get("mode") or "")
    id_card_mid = sess_payload.get("id_card_mid")

    ep_name = endpoint_id or _id_ocr.resolve_dms_endpoint(user_id, None)
    if isinstance(ep_name, dict):
        ep_name = ep_name.get("name")
    if not ep_name:
        _push(line_user_id, cards.TXT_NO_ENDPOINT)
        return

    # 台账候选：DMS 搜索有"新客隐身期"，刚推过的同尾号客户按号直读核对
    tail = str(id_card.get("people_id") or "")[-4:]
    cands = _id_ocr.recent_dms_customer_ids_by_tail(tail)
    res = dms_intake.recognize_lookup(
        ep_name,
        people_id=id_card.get("people_id", ""),
        name=id_card.get("name") or "",
        ocr_address=id_card.get("address") or {},
        phone=phone,
        fallback_customer_ids=cands,
    )
    if not res.get("ok"):
        fr = res.get("error_friendly") or {}
        _push(line_user_id, fr.get("th") or cards.TXT_LOOKUP_FAIL)
        return

    scenario = res.get("scenario")
    field_diffs = res.get("field_diffs") or []
    geo = res.get("geo") or {}
    draft_vals = draft.build_draft(id_card, geo, res.get("prefixes") or [], phone)
    summary = draft.build_summary(draft_vals, geo)
    nonce = secrets.token_hex(8)
    base = {
        "draft": draft_vals,
        "summary": summary,
        "endpoint_id": ep_name,
        "nonce": nonce,
        "field_diffs": [],  # 默认无差异；exact_diff 分支覆写成真 diffs
        "id_card": id_card,
        "phone": phone,
        "mode": mode,
        "id_card_mid": id_card_mid,
    }

    if scenario == "none":
        payload = {**base, "scenario": "none"}
        card = cards.new_customer_card(summary, nonce)
    elif scenario == "exact":
        payload = {**base, "scenario": "exact_diff", "customer_id": res["match"]["customer_id"]}
        if field_diffs:
            master_draft, master_summary = draft.master_snapshot(
                (res.get("match") or {}).get("current_fields") or {}
            )
            has_admin = draft.has_admin_creds(ep_name)
            display = draft.display_diffs(field_diffs, geo)
            # 主按钮按**花名册角色 + 审批策略**解出（搬自老产品的 `exact_diff_card`）：
            # 销售 + 该类改动要审 → 提审卡；有写权 → 直接更新；都没有 → 只提示去设置。
            from pearnly_dms.approval import approval_flow as approval_flow

            card, needs_approval = approval_flow.exact_diff_card(
                str(binding.get("user_id") or ""), display, has_admin, nonce, summary
            )
            payload.update(
                field_diffs=field_diffs,
                has_admin=has_admin,
                display_diffs=display,
                master_draft=master_draft,
                master_summary=master_summary,
                approval=needs_approval,
            )
        else:
            # 菜单层未搬 → 统一走"资料一致"卡（老产品直拍路径就是这样）
            card = cards.same_customer_card(summary, nonce)
    else:  # similar
        cands2 = [
            {
                "customer_id": str(c.get("customer_id") or ""),
                "cuscode": c.get("cuscode", ""),
                "name": c.get("name", ""),
                "people_id": c.get("people_id", ""),
            }
            for c in (res.get("candidates") or [])
        ]
        payload = {**base, "scenario": "similar", "candidates": cands2}
        card = cards.candidates_card(cands2, nonce)

    store.set_session(line_user_id, "reviewing", payload)
    _send(line_user_id, card)


# ── reviewing → 执行写档 ───────────────────────────────────────────────────
def write_create(binding: dict, line_user_id: str, payload: dict) -> None:
    d = payload.get("draft") or {}
    fields = {k: d.get(k, "") for k in _CREATE_ID_KEYS}
    block = {k: d.get(k, "") for k in _ADDR_BLOCK_KEYS}
    addresses = {"": dict(block), "_ct": dict(block), "_sd": dict(block)}
    execute(
        binding,
        line_user_id,
        payload,
        mode="create",
        customer_id=None,
        fields=fields,
        addresses=addresses,
        field_diffs=[],
    )


def write_update(binding: dict, line_user_id: str, payload: dict) -> None:
    d = payload.get("draft") or {}
    field_diffs = payload.get("field_diffs") or []
    # **只写变了的字段** + people_id/name 必填项；没变的字段不进 fields → 保留 DMS 现值。
    # 相似认领 = 空 field_diffs + payload.customer_id 指向所选候选，天然落回本路径。
    fields = {"people_id": d.get("people_id", ""), "name": d.get("name", "")}
    for diff in field_diffs:
        fields[diff["field"]] = diff.get("new", "")
    execute(
        binding,
        line_user_id,
        payload,
        mode="overwrite",
        customer_id=payload.get("customer_id"),
        fields=fields,
        addresses=None,
        field_diffs=field_diffs,
    )


def execute(
    binding: dict,
    line_user_id: str,
    payload: dict,
    *,
    mode: str,
    customer_id: Optional[str],
    fields: Dict[str, Any],
    addresses: Optional[Dict[str, Dict[str, Any]]],
    field_diffs: List[dict],
) -> None:
    user_id = binding["user_id"]
    name = str(fields.get("name") or "")
    people_id = str(fields.get("people_id") or "")

    ep_name = payload.get("endpoint_id") or None
    if not ep_name:
        _push(line_user_id, cards.TXT_NO_ENDPOINT)
        return

    result = dms_intake.push_idcard_fields(
        ep_name,
        fields=fields,
        mode=mode,
        customer_id=customer_id,
        addresses=addresses,
    )
    success = bool(result.get("success"))
    # 推送台账：LINE 推的也要出现在记录页。trigger 位保持 id_card，
    # request_body.trigger='line_dms' 标源、带 field_diffs 快照（与网页同一台账）。
    _write_push_log(
        user_id=user_id,
        endpoint=ep_name,
        business_key=str(result.get("customer_id") or ""),
        name=name,
        success=success,
        mode=mode,
        people_id_tail=people_id[-4:],
        field_diffs=field_diffs,
        result=result,
    )

    if success:
        # create 被幂等/撞码转更新时，回执如实说"已更新"
        actual = result.get("mode") or mode
        _push(line_user_id, cards.receipt_text(result.get("customer_id") or "", name, actual))
        # 落档后的分叉交给菜单层：`mode=booking`/缺省 → **接着开订车逐问**；
        # `mode=customer`（菜单 1 只建档）→ 收尾不提订车。
        from pearnly_dms.menu import menu_flow as menu_flow

        menu_flow.after_customer_saved(
            binding,
            line_user_id,
            endpoint_id=str(payload.get("endpoint_id") or ""),
            customer_id=str(result.get("customer_id") or ""),
            draft=payload.get("draft") or {},
            name=name,
            mode=str(payload.get("mode") or ""),
            summary=payload.get("summary"),
        )
    elif result.get("error_code") == "ERR_DMS_ADMIN_AUTH":
        _push(line_user_id, cards.TXT_ADMIN_AUTH_FAIL)  # 不谎称已自动通知
    else:
        fr = result.get("error_friendly") or {}
        _push(line_user_id, fr.get("th") or cards.TXT_SAVE_FAIL)


def _write_push_log(
    *,
    user_id: str,
    endpoint: str,
    business_key: str,
    name: str,
    success: bool,
    mode: str,
    people_id_tail: str,
    field_diffs: List[dict],
    result: Dict[str, Any],
) -> None:
    """写一条推送台账。**台账失败不能影响业务结果**（用户已经存成功了）。"""
    try:
        doc = frappe.new_doc("Pearnly Push Log")
        doc.status = "success" if success else "failed"
        doc.endpoint = endpoint
        # 触发方式取我们台账的 Select 选项（manual/line/auto）—— 这条是 LINE 推的。
        # 老产品那边这个位置写的是 "id_card"（它自己的表），**我们表里没有这个选项**，
        # 照抄会直接校验失败（踩过）：业务结果不受影响（台账失败已兜住），但记录就丢了。
        doc.trigger = "line"
        doc.attempt = 1
        doc.business_key = business_key
        doc.http_status = 200 if success else 0
        doc.elapsed_ms = int(result.get("elapsed_ms") or 0)
        doc.request_body = json.dumps(
            {
                "adapter": "mrerp_dms",
                "trigger": "line_dms",
                "mode": mode,
                "people_id_tail": people_id_tail,
                "field_diffs": field_diffs,
            },
            ensure_ascii=False,
        )
        doc.response_body = json.dumps(result.get("response_body") or {}, ensure_ascii=False)
        doc.error_msg = str(result.get("error_code") or "")[:500]
        doc.actor = user_id
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        logger.warning("[dms flow] 写推送台账失败（不影响业务结果）", exc_info=True)


# ── 会话/文案小工具 ─────────────────────────────────────────────────────────
def merge_session(
    binding: dict, line_user_id: str, add: dict, keep: tuple, sess: Optional[dict] = None
) -> dict:
    """把新料并进 `collecting` 会话：**只保留 keep 列的旧键**（丢掉 reviewing 残留的
    nonce/diffs），其余用新料覆盖。"""
    if sess is None:
        sess = store.get_session(line_user_id)
    old = (sess or {}).get("payload") or {}
    payload = {k: old.get(k) for k in keep if old.get(k)}
    payload.update(add)
    store.set_session(line_user_id, "collecting", payload)
    return payload


def ocr_error_text(exc: "_id_ocr.DmsOcrError") -> str:
    """**按真实原因**回话：余额/配置/系统错各说各的，只有真读不清才叫重拍。

    老产品的实案：余额为 0 的 402 曾被统一说成"拍不清"，清晰卡也被打回，
    排障方向直接被带偏。
    """
    if exc.code == "dms.no_endpoint":
        return cards.TXT_NO_ENDPOINT
    if exc.code == "insufficient_balance":
        return cards.TXT_NO_CREDIT
    if exc.code in ("ocr.id_card_unreadable", "ocr.empty_file", "ocr.file_too_large"):
        return cards.TXT_BLURRY
    return cards.TXT_SYSTEM_ERROR
