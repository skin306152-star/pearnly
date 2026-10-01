"""Enterprise pipeline: pinned OCR -> local schema, or pinned image + schema on every page.

Source: ec2e5c957e6b ``services/ocr/enterprise_pipeline.py`` (``read_pdf``, ``_local_pages``, ``run``).
Kept as deployed: Document AI reads a PDF in chunks (deployed 15 pages, now ``MAX_PAGES_PER_READ``; an
image once); bank statements and ledgers are first reconstructed locally from the page geometry and used as they are
when the arithmetic passes; otherwise — and always for a VAT report, which has no local parser —
every page is extracted by the frozen contract (the page image, the Enterprise transcript, 3.8 LOW,
the contract's JSON schema); a page whose extraction does not come back as an object fails the file
(the deployed pipeline never substitutes another method). The ledger and statement pages extracted
that way are checked as one document afterwards.

Changed for this platform: provider calls are injected (``document_ai_call``, ``model_call``) so they
run inside the durable OCR Work (checkpoints, shared budget, deferral); pages are rendered only when
the schema extraction needs them, one at a time (the deployed code rendered every page up front);
the model timeout is 180 s as deployed (120 s until 2026-09-29, the provider ceiling then). The cost estimate counts
the Document AI pages plus each extraction's tokens at the deployed ``services/ocr/cost.py`` prices
(internal observation only, never billing; until 2026-09-29 the model tokens were left out).
"""

from __future__ import annotations

import json
import re
import threading
import time
from copy import deepcopy
from functools import partial
from typing import Callable, Iterable, List, Tuple

from pearnly_cowork.ocr.engine.contracts import ModelRequest
from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.enterprise import contract, header_bank, pages as enterprise_pages, self_check
from pearnly_cowork.ocr.enterprise.adapter import PipelineResult, make_page
from pearnly_cowork.ocr.enterprise.local.geometry import rows_from_payloads
from pearnly_cowork.ocr.enterprise.local.result import check_document, reconstruct
from pearnly_cowork.ocr.enterprise.request import build_request

ENGINE = contract.CONTRACT_VERSION  # "enterprise-2026-09-03-v1", the deployed engine label
THB_PER_USD = 35.0
# USD per million input / output tokens: deployed services/ocr/cost.py MODEL_PRICES_PER_M_USD (2026-07 price
# list). An unknown model is priced as the most expensive one listed, as deployed (observation errs high).
MODEL_PRICES_PER_M_USD = {"gemini-3.8-flash": (0.75, 3.75), "gemini-3.1-flash-lite": (0.25, 1.50)}


def model_cost_thb(model, input_tokens, output_tokens):
    price = next((value for prefix, value in MODEL_PRICES_PER_M_USD.items() if str(model or "").startswith(prefix)),
                 max(MODEL_PRICES_PER_M_USD.values()))
    usd = max(0, int(input_tokens or 0)) / 1e6 * price[0] + max(0, int(output_tokens or 0)) / 1e6 * price[1]
    return round(usd * THB_PER_USD, 6)
MODEL_TIMEOUT_SECONDS = 180
CATEGORIES = {"bank_statement": "bank", "general_ledger": "gl", "vat_report": "vat"}


def pdf_chunks(pdf: bytes, page_count: int) -> List[Tuple[bytes, str, int]]:
    """The deployed ``read_pdf`` split: ``(chunk bytes, mime, pages)`` of at most ``MAX_PAGES_PER_READ`` pages each."""
    import pymupdf

    chunks = []
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        if document.page_count != page_count:
            raise ContentError("ocr.enterprise_page_count_mismatch")
        for start in range(0, page_count, enterprise_pages.MAX_PAGES_PER_READ):
            end = min(start + enterprise_pages.MAX_PAGES_PER_READ, page_count)
            with pymupdf.open() as chunk:
                chunk.insert_pdf(document, from_page=start, to_page=end - 1)
                # 不写新的 /ID：pymupdf 默认每次写出都给一个随机文件 ID，同一页块字节不同、检查点键跟着变，
                # 续跑时已读完的块又调一次、又付一次费（2026-09-29 压测 BAY-CA-03：12 页读了 5 遍，最后超时）。
                chunks.append((chunk.tobytes(no_new_id=True), "application/pdf", end - start))
    return chunks


def _local_pages(result, category):
    pages = []
    previous = result.document.get("opening_balance", "")
    for number in range(1, result.pages + 1):
        document = deepcopy(result.document)
        document["entries"] = [row for row in document["entries"] if int(row["page"]) == number]
        document["opening_balance"] = previous
        document["closing_balance"] = document["entries"][-1].get("balance", "")
        previous = document["closing_balance"]
        # Document-wide totals must not be repeated as each page's printed totals.
        for field in ("total_debit", "total_credit"):
            document[field] = ""
        pages.append(
            make_page(
                document,
                category,
                number,
                audit=result.audit if number == 1 else [],
                provenance=result.provenance if number == 1 else [],
                issues=result.issues,
                local=True,
            )
        )
    return pages


def _local(payloads, category, page_count):
    """A balanced local reconstruction or None.

    Added 2026-09-29: the deployed reconstruction raised on a page without date rows (``statistics``
    has no median to take), which the deployed pipeline reported as a page-count mismatch and failed
    the whole file; now only a real page-count mismatch fails it, anything else goes on to the schema
    extraction. A bank statement the deployed reconstruction cannot balance gets one more, stricter
    attempt anchored on the printed column headers (``header_bank``).
    """
    if rows_from_payloads(payloads)[3]["pages"] != page_count:
        raise ContentError("ocr.enterprise_page_count_mismatch")
    try:
        local = reconstruct(payloads, category, expected_pages=page_count)
    except (ValueError, ArithmeticError, LookupError):
        local = None
    if (local is None or not local.arithmetic_passed) and category == "bank":
        local = header_bank.from_payloads(payloads, page_count) or local
    return local if local is not None and local.arithmetic_passed else None


def local_only(payloads, category, page_count):
    """只做本地拼行（不调模型、不花钱）：余额链算得平回 ``PipelineResult``，否则 None。带文字层的电子 PDF 用
    （``text_layer``）；页数对不上抛 ``ContentError``（与识别路同一判据）。"""
    local = _local(payloads, category, page_count)
    if local is None:
        return None
    return PipelineResult(pages=_local_pages(local, category), page_count=page_count, elapsed_ms=0, engine=ENGINE,
        estimated_cost_thb=0.0, read_ms=[])


# 自核没过的页再抽一次（用户 2026-09-30 同意，每次约 0.5 泰铢）：换一个阶段名，检查点与第一次分开记；提示里只说
# 「上次没对上、逐行重读」，不给任何页上的数，免得模型凑数。一份文件最多再读这么多页，防成本跑飞。
REREAD_STAGE = "enterprise_schema_reread"
MAX_REREAD_PAGES = 10
REREAD_REASONS = {
    "bank": "the running balance of the rows did not follow row by row",
    "vat": "the rows did not add up to the totals printed on the page",
}
# 银行页读出的行明显少于原文里带日期的行（用户 2026-10-01 同意，每次约 0.5 泰铢，与上面共用一份文件最多再读的页数）：
# 告诉模型这页大约有几笔（只给笔数，不给任何金额），常见于一页印了两张表 / 两栏、模型只读了一张（压测 B08 第 6 页）。
SHORT_ROWS_REASON = ("it returned {found} rows although the page shows about {expected} dated transaction lines; "
                     "a page can hold more than one table or column block, read all of them")


# 税报照片第一遍只看图（不给 Document AI 转写稿）；自核对不上或没读到页合计时，用原读法（带转写稿）重读这一页。
# 用户 2026-09-30 定（压测：密集税报照片整页错位，两种读法错在不同的页上，组合后没被点名的读错行为 0；每页平均
# 约 0.41 泰铢，比原来的 0.44 省）。有意改变旧线上读法（旧线上第一遍就带转写稿）；银行、总账不变。
IMAGE_FIRST = ("vat",)


def _extract(model_call, image, mime, category, transcript, number, *, reread=False, image_only=False, note=True,
             reason=None):
    request = build_request(category, transcript, image_only=image_only)
    config = request["config"]
    prompt = request["prompt"]
    if reread and note:
        prompt += ("\nA first reading of this page did not pass its arithmetic check (" + (reason or REREAD_REASONS[category])
                   + "). Read the page again directly from the image: every printed table row exactly once, in "
                     "printed order, with each amount from its own column.")
    response = model_call(ModelRequest(stage=REREAD_STAGE if reread else "enterprise_schema", page_number=number,
        attempt=0,
        model=request["model"], prompt=prompt, images=((image, mime),),
        max_output_tokens=config["max_output_tokens"], timeout_s=MODEL_TIMEOUT_SECONDS,
        temperature=config["temperature"], response_mime=True,
        thinking=config["thinking_config"]["thinking_level"], response_schema=config["response_json_schema"]))
    if not response.text or "MAX_TOKENS" in (response.finish_reason or ""):
        raise ContentError("ocr.enterprise_schema_failed", page_number=number, detail="parse")
    try:
        data = json.loads(response.text)
    except (ValueError, TypeError):
        raise ContentError("ocr.enterprise_schema_failed", page_number=number, detail="parse") from None
    if not isinstance(data, dict):
        raise ContentError("ocr.enterprise_schema_failed", page_number=number, detail="parse")
    return data, response


def _checked(model_call, picture, category, transcript, number, data, response, running, may_reread):
    """一页抽取完先自核（``self_check``）；银行页内余额链有断点、税报行合计对不上页合计，就这页再抽一次：
    银行取断点严格更少的那次，税报只有再读对上了才用再读的；再读失败（读不出、结果不明）照旧用第一次，不丢页。
    返回 ``(document, response, 留痕 or None, 再读花的钱)``；总账不核（``None``）。"""
    short = None
    if category == "bank":
        first = self_check.bank_breaks(data)
        audit = {"state": self_check.PASSED if not first else self_check.MISMATCH, "reread": False, "breaks": [first]}
        short = self_check.rows_short(data, transcript)
        if short:
            audit.update(rows=[len(data.get("entries") or [])], dated_lines=short)
    elif category == "vat":
        state, _sums = self_check.vat_state(data, running)
        audit = {"state": state, "reread": False, "printed": self_check.printed_totals(data)}
    else:
        return data, response, None, 0.0
    # 税报第一遍只看图：没读到页合计也换原读法重读一次（没有合计就没法自核）。
    retry = (self_check.MISMATCH, self_check.NO_TOTAL) if category in IMAGE_FIRST else (self_check.MISMATCH,)
    if (audit["state"] not in retry and not short) or not may_reread:
        return _repaired(data, transcript, category, audit), response, audit, 0.0
    audit["reread"] = True
    reasons = ([REREAD_REASONS[category]] if audit["state"] in retry else []) + (
        [SHORT_ROWS_REASON.format(found=len(data.get("entries") or []), expected=short)] if short else [])
    try:
        # 税报重读用原读法原样（带转写稿、不加「上次没对上」那句），与压测模拟的读法一致
        image, mime = picture()
        second, again = _extract(model_call, image, mime, category, transcript, number, reread=True,
                                 note=category not in IMAGE_FIRST, reason="; ".join(reasons))
    except ContentError:
        audit["reread_failed"] = True
        return _repaired(data, transcript, category, audit), response, audit, 0.0
    extra = model_cost_thb(again.model, again.input_tokens, again.output_tokens)
    if category == "bank":
        breaks = self_check.bank_breaks(second)
        audit["breaks"].append(breaks)
        rows, rows_again = len(data.get("entries") or []), len(second.get("entries") or [])
        recovered = False
        if short:
            audit["rows"].append(rows_again)
            # 因少读而重读：读出更多行、又没明显超出原文带日期的行数（不凭空多出行）就用（用户 2026-10-01 定）。断点因此变多
            # 的（一页印两张表，两张表的余额本来不是一条链：B08 第 6 页补回 24 行、断点 3 → 8）照样用，但这一页每行都标
            # 待复核、结果里点名这一页，请对照原件逐行核对。因断链而重读的那条照旧，但不接受行数更少的。
            better = rows < rows_again <= self_check.recovered_limit(short) or (rows_again >= rows and breaks < first)
            recovered = better and breaks > first
        else:
            better = bool(second.get("entries")) and breaks < first
        if better:
            audit.update(state=self_check.PASSED if not breaks else self_check.MISMATCH, adopted="reread")
            if recovered:
                audit["recovered"] = True
                for entry in second.get("entries") or []:
                    entry["review_required"] = True
            return _repaired(second, transcript, category, audit), again, audit, extra
        return _repaired(data, transcript, category, audit), response, audit, extra
    state, _sums = self_check.vat_state(second, running)
    if state == self_check.PASSED:
        audit.update(state=state, adopted="reread", printed=self_check.printed_totals(second))
        return second, again, audit, extra
    return data, response, audit, extra


def _repaired(data, transcript, category, audit):
    """银行页只断一处、倒推的金额原样印在本页原文里：按倒推值纠正那一笔、标成已更正（用户 2026-10-01 同意；压测
    B07-BAY-CA 第 8 页把邻行的金额读到了这一行）。不花钱、不占再读的页数；纠正后链必须完全接上，否则原样不改。"""
    if category == "bank" and audit.get("state") == self_check.MISMATCH:
        index = self_check.repair_single_break(data, transcript)
        if index is not None:
            audit.update(state=self_check.PASSED, repaired_row=index)
    return data


def _one_at_a_time(render):
    lock = threading.Lock()

    def invoke(number):
        with lock:
            return render(number)
    return invoke


def _first_reading(model_call, render, category, transcripts, keep_image):
    """第 n 页的第一遍抽取：只用这一页自己的图和转写稿，与别的页无关。回 ``(data, response, 取这页图的办法)``；银行页抽取
    重发后仍结果不明回 ``None``（这一页读不出，见 ``run``）。一页一页读时留着这页的图给再读用（与原来一样只渲染一次）；
    各页同时读时不把整份扫描件的图都留在内存里，要再读的页再渲染一次（同一页渲染结果不变，见 ``PageSource.frame``）。"""
    def read(number):
        image, mime = render(number)
        try:
            data, response = _extract(model_call, image, mime, category, transcripts[number - 1], number,
                                      image_only=category in IMAGE_FIRST)
            return data, response, ((lambda: (image, mime)) if keep_image else partial(render, number))
        except ContentError as error:
            # 只有银行账单这样兜底：余额链一定在缺的那页断开、交人确认；总账、税报没有逐笔余额，丢一页看不出来，
            # 照旧整份读不出（结果里点名那份文件）。
            if error.code != UNREAD_PAGE or category != "bank":
                raise
            return None
    return read


def run(category: str, *, page_count: int, reads: Iterable[Tuple[bytes, str, int]],
        render: Callable[[int], Tuple[bytes, str]], document_ai_call, model_call, map_pages=None) -> PipelineResult:
    """``reads``: the Document AI inputs (PDF chunks or the one image); ``render(n)``: page n's image.

    ``map_pages(fn, page_numbers)``（可选，与发票引擎同一约定：按页码顺序交回每页结果，任一页出错等其余页结束后抛页码最小的
    那个）：各页的**第一遍抽取**同时做。之后的自核、再读、结转累计照旧按页码顺序一页一页来（再读名额先到先得、税报按前面
    各页的累计核），所以发出去的请求、每一步的判断与一页一页读完全相同；只是第一遍不再排队（2026-10-01 压测：12 页的银行
    账单在 Cloud Run 上一页一页读要 4–5 分钟，三租户时对账识别卡在每分钟约 10 份）。区别只在出错时：一页一页读遇到整份读不出
    就停，同时读时其余页的第一遍已经发出。"""
    if category not in ("bank", "gl", "vat") or page_count < 1:
        raise ContentError("ocr.enterprise_category_invalid", detail=str(category))
    started = time.monotonic()
    payloads, billed, read_ms = [], 0, []
    for index, (content, mime, expected) in enumerate(reads, 1):
        body, elapsed_read, _key = document_ai_call(content, mime, expected_pages=expected, page_number=index)
        read_ms.append(elapsed_read)
        payloads.append(body)
        billed += expected
    cost = billed * enterprise_pages.USD_PER_PAGE * THB_PER_USD
    pages = None
    if category in ("bank", "gl"):
        local = _local(payloads, category, page_count)
        if local is not None:
            pages = _local_pages(local, category)
    if pages is None:
        transcripts = [page.text for payload in payloads for page in enterprise_pages.page_texts(payload)]
        if len(transcripts) != page_count:
            raise ContentError("ocr.enterprise_transcript_count_mismatch")
        pages = []
        unread = 0
        rereads = 0
        running = {}  # 税报：本页之前各页的累计（结转、全表总计按累计核）
        numbers = range(1, len(transcripts) + 1)
        together = map_pages is not None and len(transcripts) > 1
        if together:
            render = _one_at_a_time(render)  # PDF 渲染库不是线程安全的（与发票引擎的 _SerialSource 一样）
        first = _first_reading(model_call, render, category, transcripts, keep_image=not together)
        firsts = map_pages(first, numbers) if together else None
        for number, transcript in enumerate(transcripts, 1):
            reading = firsts[number - 1] if together else first(number)
            if reading is None:
                # 这一页的抽取重发后仍结果不明（模型读超时）：只丢这一页，其余页照常（2026-09-30 压测第 23 轮
                # BAY-CA-03 / CamScanner 12 页：一页 180 秒读超时，整份作废、整套对账失败）。银行的余额链会在这里断开，
                # 交人确认；这一页记在页上，结果里点名。
                unread += 1
                page = make_page({"entries": []}, category, number, issues=["page_unreadable"])
                page.extraction_audit["page_unreadable"] = True
                pages.append(page)
                continue
            data, response, picture = reading
            data, response, checked, extra = _checked(model_call, picture, category, transcript,
                                                      number, data, response, running, rereads < MAX_REREAD_PAGES)
            del picture, reading  # one rendered page at a time, not the whole scan in memory
            rereads += bool(checked and checked["reread"])
            cost += extra
            document = data
            for row in document.get("entries") or []:
                row["page"] = str(number)
            # Per-page arithmetic alone is not evidence of full-file correctness.
            issues = ["schema_transcription_requires_document_validation"]
            if not document.get("entries"):
                issues.append("no_transaction_rows")
            page = make_page(document, category, number, issues=issues)
            if not document.get("entries") and NO_DATA_RE.search(transcript or ""):
                # 原文明确印着“无流水”（SCB「No Data Available」、BBL「ไม่พบรายการในช่วงเวลาที่ค้นหา」）：对账据此
                # 按 0 行完成，而不是报读不出（2026-09-30 压测 GIZE SCB-FIXED / BBL-SA-05）。
                page.extraction_audit["no_data_printed"] = True
            if checked:
                page.extraction_audit["self_check"] = checked
                if category == "vat":
                    for field, value in self_check.vat_sums(data).items():
                        running[field] = running.get(field, 0) + value
            page.layer2_model = response.model
            page.layer2_ms = response.elapsed_ms
            page.layer2_input_tokens = response.input_tokens
            page.layer2_output_tokens = response.output_tokens
            cost += model_cost_thb(response.model, response.input_tokens, response.output_tokens)
            pages.append(page)
        if unread == len(pages):
            raise ContentError("ocr.enterprise_schema_failed", detail="every page unreadable")
        if category in ("bank", "gl"):
            aggregate = {
                "opening_balance": pages[0].document.opening_balance,
                "closing_balance": pages[-1].document.closing_balance,
                "entries": [
                    {**row.model_dump(), "page": str(page.page_number)}
                    for page in pages
                    for row in page.document.entries
                ],
            }
            for page in pages:
                page.validation_warnings.extend(check_document(aggregate, category, page_count))
    elapsed = int((time.monotonic() - started) * 1000)
    return PipelineResult(pages=pages, page_count=page_count, elapsed_ms=elapsed, engine=ENGINE,
        estimated_cost_thb=cost, read_ms=read_ms)


# 调用方（对账的识别任务）把「这一页的抽取重发后仍结果不明」换成这个码交进来，流程只丢这一页。
UNREAD_PAGE = "ocr.provider_outcome_unknown"

NO_DATA_RE = re.compile(r"no\s+data\s+available|no\s+transactions?\s+(?:found|available)|no\s+records?\s+found"
                        r"|ไม่พบรายการ|ไม่มีรายการ(?:เคลื่อนไหว|เดินบัญชี)?", re.IGNORECASE)


def to_legacy(result: PipelineResult) -> dict:
    """The deployed ``pipeline_result_to_legacy_dict`` essentials the reconciliation decoders read."""
    return {
        "pages": [{"page_number": page.page_number,
                   "document": page.document.model_dump(mode="json") if page.document is not None else {},
                   "document_type": page.document_type,
                   "validation_warnings": list(page.validation_warnings),
                   "needs_manual_review": bool(page.needs_manual_review),
                   "extraction_audit": deepcopy(page.extraction_audit)} for page in result.pages],
        "_needs_review": any(page.needs_manual_review for page in result.pages),
        "engine": result.engine,
        "page_count": result.page_count,
        "estimated_cost_thb": result.estimated_cost_thb,
        "elapsed_ms": result.elapsed_ms,
        # Provider timings for tuning the time limits (2026-09-29): Document AI per read, extraction per page.
        "timings": {"read_ms": list(result.read_ms),
                    "extract_ms": [page.layer2_ms for page in result.pages if getattr(page, "layer2_ms", None)]},
    }
