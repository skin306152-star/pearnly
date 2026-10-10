"""对话里的文件（ADR-019）：附件进会话、结果文件列出与下载。

- 附件：浏览器传到本站 → 校验类型与大小（HEIC 照片先转 JPEG，与三个产品的上传口一致）→ 存进 Anthropic 文件库 →
  以只读方式挂进该会话的沙箱，智能体用文件工具读取。
- 结果：智能体写到 `/mnt/session/outputs/` 的文件按会话列出，可预览、下载。
- 文件不脱离会话存在：下载前先核对「会话属于当前用户」且「文件属于这个会话」，猜编号读不到别人的文件。
"""
import frappe
from frappe import _

from pearnly_core.assistant import client as anthropic_client
from pearnly_core.assistant import contracts, sessions
from pearnly_core.desk import heic

MANAGED_AGENTS_BETA = "managed-agents-2026-04-01"
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
    api = anthropic_client.client()
    stored = api.files.upload(file=(name, content, mime))
    mount = contracts.mount_path(stored.id, name)
    api.beta.sessions.resources.add(row.session_id, type="file", file_id=stored.id, mount_path=mount)
    return {"fileId": stored.id, "filename": name, "mountPath": mount, "size_bytes": len(content)}


def listing(session_id) -> list[dict]:
    """本会话的全部文件：挂进去的附件（只作输入）+ 智能体产出的结果（可下载）。"""
    row = sessions.owned(session_id, allow_archived=True)
    return _session_files(anthropic_client.client(), row.session_id)


def _session_files(api, session_id) -> list[dict]:
    found = {}
    for f in api.beta.files.list(scope_id=session_id, betas=[MANAGED_AGENTS_BETA]):
        found[f.id] = {"id": f.id, "filename": f.filename, "size_bytes": f.size_bytes, "mime_type": f.mime_type,
                       "downloadable": bool(getattr(f, "downloadable", False)), "created_at": str(f.created_at),
                       "kind": "output"}
    for r in api.beta.sessions.resources.list(session_id):
        if getattr(r, "type", "") != "file" or r.file_id in found:
            continue
        found[r.file_id] = {"id": r.file_id, "filename": (r.mount_path or "").rsplit("/", 1)[-1] or r.file_id,
                            "size_bytes": 0, "mime_type": "application/octet-stream", "downloadable": False,
                            "created_at": str(getattr(r, "created_at", "")), "kind": "upload"}
    return list(found.values())


def download(session_id, file_id, inline=False) -> None:
    row = sessions.owned(session_id, allow_archived=True)
    fid = sessions._guard(contracts.file_id, file_id)
    api = anthropic_client.client()
    meta = next((f for f in _session_files(api, row.session_id) if f["id"] == fid), None)
    if not meta or not meta["downloadable"]:
        frappe.throw(_("File not found"), frappe.DoesNotExistError)
    frappe.local.response.filename = meta["filename"]
    frappe.local.response.filecontent = api.files.download(fid).read()
    frappe.local.response.type = "binary" if inline and meta["mime_type"] in INLINE_TYPES else "download"
