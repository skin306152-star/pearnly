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
    assert check_payload_version({"payload_version": 17}) == ERR_PAYLOAD_VERSION


def test_supported_set_is_v1_to_v12_today():
    # v2(2026-09-28):明细 `code`(选定的存货按码落账)+ 单头 `depcod`(部门码)
    # v3(2026-10-13):POS 日汇总票的 `pos` 块(自编单号、票号起止、付款方式转账凭证)
    # v4(2026-10-22):明细行 / 表头 `loccod`(Express 仓库码,写进指定仓并维护 STLOC)
    # v5(2026-10-24):总账分录带 `role`(ERP 前台明细按行科目拆,费用 / 收入可以有几条),明细行带 `account`
    # v6(2026-10-25):库存单 `direction = stock`(OU 领用 / JU 调整 / TK 盘点,出入库第一期 B 段)
    # v7(2026-10-25):明细行带 ERP 的包装单位换算率 `factor`,包装单位照 Express 原生记法写(stock_units)
    # v8(2026-10-25):库存单 `action = void`(照 Express 原生作废原单,B 段 ①)
    # v9(2026-10-26):销项表头 `vat_fields`(ISVAT 金额栏照写,混有免税商品时 AMT01 只记应税部分)
    # v10(2026-10-26):收银退货单 SR(销项 `doctype = SR` 带 `refund` 块,挂原 HS、退现金、货回仓;dbf_refund)
    # v11(2026-10-26):明细免税行标记 `vat_exempt`、退货块 `cross_year`(实录第 10 条、B3b 实录点 ②)
    # v12(2026-10-29):整单免税的进货单照原生价格类型 1 / 2、写进项税记录(带 `vat_fields` AMT01 0 + AMTRAT0 全额)
    # v13(2026-10-29):进货单带人选的服务品进项税口径 `srv_vattyp`(账套历史定不了时,service_vat)
    assert check_payload_version({"payload_version": 13}) is None
    # v14(2026-10-30):仓间调拨 RL 与作废 RL(dbf_transfer,多仓闭环 P2)
    # v15(2026-11-01):库存单 / 调拨明细 `new_ok`(Express 里没有的商品,写前建档;多仓闭环 P3x)
    assert check_payload_version({"payload_version": 15}) is None
    # v16(2026-11-02):代录 Express(entry_mode = operator)
    assert SUPPORTED_PAYLOAD_VERSIONS == frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16})
    assert check_payload_version({"payload_version": 16}) is None
    assert check_payload_version({"payload_version": 14}) is None
    assert check_payload_version({"payload_version": 11}) is None
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
