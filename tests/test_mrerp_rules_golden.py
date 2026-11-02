"""MR.ERP 规则下沉共用层（评估 2026-11-02 §13 第 5 步）的逐字节锁：导入文件与推送载荷搬移前后一字不差。

黄金样本 `fixtures/mrerp/golden-rules.json` 是在**搬移前**的代码上生成并单独提交的（`--write`），搬移提交只改导入路径，
这里用同一批固定输入重算后逐项比对：

- 导入表（xlsx）：赊销、现销、赊购（货 / 费）与客户 / 供应商 / 商品三类主档，直接调生成器；
- 推送全程：Cowork 的执行入口（`push.mrerp.runner.execute`）对假 MR.ERP 站点推一批，记下发出的每个请求（方法、路径、
  参数、表单字段、上传文件）与回执（去掉耗时毫秒），覆盖自建对方 / 商品、方向判定、菜单与主档默认值、回读、重号、停下。

xlsx 的 zip 成员带写入时刻（`zipfile` 取当时的本地时间），生成时注入固定时间；生成器里只影响提示的「今天」也固定。
每份 xlsx 记两样：成员级摘要（成员名、顺序、压缩方式、时间、解压后字节，与 zlib 实现无关）与整份文件的字节摘要
（同一个 zlib 才可比，黄金样本记了当时的 zlib 版本，版本不同时只比成员级）。

需要 bench 的 Python（requests、openpyxl）：在生产镜像容器里跑，做法见 `docs/work/2026-11-02-mrerp-core-rules.md`。
"""
import contextlib
import copy
import datetime as _datetime
import hashlib
import io
import json
import socket
import sys
import time as _time
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

try:
    import requests  # noqa: F401
    from test_cowork_mrerp_push import BASE, CHOSEN, OWN, PUBLIC, SUPPLIER, FakeMrErp, flat, purchase, sales
    from pearnly_cowork.push.mrerp import routing, runner, transport
    from pearnly_cowork.push.mrerp.xlsx import generator
except (ImportError, unittest.SkipTest) as exc:  # 需要 Frappe 环境（bench 的 Python）
    raise unittest.SkipTest(f"needs the Frappe bench environment: {exc}")

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "mrerp" / "golden-rules.json"
TODAY = _datetime.date(2026, 9, 26)
ZIP_TIME = (2026, 9, 26, 9, 30, 0, 5, 269, 0)

ITEMS = [
    {"name": "กระดาษ A4 80 แกรม", "qty": "5", "unit_price": "115.00", "amount": "575.00"},
    {"name": "หมึกพิมพ์ HP 85A", "qty": "2", "unit_price": "950.00", "amount": "1900.00"},
]
BUYER = {"buyer_name": "บริษัท ลูกค้า จำกัด", "buyer_tax": "0105562055501"}
SELLER = {"seller_name": "บริษัท ผู้ขาย จำกัด", "seller_tax": "0105561012345"}


def history(hid, **fields):
    base = {"invoice_number": f"INV-{hid}", "date": "2026-09-12", "subtotal": "2475.00", "vat": "173.25",
            "total_amount": "2648.25", "items": ITEMS}
    base.update(fields)
    return {"id": f"CWH-{hid:0>12}", "invoice_no": base["invoice_number"], "invoice_date": base["date"],
            "total_amount": base["total_amount"], "fields": base}


# 一家公司算好的导入值（推送时由主档算出放进 mappings 的那些键）。
VALUES = {
    "_mrerp_header": {"branch": "00000", "dept": "D001", "job": "00001", "store": "0001", "employee": "สมชาย ใจดี",
                      "area": "กรุงเทพ", "delivery": "ส่งเอง"},
    "_mrerp_customer_type": "C-01", "_mrerp_customer_account": "1130-01", "_mrerp_customer_salesman": "E01",
    "_mrerp_customer_area": "000001", "_mrerp_customer_shipping": "000001", "_mrerp_customer_branch": "00000",
    "_mrerp_customer_prefix": "บริษัท",
    "_mrerp_supplier_type": "2-11", "_mrerp_supplier_account": "2120-01", "_mrerp_supplier_branch": "00000",
    "_mrerp_supplier_prefix": "บริษัท",
    "_mrerp_product_category": "01", **{f"_mrerp_product_unit_{k}": "PCS" for k in ("count", "std", "buy", "sell")},
    "_mrerp_product_acc_rev": "4110-01", "_mrerp_product_acc_ret": "4110-01", "_mrerp_product_acc_dis": "4110-01",
    "_mrerp_product_acc_pur": "5100-01", "_mrerp_product_acc_purret": "5100-01", "_mrerp_product_acc_purdis": "5100-01",
    "_mrerp_product_acc_inv": "1150-30", "_mrerp_product_acc_cost": "5100-01", "_mrerp_product_supplier": "เงินสด",
    "_mrerp_receipt_account_cash": "1111-01", "_mrerp_receipt_account_bank": "1112-01",
}
PRODUCTS = {"products": [{"item_name": "กระดาษ A4 80 แกรม", "erp_code": "P-A4"},
                         {"item_name": "หมึกพิมพ์ HP 85A", "erp_code": "P-HP"}]}

SHEETS = [
    ("sales_credit", False, [dict(history("a1b2c3d4", **SELLER, **BUYER), client_id="7")],
     {"clients": [{"erp_type": "mrerp", "client_id": 7, "erp_code": "C0001"}], **PRODUCTS}),
    ("sales_cash", False, [history("0000beef", **SELLER, payment_method="cash")], PRODUCTS),
    ("sales_cash", False, [history("0000bee2", **SELLER, payment_method="transfer")], PRODUCTS),
    ("purchase", False, [history("00c0ffee", **SELLER, **BUYER)], {}),
    ("purchase", True, [history("00facade", **SELLER, **BUYER)], {}),
    ("customer_master", False, [{"code": "0105562055501", "name": "บริษัท ลูกค้า จำกัด",
                                 "tax_id": "0105562055501", "address": "99 ถนนสุขุมวิท"}], {}),
    ("supplier_master", False, [{"code": "0105561012345", "name": "บริษัท ผู้ขาย จำกัด",
                                 "tax_id": "0105561012345"}], {}),
    ("product_master", False, [{"code": "P3A1B2C4D5E6", "name": "กระดาษ A4 80 แกรม", "price": "115.00"}], {}),
]


class _FixedDate(_datetime.date):
    @classmethod
    def today(cls):
        return TODAY


class _ZipClock:
    """`zipfile` 里 `time.localtime(time.time())` 取成员时间：换成固定时刻，别的照真 `time`。"""

    def time(self):
        return 0.0

    def localtime(self, *args):
        return _time.struct_time(ZIP_TIME)

    def __getattr__(self, name):
        return getattr(_time, name)


@contextlib.contextmanager
def fixed_clock():
    with patch.object(zipfile, "time", _ZipClock()), patch.object(generator, "date", _FixedDate):
        yield


def xlsx_digest(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        members = [[info.filename, info.compress_type, list(info.date_time),
                    hashlib.sha256(archive.read(info.filename)).hexdigest()] for info in archive.infolist()]
    return {"members": hashlib.sha256(json.dumps(members).encode()).hexdigest(),
            "bytes": hashlib.sha256(blob).hexdigest(), "size": len(blob)}


def _plain(value):
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    return value if isinstance(value, (str, int, float, bool, type(None))) else repr(value)


def _file_part(part):
    """上传的一项：（文件名, 内容, 类型…）；xlsx 记摘要，其余照原样。"""
    if not isinstance(part, tuple):
        return _plain(part)
    blob = part[1] if len(part) > 1 else None
    content = xlsx_digest(blob) if isinstance(blob, bytes) and str(part[0]).endswith(".xlsx") else _plain(blob)
    return [_plain(part[0]), content, *[_plain(p) for p in part[2:]]]


def run_push(site, histories, mappings=None, password="test01"):
    """Cowork 的执行入口推一批：返回（发出的每个请求, 回执）。"""
    sent = []

    def send(session, method, url, **kwargs):
        entry = {"method": method, "url": url.replace(BASE, "")}
        for key in ("params", "data", "headers"):
            if kwargs.get(key) is not None:
                entry[key] = _plain(kwargs[key])
        if kwargs.get("files"):
            files = kwargs["files"]
            entry["files"] = [[name, _file_part(part)]
                              for name, part in (files.items() if isinstance(files, dict) else files)]
        sent.append(entry)
        r = site(session, method, url, **kwargs)
        for hook in session.hooks["response"]:
            hook(r)
        return r

    payload = {"histories": histories, "mappings": {"_own_tax_id": OWN, "_mrerp_defaults": dict(CHOSEN),
                                                    **(mappings or {})}}
    with patch.object(requests.Session, "request", autospec=True, side_effect=send), \
            patch.object(socket, "getaddrinfo", return_value=PUBLIC), \
            patch.object(transport, "account_lock", lambda *a, **k: contextlib.nullcontext(True)), fixed_clock():
        out = runner.execute(payload, {"account_set": "15:1"}, {"username": "test01", "password": password},
                             BASE, TODAY)
    out = _plain(out)
    (out.get("meta") or {}).pop("elapsed_ms", None)
    return {"requests": sent, "outcome": out}


def _invoice(row):
    return routing._gen.derive_mrerp_invoice_no(row)


def push_cases():
    goods, expense = purchase("b1", posting_item_type_manual="goods"), purchase("b2", posting_item_type_manual="expense")
    menus = {"impaptran": [("67", "ซื้อ-เดิม", False), ("68", "ซื้อ", True), ("454", "ซื้อ-ค่าใช้จ่าย", True)]}
    both = {"_mrerp_menus": {"purchase": "68", "purchase_expense": "454"}}
    dup = purchase("b5")
    return {
        "sales_credit_creates_customer_and_product": (FakeMrErp(), [sales("a1", posting_payment_manual="credit")], {}),
        "sales_credit_mapped_customer": (FakeMrErp(), [sales("a2", client_id="CWC-9")],
                                         {"clients": [{"erp_type": "mrerp", "client_id": "CWC-9", "erp_code": "C0001"}],
                                          "products": [{"item_name": "กระดาษ A4", "erp_code": "P-A4"}]}),
        "sales_cash": (FakeMrErp(), [sales("a3", posting_payment_manual="cash")],
                       {"products": [{"item_name": "กระดาษ A4", "erp_code": "P-A4"}]}),
        "sales_cash_by_transfer": (FakeMrErp(), [sales("a4", posting_payment_manual="cash", payment_method="transfer")],
                                   {"products": [{"item_name": "กระดาษ A4", "erp_code": "P-A4"}]}),
        "purchase_goods": (FakeMrErp(menus=menus, prefix={"454": "PN"}), [goods], both),
        "purchase_expense": (FakeMrErp(menus=menus, prefix={"454": "PN"}), [expense], both),
        "purchase_single_linked_menu": (FakeMrErp(), [purchase("b3")], {}),
        "direction_from_company_tax": (FakeMrErp(company_tax=OWN),
                                       [flat("c1", None, seller_name="บริษัท อื่น จำกัด", seller_tax=SUPPLIER,
                                             buyer_name="บริษัท เรา จำกัด", buyer_tax=OWN)], {}),
        "several_linked_menus_ask_first": (FakeMrErp(menus=menus), [purchase("b4")], {}),
        "already_pushed": (FakeMrErp(importpc="2", notes=[(_invoice(dup), "เลขที่ดังกล่าวมีอยู่ในระบบแล้ว")],
                                     listed={"68": ["PG" + _invoice(dup)]}), [dup], {}),
        "report_row_failed": (FakeMrErp(importpc="2", notes=[(_invoice(purchase("b6")), "ไม่พบรหัสสินค้า")]),
                              [purchase("b6")], {}),
        "master_mismatch_asks_again": (FakeMrErp(importpc="2", notes=[(_invoice(purchase("b7")), "ไม่พบข้อมูลแผนก")]),
                                       [purchase("b7")], {}),
        "accepted_not_listed_unknown": (FakeMrErp(lands=False), [purchase("b8")], {}),
        "account_not_chosen_stops": (FakeMrErp(), [sales("a6")],
                                     {"_mrerp_defaults": {k: v for k, v in CHOSEN.items() if k != "ar_account"}}),
        "wrong_password": (FakeMrErp(), [purchase("b9")], {}, "wrong"),
    }


def compute():
    record = {"zlib": zlib.ZLIB_VERSION, "xlsx": {}, "push": {}, "preflight": {}}
    with fixed_clock():
        for kind, expense, rows, mappings in SHEETS:
            name = f"{kind}{'_expense' if expense else ''}"
            if name in record["xlsx"]:
                name += "_" + rows[0]["id"][-4:]
            blob = generator.generate_xlsx(copy.deepcopy(rows), {**VALUES, **copy.deepcopy(mappings)},
                                           sheet_kind=kind, expense=expense)
            record["xlsx"][name] = xlsx_digest(blob)
        for kind, expense, rows, mappings in SHEETS[:5]:
            for row in rows:
                record["preflight"][row["id"]] = _plain({
                    "invoice_no": generator.derive_mrerp_invoice_no(row),
                    "sales_credit": generator.validate_history_for_sales_credit(copy.deepcopy(row), mappings),
                    "doc_type": routing.choose_doc_type(
                        row, {}, own_tax_id=(SELLER["seller_tax"] if kind.startswith("sales") else BUYER["buyer_tax"]),
                        today=TODAY)})
    for name, case in push_cases().items():
        site, histories, mappings, *password = case
        record["push"][name] = run_push(site, histories, mappings, *password)
    return record


class GoldenRules(unittest.TestCase):
    """同一批输入，导入文件与推送载荷与搬移前逐字节相同。"""

    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cls.now = compute()

    def test_every_import_sheet_is_unchanged(self):
        same_zlib = self.golden["zlib"] == self.now["zlib"]
        self.assertEqual(sorted(self.now["xlsx"]), sorted(self.golden["xlsx"]))
        for name, expected in self.golden["xlsx"].items():
            with self.subTest(sheet=name):
                got = self.now["xlsx"][name]
                self.assertEqual(got["members"], expected["members"])
                if same_zlib:
                    self.assertEqual((got["bytes"], got["size"]), (expected["bytes"], expected["size"]))

    def test_invoice_numbers_preflight_and_document_types_are_unchanged(self):
        self.assertEqual(self.now["preflight"], self.golden["preflight"])

    def test_every_push_sends_the_same_requests_and_settles_the_same(self):
        self.assertEqual(sorted(self.now["push"]), sorted(self.golden["push"]))
        same_zlib = self.golden["zlib"] == self.now["zlib"]
        for name, expected in self.golden["push"].items():
            with self.subTest(push=name):
                got = copy.deepcopy(self.now["push"][name])
                expected = copy.deepcopy(expected)
                if not same_zlib:  # 整份文件的字节与大小随 zlib 实现变，只比成员级
                    for entry in (*got["requests"], *expected["requests"]):
                        for _name, part in entry.get("files") or []:
                            if isinstance(part, list) and isinstance(part[1], dict) and "members" in part[1]:
                                part[1] = part[1]["members"]
                    for out in (got["outcome"], expected["outcome"]):
                        (out.get("meta") or {}).pop("xlsx_size_bytes", None)
                self.assertEqual(got, expected)


if __name__ == "__main__":
    if "--write" in sys.argv:
        GOLDEN.write_text(json.dumps(compute(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {GOLDEN}")
    else:
        unittest.main()
