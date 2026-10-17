"""Original-file upload/download boundaries, without product UI or OCR execution."""
import frappe
from frappe import _
from frappe.core.doctype.access_log.access_log import make_access_log
from frappe.core.api.file import zip_files as native_zip_files
from pearnly_cowork.access import scope
from pearnly_cowork.files import access, service
from pearnly_core.originals import checks as originals


@frappe.whitelist(methods=["POST"])
def upload(workspace=None, request_key=None, client=None):
    # 界面上不出现账套（Cowork 不记账、只留记录），所以归属由服务端解析；
    # 内部调用方仍可以显式传一份（lab 脚本、批处理走的就是那条）。
    # 参数顺序保持原样（有调用方按位置传），只是让它变成可选。
    scope.require("intake")
    workspace = workspace or scope.default_workspace()
    scope.require_scope(workspace, client)
    incoming = frappe.request.files.get("file") if frappe.request else None
    if not incoming:
        frappe.throw(_("Select a file to upload."))
    content = incoming.stream.read(service.MAX_BYTES + 1)
    for attempt in range(3):
        try:
            return service.create(workspace, incoming.filename, content, request_key, client)
        except (frappe.QueryDeadlockError, frappe.DuplicateEntryError):
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist(methods=["POST"])
def begin_upload(filename=None, byte_size=None, request_key=None, workspace=None, client=None):
    """浏览器直传第一步（ADR-016）：``{"direct": 0}`` 照旧走 :func:`upload`；``{"direct": 1, "url", "headers"}``
    把原件 PUT 到这个地址（头原样带上）；``{"direct": 1, "done": …}`` 这个键已经登记过。"""
    scope.require("intake")
    workspace = workspace or scope.default_workspace()
    return service.begin_direct(workspace, filename, byte_size, request_key, client)


@frappe.whitelist(methods=["POST"])
def finish_upload(filename=None, byte_size=None, request_key=None, workspace=None, client=None):
    """浏览器直传第二步：原件已 PUT 到原件桶，核对后登记；回复与 :func:`upload` 相同，或 ``{"retry": 秒}``。"""
    scope.require("intake")
    workspace = workspace or scope.default_workspace()
    for attempt in range(3):
        try:
            return service.finish_direct(workspace, filename, byte_size, request_key, client)
        except (frappe.QueryDeadlockError, frappe.DuplicateEntryError):
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist()
def metadata(name):
    return service.project(service.get_source(name))


@frappe.whitelist()
def original(name):
    link = service.original_link(name)
    if link:
        # 原件在原件桶里：记访问日志后转到短时签名下载地址（5 分钟、只这一版本、作为附件）。
        make_access_log(doctype="Cowork Source File", document=name, file_type="original")
        frappe.local.response.update(type="redirect", location=link[1])
        frappe.local.cowork_private_response = True
        return
    filename, data = service.original(name)
    make_access_log(doctype="Cowork Source File", document=name, file_type="original")
    frappe.local.response.update(type="download", filename=filename, filecontent=data,
                                  display_content_as="attachment", content_type="application/octet-stream")
    frappe.local.cowork_private_response = True


@frappe.whitelist()
def page(history_name, physical_page):
    source, number = service.page_source(history_name, physical_page)
    link = service.preview_link(source, number)
    if link:
        # 识别时已把这一页的预览图存进原件桶：记访问日志后转到短时签名地址（同 ``original``），不占服务器现画。
        make_access_log(doctype="Cowork OCR History", document=history_name, file_type="page_png")
        frappe.local.response.update(type="redirect", location=link)
        frappe.local.cowork_private_response = True
        return
    data = service.render_page(source, number)
    make_access_log(doctype="Cowork OCR History", document=history_name, file_type="page_png")
    kind = originals.preview_type(data)
    frappe.local.response.update(type="download", filename="invoice-page." + kind.rsplit("/", 1)[1].replace("jpeg", "jpg"),
                                  filecontent=data, display_content_as="inline", content_type=kind)
    frappe.local.cowork_private_response = True


def protect_response(response):
    if getattr(frappe.local, "cowork_private_response", False):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"


@frappe.whitelist()
def guarded_zip_files(files):
    # The native ZIP builder opens its ZipFile before get_content; denying
    # there leaves an unclosed archive on Python 3.14. Preflight at the public
    # boundary, then retain native behavior for ordinary files. get_content
    # remains guarded for other server-side callers.
    names = frappe.parse_json(files)
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        frappe.throw(_("Select valid files to download."))
    for name in names:
        if access.is_protected(frappe.get_doc("File", name)):
            frappe.throw(_("Use the Cowork original-file service to access this file."), frappe.PermissionError)
    return native_zip_files(files)
