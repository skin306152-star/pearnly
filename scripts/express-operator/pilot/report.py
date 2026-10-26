"""把各方式的结果（results.jsonl）汇成报告用的中文表格（Markdown）。

    python -m pilot.report runs/haiku_fixed-1027/results.jsonl runs/opus_fixed-1027/results.jsonl ... > tables.md
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Dict, List

from .pricing import USD_TO_THB

MODE_NAMES = {"haiku_fixed": "① Haiku 5.5 + 固定流程 + Opus 兜底", "opus_fixed": "② Opus 5.5 + 固定流程",
              "opus_vision": "③ Opus 5.5 全程看图", "direct": "④ 直接写入（现有）"}
VOLUMES = (("中位数公司（约 170 张 / 月）", 170), ("平均公司（约 800 张 / 月）", 800))


def load(paths: List[str]) -> List[Dict]:
    out = []
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _pct(n: int, total: int) -> str:
    return f"{n}/{total}（{100 * n / total:.0f}%）" if total else "-"


def _usd(r: Dict, key: str) -> Decimal:
    return Decimal(r.get(key) or "0")


def summary(rows: List[Dict]) -> List[str]:
    by_mode: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        by_mode[r["mode"]].append(r)
    out = ["| 方式 | 张数 | 读回和草稿单全对 | 和标准答案全对 | 一次做对 | AI 接手（次/张） | 要人插手 | Express 崩 | 中位用时 |"
           " 识别 / 张 | 代录 / 张 | 合计 / 张 |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    per_doc = {}
    for mode in MODE_NAMES:
        rs = by_mode.get(mode)
        if not rs:
            continue
        n = len(rs)
        ocr = sum((_usd(r, "ocr_usd") for r in rs), Decimal(0)) / n
        op = sum((_usd(r, "usd") for r in rs), Decimal(0)) / n
        per_doc[mode] = ocr + op
        judged = [r for r in rs if "answer_ok" in r]
        out.append(f"| {MODE_NAMES[mode]} | {n} | {_pct(sum(r['readback_ok'] for r in rs), n)} | "
                   f"{_pct(sum(r['answer_ok'] for r in judged), len(judged))} | {_pct(sum(r['first_try'] for r in rs), n)} | "
                   f"{sum(len(r['fallbacks']) for r in rs) / n:.2f} | {sum(r['human'] for r in rs)} | "
                   f"{sum(r['crashed'] for r in rs)} | {statistics.median(r['seconds'] for r in rs):.0f} 秒 | "
                   f"฿{ocr * USD_TO_THB:.2f} | ฿{op * USD_TO_THB:.2f} | ฿{(ocr + op) * USD_TO_THB:.2f} |")
    out += ["", "每月成本推算（识别 + 代录合计，按试点每张平均 × 张数，1 美元 ≈ 33 泰铢）：", "",
            "| 方式 | " + " | ".join(name for name, _ in VOLUMES) + " |", "|---|" + "---|" * len(VOLUMES)]
    for mode, usd in per_doc.items():
        out.append(f"| {MODE_NAMES[mode]} | " + " | ".join(f"฿{usd * v * USD_TO_THB:,.0f}" for _, v in VOLUMES) + " |")
    out += ["", "每月占用电脑时间推算（按中位用时，一台电脑顺序录）：", "",
            "| 方式 | " + " | ".join(name for name, _ in VOLUMES) + " |", "|---|" + "---|" * len(VOLUMES)]
    for mode in per_doc:
        secs = statistics.median(r["seconds"] for r in by_mode[mode])
        out.append(f"| {MODE_NAMES[mode]} | " + " | ".join(f"{secs * v / 3600:.1f} 小时" for _, v in VOLUMES) + " |")
    return out


def layers(rows: List[Dict]) -> List[str]:
    """四层各管了多少：键盘 vs 坐标点击；不截图读状态（Win32 / UIA）；读 DBF；截图给 AI。"""
    out = ["| 方式 | 张数 | 第 1 层 键盘动作 / 坐标点击 | 第 2 层 Win32 / UIA 判断次数 | 格子读不到 | 第 3 层 读 DBF |"
           " 第 4 层 截图核对次数 | 给 AI 的截图（张 / 单） |", "|---|---|---|---|---|---|---|---|"]
    by_mode: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        if r["mode"] != "direct":
            by_mode[r["mode"]].append(r)
    for mode, rs in by_mode.items():
        t = Counter()
        for r in rs:
            t.update(r.get("layers", {}))
        shots = sum(r.get("shots_ai", 0) for r in rs)
        out.append(f"| {MODE_NAMES.get(mode, mode)} | {len(rs)} | {t['keys']} / {t['clicks']} | {t['win32']} / {t['uia']} | "
                   f"{t['unreadable']} | {t['dbf']} | {t['vision']} | {shots}（{shots / len(rs):.1f}） |")
    return out


def tiers(rows: List[Dict], spec: List[Dict]) -> List[str]:
    """主结论表：每档客户每月多少张、代录一张多少钱、一家每月多少钱，加整个事务所合计。
    ``spec``：[{"tier": "小", "clients": 家数, "docs": 月均张数, "example": "…"}]；单价用主推方式（①）的合计平均。"""
    main = [r for r in rows if r["mode"] == "haiku_fixed"]
    if not main:
        return ["（主推方式还没有实测数据）"]
    per = sum((_usd(r, "usd") + _usd(r, "ocr_usd") for r in main), Decimal(0)) / len(main) * USD_TO_THB
    out = ["| 客户档 | 代表 | 家数 | 每家每月单据 | 代录一张 | 一家每月 |", "|---|---|---|---|---|---|"]
    firm = Decimal(0)
    for t in spec:
        month = per * t["docs"]
        firm += month * t["clients"]
        out.append(f"| {t['tier']} | {t.get('example', '')} | {t['clients']} | 约 {t['docs']} 张 | 约 ฿{per:.2f} | 约 ฿{month:,.0f} |")
    out.append(f"| 整个事务所 | — | {sum(t['clients'] for t in spec)} | 约 {sum(t['docs'] * t['clients'] for t in spec):,} 张 | "
               f"约 ฿{per:.2f} | 约 ฿{firm:,.0f} |")
    return out


def by_type(rows: List[Dict]) -> List[str]:
    out = ["| 方式 | 单种 | 张数 | 读回全对 | 中位用时 | 每张平均成本 |", "|---|---|---|---|---|---|"]
    groups: Dict = defaultdict(list)
    for r in rows:
        groups[(r["mode"], r["doc_type"])].append(r)
    for (mode, kind), rs in sorted(groups.items(), key=lambda kv: (list(MODE_NAMES).index(kv[0][0]), kv[0][1])):
        usd = sum((Decimal(r["usd"]) for r in rs), Decimal(0)) / len(rs)
        out.append(f"| {MODE_NAMES[mode]} | {kind} | {len(rs)} | {_pct(sum(r['readback_ok'] for r in rs), len(rs))} | "
                   f"{statistics.median(r['seconds'] for r in rs):.0f} 秒 | ${usd:.4f} |")
    return out


def failures(rows: List[Dict]) -> List[str]:
    where, fields, stuck = Counter(), Counter(), Counter()
    for r in rows:
        for f in r.get("fallbacks", []):
            where[(r["mode"], f["where"].split(".")[0], f["problem"][:40], f.get("resolved_by") or "没解决")] += 1
        for d in r.get("diffs", []):
            fields[(r["mode"], d["field"].split(".")[-1])] += 1
        if r.get("stuck_at"):
            stuck[(r["mode"], r["outcome"], r["stuck_at"][:60])] += 1
    out = ["AI 接手的地方：", "", "| 方式 | 步骤 | 情况 | 由谁解决 | 次数 |", "|---|---|---|---|---|"]
    out += [f"| {MODE_NAMES.get(m, m)} | {w} | {p} | {by} | {n} |" for (m, w, p, by), n in where.most_common()]
    out += ["", "读回对不上的字段：", "", "| 方式 | 字段 | 次数 |", "|---|---|---|"]
    out += [f"| {MODE_NAMES.get(m, m)} | {f} | {n} |" for (m, f), n in fields.most_common()]
    out += ["", "停下来要人处理的：", "", "| 方式 | 结果 | 卡在 | 次数 |", "|---|---|---|---|"]
    out += [f"| {MODE_NAMES.get(m, m)} | {o} | {s} | {n} |" for (m, o, s), n in stuck.most_common()]
    return out


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    spec = []
    if "--tiers" in args:
        i = args.index("--tiers")
        spec = json.loads(Path(args[i + 1]).read_text(encoding="utf-8"))
        del args[i:i + 2]
    rows = load(args)
    print("\n".join((["## 一家客户每月多少钱", ""] + tiers(rows, spec) + [""] if spec else [])
                    + ["## 总表", ""] + summary(rows) + ["", "## 四层各管了多少", ""] + layers(rows)
                    + ["", "## 按单种", ""] + by_type(rows) + ["", "## 失败与接手", ""] + failures(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
