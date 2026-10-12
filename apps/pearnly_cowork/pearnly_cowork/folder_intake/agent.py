"""小助手最近一次文件夹同步上报的本机情况（允许读取的根目录、下级文件夹名、文件夹录入版本），按连接存在不淘汰的
短期状态里（`pearnly_core.execution.state`）。

只是给规则页选文件夹、显示「需升级小助手」用；不写数据库、不碰连接行（不与心跳 / 领任务争锁）。
原先放在 `frappe.cache`：部署、同步测试环境都会清缓存，清完到小助手下一次同步之间，网页和 CC 都显示「电脑离线、
没有文件夹」（2026-10-10 测试撞上）。丢了照样等小助手下一次同步补上。
"""
from frappe.utils import now_datetime
from pearnly_core.execution import state as pearnly_state

KEY = "cowork:folder:agent:"
TTL = 7 * 86400
# 小助手超过这么久没来同步，规则页提示「小助手没在线或版本太旧」。
STALE_SECONDS = 2 * 3600


def remember(connection, *, roots, version, health=None, device="", pending=None):
    """`health`：各规则的文件夹在不在（{规则名: ok | root_missing | folder_missing}，1.3.19 起的小助手报）；
    `device`：小助手所在电脑的名字（1.3.23 起报），网页文件夹下拉用它区分是哪台电脑；
    `pending`：各任务待处理的文件数（{规则名: 份数}，1.4.4 起报，任务列表与提醒用）；`roots[].counts` / `partial`
    是各文件夹现有的文件数（新任务卡片与表单用）。旧小助手不报，就没有这几样（不知道，不用 0 冒充）。"""
    pearnly_state.store().set_value(KEY + connection, {"roots": roots, "version": str(version or "")[:40],
                                              "health": health or {}, "device": str(device or "").strip()[:60],
                                              "pending": pending or {}, "at": now_datetime().isoformat()},
                           expires_in_sec=TTL)


def read(connection):
    # 不用请求内的进程缓存：CC 的推送线程一轮里会连着读（同 `toolrun.fresh` 说明）
    value = pearnly_state.store().get_value(KEY + connection, use_local_cache=False)
    return value if isinstance(value, dict) else {}


def fresh(state):
    """这台小助手最近同步过文件夹录入（新版、在线）。"""
    from frappe.utils import get_datetime

    if not state.get("at"):
        return False
    return (now_datetime() - get_datetime(state["at"])).total_seconds() <= STALE_SECONDS
