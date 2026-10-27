#!/usr/bin/env python3
"""回放基准报告（2026-10-27，票面自洽表）：读 ``gate.py --record`` 存下的每份 Haiku / Opus 读法，按几种升级策略离线模拟，
不调模型。每组语料报：升级率、每页成本（泰铢，35 泰铢 / 美元，与 10-17 闸门同口径）、最终结果过不过自洽表、各检查触发了
多少份、触发的那些份 Opus 重读后还剩不剩。金标准确率另用 ``gate.py --replay`` + 原打分工具算（这里只看自洽与成本）。

用法（gate 容器里，同 run-gate.sh 的挂载）：python /src/scripts/claude-ocr/replay_report.py /runs/replay /runs [策略 …]
策略：legacy、current，或检查代码逗号分隔。默认 legacy current 与每条检查单独开。
"""
import collections
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, "/src/apps/pearnly_core")
from pearnly_core.claude_ocr import consistency, engine, rules  # noqa: E402

THB = 35


def why(read, pages, policy):
    if policy == "legacy":
        return rules._voucher_basics(read, pages)
    if policy == "current":
        return rules.voucher_problems(read, pages)
    return rules.voucher_problems(read, pages, policy=tuple(policy.split(",")))


def simulate(rec, policy):
    haiku, opus = copy.deepcopy(rec["haiku"]), copy.deepcopy(rec["opus"])
    rules.fix_own_tax(haiku, rec.get("own_tax"))
    reasons = why(haiku, rec["pages"], policy)
    usd = rec["haiku_stage"]["usd"] + (rec["opus_stage"]["usd"] if reasons else 0)
    read = haiku
    if reasons and opus is not None:
        read = opus if policy == "legacy" else engine.combine(haiku, opus, reasons)
    if policy != "legacy":
        engine.finish(read, rec["pages"], escalated=bool(reasons))
    left = sorted({code for code, _t in consistency.findings(read) if code in consistency.CHECKS})
    return {"escalated": bool(reasons), "usd": usd, "left": left}


def main():
    replay, runs = Path(sys.argv[1]), Path(sys.argv[2])
    policies = sys.argv[3:] or ["legacy", "current", *consistency.CHECKS]
    recs = {}
    for path in replay.glob("*.json"):
        key, _n = path.stem.rsplit("#", 1)
        recs.setdefault(key, []).append(json.loads(path.read_text()))
    sets = {}
    for name in ("test", "ex", "sm", "cases"):
        out = runs / f"{name}-rec-cowork.jsonl"
        if out.exists():
            rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
            sets[name] = {row["id"]: row.get("pages") or 1 for row in rows if row["id"] in recs}
    for name, ids in sets.items():
        pages = sum(ids.values())
        calls = [rec for key in ids for rec in recs[key]]
        print(f"\n== {name}: {len(ids)} 份 {pages} 页，发票类读法 {len(calls)} 次")
        fired = collections.Counter()
        fixed = collections.Counter()
        for rec in calls:
            first = {code for code, _t in consistency.findings(rec["haiku"])}
            after = {code for code, _t in consistency.findings(rec["opus"])} if rec["opus"] else first
            for code in first:
                fired[code] += 1
                fixed[code] += code not in after
        print("   检查（Haiku 首读触发份数 → Opus 重读后消失的份数）：",
              {code: f"{fired[code]} → {fixed[code]}" for code in consistency.CHECKS})
        print(f"   {'策略':<44} 升级     每页 ฿   最终仍不自洽")
        for policy in policies:
            results = [simulate(rec, policy) for rec in calls]
            up = sum(r["escalated"] for r in results)
            usd = sum(r["usd"] for r in results)
            bad = sum(bool(set(r["left"]) & set(consistency.POLICY)) for r in results)
            print(f"   {policy:<44} {up:>3} ({up / max(len(results), 1):5.1%})  {usd * THB / max(pages, 1):6.3f}   {bad}")


if __name__ == "__main__":
    main()
