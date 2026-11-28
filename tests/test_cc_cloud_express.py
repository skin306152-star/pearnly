"""CC「设置 → 连接」的「云端 Express」一行（步 E，用户 11-16）：页面一侧的离线检查（读源码，不起浏览器），同 tests/test_assistant_ui.py。

- 挂在 Connections 里「Express 位置」那几行之后、只在 CC 桌面版（api）里；照 cc-settings-row / cc-status-line 的写法；
- 状态五种：未连接 / 正在连接 / 已连接 / Express 登录失败 / 账号已在别处登录，词条沿用现有的；
- 「连接」弹窗：地址、账号由桥预填（cloud_prefill）、能改；**页面没有密码框、不把密码交给桥**（ADR-026 第 7 条，密码由桥弹 CC 原生小窗收）；
- 按钮调对桥动作：cloud_status / cloud_prefill / cloud_connect {host, port, user} / cloud_disconnect；失败不自动重试；
- 不往 localStorage、偏好、对话状态写任何东西；云端位置只读、没有「添加文件夹」；四语词条齐全。
- 配对块（CloudPairing）：连接成功或「···」→ 云端安装命令时挂上、调 cloud_pair_begin；只放命令（框内换行、整行可选）、复制、
  剩余时间，到 0 或被拒显示「已过期」+「重新生成」（再调 cloud_pair_begin）；准备失败按 code 一行事实；cloud_pair_status 报 paired
  即收起；命令（含一次性令牌）只在组件 state 里，不存、不打日志、不发服务端；长命令在 375 宽也不撑破布局。
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
    def test_the_connections_page_has_three_groups_in_a_fixed_order(self):
        # 用户 11-24 审定：站点 → 账本（MR.ERP → 本地 Express → 云端 Express）→ 电脑；站点行下不再写「Express」小字
        settings = read(SETTINGS)
        connections = settings.split("function Connections(", 1)[1].split("\nfunction ", 1)[0]
        self.assertEqual(settings.count("<CloudExpressRow api={api} />"), 1)
        order = ["<Heading>{st.groupSite}</Heading>", 'data-slot="cc-site-row"', "<Heading>{st.groupLedgers}</Heading>",
                 "<MrerpRow indent />", 'data-slot="cc-local-express-row"', "<CloudExpressRow api={api} />",
                 "<PoolComputers inDesktop />"]
        places = [connections.index(item) for item in order]
        self.assertEqual(places, sorted(places), order)
        self.assertNotIn("const kinds =", connections)
        self.assertIn('import { CloudExpressRow } from "@/components/cloud-express";', settings)

    def test_local_express_is_renamed_and_its_folders_start_collapsed(self):
        settings = read(SETTINGS)
        connections = settings.split("function Connections(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("const [localOpen, setLocalOpen] = useState(false);", connections)
        self.assertIn("{st.localExpress}", connections)
        self.assertIn("{(localOpen || !roots.length) && (roots.length > 0", connections)
        self.assertIn("{placesFeature && (localOpen || !roots.length) &&", connections)
        self.assertIn("st.folderCount(", connections)

    def test_each_state_shows_an_existing_status_word(self):
        text = read(ROW)
        line = text.split("export function cloudStatusLine", 1)[1].split("\n}\n", 1)[0]
        for state, word, tone in (('"connecting"', "st.status.connecting", '"connecting"'),
                                  ('"connected"', "st.status.online", '"online"'),
                                  ('"login_failed"', "loginReason(reason)", '"attention"'),
                                  ('"replaced"', "op.reason.account_in_use", '"attention"')):
            self.assertIn(f"if (state === {state}) return {{ text: {word}, tone: {tone} }};", line)
        self.assertIn('return { text: st.notConnected, tone: "offline" };', line)
        self.assertIn('className="cc-status-line text-body" data-status={line.tone}', text)
        self.assertIn('<span className="cc-status-dot" />', text)

    def test_the_more_menu_has_relogin_detail_disconnect_and_remove(self):
        text = read(ROW)
        menu = text.split("<DropdownMenuContent", 1)[1].split("</DropdownMenuContent>", 1)[0]
        order = ["{st.relogin}", "{st.installCommand}", "`${link.name || link.host} · ${link.user}`",
                 "`CC ${cloud.version}`", "{st.disconnect}", "{st.remove}"]
        self.assertEqual([menu.index(item) for item in order], sorted(menu.index(item) for item in order))
        self.assertIn('<DropdownMenuItem variant="destructive" onSelect={disconnect}>', menu)
        # 11-28 用户：一个弹窗一次输完——「实操登录」两格并进连接弹窗，菜单里没有单独的「实操登录」
        self.assertNotIn("{st.operatorLogin}", menu)
        self.assertNotIn("CloudOperatorLogin", text)

    def test_the_connect_dialog_takes_the_operator_login_too_and_may_leave_it_empty(self):
        text = read(ROW)
        dialog = text.split("export const CloudExpressConnect", 1)[1].split("export const CloudAddPlace", 1)[0]
        self.assertIn('data-slot="cc-cloud-express-operator-title">{st.operatorLogin}</div>', dialog)
        self.assertIn("if (opUser.trim() && opPassword) {", dialog)   # 空着不报错、不拦
        self.assertIn('placeholder={status?.operatorRemembered ? "••••••••" : undefined}', dialog)

    def test_old_desktops_without_the_actions_get_no_row(self):
        text = read(ROW)
        self.assertIn("if (!value.ok) return null;", text)
        self.assertIn("if (!status) return null;", text)

    def test_buttons_call_the_right_bridge_actions(self):
        text = read(ROW)
        self.assertIn('api.call("cloud_status")', text)
        self.assertIn('api.call("cloud_prefill")', text)
        # 新桌面版（connect_dialog）：一个弹窗交地址、用户名、密码（只在填了时带），带 inline；旧版不带密码、桥弹原生小窗
        self.assertIn('api.call("cloud_connect", { host, port, user: user.trim(), inline: true, ...(password ? { password } : {}) })', text)
        self.assertIn('api.call("cloud_connect", { host, port, user: user.trim() })', text)
        self.assertIn('api.call("cloud_operator_authorize", { connection: "default", inline: true, username: opUser.trim(), password: opPassword })', text)
        self.assertIn('api.call("cloud_rescan")', text)
        self.assertIn('api.call("cloud_add_place", { path: path.trim() })', text)
        self.assertIn('api.call("cloud_disconnect")', text)
        # 11-28 用户：断开只断开；「移除」才删连接与密码，桥 cloud_status 回 forget 才出
        self.assertIn('api.call("cloud_forget")', text)
        self.assertIn('{status.forget && (', text)
        self.assertIn("const inline = !!useDesktopFeatures().connectDialog;", text)
        self.assertIn("if (result.cancelled) return;", text)
        self.assertNotIn("setTimeout", text)

    def test_the_dialog_shows_progress_and_the_login_failure_reason(self):
        text = read(ROW)
        dialog = text.split("export const CloudExpressConnect: FC", 1)[1].split("\n};\n", 1)[0]
        self.assertIn('if (state === "connected") return onClose(true);', dialog)
        self.assertIn("setError(loginReason(status.reason));", dialog)
        self.assertIn("setError(op.reason.account_in_use);", dialog)
        self.assertIn("{st.connecting}", dialog)
        self.assertIn('placeholder={status?.remembered ? "••••••••" : undefined}', dialog)

    def test_prefill_is_shown_and_editable(self):
        text = read(ROW)
        dialog = text.split("export const CloudExpressConnect: FC", 1)[1]
        self.assertIn("setAddress((now) => now || addressOf(found.host ?? \"\", found.port));", dialog)
        self.assertIn("setUser((now) => now || (found.user ?? \"\"));", dialog)
        self.assertIn("onChange={(e) => setAddress(e.target.value)}", dialog)
        self.assertIn("onChange={(e) => setUser(e.target.value)}", dialog)
        self.assertIn("{st.address}", dialog)
        self.assertIn("{st.username}", dialog)
        self.assertIn('{t("cancel")}', dialog)

    def test_passwords_stay_in_the_dialog_and_are_never_persisted(self):
        text = read(ROW)
        code = re.sub(r"//[^\n]*", "", text)
        for persisted in ("localStorage", "sessionStorage", "setPref", "prefs-sync", "managed-agents", "zustand", "indexedDB"):
            self.assertNotIn(persisted, code)
        # 只有连接弹窗与代录登录弹窗有密码框，提交后清空
        self.assertEqual(len(re.findall(r'type="password"', text)), 2)
        self.assertGreaterEqual(code.count('setPassword("")'), 2)

    def test_cloud_places_show_ledger_counts_with_rescan_and_add_folder(self):
        text = read(ROW)
        places = text.split("places.map((place) => (", 1)[1].split("))}", 1)[0]
        self.assertIn("st.ledgerCount(place.ledgers)", places)
        self.assertNotIn("remove", places)
        self.assertIn('data-slot="cc-cloud-express-rescan"', text)
        self.assertIn('data-slot="cc-cloud-express-add-place"', text)
        self.assertIn("{open && link && (", text)

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
        self.assertIn('import { ChevronRightIcon, FolderIcon, MoreHorizontalIcon } from "lucide-react";', text)
        self.assertNotIn("CloudIcon", text)   # 用户 11-25：云端行前不加图标（没经审的装饰图标一律不加）

    def test_new_words_have_four_languages(self):
        text = read(I18N)
        for row in ('cloudExpress: "Cloud Express", address: "Address", account: "Account",',
                    'cloudExpress: "云端 Express", address: "地址", account: "账号",',
                    'cloudExpress: "Express บนคลาวด์", address: "ที่อยู่", account: "บัญชี",',
                    'cloudExpress: "クラウド Express", address: "アドレス", account: "アカウント",'):
            self.assertEqual(text.count(row), 1, row)
        self.assertEqual(text.count("localExpress:"), 8)   # 设置与公司两组词条各四语
        for key in ("groupSite:", "groupLedgers:", "folderCount:", "ledgerCount:", "relogin:",
                    "operatorLogin:", "expressNotFound:", "pathNotUsable:", "loginReason:", "through:"):
            self.assertEqual(text.count(key), 4, key)


def pairing_block(text):
    return text.split("export const CloudPairing: FC", 1)[1].split("\n};\n", 1)[0]


class CloudPairingBlock(unittest.TestCase):
    def test_shown_after_connect_and_from_the_menu(self):
        text = read(ROW)
        self.assertIn("if (connected && !cloud && !status.installing) pair();", text)   # 云端没报过状态、桥也没在替人装才给命令
        self.assertIn("onClose(true);", text)
        self.assertIn('<Dialog open onOpenChange={(v) => !v && onClose(false)}>', text)
        self.assertIn("{pairing !== null && <CloudPairing key={pairing} api={api} onDone={() => setPairing(null)} />}", text)
        menu = text.split("<DropdownMenuContent", 1)[1].split("</DropdownMenuContent>", 1)[0]
        self.assertIn('<DropdownMenuItem onSelect={pair} data-slot="cc-cloud-express-pair">', menu)
        self.assertIn("{st.installCommand}", menu)
        # 断开连接即丢掉配对块
        disconnect = text.split("const disconnect = () => {", 1)[1].split("};", 1)[0]
        self.assertIn("setPairing(null);", disconnect)

    def test_begin_calls_the_bridge_and_shows_the_command(self):
        block = pairing_block(read(ROW))
        self.assertIn('void api.call("cloud_pair_begin").then((raw) => {', block)
        self.assertIn("setPair(pairOutcome(raw));", block)
        text = read(ROW)
        outcome = text.split("export function pairOutcome(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('return { phase: "ready", command: value.command, expires: value.expires };', outcome)
        self.assertNotIn("token", outcome)
        # 命令：等宽代码块、框内换行、整行可选；只有命令、复制、剩余时间
        ready = block.split('{pair.phase === "ready" && (', 1)[1]
        self.assertIn('<CodeBlockContent wrap className="select-all">', ready)
        self.assertIn("{pair.command}", ready)
        self.assertIn("<CodeBlockCopyButton value={pair.command} />", ready)
        self.assertIn("{clock(left)}", ready)
        self.assertNotIn("st.", ready)
        self.assertNotIn("t(", ready.replace("select-all", ""))

    def test_copy_copies_the_whole_line(self):
        kit = read(SPA / "components/ui/code-block.tsx")
        self.assertIn(".writeText(value)", kit)
        self.assertIn("aria-label={copied ? ui.copied : t(\"copy\")}", kit)

    def test_pairing_buttons_match_the_page_buttons(self):
        # 同一页同一套按钮（前端验收 B74 / G01）：「重试」「重新生成」与上面「重新查找」「更换…」一样用默认尺寸的 outline，不用小号
        for slot in ("cc-cloud-express-retry", "cc-cloud-express-regenerate"):
            self.assertIn(f'<Button variant="outline" onClick={{begin}} data-slot="{slot}">', read(ROW))

    def test_countdown_runs_out_into_regenerate(self):
        text = read(ROW)
        self.assertIn("export const secondsLeft = (expires: number, now: number) => Math.max(0, Math.ceil(expires - now / 1000));", text)
        self.assertIn('export const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;', text)
        block = pairing_block(text)
        self.assertIn('if (left === 0) return setPair({ phase: "expired" });', block)
        self.assertIn("return () => clearInterval(tick);", block)
        expired = block.split('{pair.phase === "expired" && (', 1)[1].split("\n      )}", 1)[0]
        self.assertIn("{st.expired}", expired)
        self.assertIn('<Button variant="outline" onClick={begin} data-slot="cc-cloud-express-regenerate">', expired)
        self.assertIn("{st.regenerate}", expired)
        # 重新生成 = 再调 cloud_pair_begin（begin 里只有这一个桥动作）
        begin = block.split("const begin = useCallback(() => {", 1)[1].split("}, [api]);", 1)[0]
        self.assertEqual(re.findall(r'api\.call\("(\w+)"', begin), ["cloud_pair_begin"])
        # 配对被拒（令牌作废）按过期
        self.assertIn('else if (found.state === "failed") setPair((p) => (p.phase === "ready" ? { phase: "expired" } : p));', block)

    def test_failure_codes_have_one_line_each_group(self):
        text = read(ROW)
        self.assertIn('export const pairFailure = (code: string) => (code === "not_running" ? st.notConnected : st.cloudPackageFailed);', text)
        block = pairing_block(text)
        failed = block.split('{pair.phase === "failed" && (', 1)[1].split("\n      )}", 1)[0]
        self.assertIn("{pairFailure(pair.code)}", failed)
        self.assertIn('role="alert"', failed)
        self.assertIn("{st.retry}", failed)
        tomo = read(ROOT / "companion/src/companion/tomo_bridge.py")
        for code in ("no_package", "download_failed", "checksum", "write_failed", "not_running"):
            self.assertIn(code, tomo)

    def test_paired_collapses_the_block(self):
        block = pairing_block(read(ROW))
        self.assertIn('void api.call("cloud_pair_status").then((raw) => {', block)
        self.assertIn('if (found.state === "paired") done.current();', block)
        self.assertIn("api.onChanged(() => {", block)

    def test_command_stays_in_memory_and_no_password_box(self):
        text = read(ROW)
        code = re.sub(r"//[^\n]*", "", text)
        self.assertNotRegex(code, r"console\.|fetch\(|localStorage|sessionStorage|navigator\.sendBeacon|reportCaught|withReport")
        # 配对块里没有密码（密码框只在连接 / 代录登录弹窗，见 CloudExpressRow 的守护）
        self.assertNotRegex(pairing_block(code).lower(), r"password")
        # 卸载即丢：命令只在 CloudPairing 的 state 里
        self.assertIn('const [pair, setPair] = useState<Pair>({ phase: "preparing" });', text)
        self.assertEqual(code.count("pair.command"), 2)

    def test_long_command_wraps_inside_the_box(self):
        css = read(SPA / "globals.css")
        rule = css.split(".cc-cloud-pair {", 1)[1].split("}", 1)[0]
        self.assertIn("min-width: 0;", rule)
        self.assertIn("max-width: 100%;", rule)
        # 令牌一长串没有空格：代码块的网格列收成 minmax(0, 1fr)、任意处断行，不按最长一段撑宽
        self.assertIn(".cc-cloud-pair .cc-ui-code-block-code { grid-template-columns: minmax(0, 1fr); overflow-wrap: anywhere; }", css)
        self.assertIn(".cc-cloud-pair-line { display: flex; align-items: center; gap: var(--cc-space-12); min-width: 0; }", css)
        # 代码块自带：wrap 时 pre-wrap + 断词、框内可滚动、不设最小内容宽
        kit = read(SPA / "components/ui/code-block.tsx")
        self.assertIn("overflow-auto", kit)
        self.assertIn("data-wrap:whitespace-pre-wrap", kit)
        self.assertIn("in-data-wrap:min-w-0", kit)
        self.assertIn('className="cc-cloud-pair" data-slot="cc-cloud-express-pairing"', read(ROW))
        self.assertNotIn("white-space: nowrap", rule)

    def test_pairing_words_have_four_languages(self):
        text = read(I18N)
        for row in ('installCommand: "Cloud install command", expired: "Expired", regenerate: "Regenerate",',
                    'installCommand: "云端安装命令", expired: "已过期", regenerate: "重新生成",',
                    'installCommand: "คำสั่งติดตั้งบนคลาวด์", expired: "หมดอายุแล้ว", regenerate: "สร้างใหม่",',
                    'installCommand: "クラウドのインストールコマンド", expired: "期限切れ", regenerate: "再生成",',
                    'cloudPackageFailed: "Could not prepare the cloud install files",',
                    'cloudPackageFailed: "无法准备云端安装文件",',
                    'cloudPackageFailed: "เตรียมไฟล์ติดตั้งบนคลาวด์ไม่สำเร็จ",',
                    'cloudPackageFailed: "クラウドのインストールファイルを準備できませんでした",'):
            self.assertEqual(text.count(row), 1, row)


class HiddenInProduction(unittest.TestCase):
    """11-24 首测：云端 Express 端到端没走通（D3 云端 CC 没有成员、D4 找不到账套），入口在正式部署里先不显示（不是按租户开关）。
    判定只有一处（``pearnly_core/cc_desktop/cloud_entry.visible``：非生产才显示），对话页与 Desk 启动信息都取它，三处入口按它渲染。"""

    CORE = ROOT / "apps/pearnly_core/pearnly_core"

    def test_only_non_production_deployments_show_the_cloud_entries(self):
        import sys

        sys.path.insert(0, str(ROOT / "apps/pearnly_core"))
        from pearnly_core.cc_desktop import cloud_entry

        for env in ({}, {"PEARNLY_DEPLOYMENT": "production"}, {"PEARNLY_DEPLOYMENT": " Production "},
                    {"PEARNLY_DEPLOYMENT": ""}):
            self.assertFalse(cloud_entry.visible(env), env)
        for name in ("dev", "testenv", "acceptance"):
            self.assertTrue(cloud_entry.visible({"PEARNLY_DEPLOYMENT": name}), name)
        boot = type("Boot", (), {})()
        cloud_entry.extend_bootinfo(boot)
        self.assertIsInstance(boot.pearnly_cloud_express, bool)

    def test_the_page_and_the_desk_boot_carry_the_decision(self):
        page = read(self.CORE / "www/assistant.py")
        self.assertIn("context.cloud_express = cloud_entry.visible()", page)
        self.assertIn("window.pearnly_cloud_express = {{ cloud_express | tojson }};", read(self.CORE / "www/assistant.html"))
        self.assertIn('"pearnly_core.cc_desktop.cloud_entry.extend_bootinfo"', read(self.CORE / "hooks.py"))

    def test_all_three_entries_render_only_when_the_server_says_so(self):
        self.assertIn("{window.pearnly_cloud_express === true && <CloudExpressRow api={api} />}", read(SETTINGS))
        picker = read(SPA / "components/company-picker.tsx")
        self.assertIn("cloud = window.pearnly_cloud_express === true", picker)
        self.assertIn("(cloud ? [...kinds, CLOUD] : kinds)", picker)
        desktop = read(SPA / "lib/desktop.ts")
        # 页面自动登记云端电脑也按同一个判定（两处触发都收）
        self.assertEqual(desktop.count("registerCloud(api, tried)"), 2)
        self.assertEqual(desktop.count("cloud_link && window.pearnly_cloud_express === true) "), 2)
        connect = read(ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/company-connect.js")
        self.assertIn('const TABS = ["mrerp", "express"].concat(frappe.boot && frappe.boot.pearnly_cloud_express ? ["express_cloud"] : []);',
                      connect)


if __name__ == "__main__":
    unittest.main()
