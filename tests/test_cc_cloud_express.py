"""CC「设置 → 连接」的「云端 Express」一行（步 E，用户 11-16）：页面一侧的离线检查（读源码，不起浏览器），同 tests/test_assistant_ui.py。

- 挂在 Connections 里「Express 位置」那几行之后、只在 CC 桌面版（api）里；照 cc-settings-row / cc-status-line 的写法；
- 状态五种：未连接 / 正在连接 / 已连接 / Express 登录失败 / 账号已在别处登录，词条沿用现有的；
- 「连接」弹窗：地址、账号由桥预填（cloud_prefill）、能改；**页面没有密码框、不把密码交给桥**（ADR-026 第 7 条，密码由桥弹 CC 原生小窗收）；
- 按钮调对桥动作：cloud_status / cloud_prefill / cloud_connect {host, port, user} / cloud_disconnect；失败不自动重试；
- 不往 localStorage、偏好、对话状态写任何东西；云端位置只读、没有「添加文件夹」；四语词条齐全。
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPA = ROOT / "frontend/assistant/src"
ROW = SPA / "components/cloud-express.tsx"
SETTINGS = SPA / "components/settings.tsx"
I18N = SPA / "i18n.ts"


def read(path):
    return path.read_text(encoding="utf-8")


class CloudExpressRow(unittest.TestCase):
    def test_the_row_sits_under_the_express_places_in_the_desktop_connections(self):
        settings = read(SETTINGS)
        connections = settings.split("function Connections(", 1)[1].split("\nfunction ", 1)[0]
        self.assertEqual(settings.count("<CloudExpressRow api={api} />"), 1)
        self.assertIn("<CloudExpressRow api={api} />", connections)
        # 在「Express 位置」各行之后、以前授权的文件夹之前
        self.assertLess(connections.index('"remove_place", { path }'), connections.index("<CloudExpressRow"))
        self.assertLess(connections.index("<CloudExpressRow"), connections.index("companyFolders && legacy.length > 0"))
        self.assertIn('import { CloudExpressRow } from "@/components/cloud-express";', settings)

    def test_each_state_shows_an_existing_status_word(self):
        text = read(ROW)
        line = text.split("export function cloudStatusLine(", 1)[1].split("\n}\n", 1)[0]
        for state, word, tone in (
            ('"connecting"', "st.status.connecting", '"connecting"'),
            ('"connected"', "st.status.online", '"online"'),
            ('"login_failed"', "op.reason.login_failed", '"attention"'),
            ('"replaced"', "op.reason.account_in_use", '"attention"'),
        ):
            self.assertIn(f"if (state === {state}) return {{ text: {word}, tone: {tone} }};", line)
        self.assertIn('return { text: st.notConnected, tone: "offline" };', line)
        self.assertIn('className="cc-status-line text-caption" data-status={line.tone}', text)
        self.assertIn('<span className="cc-status-dot" />', text)
        # 已连接 / 正在连接时不出「连接」；有连接才有「···」（重新查找、断开连接）
        self.assertIn('{state !== "connected" && state !== "connecting" && (', text)
        menu = text.split("<DropdownMenuContent", 1)[1].split("</DropdownMenuContent>", 1)[0]
        self.assertIn("<DropdownMenuItem onSelect={reload}>{st.findAgain}</DropdownMenuItem>", menu)
        self.assertIn('<DropdownMenuItem variant="destructive" onSelect={disconnect}>', menu)
        self.assertIn("{st.disconnect}", menu)

    def test_old_desktops_without_the_actions_get_no_row(self):
        text = read(ROW)
        self.assertIn("if (!value.ok) return null;", text)
        self.assertIn("if (!status) return null;", text)

    def test_buttons_call_the_right_bridge_actions(self):
        text = read(ROW)
        self.assertIn('api.call("cloud_status")', text)
        self.assertIn('api.call("cloud_prefill")', text)
        self.assertIn('api.call("cloud_connect", { host, port, user: user.trim() })', text)
        self.assertIn('api.call("cloud_disconnect")', text)
        self.assertEqual(set(re.findall(r'api\.call\("(\w+)"', text)),
                         {"cloud_status", "cloud_prefill", "cloud_connect", "cloud_disconnect"})
        # 失败照实写在弹窗里，不排重试；原生小窗取消不报错
        self.assertIn("if (result.cancelled) return;", text)
        self.assertIn("if (!result.ok) return setError(st.linkFailed);", text)
        self.assertNotRegex(text, r"setTimeout|setInterval")

    def test_prefill_is_shown_and_editable(self):
        text = read(ROW)
        dialog = text.split("export const CloudExpressConnect: FC", 1)[1]
        self.assertIn("setAddress((now) => now || addressOf(found.host ?? \"\", found.port));", dialog)
        self.assertIn("setUser((now) => now || (found.user ?? \"\"));", dialog)
        self.assertIn("onChange={(e) => setAddress(e.target.value)}", dialog)
        self.assertIn("onChange={(e) => setUser(e.target.value)}", dialog)
        self.assertIn("{st.address}", dialog)
        self.assertIn("{st.account}", dialog)
        self.assertIn('{t("cancel")}', dialog)
        self.assertIn("{st.connect}", dialog)

    def test_no_password_in_the_page_or_in_any_persisted_state(self):
        text = read(ROW)
        self.assertNotRegex(text, r'type=["{]?["\']?password')
        code = re.sub(r"//[^\n]*", "", text)
        self.assertNotRegex(code.lower(), r"password")
        for persisted in ("localStorage", "sessionStorage", "setPref", "prefs-sync", "managed-agents", "zustand", "indexedDB"):
            self.assertNotIn(persisted, code)

    def test_cloud_places_are_read_only(self):
        text = read(ROW)
        places = text.split("places.map((path) => (", 1)[1].split("))}", 1)[0]
        self.assertNotIn("addFolder", text)
        self.assertNotIn("remove", places)
        self.assertNotIn("Button", places)
        self.assertIn('{state === "connected" &&', text)

    def test_address_helpers(self):
        text = read(ROW)
        self.assertIn("const DEFAULT_PORT = 3389;", text)
        self.assertIn("host && port && port !== DEFAULT_PORT ? `${host}:${port}` : host", text)
        self.assertIn(r'/^([^:\s]+):(\d{1,5})$/', text)

    def test_the_row_is_a_product_map_action_and_uses_the_kit(self):
        text = read(ROW)
        self.assertIn("// map: cloud-express\n", text)
        self.assertIn('from "@/components/ui/button"', text)
        self.assertIn('from "@/components/ui/dialog"', text)
        self.assertIn('from "@/components/ui/dropdown-menu"', text)
        self.assertIn('from "@/components/ui/input"', text)
        self.assertNotRegex(text, r"<(button|input)(?=[\s>/])")
        self.assertIn('import { CloudIcon, FolderIcon, MoreHorizontalIcon } from "lucide-react";', text)

    def test_new_words_have_four_languages(self):
        text = read(I18N)
        for row in ('cloudExpress: "Cloud Express", address: "Address", account: "Account",',
                    'cloudExpress: "云端 Express", address: "地址", account: "账号",',
                    'cloudExpress: "Express บนคลาวด์", address: "ที่อยู่", account: "บัญชี",',
                    'cloudExpress: "クラウド Express", address: "アドレス", account: "アカウント",'):
            self.assertEqual(text.count(row), 1, row)


if __name__ == "__main__":
    unittest.main()
