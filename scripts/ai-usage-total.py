#!/usr/bin/env python3
"""把各站点的 Anthropic 账本汇总（`pearnly_core.ai_usage.report.summary`）合成一张：按租户 × 产品 × 分组列相加。

    bench --site all execute pearnly_core.ai_usage.report.summary --kwargs '{"days": 30, "by": "purpose"}' \
        | python3 scripts/ai-usage-total.py

只读标准输入，不连站点、不连网。每个站点的结果是一行 JSON（`bench execute` 的输出），其余行（bench 的提示）忽略。
Console 总额减去这里的合计，就是没经过站点的花费（评测、回放、测试脚本）。
"""
import json
import sys
from decimal import Decimal

TOKENS = ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")
THB_PER_USD = Decimal(35)


def sites(lines):
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and "rows" in item and "total" in item:
            yield item


def merge(reports) -> dict:
    """几个站点的报表 → 合并的行（键 = 租户、产品、分组列）与总计。纯函数，测试直接调。"""
    rows, total, seen = {}, {k: 0 for k in TOKENS} | {"entries": 0, "usd": Decimal(0)}, []
    for report in reports:
        seen.append(report.get("site") or "")
        groups = list(report.get("by") or [])
        for row in report["rows"]:
            key = (report.get("tenant") or "", report.get("product") or "") + tuple(str(row.get(g, "")) for g in groups)
            merged = rows.setdefault(key, {"tenant": key[0], "product": key[1], **{g: row.get(g, "") for g in groups},
                                           **{k: 0 for k in TOKENS}, "entries": 0, "usd": Decimal(0)})
            for k in TOKENS + ("entries",):
                merged[k] += int(row.get(k) or 0)
                total[k] += int(row.get(k) or 0)
            merged["usd"] += Decimal(str(row.get("usd") or 0))
            total["usd"] += Decimal(str(row.get("usd") or 0))
    out = []
    for key in sorted(rows):
        row = rows[key]
        row["thb"] = str((row["usd"] * THB_PER_USD).quantize(Decimal("0.01")))
        row["usd"] = str(row["usd"])
        out.append(row)
    total["thb"] = str((total["usd"] * THB_PER_USD).quantize(Decimal("0.01")))
    total["usd"] = str(total["usd"])
    return {"sites": seen, "rows": out, "total": total}


def main() -> int:
    result = merge(sites(sys.stdin))
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["sites"] else 1


if __name__ == "__main__":
    sys.exit(main())
