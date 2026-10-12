"""网页「自动录入规则」页的接口（浏览器会话；小助手的机器接口在 `machine.py`）。"""
import frappe

from pearnly_cowork.folder_intake import rules


@frappe.whitelist()
def listing():
    return rules.listing()


@frappe.whitelist(methods=["GET"])
def computers():
    """网页设置「连接」只读：连着的电脑、在不在线、网页可读取的文件夹（改只在电脑上的 CC 里）。"""
    return rules.computers()


@frappe.whitelist()
def options(connection, account_set_key=None):
    # 读目录 / 存货科目组都锁这条连接行，与小助手心跳、刷新目录并发时可能死锁：只读请求，回滚重试即可。
    for attempt in range(3):
        try:
            return rules.options(connection, account_set_key)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist(methods=["POST"])
def save(values):
    return rules.save(values)


@frappe.whitelist(methods=["POST"])
def set_enabled(name, enabled):
    return rules.set_enabled(name, enabled)


@frappe.whitelist(methods=["POST"])
def delete(names):
    return rules.delete(names)


@frappe.whitelist(methods=["POST"])
def run_now(name):
    return rules.run_now(name)


@frappe.whitelist()
def runs(name):
    return rules.runs(name)


# CC 对话页里的「定时任务」（frontend/assistant 的 scheduled-tasks，同本页功能，界面照 Claude，用户 2026-10-12）
# 用到的词条：按前缀从已装 App 的术语表取键，按当前语言翻好一次给出，CC 不另存一份翻译。
TERM_PREFIXES = ("cowork.folder.", "intake.target.", "intake.catalog.", "intake.agent.", "intake.mrerpMenu.",
                 "intake.mrerpDefault.", "intake.dir.", "intake.field.direction", "intake.col.status",
                 "intake.installerMissing", "connect.state.")
TERM_WORDS = ("Company", "Posting Kind", "Service", "Stock", "Payment Method", "Inventory account group", "Credit",
              "Cash", "Save", "Saved", "Delete", "Connect", "System URL", "Account", "Password", "used by {0} items",
              "Delete {0} items permanently?", "Delete {0} item permanently?", "Cancel", "Edit")


def _term_keys() -> list:
    keys = set(TERM_WORDS)
    for app in frappe.get_installed_apps():
        path = frappe.get_app_path(app, "i18n", "messages.tsv")
        try:
            with open(path, encoding="utf-8") as handle:
                rows = [line.rstrip("\n").split("\t") for line in handle][1:]
        except OSError:
            continue
        for row in rows:
            key = row[4] if len(row) > 4 and row[4] else row[0]
            if key.startswith(TERM_PREFIXES):
                keys.add(key)
    return sorted(keys)


@frappe.whitelist(methods=["GET"])
def terms():
    return {key: frappe._(key) for key in _term_keys()}
