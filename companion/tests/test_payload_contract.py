# -*- coding: utf-8 -*-
"""载荷版本协商契约:v1 放行/缺失视同 v1 放行/未来版本诚实拒绝。"""

from src.companion.payload_contract import (
    ERR_PAYLOAD_VERSION,
    SUPPORTED_PAYLOAD_VERSIONS,
    check_payload_version,
)


def test_v1_supported():
    assert check_payload_version({"payload_version": 1}) is None


def test_missing_version_defaults_to_v1_supported():
    # 兼容后端未升级期下发的旧载荷:没有这个键 → 视同 1。
    assert check_payload_version({"direction": "purchase"}) is None


def test_future_version_rejected():
    assert check_payload_version({"payload_version": 6}) == ERR_PAYLOAD_VERSION


def test_supported_set_is_v1_to_v5_today():
    # v2(2026-09-28):明细 `code`(选定的存货按码落账)+ 单头 `depcod`(部门码)
    # v3(2026-10-13):POS 日汇总票的 `pos` 块(自编单号、票号起止、付款方式转账凭证)
    # v4(2026-10-22):明细行 / 表头 `loccod`(Express 仓库码,写进指定仓并维护 STLOC)
    # v5(2026-10-24):总账分录带 `role`(ERP 前台明细按行科目拆,费用 / 收入可以有几条),明细行带 `account`
    assert SUPPORTED_PAYLOAD_VERSIONS == frozenset({1, 2, 3, 4, 5})
    assert check_payload_version({"payload_version": 5}) is None
    assert check_payload_version({"payload_version": 2}) is None
    assert check_payload_version({"payload_version": 3}) is None
    assert check_payload_version({"payload_version": 4}) is None


def test_journal_lines_with_roles_allow_several_expense_or_revenue_lines():
    # v5（10-24 验收问题 24）：ERP 前台明细按行科目拆，采购可以有几条费用分录、销项可以有几条收入分录；按 role 写，
    # 不再按位置把第二条费用当进项税。没有 role 的旧载荷照旧按位置。
    from types import SimpleNamespace

    from src.companion import dbf_schema as S
    from src.companion import purchase_lines
    from src.companion.dbf_sales import _gl_rows

    purchase = SimpleNamespace(lines=[
        {"acc": "5320-02", "side": "D", "amount": "200.00", "role": "purchase"},
        {"acc": "5330-02", "side": "D", "amount": "100.00", "role": "purchase"},
        {"acc": "1154-00", "side": "D", "amount": "21.00", "role": "input_vat"},
        {"acc": "2110-00", "side": "C", "amount": "321.00", "role": "ap"}])
    rows = purchase_lines.journal_rows(purchase)
    assert [(seqit, acc) for seqit, acc, _side, _amount in rows] == [
        (S.SEQIT["purchase"], "5320-02"), (S.SEQIT["purchase"], "5330-02"), (S.SEQIT["input_vat"], "1154-00"),
        (S.SEQIT["ap"], "2110-00")]
    old = SimpleNamespace(lines=[{**line, "role": ""} for line in purchase.lines[1:]])
    assert [seqit for seqit, *_ in purchase_lines.journal_rows(old)] == [S.SEQIT["purchase"], S.SEQIT["input_vat"], S.SEQIT["ap"]]
    sales = SimpleNamespace(lines=[
        {"acc": "1130-00", "side": "D", "amount": "321.00", "role": "asset"},
        {"acc": "4110-01", "side": "C", "amount": "200.00", "role": "revenue"},
        {"acc": "4110-02", "side": "C", "amount": "100.00", "role": "revenue"},
        {"acc": "2155-00", "side": "C", "amount": "21.00", "role": "output_vat"}])
    assert [(seqit, acc) for seqit, acc, _side, _amount in _gl_rows(sales)] == [
        (S.SALES_SEQIT["asset"], "1130-00"), (S.SALES_SEQIT["revenue"], "4110-01"), (S.SALES_SEQIT["revenue"], "4110-02"),
        (S.SALES_SEQIT["output_vat"], "2155-00")]
