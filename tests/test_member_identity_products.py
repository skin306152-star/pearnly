"""控制面成员登记（`Pearnly Member Identity`）的产品选项要覆盖成员服务认的每个产品（`member_service.PRODUCTS`）。

2026-10-18：Cowork 9-26 起有自己的账号池，成员服务按 cowork 写登记，登记表的产品选项却只有 erp，写登记时被表校验拒绝，
所有 Cowork 租户「团队管理」加成员都只回「请稍后重试」。不连库、不依赖 Frappe：读源码与表定义。"""
import ast
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTROL = ROOT / "apps/pearnly_control/pearnly_control"


def _products():
    tree = ast.parse((CONTROL / "account/member_service.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "PRODUCTS" for t in node.targets):
            return set(ast.literal_eval(node.value))
    raise AssertionError("member_service.PRODUCTS not found")


class MemberIdentityProductTests(unittest.TestCase):
    def test_the_identity_record_accepts_every_product_the_member_service_serves(self):
        doctype = json.loads((CONTROL / "pearnly_control/doctype/pearnly_member_identity/pearnly_member_identity.json")
                             .read_text(encoding="utf-8"))
        field = next(f for f in doctype["fields"] if f["fieldname"] == "product")
        options = {line for line in field["options"].split("\n") if line}
        self.assertTrue(_products(), "member_service serves no product")
        self.assertLessEqual(_products(), options)
        # 表定义带修改时间：改了选项，migrate 才会把新定义同步进库
        self.assertIn("modified", doctype)


if __name__ == "__main__":
    unittest.main()
