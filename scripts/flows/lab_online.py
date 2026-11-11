"""实验栈造出在线的电脑（写账类真模型场景与流程共用；11-11 已知问题「实验栈造的电脑永远判离线」）。

场景、流程在实验栈里造的 CC 桌面版电脑（Cowork `companion.credentials.register`、ERP `cc_desktop.devices` 的合成行）从不发
心跳：站点按「最近 3 分钟内报过到」判在线（Cowork `credentials._fresh`，ERP `cc_desktop.contracts.online`，都按 UTC 比），
跑过几分钟就判「CC 桌面版不在线」，要读科目表 / 主档的单停在「等电脑」、开始卡不出题（11-11 own-tax 真模型两次各约 1 美元白跑）。
手写 ``last_seen_at = frappe.utils.now_datetime()`` 也不行：那是站点时区（曼谷 +7），按 UTC 读是 7 小时后的未来，照样不算。

用法（实验栈里，``lab`` 是 `scripts/flows/run.py` 的 ``Lab``）::

    from lab_online import KeepOnline

    with KeepOnline(lab, site, cowork_connection=连接名):      # Cowork：这条连接上所有没吊销的电脑密钥
        ...页面步骤 / 等识别...
    with KeepOnline(lab, site, erp_device=电脑名):              # ERP：CC 桌面版共用通道的这台电脑

- 进 ``with`` 先同步报到一次：一台都报不上（连接 / 电脑不存在）直接抛错，不白跑；之后后台每 ``BEAT_SECONDS`` 秒报一次
  （在线窗口 180 秒，留两次余量）。报到失败只打印、不打断场景，下一次再报。
- 出 ``with`` 就停（成功、失败、超时都停），等后台线程退出（最多一个间隔）。之后电脑照常在 3 分钟内转离线，不留常驻。
- 报到走产品自己的写法（Cowork `credentials.record_seen`、ERP `devices.now()`），都是 UTC；只在实验栈用，不碰生产。
"""
from __future__ import annotations

import threading

BEAT_SECONDS = 60
ONLINE_SECONDS = 180  # 两个产品的在线窗口（`credentials.ONLINE_SECONDS`、`cc_desktop.contracts.ONLINE_SECONDS`，测试锁住）
KEYS_MAX = 20


def cowork_code(connection: str) -> str:
    """站点里跑的一段：这条连接上没吊销的电脑密钥都按心跳的写法报到一次（UTC），回报了几台。"""
    return f"""
from pearnly_cowork.companion import credentials
rows = frappe.get_all(credentials.DOCTYPE, filters={{"connection": {connection!r}, "revoked_at": ["is", "not set"]}},
                      fields=["name", "token_generation"], limit_page_length={KEYS_MAX})
for row in rows:
    credentials.record_seen(row.name, int(row.token_generation or 0))
frappe.db.commit()
print(json.dumps({{"beat": len(rows)}}))
"""


def erp_code(device: str) -> str:
    """站点里跑的一段：CC 桌面版共用通道的这台电脑报到一次（UTC），回报了几台。"""
    return f"""
from pearnly_core.cc_desktop import devices
found = 1 if frappe.db.exists(devices.DEVICE, {device!r}) else 0
if found:
    frappe.db.set_value(devices.DEVICE, {device!r}, "last_seen_at", devices.now(), update_modified=False)
frappe.db.commit()
print(json.dumps({{"beat": found}}))
"""


class KeepOnline:
    """``with`` 期间让实验栈造的电脑一直判在线（见文件头）。"""

    def __init__(self, lab, site: str, *, cowork_connection: str = "", erp_device: str = "", every: float = BEAT_SECONDS):
        if bool(cowork_connection) == bool(erp_device):
            raise ValueError("give exactly one of cowork_connection / erp_device")
        if not 0 < every < ONLINE_SECONDS / 2:
            raise ValueError("beat more often than half the online window")
        self.lab, self.site, self.every = lab, site, every
        self.code = cowork_code(cowork_connection) if cowork_connection else erp_code(erp_device)
        self.beats = 0
        self._stop = threading.Event()
        self._thread = None

    def beat(self) -> int:
        found = int((self.lab.python(self.site, self.code) or {}).get("beat") or 0)
        self.beats += 1
        return found

    def _loop(self):
        while not self._stop.wait(self.every):
            try:
                self.beat()
            except Exception as error:  # 报一次没报上不打断场景：下一次再报（在线窗口留了两次余量）
                print(f"lab_online: beat failed: {str(error)[:200]}", flush=True)

    def __enter__(self):
        if not self.beat():
            raise RuntimeError("lab_online: no computer to keep online (connection / device not found)")
        self._thread = threading.Thread(target=self._loop, name="lab-online", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(self.every + 5)
        return False
