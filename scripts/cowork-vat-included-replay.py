"""简式税票含税价拆税的离线回放（2026-10-15）：只读、幂等，不连站点、不调模型、不写任何文件。

规则：简式税票（ABB，ม.86/6）票面价依法含 VAT；识别出的税额是 0 或没写、税前额就是总额时，按 7/107 拆出税额。
回放用的是生产同一份代码：识别存档 `ocr.results.prepare_history_batch` → `pearnly_cowork.ocr.pages.split_included_vat`
→ 共用规则 `pearnly_core.tax_documents.rules.included_vat`。用来在已存的识别结果（如测试机金标语料）上确认这条规则
只改「税额为 0 的简式税票」，并看清每张改前改后的数。

输入：一个目录，递归读其中的 *.json 与 *.jsonl（UTF-8，可带 BOM；.jsonl 每行一个 JSON；.json 整份读不了时也按行试）。
任何层级里出现的一张票的识别字段都认：
- 字段字典本身：同时有 `document_type` 与 `total_amount` 两个键（`ThaiInvoice` 导出、`PipelineResult.pages[].invoice`、
  识别任务单 `result_json`），同页的其他票在 `additional_invoices` 里一并认；
- 包着字段的对象 `{"fields": {...}}`：识别记录 `Cowork OCR History.pages` 的每一页、`scripts/eval-invoices.py` 的
  `results.jsonl` 里 `invoices[].fields`。
`pages`、`result_json`、`fields` 的值若是 JSON 字符串（数据库导出常见）先解开再认。`ai_raw`、`raw_pages` 是同一批数据的
副本，跳过不重复计。用到的字段：document_type、total_amount、subtotal、vat、wht_amount、currency、is_not_invoice，
以及看有没有写明免税 / 零税率的 notes、items[].name、source_refs；缺的按空处理。`results.jsonl` 只存了金额与票种、
没有备注和品名，免税字样核对不到：这类票的「票面文字」一栏是 no，请对照原件或金标确认。

用法：
  python3 scripts/cowork-vat-included-replay.py <目录> [--all] [--json]
    默认  逐张列出会改的票（TSV：文件、位置、编号、方向、票种、合计、税前额 改前→改后、税额 改前→改后、票面文字），末尾汇总；
    --all 另列不改的简式税票与原因（vat_present 已有税额 / subtotal_differs 税前额与合计不同 / no_vat_marked 写明免税或
          零税率 / foreign_currency 外币 / withholding 带预扣税 / total_missing 没有合计 / vat_unreadable 税额读不成数）；
    --json 每张一行 JSON（字段同上），便于再处理。
退出码：0 跑完（不论改了几张）；2 目录不存在或一份 JSON 也没读到。读不了的文件列在汇总里，不中断。
"""
import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_cowork"))
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.tax_documents import rules  # noqa: E402
from pearnly_cowork.ocr import pages  # noqa: E402

COPIES = frozenset({"ai_raw", "raw_pages"})
ENCODED = frozenset({"pages", "result_json", "fields"})
IDS = ("编号", "history", "name", "batch", "file")
COLUMNS = ("file", "where", "id", "direction", "document_type", "total", "subtotal", "vat", "texts", "reason")


def is_fields(node) -> bool:
    return isinstance(node, dict) and "document_type" in node and "total_amount" in node


def decoded(key, value):
    if key in ENCODED and isinstance(value, str) and value.strip()[:1] in ("[", "{"):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def invoices(node, where, ids):
    """(识别字段, 位置, 上层编号) —— 按出现顺序。"""
    if isinstance(node, list):
        for index, item in enumerate(node):
            yield from invoices(item, f"{where}[{index}]", ids)
        return
    if not isinstance(node, dict):
        return
    ids = {**ids, **{key: node[key] for key in IDS if key not in ids and isinstance(node.get(key), (str, int))}}
    if is_fields(node):
        yield node, where, ids
        for index, extra in enumerate(node.get("additional_invoices") or []):
            yield from invoices(extra, f"{where}.additional_invoices[{index}]", ids)
        return
    for key, value in node.items():
        if key in COPIES:
            continue
        value = decoded(key, value)
        yield from invoices(value, f"{where}.{key}", ids)


def documents(path):
    """一份文件里的 (行号或 None, JSON)；读不了返回空表。"""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        try:
            return [(None, json.loads(text))]
        except ValueError:
            pass
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:
                found.append((number, json.loads(line)))
            except ValueError:
                continue
    return found


def money(value):
    return "" if value is None else str(value)


def label(fields, ids):
    parts = [f"{key}={ids[key]}" for key in IDS if key in ids]
    if str(fields.get("invoice_number") or "").strip():
        parts.append(f"invoice={fields['invoice_number']}")
    return " ".join(parts)


def replay(directory, *, include_all=False):
    rows, unreadable = [], []
    counts = collections.Counter()
    files = sorted(path for path in Path(directory).rglob("*") if path.is_file() and path.suffix.lower() in (".json", ".jsonl"))
    for path in files:
        try:
            docs = documents(path)
        except (OSError, UnicodeDecodeError) as error:
            unreadable.append(f"{path}: {type(error).__name__}")
            continue
        if not docs:
            unreadable.append(f"{path}: no JSON")
            continue
        counts["files"] += 1
        for line, document in docs:
            start = f"line {line}:$" if line else "$"
            for fields, where, ids in invoices(document, start, {}):
                counts["invoices"] += 1
                result = pages.included_vat(fields)
                if result is None:
                    counts["not_invoice"] += 1
                    continue
                if result.reason == rules.NOT_INCLUDED_TYPE:
                    continue
                counts["abbreviated"] += 1
                counts[result.reason] += 1
                texts = bool(pages.printed_texts(fields))
                if result.applies and not texts:
                    counts["split_without_texts"] += 1
                if not (result.applies or include_all):
                    continue
                before_sub, before_vat = money(fields.get("subtotal")), money(fields.get("vat"))
                rows.append({
                    "file": str(path.relative_to(directory)), "where": where, "id": label(fields, ids),
                    "direction": money(fields.get("direction")), "document_type": money(fields.get("document_type")),
                    "total": money(fields.get("total_amount")),
                    "subtotal": f"{before_sub}→{result.subtotal:.2f}" if result.applies else before_sub,
                    "vat": f"{before_vat}→{result.vat:.2f}" if result.applies else before_vat,
                    "texts": "yes" if texts else "no", "reason": result.reason})
    return rows, counts, unreadable


def main(argv=None):
    parser = argparse.ArgumentParser(description="简式税票含税价拆税的离线回放（只读）。输入格式见文件头。")
    parser.add_argument("directory", help="装着识别结果 JSON / JSONL 的目录（递归读）")
    parser.add_argument("--all", action="store_true", help="另列不改的简式税票与原因")
    parser.add_argument("--json", action="store_true", help="每张一行 JSON")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    directory = Path(args.directory)
    if not directory.is_dir():
        print(f"not a directory: {directory}", file=sys.stderr)
        return 2
    rows, counts, unreadable = replay(directory, include_all=args.all)
    if args.json:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
    else:
        print("\t".join(COLUMNS))
        for row in rows:
            print("\t".join(row[column] for column in COLUMNS))
    summary = {"files_read": counts["files"], "unreadable_files": len(unreadable), "invoices": counts["invoices"],
               "not_invoice": counts["not_invoice"], "abbreviated": counts["abbreviated"],
               "would_change": counts[rules.SPLIT], "would_change_without_document_text": counts["split_without_texts"],
               "unchanged_abbreviated": {reason: counts[reason] for reason in (
                   rules.VAT_PRESENT, rules.SUBTOTAL_DIFFERS, rules.NO_VAT_MARKED, rules.FOREIGN_CURRENCY,
                   rules.WITHHOLDING, rules.TOTAL_MISSING, rules.VAT_UNREADABLE) if counts[reason]}}
    print(("" if args.json else "\n") + json.dumps({"summary": summary, "unreadable": unreadable}, ensure_ascii=False),
          file=sys.stdout if not args.json else sys.stderr)
    return 0 if counts["files"] else 2


if __name__ == "__main__":
    sys.exit(main())
