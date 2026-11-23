"""MR.ERP 的密码只在 CC 桌面版的「连接 MR.ERP」弹窗里收、只经本机桥交出（ADR-026 第 7 条，2026-11-02；11-23 用户「第一个弹窗
不行吗，非要再设计一个单独的输入框干啥」，主会话审定）：页面一侧的离线检查（读源码，不起浏览器）。

- CC 对话页（frontend/assistant）：整页只有「连接 MR.ERP」弹窗（components/mrerp-connect.tsx）这一个密码框；MR.ERP 连接只在设置 →
  连接最上面那一行（用户 11-07）。弹窗：系统地址 + 用户名 + 密码，「连接」一步存地址并授权；浏览器、手机里两格不可用（没有本机桥），
  旧桌面版提示更新。密码不进网络请求（postMethod / fetch，要票只带连接号与设备密钥）、不进存储（localStorage / sessionStorage）、
  不进错误上报（reportCaught 只带错误本身）、不留在 state（交出前清空）；只经本机桥 ``mrerp_authorize`` 交给 CC；
- 定时任务的 MR.ERP「连接」直接打开设置的连接一节；
- Cowork Desk（targets.js）：MR.ERP「连接」不再自己弹窗，新开一页到 CC 设置的连接一节（没有密码字段、不往 configure 交凭据）；
- 两个产品用同一组件：组件按产品的通道要票（Cowork / 共用通道两条接口）。
CC 本机一侧（只回成没成、日志里没有密码）见 companion/tests/test_mrerp_login.py。DMS 的 Desk 连接页（DMS 账号）不在本需求范围。
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
    def test_the_only_password_input_in_the_cc_page_is_the_mrerp_connect_dialog(self):
        found = {path.name: len(re.findall(r'type=["{]?["\']?password', path.read_text(encoding="utf-8")))
                 for path in SPA.rglob("*.tsx")}
        self.assertEqual({name: n for name, n in found.items() if n}, {"mrerp-connect.tsx": 1})

    def test_the_mrerp_connect_dialog_takes_the_login_and_authorizes_in_one_step(self):
        text = CONNECT.read_text(encoding="utf-8")
        dialog = text.split("export const MrerpConnect: FC", 1)[1]
        self.assertIn("config: { system_url: url }", dialog)
        self.assertIn('type="password" value={password} disabled={!canLogin || busy} autoComplete="new-password"', dialog)
        self.assertIn("disabled={!canLogin || busy}", dialog.split('data-slot="cc-mrerp-username"')[0].rsplit("<Input", 1)[1])
        self.assertIn("const canLogin = app && !!mrerpLoginInline;", dialog)
        self.assertIn("{app && !mrerpLoginInline && (", dialog)   # 旧桌面版：提示更新
        self.assertIn("<MrErpLoginStatus credential={current?.credential} local={local} />", dialog)
        # 一个按钮：先存地址，再经本机桥授权；不再另弹原生小窗、不再有单独的「在 CC 桌面版中授权」
        submit = block(dialog, "const submit = async () => {", "\n  };\n")
        self.assertLess(submit.index("await save()"), submit.index("await authorizeConnection("))
        # 双击只进一次（11-23 testerp 多出一条空连接）：第一句就挡，收尾放开；服务端另按系统地址复用
        self.assertTrue(submit.startswith("const submit = async () => {\n    if (running.current) return;\n    running.current = true;"))
        self.assertIn("running.current = false;", submit)
        self.assertNotIn("<MrErpLogin ", text)
        # 接口前缀只来自产品声明（connect.mrerp），不写死某个产品；目录刷新也走它
        self.assertIn("useProjects((s) => s.connect.mrerp", text)
        self.assertNotIn("pearnly_cowork", text)
        self.assertNotIn("pearnly_erp_portal", text)
        self.assertIn('postMethod(api + "request_refresh", { name, request_key: requestKey() })', text)

    def test_the_password_goes_only_to_the_desktop_bridge(self):
        connect, component = CONNECT.read_text(encoding="utf-8"), COMPONENT.read_text(encoding="utf-8")
        dialog = connect.split("export const MrerpConnect: FC", 1)[1]
        submit = block(dialog, "const submit = async () => {", "\n  };\n")
        # 交出前就清空 state：之后哪一步出错，密码都不留在页面里
        self.assertLess(submit.index('setPassword("")'), submit.index("await save()"))
        for name, text in (("mrerp-connect.tsx", connect), ("mrerp-login.tsx", component)):
            with self.subTest(file=name):
                # 不进网络请求：postMethod / getMethod / fetch 的参数里没有密码
                for call in re.findall(r"(?:postMethod|getMethod|fetch)\(.*?\)", text, flags=re.S):
                    self.assertNotIn("password", call.lower(), call)
                # 不进存储、不进错误上报、不打控制台
                self.assertNotRegex(text, r"(localStorage|sessionStorage)")
                for report in re.findall(r"reportCaught\([^)]*\)|withReport\([^)]*\)|console\.\w+\([^)]*\)", text):
                    self.assertNotIn("password", report.lower(), report)
        # 要票只带连接号与设备密钥；密码只交给本机桥
        self.assertIn("{ connection, device_key: key }", component)
        self.assertIn("return authorizeMrErp(issued.ticket, username, password);", component)
        self.assertIn('cowork: "pearnly_cowork.companion.api.mrerp_login_ticket"', component)
        self.assertIn('core: "pearnly_core.cc_desktop.api.mrerp_login_ticket"', component)
        bridge = block(DESKTOP.read_text(encoding="utf-8"), "export async function authorizeMrErp(", "\n}\n")
        self.assertIn('api.call("mrerp_authorize", { ticket, username, password })', bridge)
        self.assertNotRegex(bridge, r"(postMethod|fetch|localStorage|sessionStorage|reportCaught|console)")
        self.assertIn("if (!api) return null;", bridge)   # 浏览器里没有桥：交不出去

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
