"""发票识别准确率评测：按真实入口识别语料样本，对照 Express 答案逐字段打分（2026-10-02，用户：「先测真实准确率」）。

两步，都在生产机一次性容器里跑，语料与结果不出生产机：
  run    在站点里按真实入口提交（``files.service.create`` 建原件 → ``ocr.execution.service.submit`` 交识别），等全部有结论，
         把每份的识别记录（逐张票的字段、需复核标记、状态、耗时）写进 ``results.jsonl``。会真实识别、按页计费。
  score  不连站点：读 ``results.jsonl`` 与样本清单、各公司 Express 答案（进项 / 销项税登记），配对后逐字段比，
         写 ``score.json``、``report.md``，并导出 GLM 对照子集 ``glm42-current.tsv``。

配对（单据与答案行之间没有对应关系）：在这家公司所有账套的进项、销项税登记里找候选行，单号（去空格、横杠、前导零，
字母前缀不同也算）、对方税号、金额（含税或未税，差 0.01 内）、日期四项至少两项吻合且候选唯一才算对上；进项还是销项
按本公司税号是买方还是卖方定。对上后比：单号、日期、对方税号、对方分店、对方名称（模糊）、未税额、税额、含税额。

口径（按类归纳，不逐家特判）：
- 单号只差字母前缀（原件 INV…、会计录 IV…）不算错，单列「前缀不同」；
- 原件上本来没印的字段（水电费单的税号等）识别为空不算错，单列「原件无此字段」；
- 海关税单（分类「进出口」）会计用报关数，单独统计，不混进普通发票；
- 原件本身打不开（空文件、带密码的 PDF）不计入分母，单列并记下系统给的提示。

用法：
  run:   docker run --rm --user 0 --memory 3g --env-file <web 的环境> --network pearnly-production_runtime
           --volumes-from pearnly-production-cowork-web-1 -v /root/pearnly-corpus/06-录入推送:/corpus:ro
           -v /tmp/pearnly-eval:/out -v <本文件>:/eval.py:ro -w /home/frappe/frappe-bench/sites --entrypoint bash <镜像>
           -c 'su frappe -s /bin/bash -c "../env/bin/python /eval.py run --site testcowork.pearnly.com
               --user pearnlyai@gmail.com --sample /corpus/样本-发票300.tsv --corpus /corpus --out /out --tag eval1"'
  score: python3 eval-invoices.py score --sample .../样本-发票300.tsv --corpus .../06-录入推送 --out /tmp/pearnly-eval
           [--glm .../样本-GLM子集42.tsv]
"""
import argparse
import collections
import csv
import difflib
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

DIRECTIONS = {"采购": "purchase", "销售": "sales"}
FIELDS = ("doc_no", "date", "party_tax", "party_branch", "party_name", "net", "vat", "gross")
CUSTOMS = ("进出口",)
UTILITY = ("电费", "水费")


def read_sample(path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            row = {key.strip(): (value or "").strip() for key, value in row.items()}
            row["副本"] = row["副本"].replace("\\", "/")
            row["答案目录"] = row["答案目录"].replace("\\", "/")
            rows.append(row)
    return rows


# ── run：真实入口识别 ───────────────────────────────────────────────────────────────


def run(args):
    import frappe

    frappe.init(args.site, sites_path=".")
    frappe.connect()
    frappe.set_user(args.user)
    from pearnly_cowork.access import scope
    from pearnly_cowork.files import service as files
    from pearnly_cowork.ocr.execution import service as ocr

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sample = read_sample(args.sample)
    if args.only:
        wanted = set(args.only.split(","))
        sample = [row for row in sample if row["编号"] in wanted]
    workspace = scope.default_workspace()
    pending, done = {}, []
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for row in sample:
        path = Path(args.corpus, row["副本"])
        record = {"编号": row["编号"], "file": path.name, "submitted": time.time()}
        try:
            content = path.read_bytes()
            key = f"{args.tag}-{row['编号']}"
            source = files.create(workspace, path.name, content, key, None)
            source_name = source["name"] if isinstance(source, dict) else source.name
            batch = ocr.submit(source_name, key, direction=DIRECTIONS.get(row.get("方向")))
            frappe.db.commit()
            record.update(source=source_name, batch=batch["name"])
            pending[batch["name"]] = record
        except Exception as error:
            frappe.db.rollback()
            record.update(state="rejected", error=f"{type(error).__name__}: {frappe.utils.strip_html(str(error))[:300]}")
            done.append(record)
    print(f"submitted {len(pending)}, rejected {len(done)}", flush=True)
    deadline = time.time() + args.timeout
    while pending and time.time() < deadline:
        time.sleep(5)
        frappe.db.rollback()
        for name in list(pending):
            view = ocr.detail(name)
            if view["state"] not in ("saved", "not_invoice", "failed", "uncertain", "cancelled"):
                continue
            record = pending.pop(name)
            record.update(state=view["state"], seconds=round(time.time() - record["submitted"], 1),
                          requires_review=view["requires_review"], last_code=view.get("last_code"),
                          invoices=_invoices(view["history_records"]))
            done.append(record)
        print(f"{time.strftime('%H:%M:%S')} waiting {len(pending)}", flush=True)
    for record in pending.values():
        record.update(state="timeout")
        done.append(record)
    Path(out, "run.json").write_text(json.dumps({"started": started, "finished": time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "site": args.site, "tag": args.tag, "samples": len(sample)}))
    with open(out / "results.jsonl", "w", encoding="utf-8") as handle:
        for record in done:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(collections.Counter(record["state"] for record in done)), flush=True)


def _invoices(names):
    """一份原件识别出的每张票：页内字段（逐页取第一张有字段的）加记录上的复核信息。"""
    import frappe

    invoices = []
    for name in names or []:
        doc = frappe.db.get_value("Cowork OCR History", name, ["pages", "quality_status", "review_reasons", "staged",
                                                               "posting_kind", "source_index"], as_dict=True)
        if not doc:
            continue
        pages = json.loads(doc.pages or "[]")
        fields = next((page.get("fields") for page in pages if page.get("fields")), {}) or {}
        invoices.append({"history": name, "quality_status": doc.quality_status, "staged": doc.staged,
                         "review_reasons": json.loads(doc.review_reasons or "[]"), "posting_kind": doc.posting_kind,
                         "pages": len(pages), "fields": {key: fields.get(key) for key in (
                             "invoice_number", "date", "date_raw", "seller_tax", "seller_branch", "seller_name",
                             "buyer_tax", "buyer_branch", "buyer_name", "subtotal", "vat", "total_amount",
                             "document_type")}})
    return invoices


# ── score：对照 Express 答案 ───────────────────────────────────────────────────────


def money(value):
    try:
        return Decimal(str(value).replace(",", "").strip()) if value not in (None, "") else None
    except InvalidOperation:
        return None


def digits(value):
    return re.sub(r"\D", "", str(value or "")).lstrip("0")


def alnum(value):
    return re.sub(r"[^0-9A-Z]", "", str(value or "").upper())


def doc_no_key(value):
    """单号比对用：去空格横杠，数字部分去前导零；字母前缀另记。"""
    text = alnum(value)
    prefix = re.match(r"^[A-Z]*", text).group(0)
    return prefix, text[len(prefix):].lstrip("0")


def branch(value):
    text = digits(value)
    return text or "0" if value not in (None, "") else None


def parse_day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def name_key(value):
    text = re.sub(r"(บริษัท|จำกัด|\(มหาชน\)|ห้างหุ้นส่วน|สำนักงานใหญ่|COMPANY|CO\.?|LTD\.?|LIMITED|,|\.)", "",
                  str(value or ""), flags=re.I)
    return re.sub(r"\s+", "", text).lower()


def load_answers(corpus, answer_dir):
    """这家公司所有账套的进项 / 销项税登记行。"""
    rows = []
    for register, direction in (("进项税登记.csv", "purchase"), ("销项税登记.csv", "sale")):
        for path in sorted(Path(corpus, answer_dir).glob(f"*/{register}")):
            with open(path, encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    net, vat, zero = money(row.get("未税额")), money(row.get("税额")), money(row.get("零税额"))
                    rows.append({"direction": direction, "ledger": path.parent.name, "doc_no": row.get("对方单号") or "",
                                 "voucher": row.get("凭证号") or "", "date": parse_day(row.get("单据日期")),
                                 "party_name": row.get("对方名称") or "", "party_tax": digits(row.get("税号")),
                                 "party_branch": branch(row.get("分店")), "net": (net or 0) + (zero or 0),
                                 "vat": vat or Decimal(0), "gross": (net or 0) + (zero or 0) + (vat or 0)})
    return rows


def recognized(invoice, own_tax, direction=None):
    """识别出的一张票换成「对方」口径：销售的对方是买方，采购的对方是卖方。样本给了方向就按它；没给按本公司税号是卖方
    还是买方定。"""
    fields = invoice["fields"]
    seller, buyer = digits(fields.get("seller_tax")), digits(fields.get("buyer_tax"))
    own = digits(own_tax)
    direction = direction or ("sale" if seller and seller == own else "purchase" if buyer and buyer == own else None)
    side = "buyer" if direction == "sale" else "seller"
    return {"direction": direction, "doc_no": fields.get("invoice_number") or "", "date": parse_day(fields.get("date")),
            "party_tax": digits(fields.get(f"{side}_tax")), "party_branch": branch(fields.get(f"{side}_branch")),
            "party_name": fields.get(f"{side}_name") or "", "net": money(fields.get("subtotal")),
            "vat": money(fields.get("vat")), "gross": money(fields.get("total_amount")),
            "own_as_party": bool(own) and digits(fields.get(f"{side}_tax")) == own,
            "date_raw": fields.get("date_raw")}


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= Decimal("0.01")


def match(item, answers):
    """四项（单号、对方税号、金额、日期）至少两项吻合且候选唯一。回 ``(行, 吻合项)`` 或 ``(None, 原因)``。"""
    pool = [row for row in answers if item["direction"] in (None, row["direction"])]
    scored = []
    for row in pool:
        hits = []
        if doc_no_key(item["doc_no"])[1] and doc_no_key(item["doc_no"])[1] == doc_no_key(row["doc_no"])[1]:
            hits.append("doc_no")
        if item["party_tax"] and item["party_tax"] == row["party_tax"]:
            hits.append("party_tax")
        if close(item["gross"], row["gross"]) or close(item["net"], row["net"]):
            hits.append("amount")
        if item["date"] and item["date"] == row["date"]:
            hits.append("date")
        if len(hits) >= 2:
            scored.append((len(hits), hits, row))
    if not scored:
        return None, "no_candidate"
    best = max(score for score, _hits, _row in scored)
    top = [(hits, row) for score, hits, row in scored if score == best]
    if len(top) > 1:
        return None, "ambiguous"
    return top[0][1], top[0][0]


def classify(field, got, want, item):
    """一处不对的类别（按类归纳，用来按类修）。"""
    if got in (None, "") or (got == Decimal(0) and field in ("net", "vat", "gross") and want):
        return "missing"
    if field == "date" and isinstance(got, date) and isinstance(want, date):
        if abs(got.year - want.year) == 543 and (got.month, got.day) == (want.month, want.day):
            return "buddhist_year"
        if (got.month, got.day) == (want.day, want.month):
            return "day_month_swapped"
        if got.year != want.year and (got.month, got.day) == (want.month, want.day):
            return "year"
        return "date_other"
    if field == "party_tax" and item.get("own_as_party"):
        return "own_tax_as_party"
    if field in ("net", "vat", "gross") and got is not None and want is not None:
        if abs(got - want) <= 1:
            return "rounding"
        if digits(got) and digits(want) and (digits(got) in digits(want) or digits(want) in digits(got)):
            return "digits_dropped_or_added"
        if field == "net" and close(got, want + (item.get("vat") or 0)):
            return "net_is_gross"
        return "amount_other"
    return f"{field}_other"


def compare(item, row, category):
    """逐字段：``{字段: 对 / 错 / 前缀不同 / 原件无此字段}``，错的带类别。"""
    result = {}
    got_prefix, got_no = doc_no_key(item["doc_no"])
    want_prefix, want_no = doc_no_key(row["doc_no"])
    if not want_no and not row["doc_no"]:
        result["doc_no"] = ("n/a", None)
    elif got_no == want_no and got_prefix == want_prefix:
        result["doc_no"] = ("ok", None)
    elif got_no and got_no == want_no:
        result["doc_no"] = ("prefix_only", None)
    else:
        result["doc_no"] = ("wrong", "missing" if not got_no else "doc_no_other")
    result["date"] = ("ok", None) if item["date"] == row["date"] else ("wrong", classify("date", item["date"], row["date"], item))
    for field in ("party_tax", "party_branch"):
        want, got = row[field], item[field]
        if not want:
            result[field] = ("n/a", None)
        elif got == want:
            result[field] = ("ok", None)
        elif not got and category in UTILITY:
            result[field] = ("not_printed", None)
        else:
            result[field] = ("wrong", classify(field, got, want, item))
    ratio = difflib.SequenceMatcher(None, name_key(item["party_name"]), name_key(row["party_name"])).ratio()
    result["party_name"] = ("ok", None) if ratio >= 0.8 else (
        "not_printed", None) if not item["party_name"] and category in UTILITY else ("wrong", "name_other")
    for field in ("net", "vat", "gross"):
        result[field] = ("ok", None) if close(item[field], row[field]) else ("wrong", classify(
            field, item[field], row[field], {**item, "vat": row["vat"]}))
    return result


def score(args):
    sample = read_sample(args.sample)
    by_id = {row["编号"]: row for row in sample}
    results = [json.loads(line) for line in open(Path(args.out, "results.jsonl"), encoding="utf-8")]
    glm = {row["编号"] for row in read_sample(args.glm)} if args.glm else set()
    answers_cache = {}
    per_invoice, broken, unmatched = [], [], []
    for record in results:
        row = by_id[record["编号"]]
        if record["state"] in ("rejected",) or (record["state"] != "saved" and not record.get("invoices")):
            broken.append({"编号": row["编号"], "方向": row.get("方向") or "", "公司": row["公司"], "形态": row["形态"], "state": record["state"],
                           "message": record.get("error") or record.get("last_code")})
            continue
        answers = answers_cache.setdefault(row["答案目录"], load_answers(args.corpus, row["答案目录"]))
        for invoice in record.get("invoices") or []:
            item = recognized(invoice, row["税号"], {"采购": "purchase", "销售": "sale"}.get(row.get("方向")))
            found, how = match(item, answers)
            base = {"编号": row["编号"], "方向": row.get("方向") or "", "公司": row["公司"], "分类": row["分类"], "形态": row["形态"],
                    "customs": row["分类"] in CUSTOMS, "flagged": bool(record.get("requires_review")) or bool(
                        invoice["review_reasons"]) or invoice["quality_status"] != "confirmed",
                    "seconds": record.get("seconds"), "fields": invoice["fields"], "history": invoice["history"]}
            if found is None:
                unmatched.append({**base, "reason": how})
                continue
            fields = compare(item, found, row["分类"])
            per_invoice.append({**base, "matched_on": how, "answer": {k: str(v) for k, v in found.items()},
                                "result": {k: v[0] for k, v in fields.items()},
                                "errors": {k: v[1] for k, v in fields.items() if v[0] == "wrong"}})
    run_info = json.loads(Path(args.out, "run.json").read_text()) if Path(args.out, "run.json").exists() else {}
    report = summarize(per_invoice, unmatched, broken, results)
    report["run"] = run_info
    Path(args.out, "score.json").write_text(json.dumps({"summary": report, "invoices": per_invoice,
        "unmatched": unmatched, "broken": broken}, ensure_ascii=False, default=str, indent=1), encoding="utf-8")
    Path(args.out, "report.md").write_text(render(report), encoding="utf-8")
    if glm:
        export_glm(args, results, glm)
    print(render(report))


def rates(items):
    total, wrong = collections.Counter(), collections.Counter()
    for item in items:
        for field, verdict in item["result"].items():
            if verdict in ("ok", "wrong"):
                total[field] += 1
                wrong[field] += verdict == "wrong"
    fields = {field: {"n": total[field], "wrong": wrong[field],
                      "error_rate": round(wrong[field] / total[field], 4) if total[field] else None} for field in FIELDS}
    all_ok = sum(1 for item in items if not item["errors"])
    cells = sum(total.values())
    return {"invoices": len(items), "all_correct": all_ok, "all_correct_rate": round(all_ok / len(items), 4) if items else None,
            "field_error_rate": round(sum(wrong.values()) / cells, 4) if cells else None, "fields": fields}


def summarize(per_invoice, unmatched, broken, results):
    regular = [item for item in per_invoice if not item["customs"]]
    customs = [item for item in per_invoice if item["customs"]]
    categories = collections.Counter(kind for item in regular for kind in item["errors"].values())
    by_field = collections.Counter(f"{field}:{kind}" for item in regular for field, kind in item["errors"].items())
    with_error = [item for item in regular if item["errors"]]
    flagged = [item for item in regular if item["flagged"]]
    missed = [item for item in with_error if not item["flagged"]]
    notes = collections.Counter(verdict for item in regular for verdict in item["result"].values()
                                if verdict in ("prefix_only", "not_printed"))
    seconds = sorted(record["seconds"] for record in results if record.get("seconds"))
    return {
        "samples": len(results), "broken": len(broken), "matched_invoices": len(per_invoice), "unmatched_invoices": len(unmatched),
        "unmatched_reasons": dict(collections.Counter(item["reason"] for item in unmatched)),
        "regular": rates(regular), "customs": rates(customs),
        "by_form": {form: rates([item for item in regular if item["形态"] == form])
                    for form in sorted({item["形态"] for item in regular})},
        "by_direction": {way: rates([item for item in regular if item["方向"] == way])
                         for way in sorted({item["方向"] for item in regular})},
        "by_direction_form": {f"{way}/{form}": rates([item for item in regular if (item["方向"], item["形态"]) == (way, form)])
                              for way, form in sorted({(item["方向"], item["形态"]) for item in regular})},
        "by_direction_category": {f"{way}/{cat}": rates([item for item in regular if (item["方向"], item["分类"]) == (way, cat)])
                                  for way, cat in sorted({(item["方向"], item["分类"]) for item in regular})},
        "by_company": {company: rates([item for item in regular if item["公司"] == company])
                       for company in sorted({item["公司"] for item in regular})},
        "by_category": {category: rates([item for item in regular if item["分类"] == category])
                        for category in sorted({item["分类"] for item in regular})},
        "error_kinds": dict(categories.most_common()), "error_kinds_by_field": dict(by_field.most_common()),
        "notes": dict(notes),
        "review": {"flag_rate": round(len(flagged) / len(regular), 4) if regular else None,
                   "invoices_with_error": len(with_error), "flagged_with_error": len(with_error) - len(missed),
                   "missed_rate": round(len(missed) / len(with_error), 4) if with_error else None},
        "seconds": {"p50": seconds[len(seconds) // 2] if seconds else None,
                    "p95": seconds[int(len(seconds) * 0.95) - 1] if seconds else None},
    }


def render(report):
    regular = report["regular"]
    lines = ["# 发票识别准确率", "",
             f"样本 {report['samples']} 份；打不开的原件 {report['broken']} 份（不计入）；配上答案 {report['matched_invoices']} 张、"
             f"未对上 {report['unmatched_invoices']} 张 {report['unmatched_reasons']}。", "",
             f"普通发票：{regular['invoices']} 张，整张全对 {regular['all_correct_rate']}，字段错误率 {regular['field_error_rate']}。",
             f"海关税单（报关数口径，单列）：{report['customs']['invoices']} 张，字段错误率 {report['customs']['field_error_rate']}。", "",
             "| 字段 | 张数 | 错 | 错误率 |", "| --- | --- | --- | --- |"]
    for field, value in regular["fields"].items():
        lines.append(f"| {field} | {value['n']} | {value['wrong']} | {value['error_rate']} |")
    lines += ["", "| 方向/形态 | 张数 | 整张全对 | 字段错误率 |", "| --- | --- | --- | --- |"]
    lines += [f"| {key} | {value['invoices']} | {value['all_correct_rate']} | {value['field_error_rate']} |"
              for key, value in report["by_direction_form"].items()]
    lines += ["", "| 方向/分类 | 张数 | 整张全对 | 字段错误率 |", "| --- | --- | --- | --- |"]
    lines += [f"| {key} | {value['invoices']} | {value['all_correct_rate']} | {value['field_error_rate']} |"
              for key, value in report["by_direction_category"].items()]
    lines += ["", "按形态：" + "；".join(f"{form} 全对 {value['all_correct_rate']} 字段错 {value['field_error_rate']}"
                                     for form, value in report["by_form"].items()),
              "", "错误类别：" + json.dumps(report["error_kinds"], ensure_ascii=False),
              "", "口径单列：" + json.dumps(report["notes"], ensure_ascii=False),
              "", f"需复核：标记率 {report['review']['flag_rate']}，有错的 {report['review']['invoices_with_error']} 张里"
                  f"漏标率 {report['review']['missed_rate']}。",
              "", f"耗时：p50 {report['seconds']['p50']} 秒，p95 {report['seconds']['p95']} 秒。",
              "", f"运行：{report.get('run', {}).get('started')} 至 {report.get('run', {}).get('finished')}（UTC）。"]
    return "\n".join(lines) + "\n"


def export_glm(args, results, glm):
    columns = ["编号", "页", "单号", "日期", "卖方税号", "卖方分店", "买方税号", "买方分店", "未税额", "税额", "合计", "是否标需复核", "状态"]
    with open(Path(args.out, "glm42-current.tsv"), "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(columns)
        for record in results:
            if record["编号"] not in glm:
                continue
            for invoice in record.get("invoices") or [{"fields": {}, "pages": 0, "review_reasons": []}]:
                fields = invoice["fields"]
                writer.writerow([record["编号"], invoice.get("pages"), fields.get("invoice_number"), fields.get("date"),
                                 fields.get("seller_tax"), fields.get("seller_branch"), fields.get("buyer_tax"),
                                 fields.get("buyer_branch"), fields.get("subtotal"), fields.get("vat"),
                                 fields.get("total_amount"),
                                 int(bool(record.get("requires_review") or invoice.get("review_reasons"))),
                                 record["state"]])


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--site", required=True)
    runner.add_argument("--user", required=True)
    runner.add_argument("--sample", required=True)
    runner.add_argument("--corpus", required=True)
    runner.add_argument("--out", required=True)
    runner.add_argument("--tag", required=True, help="请求号前缀，同一前缀重跑会认成同一份、不重复识别")
    runner.add_argument("--only", help="只跑这几个编号（逗号分隔）")
    runner.add_argument("--timeout", type=int, default=3600)
    scorer = sub.add_parser("score")
    scorer.add_argument("--sample", required=True)
    scorer.add_argument("--corpus", required=True)
    scorer.add_argument("--out", required=True)
    scorer.add_argument("--glm")
    args = parser.parse_args()
    (run if args.command == "run" else score)(args)


if __name__ == "__main__":
    sys.exit(main())
