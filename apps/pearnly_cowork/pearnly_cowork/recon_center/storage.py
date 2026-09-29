"""对账上传文件的临时存放：存成本人的私有 File，任务结束即删。

与 Cowork 发票原件内容完全相同的文件（客户先录发票、月底拿同一批 PDF 做销项核查）不能照原样再存一份：原件保护按内容
哈希认文件（Frappe 改公开 / 私有时会连带改所有同哈希的 File），同内容的新 File 一律当成改原件拒绝（「Original files
cannot be changed.」，2026-09-30 压测 V02）。这种文件存成「固定前缀 + 原文件名 + 原字节」、名字一律 .bin（按扩展名走 PDF / 图片处理会把加了前缀的字节
当坏文件：第 17 轮 V02「Cannot find Root object in pdf」），内容哈希与原件不同；读取时还原文件名和原字节。
原件保护本身不动。
"""
import frappe
from frappe.core.doctype.file.utils import get_content_hash

from pearnly_cowork.files import access

COPY_PREFIX = b"PEARNLY-RECON-COPY\x00"
COPY_NAME = "recon-copy.bin"


def _is_original_copy(content):
    snapshot = frappe._dict(content_hash=get_content_hash(content))
    return bool(access.protected_sources(snapshot, include_hash=True))


def save(content, filename):
    """存一份，返回 File 名。原生 File 插入，按站点上限（默认 25MB）。"""
    if _is_original_copy(content):
        content, filename = COPY_PREFIX + filename.encode("utf-8") + b"\x00" + content, COPY_NAME
    return frappe.get_doc({"doctype": "File", "file_name": filename, "is_private": 1,
                           "content": content}).insert(ignore_permissions=True).name


def read(name):
    """``(上传时的文件名, 原字节)``。"""
    doc = frappe.get_doc("File", name)
    content = doc.get_content()
    content = content if isinstance(content, bytes) else content.encode()
    if content.startswith(COPY_PREFIX):
        name, _separator, original = content[len(COPY_PREFIX):].partition(b"\x00")
        return name.decode("utf-8"), original
    return doc.file_name or "", content
