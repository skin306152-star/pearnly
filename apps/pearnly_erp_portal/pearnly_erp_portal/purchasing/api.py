"""采购域接口层。业务与权限在 services，这里只做参数解析。"""

import frappe

from pearnly_erp_portal.purchasing import service, settings, suppliers


def _payload(value):
    return frappe.parse_json(value) if isinstance(value, str) else (value or {})


@frappe.whitelist(methods=["POST"])
def api_post_purchase(payload, request_id, name=None, amended_from=None):
    from pearnly_erp_portal.purchasing import entry

    return entry.post(_payload(payload), request_id=request_id, name=name, amended_from=amended_from)


@frappe.whitelist()
def api_purchase_records(params=None, start=0, limit=20, sort_by=None, sort_order=None):
    from pearnly_erp_portal.purchasing import records

    return {"items": records.rows(_payload(params), start=start, limit=limit,
                                  sort_by=sort_by, sort_order=sort_order),
            **records.overview(_payload(params))}


@frappe.whitelist()
def api_purchase_document(name):
    from pearnly_erp_portal.purchasing import records

    return records.get(name)


@frappe.whitelist()
def api_purchase_defaults(doc_date=None):
    from pearnly_erp_portal.access import guard
    from pearnly_erp_portal.numbering import service as numbering
    from pearnly_erp_portal.purchasing import records
    from pearnly_core import ocr

    guard.require("purchase.read")
    return {"system_no": numbering.preview(direction="purchase", on=doc_date),
            "is_paid": 1, "update_stock": 1, "auto_create_product": 1,
            "categories": records.categories(), "settings": settings.read_settings(),
            "capabilities": {"ocr": bool(ocr.is_configured() and guard.has("purchase.write"))}}


@frappe.whitelist(methods=["POST"])
def api_purchase_amounts(payload):
    from pearnly_erp_portal.purchasing import entry

    return entry.preview(_payload(payload))


@frappe.whitelist(methods=["POST"])
def api_cancel_purchase(name, reason=None):
    from pearnly_erp_portal.purchasing import entry

    return entry.cancel(name, reason)


@frappe.whitelist(methods=["POST"])
def api_purchase_payment(name, payload):
    from pearnly_erp_portal.purchasing import entry

    return entry.payment(name, **_payload(payload))


@frappe.whitelist(methods=["POST"])
def api_delete_purchase_drafts(names):
    from pearnly_erp_portal.purchasing import entry

    return entry.delete_drafts(frappe.parse_json(names))


@frappe.whitelist(methods=["POST"])
def api_purchase_attachments(name, attachments):
    from pearnly_erp_portal.purchasing import files

    return files.add(name, frappe.parse_json(attachments))


@frappe.whitelist(methods=["POST"])
def api_remove_purchase_attachment(name, file):
    from pearnly_erp_portal.purchasing import files

    return files.remove(name, file)


@frappe.whitelist(methods=["POST"])
def api_purchase_category(category_name, parent_category=None):
    from pearnly_erp_portal.purchasing import records

    return records.category(category_name, parent_category)


@frappe.whitelist()
def api_purchase_export(params=None):
    """小的当场回文件，大的排后台、做完推送下载（`pearnly_core.exports`）。"""
    from pearnly_core.exports import service as exports

    return exports.deliver(count="pearnly_erp_portal.purchasing.output.export_size",
                           build="pearnly_erp_portal.purchasing.output.workbook", args={"params": _payload(params)},
                           filename="purchase.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@frappe.whitelist(methods=["POST"])
def api_purchase_voucher(name, kind):
    from pearnly_erp_portal.purchasing import output

    return output.voucher(name, kind)


@frappe.whitelist(methods=["POST"])
def api_recognise_purchase(attachments=None, document_name=None, progress_key=None):
    """排到后台读（`recognition.background`），页面按 ``progress_key`` 问 `api_recognise_progress` 拿进度与结果。"""
    from pearnly_erp_portal.access import guard
    from pearnly_erp_portal.masters import company
    from pearnly_erp_portal.recognition import background

    guard.require("purchase.write")
    return background.start("purchase", progress_key, attachments=frappe.parse_json(attachments),
                            document_name=document_name, company=company.active_company())


@frappe.whitelist()
def api_recognise_progress(progress_key=None):
    """本人发起的那次识别：``{state, done, total, result | message}``（`recognition.progress.read`），还没发起是空。"""
    from pearnly_erp_portal.recognition import progress

    return progress.read(progress_key)


@frappe.whitelist()
def api_purchase_file_page(file, page=1):
    from pearnly_erp_portal.purchasing import preview

    return preview.page_image(file, page)


def _clean(kwargs):
    kwargs.pop("cmd", None)
    return kwargs


def _json_args(kwargs, *names):
    for name in names:
        if isinstance(kwargs.get(name), str):
            kwargs[name] = frappe.parse_json(kwargs[name])
    return kwargs


# ── 采购记录 ────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def api_create_purchase(**kwargs):
    _json_args(kwargs, "lines")
    return service.create_purchase(**_clean(kwargs))


@frappe.whitelist()
def api_list_purchases(**kwargs):
    return service.list_purchases(**_clean(kwargs))


@frappe.whitelist()
def api_purchase_total(**kwargs):
    return service.purchase_total(**_clean(kwargs))


@frappe.whitelist()
def api_get_purchase(**kwargs):
    kwargs = _clean(kwargs)
    return service.get_purchase(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_update_purchase(**kwargs):
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    name = kwargs.pop("name", None)
    return service.update_purchase(name, **kwargs)


@frappe.whitelist(methods=["POST"])
def api_submit_purchase(**kwargs):
    kwargs = _clean(kwargs)
    return service.set_status(kwargs.pop("name", None), "submitted",
                              receive_stock=kwargs.get("receive_stock"))


@frappe.whitelist(methods=["POST"])
def api_void_purchase(**kwargs):
    kwargs = _clean(kwargs)
    return service.set_status(kwargs.pop("name", None), "void", reason=kwargs.get("reason"))


@frappe.whitelist(methods=["POST"])
def api_pay_purchase(**kwargs):
    kwargs = _clean(kwargs)
    return service.pay(kwargs.pop("name", None), **kwargs)


@frappe.whitelist(methods=["POST"])
def api_set_payment_status(**kwargs):
    kwargs = _clean(kwargs)
    return service.set_payment_status(kwargs.pop("name", None), kwargs.get("status"))


@frappe.whitelist()
def api_summary(**kwargs):
    return service.summary(**_clean(kwargs))


# ── 供应商 ──────────────────────────────────────────────────────────────────


@frappe.whitelist()
def api_list_suppliers(**kwargs):
    return suppliers.list_suppliers(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_create_supplier(**kwargs):
    return suppliers.create_supplier(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_update_supplier(**kwargs):
    kwargs = _clean(kwargs)
    return suppliers.update_supplier(kwargs.pop("name", None), **kwargs)


# ── 采购设置 ────────────────────────────────────────────────────────────────


@frappe.whitelist()
def api_get_settings(**kwargs):
    return settings.get_settings()


@frappe.whitelist(methods=["POST"])
def api_update_settings(**kwargs):
    return settings.update_settings(**_clean(kwargs))
