"""按需请求的「有活了」（多产品共用：ERP 的 CC 桌面版通道与 Cowork 的连接通道同一份，11-15）。

CC 桌面版空闲时每 15 秒问一次有没有要它读的东西（``request_worker``），领单每 3 秒一次；LINE 选完公司马上选客户时，预热的读回
要等下一个 15 秒才被领走。建请求时在缓存里给够得到的电脑（Cowork 是这条连接）记一笔，领单回复带 ``requests_waiting``，电脑
据此马上去领：不多发任何请求，空闲时电脑那边的耗用不变（领单本来就在问）。标记一次性、60 秒过期；只是叫醒，领不领、领哪件仍由
``lease_next`` 判（多叫一次只是多问一次）。
"""
import frappe

PREFIX = "cc-request-wake:"
TTL = 60


def ring(scopes) -> None:
    """这几处（电脑名、``cw:<连接>``）有新请求了。缓存出错不影响建请求。"""
    for scope in sorted({str(scope) for scope in scopes or () if scope}):
        try:
            frappe.cache.set_value(PREFIX + scope, 1, expires_in_sec=TTL)
        except Exception:  # noqa: BLE001 — 叫不醒就等电脑自己按间隔来问
            pass


def take(scope) -> bool:
    """（领单）这处有没有被叫过；看过即清。"""
    if not scope:
        return False
    try:
        found = frappe.cache.get_value(PREFIX + str(scope))
        if found:
            frappe.cache.delete_value(PREFIX + str(scope))
        return bool(found)
    except Exception:  # noqa: BLE001
        return False
