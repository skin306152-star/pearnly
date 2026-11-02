"""MR.ERP 代录（CC 桌面版）找回 / 核对单据的列表解析与服务端导入直写的回读同口径（ADR-026 第 2 步、评估 §14.1）。

CC 不能导入服务端代码，``companion/src/companion/mrerp_operator/book.py`` 写了同口径的解析；这里拿同一份 HTML 让两边各解析一遍，
结论必须一致（行、找单、列表能不能用）。服务端那份是 ``apps/pearnly_core/pearnly_core/mrerp/push/readback.py``。

    python3 tests/test_mrerp_operator_readback_parity.py
"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SERVER = _load("mrerp_server_readback", ROOT / "apps/pearnly_core/pearnly_core/mrerp/push/readback.py")
sys.path.insert(0, str(ROOT / "companion/src"))
from companion.mrerp_operator import book as CLIENT  # noqa: E402

# 照 aptran/component/showdata.php 的输出：每行一个 <p>，前四格单号 / 日期 / 名称 / 金额，URA 小表嵌在第五格，查看链接在后面
ROWS = """
<p><span>PNINV-0007</span><span>20/10/2569</span><span>ผู้ขาย หนึ่ง &amp; ลูก</span><span>1,234.50</span>
 <span><a class="waitreview"></a><span class="showura"><span><span>รายการ</span><span>รหัส</span><span>วันที่</span></span>
 <span><span>User</span><span>ccbot</span><span>20/10/2569</span></span></span></span>
 <span><a href="allform.php?id=812&status=vie"></a></span><span><a href="allform.php?id=812&status=edt"></a></span></p>
<p><span>PNINV-0007X</span><span>21/10/2569</span><span>อื่น</span><span>99.00</span><span><a></a></span>
 <span><a href="allform.php?id=813&amp;status=vie"></a></span></p>
<p><span>INV-0007</span><span>19/10/2569</span><span>ไม่มีคำนำหน้า</span><span>10.00</span><span><a></a></span></p>
<p id="nodata">No Data</p>
"""
NOT_LINKED = '<div id="verticalmiddle"><div><h3>กรุณากำหนดการเชื่อมโยง "สมุดรายวัน"</h3></div></div>'
SETUP = '<h3>กรุณาป้อนข้อมูล "ผังบัญชีอ้างอิง"</h3>'


class SameReadback(unittest.TestCase):
    def test_rows_parse_the_same(self):
        server = [tuple(vars(r).values()) for r in SERVER.parse_rows(ROWS)]
        client = [(r.docno, r.date, r.name, r.amount) for r in CLIENT.parse_rows(ROWS)]
        self.assertEqual(server, client)
        self.assertEqual(client[0], ("PNINV-0007", "20/10/2569", "ผู้ขาย หนึ่ง & ลูก", "1,234.50"))
        # CC 多读查看页编号（同一批行、同一顺序）；没有查看权限的行为空
        listed = CLIENT.parse_listing(ROWS)
        self.assertEqual([(r["docno"], r["id"]) for r in listed],
                         [("PNINV-0007", "812"), ("PNINV-0007X", "813"), ("INV-0007", "")])

    def test_finding_a_document_gives_the_same_answer(self):
        server_rows, client_rows = SERVER.parse_rows(ROWS), CLIENT.parse_rows(ROWS)
        for ours in ("INV-0007", "0007", "INV-0007X", "nothing", ""):
            found_server = SERVER.find(server_rows, ours)
            found_client = CLIENT.find(client_rows, ours)
            self.assertEqual(found_server and found_server.docno, found_client and found_client.docno, ours)

    def test_an_unusable_list_is_recognised_the_same(self):
        for page in (NOT_LINKED, SETUP, ROWS, ""):
            self.assertEqual(SERVER.listing_problem(page), CLIENT.listing_problem(page))
        self.assertEqual(CLIENT.listing_problem(NOT_LINKED), "not_linked")
        self.assertEqual((SERVER.NOT_LINKED_MARK, SERVER.PAGE_ROWS), (CLIENT.NOT_LINKED_MARK, 30))

    def test_no_data_and_empty_pages_have_no_rows_on_both_sides(self):
        for page in ('<p id="nodata">No Data</p>', "", "<div></div>"):
            self.assertEqual(SERVER.parse_rows(page), [])
            self.assertEqual(CLIENT.parse_rows(page), [])


if __name__ == "__main__":
    unittest.main()
