"""推送前冒出来的两类设置题（总表 CW-D-MS、CW-D-SG；评估 docs/work/2026-11-16-cc-guided-settings-评估.md）：

- MR.ERP 设置：这份 MR.ERP 账套这个方向还要人选的导入菜单（``mrerp_menu``，subject = 单据类型）与主档默认值（``mrerp_default``，
  subject = 主档字段）——要问哪几项按 `pearnly_core.mrerp.needs.needed`，可选值是最近一次从 MR.ERP 读到的（`connections.service.mrerp_menu`），
  同待处理页 `assistant.pending_api.mrerp_needs` 与网页推送前的弹窗；
- 存货科目组（``stock_group``，subject 为空）：Express 账套记存货、这份账套读到两个以上合格的组且还没选过时问
  （`connections.service.stock_acc_group`，同新建录入与连接页）。

不上菜单，是判定卡（`assistant.precheck_source`）与定时任务引导（MR.ERP 账套那一步）里的题：卡片项形状照 ERP 的
`express.precheck.mrerp_needed`（``{kind, subject, label, value, source, options: [{code, name}]}``）。作答 :func:`remember` 只收此刻
问的那几项、值在选项里（不是就整批拒），记法走现成的 `choose_mrerp_menu / choose_mrerp_default / choose_stock_acc_group`，
记在这份账套名下、大家共用（同网页弹窗）。不另查库、不另写规则。
"""
from __future__ import annotations

import frappe
from frappe import _

MRERP, EXPRESS = "mrerp", "express"
MENU, DEFAULT, STOCK_GROUP = "mrerp_menu", "mrerp_default", "stock_group"
KINDS = (MENU, DEFAULT, STOCK_GROUP)


def _item(kind, subject, label, options):
    return {"kind": kind, "subject": subject, "label": label, "value": "", "source": "", "options": options}


def _mrerp(connection, account_set_key, direction):
    from pearnly_core.mrerp import needs
    from pearnly_cowork.connections import service as connections

    view = connections.mrerp_menu(connection, account_set_key)
    found = needs.needed(view, direction or None)
    items = [_item(MENU, kind, _(f"intake.mrerpMenu.{kind}"),
                   [{"code": option["idmenu"], "name": option.get("label") or option["idmenu"]}
                    for option in view["kinds"][kind]["options"]]) for kind in found["menus"]]
    items += [_item(DEFAULT, field, _(f"intake.mrerpDefault.{field}"),
                    [{"code": option["code"], "name": " · ".join(part for part in (option["code"], option.get("name")) if part)}
                     for option in view["defaults"]["fields"][field]["options"]]) for field in found["defaults"]]
    return {"known": bool(found["known"]), "items": items}


def _stock_group(connection, account_set_key):
    from pearnly_cowork.connections import service as connections

    view = connections.stock_acc_group(connection, account_set_key) or {}
    groups = view.get("groups") or []
    if not view.get("known"):
        return {"known": False, "items": []}
    if view.get("chosen") or len(groups) < 2:
        return {"known": True, "items": []}
    return {"known": True, "items": [_item(STOCK_GROUP, "", _("Inventory account group"),
                                           [{"code": group["acccod"], "name": group.get("label") or group["acccod"]}
                                            for group in groups])]}


def items(connection, account_set_key, direction, *, stock=False) -> dict:
    """这份账套此刻还要人选的设置题：``{"known": 读到过没有, "items": [卡片项]}``。MR.ERP 账套问导入菜单与默认值；Express 账套
    只在 ``stock``（记存货）时问存货科目组；别的不问。"""
    from pearnly_cowork.connections import service as connections

    adapter = connections.adapter_of(connection)
    if adapter == MRERP:
        return _mrerp(connection, account_set_key, direction)
    if adapter == EXPRESS and stock:
        return _stock_group(connection, account_set_key)
    return {"known": True, "items": []}


def remember(connection, account_set_key, direction, answers, *, stock=False) -> int:
    """记下作答（``answers``：``[{kind, subject, value}]``）：只收 :func:`items` 此刻问的那几项、值在那一项的选项里，否则整批拒
    （`frappe.ValidationError`，什么都不记）。回记下的条数。"""
    from pearnly_cowork.connections import service as connections

    asked = {(item["kind"], item["subject"]): item for item in items(connection, account_set_key, direction, stock=stock)["items"]}
    checked = []
    for answer in answers or ():
        if not isinstance(answer, dict):
            frappe.throw(_("Invalid request"))
        key = (answer.get("kind"), str(answer.get("subject") or ""))
        item = asked.get(key)
        value = answer.get("value")
        if item is None or not isinstance(value, str) or value not in {option["code"] for option in item["options"]}:
            frappe.throw(_("Invalid request"))
        checked.append((key, value))
    for (kind, subject), value in checked:
        if kind == MENU:
            connections.choose_mrerp_menu(connection, account_set_key, subject, value)  # loop-query: 一张卡答的几项
        elif kind == DEFAULT:
            connections.choose_mrerp_default(connection, account_set_key, subject, value)  # loop-query: 一张卡答的几项
        else:
            connections.choose_stock_acc_group(connection, account_set_key, value)
    if any(kind in (MENU, DEFAULT) for (kind, _subject), _value in checked):
        # 同待处理页当场选：开着的公司页「记住的选择」当场重取
        from pearnly_cowork.assistant import tools

        tools.announce_mrerp_choices(connection, account_set_key)
    return len(checked)
