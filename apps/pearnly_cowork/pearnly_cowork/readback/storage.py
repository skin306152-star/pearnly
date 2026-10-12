"""回读的文件：站点私有目录 ``private/cowork_readback/<回读编号>/``（不进 File 表、没有下载地址），只经 `service` 给发起人。
里面是四份工作簿和 ``data.json.gz``（电脑交回的读账结果 + 推送过的单对应的识别记录，供税务草稿再读）。删回读时整个目录删。"""
import gzip
import json
import os
import re
import shutil

import frappe

FOLDER = "cowork_readback"
DATA = "data.json.gz"
_NAME = re.compile(r"^[0-9a-f]{32}$")
_FILE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


def _folder(name):
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("invalid readback name")
    return frappe.get_site_path("private", FOLDER, name)


def _path(name, filename):
    if not isinstance(filename, str) or not _FILE.fullmatch(filename) or filename.startswith("."):
        raise ValueError("invalid readback file")
    return os.path.join(_folder(name), filename)


def put(name, filename, content):
    path = _path(name, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "wb") as handle:
        handle.write(content)
    os.replace(path + ".part", path)


def read(name, filename):
    path = _path(name, filename)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        return handle.read()


def put_data(name, value):
    put(name, DATA, gzip.compress(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")))


def read_data(name):
    content = read(name, DATA)
    return json.loads(gzip.decompress(content).decode("utf-8")) if content else None


def drop(name):
    shutil.rmtree(_folder(name), ignore_errors=True)
