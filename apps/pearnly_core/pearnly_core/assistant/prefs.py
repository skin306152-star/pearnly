"""对话页（CC）外壳的个人排列，按人存在服务端、跨设备同步（用户 2026-10-16 选「公司可拖 + 置顶可拖」时说「按人记住」）：

- 存什么：侧栏手排的公司组、置顶与组里对话的顺序（order）、侧栏「分组和排序」（view）、右侧会话栏卡片的顺序与开合（cards），
  由对话页按项交来（frontend/assistant/src/lib/prefs-sync.ts）。侧栏各组的折叠、会话栏停靠时开着没有仍按设备记，不在这里。
- 存在哪：当前用户自己的用户默认值（Frappe User Default，`DefaultValue` 表 parent = 用户），键 `pearnly_cc_layout`，一份 JSON。
  同侧栏页面顺序（desk/navigation.py 的 `pearnly_sidebar_order_*`）一样只按会话用户读写，不收别的用户名：只能读写自己的。
- 谁为准：按项合并，各项后写的为准。对话页改了哪项只交哪项（如 {"cards": {"collapsed": [...]}}），这里并进已有的那份，
  不动别的份、别的项（第 7 轮审查：整份覆盖时，没重取过的设备改一项就把别的设备改过的几项打回旧值）；
  各设备打开、回到前台时取，本机缓存只让打开时立刻生效、离线照样能用。
- 上限：合并后序列化 32 KB（各列表远用不到；超了拒收，对话页照样按本机的用）。只收 JSON 对象，顶层只认上面三项。
"""
import json

import frappe
from frappe import _

KEY = "pearnly_cc_layout"
PARTS = ("order", "view", "cards")
MAX_BYTES = 32 * 1024


def _user() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return user


def read() -> dict | None:
    """当前用户存下的那份；没存过（或存坏了）回 None，对话页据此把本机原有的迁上来。"""
    raw = frappe.defaults.get_user_default(KEY, user=_user())
    try:
        value = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _too_big(prefs: dict) -> bool:
    return len(json.dumps(prefs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_BYTES


def clean(prefs) -> dict:
    """收下的那份：JSON 对象，顶层只留认识的几项（各自是对象），序列化后不超过上限；不合规拒收。"""
    if isinstance(prefs, str):
        try:
            prefs = json.loads(prefs)
        except ValueError:
            frappe.throw(_("Invalid value"))
    if not isinstance(prefs, dict):
        frappe.throw(_("Invalid value"))
    kept = {part: prefs[part] for part in PARTS if part in prefs}
    if any(not isinstance(value, dict) for value in kept.values()):
        frappe.throw(_("Invalid value"))
    if _too_big(kept):
        frappe.throw(_("Invalid value"))
    return kept


def save(prefs) -> dict:
    """把交来的几项并进当前用户的那份（交了的项换掉，没交的份、项照旧），回合并后的整份。"""
    user = _user()
    kept = clean(prefs)
    merged = read() or {}
    for part, fields in kept.items():
        merged[part] = {**(merged.get(part) if isinstance(merged.get(part), dict) else {}), **fields}
    if _too_big(merged):
        frappe.throw(_("Invalid value"))
    frappe.defaults.set_user_default(KEY, json.dumps(merged, ensure_ascii=False, separators=(",", ":")), user=user)
    return merged
