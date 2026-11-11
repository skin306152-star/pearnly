"""账本汇总（没有页面）：

    bench --site <站点> execute pearnly_core.ai_usage.report.summary --kwargs '{"days": 30, "by": "day,purpose"}'
    bench --site all execute pearnly_core.ai_usage.report.summary --kwargs '{"days": 30}' | python3 scripts/ai-usage-total.py

``by`` 取 ``day`` / ``purpose`` / ``product`` / ``tenant`` / ``user`` / ``model`` / ``usd_source`` 任意几项（逗号分隔）。
回 ``{"site", "tenant", "product", "since", "until", "rows": [...], "total": {...}}``；美元按 ``THB_PER_USD`` 另给泰铢。
数据库里按组求和（不把明细取回来再加）。
"""
from decimal import Decimal

import frappe

from pearnly_core.ai_usage import contracts

DOCTYPE = "Pearnly AI Usage"
GROUPS = ("day", "purpose", "product", "tenant", "user", "model", "usd_source")
THB_PER_USD = Decimal(35)  # 与识别观察成本同一口径（`pearnly_cowork.ocr.engine.cost.THB_PER_USD`）
USD_PLACES = Decimal("0.000001")
MAX_GROUPS = 5000


def groups_of(by) -> list[str]:
    """``by`` → 合法的分组列（未知的忽略，保持给的次序、去重）。纯函数。"""
    seen = []
    for part in str(by or "").split(","):
        part = part.strip()
        if part in GROUPS and part not in seen:
            seen.append(part)
    return seen


def shape(rows, groups) -> tuple[list[dict], dict]:
    """数据库按组求和的结果 → 报表行与合计（美元保留 6 位、另给泰铢）。纯函数。"""
    out, total = [], {k: 0 for k in contracts.TOKENS} | {"entries": 0, "usd": Decimal(0)}
    for row in rows:
        usd = Decimal(str(row.get("usd") or 0))
        item = {g: (str(row.get(g)) if row.get(g) is not None else "") for g in groups}
        item.update({k: int(row.get(k) or 0) for k in contracts.TOKENS})
        item["entries"] = int(row.get("entries") or 0)
        item["usd"] = str(usd.quantize(USD_PLACES))
        item["thb"] = str((usd * THB_PER_USD).quantize(Decimal("0.01")))
        out.append(item)
        for key in contracts.TOKENS + ("entries",):
            total[key] += item[key]
        total["usd"] += usd
    total["thb"] = str((total["usd"] * THB_PER_USD).quantize(Decimal("0.01")))
    total["usd"] = str(total["usd"].quantize(USD_PLACES))
    return out, total


def summary(days: int = 30, by: str = "day,purpose", since: str | None = None, until: str | None = None) -> dict:
    """按组汇总本站的账（默认最近 30 天、按日与用途）。只给 System Manager（bench execute 以 Administrator 跑）。"""
    frappe.only_for("System Manager")
    today = frappe.utils.getdate(frappe.utils.nowdate())
    since = frappe.utils.getdate(since) if since else frappe.utils.add_days(today, -max(int(days or 30), 1) + 1)
    until = frappe.utils.getdate(until) if until else today
    groups = groups_of(by)
    sums = ", ".join(f"sum(`{k}`) as `{k}`" for k in contracts.TOKENS)
    select = ", ".join(f"`{g}`" for g in groups)
    query = (f"select {select + ', ' if select else ''}{sums}, count(*) as entries, sum(`usd`) as usd "
             f"from `tab{DOCTYPE}` where `day` between %(since)s and %(until)s"
             + (f" group by {select} order by {select}" if select else "") + f" limit {MAX_GROUPS}")
    rows = frappe.db.sql(query, {"since": since, "until": until}, as_dict=True)
    out, total = shape(rows, groups)
    return {"site": frappe.local.site, "tenant": str(frappe.conf.get("pearnly_tenant") or frappe.local.site),
            "product": str(frappe.conf.get("pearnly_product") or ""), "since": str(since), "until": str(until),
            "by": groups, "rows": out, "total": total}
