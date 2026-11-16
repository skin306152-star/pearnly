"""对话里对留着等人的代录单做的选择，经领单回复送到执行电脑（多产品共用：ERP 的 CC 桌面版通道与 Cowork 的连接通道同一份；
CC 桌面版 1.12.23 ③④）。

代录遇到配方外的业务选择框时，那台电脑把框、单据窗口和 Express 原样留着（``held``），回执带框的样子与指纹。人在对话里选了某个钮：
站点按 ``send`` 给那台电脑记一条，电脑下一次领单（3 秒一次）取走、在取任务线程里核框没变再点；存完由电脑用原回执号补报。
不另开轮询、不多发请求；信箱放缓存，10 分钟过期（电脑不在线就作废，人再选一次）；只发给 1.12.23 起的电脑（旧版不认，不发）。
"""
import frappe

from pearnly_core.cc_desktop.wire import queue_contracts
from pearnly_core.cc_desktop.wire.request_contracts import version_tuple

PREFIX = "cc-operator-controls:"
TTL = 600
MIN_COMPANION = "1.12.23"


def supports(version) -> bool:
    return version_tuple(version) >= version_tuple(MIN_COMPANION)


def send(scope, batch, doc, action, digest, button) -> bool:
    """给 ``scope``（ERP：电脑名；Cowork：``cw:<连接>:<电脑>``）记一条指令；形状不合格回假、不记。"""
    item = queue_contracts.operator_control({"action": action, "batch": batch, "doc": doc, "digest": digest,
                                             "button": button})
    if not scope or item is None:
        return False
    key = PREFIX + str(scope)
    try:
        found = frappe.cache.get_value(key) or []
        found = [old for old in found if isinstance(old, dict) and (old.get("batch"), old.get("doc")) != (batch, doc)]
        found.append(item)
        frappe.cache.set_value(key, found[-queue_contracts.OPERATOR_CONTROLS_MAX:], expires_in_sec=TTL)
        return True
    except Exception:  # noqa: BLE001 — 记不下：人再选一次
        return False


def take(scope, version) -> list:
    """（领单）这处的指令；看过即清。电脑版本不够不取（留着过期，旧版不认这个键）。"""
    if not scope or not supports(version):
        return []
    key = PREFIX + str(scope)
    try:
        found = frappe.cache.get_value(key) or []
        if found:
            frappe.cache.delete_value(key)
        return [item for item in found if isinstance(item, dict)]
    except Exception:  # noqa: BLE001
        return []
