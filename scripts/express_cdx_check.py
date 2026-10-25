"""真账副本离线回归共用：写入时用不用 Express 索引重建程序，以及写完后核 CDX 与 DBF 一致（10-25）。

给了 ``--runner``（测试机上已安装 CC 的 cdx_reindex_runner.exe）就照正式 CC 那样重建索引，写前记下每张要写的表的
CDX 标签，写完核：
- 用 Express 的 CDX 还能打开（-330「Tag Name not Found」这类对不上会在这里报出来），标签名和键表达式一个不少、没变；
- 打开时看到的记录数等于 DBF 里的记录数；
- 新单在每个以 DOCNUM / VOUCHER 开头的标签里都查得到（Express 靠这些标签找单）。
没给 ``--runner`` 只核表内容：打开开发 / 测试开关走「只改 DBF」的回落（正式 CC 没有重建程序会拒写）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_LOOKUP = re.compile(r"^(DOCNUM|VOUCHER)\b")


def use_runner(cdx_reindex, runner):
    """回归开头调一次：有 runner 用它重建，没有就只核表内容。"""
    if runner:
        exe = Path(runner)
        if not exe.is_file():
            raise SystemExit(f"runner not found: {exe}")
        cdx_reindex.runner_path = lambda: exe
        return True
    cdx_reindex.runner_available = lambda: False
    os.environ[cdx_reindex.DBF_FALLBACK_ENV] = "1"
    return False


def snapshot(cdx_reindex, acct: Path, tables) -> dict:
    """写前：每张有 CDX 的表 → (记录数, 标签)。"""
    return {table: cdx_reindex.tags(acct, table) for table in sorted(tables) if (Path(acct) / f"{table}.CDX").is_file()}


def problems(cdx_reindex, dbf, schema, acct: Path, before: dict, docnums) -> list:
    """写后核 CDX 与 DBF 一致，返回问题列表（空 = 一致）。"""
    found = []
    for table, (_, tags_before) in before.items():
        try:
            records, tags_after = cdx_reindex.tags(acct, table)
        except cdx_reindex.CdxReindexError as error:
            found.append(f"{table} index cannot be opened: {error}")
            continue
        if tags_after != tags_before:
            found.append(f"{table} CDX tags changed: {tags_before} -> {tags_after}")
        t = dbf.Table(str(Path(acct) / f"{table}.DBF"), codepage=schema.CODEPAGE, unicode_errors=schema.UNICODE_ERRORS)
        t.open(dbf.READ_ONLY)
        try:
            count = len(t)
            names = {name.upper() for name in t.field_names}
            values = {field: {str(getattr(r, field) or "").strip() for r in schema.live_rows(t)}
                      for field in ("DOCNUM", "VOUCHER") if field in names}
        finally:
            t.close()
        if records != count:
            found.append(f"{table} CDX sees {records} records, DBF has {count}")
        for name, expression in tags_after:
            match = _LOOKUP.match(expression.upper())
            if not match or match.group(1) not in values:
                continue
            for docnum in docnums:
                if docnum in values[match.group(1)] and not cdx_reindex.seek(acct, table, name, docnum).found:
                    found.append(f"{table}.{name} cannot find {docnum}")
    return found
