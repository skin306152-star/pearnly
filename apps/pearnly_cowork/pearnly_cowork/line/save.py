"""LINE 识别卡片「保存」（用户 11-18）：按钮出不出、点了之后回什么。推送本身在 `push.application.card_save`。"""
import frappe
from frappe import _

SAVE_ACTION = "cowork_save"  # 卡片「保存」的 postback（`cards._card` 里写字面量：产品地图按它认入口）
CANCEL_ACTION = "cowork_cancel"  # 卡片「取消」


def reply(name, reply_token, cancel=False):
    """postback 入口：推完回一句；停下回原因 + 快捷回复「编辑」（打开录入页这张票）。``cancel``：卡片「取消」。"""
    from pearnly_core import line
    from pearnly_core.account import line_login

    try:
        from pearnly_cowork.push.application import card_save

        result = card_save.cancel(name) if cancel else card_save.save(name)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        line.reply(reply_token, [{"type": "text", "text": frappe.utils.strip_html(str(error)) or _("Not permitted")}])
        return
    except Exception:
        # 意外的错（11-19 真机：一处写错的表名让保存直接抛错、LINE 一句都不回）：记日志，回「失败」，什么都不推
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.log_error(title="Cowork LINE card save failed", message=frappe.get_traceback()[-1500:])
        line.reply(reply_token, [{"type": "text", "text": _("Failed")}])
        return
    frappe.db.commit()
    if result.get("cancelled"):
        line.reply(reply_token, [{"type": "text", "text": _("Cancelled")}])
        return
    if result.get("saved") or result.get("kept"):
        line.reply(reply_token, [{"type": "text", "text": _("Saved")}])
        return
    if result.get("stop"):
        uri = line_login.entry_url("/desk/pearnly-intake/new/" + name)
        line.reply(reply_token, [{"type": "text", "text": result["stop"][:2000], "quickReply": {"items": [
            {"type": "action", "action": {"type": "uri", "label": str(_("Edit"))[:20], "uri": uri}}]}}])
        return
    line.reply(reply_token, [{"type": "text", "text": _("Entering")}])  # 同列表「录入中」
