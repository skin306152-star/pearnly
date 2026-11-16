#!/usr/bin/env python3
"""Select 字段存空会被补成第一项的闸门（11-16 验收：未收款的销售单「收款方式」存成了 cash）。

Frappe 保存时，非必填、没有默认值、选项第一行不是空行的 Select 字段，值为空就补成第一项——代码明明传了空，库里却是
一个有业务含义的值。本仓自有 DocType 里这种字段一律要么选项首行留空（原生写法：`"options": "\\nA\\nB"`），要么在下面
`ALLOW` 登记「为什么不会存空」（核过才登记），否则失败。只读、不连网络。

    python3 scripts/check-select-blank.py
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (DocType, 字段) → 不会存空的依据（11-16 逐个核：写入点都显式给值，或 validate 自己处理空值）。
# 新增字段别往这里加，优先在选项首行留空。框架规则（Frappe 16.33）：model/create_new.py 取 options 第一项作模板值，
# document._set_defaults 在新建（子表为新行）时只补值为 None 的字段，传 "" 原样保留。
ALLOW = {
    ("Pearnly AI Usage", "purpose"): "记账入口按调用方显式给（pearnly_core.ai_usage）",
    ("Pearnly Assistant Write Plan", "mode"): "建计划时按代录 / 直写显式给（assistant.write_plan.make）",
    ("Cowork ERP Refresh", "status"): "建记录时显式给 requested",
    ("Cowork Express Choice Change", "source"): "记录选择改动时显式给来源",
    ("Cowork Folder Intake File", "route"): "建文件行时显式给（已抽查主写入点）",
    ("Cowork Folder Intake Run", "trigger"): "建批次时显式给 schedule / manual",
    ("Cowork Folder Intake Run", "brief_state"): "建批次时显式给",
    ("Cowork OCR Batch", "phase"): "建批次时显式给",
    ("Cowork OCR Batch", "billing_state"): "建批次时显式给",
    ("Cowork OCR Batch", "reference_kind"): "建批次时显式给",
    ("Cowork OCR Batch", "billing_kind"): "建批次时显式给",
    ("Cowork OCR History", "quality_status"): "识别落库时显式给",
    ("Cowork Source File", "kind"): "建原件记录时按文件类型显式给（已抽查主写入点）",
    ("ERP Collection", "source"): "建收款时按入口显式给 line / web / cc（collection.service）",
    ("ERP Collection Payment", "role"): "collection.contracts.method_of 只回 transfer / discount，认不出就报错",
    ("ERP Customer", "branch_type"): "erp_customer.py validate 在补默认之后按税号清回空",
    ("ERP Serial Movement", "action"): "过账时按入 / 出显式给（inventory.serials）",
    ("ERP LINE Intake", "item_kind"): "会补成 goods 但新票不读：识别后 line_items.prepare 必写按行的选择（intake.py:236/566），item_kind 只在改版前的旧票上读（intake.py:286）",
    ("Pearnly Express Operator Task Document", "doc_group"): "会补成 purchase 但只用于展示（feed.py:211/253），前端 operator-shared.ts groupOf 对空值的回退本来就是 purchase",
    ("ERP Purchase Payment", "method"): "新路径总给值（service.py:404-408 method or cash、entry.py 校验）；只有直接调旧接口的 v1 单不传 method 时补成 cash，与默认现金相符",
}


def blank_risks() -> list[tuple[str, str, str]]:
    found = []
    for path in sorted(glob.glob(str(ROOT / "apps/pearnly_*/pearnly_*/pearnly_*/doctype/*/*.json"))):
        try:
            meta = json.loads(Path(path).read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(meta, dict) or meta.get("doctype") != "DocType":
            continue
        for field in meta.get("fields") or ():
            options = str(field.get("options") or "")
            if (field.get("fieldtype") == "Select" and not field.get("reqd") and field.get("default") in (None, "")
                    and not options.startswith("\n")):
                found.append((meta["name"], field["fieldname"], options.split("\n")[0]))
    return found


def main() -> int:
    risks = blank_risks()
    new = [item for item in risks if (item[0], item[1]) not in ALLOW]
    stale = sorted(set(ALLOW) - {(doctype, field) for doctype, field, _first in risks})
    for doctype, field, first in new:
        print(f"Select 存空会被补成第一项「{first}」：{doctype}.{field}——选项首行留空，或在 ALLOW 登记不会存空的依据")
    for doctype, field in stale:
        print(f"ALLOW 里的 {doctype}.{field} 已经不是这种字段了（改了选项或加了默认），从 ALLOW 删掉")
    if new or stale:
        return 1
    print(f"Select 空值闸门：通过（{len(risks)} 个登记过的字段）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
