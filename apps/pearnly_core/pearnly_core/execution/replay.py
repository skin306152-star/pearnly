"""回放压测（2026-10-02 用户经压测窗口：压测不再花识别费）：测试租户上不调谷歌，产品交回这个租户以前真识别过的同一份原件的
结果，交回前按实测时延等；排队、入库、扣费、推送、网页照常跑。

只在**测试租户**由站点配置打开：``pearnly_ocr_replay`` = ``{"enabled": 1, "profile": "normal" | "peak"}``，或直接给
``"median"`` / ``"p95"``（秒）。站点名不在 :data:`TEST_SITES` 里一律当没开（正式站误开也照常真识别，并记一条错误日志）；
架构检查拦住在仓库的生产配置里写这个键。

开着时这个站点的付费识别调用（本机 ``provider.post``、交给 Cloud Run 的 ``forward``）一律拒绝（:class:`Refused`）：
没做回放的入口在压测时直接失败，不会悄悄花钱。
"""
import math
import random
import re

import frappe

CONFIG = "pearnly_ocr_replay"
# 测试租户（生产机上的 testcowork / testerp / testdms 系列）与本地实验室站点；别的站点名一律不认。
TEST_SITES = re.compile(r"test(?:cowork|erp|dms)[0-9]{0,2}\.pearnly\.com|[a-z0-9-]+\.localhost")
# 时延档（秒）：normal = 2026-10-02 m7 实测 Cloud Run 段（派出到回复）中位 5.8、p95 19.7；peak = 晚高峰，p95 调到 40。
PROFILES = {"normal": (5.8, 19.7), "peak": (5.8, 40.0)}
MAX_DELAY = 300  # 共用层暂缓最长 300 秒
Z95 = 1.6448536269514722


class Refused(RuntimeError):
    """回放开着时有入口要发付费识别调用：拒绝（按程序错误收尾，不重放、不扣费）。"""


def settings():
    """开着且是测试租户返回 ``{"median", "p95"}``，否则 ``None``。"""
    try:
        value = (frappe.conf or {}).get(CONFIG)
        site = getattr(frappe.local, "site", None)
    except RuntimeError:  # 没有站点上下文（离线调用）
        return None
    if not isinstance(value, dict) or not value.get("enabled"):
        return None
    if not isinstance(site, str) or not TEST_SITES.fullmatch(site):
        _misconfigured(site)
        return None
    median, p95 = PROFILES.get(value.get("profile") or "normal", PROFILES["normal"])
    median = _seconds(value.get("median"), median)
    p95 = max(median, _seconds(value.get("p95"), p95))
    return {"median": median, "p95": p95}


def active():
    return settings() is not None


def refuse_paid(label):
    """付费识别调用之前调用：回放开着就拒绝。"""
    if active():
        raise Refused(f"OCR replay is on for this site; refusing a paid call ({label})")


def delay(config, rand=random.random):
    """按对数正态分布抽一次等待秒数：中位 = ``median``，95% 分位 = ``p95``。"""
    sigma = math.log(config["p95"] / config["median"]) / Z95 if config["p95"] > config["median"] else 0.0
    # Box-Muller：只用一个可替换的均匀随机源，测试可复现
    u1, u2 = max(rand(), 1e-12), rand()
    z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
    return max(0.5, min(MAX_DELAY, config["median"] * math.exp(sigma * z)))


def _seconds(value, default):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= MAX_DELAY \
        else default


def _misconfigured(site):
    try:
        if not frappe.cache.get_value("pearnly:ocr-replay-refused"):
            frappe.cache.set_value("pearnly:ocr-replay-refused", 1, expires_in_sec=3600)
            frappe.log_error(title="OCR replay refused on a non-test site",
                             message=f"{CONFIG} is set on {site!r}; recognition stays real.")
    except Exception:
        pass
