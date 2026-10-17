"""对账上传文件的临时存放：每份一个本人的私有 File，任务结束即删（答预设问题要用的留到过期）。

站点配了原件层（ADR-024，``pearnly_core.originals.store``：选了 Claude → 甲骨文、谷歌租户 → 谷歌存储）时，原件交原件层存，
本机这个 File 只是一条不到 1KB 的登记（``pearnly_core.originals.registered``）：扫描件、照片由存放那边按引用读
（甲骨文逐页渲染交 Claude、Cloud Run 按版本号读），删登记时连原件一起删。原件层的检查不收的（超 50 页的电子总账、加密 PDF、
坏图片）和没配原件层的站点照旧存本机——那几种本来就在这台上解析或报读不出，不交识别。

与 Cowork 发票原件内容完全相同的文件（客户先录发票、月底拿同一批 PDF 做销项核查）存本机时不能照原样再存一份：原件保护按内容
哈希认文件（Frappe 改公开 / 私有时会连带改所有同哈希的 File），同内容的新 File 一律当成改原件拒绝（「Original files
cannot be changed.」，2026-09-30 压测 V02）。这种文件存成「固定前缀 + 原文件名 + 原字节」、名字一律 .bin（按扩展名走 PDF / 图片处理会把加了前缀的字节
当坏文件：第 17 轮 V02「Cannot find Root object in pdf」），内容哈希与原件不同；读取时还原文件名和原字节。
原件保护本身不动。
"""
import uuid

import frappe
from frappe import _
from frappe.core.doctype.file.utils import get_content_hash
from pearnly_core.originals import registered, store

from pearnly_cowork.files import access

COPY_PREFIX = b"PEARNLY-RECON-COPY\x00"
COPY_NAME = "recon-copy.bin"


def _is_original_copy(content):
    snapshot = frappe._dict(content_hash=get_content_hash(content))
    return bool(access.protected_sources(snapshot, include_hash=True))


def save(content, filename):
    """存一份，返回 File 名。原生 File 插入，按站点上限（默认 25MB）。"""
    from pearnly_core.execution.errors import Deferred

    try:
        stored = registered.put("CWS-" + (uuid.uuid4().hex + uuid.uuid4().hex)[:48], filename, content)
    except Deferred:
        frappe.throw(_("reconciliation.system_busy"))
    if stored is not None and not isinstance(stored, dict):
        return stored.name  # 原件在原件层，本机只有登记
    if _is_original_copy(content):
        content, filename = COPY_PREFIX + filename.encode("utf-8") + b"\x00" + content, COPY_NAME
    return frappe.get_doc({"doctype": "File", "file_name": filename, "is_private": 1,
                           "content": content}).insert(ignore_permissions=True).name


def read(name):
    """``(上传时的文件名, 原字节)``。原件在原件层就从那边取回（表格、电子 PDF 在这台解析要用）。"""
    doc = frappe.get_doc("File", name)
    found = registered.entry(doc)
    if found:
        return found["filename"], store.read(found["ref"])
    content = doc.get_content()
    content = content if isinstance(content, bytes) else content.encode()
    if content.startswith(COPY_PREFIX):
        name, _separator, original = content[len(COPY_PREFIX):].partition(b"\x00")
        return name.decode("utf-8"), original
    return doc.file_name or "", content


def stored(name):
    """原件在原件层时交识别带的引用：甲骨文 ``(对象名, sha256)``、谷歌存储 ``(桶, 对象名, 版本号, sha256)``；存本机回 ``None``。"""
    return registered.stored(name)


def content_hash(name):
    """这份文件内容的哈希（判断「本人后来交了同内容的对账」）：存在原件层的取原件的 sha256，否则 File 的内容哈希。"""
    found = registered.entry(name)
    return found["sha256"] if found else frappe.db.get_value("File", name, "content_hash")


def delete(name):
    """删掉这份；原件在原件层的，File 删掉后排任务连那边一起删（``registered.RegisteredFile``）。"""
    if frappe.db.exists("File", name):
        frappe.delete_doc("File", name, ignore_permissions=True, force=True)
