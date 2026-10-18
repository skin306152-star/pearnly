"""Cowork 对账三类文档（银行账单 / 总账 / 销项税报告）的 Claude 读法（ADR-022）：本站点选了 Claude 时，扫描件、照片和要模型兜底的
PDF 逐页交共用层 ``pearnly_core.claude_ocr``（Haiku 读、本页数字自己对不上交 Opus），不调 Document AI / Gemini。

页面渲染与谷歌那条路同一个 ``PageSource``；每页结果按对账原有格式（``ocr.enterprise.adapter.make_page``）拼成整份，
跨页核对（``check_document``）、读不出的页、页上明确印着「无交易」都与原来同样标记，下游对账代码不动。纯计算：不碰数据库。
"""
import threading
from decimal import Decimal, InvalidOperation

from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.enterprise.adapter import PipelineResult, make_page
from pearnly_cowork.ocr.enterprise.local.result import check_document
from pearnly_cowork.ocr.enterprise.pipeline import CATEGORIES, to_legacy

ENGINE = "claude_v1"
KINDS = {"bank": "bank", "gl": "gl", "vat": "vat_report"}
THB_PER_USD = 35


def _serial(render):
    """PDF 渲染库不是线程安全的：各页同时读时，渲染一次只做一页。"""
    lock = threading.Lock()

    def invoke(number):
        with lock:
            return render(number)
    return invoke


class Numbered:
    """原件在甲骨文、按页交它读（原件层，ADR-024）：这里只有页数，页不在这台渲染 —— ``read_page`` 收到的图与类型为空，
    按页号让甲骨文自己出这一页（``pearnly_core.claude_ocr.pages.frame``，与 ``PageSource.frame`` 同一做法）。"""
    _blank = type("Blank", (), {"image_bytes": None, "mime_type": None})()

    def __init__(self, page_count):
        self.page_count = int(page_count)

    def frame(self, _number):
        return self._blank


def _money(value):
    try:
        return Decimal(str(value).replace(",", "")) if str(value or "").strip() else None
    except InvalidOperation:
        return None


def _ledger_opening(document):
    """总账第 1 页没读出期初（Claude 偶尔把只印余额的「ยอดยกมา」行整行略过，10-18 闸门 BBL6764）：按第一笔印的余额倒推
    —— 第一笔之前的余额，方向（借增 / 贷增）按本页各行余额怎么走定。只用印出来的数；第一笔没印余额就不补。"""
    rows = document.get("entries") or []
    first = next((row for row in rows if _money(row.get("balance")) is not None), None)
    if first is None or rows.index(first) != 0:
        return ""
    steps = []
    for before, row in zip(rows, rows[1:]):
        a, b = _money(before.get("balance")), _money(row.get("balance"))
        if a is not None and b is not None:
            steps.append((b - a, (_money(row.get("debit")) or 0) - (_money(row.get("credit")) or 0)))
    sign = -1 if sum(abs(m + n) < Decimal("0.01") for m, n in steps) > sum(abs(m - n) < Decimal("0.01") for m, n in steps) else 1
    net = (_money(first.get("debit")) or 0) - (_money(first.get("credit")) or 0)
    return str(_money(first["balance"]) - sign * net)


def _page(outcome, category, number):
    read = outcome.get("read") if outcome else None
    if read is None:
        # 两次都没读出这一页：只丢这一页，其余页照常（与谷歌路线的「读不出的页」同样标记，银行余额链在这里断开交人确认）。
        page = make_page({"entries": []}, category, number, issues=["page_unreadable"])
        page.extraction_audit["page_unreadable"] = True
        return page
    document = dict(read)
    no_data = str(document.pop("no_data_printed", "") or "").strip().lower() == "yes"
    derived = category == "gl" and number == 1 and not str(document.get("opening_balance") or "").strip()
    if derived:
        document["opening_balance"] = _ledger_opening(read)
    document = {key: ("" if value is None else value) for key, value in document.items()}
    document["entries"] = [{key: ("" if value is None else value) for key, value in row.items()} | {"page": str(number)}
                           for row in read.get("entries") or []]
    issues = ["schema_transcription_requires_document_validation"]  # 单页对平不代表整份对，与谷歌路线同一标记
    if not document["entries"]:
        issues.append("no_transaction_rows")
    page = make_page(document, category, number, issues=issues)
    if no_data and not document["entries"]:
        page.extraction_audit["no_data_printed"] = True  # 原文印着「无流水」：对账按 0 行完成
    stages = outcome.get("stages") or []
    page.layer_chain = ["CLAUDE", *[str(s.get("model", "")).upper() for s in stages]]
    page.layer2_model = stages[-1]["model"] if stages else ""
    page.layer2_ms = int(sum(s.get("seconds") or 0 for s in stages) * 1000)
    page.layer2_input_tokens = sum((s.get("usage") or {}).get("input_tokens", 0) for s in stages)
    page.layer2_output_tokens = sum((s.get("usage") or {}).get("output_tokens", 0) for s in stages)
    page.extraction_audit["claude"] = {"escalated": bool(outcome.get("escalated")), "why": list(outcome.get("why") or []),
                                       "where": outcome.get("where"), "opening_derived": bool(derived and document["opening_balance"])}
    return page


def read_document(source, document_type, read_page, map_pages=None):
    """``source``：已打开的 ``PageSource``；``read_page(kind, image_bytes, mime, page_number)`` → 共用层 ``client.read`` 的结果（读不出时
    ``read`` 为 None）。``map_pages``：各页同时读（与发票引擎同一约定）。返回对账的整份结果（``to_legacy`` 形状）。"""
    category = CATEGORIES[document_type]
    kind = KINDS[category]
    numbers = list(range(1, source.page_count + 1))
    together = map_pages is not None and len(numbers) > 1
    render = _serial(source.frame) if together else source.frame

    def one(number):
        frame = render(number)
        return read_page(kind, frame.image_bytes, frame.mime_type, number)

    outcomes = map_pages(one, numbers) if together else [one(number) for number in numbers]
    pages = [_page(outcome, category, number) for number, outcome in zip(numbers, outcomes)]
    if all(page.extraction_audit.get("page_unreadable") for page in pages):
        raise ContentError("ocr.enterprise_schema_failed", detail="every page unreadable")
    if category in ("bank", "gl"):
        aggregate = {"opening_balance": pages[0].document.opening_balance, "closing_balance": pages[-1].document.closing_balance,
                     "entries": [{**row.model_dump(), "page": str(page.page_number)} for page in pages for row in page.document.entries]}
        for page in pages:
            page.validation_warnings.extend(check_document(aggregate, category, len(pages)))
    usd = sum(outcome.get("usd") or 0 for outcome in outcomes if outcome)
    seconds = sum(outcome.get("seconds") or 0 for outcome in outcomes if outcome)
    result = PipelineResult(pages=pages, page_count=len(pages), elapsed_ms=int(seconds * 1000), engine=ENGINE,
                            estimated_cost_thb=round(usd * THB_PER_USD, 4))
    return to_legacy(result)
