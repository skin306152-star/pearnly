"""按需请求的结果字节：站点私有目录 ``private/cc_desktop_requests/<请求号>``（多产品共用，ADR-025；做法同 Cowork
`companion.request_store`）。分块追加时先截到库里记的已收字节数再写，取走或过期即删；文件名就是请求号（32 位十六进制）。
"""
import hashlib
import os

import frappe

FOLDER = "cc_desktop_requests"


def _path(name):
    if not isinstance(name, str) or len(name) != 32 or any(ch not in "0123456789abcdef" for ch in name):
        raise ValueError("invalid request name")
    return frappe.get_site_path("private", FOLDER, name)


def write(name, offset, content):
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
    except (FileNotFoundError, ValueError):
        pass
