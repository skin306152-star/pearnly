"""LINE 识别卡片「保存」（用户 11-18）：按钮出不出、点了之后回什么。推送本身在 `push.application.card_save`。"""
import frappe
from frappe import _

PUSH_TARGETS = ("express", "mrerp")  # 同 `push.application.card_save.PUSH_TARGETS`
SAVE_ACTION = "cowork_save"  # 卡片「保存」的 postback（`cards._card` 里写字面量：产品地图按它认入口）


def pushable(record) -> bool:
    """卡片上出不出「保存」：只有推往 ERP 的票（「仅识别」的票本来就存好了，只有「编辑」）。"""
    return record.get("intake_target") in PUSH_TARGETS and bool(record.get("push_connection"))


def reply(name, reply_token):
    """postback 入口：推完回一句；停下回原因 + 快捷回复「编辑」（打开录入页这张票）。"""
    from pearnly_core import line
    from pearnly_core.account import line_login

    try:
        from pearnly_cowork.push.application import card_save

        result = card_save.save(name)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        line.reply(reply_token, [{"type": "text", "text": frappe.utils.strip_html(str(error)) or _("Not permitted")}])
        return
    frappe.db.commit()
    if result.get("stop"):
        uri = line_login.entry_url("/desk/pearnly-intake/new/" + name)
        line.reply(reply_token, [{"type": "text", "text": result["stop"][:2000], "quickReply": {"items": [
            {"type": "action", "action": {"type": "uri", "label": str(_("Edit"))[:20], "uri": uri}}]}}])
        return
    line.reply(reply_token, [{"type": "text", "text": _("Entering")}])  # 同列表「录入中」
