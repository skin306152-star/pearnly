"""经机器接口上传的原件（原件桶未开、HEIC 等不能直传的）先落在站点私有目录，受理建原件后即删。

网页进程与后台进程挂的是同一个站点目录；文件名就是文件夹条目号（64 位十六进制），不含任何本机路径。
"""
import os

import frappe

FOLDER = "cowork_folder_inbox"


def _path(name):
    if len(name) != 64 or any(ch not in "0123456789abcdef" for ch in name):
        raise ValueError("invalid inbox name")
    return frappe.get_site_path("private", FOLDER, name)


def put(name, content):
    path = _path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".part"
    with open(temporary, "wb") as handle:
        handle.write(content)
    os.replace(temporary, path)


def take(name):
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
