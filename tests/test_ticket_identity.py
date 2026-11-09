"""同一张票的统一身份（复核 5 批之第 1 批，`pearnly_core.express_rules.contracts.ticket_identity`）。

11-08 生产 SincereIce：同一份 24 页 PDF 在代录里识别成 24 张，又截成 4 张图在直写里识别成 4 张新的；两边各判各的，
同一张票写进了真账两次的风险都在。这里守住一份口径：单号写法不同（IV/69/00751、IV69-00751、iv69 00751）是同一张；
没印分店和总公司不是同一张；一方没有税号只算近似；单号只差一两个字的只算近似（不自动跳过）。契约是纯的，不读库。
"""
import ast
from datetime import date
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.express_rules.contracts import ticket_identity as ti  # noqa: E402

SOURCE = ROOT / "apps/pearnly_core/pearnly_core/express_rules/contracts/ticket_identity.py"


def face(**changes):
    values = dict(direction="sales", number="IV69/00751", date="2026-10-31", total="1,070.00",
                  seller_tax="0105536000633", seller_branch="00002", buyer_tax="0315551000297", buyer_branch="สำนักงานใหญ่",
                  seller_name="Big C Supercenter", buyer_name="MR. ERP COMPANY LIMITED")
    values.update(changes)
    return ti.Face(**values)


class TicketIdentityTests(unittest.TestCase):
    def test_one_ticket_written_three_ways_is_one_ticket(self):
        keys = {ti.identity(face(number=n)).key for n in ("IV69/00751", "IV69-00751", "iv69 00751", "ＩＶ６９００７５１", "IV.69.00751")}
        self.assertEqual(len(keys), 1)
        self.assertEqual(ti.same(face(), face(number="iv69-00751")), ti.EXACT)

    def test_each_part_of_the_face_tells_tickets_apart(self):
        base = ti.identity(face()).key
        for changed in (dict(number="IV69/00752"), dict(date="2026-11-01"), dict(total="1070.01"), dict(direction="purchase"),
                        dict(seller_tax="0105536000634"), dict(buyer_tax="0105500000000"), dict(seller_branch="00003")):
            self.assertNotEqual(ti.identity(face(**changed)).key, base, changed)
        self.assertEqual(ti.identity(face(total=1070)).key, base)  # 金额按 Decimal 到 0.01 比
        self.assertEqual(ti.identity(face(date=date(2026, 10, 31))).key, base)

    def test_a_branch_not_printed_is_not_the_head_office(self):
        self.assertEqual(ti.identity(face(buyer_branch="00000")).key, ti.identity(face(buyer_branch="Head Office")).key)
        self.assertNotEqual(ti.identity(face(buyer_branch="")).key, ti.identity(face(buyer_branch="00000")).key)

    def test_a_side_without_a_tax_number_is_only_near(self):
        walk_in = face(buyer_tax="", buyer_branch="", buyer_name="ลูกค้าเงินสด")
        self.assertEqual(ti.identity(walk_in).level, ti.NEAR)
        self.assertEqual(ti.same(walk_in, face(buyer_tax="", buyer_name="ลูกค้า เงินสด")), ti.NEAR)  # 名称规范化后对上：只标
        self.assertEqual(ti.identity(face()).level, ti.EXACT)

    def test_a_number_one_or_two_characters_off_is_near_and_never_the_same(self):
        # 截图把 IV69/00751 读成 IV/69/06751：其余全同 → 像（复核卡标出来让人决定），不当成同一张自动跳过
        self.assertEqual(ti.same(face(), face(number="IV/69/06751")), ti.NEAR)
        self.assertEqual(ti.same(face(), face(number="IV69/00799")), ti.NEAR)
        self.assertEqual(ti.same(face(), face(number="IV69/12345")), "")  # 差太多：不是
        self.assertEqual(ti.same(face(), face(number="IV69/00752", total="99.00")), "")  # 连号但金额不同：不是

    def test_no_identity_without_a_number_a_date_or_a_total(self):
        for missing in (dict(number=""), dict(date=""), dict(total=""), dict(total="abc"), dict(date="31/10/2026")):
            self.assertIsNone(ti.identity(face(**missing)), missing)
        self.assertEqual(ti.same(face(number=""), face(number="")), "")
        self.assertIsNone(ti.identity(face(buyer_tax="", buyer_name="")))  # 认不出是谁的票

    def test_a_recognized_record_carries_its_identity_columns(self):
        # Cowork 识别单随每次写入重算的身份列（`history.extract_ticket_fields`，与摘要同一主页面）
        from pearnly_core.express_rules.contracts.history import TICKET_FIELDS, extract_ticket_fields

        fields = {"invoice_number": "IV/69/00751", "date": "2026-10-31", "total_amount": "1,070.00", "direction": "sales",
                  "seller_tax": "0105536000633", "seller_branch": "สาขาที่ 2", "buyer_tax": "0315551000297", "buyer_branch": "",
                  "seller_name": "Big C", "buyer_name": "MR. ERP"}
        got = extract_ticket_fields([{"fields": fields}])
        self.assertEqual(set(got), set(TICKET_FIELDS))
        self.assertEqual((got["seller_branch"], got["buyer_branch"], got["ticket_number"], got["ticket_level"]),
                         ("00002", "", "IV6900751", ti.EXACT))
        self.assertEqual(got["ticket_key"], ti.identity(face(buyer_branch="", seller_branch="2", number="IV69-00751",
                                                              total="1070")).key)
        empty = extract_ticket_fields([{"fields": {**fields, "total_amount": ""}}])
        self.assertEqual((empty["ticket_key"], empty["ticket_level"], empty["ticket_number"]), (None, None, "IV6900751"))

    def test_the_contract_stays_pure(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        allowed = {"__future__", "dataclasses", "datetime", "decimal", "hashlib", "re", "unicodedata",
                   "pearnly_core.express_rules.targets.account_match"}
        self.assertEqual(imported - allowed, set())


if __name__ == "__main__":
    unittest.main()
