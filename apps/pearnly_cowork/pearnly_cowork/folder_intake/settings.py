"""文件夹自动录入的站点总闸 `pearnly_cowork_folder_intake`（缺省 = 全关）。

规则页、小助手接口、受理、自动推送、结果表格每一步都先读它；关掉后整条链停下，已有的票与推送不受影响。
写法：`true`，或 `{"enabled": 1, "ocr_inflight": 0, "push_pending": 5, "inline_max_mib": 15}`。非法值只关本功能。
`ocr_inflight`：同时在识别的文件夹单上限；0（缺省）= 自动，取商户识别并发的一半，另一半留给网页 / LINE。
"""
import frappe
from frappe import _

KEY = "pearnly_cowork_folder_intake"
DEFAULTS = {"ocr_inflight": 0, "push_pending": 5, "inline_max_mib": 15}
LIMITS = {"ocr_inflight": (0, 8), "push_pending": (1, 20), "inline_max_mib": (1, 20)}


def config():
    raw = frappe.conf.get(KEY)
    if raw in (True, 1):
        raw = {"enabled": 1}
    if not isinstance(raw, dict) or raw.get("enabled") not in (True, 1):
        return None
    values = dict(DEFAULTS)
    for key, (low, high) in LIMITS.items():
        value = raw.get(key, DEFAULTS[key])
        if type(value) is not int or not low <= value <= high:
            return None
        values[key] = value
    return values


def enabled():
    return config() is not None


def require():
    values = config()
    if values is None:
        frappe.throw(_("cowork.folder.disabled"), frappe.PermissionError)
    return values
