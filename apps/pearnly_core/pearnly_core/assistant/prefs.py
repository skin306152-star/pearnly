"""对话页（CC）外壳的个人排列，按人存在服务端、跨设备同步（用户 2026-10-16 选「公司可拖 + 置顶可拖」时说「按人记住」）：

- 存什么：侧栏手排的公司组、置顶与组里对话的顺序（order：companies、pinned，组里每一组一项 `group:<公司编号>`，最近
  `group:`）、侧栏「分组和排序」（view）、右侧会话栏卡片的顺序与开合（cards），由对话页按项交来
  （frontend/assistant/src/lib/prefs-sync.ts）。侧栏各组的折叠、会话栏停靠时开着没有仍按设备记，不在这里。
- 存在哪：当前用户自己的用户默认值（Frappe User Default，`DefaultValue` 表 parent = 用户），键 `pearnly_cc_layout`，一份 JSON。
  同侧栏页面顺序（desk/navigation.py 的 `pearnly_sidebar_order_*`）一样只按会话用户读写，不收别的用户名：只能读写自己的。
- 谁为准：按项合并，各项后写的为准。对话页改了哪项只交哪项（如 {"cards": {"collapsed": [...]}}），这里并进已有的那份，
  不动别的份、别的项（第 7 轮审查：整份覆盖时，没重取过的设备改一项就把别的设备改过的几项打回旧值）；交来 null 的项删掉
  （对话页去掉了一组）。组里每一组各是一项，所以两台设备各排各的组互不冲掉（审查：原来各组合成一项）。
  各设备打开、回到前台时取，本机缓存只让打开时立刻生效、离线照样能用。
- 上限：合并后序列化 32 KB。组里的各组合起来不超过 `GROUPS_BYTES`：收下时记上服务端的时刻（`saved`），超了先去掉最早存下的组
  （整组回到按排序方式排；对话页下次取到时跟着去掉），不因组多拒收整次——拒收时同一批的别的项也存不上，而且对话页每次回到前台
  重试都失败（审查）。公司组、置顶由对话页按字节截好再交。别的超限（各项本来远用不到）拒收，对话页照样按本机的用。
- 过渡：旧版对话页的「恢复默认设置」只交 companies、pinned 两个空列表，不知道组里的；这样的一次（两项都交、都空）把组里的一并清掉。
- 只收 JSON 对象，顶层只认上面三项。
"""
import json
import time

import frappe
from frappe import _

KEY = "pearnly_cc_layout"
PARTS = ("order", "view", "cards")
MAX_BYTES = 32 * 1024
IN_BYTES = 2 * MAX_BYTES  # 一次交来的上限（组里的超了在合并后按 GROUPS_BYTES 截，不拒收）
GROUP_FIELD = "group:"  # order 里组里的一组：group:<公司编号>，最近 group:
GROUPS_BYTES = 16 * 1024


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


def _size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _too_big(prefs: dict, limit: int = MAX_BYTES) -> bool:
    return _size(prefs) > limit


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
    groups = {field: value for field, value in kept.get("order", {}).items() if field.startswith(GROUP_FIELD)}
    if any(value is not None and not isinstance(value, dict) for value in groups.values()):
        frappe.throw(_("Invalid value"))
    if _too_big(kept, IN_BYTES):
        frappe.throw(_("Invalid value"))
    return kept


def _trim_groups(order: dict) -> None:
    """组里的各组合起来不超过 GROUPS_BYTES：最近存下的先留，超出的整组去掉。"""
    groups = sorted(((field, value) for field, value in order.items() if field.startswith(GROUP_FIELD)),
                    key=lambda item: item[1].get("saved") or 0 if isinstance(item[1], dict) else 0, reverse=True)
    left = GROUPS_BYTES
    for field, value in groups:
        size = _size({field: value})
        if size <= left:
            left -= size
        else:
            left = 0  # 更早存下的都不留（不跳过大的去留更早的小的）
            order.pop(field)


def save(prefs) -> dict:
    """把交来的几项并进当前用户的那份（交了的项换掉，交 null 的删掉，没交的份、项照旧），回合并后的整份。"""
    user = _user()
    kept = clean(prefs)
    merged = read() or {}
    now = int(time.time() * 1000)
    for part, fields in kept.items():
        old = dict(merged.get(part)) if isinstance(merged.get(part), dict) else {}
        if part == "order" and fields.get("companies") == [] and fields.get("pinned") == []:
            old = {field: value for field, value in old.items() if not field.startswith(GROUP_FIELD)}  # 「恢复默认设置」
        for field, value in fields.items():
            if value is None:
                old.pop(field, None)
            elif part == "order" and field.startswith(GROUP_FIELD):
                old[field] = {**value, "saved": now}
            else:
                old[field] = value
        if part == "order":
            _trim_groups(old)
        merged[part] = old
    if _too_big(merged):
        frappe.throw(_("Invalid value"))
    frappe.defaults.set_user_default(KEY, json.dumps(merged, ensure_ascii=False, separators=(",", ":")), user=user)
    return merged
