"""ERP 采购 / 销售识别的调用策略：照旧线上发票识别的实际运行配置。

旧线上（`pearnly-app@ec2e5c95`，运行配置取证见 docs/work/cowork-backend/ocr-runtime-policy.md）：
发票为 economy 档，先用 `gemini-3.1-flash-lite` 直接读图，思考等级 MINIMAL；读出的结果过不了
确定性硬闸，才升到 `gemini-3.8-flash`（思考等级 LOW）重读（旧 `vertex.py:118-132`、
`direct_read.py:429-445`）。旧线上硬闸不过是改走 Vision 转写路线，但那条路线在线上缺凭据
不可用（同一份取证记录），所以这里直接升级到旧线上 L3 用的那个模型。

ERP 显式用这套，不跟站点的 `pearnly_ocr_mode`（DMS 的证件识别用企业档，与发票无关）。
"""
import frappe

from pearnly_erp_portal.recognition import checks

MODE = "economy"


def models():
    from pearnly_core import ocr

    table = ocr.MODE_MODEL_MAPS[MODE]
    return table["flash_lite"], table["fallback"]


TEXT_NOTE = ("The document is given below as the text layer extracted from its PDF instead of an image; "
             "read it the same way.\n\n")


def read(prompt, images, *, structured, review, label):
    """读一次；硬闸不过就用升级模型再读一次。

    PDF 自带文字层时首读只传文字（`inputs.text_path`，旧线上文字快路）；升级那次一律读原件。
    `structured(raw)` 把模型原始输出整理成结果（不合格返回 None）；`review(result)` 给出检查
    代码。返回 (结果, 检查代码, 用的模型)；两次都读不出结构返回 (None, [], 模型)。
    """
    from pearnly_core import ocr

    from pearnly_erp_portal.recognition import inputs

    primary, fallback = models()
    text = inputs.text_path(images)
    best = None
    for model in (primary, fallback):
        if text is not None and model == primary:
            raw = ocr.json_from_images(prompt + "\n" + TEXT_NOTE + text, [], model=model,
                                       thinking=ocr.thinking_for(model))
        else:
            raw = ocr.json_from_images(prompt, images, model=model, thinking=ocr.thinking_for(model))
        result = structured(raw) if isinstance(raw, dict) else None
        if result is None:
            frappe.logger("pearnly.ocr").info(f"{label}: {model} 没读出结构")
            continue
        issues = review(result)
        current = (result, issues, model)
        # 升级后仍不过：留硬闸问题少的那次（相同则留升级模型的）。
        if best is None or len(checks.hard(issues)) <= len(checks.hard(best[1])):
            best = current
        if not checks.hard(issues):
            break
        frappe.logger("pearnly.ocr").info(f"{label}: {model} 硬闸 {issues}，升级重读")
    return best or (None, [], fallback)


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
    for index, unit in enumerate(units, 1):
        result, issues, model = read(prompt, unit, structured=structured, review=page_review,
                                     label=f"{label} page {index}" if pages else label)
        if result is None:
            continue
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
    return [(result, review(result), model) for result, model in documents] or (
        [(found[0][0], [], found[0][1])] if found else [])


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
            continue
        merged.append((result, model))
    return merged
