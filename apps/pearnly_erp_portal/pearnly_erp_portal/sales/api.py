"""销售单据接口层。业务与权限在 services，这里只做参数解析。"""

import frappe

from pearnly_erp_portal.sales import contracts, service


def _clean(kwargs):
    kwargs.pop("cmd", None)
    return kwargs


def _json_args(kwargs, *names):
    for name in names:
        if isinstance(kwargs.get(name), str):
            kwargs[name] = frappe.parse_json(kwargs[name])
    return kwargs


# ── 单据 ────────────────────────────────────────────────────────────────────


@frappe.whitelist()
def api_list_sales(**kwargs):
    return service.list_documents(**_clean(kwargs))


@frappe.whitelist()
def api_get_sale_document(**kwargs):
    kwargs = _clean(kwargs)
    return service.get_document(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_create_sale_document(**kwargs):
    """手工录入：来源固定 manual。"""
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    return service.create_document(**_clean({**kwargs, "source": contracts.SOURCE_MANUAL}))


@frappe.whitelist(methods=["POST"])
def api_update_sale_document(**kwargs):
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    return service.update_document(kwargs.pop("name", None), **kwargs)


@frappe.whitelist(methods=["POST"])
def api_issue_sale_document(**kwargs):
    kwargs = _clean(kwargs)
    return service.issue_document(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_void_sale_document(**kwargs):
    kwargs = _clean(kwargs)
    return service.void_document(kwargs.get("name"), kwargs.get("reason"))


@frappe.whitelist(methods=["POST"])
def api_credit_note(**kwargs):
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    name = kwargs.pop("name", None)
    return service.create_note(name, contracts.CREDIT_NOTE, **kwargs)


@frappe.whitelist(methods=["POST"])
def api_debit_note(**kwargs):
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    name = kwargs.pop("name", None)
    return service.create_note(name, contracts.DEBIT_NOTE, **kwargs)


# ── 上传发票 ────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def api_recognise_sale_invoice(**kwargs):
    """上传发票 → 识别（**按页计费**）→ 落一张 source=ocr 的草稿。

    传 `manual=1` 表示不识别，只把文件留底后手工填（上传不等于必须花钱）。
    """
    guards = _clean(kwargs)
    uploaded = frappe.request.files.get("file") if frappe.request else None
    if uploaded:
        content = uploaded.stream.read()
        content_type = uploaded.content_type or "application/octet-stream"
        filename = uploaded.filename or "invoice"
    else:
        content = frappe.parse_json(kwargs.get("content") or "") if kwargs.get("content") else b""
        content_type = kwargs.get("content_type") or "application/octet-stream"
        filename = kwargs.get("filename") or "invoice"
    if isinstance(content, str):
        content = content.encode("utf-8")
    return service.create_from_upload(
        content=content, content_type=content_type, filename=filename,
        doc_date=guards.get("doc_date"),
        manual=str(guards.get("manual") or "") in ("1", "true", "True"))


@frappe.whitelist(methods=["POST"])
def api_attach_recognition(**kwargs):
    """复核后把识别字段填回草稿。"""
    _json_args(kwargs, "payload")
    kwargs = _clean(kwargs)
    return service.attach_recognition(kwargs.get("name"), payload=kwargs.get("payload") or {})
