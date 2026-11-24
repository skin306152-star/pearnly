"""推送前冒出来的 MR.ERP 设置题（总表 CW-D-MS；评估 docs/work/2026-11-16-cc-guided-settings-评估.md）：这份 MR.ERP 账套这个方向
还要人选的导入菜单（``mrerp_menu``，subject = 单据类型）与主档默认值（``mrerp_default``，subject = 主档字段）——要问哪几项按
`pearnly_core.mrerp.needs.needed`，可选值是最近一次从 MR.ERP 读到的（`connections.service.mrerp_menu`），同待处理页
`assistant.pending_api.mrerp_needs`、CC 推送前的核对与网页推送前的弹窗；推送那一侧停在「要选菜单 / 默认值」用的是同一份视图
（`menus.resolve` / `masters`），所以卡上不问的推送也不会停在要选（11-24 守门测试）。

判定卡（`assistant.precheck_source.document`，推送目标是 MR.ERP 时）里的题：卡片项形状照 ERP 的 `express.precheck.mrerp_needed`
（``{kind, subject, label, value, source, options: [{code, name}]}``，选项显示「代码 · 名称」）。作答 :func:`remember` 只收此刻问的那几项、
值在选项里（不是就整批拒），记法走现成的 `choose_mrerp_menu / choose_mrerp_default`，记在这份账套名下、大家共用（同网页弹窗）。
不另查库、不另写规则。

（11-16 原稿还有一道 Express「存货科目组」题，出题条件是「合格组两个以上、没选过就问」，与推送的判据
`express_rules.push.stock_acc_group.new_item_group`（能证明用得最多的合格组照用、只有一个带上、证明不了又有几个才问）不一致；
判定卡已有按推送同一视图出的那道题（`precheck_source._stock_group_item`），11-24 主会话定删掉这段。）
"""
from __future__ import annotations

import frappe
from frappe import _

MRERP = "mrerp"
MENU, DEFAULT = "mrerp_menu", "mrerp_default"
KINDS = (MENU, DEFAULT)


def _item(kind, subject, label, options):
    return {"kind": kind, "subject": subject, "label": label, "value": "", "source": "", "options": options}


def _shown(code, name) -> str:
    return " · ".join(str(part) for part in dict.fromkeys((code, name)) if part)


def items(connection, account_set_key, direction) -> dict:
    """这份 MR.ERP 账套此刻还要人选的设置题：``{"known": 读到过没有, "items": [卡片项]}``；不是 MR.ERP 账套的不问。"""
    from pearnly_core.mrerp import needs
    from pearnly_cowork.connections import service as connections

    if connections.adapter_of(connection) != MRERP:
        return {"known": True, "items": []}
    view = connections.mrerp_menu(connection, account_set_key)
    found = needs.needed(view, direction or None)
    out = [_item(MENU, kind, _(f"intake.mrerpMenu.{kind}"),
                 [{"code": option["idmenu"], "name": _shown(option["idmenu"], option.get("label"))}
                  for option in view["kinds"][kind]["options"]]) for kind in found["menus"]]
    out += [_item(DEFAULT, field, _(f"intake.mrerpDefault.{field}"),
                  [{"code": option["code"], "name": _shown(option["code"], option.get("name"))}
                   for option in view["defaults"]["fields"][field]["options"]]) for field in found["defaults"]]
    return {"known": bool(found["known"]), "items": out}


def remember(connection, account_set_key, direction, answers) -> int:
    """记下作答（``answers``：``[{kind, subject, value}]``）：只收 :func:`items` 此刻问的那几项、值在那一项的选项里，否则整批拒
    （`frappe.ValidationError`，什么都不记）。回记下的条数。"""
    from pearnly_cowork.connections import service as connections

    asked = {(item["kind"], item["subject"]): item for item in items(connection, account_set_key, direction)["items"]}
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
        else:
            connections.choose_mrerp_default(connection, account_set_key, subject, value)  # loop-query: 一张卡答的几项
    if checked:
        # 同待处理页当场选：开着的公司页「记住的选择」当场重取
        from pearnly_cowork.assistant import tools

        tools.announce_mrerp_choices(connection, account_set_key)
    return len(checked)
