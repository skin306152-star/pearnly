"""产品怎么接共用机器通道（多产品共用，ADR-025）：产品在自己的 ``hooks.py`` 里声明 ``pearnly_cc_desktop``，共用层只读它。

    pearnly_cc_desktop = {
        "allowed": "<点路径>",        # (user) -> bool：谁能把自己的电脑连上这个站点（在 CC 窗口里登录站点时登记）
        "job_changed": "<点路径>",    # (job: dict) -> None：一件写账有了新状态（同一事务里叫，产品把单号 / 原因写回自己的单据）
        "kinds": ["express_masters"], # 这个站点会请电脑做的按需请求种类
        "books_changed": "<点路径>",  # (device) -> None：可选；一台电脑报来的账套名单有了变化（同一事务里叫，产品排后台任务）
    }

没声明的站点（Cowork、DMS）：登记一律拒绝，机器方法一律 403，什么都不做。一个站点只装一个产品，取最后声明的那一份。
"""
import frappe

HOOK = "pearnly_cc_desktop"


def _declared(key):
    found = frappe.get_hooks(HOOK) or {}
    values = found.get(key) if isinstance(found, dict) else None
    if isinstance(values, list):
        return values
    return [] if values in (None, "") else [values]


def _path(key):
    values = [value for value in _declared(key) if isinstance(value, str) and value]
    return values[-1] if values else ""


def enabled() -> bool:
    return bool(_path("allowed"))


def allowed(user) -> bool:
    """``user`` 此刻能不能用共用通道（登记电脑、电脑以他的身份领单）。没声明的站点一律不能。"""
    path = _path("allowed")
    return bool(path and user and user not in ("Guest", "Administrator") and frappe.get_attr(path)(user))


def job_changed(view: dict) -> None:
    path = _path("job_changed")
    if path:
        frappe.get_attr(path)(view)


def books_changed(device: str) -> None:
    path = _path("books_changed")
    if path:
        frappe.get_attr(path)(device)


def kinds() -> list:
    return [value for value in dict.fromkeys(_declared("kinds")) if isinstance(value, str)]
