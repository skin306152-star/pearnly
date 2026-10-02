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
  l1:    同一容器写法，``/eval.py l1 --site testcowork.pearnly.com --out /out/<轮次>``：不调谷歌，从检查点桶（7 天自动删）
           取回这一轮每页的 Vision 原文存成 ``l1.jsonl``，供零费用重放。
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
            row["答案目录"] = (row.get("答案目录") or "").replace("\\", "/")
            # 金标扩充样本没有「方向」：销项发票按销售交，其余按采购（打分以金标的方向为准）
            row.setdefault("方向", "销售" if row.get("分类") == "销项发票" else "采购")
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
    for source_name in [name for name in (args.resubmit or "").split(",") if name]:
        # 站点上已有的原件（如压测回归用的 Big C 27 页）按新请求号重读一遍：编号记成原件名
        record = {"编号": source_name, "file": source_name, "submitted": time.time(), "source": source_name}
        try:
            # 同一份原件只能有一个识别任务：取出原字节，按新请求号另建一份原件再交
            from pearnly_cowork.files import access as file_access

            original = frappe.get_doc(file_access.SOURCE, source_name)
            key = f"{args.tag}-{source_name[-12:]}"
            copy = files.create(workspace, original.filename, files.read_bytes(original), key, None)
            batch = ocr.submit(copy["name"], key)
            frappe.db.commit()
            record["batch"] = batch["name"]
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


def collect(args):
    """不提交，只把站点上已有的一轮（请求号前缀 ``--tag``）收成 results.jsonl：评测中途被打断（如余额用完）后，
    识别任务自己接着跑完，再用它把这一轮补齐。"""
    import frappe

    frappe.init(args.site, sites_path=".")
    frappe.connect()
    frappe.set_user(args.user)
    from pearnly_cowork.ocr.execution import service as ocr

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    states = collections.Counter()
    with open(out / "results.jsonl", "w", encoding="utf-8") as handle:
        for row in frappe.db.sql("SELECT name, request_key, creation, modified FROM `tabCowork OCR Batch` WHERE request_key LIKE %s",
                                 (f"{args.tag}-%",), as_dict=True):
            view = ocr.detail(row.name)
            number = row.request_key[len(args.tag) + 1:]
            record = {"编号": number, "batch": row.name, "state": view["state"], "requires_review": view["requires_review"],
                      "last_code": view.get("last_code"), "seconds": None,
                      "invoices": _invoices(view["history_records"]) if view["state"] == "saved" else []}
            states[record["state"]] += 1
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(states))


def l1(args):
    """不调谷歌，只把一轮里每页的 Vision 原文（``layer1``）从检查点桶取回存下，供以后零费用重放（票头、分店、单号前缀）。
    检查点桶 7 天自动删，评测跑完一周内取。按批次的调用证据（``provider_evidence``）里的检查点钥匙找对象；
    只存整页文字，不存坐标。输出 ``<out>/l1.jsonl``：一份原件一行 ``{编号, batch, pages: {页码: 原文}}``。"""
    import gzip
    import hashlib
    from urllib.parse import quote

    import frappe
    import requests
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    frappe.init(args.site, sites_path=".")
    frappe.connect()
    from pearnly_core import ocr as core_ocr
    from pearnly_cowork.ocr.remote import tasks

    bucket = (tasks.settings() or {}).get("bucket")
    if not bucket:
        raise SystemExit("task dispatch (checkpoint bucket) is not configured on this site")
    credentials = service_account.Credentials.from_service_account_file(
        core_ocr.credentials_path(), scopes=["https://www.googleapis.com/auth/devstorage.read_only"])
    session = requests.Session()

    def fetch(name):
        if not credentials.valid:
            credentials.refresh(Request())
        response = session.get(f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(name, safe='')}",
                               params={"alt": "media"}, headers={"Authorization": f"Bearer {credentials.token}"}, timeout=60)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        content = response.content
        return json.loads(gzip.decompress(content) if content[:2] == b"\x1f\x8b" else content)

    out = Path(args.out)
    counts = collections.Counter()
    with open(out / "results.jsonl", encoding="utf-8") as source, open(out / "l1.jsonl", "w", encoding="utf-8") as handle:
        for line in source:
            record = json.loads(line)
            row = frappe.db.get_value("Cowork OCR Batch", record.get("batch"), ["work_name", "provider_evidence"], as_dict=True)
            if not row or not row.work_name:
                counts["no_batch"] += 1
                continue
            pages = {}
            for call in json.loads(row.provider_evidence or "[]"):
                if call.get("stage") != "layer1" or str(call.get("page")) in pages:
                    continue
                name = f"{args.site}/{row.work_name}/cp/" + hashlib.sha256(call["checkpoint_key"].encode()).hexdigest() + ".json"
                found = fetch(name)
                body = (found or {}).get("body") or {}
                responses = body.get("responses") if isinstance(body, dict) else None
                text = ((responses or [{}])[0].get("fullTextAnnotation") or {}).get("text") if responses else None
                counts["page_text" if text else "page_missing"] += 1
                if text:
                    pages[str(call.get("page"))] = text
            counts["docs_with_text" if pages else "docs_without_text"] += 1
            handle.write(json.dumps({"编号": record["编号"], "batch": record.get("batch"), "pages": pages},
                                    ensure_ascii=False) + "\n")
    print(json.dumps(counts))


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
                             "document_type", "wht_amount")}, "items": len(fields.get("items") or [])})
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
    """这家公司所有账套的进项 / 销项税登记行，加进货单 / 销货单（不带增值税的单据不进税登记，只在单据表里）。
    金额为 0 的行（作废、冲销）不要。"""
    rows = []
    for register, direction in (("进货单.csv", "purchase"), ("销货单.csv", "sale")):
        for path in sorted(Path(corpus, answer_dir).glob(f"*/{register}")):
            with open(path, encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    net, vat, gross = money(row.get("合计")), money(row.get("税额")), money(row.get("净额"))
                    if not gross:
                        continue
                    rows.append({"direction": direction, "ledger": path.parent.name, "source": "document",
                                 "doc_no": row.get("对方单号") or row.get("对方参考") or "", "voucher": row.get("凭证号") or "",
                                 "date": parse_day(row.get("日期")), "party_name": row.get("对方名称") or "",
                                 "party_tax": digits(row.get("对方税号")), "party_branch": branch(row.get("对方分店")),
                                 "net": net or Decimal(0), "vat": vat or Decimal(0), "gross": gross})
    for register, direction in (("进项税登记.csv", "purchase"), ("销项税登记.csv", "sale")):
        for path in sorted(Path(corpus, answer_dir).glob(f"*/{register}")):
            with open(path, encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    net, vat, zero = money(row.get("未税额")), money(row.get("税额")), money(row.get("零税额"))
                    if not ((net or 0) + (zero or 0) + (vat or 0)):
                        continue
                    rows.append({"direction": direction, "ledger": path.parent.name, "source": "register",
                                 "doc_no": row.get("对方单号") or "",
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


def same_number(got, want):
    """单号数字部分相同，或较长的一串里包含较短的（≥ 6 位，原件多印了前后缀）。"""
    got, want = doc_no_key(got)[1], doc_no_key(want)[1]
    return bool(got and want) and (got == want or (min(len(got), len(want)) >= 8 and (got in want or want in got)))


class Answers:
    """一家公司的答案行，按金额（分）、单号数字、税号建索引：每张票只比可能的那几行，不逐行扫几万行。"""

    def __init__(self, rows):
        self.rows, self.by_amount, self.by_no, self.by_tax = rows, collections.defaultdict(list), \
            collections.defaultdict(list), collections.defaultdict(list)
        for index, row in enumerate(rows):
            for amount in (row["gross"], row["net"]):
                if amount:
                    self.by_amount[cents(amount)].append(index)
            number = doc_no_key(row["doc_no"])[1]
            if number:
                self.by_no[number].append(index)
            if row["party_tax"]:
                self.by_tax[row["party_tax"]].append(index)
        self.years = {row["date"].year for row in rows if row["date"]}

    def candidates(self, item):
        found = set()
        for amount in (item["gross"], item["net"]):
            if amount is not None:
                for delta in (-1, 0, 1):
                    found.update(self.by_amount.get(cents(amount) + delta, ()))
        number = doc_no_key(item["doc_no"])[1]
        if number:
            found.update(self.by_no.get(number, ()))
            if len(number) >= 8:
                found.update(index for key, indexes in self.by_no.items() if len(key) >= 8 and (key in number or number in key)
                             for index in indexes)
        return [self.rows[index] for index in sorted(found)]


def cents(amount):
    return int((amount * 100).to_integral_value())


def match(item, answers):
    """配对：单号、对方税号、金额（含税或未税）、日期（同一天，或 45 天内——水电费会计按缴费收据的日期入账）。
    认的组合：单号 + 任一项、税号 + 金额、金额 + 45 天内。打分取最高；并列取日期最近；还并列而且是同一行的重复
    （相邻账套重叠登了同一张）就取第一行，否则算「多个候选」。回 ``(行, 吻合项)`` 或 ``(None, 原因)``。"""
    pool = [row for row in answers.candidates(item) if item["direction"] in (None, row["direction"])]
    years = answers.years
    scored = []
    for row in pool:
        hits = set()
        if same_number(item["doc_no"], row["doc_no"]):
            hits.add("doc_no")
        if item["party_tax"] and item["party_tax"] == row["party_tax"]:
            hits.add("party_tax")
        if close(item["gross"], row["gross"]) or close(item["net"], row["net"]):
            hits.add("amount")
        gap = abs((item["date"] - row["date"]).days) if item["date"] and row["date"] else None
        if gap == 0:
            hits.add("date")
        elif gap is not None and gap <= 45:
            hits.add("date_near")
        near = bool(hits & {"date", "date_near"})
        # 单号或金额至少一项，再加税号（或单号 / 金额另一项）：同一个税号（电力局）加日期配得上别家的票（压测窗口核对原件）
        if not (("doc_no" in hits and hits & {"party_tax", "amount"}) or {"party_tax", "amount"} <= hits):
            continue
        score = 3 * ("doc_no" in hits) + 2 * ("party_tax" in hits) + 2 * ("amount" in hits) + ("date" in hits) + 0.5 * (
            "date_near" in hits)
        scored.append((score, -(gap if gap is not None else 999), sorted(hits), row))
    if not scored:
        return None, "answer_year_missing" if item["date"] and item["date"].year not in years else "no_candidate"
    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    best = [entry for entry in scored if entry[:2] == scored[0][:2]]
    rows = {(str(entry[3]["gross"]), entry[3]["party_tax"]) for entry in best}  # 税登记与单据表里同一张票算一行
    if len(rows) > 1:
        return None, "ambiguous"
    return scored[0][3], scored[0][2]


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
    elif category in UTILITY and got_no:
        result["doc_no"] = ("receipt_basis", None)  # 账单上有户号、账单号，会计登的是收据号
    else:
        result["doc_no"] = ("wrong", "missing" if not got_no else "doc_no_other")
    gap = abs((item["date"] - row["date"]).days) if item["date"] and row["date"] else None
    if item["date"] == row["date"]:
        result["date"] = ("ok", None)
    elif category in UTILITY and gap is not None and gap <= 45:
        result["date"] = ("receipt_basis", None)  # 账单印的是出账日，会计按缴费收据日入账
    else:
        result["date"] = ("wrong", classify("date", item["date"], row["date"], item))
    for field in ("party_tax", "party_branch"):
        want, got = row[field], item[field]
        if not want:
            result[field] = ("n/a", None)
        elif got == want:
            result[field] = ("ok", None)
        elif not got and category in UTILITY:
            result[field] = ("not_printed", None)
        elif field == "party_branch" and category in UTILITY:
            result[field] = ("receipt_basis", None)  # 水电账单上没印分店，会计按收据上的供电所 / 水务所分店号入账
        else:
            result[field] = ("wrong", classify(field, got, want, item))
    ratio = difflib.SequenceMatcher(None, name_key(item["party_name"]), name_key(row["party_name"])).ratio()
    result["party_name"] = ("ok", None) if ratio >= 0.8 else (
        "not_printed", None) if not item["party_name"] and category in UTILITY else ("wrong", "name_other")
    for field in ("net", "vat", "gross"):
        result[field] = ("ok", None) if close(item[field], row[field]) else ("wrong", classify(
            field, item[field], row[field], {**item, "vat": row["vat"]}))
    return result


_texts = {}


def pdf_text(path):
    """电子 PDF 的文字层（原件上实际印的内容）：用来裁决「识别错」还是「会计录法不同」。读不出回空串。"""
    if path not in _texts:
        try:
            from pdfminer.high_level import extract_text

            _texts[path] = extract_text(str(path)) or ""
        except Exception:
            _texts[path] = ""
    return _texts[path]


def printed(field, value, text):
    """这个值在原件文字层里有没有：单号、税号、分店按数字找，金额按两位小数（带不带千分位）找，名称按去空白后找。"""
    if value in (None, "", "None"):
        return False
    flat = re.sub(r"\s+", "", text)
    if field in ("doc_no",):
        return bool(alnum(value)) and alnum(value) in alnum(text)
    if field in ("party_tax",):
        return bool(digits(value)) and digits(value) in re.sub(r"\D", "", text)
    if field == "party_branch":
        number = digits(value) or "0"
        return f"{int(number):05d}" in re.sub(r"\D", "", text) or (number == "0" and ("สำนักงานใหญ่" in flat or "HEADOFFICE" in flat.upper()))
    if field in ("net", "vat", "gross"):
        amount = money(value)
        return amount is not None and (f"{amount:,.2f}" in flat or f"{amount:.2f}" in flat)
    if field == "party_name":
        return bool(name_key(value)) and name_key(value) in name_key(text)
    return False


def adjudicate(item, row, wrong, path):
    """电子 PDF 上逐个错的字段裁决：答案的值印在原件上、识别的没印 → 识别错；识别的值印在原件上、答案的没印 → 会计录法
    与原件不同（不算识别错）；其余说不清。"""
    text = pdf_text(path)
    if not text.strip():
        return {}
    got = {"doc_no": item["doc_no"], "party_tax": item["party_tax"], "party_branch": item["party_branch"],
           "party_name": item["party_name"], "net": item["net"], "vat": item["vat"], "gross": item["gross"]}
    verdicts = {}
    for field in wrong:
        if field not in got:
            continue
        want_on, got_on = printed(field, row[field], text), printed(field, got[field], text)
        verdicts[field] = ("recognition_error" if want_on and not got_on else
                           "answer_differs_from_document" if got_on and not want_on else "unclear")
    return verdicts


def load_verdicts(path):
    """人工对照原件的裁决：``{(编号, 字段或 *): 裁决}``。裁决：recognition_error 识别错、answer_convention 会计录法
    与原件不同、not_in_answers 答案里本来没有、non_invoice 不是票据、scorer_mismatch 打分器配错。"""
    verdicts = {}
    if path and Path(path).exists():
        with open(path, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                verdicts[(row["编号"].strip(), (row.get("字段") or "*").strip() or "*")] = row["裁决"].strip()
    return verdicts


def score(args):
    sample = read_sample(args.sample)
    verdicts = load_verdicts(args.verdicts)
    by_id = {row["编号"]: row for row in sample}
    results = [json.loads(line) for line in open(Path(args.out, "results.jsonl"), encoding="utf-8")]
    glm = {row["编号"] for row in read_sample(args.glm)} if args.glm else set()
    current = {"dir": None, "answers": None}
    per_invoice, broken, unmatched, not_invoice, functional = [], [], [], [], []
    for record in sorted(results, key=lambda record: by_id[record["编号"]]["答案目录"]):  # 一家一家来，答案用完就放
        row = by_id[record["编号"]]
        if record["state"] == "not_invoice":
            not_invoice.append({"编号": row["编号"], "方向": row.get("方向") or "", "分类": row["分类"], "形态": row["形态"]})
            continue
        if record["state"] in ("rejected",) or (record["state"] != "saved" and not record.get("invoices")):
            broken.append({"编号": row["编号"], "方向": row.get("方向") or "", "公司": row["公司"], "形态": row["形态"], "state": record["state"],
                           "message": record.get("error") or record.get("last_code")})
            continue
        if current["dir"] != row["答案目录"]:
            current.update(dir=row["答案目录"], answers=Answers(load_answers(args.corpus, row["答案目录"])))
        answers = current["answers"]
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
            errors = {k: v[1] for k, v in fields.items() if v[0] == "wrong"}
            if verdicts.get((row["编号"], "*")) in ("void_recorded", "non_invoice_recorded"):
                functional.append({**base, "kind": verdicts[(row["编号"], "*")]})
            if verdicts.get((row["编号"], "*")) in ("scorer_mismatch", "not_in_answers", "non_invoice", "non_invoice_recorded"):
                unmatched.append({**base, "reason": verdicts[(row["编号"], "*")]})
                continue
            judged = adjudicate(item, found, errors, Path(args.corpus, row["副本"])) if (
                errors and row["形态"] == "电子PDF") else {}
            for field in errors:
                manual = verdicts.get((row["编号"], field))
                if manual == "recognition_error":
                    judged[field] = "recognition_error"
                elif manual == "answer_convention":
                    judged[field] = "answer_differs_from_document"
            per_invoice.append({**base, "matched_on": how, "answer": {k: str(v) for k, v in found.items()},
                                "result": {k: v[0] for k, v in fields.items()}, "errors": errors, "adjudicated": judged})
    run_info = json.loads(Path(args.out, "run.json").read_text()) if Path(args.out, "run.json").exists() else {}
    report = summarize(per_invoice, unmatched, broken, results)
    report["not_invoice"] = {"count": len(not_invoice), "by_category": dict(collections.Counter(
        f"{item['方向']}/{item['分类']}/{item['形态']}" for item in not_invoice).most_common())}
    report["functional_errors"] = dict(collections.Counter(item["kind"] for item in functional))
    report["broken_detail"] = dict(collections.Counter(f"{item['state']}: {item['message']}" for item in broken))
    report["run"] = run_info
    Path(args.out, "score.json").write_text(json.dumps({"summary": report, "invoices": per_invoice,
        "unmatched": unmatched, "broken": broken, "not_invoice": not_invoice}, ensure_ascii=False, default=str, indent=1), encoding="utf-8")
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
                                if verdict in ("prefix_only", "not_printed", "receipt_basis"))
    seconds = sorted(record["seconds"] for record in results if record.get("seconds"))
    electronic = [item for item in regular if item["形态"] == "电子PDF"]
    confirmed_all = collections.Counter(field for item in regular for field, verdict in item["adjudicated"].items()
                                        if verdict == "recognition_error")
    unresolved_all = collections.Counter(field for item in regular for field in item["errors"]
                                         if item["adjudicated"].get(field) not in ("recognition_error", "answer_differs_from_document"))
    cells_all = collections.Counter(field for item in regular for field, verdict in item["result"].items() if verdict in ("ok", "wrong"))
    judged = collections.Counter(f"{field}:{verdict}" for item in electronic for field, verdict in item["adjudicated"].items())
    confirmed = collections.Counter(field for item in electronic for field, verdict in item["adjudicated"].items()
                                    if verdict == "recognition_error")
    cells = collections.Counter(field for item in electronic for field, verdict in item["result"].items() if verdict in ("ok", "wrong"))
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
        "confirmed": {"field_error_rate": round(sum(confirmed_all.values()) / sum(cells_all.values()), 4) if cells_all else None,
                      "unresolved_rate": round(sum(unresolved_all.values()) / sum(cells_all.values()), 4) if cells_all else None,
                      "by_field": {field: {"confirmed": confirmed_all[field], "unresolved": unresolved_all[field], "n": cells_all[field]}
                                   for field in FIELDS if cells_all[field]}},
        "electronic_adjudicated": {"verdicts": dict(judged.most_common()),
                                   "confirmed_error_rate": {field: round(confirmed[field] / cells[field], 4)
                                                            for field in FIELDS if cells[field]},
                                   "confirmed_field_error_rate": round(sum(confirmed.values()) / sum(cells.values()), 4)
                                   if cells else None},
        "review": {"flag_rate": round(len(flagged) / len(regular), 4) if regular else None,
                   "invoices_with_error": len(with_error), "flagged_with_error": len(with_error) - len(missed),
                   "missed_rate": round(len(missed) / len(with_error), 4) if with_error else None},
        "seconds": {"p50": seconds[len(seconds) // 2] if seconds else None,
                    "p95": seconds[int(len(seconds) * 0.95) - 1] if seconds else None},
    }


def render(report):
    regular = report["regular"]
    lines = ["# 发票识别准确率", "",
             f"样本 {report['samples']} 份；打不开的原件 {report['broken']} 份（不计入）{report.get('broken_detail')}；"
             f"判成「不是发票」{report.get('not_invoice', {}).get('count')} 份（不计入准确率；要对照原件确认是不是真的非票据，"
             f"eval1 的 45 份经人工核对全部确实不是票据）；"
             f"配上答案 {report['matched_invoices']} 张、未对上 {report['unmatched_invoices']} 张 {report['unmatched_reasons']}。", "",
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
              "", "功能性错误（人工对照原件：作废单据照常入账、非票据被当成发票入账）：" + json.dumps(report.get("functional_errors"), ensure_ascii=False),
              "", f"确认错误率（电子 PDF 文字层裁决 + 人工对照原件确认的识别错 ÷ 全部比对字段）：{report['confirmed']['field_error_rate']}；"
                  f"尚未裁决的不一致 {report['confirmed']['unresolved_rate']}；逐字段 " + json.dumps(report["confirmed"]["by_field"], ensure_ascii=False),
              "", "电子 PDF 用原件文字层裁决（识别错 / 会计录法与原件不同 / 说不清）："
                  + json.dumps(report["electronic_adjudicated"], ensure_ascii=False),
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


# ── gold：对照金标（压测窗口逐份看原件、记票面印的值）─────────────────────────────

GOLD_FIELDS = (("doc_no", "单号", "invoice_number"), ("date", "日期", "date"), ("seller_tax", "卖方税号", "seller_tax"),
               ("seller_branch", "卖方分店", "seller_branch"), ("buyer_tax", "买方税号", "buyer_tax"),
               ("buyer_branch", "买方分店", "buyer_branch"), ("net", "未税额", "subtotal"), ("vat", "税额", "vat"),
               ("gross", "合计", "total_amount"), ("wht", "预扣税额", "wht_amount"))


def gold_value(field, value):
    if field in ("net", "vat", "gross", "wht"):
        return money(value) or None
    if field == "date":
        return str(value or "").strip()[:10] or None
    if field in ("seller_branch", "buyer_branch"):
        text = digits(value) if value not in (None, "") else None
        return None if text is None and value in (None, "") else (text or "0")
    if field in ("seller_tax", "buyer_tax"):
        return digits(value) or None
    return alnum(value) or None


def gold_align(golds, invoices):
    """一份文件里的金标票与识别出的票对齐：单号相同 3 分、合计相同 2 分、日期相同 1 分，至少 2 分才算同一张，按分高的先配。"""
    pairs = []
    for g_index, gold in enumerate(golds):
        for r_index, invoice in enumerate(invoices):
            fields = invoice["fields"]
            score = 3 * (bool(gold_value("doc_no", gold["单号"])) and gold_value("doc_no", gold["单号"]) == gold_value(
                "doc_no", fields.get("invoice_number"))) + 2 * close(money(gold["合计"]), money(fields.get("total_amount"))) + (
                bool(gold["日期"]) and gold_value("date", gold["日期"]) == gold_value("date", fields.get("date")))
            if score >= 2:
                pairs.append((score, g_index, r_index))
    used_g, used_r, matched = set(), set(), []
    for score, g_index, r_index in sorted(pairs, reverse=True):
        if g_index in used_g or r_index in used_r:
            continue
        used_g.add(g_index)
        used_r.add(r_index)
        matched.append((golds[g_index], invoices[r_index]))
    return matched, [g for i, g in enumerate(golds) if i not in used_g], [r for i, r in enumerate(invoices) if i not in used_r]


def gold_score(results, gold_rows):
    by_doc = collections.defaultdict(list)
    for row in gold_rows:
        by_doc[row["编号"]].append(row)
    cells = collections.Counter()
    wrong = collections.Counter()
    kinds = collections.Counter()
    docs = collections.Counter()
    details = []
    for record in results:
        rows = by_doc.get(record["编号"])
        if not rows:
            continue
        head = rows[0]
        invoices = record.get("invoices") or []
        expected = [row for row in rows if row["是否票据"] == "是"]
        if not expected:  # 不是票据 / 作废：应当判成不是发票
            docs["reject_expected"] += 1
            docs["reject_correct" if not invoices else "non_invoice_recorded"] += 1
            continue
        if record["state"] == "rejected":
            docs["unopenable"] += 1
            continue
        docs["invoice_docs"] += 1
        if not invoices:
            docs["missed_document"] += 1
            details.append({"编号": record["编号"], "field": "*", "kind": "missed_document", "state": record["state"],
                            "types": sorted({row["票据类型"] for row in expected})})
            continue
        matched, missed, extra = gold_align(expected, invoices)
        docs["gold_invoices"] += len(expected)
        docs["missed_invoices"] += len(missed)
        docs["extra_invoices"] += len(extra)
        for gold, invoice in matched:
            fields = invoice["fields"]
            for field, column, key in GOLD_FIELDS:
                if key not in fields:  # 早几轮没记这一栏（预扣税额）：不比，不算错
                    continue
                want, got = gold_value(field, gold[column]), gold_value(field, fields.get(key))
                printed = want is not None
                if not printed and field in ("seller_tax",) and "IT" in (gold.get("备注") or "") + "DE":
                    continue
                group = (field, head["方向"], head["形态"], "printed" if printed else "not_printed")
                if not printed and got is None:
                    cells[group] += 1
                    continue
                if not printed and field in ("net", "vat"):
                    # 没印未税 / 税额的收据：系统按记账口径推出未税 = 合计 − 税额、税额 = 0，不算多填（与印面不矛盾）
                    gross, vat = money(gold["合计"]), money(gold["税额"]) or Decimal(0)
                    derived = (gross - vat) if field == "net" and gross is not None else Decimal(0)
                    if close(got, derived) or (field == "vat" and got == 0):
                        cells[group] += 1
                        continue
                cells[group] += 1
                ok = (close(got, want) if field in ("net", "vat", "gross", "wht") else got == want)
                if not ok:
                    wrong[group] += 1
                    kind = ("overfilled" if not printed else "missing" if got is None else
                            classify(field if field in ("date", "net", "vat", "gross", "wht") else "x", got, want, {}))
                    kinds[f"{field}:{kind}"] += 1
                    details.append({"编号": record["编号"], "field": field, "kind": kind, "printed": printed})
    def rate(selector):
        n = sum(v for k, v in cells.items() if selector(k))
        w = sum(v for k, v in wrong.items() if selector(k))
        return {"n": n, "wrong": w, "rate": round(w / n, 4) if n else None}
    core = lambda k: k[0] != "wht"  # 预扣税额早几轮没记：总口径不含它，单列
    report = {"documents": dict(docs), "overall": rate(core),
              "printed": rate(lambda k: core(k) and k[3] == "printed"),
              "not_printed_filled": rate(lambda k: core(k) and k[3] == "not_printed"),
              "by_field": {field: rate(lambda k, f=field: k[0] == f) for field, _c, _k in GOLD_FIELDS},
              "by_direction_form": {f"{d}/{f}": rate(lambda k, d=d, f=f: (k[1], k[2]) == (d, f))
                                    for d, f in sorted({(k[1], k[2]) for k in cells})},
              "kinds": dict(kinds.most_common(25))}
    return report, details


def gold(args):
    gold_rows = []
    with open(args.gold, encoding="utf-8-sig", newline="") as handle:
        gold_rows = [{key.strip(): (value or "").strip() for key, value in row.items()} for row in csv.DictReader(handle, delimiter="\t")]
    reports = {}
    for out in args.runs:
        results = [json.loads(line) for line in open(Path(out, "results.jsonl"), encoding="utf-8")]
        report, details = gold_score(results, gold_rows)
        Path(out, "gold.json").write_text(json.dumps({"report": report, "details": details}, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        reports[Path(out).name] = report
    lines = ["# 金标打分（票面印的值）", "", "| 指标 | " + " | ".join(reports) + " |", "| --- |" + " --- |" * len(reports)]
    def cell(value):
        return f"{value['rate']} ({value['wrong']}/{value['n']})"
    for label, key in (("字段总错误率", "overall"), ("印了的读错或漏读", "printed"), ("没印却填了", "not_printed_filled")):
        lines.append(f"| {label} | " + " | ".join(cell(r[key]) for r in reports.values()) + " |")
    for field, _c, _k in GOLD_FIELDS:
        lines.append(f"| {field} | " + " | ".join(cell(r["by_field"][field]) for r in reports.values()) + " |")
    keys = sorted({key for r in reports.values() for key in r["by_direction_form"]})
    for key in keys:
        lines.append(f"| {key} | " + " | ".join(cell(r["by_direction_form"][key]) if key in r["by_direction_form"] else "-"
                                              for r in reports.values()) + " |")
    lines += ["", "文件级：" + json.dumps({name: r["documents"] for name, r in reports.items()}, ensure_ascii=False),
              "", "错误类别：" + json.dumps({name: r["kinds"] for name, r in reports.items()}, ensure_ascii=False)]
    text = "\n".join(lines) + "\n"
    Path(args.runs[-1], "gold-compare.md").write_text(text, encoding="utf-8")
    print(text)


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
    runner.add_argument("--resubmit", help="站点上已有的原件名（逗号分隔），按新请求号重读")
    runner.add_argument("--timeout", type=int, default=3600)
    scorer = sub.add_parser("score")
    scorer.add_argument("--sample", required=True)
    scorer.add_argument("--corpus", required=True)
    scorer.add_argument("--out", required=True)
    scorer.add_argument("--glm")
    scorer.add_argument("--verdicts", help="人工裁决表（编号、字段、裁决、说明），见 load_verdicts")
    collector = sub.add_parser("collect")
    collector.add_argument("--site", required=True)
    collector.add_argument("--user", required=True)
    collector.add_argument("--tag", required=True)
    collector.add_argument("--out", required=True)
    golder = sub.add_parser("gold")
    golder.add_argument("--gold", required=True)
    golder.add_argument("--runs", nargs="+", required=True, help="一个或几个 run 的输出目录，并排比")
    l1_reader = sub.add_parser("l1")
    l1_reader.add_argument("--site", required=True)
    l1_reader.add_argument("--out", required=True)
    args = parser.parse_args()
    {"run": run, "score": score, "gold": gold, "collect": collect, "l1": l1}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
