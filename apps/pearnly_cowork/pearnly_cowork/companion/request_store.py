"""按需请求的结果字节：站点私有目录 ``private/cowork_companion_requests/<请求号>``，网页进程与后台进程挂同一个站点目录。

分块追加时先截到库里记的已收字节数再写（库是准的：上一块写了文件却没提交，重发同一块时覆盖掉），取走或过期即删。
文件名就是请求号（32 位十六进制），不含任何本机路径。
"""
import hashlib
import os

import frappe

FOLDER = "cowork_companion_requests"


def _path(name):
    if not isinstance(name, str) or len(name) != 32 or any(ch not in "0123456789abcdef" for ch in name):
        raise ValueError("invalid request name")
    return frappe.get_site_path("private", FOLDER, name)


def write(name, offset, content):
    """从 ``offset`` 起写这一块，返回写完后的总字节数；文件比 ``offset`` 短（中间缺了）就报错。"""
    path = _path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        if offset:
            raise ValueError("missing earlier chunks")
        with open(path, "wb") as handle:
            handle.write(content)
        return len(content)
    with open(path, "r+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() < offset:
            raise ValueError("missing earlier chunks")
        handle.truncate(offset)
        handle.seek(offset)
        handle.write(content)
    return offset + len(content)


def digest(name):
    """(字节数, sha256)；没有文件时是 (0, 空内容的摘要)。"""
    path = _path(name)
    found = hashlib.sha256()
    size = 0
    if os.path.exists(path):
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                found.update(block)
                size += len(block)
    return size, found.hexdigest()


def read(name):
    path = _path(name)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return handle.read()


def drop(name):
    try:
        os.remove(_path(name))
    except FileNotFoundError:
        pass
