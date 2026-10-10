"""对话里的文件（ADR-019）：附件进会话、结果文件列出与下载。

- 附件：浏览器传到本站 → 校验类型与大小（HEIC 照片先转 JPEG，与三个产品的上传口一致）→ 交给引擎放进该会话，
  智能体按返回的位置读取。
- 结果：智能体产出的文件按会话列出，可预览、下载。
- 文件不脱离会话存在：下载前先核对「会话属于当前用户」且「文件属于这个会话」，猜编号读不到别人的文件。
"""
import frappe
from frappe import _

from pearnly_core.assistant import contracts, engine, sessions
from pearnly_core.desk import heic

# 只有这些类型可以在本站内联打开。网页、SVG 一律按下载给出：它们在本站域名下打开会执行脚本；
# 对话页的预览把网页放进无同源权限的沙箱 iframe 里显示（前端读文本后用 srcdoc）。
INLINE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"})


def upload(session_id) -> dict:
    row = sessions.owned(session_id)
    sent = (frappe.request.files or {}).get("file") if frappe.request else None
    if not sent:
        frappe.throw(_("No file was received."))
    content = sent.stream.read(contracts.UPLOAD_MAX_BYTES + 1)
    name = sessions._guard(contracts.upload_name, sent.filename, len(content))
    mime = sent.mimetype or "application/octet-stream"
    if heic.is_heic(content):
        content, name, mime = heic.to_jpeg(content), heic.jpeg_name(name), "image/jpeg"
    api = engine.adapter()
    stored = api.attach(sessions.segment_for_turn(row, api), name, content, mime)
    return {"fileId": stored["file_id"], "filename": name, "mountPath": stored["path"], "size_bytes": len(content)}


def listing(session_id) -> list[dict]:
    """本会话的全部文件：挂进去的附件（只作输入）+ 智能体产出的结果（可下载）。"""
    row = sessions.owned(session_id, allow_archived=True)
    api = engine.adapter()
    return [f for remote in sessions.segments(row) for f in api.files(remote)]


def _output(row, api, fid) -> tuple[dict, str]:
    """本对话（任一段）里可下载的结果文件及其所在的远端会话；不是就当作不存在。"""
    for remote in sessions.segments(row):
        for f in api.files(remote):
            if f["id"] == fid and f["downloadable"]:
                return f, remote
    frappe.throw(_("File not found"), frappe.DoesNotExistError)


def download(session_id, file_id, inline=False) -> None:
    row = sessions.owned(session_id, allow_archived=True)
    fid = sessions._guard(contracts.file_id, file_id)
    api = engine.adapter()
    meta, remote = _output(row, api, fid)
    frappe.local.response.filename = meta["filename"]
    frappe.local.response.filecontent = api.read_file(remote, fid)
    frappe.local.response.type = "binary" if inline and meta["mime_type"] in INLINE_TYPES else "download"


# 结果面板的表格预览：只读前几张表的前若干行列，单个文件超过上限就不预览（下载照常）。
PREVIEW_MAX_BYTES = 5 * 1024 * 1024
PREVIEW_SHEETS = 10
PREVIEW_ROWS = 500
PREVIEW_COLS = 40
SHEET_TYPES = (".xlsx", ".xlsm")


def preview(session_id, file_id) -> dict:
    """结果文件的结构化预览。目前支持 Excel：`{"type": "sheets", "sheets": [{"name", "rows"}]}`；其余 `{"type": "none"}`。"""
    row = sessions.owned(session_id, allow_archived=True)
    fid = sessions._guard(contracts.file_id, file_id)
    api = engine.adapter()
    meta, remote = _output(row, api, fid)
    if not meta["filename"].lower().endswith(SHEET_TYPES) or int(meta.get("size_bytes") or 0) > PREVIEW_MAX_BYTES:
        return {"type": "none"}
    return {"type": "sheets", "sheets": sheets(api.read_file(remote, fid))}


def sheets(content: bytes) -> list[dict]:
    import io

    from openpyxl import load_workbook

    values = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    formulas = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
    out = []
    for sheet in values.worksheets[:PREVIEW_SHEETS]:
        raw = formulas[sheet.title].iter_rows(max_row=PREVIEW_ROWS, max_col=PREVIEW_COLS)
        rows = [[_cell(v, f) for v, f in zip(vrow, frow)]
                for vrow, frow in zip(sheet.iter_rows(max_row=PREVIEW_ROWS, max_col=PREVIEW_COLS), raw)]
        while rows and not any(rows[-1]):
            rows.pop()
        width = max((max((i + 1 for i, c in enumerate(r) if c), default=0) for r in rows), default=0)
        out.append({"name": sheet.title, "rows": [r[:width] for r in rows]})
    return out


def _cell(value_cell, formula_cell) -> str:
    """单元格显示文字：按数字格式粗略排版；没有缓存值的公式显示公式本身。"""
    import datetime

    value = getattr(value_cell, "value", None)
    if value is None:
        formula = getattr(formula_cell, "value", None)
        return formula if isinstance(formula, str) and formula.startswith("=") else ""
    fmt = str(getattr(value_cell, "number_format", "") or "")
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        if "0.00" in fmt:
            return f"{value:,.2f}" if "," in fmt else f"{value:.2f}"
        if "#,##0" in fmt:
            return f"{value:,.0f}"
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M") if (value.hour or value.minute) else value.strftime("%Y-%m-%d")
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value)
