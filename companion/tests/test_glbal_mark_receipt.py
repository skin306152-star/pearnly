# -*- coding: utf-8 -*-
"""收款单 RE 真写一张后，分录用到的「科目 + 部门」那一行 GLBAL 这一期那一位标成待重算（照 Express 原生；集成分支拼车核接缝时指出的缺口，11-12）。

收款写路（``dbf_receipt._write_all``）写完 GLJNLIT、关表之后调 ``glbal_mark.mark``；这里用收款测试的夹具（NLTTEST 结构的合成账套）真写一张，
逐行核 GLBAL：分录科目那几行第 11 位（2026-11）1 → 0，别的行、别的字节不动，回执没有没标上的格。单独一个文件，不改收款分支的测试。
"""

from __future__ import annotations

import dbf

from companion import dbf_schema as S
from companion import glbal_mark
from test_dbf_receipt import AR, BBL_CA, BBL_SA, CASH, DISCOUNT, FEE, _journal, _payload, _write, acct  # noqa: F401
from test_glbal_mark import NATIVE_SPEC, _raw

ALL = "1" * 24


def _seed(acct, keys):
    table = dbf.Table(str(acct / "GLBAL.DBF"), NATIVE_SPEC, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE)
    table.open(dbf.READ_WRITE)
    try:
        for acc, dep in keys:
            table.append({"ACCNUM": acc, "DEPCOD": dep, "CALSTA": ALL, "DEBIT11": 1.0})
    finally:
        table.close()


def test_a_receipt_marks_the_period_position_of_its_journal_accounts(acct):
    keys = [(acc, dep) for acc in (CASH, BBL_SA, BBL_CA, DISCOUNT, AR, FEE, "9999-99") for dep in ("", "CPI")]
    _seed(acct, keys)
    before = _raw(acct)
    with glbal_mark.collecting() as unmarked:
        res = _write(acct, _payload())
    assert res.ok, res.error
    posted = {(acc, dep) for _seq, acc, _side, _amount, dep in _journal(acct, res.docnum)}
    assert posted and all(key in keys for key in posted)
    assert unmarked == []
    after = _raw(acct)
    assert len(after) == len(before)
    offset = 1 + 15 + 4 + 6 + 10  # 删除标记 + ACCNUM + DEPCOD + JOBCOD → CALSTA 第 11 位（2026-11）
    for key, old, new in zip(keys, before, after):
        diff = [i for i in range(len(old)) if old[i] != new[i]]
        assert diff == ([offset] if key in posted else []), key
        if key in posted:
            assert new[offset:offset + 1] == b"0"
