"""银行对账准确率评测：用重放框架按当前代码重算已跑过的对账（零费用），按套、按系列出准确率（2026-10-02）。

基准：总账一侧（B04 Mrs Flowers 同账户同月电子总账、B05–B09 Express 账套生成的总账、B07 GIZE Express 当月总账）是准确数据；
B03（同一张账单的电子版 bank17 与扫描版 bank18 / bank19）、B02（同一份总账的电子版与无文字层版）用电子版逐行比扫描版。

口径（与压测窗口约定）：
- 余额链：断点为 0 的运行比例；断点行数 ÷ 总行数（备用识别测试同一口径，可直接比）。
- 账单与总账：账单期初 / 期末对总账期初 / 期末的差；账单存 / 取合计对总账借 / 贷合计。
- 匹配：匹配上的笔数 ÷ 账单笔数。
- 电子对扫描：扫描 / 照片读出的行对电子版读出的行的召回与准确（日期、存、取、余额都一样才算同一行）。
- 已知语料问题照压测窗口的清单标出，不算识别错。

用法（生产机一次性容器，同 replay-bank-recon.py，另挂本文件与输出目录）：
  ... /eval-bank.py --sites testcowork.pearnly.com,testcowork2.pearnly.com,testcowork3.pearnly.com --since 2026-10-01
      --corpus /corpus --sites-from /sites-ro --out /out
"""
import argparse
import collections
import importlib.util
import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
KNOWN = {  # 已知语料问题（压测窗口清单）：标出、不算识别错
    "B07-GIZE-BBL-CA-2026-01": "只有汇总页",
    "B02-罗勇五十铃-TTB-2026-05": "期初本来就是 0",
}
PAIRS = {  # 同一份资料的电子版与扫描版：(侧, 电子版, [扫描版…])
    "B03-KBank往来户-2025-06-电子vs扫描": ("statement", "bank17.pdf", ["bank18.pdf", "bank19.pdf"]),
    "B02-罗勇五十铃-TTB-2026-05": ("ledger", "gl_0_GL TTB.pdf", ["gl_ttb_7p_180dpi.image-only.pdf"]),
}


def _replay_module():
    path = Path(os.environ.get("REPLAY_SCRIPT") or HERE / "replay-bank-recon.py")
    spec = importlib.util.spec_from_file_location("replay_bank_recon", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def num(value):
    try:
        return Decimal(str(value)) if value not in (None, "", "None") else None
    except InvalidOperation:
        return None


def case_of(title):
    found = re.search(r"(B\d\d-.+?-\d{4}-\d{2}(?:-[^\]\s]*?(?:照片|电子vs扫描))?)(?:-mu\d+.*|-load-u.*|$)", title.split("] ", 1)[-1])
    return found.group(1) if found else title.split("] ", 1)[-1]


def chain(statement):
    """``(断点数, 有余额的行数)``：相邻两行余额对不上（前一行余额 + 存 − 取 ≠ 本行余额）算一处。"""
    breaks, counted, previous = 0, 0, None
    for _date, deposit, withdrawal, balance in statement:
        balance = num(balance)
        if balance is None:
            continue
        counted += 1
        if previous is not None and abs(previous + (num(deposit) or 0) - (num(withdrawal) or 0) - balance) > Decimal("0.01"):
            breaks += 1
        previous = balance
    return breaks, counted


def cent(value):
    value = num(value)
    return None if value is None else str(value.quantize(Decimal("0.01")))


def line_set(rows, width):
    return collections.Counter(tuple(cent(value) if index else value for index, value in enumerate(row[:width]))
                               for row in rows or [])


def pair_score(result, case):
    side, electronic, scans = PAIRS[case]
    rows = result.get("per_file" if side == "statement" else "per_ledger") or {}
    width = 4 if side == "statement" else 3
    truth = line_set(rows.get(electronic), width)
    out = {}
    for scan in scans:
        got = line_set(rows.get(scan), width)
        same = sum((truth & got).values())
        out[scan] = {"truth_rows": sum(truth.values()), "read_rows": sum(got.values()), "same": same,
                     "recall": round(same / sum(truth.values()), 4) if truth else None,
                     "precision": round(same / sum(got.values()), 4) if got else None}
    return out


TIERS = ((0, 20, "≤20"), (21, 40, "21–40"), (41, 10 ** 6, ">40"))


def tier_of(rows):
    return next(label for low, high, label in TIERS if low <= rows <= high)


def against_ledger(statement, ledger):
    """账单逐笔对总账（总账是准确数据）：同方向同金额配掉，剩下的分漏行（总账有、账单没有）、多行（账单有、总账没有）、
    拆行（一笔总账被读成两行，两行相加等于它）、并行（两笔总账被读成一行）、金额错一位（同方向、只差一位数字）。"""
    def side_amount(row, deposit_index, withdrawal_index):
        deposit, withdrawal = num(row[deposit_index]) or Decimal(0), num(row[withdrawal_index]) or Decimal(0)
        return ("in", deposit.quantize(Decimal("0.01"))) if deposit else ("out", withdrawal.quantize(Decimal("0.01")))
    read = collections.Counter(side_amount(row, 1, 2) for row in statement)
    truth = collections.Counter(side_amount(row, 1, 2) for row in ledger)
    extra, missing = read - truth, truth - read
    extra_list, missing_list = list(extra.elements()), list(missing.elements())
    split = merged = digit = 0
    for side, amount in list(missing_list):
        parts = [value for s_, value in extra_list if s_ == side]
        if any(a + b == amount for i, a in enumerate(parts) for b in parts[i + 1:]):
            split += 1
    for side, amount in list(extra_list):
        parts = [value for s_, value in missing_list if s_ == side]
        if any(a + b == amount for i, a in enumerate(parts) for b in parts[i + 1:]):
            merged += 1

    def one_digit(a, b):
        a, b = f"{a:.2f}", f"{b:.2f}"
        return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1
    for side, amount in extra_list:
        if any(s_ == side and one_digit(amount, value) for s_, value in missing_list):
            digit += 1
    return {"ledger_rows": sum(truth.values()), "read_rows": sum(read.values()), "missing": len(missing_list),
            "extra": len(extra_list), "split": split, "merged": merged, "amount_one_digit": digit}


def metrics(result, case):
    if "error" in result or "missing" in result:
        return {"failed": result.get("error") or f"missing {result.get('missing')}"}
    statement, ledger, header = result["statement"], result["ledger"], result["header"]
    breaks, counted = chain(statement)
    deposit = sum((num(row[1]) or 0) for row in statement)
    withdrawal = sum((num(row[2]) or 0) for row in statement)
    debit = sum((num(row[1]) or 0) for row in ledger)
    credit = sum((num(row[2]) or 0) for row in ledger)
    opening = (num(header["stmt_opening"]) or 0) - (num(header["gl_opening"]) or 0)
    closing = (num(header["stmt_closing"]) or 0) - (num(header["gl_closing"]) or 0)
    out = {"rows": len(statement), "balance_rows": counted, "breaks": breaks, "matched": result["matched"],
           "match_rate": round(result["matched"] / len(statement), 4) if statement else None,
           "deposit_vs_debit": str(deposit - debit), "withdrawal_vs_credit": str(withdrawal - credit),
           "opening_diff": str(opening), "closing_diff": str(closing),
           "totals_agree": abs(deposit - debit) <= Decimal("0.01") and abs(withdrawal - credit) <= Decimal("0.01")}
    if case in PAIRS:
        out["pair"] = pair_score(result, case)
    if case.startswith(("B04", "B05", "B06", "B07", "B08", "B09")):
        out["vs_ledger"] = against_ledger(statement, ledger)
    pages = [page for diag in (result.get("page_diag") or {}).values() for page in diag]
    if pages:
        out["pages"] = [{**page, "tier": tier_of(page["rows"])} for page in pages]
        out["tier"] = tier_of(max(page["rows"] for page in pages))
    return out


def summarize(runs):
    def block(items):
        done = [item for item in items if "failed" not in item["m"]]
        rows = sum(item["m"]["rows"] for item in done)
        counted = sum(item["m"]["balance_rows"] for item in done)
        return {"runs": len(items), "failed": len(items) - len(done),
                "chain_pass_rate": round(sum(item["m"]["breaks"] == 0 for item in done) / len(done), 4) if done else None,
                "break_row_ratio": round(sum(item["m"]["breaks"] for item in done) / counted, 4) if counted else None,
                "match_rate": round(sum(item["m"]["matched"] for item in done) / rows, 4) if rows else None,
                "totals_agree_rate": round(sum(item["m"]["totals_agree"] for item in done) / len(done), 4) if done else None,
                "rows": rows}
    scored = [run for run in runs if run["case"] not in KNOWN]
    series = collections.defaultdict(list)
    for run in scored:
        series[run["case"][:3]].append(run)
    pairs = collections.defaultdict(list)
    for run in runs:
        for scan, value in (run["m"].get("pair") or {}).items():
            pairs[f"{run['case']} {scan}"].append(value)
    tiers = collections.defaultdict(lambda: collections.Counter())
    for run in scored:
        for page in run["m"].get("pages") or []:
            tier = tiers[page["tier"]]
            tier["pages"] += 1
            tier["rows"] += page["rows"]
            tier["local"] += page["local"]
            tier["reread"] += page["reread"]
            tier["still_broken"] += page["breaks_left"] > 0
    ledger_by_tier = collections.defaultdict(lambda: collections.Counter())
    for run in scored:
        if run["m"].get("vs_ledger") and run["m"].get("tier"):
            ledger_by_tier[run["m"]["tier"]].update({**run["m"]["vs_ledger"], "runs": 1,
                                                     "breaks": run["m"]["breaks"], "balance_rows": run["m"]["balance_rows"]})
    dense = {key: block(value) for key, value in {
        "B07-GIZE-BAY-CA": [run for run in scored if run["case"].startswith("B07-GIZE-BAY-CA")],
        "B07-GIZE-SCB-CA": [run for run in scored if run["case"].startswith("B07-GIZE-SCB-CA")]}.items() if value}
    return {"overall": block(scored), "by_series": {key: block(value) for key, value in sorted(series.items())},
            "page_tiers": {label: {**dict(tiers[label]),
                                   "local_share": round(tiers[label]["local"] / tiers[label]["pages"], 4) if tiers[label]["pages"] else None,
                                   "reread_share": round(tiers[label]["reread"] / tiers[label]["pages"], 4) if tiers[label]["pages"] else None,
                                   "still_broken_share": round(tiers[label]["still_broken"] / tiers[label]["pages"], 4) if tiers[label]["pages"] else None}
                           for _low, _high, label in TIERS},
            "vs_ledger_by_tier": {label: {**dict(value), "break_row_ratio": round(value["breaks"] / value["balance_rows"], 4)
                                          if value["balance_rows"] else None} for label, value in ledger_by_tier.items()},
            "dense_sets": dense,
            "known_corpus_issues": {case: KNOWN[case] for case in {run["case"] for run in runs} if case in KNOWN},
            "electronic_vs_scan": {key: {"runs": len(values),
                                         "recall": round(sum(v["recall"] or 0 for v in values) / len(values), 4),
                                         "precision": round(sum(v["precision"] or 0 for v in values) / len(values), 4)}
                                   for key, values in pairs.items()}}


def render(report):
    overall = report["overall"]
    lines = ["# 银行对账准确率（重放已存结果，零费用）", "",
             f"运行 {overall['runs']} 次（失败 {overall['failed']}）：余额链全通 {overall['chain_pass_rate']}，"
             f"断点行 / 总行 {overall['break_row_ratio']}，匹配率 {overall['match_rate']}，账单存取合计与总账借贷一致 {overall['totals_agree_rate']}。",
             "", "| 系列 | 运行 | 余额链全通 | 断点行比 | 匹配率 | 合计一致 |", "| --- | --- | --- | --- | --- | --- |"]
    for key, value in report["by_series"].items():
        lines.append(f"| {key} | {value['runs']} | {value['chain_pass_rate']} | {value['break_row_ratio']} | "
                     f"{value['match_rate']} | {value['totals_agree_rate']} |")
    lines += ["", "电子对扫描（扫描 / 照片读出的行对电子版）："]
    lines += [f"- {key}：{value['runs']} 次，召回 {value['recall']}，准确 {value['precision']}"
              for key, value in report["electronic_vs_scan"].items()]
    lines += ["", "按每页笔数分档（页数、本地拼平、重读、重读后仍断的页占比）："]
    lines += [f"- {label}：{value.get('pages', 0)} 页 {value.get('rows', 0)} 笔，本地拼平 {value['local_share']}，重读 {value['reread_share']}，"
              f"重读后仍断 {value['still_broken_share']}" for label, value in report["page_tiers"].items()]
    lines += ["", "逐笔对总账（按文件最密一页分档）："]
    lines += [f"- {label}：{value.get('runs')} 次，总账 {value.get('ledger_rows')} 笔、读出 {value.get('read_rows')}，漏 {value.get('missing')}、"
              f"多 {value.get('extra')}、拆 {value.get('split')}、并 {value.get('merged')}、金额错一位 {value.get('amount_one_digit')}，"
              f"断点行比 {value.get('break_row_ratio')}" for label, value in report["vs_ledger_by_tier"].items()]
    lines += ["", "密集套：" + json.dumps(report["dense_sets"], ensure_ascii=False)]
    lines += ["", "已知语料问题（未计入）：" + json.dumps(report["known_corpus_issues"], ensure_ascii=False)]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", required=True)
    parser.add_argument("--since", default="2026-10-01")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--sites-from")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    replay = _replay_module()
    if args.sites_from:
        import shutil

        for name in ("common_site_config.json", "apps.txt"):
            shutil.copy(Path(args.sites_from, name), Path(replay.SITES, name))
        for site in args.sites.split(","):
            Path(replay.SITES, site, "logs").mkdir(parents=True, exist_ok=True)
            shutil.copy(Path(args.sites_from, site, "site_config.json"), Path(replay.SITES, site, "site_config.json"))
    os.chdir(replay.SITES)
    import frappe

    index = replay.corpus_index(args.corpus)
    runs = []
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "bank-runs.jsonl", "w", encoding="utf-8") as handle:
        for site in args.sites.split(","):
            frappe.init(site, sites_path=replay.SITES)
            frappe.connect()
            frappe.db.sql("SET SESSION TRANSACTION READ ONLY")
            jobs = replay.recon_jobs(site)
            for row in frappe.db.sql("SELECT name FROM `tabCowork Bank Reconciliation` WHERE creation >= %s ORDER BY creation",
                                     [args.since], as_dict=True):
                doc = frappe.get_doc("Cowork Bank Reconciliation", row.name)
                _job, user, works = jobs.get(doc.request_key) or (None, doc.owner, {})
                if not frappe.db.exists("User", user):
                    user = "Administrator"
                frappe.set_user(user)
                result = replay.replay(doc, works, index, "new", details=True)
                frappe.set_user("Administrator")
                case = case_of(doc.title)
                run = {"site": site, "task": doc.name, "title": doc.title, "case": case, "m": metrics(result, case)}
                runs.append(run)
                handle.write(json.dumps(run, ensure_ascii=False, default=str) + "\n")
                handle.flush()
            frappe.destroy()
    report = summarize(runs)
    (out / "bank-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "bank-report.md").write_text(render(report), encoding="utf-8")
    print(render(report))


if __name__ == "__main__":
    main()
