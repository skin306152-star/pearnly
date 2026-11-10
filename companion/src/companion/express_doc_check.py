# -*- coding: utf-8 -*-
"""按需请求 ``express_doc_check``（录过的票先核账本，用户 11-10「万一 exp 那边删了呢」）：服务端点名几张我们写过的单
（单号 + 进项 / 销项），CC 桌面版只读单头，回每张在账本里还在不在。

- 进项读 APTRN、销项读 ARTRN，按 DOCNUM 认（不分大小写、去两端空白）：
  有一行没打删除标记、``DOCSTAT`` 不是 C（作废）→ ``present``；同号的行都打了删除标记或都作废 → ``gone``；
  单头表里没有这个号 → ``gone``（Express 里删掉的单单头不留）；
- 表不存在、读不了、方向不认识 → ``unknown``（服务端照样问用户，照实说核不了）。
- 只读：不复制账套、不写、不碰写者锁；同号有几张（用户选过「再录一次」）只要一张还在就是 ``present``。

参数 ``{ledger: "express", account_set, account_root, items: [{doc_id, docnum, direction}]}``（最多 ``ITEMS_MAX`` 张，
账套判据同回读 ``express_read.resolve``）；结果 gzip JSON ``{"items": [{doc_id, docnum, state}]}``，按参数顺序。
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import dbf

from . import dbf_schema as S
from . import express_read
from .logger import get_logger
from .request_contract import MAX_BYTES, RequestFailed, Result

log = get_logger(__name__)

PRESENT, GONE, UNKNOWN = "present", "gone", "unknown"
HEADS = {"purchase": "APTRN", "sales": "ARTRN"}
VOIDED = "C"
ITEMS_MAX = 50
PARAM_KEYS = ("ledger", "account_set", "account_root", "items")
ITEM_KEYS = ("doc_id", "docnum", "direction")
TEXT_MAX = 140


def _params(params) -> tuple:
    if not isinstance(params, dict) or set(params) - set(PARAM_KEYS) or params.get("ledger", "express") != "express":
        raise RequestFailed("params_invalid")
    account_set, account_root = params.get("account_set"), params.get("account_root")
    if any(value is not None and not isinstance(value, str) for value in (account_set, account_root)):
        raise RequestFailed("params_invalid")
    items = params.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= ITEMS_MAX:
        raise RequestFailed("params_invalid")
    for item in items:
        if not isinstance(item, dict) or set(item) - set(ITEM_KEYS) \
                or any(not isinstance(item.get(key), str) or len(item[key]) > TEXT_MAX for key in ITEM_KEYS) \
                or not item["docnum"].strip() or not item["doc_id"]:
            raise RequestFailed("params_invalid")
    return account_set, account_root, items


def _key(value) -> str:
    return str(value or "").strip().upper()


def _table(direction) -> str:
    return HEADS.get(str(direction or "").strip().casefold(), "")


def _scan(folder: Path, table: str, docnums: set) -> dict | None:
    """这张单头表里点名的单号 → present / gone；表不在或读不了回 None。"""
    path = folder / f"{table}.DBF"
    if not path.is_file():
        return None
    found: dict = {}
    try:
        opened = dbf.Table(str(path), codepage=S.CODEPAGE)
        opened.open(dbf.READ_ONLY)
        try:
            has_status = "DOCSTAT" in opened.field_names
            for row in opened:
                number = _key(row["DOCNUM"])
                if number not in docnums or found.get(number) == PRESENT:
                    continue
                live = not dbf.is_deleted(row) and not (has_status and _key(row["DOCSTAT"]) == VOIDED)
                found[number] = PRESENT if live else GONE
        finally:
            opened.close()
    except Exception as error:  # 表被锁、坏了：这一边整体核不了
        log.info("单头表读不了 %s: %s", table, error)
        return None
    return found


def check(folder: Path, items: list) -> list:
    """每张点名的单在账本里的状态（口径见文件头），按 ``items`` 顺序。"""
    wanted: dict = {}
    for item in items:
        if _table(item["direction"]):
            wanted.setdefault(_table(item["direction"]), set()).add(_key(item["docnum"]))
    read = {table: _scan(folder, table, docnums) for table, docnums in wanted.items()}
    out = []
    for item in items:
        found = read.get(_table(item["direction"]))
        state = UNKNOWN if found is None else found.get(_key(item["docnum"]), GONE)
        out.append({"doc_id": item["doc_id"], "docnum": item["docnum"], "state": state})
    return out


def check_docs(cfg, params: dict, **_ignored) -> Result:
    """处理函数（``request_worker.HANDLERS["express_doc_check"]``）。"""
    account_set, account_root, items = _params(params)
    folder = express_read.resolve(cfg, account_set, account_root)
    content = gzip.compress(json.dumps({"items": check(folder, items)}, ensure_ascii=False).encode("utf-8"))
    if len(content) > MAX_BYTES["express_doc_check"]:
        raise RequestFailed("too_large")
    return Result(content, {"format": "json.gz"})
