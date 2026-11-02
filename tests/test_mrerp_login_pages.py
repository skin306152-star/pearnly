"""MR.ERP 的密码不在网页上收（ADR-026 第 7 条，评估 §14.4，2026-11-02）：页面一侧的离线检查（读源码，不起浏览器）。

- CC 对话页（frontend/assistant）：没有任何密码输入框；MR.ERP 连接弹窗只收网址，登录用共用组件 MrErpLogin（显示
  「已授权：用户名 · 电脑 · 时间」与「在 CC 桌面版中授权」）；组件与桥只传一次性票，不碰密码；浏览器里按钮不可用；
- Cowork Desk（targets.js）：MR.ERP 连接弹窗没有 Password 字段、不往 configure 交凭据，授权按钮在 Desk 里不可用；
- 两个产品用同一组件：组件按产品的通道要票（Cowork / 共用通道两条接口）。
DMS 的 Desk 连接页（DMS 账号）不在本需求范围，不查。
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPA = ROOT / "frontend/assistant/src"
COMPONENT = SPA / "components/mrerp-login.tsx"
TASKS = SPA / "components/scheduled-tasks.tsx"
DESKTOP = SPA / "lib/desktop.ts"
DESK = ROOT / "apps/pearnly_cowork/pearnly_cowork/public/desk/targets.js"


def block(text, start, end):
    return text[text.index(start):text.index(end, text.index(start))]


class CcPage(unittest.TestCase):
    def test_no_password_input_anywhere_in_the_cc_page(self):
        for path in SPA.rglob("*.tsx"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotRegex(text, r'type=["{]?["\']?password')

    def test_the_mrerp_connect_dialog_takes_only_the_address_and_uses_the_shared_component(self):
        dialog = block(TASKS.read_text(encoding="utf-8"), "function MrerpConnect(", "function HelperConnect(")
        self.assertNotIn("password", dialog.lower())
        self.assertNotIn("credentials", dialog)
        self.assertNotIn('tr("Account")', dialog)
        self.assertIn("<MrErpLogin", dialog)
        self.assertIn("config: { system_url: url }", dialog)

    def test_the_component_passes_only_a_ticket_and_is_disabled_outside_cc(self):
        text = COMPONENT.read_text(encoding="utf-8")
        self.assertNotRegex(text.lower(), r"password\b(?!s)")
        self.assertIn("disabled={!app || busy}", text)
        self.assertIn("st.mrerpAuthorized(detail)", text)
        # 两个产品同一组件：Cowork 走 Cowork 通道，ERP 走共用通道
        self.assertIn('cowork: "pearnly_cowork.companion.api.mrerp_login_ticket"', text)
        self.assertIn('core: "pearnly_core.cc_desktop.api.mrerp_login_ticket"', text)
        bridge = block(DESKTOP.read_text(encoding="utf-8"), "export async function authorizeMrErp(", "\n}\n")
        self.assertIn('api.call("mrerp_authorize", { ticket })', bridge)
        self.assertNotIn("password", bridge.lower())


class Desk(unittest.TestCase):
    def test_the_desk_dialog_has_no_password_field_and_sends_no_credentials(self):
        dialog = block(DESK.read_text(encoding="utf-8"), "function connect_dialog(", "\n\t}\n")
        self.assertNotRegex(dialog, r'fieldtype:\s*"Password"')
        self.assertNotIn("credentials:", dialog)
        self.assertIn('__("connect.mrerp.authorizeInCC")', dialog)
        self.assertIn('d.fields_dict.authorize.$input?.prop("disabled", true)', dialog)
        self.assertTrue(re.search(r'__\("connect\.mrerp\.(authorized|notAuthorized)"', dialog))


if __name__ == "__main__":
    unittest.main()
