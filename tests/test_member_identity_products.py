"""控制面成员登记（`Pearnly Member Identity`）要覆盖成员服务认的每个产品，并按站点实际签到的账号池建号。

2026-10-18：Cowork 9-26 起有自己的账号池，成员服务按 cowork 写登记，登记表的产品选项却只有 erp，写登记时被表校验拒绝，
所有 Cowork 租户「团队管理」加成员都只回「请稍后重试」。同一天查实第二处：建号按产品选池，而老租户 testcowork
签到的是业务池，建在 Cowork 池的号它登不进去；站点发建号 / 改密码时要带本站签发方，控制面按它选池并记在登记上。
不连库、不依赖 Frappe：读源码与表定义。"""
import ast
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTROL = ROOT / "apps/pearnly_control/pearnly_control"
CLIENT = ROOT / "apps/pearnly_core/pearnly_core/account/member_client.py"
DOCTYPE = CONTROL / "pearnly_control/doctype/pearnly_member_identity/pearnly_member_identity.json"


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(tree, name):
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)


def _products():
    for node in _tree(CONTROL / "account/member_service.py").body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "PRODUCTS" for t in node.targets):
            return set(ast.literal_eval(node.value))
    raise AssertionError("member_service.PRODUCTS not found")


def _calls(function, name):
    return [node for node in ast.walk(function)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == name]


def _passes(call, name):
    return any(getattr(arg, "id", "") == name for arg in call.args) or any(
        keyword.arg == name and getattr(keyword.value, "id", "") == name for keyword in call.keywords)


class MemberIdentityProductTests(unittest.TestCase):
    def test_the_identity_record_accepts_every_product_the_member_service_serves(self):
        doctype = json.loads(DOCTYPE.read_text(encoding="utf-8"))
        field = next(f for f in doctype["fields"] if f["fieldname"] == "product")
        options = {line for line in field["options"].split("\n") if line}
        self.assertTrue(_products(), "member_service serves no product")
        self.assertLessEqual(_products(), options)
        # 表定义带修改时间：改了选项，migrate 才会把新定义同步进库
        self.assertIn("modified", doctype)


class MemberIdentityPoolTests(unittest.TestCase):
    def test_the_identity_record_keeps_the_pool_it_was_created_in(self):
        doctype = json.loads(DOCTYPE.read_text(encoding="utf-8"))
        field = next((f for f in doctype["fields"] if f["fieldname"] == "realm"), None)
        self.assertIsNotNone(field, "Pearnly Member Identity has no realm field")
        self.assertEqual(field["fieldtype"], "Data")
        self.assertEqual(field.get("read_only"), 1)
        self.assertIn("realm", doctype["field_order"])

    def test_site_create_and_reset_send_the_site_issuer(self):
        tree = _tree(CLIENT)
        payloads = [node for node in ast.walk(_function(tree, "_call")) if isinstance(node, ast.Dict)]
        sent = {key.value: value for payload in payloads for key, value in zip(payload.keys, payload.values)
                if isinstance(key, ast.Constant)}
        self.assertIn("issuer", sent, "create / reset-password do not send the site issuer")
        self.assertEqual(getattr(sent["issuer"].func, "id", ""), "_issuer")
        # 与 LINE 关联同一个来源：本站 `pearnly_oidc_issuer`
        self.assertIn("pearnly_oidc_issuer", ast.unparse(_function(tree, "_issuer")))

    def test_control_passes_the_issuer_to_create_and_reset(self):
        tree = _tree(CONTROL / "account/member_service.py")
        execute = _function(tree, "execute")
        for name in ("create", "reset_password"):
            calls = _calls(execute, name)
            self.assertTrue(calls, f"execute does not call {name}")
            self.assertTrue(all(_passes(call, "issuer") for call in calls), f"execute drops the issuer for {name}")
            self.assertIn("issuer", [arg.arg for arg in _function(tree, name).args.args])


if __name__ == "__main__":
    unittest.main()
