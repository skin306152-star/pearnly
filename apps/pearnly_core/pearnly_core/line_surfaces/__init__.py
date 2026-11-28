"""站外 LINE 表面随部署同步（多产品共用：ERP / Cowork / DMS 都经这里）。

11-26 事故：正式租户 ris 的 DMS 富菜单自 9 月开户后从没随部署更新——富菜单发布只在开户 / 改渠道时做一次，
部署只换镜像。用户硬规矩：三个产品以后不允许出现这样的情况。所以：

- 每个产品在自己的 ``hooks.py`` 声明自己的站外表面（ADR-009）::

      pearnly_line_surfaces = [{"product": "cowork", "module": "pearnly_cowork.line.menu"}]

  ``product`` 与站点配置 ``pearnly_product`` 对上才用（一个站点装了别的产品的 App 也不会往本频道发别的产品的菜单）。
- 声明的模块提供同一组函数（与它自己的 ``publish()`` 同一份算法，不另算一份）：

  - ``SURFACE``：表面名（报告里用）；``CONFIG``：站点配置里记已发布编号的键；
  - ``ready() -> str``：这个站点能不能发（空串 = 能；否则是跳过原因，与开户 ``set_line`` 同一条件）；
  - ``drift() -> dict``：只读，LINE 上现挂的与这一版代码应有的对照，``{"behind": [...], "missing": [...], "default": bool}``
    （``default`` 只有管官方号默认菜单的产品给）；全空 / 为真 = 一致；
  - ``publish()``：幂等发布（按内容散列命名、重跑复用、不删旧菜单），并排已绑定用户重挂；
  - ``restore(before: dict)``：把站点配置换回 ``before``（部署日志里的 BEFORE 行），旧菜单从没删过，原编号仍有效；
  - ``release(key) -> (body, image)``：这一版这个键的菜单 body 与图（``explain`` 用它对照 LINE 上现挂的那套，说清改了什么）；
  - ``remount(line_user_id)``：按当前绑定 / 权限 / 语言同步挂这个人的个人菜单（产品自己的 ``sync``）。

- 部署（``scripts/production/deploy-run.sh`` 的 surfaces 步骤）在每个产品网页容器里跑 ``python -m pearnly_core.line_surfaces sync``：
  有漂移才发布，发布前打 BEFORE 行，发布后再 ``drift()`` 回读，不一致即部署失败；``check`` 只读报告（演练与部署后回读用）。

本模块只依赖 Frappe 与声明的模块，不导入任何产品。
"""
import importlib
import json
from urllib.parse import quote

import frappe

HOOK = "pearnly_line_surfaces"
# 从没发布过（站点配置里没有这个表面的键）：开户 ``set_line`` 才是第一次发布的地方；部署不替开户做第一次——
# DMS 的发布会换官方号默认菜单，开户没走完时不能被部署抢先换掉。部分键缺（加了语言 / 组合）照常算漂移、照常发布。
NEVER_PUBLISHED = "never published (onboarding set_line publishes first)"


def declared(product: str) -> list:
    """本站点上属于 ``product`` 的表面模块（按 hooks 声明顺序）。"""
    out = []
    for entry in frappe.get_hooks(HOOK) or []:
        if isinstance(entry, str):
            entry = json.loads(entry)
        if entry.get("product") == product:
            out.append(importlib.import_module(entry["module"]))
    return out


def clean(state: dict) -> bool:
    return not state.get("behind") and not state.get("missing") and state.get("default", True) is not False


def _skip(module) -> str:
    return module.ready() or ("" if frappe.conf.get(module.CONFIG) else NEVER_PUBLISHED)


def _short(value) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value
    return text if len(text) <= 60 else text[:57] + "..."


def _fields(old: dict, new: dict) -> list:
    """LINE 上现挂的 body 与这一版的 body 逐项对照：聊天栏字、尺寸、每格的位置与动作字段（label / displayText / data / uri…）。"""
    changes = []
    for field in ("chatBarText", "size", "selected"):
        if old.get(field) != new.get(field):
            changes.append(f"{field}: {_short(old.get(field))} -> {_short(new.get(field))}")
    old_areas, new_areas = old.get("areas") or [], new.get("areas") or []
    if len(old_areas) != len(new_areas):
        changes.append(f"cells: {len(old_areas)} -> {len(new_areas)}")
    for index, (a, b) in enumerate(zip(old_areas, new_areas), 1):
        if a.get("bounds") != b.get("bounds"):
            changes.append(f"cell {index} bounds: {_short(a.get('bounds'))} -> {_short(b.get('bounds'))}")
        before, after = a.get("action") or {}, b.get("action") or {}
        for field in sorted(set(before) | set(after)):
            if before.get(field) != after.get(field):
                changes.append(f"cell {index} {field}: {_short(before.get(field))} -> {_short(after.get(field))}")
    return changes


def explain(module, keys) -> dict:
    """落后的每个键：LINE 上现挂的那套与这一版差在哪（文字字段逐项列出；图逐字节比，读不到写 unknown）。只读（GET）。"""
    from pearnly_core import line

    current = dict(frappe.conf.get(module.CONFIG) or {})
    out = {}
    for key in keys:
        menu_id = current.get(key)
        body, image = module.release(key)
        row = {"text": [], "image": "unknown"}
        if not menu_id:
            out[key] = {"text": ["not published"], "image": "new"}
            continue
        try:
            row["text"] = _fields(module._request("GET", "richmenu/" + quote(menu_id, safe=""), missing_ok=True) or {}, body)
        except Exception as exc:
            row["text"] = ["unreadable: " + repr(exc)[:120]]
        try:
            response = line.request("GET", "https://api-data.line.me/v2/bot/richmenu/" + quote(menu_id, safe="") + "/content",
                                    timeout=30)
            if response.status_code == 200:
                row["image"] = "same" if response.content == image else "changed"
        except Exception:
            pass
        out[key] = row
    return out


def _mounts(module) -> dict:
    """当前有效绑定的人各自挂着的个人菜单（没有挂的不列）。只读（GET）。"""
    out = {}
    # bounded: 一个租户的有效 LINE 绑定（每人至多一条），与产品自己的 reconcile() 同一个名单
    for line_user_id in frappe.get_all("Pearnly LINE Binding", filters={"status": "active"}, pluck="line_user_id"):  # bounded: 同上
        menu_id = (module._request("GET", "user/" + quote(line_user_id, safe="") + "/richmenu", missing_ok=True) or {}).get(
            "richMenuId")
        if menu_id:
            out[line_user_id] = menu_id
    return out


def _remount(module, before: dict) -> dict:
    """发布后给每个有效绑定的人当场重挂（不等队列），再回读：发布前挂着个人菜单的人一个不少、每个人挂的都是这一版的菜单。
    发布前没挂、这次挂上的（例如以前一次重挂失败漏掉的）算补上，只计数。"""
    for line_user_id in frappe.get_all("Pearnly LINE Binding", filters={"status": "active"}, pluck="line_user_id"):  # bounded: 一个租户的有效 LINE 绑定
        module.remount(line_user_id)
    frappe.db.commit()
    after = _mounts(module)
    published = set((frappe.conf.get(module.CONFIG) or {}).values())
    stale = sorted(uid[:8] for uid, menu_id in after.items() if menu_id not in published)
    lost = sorted(uid[:8] for uid in before if uid not in after)
    result = {"mounted_before": len(before), "mounted_after": len(after)}
    if lost or stale:
        raise RuntimeError(f"{frappe.local.site} {module.SURFACE} remount readback: {len(before)} users had a personal menu "
                           f"before, {len(after)} after; lost: {lost}; not on this release: {stale}")
    return result


def check_site(product: str) -> dict:
    """当前站点（已 init / connect）每个表面的只读对照。"""
    rows = {}
    for module in declared(product):
        reason = _skip(module)
        if reason:
            rows[module.SURFACE] = {"skip": reason}
            continue
        state = module.drift()
        rows[module.SURFACE] = {**state, "ok": clean(state)}
        if not clean(state):
            rows[module.SURFACE]["changes"] = explain(module, state.get("behind", []) + state.get("missing", []))
    return rows


def sync_site(product: str, emit=print) -> dict:
    """当前站点每个表面：有漂移才发布，发布后回读；回读仍不一致抛错（部署失败）。"""
    rows = {}
    for module in declared(product):
        reason = _skip(module)
        if reason:
            rows[module.SURFACE] = {"skip": reason}
            continue
        state = module.drift()
        if clean(state):
            rows[module.SURFACE] = {**state, "ok": True, "published": False}
            continue
        before = dict(frappe.conf.get(module.CONFIG) or {})
        changes = explain(module, state.get("behind", []) + state.get("missing", []))
        emit("PLAN " + json.dumps({"site": frappe.local.site, "surface": module.SURFACE, "drift": state, "changes": changes},
                                  ensure_ascii=False, sort_keys=True))
        emit("BEFORE " + json.dumps({"site": frappe.local.site, "surface": module.SURFACE, "config": before},
                                    ensure_ascii=False, sort_keys=True))
        mounted = _mounts(module)  # loop-query: 每个产品只声明一个表面（富菜单），一个站点只查一次本站绑定
        module.publish()
        frappe.db.commit()  # publish() 末尾 reconcile() 排的重挂任务是 enqueue_after_commit（幂等，与下面当场重挂不冲突）
        users = _remount(module, mounted)  # loop-query: 同上，一个站点一次
        after = module.drift()
        if not clean(after):
            raise RuntimeError(f"{frappe.local.site} {module.SURFACE} still differs after publish: "
                               + json.dumps(after, ensure_ascii=False, sort_keys=True)[:400])
        rows[module.SURFACE] = {**after, "ok": True, "published": True, "was": state, "changes": changes, **users}
    return rows


def restore_site(product: str, surface: str, before: dict) -> dict:
    for module in declared(product):
        if module.SURFACE == surface:
            module.restore(before)
            frappe.db.commit()
            return module.drift()
    raise RuntimeError(f"{frappe.local.site} has no surface {surface} for {product}")
