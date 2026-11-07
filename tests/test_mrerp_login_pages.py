"""MR.ERP 的密码不在网页上收（ADR-026 第 7 条，评估 §14.4，2026-11-02）：页面一侧的离线检查（读源码，不起浏览器）。

- CC 对话页（frontend/assistant）：没有任何密码输入框；MR.ERP 连接只在设置 → 连接最上面那一行（用户 11-07，components/mrerp-connect.tsx），
  弹窗只收网址，登录用共用组件 MrErpLogin（显示「已授权：用户名 · 电脑 · 时间」与「在 CC 桌面版中授权」）；组件与桥只传一次性票，
  不碰密码；浏览器里按钮不可用；定时任务的 MR.ERP「连接」直接打开设置的连接一节；
- Cowork Desk（targets.js）：MR.ERP「连接」不再自己弹窗，新开一页到 CC 设置的连接一节（没有密码字段、不往 configure 交凭据）；
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
CONNECT = SPA / "components/mrerp-connect.tsx"
SETTINGS = SPA / "components/settings.tsx"
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
        text = CONNECT.read_text(encoding="utf-8")
        dialog = text.split("export const MrerpConnect: FC", 1)[1]
        self.assertNotIn("password", dialog.lower())
        self.assertNotIn("credentials", dialog)
        self.assertIn("<MrErpLogin", dialog)
        self.assertIn("config: { system_url: url }", dialog)
        # 接口前缀只来自产品声明（connect.mrerp），不写死某个产品；目录刷新也走它
        self.assertIn("useProjects((s) => s.connect.mrerp", text)
        self.assertNotIn("pearnly_cowork", text)
        self.assertNotIn("pearnly_erp_portal", text)
        self.assertIn('postMethod(api + "request_refresh", { name, request_key: requestKey() })', text)

    def test_only_one_entry_settings_connections(self):
        # 设置 → 连接最上面一行（桌面版「此电脑 → 连接」与浏览器的「连接」）；定时任务不再有自己的 MR.ERP 弹窗
        settings = SETTINGS.read_text(encoding="utf-8")
        self.assertEqual(settings.count("<MrerpRow />"), 2)
        self.assertIn("<MrerpRow />\n      <PoolComputers />", settings)
        self.assertIn('const wanted = new URL(location.href).searchParams.get("settings");', settings)
        tasks = TASKS.read_text(encoding="utf-8")
        self.assertNotIn("function MrerpConnect(", tasks)
        self.assertNotIn("<MrErpLogin", tasks)
        self.assertIn('const connectTo = (kind: string) => (kind === "mrerp" ? openSettings("connections") : setConnectOpen(kind));', tasks)
        self.assertNotIn("setConnectOpen(target)", tasks)
        self.assertNotIn("setConnectOpen(a.adapter)", tasks)

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
    def test_the_desk_connect_opens_cc_settings_and_sends_no_credentials(self):
        text = DESK.read_text(encoding="utf-8")
        dialog = block(text, "function connect_dialog(", "\n\t}\n")
        self.assertIn('window.open("/assistant?settings=connections", "_blank", "noopener");', dialog)
        self.assertNotIn("p.dialog(", dialog)
        self.assertNotRegex(text, r'fieldtype:\s*"Password"')
        self.assertNotIn("credentials:", text)

if __name__ == "__main__":
    unittest.main()
