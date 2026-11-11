"""ERP 接 MR.ERP 导入直写（评估 2026-11-02 §13 第 6 步）的逐字节锁：ERP 这条路生成的导入表与发出的请求与 Cowork 一字不差。

- **ERP 单据 → 规范载荷**有固定样本：采购（赊购货品）、采购（赊购费用）、销售（赊销）、销售（现销）四张 ERP 单（ERP 交给 Express
  写账规则的同一个形状 `express.contracts.document`），经 `pearnly_erp_portal.mrerp.contracts` 换成的规范载荷与
  `fixtures/mrerp/erp-direct-golden.json` 逐字节相同。
- **同一份规范载荷 + 同样的菜单 / 默认值**：ERP 导入用的执行（`pearnly_core.mrerp.push.execute.execute`，`mrerp.push.run` 调它）与
  Cowork 的执行入口（`push.mrerp.runner.execute`）对同一个假 MR.ERP 站点推，发出的每个请求（方法、路径、参数、表单、上传的 xlsx 成员级
  与整份摘要）与回执相同，且与黄金样本相同；Cowork 的黄金样本 3（`test_mrerp_rules_golden.py` 的 15 次推送）经 ERP 用的执行再跑一遍
  也逐项相同。

需要 bench 的 Python（requests、openpyxl）：在生产镜像容器里跑（候选检查离线车道），做法同 `test_mrerp_rules_golden.py`。
``--write`` 重写黄金样本（只在规范载荷的形状有意改变时，且要另外说明）。
"""
import copy
import contextlib
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import requests  # noqa: F401
    import test_mrerp_rules_golden as golden
    from test_cowork_mrerp_push import BASE, CHOSEN, OWN, PUBLIC, SUPPLIER, CUSTOMER, FakeMrErp
    from pearnly_core.mrerp import account as mrerp_account
    from pearnly_core.mrerp.push import execute as core_execute, transport
    from pearnly_cowork.push.mrerp import runner
    from pearnly_erp_portal.express import contracts as E
    from pearnly_erp_portal.mrerp import contracts as M
except (ImportError, unittest.SkipTest) as exc:  # 需要 Frappe 环境（bench 的 Python）
    raise unittest.SkipTest(f"needs the Frappe bench environment: {exc}")

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "mrerp" / "erp-direct-golden.json"
COMPANY = {"tax_id": OWN, "branch": "00000"}
LINES = [{"name": "กระดาษ A4", "code": "", "unit": "รีม", "qty": "2", "amount": "1000.00", "stock": True},
         {"name": "หมึกพิมพ์", "code": "", "unit": "ชิ้น", "qty": "1", "amount": "500.00", "stock": True}]


def body(ref, direction, paid, party, **kw):
    return E.document(ref=ref, direction=direction, doc_date="2026-09-12", number=kw.pop("number", ref.split("@")[0]),
                      company=COMPANY, party=party, paid=paid, has_vat=True, total="1605.00", vat="105.00",
                      lines=copy.deepcopy(LINES), **kw)


SUPPLIER_PARTY = {"code": "", "name": "บริษัท ผู้ขาย จำกัด", "tax_id": SUPPLIER, "branch": "00000"}
CUSTOMER_PARTY = {"code": "", "name": "บริษัท ลูกค้า จำกัด", "tax_id": CUSTOMER, "branch": "00000"}
# （名字, ERP 单据, 单据种类, 菜单, 默认值, 假站点）
DOCS = [
    ("purchase_goods_credit", body("PUR-2026-00012@20260912093000000000", "purchase", False, SUPPLIER_PARTY,
                                   number="IV6909-0012"), "goods", {"purchase": "68"}, dict(CHOSEN),
     lambda: FakeMrErp(menus={"impaptran": [("68", "ซื้อ", True), ("454", "ซื้อ-ค่าใช้จ่าย", True)]})),
    ("purchase_expense_credit", body("PUR-2026-00013@20260912100000000000", "purchase", False, SUPPLIER_PARTY,
                                     number="IV6909-0013"), "expense", {"purchase_expense": "454"}, dict(CHOSEN),
     lambda: FakeMrErp(menus={"impaptran": [("68", "ซื้อ", True), ("454", "ซื้อ-ค่าใช้จ่าย", True)]},
                       prefix={"454": "PN"})),
    ("sales_credit", body("SAL-2026-00007@20260912110000000000", "sales", False, CUSTOMER_PARTY), "receipt", {},
     dict(CHOSEN), FakeMrErp),
    ("sales_cash", body("SAL-2026-00008@20260912113000000000", "sales", True, CUSTOMER_PARTY), "receipt", {},
     dict(CHOSEN), FakeMrErp),
]


def canonical():
    return {name: M.payload(M.history(doc, company_name="บริษัท เรา จำกัด", doc_kind=kind), own_tax_id=OWN,
                            menus=menus, defaults=defaults)
            for name, doc, kind, menus, defaults, _site in DOCS}


def recorded(fn, site, payload, password="test01"):
    """``fn``（某一边的执行）对假站点推一份载荷：（发出的每个请求, 回执），记法同 Cowork 黄金样本。"""
    sent = []

    def send(session, method, url, **kwargs):
        entry = {"method": method, "url": url.replace(BASE, "")}
        for key in ("params", "data", "headers"):
            if kwargs.get(key) is not None:
                entry[key] = golden._plain(kwargs[key])
        if kwargs.get("files"):
            files = kwargs["files"]
            entry["files"] = [[name, golden._file_part(part)]
                              for name, part in (files.items() if isinstance(files, dict) else files)]
        sent.append(entry)
        r = site(session, method, url, **kwargs)
        for hook in session.hooks["response"]:
            hook(r)
        return r

    with patch.object(requests.Session, "request", autospec=True, side_effect=send), \
            patch.object(socket, "getaddrinfo", return_value=PUBLIC), \
            patch.object(mrerp_account, "account_lock", lambda *a, **k: contextlib.nullcontext(True)), golden.fixed_clock():
        out = fn(copy.deepcopy(payload), {"account_set": "15:1"}, {"username": "test01", "password": password},
                 BASE, golden.TODAY)
    out = golden._plain(out)
    (out.get("meta") or {}).pop("elapsed_ms", None)
    return {"requests": sent, "outcome": out}


def compute():
    payloads = canonical()
    push = {name: recorded(core_execute.execute, site(), payloads[name]) for name, _d, _k, _m, _f, site in DOCS}
    return {"zlib": golden.zlib.ZLIB_VERSION, "canonical": payloads, "push": push}


def _members_only(record):
    record = copy.deepcopy(record)
    for entry in record["requests"]:
        for _name, part in entry.get("files") or []:
            if isinstance(part, list) and isinstance(part[1], dict) and "members" in part[1]:
                part[1] = part[1]["members"]
    (record["outcome"].get("meta") or {}).pop("xlsx_size_bytes", None)
    return record


class ErpDirectImportGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cls.now = compute()

    def test_erp_documents_become_the_same_canonical_payload(self):
        for name, expected in self.golden["canonical"].items():
            with self.subTest(document=name):
                self.assertEqual(json.dumps(self.now["canonical"][name], ensure_ascii=False, sort_keys=True),
                                 json.dumps(expected, ensure_ascii=False, sort_keys=True))
        self.assertEqual(sorted(self.now["canonical"]), sorted(self.golden["canonical"]))

    def test_erp_import_sends_the_same_requests_as_the_golden_sample(self):
        same_zlib = self.golden["zlib"] == self.now["zlib"]
        for name, expected in self.golden["push"].items():
            with self.subTest(document=name):
                got = self.now["push"][name]
                self.assertEqual(got if same_zlib else _members_only(got),
                                 expected if same_zlib else _members_only(expected))

    def test_cowork_sends_byte_for_byte_the_same_import_for_the_same_payload(self):
        for name, _doc, _kind, _menus, _defaults, site in DOCS:
            with self.subTest(document=name):
                payload = self.now["canonical"][name]
                self.assertEqual(recorded(runner.execute, site(), payload), recorded(core_execute.execute, site(), payload))

    def test_cowork_golden_sample_runs_the_same_through_the_erp_execution(self):
        """Cowork 黄金样本 3 的 15 次推送：换成 ERP 用的执行，结论与请求逐项相同。"""
        expected = json.loads(golden.GOLDEN.read_text(encoding="utf-8"))
        same_zlib = expected["zlib"] == golden.zlib.ZLIB_VERSION
        with patch.object(runner, "execute", core_execute.execute):
            for name, case in golden.push_cases().items():
                site, histories, mappings, *password = case
                with self.subTest(push=name):
                    got = golden.run_push(site, histories, mappings, *password)
                    want = expected["push"][name]
                    self.assertEqual(got if same_zlib else _members_only(got), want if same_zlib else _members_only(want))

    def test_the_canonical_payload_is_plain_json(self):
        for name, payload in self.now["canonical"].items():
            with self.subTest(document=name):
                self.assertEqual(json.loads(json.dumps(payload, ensure_ascii=False)), payload)
                self.assertEqual(sorted(payload["mappings"]), ["_mrerp_defaults", "_mrerp_menus", "_own_tax_id",
                                                               "accounts", "clients"])


if __name__ == "__main__":
    if "--write" in sys.argv:
        GOLDEN.write_text(json.dumps(compute(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {GOLDEN}")
    else:
        unittest.main()
