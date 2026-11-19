# -*- coding: utf-8 -*-
"""存货科目组(ISACC ACCCOD)判据层 —— 候选筛选 + 「这次建库存品挂哪个组」三档解析。纯函数。

与 agent_reporting 分工:那边管心跳上报的存取(净化 + 写 config),这里只回答「拿到这份候选
之后怎么办」。销项/采购两个 mapper、端点 PATCH、异常卡认同一份判据,才不会出现「卡里选得到、
推的时候还是拦」。agent_reporting 顶部 re-import fit_stock_acc_groups 当 facade,老调用点不动。
"""

from __future__ import annotations

from typing import Any, Dict, List, NamedTuple, Optional

from pearnly_core.express_rules.targets import stock_groups
from pearnly_core.express_rules.receipts import request_payload

# 零库存主档的账套要建第一个库存品时,缺的到底是哪一样 —— 两个码分开,补救卡给的指引才不一样:
#   missing  一个合格的存货科目组都没有 → 会计得先去 Express 建(组本身不存在,问他选也没得选)
#   required 合格的有好几个 → 只是没人拍板用哪个 → 弹卡选一次,写进端点 config 后不再问
REASON_ACC_GROUP_MISSING = "stock_acc_group_missing"
REASON_ACC_GROUP_REQUIRED = "stock_acc_group_required"


class StockAccGroupChoice(NamedTuple):
    """acccod = 定下来的组(空 = 没定);fail_reason 非空 → 拦下这张票;auto = 系统替客户定的。"""

    acccod: str
    fail_reason: str
    auto: bool


def fit_stock_acc_groups(config: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """本端点可选的存货科目组(小助手判 fit=True 的候选)· 不查库。

    单一名单源:端点 PATCH 用它校验选择、异常卡用它渲染下拉、下面的三档解析用它数候选 ——
    三处认同一份名单,不会出现「卡里选得到、存的时候 400」。
    """
    return stock_groups.fit((config or {}).get("reported_stock_acc_groups"))


def resolve_stock_acc_group(config: Optional[Dict[str, Any]]) -> StockAccGroupChoice:
    """账套零库存主档时,这次建库存品挂哪个存货科目组;定不下来就说清缺的是哪一样。

    端点显式配过 stock_acccod → 一律用它,不看候选数(会计拍过板的决定,不因心跳刷新翻案)。
    没配则按合格候选数分三档:
      1 个  → 自动用它。没得选,问了也是白问 —— 每张票弹一次卡纯粹是挡会计的路。
      0 个  → REASON_ACC_GROUP_MISSING,缺的是科目本身,得先去 Express 建一个。
      ≥2 个 → REASON_ACC_GROUP_REQUIRED,弹卡选一次(选完写端点 config,以后不再问)。

    ★不按 used_by 众数自动选:真账套 69SINCER 的 37 个库存品里 ST01 挂了 19 个(最多),可它的
    存货科目是 14-01-03-00 อุปกรณ์สำนักงาน(办公设备 = 固定资产),挂它的那 19 个商品名全是
    「ห้ามใช้ / ไม่ใช้แล้วนะคะ」的废弃档;真正在用的是 DM(17 个 · 11-04-01-00 วัตถุดิบคงเหลือ)。
    众数会挑中作废的那组,所以这里只认过了存货科目判据的 fit 名单,不看谁挂得多。
    """
    cfg = config or {}
    chosen = str(cfg.get("stock_acccod") or "").strip()
    if chosen:
        return StockAccGroupChoice(chosen, "", False)
    groups = fit_stock_acc_groups(cfg)
    if len(groups) == 1:
        return StockAccGroupChoice(str(groups[0]["acccod"]).strip(), "", True)
    return StockAccGroupChoice(
        "", REASON_ACC_GROUP_REQUIRED if groups else REASON_ACC_GROUP_MISSING, False
    )


def stock_master_count(config: Optional[Dict[str, Any]]) -> Optional[int]:
    """账套里库存品的总件数(心跳 catalog_fingerprint 报的);没报回 None。"""
    try:
        return int(((config or {}).get("catalog_fingerprint") or {})["stock_master_count"])
    except (TypeError, KeyError, ValueError):
        return None


def most_used_group(config: Optional[Dict[str, Any]]) -> str:
    """合格组里挂得最多的那个组码(推荐值,点了才算);没有合格组回空。"""
    groups = fit_stock_acc_groups(config)
    if not groups:
        return ""
    return str(max(groups, key=lambda group: int(group.get("used_by") or 0))["acccod"]).strip()


def new_item_group(payload: Optional[Dict[str, Any]], config: Optional[Dict[str, Any]]) -> StockAccGroupChoice:
    """这张要在 Express 里新建库存品(库存行没有存货码)、账套又没选过存货科目组时,新品挂哪个组(ERP、Cowork 同用,11-19)。

    `resolve_stock_acc_group` 只管零库存主档那一档(mapper 在零主档时调它);账套里已有库存品时 mapper 不带组,电脑照抄挂得最多的
    那组(CC 桌面版 `stock_template.pick` 的众数),那组的存货科目不像存货科目就在写账时停下(``STOCK_ACC_GROUP_UNFIT``;11-19 真机
    69EXP\\TEST:ST01 → 14-01-03-00 办公设备)。服务端只拿得到合格的组(心跳只报 fit 的,带各组挂了几件)和库存品总数:
      · 能证明挂得最多的是合格组(它挂的件数多过不合格的组加起来) → 照旧交电脑,回空;
      · 证明不了 → 同零主档那一档:合格的 1 个就用它(``auto``),几个 → REASON_ACC_GROUP_REQUIRED(选一次、按账套记住),
        一个都没有 → REASON_ACC_GROUP_MISSING(要在 Express 里配)。
    载荷或端点已带组、没有要新建的库存品 → 回空。没带存货码、名字与账套里已有的库存品同名的行不是新建：电脑照名取已有那件
    （CC 桌面版 `stock_purchase` 复用同名库存品，``choices`` 同名不问存货对照），不为它定组。"""
    from pearnly_core.express_rules.push.choices import norm_name
    from pearnly_core.express_rules.push.common import ITEM_MODE_STOCK

    payload, cfg = payload or {}, config or {}
    if str(payload.get("stock_acccod") or "").strip() or str(cfg.get("stock_acccod") or "").strip():
        return StockAccGroupChoice("", "", False)
    existing = {norm_name(row.get("name")) for row in cfg.get("reported_products") or ()
                if isinstance(row, dict) and str(row.get("code") or "").strip()
                and str(row.get("kind") or "stock") == "stock" and norm_name(row.get("name"))}
    if not any(isinstance(item, dict) and item.get("item_mode") == ITEM_MODE_STOCK and not str(item.get("code") or "").strip()
               and norm_name(item.get("name")) not in existing for item in payload.get("items") or ()):
        return StockAccGroupChoice("", "", False)
    groups = fit_stock_acc_groups(cfg)
    total = stock_master_count(cfg)
    if groups and total is not None:
        counts = [int(group.get("used_by") or 0) for group in groups]
        if max(counts) > total - sum(counts):
            return StockAccGroupChoice("", "", False)  # 挂得最多的就是合格组:电脑照它抄
    return resolve_stock_acc_group({"reported_stock_acc_groups": cfg.get("reported_stock_acc_groups")})


def describe_from_request(request_body: Any, reported: Any) -> Dict[str, str]:
    """从推送载荷直接反查这一组的存货科目号/名。推送日志卡与导出复核表共用一个入口。

    载荷键名 `stock_acccod` 只写在这里一处 —— 各写一遍就是各持一份对 mapper 的假设,
    改了键名两边一起静默降级(卡片少个徽标、导出少一列),没有一处会红。
    """
    return describe_stock_acc_group(reported, request_payload(request_body).get("stock_acccod"))


def stock_acc_group_label(group: Dict[str, str]) -> str:
    """「码 · 科目号 · 科目名」—— 会计要拿推送日志卡和导出 Excel 互相对照,
    分隔符和字段顺序必须同源,不能两边各拼一次(选组下拉也用这一份)。"""
    return stock_groups.label(group)


def describe_stock_acc_group(reported: Any, acccod: str) -> Dict[str, str]:
    """按 acccod 从上报候选表反查这一组的存货科目号/名(空 dict = 查不到)。

    载荷里只留了码(ST01 / DM 这类账套内部代号),光看码认不出存货记进了哪个科目 —— 推送日志
    和导出表要给人看的是「11-04-01-00 วัตถุดิบคงเหลือ」。查不到就不编,少显一行也别显错科目。
    """
    code = str(acccod or "").strip()
    if not code or not isinstance(reported, list):
        return {}
    for g in reported:
        if isinstance(g, dict) and str(g.get("acccod") or "").strip() == code:
            return {
                "acccod": code,
                "stock_acc": str(g.get("stock_acc") or "").strip(),
                "stock_acc_name": str(g.get("stock_acc_name") or "").strip(),
            }
    return {"acccod": code}
