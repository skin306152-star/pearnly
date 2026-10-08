# -*- coding: utf-8 -*-
"""给写入测试补「账套设置」:ISINFO(真账套必有、合成表规格里没有)与可记账科目。"""
from __future__ import annotations

from pathlib import Path

import dbf

from companion import dbf_schema as S


def seed(acct: Path, table: str, rows) -> None:
    t = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for row in rows:
            t.append(row)
    finally:
        t.close()


def isinfo(acct: Path, **fields) -> None:
    spec = "; ".join(f"{name} C(15)" for name in ("THINAM", *fields))
    path = acct / "ISINFO.DBF"
    if path.exists():
        path.unlink()
    t = dbf.Table(str(path), spec, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE)
    t.open(dbf.READ_WRITE)
    try:
        t.append({"THINAM": "TEST", **fields})
    finally:
        t.close()


def glacc(acct: Path, *accounts) -> None:
    seed(acct, "GLACC", [{"ACCNUM": num, "ACCNAM": nam, "ACCTYP": "0"} for num, nam in accounts])
