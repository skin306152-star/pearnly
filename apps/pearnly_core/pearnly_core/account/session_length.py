"""登录保持多久（用户 2026-10-15：「30天吧，那有那么容易丢手机和电脑，而且现在 cc 移动端是要绑定公司本地 exp 使用的，
本地客户端则是长期使用场景」）。

站点会话时长用框架原生的系统设置 `session_expiry`（小时:分钟）：产品站点默认 30 天。只在还是空或框架默认值（170:00，
约 7 天）时改，管理员在系统设置里改过别的值就保留；控制面不动。每次 migrate 都核一次（新开的站点不跑旧补丁）。
已登录的会话按登录时的时长到期，之后登录的按新时长。"""
import frappe

PRODUCT_SESSION_EXPIRY = "720:00"
FRAMEWORK_DEFAULTS = {"", "170:00"}


def ensure_session_expiry():
    if frappe.conf.get("pearnly_product") not in {"dms", "cowork", "erp"}:
        return
    current = str(frappe.db.get_single_value("System Settings", "session_expiry") or "").strip()
    if current in FRAMEWORK_DEFAULTS:
        frappe.db.set_single_value("System Settings", "session_expiry", PRODUCT_SESSION_EXPIRY)
