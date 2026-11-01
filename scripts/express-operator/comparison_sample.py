# -*- coding: utf-8 -*-
"""对比报告样张（开发工具）：同一批单据，代录 / 现有直写 / 会计手录三列并排（共用层 ``express_operator.report``）。

    python3 comparison_sample.py --operator batch.json --direct a.jsonl [--direct b.jsonl] --drafts drafts.jsonl --out x.md

- 代录：代录批次文件（每张用时、读回、要人原因、作废、空单头）；
- 直写：直写基准的结果（每张用时、读回）；同一张有几份取最后一份；
- 会计手录：作为标准答案（不自评准确率）；用时等试用公司给，没有就空着（不估）；
- 费用：识别费（草稿里记的实际花费，美元按 33 换泰铢）+ AI 接手费（代录这批 0 次调用）。手录的人工费等试用公司给时薪。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THB = Decimal("33")
LABELS = {"docs": "张数", "docs_by_type": "张数（单种分列）", "seconds_total": "总用时（秒）", "seconds_per_doc": "每张平均（秒）",
          "accurate": "读回一致张数", "accuracy": "准确率（%）", "human": "要人次数", "human_by_reason": "要人原因分列",
          "voided": "作废单数", "placeholders": "留下的空单头", "cost_thb": "费用（泰铢）", "cost_per_doc_thb": "每张费用（泰铢）",
          "machine_hours": "电脑占用（小时）"}
METHOD_LABELS = {"operator": "代录", "direct": "现有直写", "manual": "会计手录"}


def _report():
    spec = importlib.util.spec_from_file_location("op_report", ROOT / "apps/pearnly_core/pearnly_core/express_operator/report.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["op_report"] = module  # dataclass 要能在模块表里找到自己
    spec.loader.exec_module(module)
    return module


def _show(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, dict):
        return "、".join(f"{k} {v}" for k, v in value.items()) or "—"
    return str(value)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--operator", required=True)
    ap.add_argument("--direct", action="append", default=[])
    ap.add_argument("--drafts", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    R = _report()
    drafts = {d["doc_id"]: d for d in (json.loads(line) for line in Path(args.drafts).read_text(encoding="utf-8-sig").splitlines() if line.strip())}
    ocr = {k: (Decimal(str(v.get("ocr_usd") or 0)) * THB) for k, v in drafts.items()}
    batch = json.loads(Path(args.operator).read_text(encoding="utf-8"))
    op_docs = [R.DocResult(d["doc_id"], d["doc_type"], d["seconds"], bool(d.get("readback_ok")), d["status"] == "waiting",
                           d.get("reason") or "", len(d.get("voided") or []), len(d.get("placeholders") or []),
                           ocr.get(d["doc_id"], Decimal(0)))
               for d in batch["docs"] if d["doc_id"] in drafts]
    ids = [d.doc_id for d in op_docs]
    direct = {}
    for path in args.direct:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                direct[row["doc_id"]] = row
    dir_docs = [R.DocResult(i, direct[i]["doc_type"], direct[i]["seconds"], bool(direct[i]["readback_ok"]),
                            bool(direct[i].get("human")), "", 0, 0, ocr.get(i, Decimal(0))) for i in ids if i in direct]
    manual = [R.DocResult(i, drafts[i]["doc_type"]) for i in ids]
    hours = round(sum(d.seconds for d in op_docs) / 3600, 3)
    table = R.compare([R.Run("operator", op_docs, machine_hours=hours), R.Run("direct", dir_docs),
                       R.Run("manual", manual, baseline=True)])
    lines = ["| 指标 | " + " | ".join(METHOD_LABELS[m] for m in table["methods"]) + " |",
             "|---|" + "---|" * len(table["methods"])]
    for row in table["rows"]:
        lines.append(f"| {LABELS[row['metric']]} | " + " | ".join(_show(row["values"][m]) for m in table["methods"]) + " |")
    note = "" if table["same_docs"] else "\n> 三种方式的单据不完全相同（直写基准只覆盖其中一部分），这一版不能直接比，见说明。\n"
    Path(args.out).write_text("\n".join(lines) + "\n" + note, encoding="utf-8")
    print("\n".join(lines) + note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
