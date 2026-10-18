#!/usr/bin/env python3
"""Claude 识别路线的上线闸门（ADR-022）：语料**原件**走产品的正式代码，读法经共用层 ``claude_ocr.client`` 交甲骨文的
claude-ocr 服务（与生产同一条路：同一份转正、渲染、提示词、分级、转换）。测出来的就是生产会存下的结果。

- ``invoices``：原件 → Cowork ``ocr.execution.claude.recognize`` → ``prepare_history_batch``（存档前的最后一步）→ 存下的记录；
  同一次读到的结果再原样交 ERP 采购的 ``read_all`` → ``_structured``（不再付费），两边各写一份评测打分工具认的格式
  （``runs/<前缀>-prod-cowork.jsonl`` / ``-prod-erp.jsonl``），金标 / 热敏 / Express 照原来的打分口径比。
- ``recon``：原件 → 对账的正式入口（银行账单 ``bank_intake.read_statement_file``、总账 ``read_ledger_file``、税报
  ``recon_center.sources.report_source`` → ``vat_parse.decode``），模型那一步是 ``recon_center.document_ocr`` 的
  Claude 读法（``ocr.execution.claude_documents``）；写出对账拿到的行与余额链核对结果（``runs/recon-prod.jsonl``）。

在甲骨文的 gate 容器里跑（python 3.14，依赖与生产镜像同版本；见 ``run-gate.sh``）。付费：按每份结果的实际花费累计，
到 ``--cap`` 美元就停；已跑过的份数跳过（断了重跑接着来）。只读语料，不写任何站点。
"""
import argparse
import csv
import json
import logging
import os
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SRC = Path(os.environ.get("GATE_SRC", "/src"))
CORPUS = Path(os.environ.get("GATE_CORPUS", "/corpus"))
RUNS = Path(os.environ.get("GATE_RUNS", "/runs"))
for app in ("pearnly_core", "pearnly_cowork", "pearnly_erp_portal"):
    sys.path.insert(0, str(SRC / "apps" / app))


class _Flags(dict):
    __getattr__ = dict.get


def _stub_frappe():
    """产品代码里只碰得到的几个 Frappe 名字：站点配置（选了 Claude）、日志、报错。不连数据库、不写站点。"""
    frappe = types.ModuleType("frappe")
    frappe.conf = {"pearnly_ocr_engine": "claude"}
    frappe.flags = _Flags()
    frappe.local = types.SimpleNamespace(site="gate.localhost", lang="en")
    frappe.session = types.SimpleNamespace(user="Administrator")
    frappe._ = lambda text, *args, **kwargs: text
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.logger = lambda *a, **k: logging.getLogger("frappe")

    class ValidationError(Exception):
        pass

    def throw(message, exc=ValidationError, *a, **k):
        raise (exc if isinstance(exc, type) and issubclass(exc, Exception) else ValidationError)(message)
    frappe.ValidationError, frappe.PermissionError, frappe.throw = ValidationError, ValidationError, throw
    frappe.db = None  # 没有站点数据库：产品代码里「if frappe.db」的地方照没连库的口径走（如 ERP 默认泰语）
    frappe.log_error = lambda *a, **k: None
    frappe.get_traceback = lambda *a, **k: ""
    frappe.__path__ = []  # 当包用：产品模块顶上 ``from frappe.utils import …`` 只取几个纯函数
    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda value, default=0: int(float(value)) if str(value or "").strip() not in ("", "None") else default
    utils.flt = lambda value, precision=None: round(float(value or 0), precision) if precision is not None else float(value or 0)
    utils.cstr = lambda value: "" if value is None else str(value)
    from datetime import date, datetime
    utils.now_datetime = datetime.now
    utils.now = lambda: datetime.now().isoformat(sep=" ")
    utils.nowdate = lambda: date.today().isoformat()
    utils.getdate = lambda value=None: date.fromisoformat(str(value)[:10]) if value else date.today()
    utils.get_datetime = lambda value=None: datetime.fromisoformat(str(value)) if value else datetime.now()
    frappe.utils = utils

    def placeholder(name):
        """产品模块导入时顺带碰到、闸门用不到的 Frappe 名字：类名给空类（可继承），其余给什么都不做的函数。"""
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (), {}) if name[:1].isupper() else (lambda *a, **k: None)
    frappe.__getattr__ = utils.__getattr__ = placeholder

    import importlib.abc
    import importlib.machinery

    class _Stubs(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.startswith("frappe.") and fullname not in sys.modules:
                return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
            return None

        def create_module(self, spec):
            module = types.ModuleType(spec.name)
            module.__path__, module.__getattr__ = [], placeholder
            return module

        def exec_module(self, module):
            pass
    sys.meta_path.insert(0, _Stubs())
    sys.modules["frappe"], sys.modules["frappe.utils"] = frappe, utils
    work = types.ModuleType("pearnly_core.execution.work")
    work.checkpoint = lambda key, state=None, body=None: None  # 闸门每次都真读，不取断点
    work.has_completed_call = lambda: False
    sys.modules["pearnly_core.execution.work"] = work
    import pearnly_core.execution as execution
    execution.work = work


_stub_frappe()
from pearnly_core.claude_ocr import client  # noqa: E402

_real_read = client.read
_local = threading.local()
LOCK = threading.Lock()


def seen():
    """这个线程正在读的那份原件里 client.read 的每次结果（花费、升级、原始读法）。"""
    if not hasattr(_local, "items"):
        _local.items = []
    return _local.items


def _recording_read(kind, files, own_tax=None, text=None):
    outcome = _real_read(kind, files, own_tax, text)
    seen().append(outcome)
    return outcome


client.read = _recording_read


def _pages(path, content):
    if path.suffix.lower() == ".pdf":
        import pymupdf
        with pymupdf.open(stream=content, filetype="pdf") as document:
            return len(document)
    return 1


def _spent(name):
    out = RUNS / name
    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()] if out.exists() else []
    # 出错的那份不算跑过（下次重跑再读）；花费照记。
    return {row["id"]: row for row in rows if not row.get("error")}, sum(row.get("usd") or 0 for row in rows)


# ── 发票类：原件 → Cowork 存下的记录；同一次读法交 ERP 采购 ─────────────────────────────────────────────────────


def _invoice(fields):
    return {"invoice_number": fields.get("invoice_number"), "date": fields.get("date"), "seller_tax": fields.get("seller_tax"),
            "buyer_tax": fields.get("buyer_tax"), "seller_branch": fields.get("seller_branch"),
            "buyer_branch": fields.get("buyer_branch"), "seller_name": fields.get("seller_name"),
            "buyer_name": fields.get("buyer_name"), "subtotal": fields.get("subtotal"), "vat": fields.get("vat"),
            "total": fields.get("total_amount"), "wht_amount": fields.get("wht_amount"),
            "document_type": fields.get("document_type")}


# 这个租户选的单据口径（ADR-022 闸门默认同金标口径：转账凭条、预扣税凭证不录）；``--policy none`` = 还没选（照算票，推送前问）。
POLICY = {"payment_evidence": "skip", "withholding_certificate": "skip"}


def cowork_read(content, filename, pages, year):
    from pearnly_cowork.ocr.execution import claude
    from pearnly_cowork.ocr.results import prepare_history_batch
    from pearnly_cowork.ocr.schema import PipelineResult

    from pearnly_core.originals import checks as originals

    try:
        originals.inspect(filename, content)  # 上传时同一份检查：有密码的 PDF 等在这里就被拒，到不了识别
    except originals.Rejected as rejected:
        return {"entry": "not_invoice", "invoices": [], "state": "rejected_at_upload", "rejected": str(rejected)}
    pipeline, _evidence = claude.recognize(content, filename, pages, year)
    result = PipelineResult.model_validate_json(pipeline, context={"anchor_year": year})
    plan = prepare_history_batch(result, expected_page_count=pages, workspace="W", source="manual", client=None,
                                 direction=None, posting_kind=None, payment=None, reference_kind="physical_page",
                                 document_policy=POLICY)
    invoices = [_invoice(group["pages"][0].get("fields") or {}) for group in plan["groups"]]
    for invoice, group in zip(invoices, plan["groups"]):
        invoice["page"] = (group["source_page_indices"] or [1])[0]
    return {"entry": "enter" if invoices else "not_invoice", "invoices": invoices, "state": plan["state"]}


def erp_read(outcome, content, filename):
    """同一次读法原样交 ERP 采购的 ``read_all``（``client.read`` 换成交回这次结果，不再付费）。"""
    from pearnly_erp_portal.purchasing import recognition as purchasing
    from pearnly_erp_portal.recognition import claude as erp_claude

    original = erp_claude.client.read
    erp_claude.client.read = lambda *a, **k: outcome
    try:
        found = erp_claude.read_all([(content, "application/pdf" if filename.lower().endswith(".pdf") else "image/jpeg")],
                                    structured=purchasing._structured, review=purchasing.review, label="purchase")
    finally:
        erp_claude.client.read = original
    invoices = []
    for result, _issues, _model in found:
        if result.get("not_document"):
            continue
        printed = result.get("printed") or {}
        branch = result.get("supplier_branch_no") or ("00000" if result.get("supplier_branch_type") == "head_office" else None)
        invoices.append({"invoice_number": result.get("doc_no"), "date": result.get("doc_date"),
                         "seller_tax": result.get("supplier_tax_id"), "buyer_tax": result.get("buyer_tax_id"),
                         "seller_branch": branch, "subtotal": printed.get("subtotal"), "vat": printed.get("vat"),
                         "total": printed.get("total"), "document_type": result.get("document_type")})
    return {"entry": "enter" if invoices else "not_invoice", "invoices": invoices}


def invoice_sets():
    """每组语料：(编号, 原件路径)。金标按 samples.tsv 的路径，Express 按编号找原件，热敏是原图。"""
    gold = json.load(open(CORPUS / "gold/gold-final.json"))
    rows = {r["编号"]: r for r in csv.DictReader(open(CORPUS / "gold/samples.tsv", encoding="utf-8-sig"), delimiter="\t")}
    express = {}
    for path in (CORPUS / "originals/06-录入推送").rglob("*"):
        if path.is_file():
            express.setdefault(path.name.split("_", 1)[0], path)
    listing = [r["编号"] for r in csv.DictReader(open(CORPUS / "express/list.tsv", encoding="utf-8"), delimiter="\t")]
    return {"test": [(k, CORPUS / "originals" / rows[k]["路径"]) for k in sorted(gold)],
            "sm": [(p.stem, p) for p in sorted((CORPUS / "sm/raw").iterdir())],
            "ex": [(k, express[k]) for k in listing if k in express]}


def invoices(args):
    from pearnly_core.execution.errors import Deferred

    items = invoice_sets()[args.set]
    if args.ids:
        wanted = set(args.ids.split(","))
        items = [item for item in items if item[0] in wanted]
    name, erp_name = f"{args.set}-prod-cowork.jsonl", f"{args.set}-prod-erp.jsonl"
    done, spent = _spent(name)
    budget = {"spent": spent}

    def one(entry):
        key, path = entry
        with LOCK:
            if budget["spent"] >= args.cap:
                return
        content = path.read_bytes()
        pages = _pages(path, content)
        seen().clear()
        started = time.monotonic()
        try:
            read, error = cowork_read(content, path.name, pages, args.year), None
        except (Deferred, Exception) as exc:  # noqa: BLE001  读不出 / 服务出错：记下，接着下一份
            read, error = None, f"{type(exc).__name__}: {str(exc)[:200]}"
        outcomes = list(seen())
        outcome = outcomes[-1] if outcomes else {}
        item = {"id": key, "pages": pages, "seconds": round(time.monotonic() - started, 2), "outcome": outcome.get("read"),
                "usd": round(sum(o.get("usd") or 0 for o in outcomes), 6), "escalated": any(o.get("escalated") for o in outcomes),
                "why": outcome.get("why"), "where": outcome.get("where"), "doc_kind": (outcome.get("read") or {}).get("doc_kind")}
        try:
            erp = erp_read(outcome, content, path.name) if outcome.get("read") else None
        except Exception as exc:  # noqa: BLE001  ERP 那边出错单独记，不丢 Cowork 这份
            erp = {"entry": "error", "invoices": [], "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        with LOCK:
            budget["spent"] += item["usd"]
            with open(RUNS / name, "a") as fh:
                fh.write(json.dumps(item | ({"read": read | {"doc_kind": item["doc_kind"]}} if read else {"error": error}),
                                    ensure_ascii=False) + "\n")
            if erp:
                with open(RUNS / erp_name, "a") as fh:
                    fh.write(json.dumps(item | {"read": erp | {"doc_kind": item["doc_kind"]}}, ensure_ascii=False) + "\n")
        print(key, pages, "页", item["seconds"], "s", f"${item['usd']:.4f}", "升级" if item["escalated"] else "", error or "",
              flush=True)

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, [item for item in items if item[0] not in done]))
    print(json.dumps({"set": args.set, "usd": round(budget["spent"], 4)}))


# ── 对账三类文档：原件 → 对账的正式入口 ──────────────────────────────────────────────────────────────────────


def claude_read(document_type, content, filename):
    """``recon_center.document_ocr._read_claude`` 的同一读法（去掉看板与让出这些站点上的事）。"""
    from pearnly_core import ocr
    from pearnly_cowork.ocr.execution import claude_documents
    from pearnly_cowork.ocr.inputs_runtime.source import PageSource

    pages = _pages(Path(filename), content)
    with PageSource(content, filename, expected_page_count=pages, enable_text_path=False, image_max_long_edge=2400,
                    orient=ocr.orient) as source:
        return claude_documents.read_document(source, document_type,
                                              lambda kind, image, mime, number: client.read(kind, [(image, mime)]))


def _rows(result):
    return [row if isinstance(row, dict) else row.__dict__ for row in result.get("rows") or []]


def _chain(rows, opening):
    """对账拿到的账单行：相邻两行都有余额时，上一行余额 + 存入 − 取出 = 本行余额（与评测同一口径）。"""
    checked = broken = 0
    previous = opening
    for row in rows:
        balance = row.get("balance")
        if balance in (None, ""):
            previous = None
            continue
        if previous not in (None, ""):
            checked += 1
            moved = float(row.get("deposit") or 0) - float(row.get("withdrawal") or 0)
            if abs(float(previous) + moved - float(balance)) > 0.01:
                broken += 1
        previous = balance
    return checked, broken


def recon(args):
    """清单每行：``bank<TAB>账单<TAB>总账[<TAB>科目]`` 或 ``vat<TAB>税报``（路径相对语料的 recon/）。银行这套按对账的正式
    入口读两边（要模型的那边走 Claude），再用对账的配对（``bank_matching.reconcile``）出会计看到的结果。"""
    from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import document_model_rows
    from pearnly_cowork.reconciliation import bank_intake, bank_matching, vat_parse
    from pearnly_cowork.recon_center import sources

    name = args.out
    done, spent = _spent(name)
    budget = {"spent": spent}
    lines = [line.split("\t") for line in Path(args.list).read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]

    def one(parts):
        kind, key = parts[0], "\t".join(parts)
        with LOCK:
            if budget["spent"] >= args.cap:
                return
        seen().clear()
        started = time.monotonic()
        item = {"id": key, "kind": kind}
        try:
            if kind == "bank":
                stmt_path, gl_path = CORPUS / "recon" / parts[1], CORPUS / "recon" / parts[2]
                model = document_model_rows(read=claude_read, gl_account_code=parts[3] if len(parts) > 3 else "")
                stmt = bank_intake.read_statement_file(stmt_path.read_bytes(), stmt_path.name, model_rows=model)
                gl = bank_intake.read_ledger_file(gl_path.read_bytes(), gl_path.name,
                                                  account_code=parts[3] if len(parts) > 3 else "", model_rows=model)
                rows = _rows(stmt)
                item.update(stmt_ok=stmt.get("ok"), stmt_rows=len(rows), stmt_source=stmt.get("source"),
                            stmt_error=stmt.get("error_code"), needs_review=stmt.get("needs_review"),
                            balance_warn=stmt.get("balance_warn_count"), chain=_chain(rows, stmt.get("opening")),
                            gl_ok=gl.get("ok"), gl_rows=len(gl.get("rows") or []), gl_source=gl.get("source"),
                            gl_error=gl.get("error_code"))
                if stmt.get("ok") and gl.get("ok"):
                    _results, summary = bank_matching.reconcile(stmt["rows"], gl["rows"],
                        stmt_opening=float(stmt.get("opening") or 0), gl_opening=float(gl.get("opening") or 0),
                        stmt_closing=float(stmt.get("closing") or 0), gl_closing=float(gl.get("closing") or 0))
                    item["summary"] = {k: round(v, 2) if isinstance(v, float) else v for k, v in summary.__dict__.items()
                                       if k in ("matched_count", "stmt_deposit_only_count", "stmt_withdrawal_only_count",
                                                "gl_debit_only_count", "gl_credit_only_count", "stmt_opening", "stmt_closing",
                                                "gl_opening", "gl_closing", "formula_diff")}
            else:
                path = CORPUS / "recon" / parts[1]
                source = sources.report_source(path.read_bytes(), path.name, read=claude_read)
                decoded = vat_parse.decode(source) or {}
                rows = decoded.get("rows") or []
                item.update(ok=bool(rows), rows=len(rows), method=decoded.get("method"),
                            pre_vat=round(sum(float(r.get("report_amount_pre_vat") or 0) for r in rows), 2),
                            entries=[{k: r.get(k) for k in ("report_invoice_no", "report_date", "report_amount_pre_vat",
                                                            "report_vat_amount", "report_buyer_tax_id")} for r in rows])
        except Exception as exc:  # noqa: BLE001
            item.update(error=f"{type(exc).__name__}: {str(exc)[:300]}")
        outcomes = list(seen())
        item.update(seconds=round(time.monotonic() - started, 2), usd=round(sum(o.get("usd") or 0 for o in outcomes), 6),
                    pages_read=len(outcomes), escalated=sum(1 for o in outcomes if o.get("escalated")))
        with LOCK:
            budget["spent"] += item["usd"]
            with open(RUNS / name, "a") as fh:
                fh.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
        print(json.dumps(item, ensure_ascii=False, default=str)[:400], flush=True)

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, [parts for parts in lines if "\t".join(parts) not in done]))
    print(json.dumps({"set": "recon", "usd": round(budget["spent"], 4)}))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    one = sub.add_parser("invoices")
    one.add_argument("--set", choices=("test", "sm", "ex"), required=True)
    one.add_argument("--ids")
    one.add_argument("--year", type=int, default=2026)
    one.add_argument("--cap", type=float, default=3.0)
    one.add_argument("--workers", type=int, default=6)
    one.add_argument("--policy", choices=("skip", "none"), default="skip")
    two = sub.add_parser("recon")
    two.add_argument("--list", required=True)
    two.add_argument("--cap", type=float, default=3.0)
    two.add_argument("--workers", type=int, default=4)
    two.add_argument("--out", default="recon-prod.jsonl")
    args = parser.parse_args()
    RUNS.mkdir(parents=True, exist_ok=True)
    if getattr(args, "policy", "skip") == "none":
        POLICY.clear()
    {"invoices": invoices, "recon": recon}[args.cmd](args)


if __name__ == "__main__":
    main()
