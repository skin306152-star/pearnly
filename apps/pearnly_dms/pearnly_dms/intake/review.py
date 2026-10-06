"""A changed upstream customer requires a NEW server-owned review."""
import secrets

from pearnly_core import line_session as store
from pearnly_dms.contracts.customer_merge import secondary_addresses, split
from pearnly_dms.intake.customer_diff import master_name_if_close
from pearnly_dms.intake import draft
from pearnly_dms.line import cards, out
from pearnly_dms.line.errors import friendly
from pearnly_dms.line.identity import matches
from pearnly_dms import i18n


def diff_card(user_id, display, has_admin, nonce, summary):
    """有差异的老客户卡：能改（本人 DMS 账号有写权，或花名册是管理员）就给「更新」，否则只给「用旧资料」。

    原来走审批（销售提审 → 管理员批）的那一支已删（用户 2026-10-05：审批不做）；审批策略本来就是关的，
    这里就是原来实际走的那一支。"""
    from pearnly_dms.operators import roster

    role = (roster.get_profile(str(user_id)) or {}).get("dms_role") or ""
    primary = "update" if (has_admin or role == "admin") else "none"
    return cards.diff_card(display, nonce, primary=primary, summary=summary)


def conflict_notice(conflict):
    text = friendly("ERR_DMS_CUSTOMER_CHANGED", lang=i18n.line_lang())
    for change in conflict.get("conflicts") or []:
        label = cards.FIELD_LABELS_TH.get(change["field"], change["field"])
        text += "\n" + str(label) + ": " + str(change["old"]) + " → " + str(change["new"])
    return text


def build(user_id, payload, conflict, *, has_admin, existing=False):
    """重新出卡。``existing``＝建档时撞上已有客户：姓名按查重命中时同一条规则——识别结果与主档
    足够像就沿用主档写法（缓存没跟上时走到这条路，2026-10-06 实测两次结果不一致）。"""
    current = conflict["current"]
    # 卡上只列真会写进 DMS 的差异：联系/寄送地址已有内容的不改（与写入层同一条规则）。
    desired = secondary_addresses(conflict["desired"], current)
    if existing and "name" in desired:
        desired["name"] = master_name_if_close(current.get("name"), desired["name"])
    diffs = [{"field": key, "old": str(current.get(key) or ""), "new": str(value)}
             for key, value in desired.items() if str(current.get(key) or "") != str(value)]
    fields, addresses = split(desired)
    customer = {**current, **desired}
    _, summary = draft.master_snapshot(customer)
    master_draft, master_summary = draft.master_snapshot(current)
    nonce = secrets.token_hex(8)
    updated = {key: value for key, value in payload.items()
               if key not in {"dms_retry", "candidates", "approval", "display_diffs"}}  # approval：旧会话残留
    updated.update(nonce=nonce, customer_id=conflict["customer_id"], scenario="exact_diff",
                   customer_baseline=current, draft=customer, summary=summary, field_diffs=diffs,
                   write_fields=fields, write_addresses=addresses,
                   master_draft=master_draft, master_summary=master_summary)
    if diffs:
        display = draft.display_diffs(diffs, {})
        card = diff_card(user_id, display, has_admin, nonce, summary)
        updated.update(display_diffs=display, has_admin=has_admin)
    else:
        card = cards.same_customer_card(summary, nonce)
    return updated, card


def reopen(binding, line_user_id, payload, result, *, reply_token="", expected=None):
    if not matches(binding, line_user_id):
        return False
    current = store.get_session(line_user_id)
    expected = expected or {"state": "reviewing", "payload": {**payload, "nonce": None}}
    if (not current or current.get("state") != expected.get("state")
            or current.get("payload") != expected.get("payload")):
        return False
    from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
    endpoint = resolve_dms_endpoint(binding["user_id"], payload.get("endpoint_id"))
    updated, card = build(binding["user_id"], payload,
        result["response_body"]["customer_conflict"], has_admin=draft.has_admin_creds(endpoint),
        existing=result.get("error_code") == "ERR_DMS_CUSTOMER_EXISTS")
    if not store.replace_session_snapshot(line_user_id, current, "reviewing", updated):
        return False
    out.push_or_reply_text(line_user_id, friendly(result["error_code"], lang=i18n.line_lang()), reply_token)
    out.send(line_user_id, card)
    return True
