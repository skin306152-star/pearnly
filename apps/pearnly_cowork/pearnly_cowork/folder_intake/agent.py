"""小助手最近一次文件夹同步上报的本机情况（允许读取的根目录、下级文件夹名、文件夹录入版本），按连接存在缓存里。

只是给规则页选文件夹、显示「需升级小助手」用；不写数据库、不碰连接行（不与心跳 / 领任务争锁）。
缓存丢了等小助手下一次同步（几分钟内）自然补上。
"""
import frappe
from frappe.utils import now_datetime

KEY = "cowork:folder:agent:"
TTL = 7 * 86400
# 小助手超过这么久没来同步，规则页提示「小助手没在线或版本太旧」。
STALE_SECONDS = 2 * 3600


def remember(connection, *, roots, version, health=None):
    """`health`：各规则的文件夹在不在（{规则名: ok | root_missing | folder_missing}，1.3.19 起的小助手报）。"""
    frappe.cache.set_value(KEY + connection, {"roots": roots, "version": str(version or "")[:40],
                                              "health": health or {}, "at": now_datetime().isoformat()},
                           expires_in_sec=TTL)


def read(connection):
    value = frappe.cache.get_value(KEY + connection)
    return value if isinstance(value, dict) else {}


def fresh(state):
    """这台小助手最近同步过文件夹录入（新版、在线）。"""
    from frappe.utils import get_datetime

    if not state.get("at"):
        return False
    return (now_datetime() - get_datetime(state["at"])).total_seconds() <= STALE_SECONDS
