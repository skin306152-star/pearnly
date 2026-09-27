"""ERP 采购 / 销售识别的读法（ADR-013）：便宜模型读，代码兜底，只在值得时再读，默认不用 3.8。

语料实测（2026-09-27，194 张金标：真实小票、退化照片、真实采购税票、合成压力集，
`scripts/ocr-policy-bench.py` / `ocr-policy-report.py`）：

| 读法 | 金额+VAT+进项全对 | 错了没标红 | 每张原价 | 平均 / P95 |
| --- | --- | --- | --- | --- |
| 旧：3.1 → 过不了硬闸整张升 3.8 | 170 | 8 | ฿0.176 | 7.4 / 15.3 秒 |
| 新：3.1 → 代码修复 → 3.1 带提示复核 → OCR 校对 | 171 | 5 | ฿0.089 | 5.7 / 9.4 秒 |

3.8 只在两张真实采购税票上胜过 3.1，而那两张用代码（反推整单折扣、OCR 原文确认本公司）也能修好；
退化照片、压力集上它没有提升，「错了没标红」反而更多。所以：

1. `gemini-3.1-flash-lite` 思考 MINIMAL 读（PDF 自带文字层时首读只传文字，旧线上文字快路）；
2. 调用方的 `structured` 做确定性整理与修复（折扣、预扣、不可抵扣 VAT、票种、反推整单折扣…）；
3. 只有「金额 / 结构对不上」（`checks.reread`）才让同一个便宜模型带着第一次的读数和没过的检查、
   思考调高一档再看原件核对一次；还不过、且是单张图片时，调一次 Vision 原文，「原图 + 原文」再读一次
   （18 张对比实测：补上了看图读不对的明细密集税票）；
4. 高金额票据可按站点配置 `pearnly_erp_ocr_escalation = {"model": "gemini-3.8-flash", "min_total": 50000}`
   再升一次，**默认没有这项、不升级**（用户 2026-09-27：保留开关、默认关）；
5. 还不过就标红交人工。税号校验位、本公司是否为买方由采购识别用 OCR 原文另做核对（`purchasing.recognition`）。
"""
import json

import frappe

from pearnly_erp_portal.recognition import checks

PRIMARY = "gemini-3.1-flash-lite"
# 单次输出上限：正常票据 400–1,100 个 token，60 行明细也在 4,000 以内。严重退化的照片会让模型重复输出、
# 一直写到上限（语料实测：两次各写满 16k token，共 ฿1.6、80 秒才判为读不出），上限减半把最坏成本减半。
MAX_OUTPUT_TOKENS = 8192
RECHECK_THINKING = {"thinkingLevel": "LOW"}


class UnsupportedDocument(frappe.ValidationError):
    """读出来了、但不能按本单录入（不是单据、贷项通知单）：给本人看的话，LINE 与网页都原样转达。"""


def escalation():
    """站点开关：{"model": 模型名, "min_total": 票面合计下限}；没配、配错都当关。"""
    try:
        value = frappe.conf.get("pearnly_erp_ocr_escalation")
    except (AttributeError, RuntimeError):  # 没有站点上下文（离线检查）
        return None
    if not isinstance(value, dict) or not str(value.get("model") or "").strip():
        return None
    return str(value["model"]).strip(), checks.money(value.get("min_total")) or 0


TEXT_NOTE = ("The document is given below as the text layer extracted from its PDF instead of an image; "
             "read it the same way.\n\n")
OCR_NOTE = ("\n\nAn OCR transcript of this image follows. Use the image for the layout and the transcript to read "
            "every digit exactly.\n\n")
KEEP_FOR_RECHECK = ("doc_no", "doc_date", "doc_date_raw", "supplier_tax_id", "buyer_tax_id", "seller_tax_id",
                    "discount_total", "wht_rate", "wht_amount", "printed", "lines")


def recheck_prompt(prompt, raw, issues):
    """带提示复核：把第一次的读数和没过的检查告诉模型，请它看原件改读错的、保留读对的。"""
    if not isinstance(raw, dict):
        return prompt
    keep = {key: raw.get(key) for key in KEEP_FOR_RECHECK if key in raw}
    reasons = "; ".join(checks.MESSAGES.get(code, code) for code in issues)
    return (prompt + "\n\nA first reading of this document returned:\n" + json.dumps(keep, ensure_ascii=False, default=str)
            + f"\nIt failed these checks: {reasons}.\nLook at the document again carefully, especially the parts "
              "these checks concern (amounts at the foot, line amounts, discounts, tax IDs, dates), correct what was "
              "misread and return the complete JSON in the same format. Keep values that were read correctly.")


def _outcome(raw, structured, review, model):
    result = structured(raw) if isinstance(raw, dict) else None
    return None if result is None else (result, review(result), model, raw)


def _better(current, candidate):
    """硬闸问题更少的那次；一样多保留先读的那次（复核没有改善就不换）。"""
    if candidate is None:
        return current
    if current is None or len(checks.hard(candidate[1])) < len(checks.hard(current[1])):
        return candidate
    return current


def _labels(label, stage):
    return {"product": "erp", "task": label.split()[0], "stage": stage}


def read(prompt, images, *, structured, review, label):
    """读一张（或一页）：返回 (结果, 检查代码, 用的模型)；读不出结构返回 (None, [], 模型)。"""
    from pearnly_core import ocr

    from pearnly_erp_portal.recognition import inputs

    text = inputs.text_path(images)
    if text is not None:
        raw = ocr.json_from_images(prompt + "\n" + TEXT_NOTE + text, [], model=PRIMARY, max_tokens=MAX_OUTPUT_TOKENS,
                                   thinking=ocr.thinking_for(PRIMARY), request_labels=_labels(label, "read"))
    else:
        raw = ocr.json_from_images(prompt, images, model=PRIMARY, thinking=ocr.thinking_for(PRIMARY),
                                   max_tokens=MAX_OUTPUT_TOKENS, request_labels=_labels(label, "read"))
    best = _outcome(raw, structured, review, PRIMARY)
    image = len(images) == 1 and images[0][1].startswith("image/")
    # 首读读不出结构（多半是输出失控写满上限）时，照原样看图复核会同样失控：图片直接走下面的「原图 + 原文」。
    if (best is not None and checks.reread(best[1])) or (best is None and not image):
        frappe.logger("pearnly.ocr").info(f"{label}: {PRIMARY} {best and best[1]}，带提示复核")
        raw = ocr.json_from_images(recheck_prompt(prompt, best and best[3], best[1] if best else []), images,
                                   model=PRIMARY, thinking=RECHECK_THINKING, max_tokens=MAX_OUTPUT_TOKENS,
                                   request_labels=_labels(label, "recheck"))
        best = _better(best, _outcome(raw, structured, review, PRIMARY))
    if (best is None or checks.reread(best[1])) and image:
        # 复核后仍对不上：调一次 OCR 原文，原图 + 原文一起再读（语料实测：明细密集的税票，如 NBC 逐行折扣、
        # Giffarine 整单折扣，看图读不对，给了原文就对；Vision 原文同一次识别里只取一次）。
        text = ocr.text_from_image(*images[0])
        if text:
            frappe.logger("pearnly.ocr").info(f"{label}: 复核仍不过，按 OCR 原文再读")
            raw = ocr.json_from_images(prompt + OCR_NOTE + text, images, model=PRIMARY, thinking=RECHECK_THINKING,
                                       max_tokens=MAX_OUTPUT_TOKENS, request_labels=_labels(label, "ocr-read"))
            best = _better(best, _outcome(raw, structured, review, PRIMARY))
    upgrade = escalation()
    if upgrade and (best is None or checks.reread(best[1])):
        total = checks.money(((best[0] if best else {}) or {}).get("printed", {}).get("total")) or 0
        if best is None or total >= upgrade[1]:
            frappe.logger("pearnly.ocr").info(f"{label}: 站点开启升级，{upgrade[0]} 重读")
            raw = ocr.json_from_images(prompt, images, model=upgrade[0], thinking=ocr.thinking_for(upgrade[0]),
                                       max_tokens=MAX_OUTPUT_TOKENS, request_labels=_labels(label, "upgrade"))
            best = _better(best, _outcome(raw, structured, review, upgrade[0]))
    return best[:3] if best else (None, [], PRIMARY)


def describe(codes):
    from frappe import _

    return [_(checks.MESSAGES[code]) for code in codes if code in checks.MESSAGES]


def for_web(call):
    """网页端「上传识别」是同步请求：限流 / 容量不足 / 结果不确定给人话，不把内部异常抛到页面上。
    只给网页入口用 —— LINE 收单在队列里跑，`Deferred` 要原样抛出去让队列按时重试。"""
    from frappe import _
    from pearnly_core.execution.errors import Deferred, QueueFull, Uncertain

    try:
        return call()
    except (Deferred, QueueFull) as exc:
        if getattr(exc, "reason", "") == "billing_unavailable":
            frappe.throw(_("The balance could not be checked right now. Please try again in a minute."))
        frappe.throw(_("Recognition is busy right now. Please try again in a minute."))
    except Uncertain:
        frappe.throw(_("The recognition service did not answer in time. Please try again."))


def read_all(prompt, images, *, structured, review, label, fields, finish=None):
    """一个文件里的**全部**单据：[(结果, 检查代码, 模型), …]，第一张在前。

    照旧线上（`direct_read.py` 按页直读、`layer2_prompts.py` 第 9 条 `additional_invoices`、
    `invoice_grouper.py` 按票号归组）：
    - 多页 PDF 拆成单页逐页读（不并发）。某页被限流时整个任务按队列退避推迟；已读完的页有断点，
      重跑时直接复用、不重复付费，也不整份作废（旧线上遗留的「一页失败整份作废」）。
    - 一页 / 一张图里有几张单：模型把第二张起放在 `additional_documents`，各算一张。
    - 相邻几页单号相同（或后一页没有单号）归成一张：明细接起来，票面合计取后面印了的那页。
    续页上没有合计是正常的，所以逐页读时「合计缺失」不触发升级；归组后整张再检查一次。
    """
    from pearnly_erp_portal.recognition import inputs

    pages = inputs.pdf_pages(images[0][0]) if len(images) == 1 and images[0][1] == "application/pdf" else []
    units = [[(page, "application/pdf")] for page in pages] or [images]
    page_review = (lambda result: [code for code in review(result) if code != "total_missing"]) if pages else review
    found = []
    unread = []  # 还没有前一张可挂的读不出页（第 1 页起连着读不出），挂到下一张读出的单据上
    for index, unit in enumerate(units, 1):
        result, issues, model = read(prompt, unit, structured=structured, review=page_review,
                                     label=f"{label} page {index}" if pages else label)
        if result is None:
            # 这一页几次都读不出（多半是输出失控写满上限）：不再静默跳过——那页上的单据会悄悄漏掉。
            # 记在它前一页读出的单据上（续页多半属于它），归组后那张单据标红「有页没读出」。
            if found:
                found[-1][0].setdefault("unread_pages", []).append(index)
            else:
                unread.append(index)
            continue
        if unread:
            result["unread_pages"] = unread + list(result.get("unread_pages") or [])
            unread = []
        extras = [structured(raw) for raw in result.pop("additional_documents", None) or [] if isinstance(raw, dict)]
        for extra in extras:
            if extra:
                extra.pop("additional_documents", None)
        found.append((result, model))
        found.extend((extra, model) for extra in extras if extra)
    documents = [row for row in found if not row[0].get("not_document")]
    if pages:
        documents = _group(documents, fields)
    if finish:
        for result, _model in documents:
            finish(result)
    return [(result, review(result) + _unread(result), model) for result, model in documents] or (
        [(found[0][0], _unread(found[0][0]), found[0][1])] if found else [])


def _unread(result):
    return [checks.PAGE_UNREAD] if result.get("unread_pages") else []


def _group(documents, fields):
    merged = []
    for result, model in documents:
        previous = merged[-1][0] if merged else None
        number = str(result.get("doc_no") or "").strip()
        if previous is not None and (not number or number == str(previous.get("doc_no") or "").strip()):
            previous["lines"] = (previous.get("lines") or []) + (result.get("lines") or [])
            for key in fields:
                if previous.get(key) in (None, "") and result.get(key) not in (None, ""):
                    previous[key] = result[key]
            printed = result.get("printed") or {}
            if any(printed.get(key) not in (None, "") for key in ("total", "vat", "subtotal")):
                previous["printed"] = printed
            if result.get("unread_pages"):
                previous["unread_pages"] = list(previous.get("unread_pages") or []) + result["unread_pages"]
            continue
        merged.append((result, model))
    return merged
