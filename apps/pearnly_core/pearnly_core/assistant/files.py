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


def download(session_id, file_id, inline=False) -> None:
    row = sessions.owned(session_id, allow_archived=True)
    fid = sessions._guard(contracts.file_id, file_id)
    api = engine.adapter()
    meta, remote = next(((f, r) for r in sessions.segments(row) for f in api.files(r) if f["id"] == fid), (None, None))
    if not meta or not meta["downloadable"]:
        frappe.throw(_("File not found"), frappe.DoesNotExistError)
    frappe.local.response.filename = meta["filename"]
    frappe.local.response.filecontent = api.read_file(remote, fid)
    frappe.local.response.type = "binary" if inline and meta["mime_type"] in INLINE_TYPES else "download"
