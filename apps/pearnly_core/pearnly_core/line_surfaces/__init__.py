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
  - ``restore(before: dict)``：把站点配置换回 ``before``（部署日志里的 BEFORE 行），旧菜单从没删过，原编号仍有效。

- 部署（``scripts/production/deploy-run.sh`` 的 surfaces 步骤）在每个产品网页容器里跑 ``python -m pearnly_core.line_surfaces sync``：
  有漂移才发布，发布前打 BEFORE 行，发布后再 ``drift()`` 回读，不一致即部署失败；``check`` 只读报告（演练与部署后回读用）。

本模块只依赖 Frappe 与声明的模块，不导入任何产品。
"""
import importlib
import json

import frappe

HOOK = "pearnly_line_surfaces"


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


def check_site(product: str) -> dict:
    """当前站点（已 init / connect）每个表面的只读对照。"""
    rows = {}
    for module in declared(product):
        reason = module.ready()
        if reason:
            rows[module.SURFACE] = {"skip": reason}
            continue
        state = module.drift()
        rows[module.SURFACE] = {**state, "ok": clean(state)}
    return rows


def sync_site(product: str, emit=print) -> dict:
    """当前站点每个表面：有漂移才发布，发布后回读；回读仍不一致抛错（部署失败）。"""
    rows = {}
    for module in declared(product):
        reason = module.ready()
        if reason:
            rows[module.SURFACE] = {"skip": reason}
            continue
        state = module.drift()
        if clean(state):
            rows[module.SURFACE] = {**state, "ok": True, "published": False}
            continue
        before = dict(frappe.conf.get(module.CONFIG) or {})
        emit("BEFORE " + json.dumps({"site": frappe.local.site, "surface": module.SURFACE, "config": before},
                                    ensure_ascii=False, sort_keys=True))
        module.publish()
        frappe.db.commit()  # publish() 末尾 reconcile() 排的重挂任务是 enqueue_after_commit
        after = module.drift()
        if not clean(after):
            raise RuntimeError(f"{frappe.local.site} {module.SURFACE} still differs after publish: "
                               + json.dumps(after, ensure_ascii=False, sort_keys=True)[:400])
        rows[module.SURFACE] = {**after, "ok": True, "published": True, "was": state}
    return rows


def restore_site(product: str, surface: str, before: dict) -> dict:
    for module in declared(product):
        if module.SURFACE == surface:
            module.restore(before)
            frappe.db.commit()
            return module.drift()
    raise RuntimeError(f"{frappe.local.site} has no surface {surface} for {product}")
