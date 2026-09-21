"""Offline checks for the ERP portal access rules.

No Frappe, no Site, no network: `access.rules` must stay pure. The role names are
compared against the control-plane catalogue that actually creates them, so a
rename on either side fails here instead of silently granting nothing.
"""
import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_erp_portal"))

from pearnly_erp_portal.access import rules  # noqa: E402
from pearnly_erp_portal.products import contracts  # noqa: E402
from pearnly_erp_portal.purchasing import contracts as purchase_contracts  # noqa: E402
from pearnly_erp_portal.inventory import contracts as stock_contracts  # noqa: E402
from pearnly_erp_portal.cashier import contracts as cashier_contracts  # noqa: E402
from pearnly_erp_portal.masters import contracts as master_contracts  # noqa: E402

FEATURES = ROOT / "apps/pearnly_control/pearnly_control/features.py"


def _literal(name):
    """Pull one module-level literal assignment out of the control-plane module."""
    tree = ast.parse(FEATURES.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {FEATURES}")


class RoleParity(unittest.TestCase):
    def test_admin_role_matches_control_plane(self):
        self.assertEqual(rules.ADMIN_ROLE, _literal("PRODUCT_ADMIN_ROLE")["erp"])

    def test_base_role_is_granted_by_control_plane(self):
        self.assertIn(rules.BASE_ROLE, _literal("BASE_ROLES"))

    def test_feature_roles_match_control_plane(self):
        enforcement = _literal("ENFORCEMENT")
        expected = {
            code: role for (product, code), (role, _doctypes) in enforcement.items()
            if product == "erp"
        }
        self.assertTrue(expected, "control plane must enforce at least one ERP feature")
        for code, role in expected.items():
            with self.subTest(code=code):
                self.assertEqual(rules.FEATURE_ROLES.get(code), role)

    def test_every_capability_points_at_a_real_feature(self):
        for capability, features in rules.CAPABILITY_FEATURES.items():
            with self.subTest(capability=capability):
                self.assertTrue(features, "a capability must name at least one feature")
                for feature in features:
                    self.assertIn(feature, rules.FEATURE_ROLES)


class Capabilities(unittest.TestCase):
    def test_purchase_requires_the_feature_role(self):
        self.assertEqual(rules.required_roles("purchase.read"), ("ERP Purchase",))
        self.assertTrue(rules.can({"ERP Purchase"}, "purchase.read"))
        self.assertTrue(rules.can({"ERP Purchase"}, "purchase.write"))
        self.assertFalse(rules.can({"Desk User"}, "purchase.read"))

    def test_unknown_capability_is_denied(self):
        self.assertEqual(rules.required_roles("purchase.delete"), ())
        self.assertFalse(rules.can({"ERP Admin", "ERP Purchase"}, "purchase.delete"))

    def test_admin_role_alone_grants_no_business_capability(self):
        self.assertFalse(rules.can({rules.ADMIN_ROLE}, "purchase.read"))


class RecordScope(unittest.TestCase):
    def test_admins_see_all_records(self):
        self.assertEqual(rules.record_scope({rules.ADMIN_ROLE}), rules.ALL_RECORDS)
        self.assertEqual(rules.record_scope({rules.SYSTEM_ADMIN_ROLE}), rules.ALL_RECORDS)

    def test_employee_role_sees_own_records_only(self):
        self.assertEqual(rules.record_scope({"ERP Purchase"}), rules.OWN_RECORDS)
        self.assertEqual(rules.record_scope(set()), rules.OWN_RECORDS)


class TeamRoles(unittest.TestCase):
    def test_team_management_is_admin_only(self):
        self.assertEqual(rules.required_roles("team.manage"), (rules.ADMIN_ROLE,))
        self.assertTrue(rules.can({rules.ADMIN_ROLE}, "team.manage"))
        self.assertFalse(rules.can({"ERP Purchase"}, "team.manage"))

    def test_only_roles_present_on_the_site_are_assignable(self):
        present = ["ERP Purchase", "Desk User", "System Manager"]
        self.assertEqual(rules.assignable_roles(present), ("ERP Purchase",))
        self.assertTrue(rules.is_assignable("ERP Purchase", present))
        # 平台角色与产品管理员角色永不外发
        self.assertFalse(rules.is_assignable("System Manager", present))
        self.assertFalse(rules.is_assignable(rules.ADMIN_ROLE, [rules.ADMIN_ROLE]))

    def test_nothing_is_assignable_without_purchased_roles(self):
        self.assertEqual(rules.assignable_roles(["Desk User"]), ())


class ProductContracts(unittest.TestCase):
    def test_blank_price_stays_blank(self):
        # 旧线上强调过：0 当默认值会让"没设价"与"真的 ฿0"在库里变成同一个值。
        self.assertIsNone(contracts.decimal_or_none(None))
        self.assertIsNone(contracts.decimal_or_none(""))
        self.assertEqual(str(contracts.decimal_or_none("0")), "0")

    def test_negative_or_junk_price_is_rejected(self):
        for value in ("-1", "abc", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contracts.decimal_or_none(value)

    def test_unit_factor_must_be_positive(self):
        self.assertEqual(str(contracts.unit_factor(12)), "12")
        for value in (0, -3, None, "x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contracts.unit_factor(value)

    def test_name_is_required_and_bounded(self):
        self.assertEqual(contracts.require_name("  สินค้า  "), "สินค้า")
        with self.assertRaises(ValueError):
            contracts.require_name("   ")
        with self.assertRaises(ValueError):
            contracts.require_name("x" * (contracts.MAX_NAME + 1))

    def test_costs_are_masked_without_the_role(self):
        row = {"name": "PRD-1", "default_cost": "12", "avg_cost": "13"}
        masked = contracts.mask_costs(dict(row), False)
        self.assertIsNone(masked["default_cost"])
        self.assertIsNone(masked["avg_cost"])
        self.assertEqual(masked["name"], "PRD-1")
        self.assertEqual(contracts.mask_costs(dict(row), True), row)

    def test_stock_role_sees_cost_but_a_shop_role_does_not(self):
        self.assertTrue(rules.cost_visible({rules.FEATURE_ROLES["warehouses"]}))
        self.assertTrue(rules.cost_visible({rules.ADMIN_ROLE}))
        self.assertFalse(rules.cost_visible({rules.FEATURE_ROLES["products"]}))
        self.assertFalse(rules.cost_visible(set()))

    def test_product_capabilities_follow_the_products_feature(self):
        role = rules.FEATURE_ROLES["products"]
        self.assertTrue(rules.can({role}, "products.view"))
        self.assertTrue(rules.can({role}, "products.manage"))
        self.assertFalse(rules.can({rules.FEATURE_ROLES["cashier"]}, "products.manage"))

    def test_stock_role_may_view_products_but_not_manage_them(self):
        # 库存岗位做入库/出库要先看得到商品，但商品主数据仍归商品岗位。
        stock = rules.FEATURE_ROLES["warehouses"]
        self.assertTrue(rules.can({stock}, "products.view"))
        self.assertFalse(rules.can({stock}, "products.manage"))


class PurchaseAmounts(unittest.TestCase):
    def test_totals_follow_the_legacy_shape(self):
        # 旧线上实测过的一组：小计 100、VAT 7、WHT 5 → 净应付 102。
        totals = purchase_contracts.compute_totals(
            lines=[{"qty": 1, "unit_price": 100, "vat_rate": 7}],
            has_vat=True, doc_kind="service", wht_amount=5,
        )
        self.assertEqual(str(totals["subtotal"]), "100.00")
        self.assertEqual(str(totals["vat_amount"]), "7.00")
        self.assertEqual(str(totals["grand_total"]), "107.00")
        self.assertEqual(str(totals["net_payable"]), "102.00")

    def test_wht_can_come_from_a_rate(self):
        totals = purchase_contracts.compute_totals(
            lines=[{"qty": 2, "unit_price": 50}], has_vat=False, doc_kind="service",
            wht_rate=3,
        )
        self.assertEqual(str(totals["wht_amount"]), "3.00")
        self.assertEqual(str(totals["net_payable"]), "97.00")

    def test_line_discount_and_document_adjustments(self):
        totals = purchase_contracts.compute_totals(
            lines=[{"qty": 3, "unit_price": 10, "discount": 5}],
            has_vat=False, doc_kind="goods", discount_total=2, rounding="0.5",
        )
        self.assertEqual(str(totals["subtotal"]), "25.00")
        self.assertEqual(str(totals["net_payable"]), "23.50")

    def test_bad_lines_are_rejected(self):
        cases = [
            ([], True, "lines"),
            ([{"qty": 0, "unit_price": 10}], False, "greater than 0"),
            ([{"qty": 1, "unit_price": -1}], False, "invalid"),
            ([{"qty": 1, "unit_price": 10, "discount": 20}], False, "exceeds"),
        ]
        for lines, has_vat, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                purchase_contracts.compute_totals(lines=lines, has_vat=has_vat, doc_kind="goods")

    def test_payment_status_follows_the_amount(self):
        status = purchase_contracts.payment_status
        self.assertEqual(status(0, "102.00"), "unpaid")
        self.assertEqual(status("50", "102.00"), "partial")
        self.assertEqual(status("102", "102.00"), "paid")
        self.assertEqual(status("150", "102.00"), "paid")

    def test_only_allowed_status_transitions(self):
        can = purchase_contracts.can_transition
        self.assertTrue(can("draft", "submitted"))
        self.assertTrue(can("draft", "void"))
        self.assertTrue(can("submitted", "void"))
        self.assertFalse(can("submitted", "draft"))
        self.assertFalse(can("void", "draft"))
        self.assertFalse(can("void", "submitted"))

    def test_duplicate_key_needs_both_supplier_and_doc_no(self):
        key = purchase_contracts.duplicate_key
        self.assertEqual(key("ACME", "IV-1"), "acme:iv-1")
        # 单据类型不进判据：同一家供应商的同一个单号就是同一张单。
        self.assertEqual(key("ACME", "IV-1"), key("ACME", "iv-1"))
        self.assertIsNone(key("ACME", ""))
        self.assertIsNone(key("", "IV-1"))


class StockRollingCost(unittest.TestCase):
    """滚动加权成本：口径照旧线上 rolling.py，重点是"成本未知不变成零"。"""

    def test_opening_with_zero_qty_has_no_cost(self):
        bal = stock_contracts.opening_balance(0, 100)
        self.assertEqual(bal.qty, 0)
        self.assertIsNone(bal.unit)
        self.assertIsNone(bal.value)

    def test_opening_establishes_cost(self):
        bal = stock_contracts.opening_balance(10, "12.50")
        self.assertEqual(str(bal.qty), "10")
        self.assertEqual(str(bal.value), "125.00")
        self.assertEqual(str(bal.unit), "12.50")

    def test_purchase_averages_the_cost(self):
        bal = stock_contracts.opening_balance(10, 10)          # 100
        bal, detail = stock_contracts.receive_priced(bal, 10, 20)  # +200
        self.assertEqual(str(bal.qty), "20")
        self.assertEqual(str(bal.value), "300.00")
        self.assertEqual(str(bal.unit), "15.00")
        self.assertEqual(str(detail["amount"]), "200.00")

    def test_issue_uses_the_current_unit_cost_without_repricing(self):
        bal = stock_contracts.opening_balance(20, 15)              # 300
        bal, detail = stock_contracts.issue(bal, 5)
        self.assertEqual(str(bal.qty), "15")
        self.assertEqual(str(detail["unit_price"]), "15.00")
        self.assertEqual(str(detail["amount"]), "75.00")
        self.assertEqual(str(bal.value), "225.00")
        self.assertEqual(str(bal.unit), "15.00")

    def test_unknown_cost_never_becomes_zero(self):
        # 从未确立成本：出库只减数量，金额与单价必须留空。
        bal, detail = stock_contracts.issue(stock_contracts.ZERO_BALANCE, 3)
        self.assertEqual(str(bal.qty), "-3")
        self.assertIsNone(bal.unit)
        self.assertIsNone(bal.value)
        self.assertIsNone(detail["amount"])
        self.assertIsNone(detail["unit_price"])

    def test_return_in_uses_current_cost_and_keeps_the_average(self):
        bal = stock_contracts.opening_balance(10, 10)
        bal, _ = stock_contracts.issue(bal, 4)          # 60
        bal, _ = stock_contracts.receive_at_cost(bal, 4)  # +40 → 100
        self.assertEqual(str(bal.qty), "10")
        self.assertEqual(str(bal.value), "100.00")
        self.assertEqual(str(bal.unit), "10.00")

    def test_count_movement_settles_to_the_counted_quantity(self):
        bal = stock_contracts.opening_balance(10, 5)
        bal, _ = stock_contracts.apply_move(bal, -4)
        self.assertEqual(str(bal.qty), "6")
        bal, _ = stock_contracts.apply_move(bal, 4)
        self.assertEqual(str(bal.qty), "10")

    def test_signed_qty_follows_the_movement_type(self):
        signed = stock_contracts.signed_qty
        self.assertEqual(str(signed("purchase_in", 5)), "5")
        self.assertEqual(str(signed("stock_out", 5)), "-5")
        self.assertEqual(str(signed("adjust", -2)), "-2")
        with self.assertRaises(ValueError):
            signed("nonsense", 1)

    def test_negative_quantity_is_rejected_for_directional_types(self):
        with self.assertRaises(ValueError):
            stock_contracts.signed_qty("purchase_in", -1)

    def test_signed_base_is_the_only_quantity_carried(self):
        # 方向只由符号表达：落库的数量直接就是滚存用的数量。
        self.assertEqual(str(stock_contracts.signed_base("stock_out", 3, 2)), "-6")
        self.assertEqual(str(stock_contracts.signed_base("purchase_in", 3, 12)), "36")
        self.assertEqual(str(stock_contracts.signed_base("adjust", -4, 1)), "-4")

    def test_replaying_stored_movements_is_stable(self):
        # 同一份流水实时记一次、事后重算一次，结果必须一致（这是"结存可由流水重建"的前提）。
        movements = [
            {"movement_type": "opening", "qty_base": 10, "cost_known": True, "unit_cost": 10},
            {"movement_type": "stock_in", "qty_base": 5, "cost_known": True, "unit_cost": 20},
            {"movement_type": "stock_out", "qty_base": -3, "cost_known": True, "unit_cost": 20},
            {"movement_type": "count", "qty_base": 4, "cost_known": True, "unit_cost": 1},
        ]
        final, rows = stock_contracts.roll(stock_contracts.ZERO_BALANCE, movements)
        self.assertEqual(str(final.qty), "16")
        # 出库按当时均价 13.33 计价后，入库 4 件 @1 元把均价拉低。
        self.assertEqual(str(rows[1]["bal_qty"]), "15")
        self.assertEqual(str(rows[2]["bal_qty"]), "12")
        self.assertAlmostEqual(float(rows[2]["bal_unit_cost"]), 13.33, places=2)

    def test_unit_conversion_uses_the_factor(self):
        self.assertEqual(str(stock_contracts.to_base(2, 12)), "24")
        for bad in (0, -1, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                stock_contracts.to_base(1, bad)

    def test_opening_date_splits_movements(self):
        movements = [
            {"posting_date": "2026-08-31", "movement_type": "opening", "qty_base": 5,
             "cost_known": True, "unit_cost": 10},
            {"posting_date": "2026-09-01", "movement_type": "stock_in", "qty_base": 3,
             "cost_known": True, "unit_cost": 10},
            {"posting_date": "2026-09-10", "movement_type": "stock_out", "qty_base": -1,
             "cost_known": True, "unit_cost": 10},
        ]
        before, period = stock_contracts.movements_before(movements, "2026-09-01")
        # 期内从 date_from 当天算起，期初是它之前的结转。
        self.assertEqual([m["qty_base"] for m in before], [5])
        self.assertEqual([m["qty_base"] for m in period], [3, -1])




class CashierSettlement(unittest.TestCase):
    """付款结算与找零：口径照旧线上 payment_settlement.py，一条都不放宽。"""

    def test_cash_payment_with_change(self):
        payments, paid, change = cashier_contracts.settle(
            [{"method": "cash", "amount": 100}], 87)
        self.assertEqual(str(paid), "100.00")
        self.assertEqual(str(change), "13.00")
        self.assertEqual(payments[0]["method"], "cash")

    def test_qr_is_normalised_to_promptpay(self):
        payments, _, _ = cashier_contracts.settle([{"method": "qr", "amount": 50}], 50)
        self.assertEqual(payments[0]["method"], "promptpay")

    def test_exact_non_cash_payment_is_fine(self):
        _, paid, change = cashier_contracts.settle([{"method": "card", "amount": 50}], 50)
        self.assertEqual((str(paid), str(change)), ("50.00", "0.00"))

    def test_non_cash_overpay_is_rejected(self):
        # 非现金多付不给找零：要么是错，要么得改单。
        with self.assertRaises(cashier_contracts.SettlementError) as ctx:
            cashier_contracts.settle([{"method": "card", "amount": 60}], 50)
        self.assertEqual(ctx.exception.detail, "noncash_overpay")

    def test_underpayment_is_rejected(self):
        with self.assertRaises(cashier_contracts.SettlementError) as ctx:
            cashier_contracts.settle([{"method": "cash", "amount": 40}], 50)
        self.assertEqual(ctx.exception.detail, "underpaid")

    def test_split_payment_may_change_from_the_cash_part(self):
        _, paid, change = cashier_contracts.settle(
            [{"method": "card", "amount": 50}, {"method": "cash", "amount": 60}], 100)
        self.assertEqual((str(paid), str(change)), ("110.00", "10.00"))

    def test_zero_total_takes_no_payment(self):
        self.assertEqual(cashier_contracts.settle([], 0), ([], 0, 0))
        with self.assertRaises(cashier_contracts.SettlementError) as ctx:
            cashier_contracts.settle([{"method": "cash", "amount": 10}], 0)
        self.assertEqual(ctx.exception.detail, "zero_total")

    def test_bad_payment_rows_are_rejected(self):
        cases = [
            ([{"method": "crypto", "amount": 10}], "method"),
            ([{"method": "cash", "amount": 0}], "amount"),
            ([{"method": "cash", "amount": -5}], "amount"),
            ([{"method": "cash", "amount": "10.555"}], "amount"),
            (["cash"], "payment"),
        ]
        for payments, detail in cases:
            with self.subTest(detail=detail), self.assertRaises(
                    cashier_contracts.SettlementError) as ctx:
                cashier_contracts.settle(payments, 10)
            self.assertEqual(ctx.exception.detail, detail)

    def test_sale_totals_apply_discount_and_vat(self):
        # 行净额 2×50−10 = 90；单据折扣 5 → 应税 85；VAT 7% = 5.95；合计 90.95。
        totals = cashier_contracts.sale_totals(
            [{"qty": 2, "unit_price": 50, "discount": 10}], discount_total=5, vat_rate=7)
        self.assertEqual(str(totals["subtotal"]), "90.00")
        self.assertEqual(str(totals["discount_total"]), "5.00")
        self.assertEqual(str(totals["vat_amount"]), "5.95")
        self.assertEqual(str(totals["grand_total"]), "90.95")

    def test_sale_totals_reject_non_positive_qty_and_big_discounts(self):
        with self.assertRaises(cashier_contracts.SettlementError):
            cashier_contracts.sale_totals([{"qty": 0, "unit_price": 10}])
        with self.assertRaises(cashier_contracts.SettlementError):
            cashier_contracts.sale_totals([], )
        with self.assertRaises(cashier_contracts.SettlementError):
            cashier_contracts.sale_totals([{"qty": 1, "unit_price": 10, "discount": 20}])
        with self.assertRaises(cashier_contracts.SettlementError):
            cashier_contracts.sale_totals([{"qty": 1, "unit_price": 10}], discount_total=20)

    def test_shift_cash_expectation_and_difference(self):
        # 备用金 500 + 现金实收 1000 − 找零 60 = 1440
        expected = cashier_contracts.shift_expected(500, 1000, 60)
        self.assertEqual(str(expected), "1440.00")
        self.assertEqual(str(cashier_contracts.shift_difference(1400, expected)), "-40.00")
        self.assertEqual(str(cashier_contracts.shift_difference(1500, expected)), "60.00")


class BuyerNormalisation(unittest.TestCase):
    """买方归一化：类型决定哪些字段有意义，不该有的值一律清掉。"""

    def test_company_keeps_tax_id_and_branch(self):
        clean = master_contracts.normalize_buyer({
            "customer_name": "ACME", "party_type": "company", "tax_id": "0105",
            "branch_type": "branch", "branch_no": "00001"})
        self.assertEqual(clean["tax_id"], "0105")
        self.assertEqual((clean["branch_type"], clean["branch_no"]), ("branch", "00001"))

    def test_company_head_office_drops_the_branch_number(self):
        clean = master_contracts.normalize_buyer({
            "customer_name": "ACME", "party_type": "company", "branch_type": "hq",
            "branch_no": "00001"})
        self.assertIsNone(clean["branch_no"])

    def test_person_and_anonymous_drop_tax_id_and_branch(self):
        for party in ("person", "foreign", "anonymous"):
            with self.subTest(party=party):
                clean = master_contracts.normalize_buyer({
                    "customer_name": "Walk-in", "party_type": party, "tax_id": "0105",
                    "branch_type": "branch", "branch_no": "1"})
                self.assertIsNone(clean["tax_id"])
                self.assertIsNone(clean["branch_type"])
                self.assertIsNone(clean["branch_no"])

    def test_unknown_party_type_falls_back_to_company(self):
        clean = master_contracts.normalize_buyer({"customer_name": "X", "party_type": "nonsense"})
        self.assertEqual(clean["party_type"], "company")

    def test_overlong_values_are_rejected(self):
        with self.assertRaises(ValueError):
            master_contracts.normalize_buyer({"customer_name": "x" * 300})

    def test_tax_invoice_requirements_per_buyer_type(self):
        missing = master_contracts.missing_for_tax_invoice
        self.assertEqual(missing({"party_type": "anonymous"}), ["party_type"])
        self.assertEqual(sorted(missing({"party_type": "company", "customer_name": "A"})),
                         ["address", "tax_id"])
        self.assertEqual(missing({"party_type": "person", "customer_name": "A",
                                 "address": "B"}), [])
        self.assertEqual(missing({"party_type": "company", "customer_name": "A", "address": "B",
                                  "tax_id": "1", "branch_type": "branch"}), ["branch_no"])

    def test_fiscal_month_bounds(self):
        self.assertTrue(master_contracts.fiscal_month_ok(1))
        self.assertTrue(master_contracts.fiscal_month_ok("12"))
        self.assertFalse(master_contracts.fiscal_month_ok(0))
        self.assertFalse(master_contracts.fiscal_month_ok(13))
        self.assertFalse(master_contracts.fiscal_month_ok("jan"))


class IntegrationBinding(unittest.TestCase):
    """集成（LINE 绑定）：任何业务岗位都能绑**自己**的账号，没岗位的账号不能。"""

    def test_any_business_job_can_bind(self):
        for feature, role in rules.FEATURE_ROLES.items():
            with self.subTest(feature=feature):
                self.assertTrue(rules.can({role}, "integration.read"))
                self.assertTrue(rules.can({role}, "integration.manage"))

    def test_without_a_business_job_there_is_nothing_to_bind(self):
        self.assertFalse(rules.can({"Desk User"}, "integration.read"))
        self.assertFalse(rules.can({"Desk User"}, "integration.manage"))
        self.assertEqual(rules.required_roles("integration.read"),
                         rules.required_roles("integration.manage"))

    def test_the_admin_role_alone_does_not_open_it(self):
        self.assertFalse(rules.can({rules.ADMIN_ROLE}, "integration.manage"))

    def test_unbind_word_list_keeps_the_words_the_user_types(self):
        """解绑是**用户手打的词**，翻了就等于这个功能不存在（登记在 keep-cjk）。"""
        source = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/integrations"
                  / "handlers.py").read_text()
        tree = ast.parse(source)
        words = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "UNBIND_WORDS" for t in node.targets
            ):
                words = ast.literal_eval(node.value)
        self.assertIsNotNone(words, "UNBIND_WORDS must stay a literal list of words")
        # 与 DMS 通道收同一批词：老用户按旧话术打字也必须能解绑。
        for word in ("ยกเลิกการผูก", "ยกเลิกการเชื่อมต่อ", "解绑", "unbind"):
            self.assertIn(word, words)


if __name__ == "__main__":
    unittest.main()
