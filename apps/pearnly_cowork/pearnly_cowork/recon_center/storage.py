"""对账上传文件的临时存放：每份一个本人的私有 File，任务结束即删（答预设问题要用的留到过期）。

站点配了原件层（ADR-024，``pearnly_core.originals.store``：选了 Claude → 甲骨文、谷歌租户 → 谷歌存储）时，原件交原件层存，
本机这个 File 只是一条不到 1KB 的登记（「固定前缀 + 原文件名 / 原件引用 / sha256 / 页数」）：扫描件、照片由存放那边按引用读
（甲骨文逐页渲染交 Claude、Cloud Run 按版本号读），删登记时连原件一起删。原件层的检查不收的（超 50 页的电子总账、加密 PDF、
坏图片）和没配原件层的站点照旧存本机——那几种本来就在这台上解析或报读不出，不交识别。

与 Cowork 发票原件内容完全相同的文件（客户先录发票、月底拿同一批 PDF 做销项核查）存本机时不能照原样再存一份：原件保护按内容
哈希认文件（Frappe 改公开 / 私有时会连带改所有同哈希的 File），同内容的新 File 一律当成改原件拒绝（「Original files
cannot be changed.」，2026-09-30 压测 V02）。这种文件存成「固定前缀 + 原文件名 + 原字节」、名字一律 .bin（按扩展名走 PDF / 图片处理会把加了前缀的字节
当坏文件：第 17 轮 V02「Cannot find Root object in pdf」），内容哈希与原件不同；读取时还原文件名和原字节。
原件保护本身不动。
"""
import json
import uuid

import frappe
from frappe import _
from frappe.core.doctype.file.utils import get_content_hash
from pearnly_core.originals import store
from pearnly_core.originals.rules import file_extension

from pearnly_cowork.files import access

COPY_PREFIX = b"PEARNLY-RECON-COPY\x00"
COPY_NAME = "recon-copy.bin"
REF_PREFIX = b"PEARNLY-RECON-ORIGINAL\x00"
REF_NAME = "recon-original.bin"


def _is_original_copy(content):
    snapshot = frappe._dict(content_hash=get_content_hash(content))
    return bool(access.protected_sources(snapshot, include_hash=True))


def _put(content, filename):
    """交原件层存：返回登记内容（dict），这个站点没配原件层或原件层不收回 ``None``。存放处一时连不上：提示稍后再试。"""
    from pearnly_core.execution.errors import Deferred

    original_id = "CWS-" + (uuid.uuid4().hex + uuid.uuid4().hex)[:48]
    try:
        stored = store.put(original_id, file_extension(filename), filename, content)
    except Deferred:
        frappe.throw(_("reconciliation.system_busy"))
    if not stored or stored["status"] != "ok":
        return None
    meta = stored["meta"]
    return {"filename": filename, "ref": stored["ref"], "sha256": meta["sha256"], "pages": int(meta["page_count"])}


def save(content, filename):
    """存一份，返回 File 名。原生 File 插入，按站点上限（默认 25MB）。"""
    entry = _put(content, filename)
    if entry is not None:
        content, filename = REF_PREFIX + json.dumps(entry).encode("utf-8"), REF_NAME
    elif _is_original_copy(content):
        content, filename = COPY_PREFIX + filename.encode("utf-8") + b"\x00" + content, COPY_NAME
    return frappe.get_doc({"doctype": "File", "file_name": filename, "is_private": 1,
                           "content": content}).insert(ignore_permissions=True).name


def _raw(name):
    doc = frappe.get_doc("File", name)
    content = doc.get_content()
    return doc, content if isinstance(content, bytes) else content.encode()


def _entry(content):
    return json.loads(content[len(REF_PREFIX):]) if content.startswith(REF_PREFIX) else None


def _registered(name):
    """存在原件层的那份的登记（dict）；存本机的回 ``None``（先看文件名，不读存本机的整份）。"""
    if frappe.db.get_value("File", name, "file_name") != REF_NAME:
        return None
    return _entry(_raw(name)[1])


def read(name):
    """``(上传时的文件名, 原字节)``。原件在原件层就从那边取回（表格、电子 PDF 在这台解析要用）。"""
    doc, content = _raw(name)
    entry = _entry(content)
    if entry is not None:
        return entry["filename"], store.read(entry["ref"])
    if content.startswith(COPY_PREFIX):
        name, _separator, original = content[len(COPY_PREFIX):].partition(b"\x00")
        return name.decode("utf-8"), original
    return doc.file_name or "", content


def stored(name):
    """原件在原件层时交识别带的引用：甲骨文 ``(对象名, sha256)``、谷歌存储 ``(桶, 对象名, 版本号, sha256)``；存本机回 ``None``。"""
    entry = _registered(name)
    found = store.parse(entry["ref"]) if entry else None
    if not found:
        return None
    place, value = found
    return value if place == store.ORACLE else (*value, entry["sha256"])


def content_hash(name):
    """这份文件内容的哈希（判断「本人后来交了同内容的对账」）：存在原件层的取原件的 sha256，否则 File 的内容哈希。"""
    entry = _registered(name)
    return entry["sha256"] if entry else frappe.db.get_value("File", name, "content_hash")


def delete(name):
    """删掉这份：原件在原件层先删那边（删不掉抛出，登记留着、按日志处理），再删本机 File。"""
    if not frappe.db.exists("File", name):
        return
    entry = _registered(name)
    if entry is not None:
        store.delete([entry["ref"]])
    frappe.delete_doc("File", name, ignore_permissions=True, force=True)
