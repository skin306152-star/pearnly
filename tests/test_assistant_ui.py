"""对话页（frontend/assistant，ADR-019）CC 闭环第一批的源码检查（2026-10-12）：
提醒卡片（通道 1）、侧栏圆点与浮顶、补拉锚点、推送通知（通道 2）、深链接、桌面端「打开对话」、定时任务页。

前半只读源码，守住各路之间的约定不被改丢；不跑浏览器、不连站点。
后半（BehaviourTests，2026-10-13 审查修复）把被测源码连同组件库用 esbuild 打成一个文件交给 node 跑，
浏览器接口（通知、后台脚本、fetch）换成桩，验证审查发现的失败场景已经不再出现。要先装好前端依赖
（npm ci --prefix frontend/assistant）且有 node，否则跳过。
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend/assistant"
SRC = FRONTEND / "src"


def _rule():
    """共用层的界面语言规则（apps/pearnly_core/pearnly_core/account/ui_language.py，纯规则）：站点按它给页面定 <html lang>。"""
    spec = importlib.util.spec_from_file_location("cc_ui_language", ROOT / "apps/pearnly_core/pearnly_core/account/ui_language.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ui_language = _rule()


def read(path):
    return (SRC / path).read_text(encoding="utf-8")


def block(source, start):
    """从 start 那一行的「{」起，取到配对的「}」为止（含两端）。"""
    begin = source.index(start)
    opening = source.index("{", begin)
    depth = 0
    for i in range(opening, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[opening:i + 1]
    raise AssertionError(f"unbalanced block after {start!r}")


def keys(language_block):
    return set(re.findall(r"(?:^|[\s,{])(\w+):\s*[\"'(`{]", language_block))


class FeedTests(unittest.TestCase):
    def test_local_events_are_never_the_poll_anchor(self):
        # 本站提醒不在引擎事件记录里：拿它当锚点，服务端每轮都找不到、退回整段重放
        events = read("lib/managed-agents/events.ts")
        self.assertIn('type: "pearnly.notice";', events)
        self.assertIn('return String(event.type).startsWith("pearnly.");', events)
        self.assertIn("export type FeedEvent = StreamEvent | NoticeEvent;", events)
        poll = read("lib/managed-agents/poll-source.ts")
        self.assertIn("if (anchor && !isLocalEvent(event)) this.lastId = id;", poll)
        self.assertNotIn("if (anchor) this.lastId = id;", poll)

    def test_reducer_keeps_notices_beside_the_messages_and_the_page_merges_them(self):
        reducer = read("lib/managed-agents/reducer.ts")
        self.assertIn('case "pearnly.notice":', reducer)
        self.assertIn("notices: insertNotice(state.notices, event)", reducer)
        self.assertIn("notices: [],", reducer)  # initialProjection
        self.assertIn("export function withNotices(", reducer)
        merge = reducer.split("export function withNotices(", 1)[1]
        self.assertIn('if (message.role === "user")', merge)  # 只插在用户消息前，不切开一轮回复
        self.assertIn("metadata: { custom: { notice:", reducer)
        self.assertIn("new WeakMap<NoticeItem, NoticeMessage>()", reducer)  # 同一条提醒同一个对象
        # 卡片是 system 消息：最后一条是它时组件库照样补「思考中」占位，也不会被当成回复（审查 frontend-6）
        card = reducer.split("function noticeMessage(", 1)[1].split("\n}", 1)[0]
        self.assertIn('role: "system"', card)
        self.assertNotIn("status:", card)  # 组件库不许 system 消息带状态
        self.assertIn("replyInProgress(messages.at(-1))", merge)
        provider = read("lib/managed-agents/runtime-provider.tsx")
        self.assertIn("withNotices(snapshot.messages, snapshot.notices)", provider)
        self.assertIn("messages: messages as readonly ThreadMessageLike[]", provider)
        self.assertIn("noticesShown(sessionId, lastNotice)", provider)
        # 回复完成通知、文件面板仍只看引擎消息
        self.assertNotIn("withNotices", provider.split("export function useActiveSessionSnapshot", 1)[1])

    def test_unattached_notices_still_reach_the_page(self):
        realtime = read("lib/managed-agents/realtime.ts")
        page = "if (push.session_id) handlers.get(push.session_id)"
        sidebar = 'if (push.session_id || isNotice(push.event)) safely(() => noteEvent(push.session_id || "", push.event));'
        self.assertIn(page, realtime)
        self.assertIn(sidebar, realtime)
        # 先给会话的页面，再记侧栏与提醒；每一路单独兜住（审查 frontend-2）
        self.assertLess(realtime.index(page), realtime.index(sidebar))
        self.assertIn("socket.on(EVENT, dispatch);", realtime)
        self.assertIn("export function onSiteEvent(event: string, handler: (data: unknown) => void)", realtime)
        self.assertIn("export function onConnectionChange(listener: (connected: boolean) => void)", realtime)
        # 共用层不认识产品事件
        self.assertNotIn("cowork", realtime)

    def test_sidebar_dot_and_float_to_top(self):
        api = read("lib/managed-agents/api.ts")
        self.assertIn('post("notices_seen", { session_id: id })', api)
        activity = read("lib/managed-agents/activity.ts")
        self.assertIn('export type Activity = "running" | "unread" | "notice";', activity)
        for name in ("seedUnread", "clearUnread", "noticesShown", "useBumps", "onNotice", "setChatView"):
            self.assertIn(f"export function {name}(", activity)
        self.assertIn("markNoticesSeen(sessionId)", activity)
        self.assertIn("return sessionId === active && chatView && document.visibilityState", activity)
        adapter = read("lib/managed-agents/session-list-adapter.ts")
        self.assertIn("if (s.unread) seedUnread(s.id);", adapter)
        self.assertIn("else if (s.unread === false) clearUnread(s.id, asked);", adapter)
        self.assertLess(adapter.index("const asked = Date.now();"), adapter.index("await listSessions("))
        page = read("assistant.tsx")
        self.assertIn('useEffect(() => setChatView(view === "chat"), [view]);', page)
        # 提醒挂到没加载到的对话：不再每条都整表重拉（审查 frontend-4）
        self.assertIn("noticeReloader(() => aui.threads(), NOTICE_RELOAD_MS)", page)
        self.assertNotIn("if (!listed) void aui.threads().reload();", page)
        threads = read("components/assistant-ui/thread-list.tsx")
        self.assertIn("const bumps = useBumps();", threads)
        self.assertIn('t("newNotice")', threads)
        css = read("globals.css")
        self.assertIn('.cc-activity[data-activity="notice"]::before', css)


class NoticeCardTests(unittest.TestCase):
    def test_card_reuses_the_file_card_look_and_offers_push_only_when_off(self):
        thread = read("components/assistant-ui/thread.tsx")
        self.assertIn("if (notice) return <NoticeMessage />;", thread)
        card = thread.split("const NoticeMessage: FC = () => {", 1)[1].split("\n};", 1)[0]
        self.assertIn('role="note"', card)
        self.assertIn('aria-label={t("notice")}', card)
        self.assertIn("cc-card", card)
        self.assertIn("<BellIcon", card)
        self.assertIn("whitespace-pre-line", card)
        self.assertIn('push.state === "off"', card)
        self.assertIn("push.enable()", card)
        self.assertIn("push.failure", card)  # 打开失败给原因（审查 frontend-7）
        # 按提醒这类事件挂，不按哪个产品的工具（ADR-009）
        self.assertNotIn("cowork", card)
        # 文件卡片的「下一条」跳过提醒卡片
        self.assertIn("messages.slice(index + 1).find((m) => !isNoticeMessage(m))", thread)


class PushTests(unittest.TestCase):
    def test_push_subscribes_through_the_three_core_endpoints(self):
        push = read("lib/push.ts")
        self.assertIn("userVisibleOnly: true", push)
        self.assertIn('get<{ key: string }>("push_key")', push)
        self.assertIn('post("push_subscribe", payload(', push)
        self.assertIn('post("push_unsubscribe", { endpoint: subscription.endpoint })', push)
        self.assertIn('const BASE = "/api/method/pearnly_core.assistant.api.";', read("lib/frappe.ts"))
        # 桌面端窗口不用；iPhone 只有主屏幕 app 收得到
        self.assertIn('if (isDesktopApp()) return setState("desktop");', push)
        self.assertIn('if (isIOS() && !standalone()) return setState("install");', push)
        self.assertIn('if (!capable()) return setState("unsupported");', push)
        # 打开要在点击里直接订阅：enablePush 里第一个异步动作就是 subscribe
        enable = push.split("export function enablePush()", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("await", enable)
        self.assertLess(enable.index("pushManager"), enable.index("push_subscribe"))

    def test_settings_row_and_sign_out_forget_the_device(self):
        settings = read("components/settings.tsx")
        self.assertIn("const push = usePush();", settings)
        self.assertIn('push.failure && <div className="text-danger text-xs">{push.failure}</div>', settings)
        self.assertIn("export function Switch(", settings)
        self.assertIn("<Notifications />", settings)
        # 这一行一直在（桌面端窗口除外）：开不了开关灰着，下面一行给原因（用户 2026-10-13，iPhone 上找不到「通知」）
        row = settings.split("export function Notifications()", 1)[1].split("\n}", 1)[0]
        self.assertIn('if (push.state === "desktop") return null;', row)
        self.assertIn("disabled={!usable}", row)
        for reason in ("st.notificationsBlocked", "st.notificationsAddToHome", "st.notificationsUnsupported"):
            self.assertIn(reason, row)
        self.assertIn("onClick={showGuide}", row)  # 「添加到主屏幕」那句点了打开添加步骤
        self.assertIn("useEffect(() => void syncPush(), []);", row)  # 上次没准备好，打开设置再同步一次
        page = read("assistant.tsx")
        sign_out = page.split("async function signOut()", 1)[1].split("\n}", 1)[0]
        self.assertLess(sign_out.index("await forgetDevice()"), sign_out.index("pearnly_core.identity.sign_out"))
        self.assertIn("useEffect(() => void syncPush(), []);", page)
        # 「通知」一行要能打开「添加到主屏幕」的步骤：设置放在 InstallProvider 里
        workspace = page.split("function Workspace()", 1)[1]
        self.assertLess(workspace.index("<InstallProvider>"), workspace.index("<Settings />"))

    def test_page_notifications_for_notices(self):
        desktop = read("lib/desktop.ts")
        alert = desktop.split("onNotice((notice) => {", 1)[1].split("\n      }),", 1)[0]
        self.assertIn("`notice:${notice.id}`", alert)
        self.assertIn("notice.title || APP_NAME", alert)
        self.assertIn('deepLink(notice.session || "")', alert)
        self.assertIn('openTarget(notice.session || "")', alert)
        # 浏览器里开了推送由后台脚本弹，不再另弹
        self.assertIn("if (!desktopApp && (pushOnRef.current || document.hasFocus())) return;", alert)
        # 回复完成与提醒都经 showNote：浏览器里由后台脚本弹，安卓 Chrome 不能直接 new Notification（审查 frontend-1/2）
        self.assertEqual(desktop.count("void showNote("), 2)
        self.assertNotIn("new Notification(", desktop)
        notify = read("lib/notify.ts")
        self.assertIn("navigator.serviceWorker.getRegistration(HOME)", notify)
        self.assertIn("registration.showNotification(title, { body, tag, data: { url } })", notify)
        # 整页最外层有错误边界：哪里抛错都不整页变白
        main = read("main.tsx")
        self.assertLess(main.index("<ErrorBoundary>"), main.index("<Assistant />"))
        boundary = read("components/error-boundary.tsx")
        self.assertIn("static getDerivedStateFromError()", boundary)
        self.assertIn('t("reload")', boundary)


class DeepLinkTests(unittest.TestCase):
    def test_nav_reads_c_and_view_then_cleans_the_address(self):
        nav = read("lib/nav.ts")
        self.assertIn('parsed.searchParams.get("c")', nav)
        self.assertIn('parsed.searchParams.get("view")', nav)
        self.assertIn('target.view === "tasks"', nav)
        self.assertIn('history.replaceState(history.state, "", url.pathname + url.search + url.hash);', nav)
        self.assertIn('data?.type !== "pearnly-open"', nav)
        self.assertIn('window.addEventListener("tomo-open", fromDesktop);', nav)
        # 不是本人的、不存在的：先问本站（不是本人的服务端回 404），失败改开定时任务页或新对话
        self.assertIn("await getSession(id);", nav)
        self.assertIn('latest.current.setView("tasks")', nav)
        self.assertIn("switchToNewThread()", nav)
        self.assertIn("/^[A-Za-z0-9][A-Za-z0-9_.:-]{3,127}$/", nav)  # 同服务端 OPAQUE_ID
        page = read("assistant.tsx")
        self.assertIn("useDeepLinks(setView, () => loadProfile().then(hasScheduledTasks));", page)
        self.assertIn("<DeepLinks />", page)

    def test_desktop_open_dispatches_tomo_open(self):
        desktop = read("lib/desktop.ts")
        self.assertIn("open: (target: string) => void", desktop)
        self.assertIn("open: (target: string) => openTarget(target),", desktop)
        self.assertIn('new CustomEvent("tomo-open", { detail: String(target ?? "") })', desktop)


class ScheduledTaskPageTests(unittest.TestCase):
    def test_live_refresh_follows_the_desk_rules(self):
        live = read("lib/live.ts")
        for text in ("busyMs = 10_000", "idleMs = 60_000", "settleMs = 800", "minMs = 3_000",
                     '"visibilitychange"', 'addEventListener("focus"', "onConnectionChange(", "onSiteEvent(",
                     "document.hidden"):
            self.assertIn(text, live)

    def test_page_shows_progress_waiting_and_remind(self):
        page = read("components/scheduled-tasks.tsx")
        self.assertIn('const FOLDER_EVENT = "cowork_folder_changed";', page)
        self.assertIn("useLiveRefresh(reload, { events: [FOLDER_EVENT]", page)
        self.assertIn('tr("cowork.folder.moving", rule.results.moving)', page)
        # 列表里的是待处理数，用待处理的说法；「现有 N 份」只给文件夹计数（审查 interfaces-3）
        self.assertIn('tr("cowork.folder.pending", plus(rule.waiting, rule.waiting_partial))', page)
        self.assertNotIn('tr("cowork.folder.waiting", plus(rule.waiting', page)
        self.assertIn('rule.include_existing ? "cowork.folder.enableConfirmAll" : "cowork.folder.enableConfirm"', page)
        self.assertIn('import { Switch } from "@/components/settings";', page)
        self.assertIn('tr("cowork.folder.field.remind")', page)
        self.assertIn("remind: remind ? 1 : 0", page)
        self.assertIn(
            'hint={folderCount ? tr(pendingHint ? "cowork.folder.pending" : "cowork.folder.waiting", folderCount) : tr(folderHint)}',
            page,
        )
        # 词条走 Cowork 术语表（folder_intake.api.terms 的 cowork.folder. 前缀），不进前端词条
        i18n = read("i18n.ts")
        self.assertNotIn("cowork.folder", i18n)

    def test_reminder_times_are_chosen_in_the_form(self):
        """2026-10-13（用户：哪天、什么时间都可以自定义）：打开提醒后最多 3 行「当天 / 前 1–7 天」+ 原生时间输入（分钟任选），
        可加可删，默认前一天 08:00、14:00；时间没填全不能保存；保存带 remind_times。"""
        page = read("components/scheduled-tasks.tsx")
        self.assertIn("const REMIND_MAX = 3;", page)
        self.assertIn('const DEFAULT_REMIND: RemindTime[] = [{ days_before: 1, time: "08:00" }, { days_before: 1, time: "14:00" }];',
                      page)
        self.assertIn("Array.from({ length: 8 }, (_, d) => ({", page)
        for key in ("cowork.folder.remind.sameDay", "cowork.folder.remind.dayBefore", "cowork.folder.remind.daysBefore",
                    "cowork.folder.field.remindDay", "cowork.folder.field.remindTime", "cowork.folder.remind.add"):
            self.assertIn(f'tr("{key}"', page)
        self.assertIn('type="time"', page)
        self.assertIn("step={60}", page)
        self.assertIn("onChange={(e) => setRemindTime(i, { time: e.target.value.slice(0, 5) })}", page)
        self.assertIn("{remind && (", page)
        self.assertIn("remindTimes.length < REMIND_MAX", page)
        self.assertIn("remindTimes.length > 1", page)
        self.assertIn("useState<RemindTime[]>(rule?.remind_times?.length ? rule.remind_times : DEFAULT_REMIND)", page)
        self.assertIn("(!remind || remindTimes.every((row) => /^\\d{2}:\\d{2}$/.test(row.time)))", page)
        self.assertIn("...(remind ? { remind_times: remindTimes } : {}),", page)
        # 「添加时间」依次给前 1 天 08:00、14:00、前 2 天 08:00、前 1 天 18:00 里还没有的那个，不拿「当天」当默认
        self.assertIn('const REMIND_NEXT: RemindTime[] = [...DEFAULT_REMIND, { days_before: 2, time: "08:00" }, '
                      '{ days_before: 1, time: "18:00" }];', page)
        self.assertIn("onClick={() => setRemindTimes((rows) => [...rows, nextRemind(rows)])}", page)
        self.assertNotIn('{ days_before: 0, time: "08:00" }', page)
        # 时间不再只能选整点 / 半点（运行时间那一个下拉照旧）
        self.assertNotIn("options={TIMES.map((v) => ({ value: v, label: v }))} onChange={(v) => setRemindTime", page)


class TextTests(unittest.TestCase):
    def test_new_words_exist_in_every_page_language(self):
        i18n = read("i18n.ts")
        messages = block(i18n, "const MESSAGES = {")
        settings = block(i18n, "const SETTINGS = {")
        for lang in ("en", "zh", "th"):
            words = keys(block(messages, f"  {lang}: {{"))
            self.assertTrue({"notice", "newNotice"} <= words, lang)
            words = keys(block(settings, f"  {lang}: {{"))
            self.assertTrue({"notifications", "turnOnNotifications", "notificationsBlocked", "notificationsFailed",
                             "notificationsAddToHome", "notificationsUnsupported"} <= words, lang)
        self.assertNotIn("Express", messages.split("conversationMenu", 1)[1].split("}", 1)[0])


class ShortcutTests(unittest.TestCase):
    """键盘快捷键提示（用户 2026-10-13：iPhone 上用户菜单「การตั้งค่า」旁边显示「Ctrl ,」）：整页只经 components/shortcut.tsx
    显示，触屏设备不显示、CC 桌面端窗口照常显示；是不是触屏只在 lib/touch.ts 判断一次。"""

    KEYS = re.compile(r"Ctrl|⌘|⌥|⇧|⌃|\bCmd\b|\bEsc\b|Shift\s*\+|Alt\s*\+")

    @staticmethod
    def code(text):
        """去掉注释（块注释、JSX 注释、行注释；网址里的 //、"image/*" 这类字符串不算），只留会显示或会执行的部分。"""
        text = re.sub(r"(?<![^\s{(,;])/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
        return re.sub(r"(^|\s)//.*$", r"\1", text, flags=re.M)

    def sources(self):
        for path in sorted(SRC.rglob("*")):
            if path.suffix in (".ts", ".tsx"):
                yield path.relative_to(SRC).as_posix(), self.code(path.read_text(encoding="utf-8"))

    def test_every_shortcut_hint_goes_through_the_shared_component(self):
        hints = []
        for name, text in self.sources():
            for number, line in enumerate(text.splitlines(), 1):
                if not self.KEYS.search(line):
                    continue
                self.assertRegex(line, r'<Shortcut keys="[^"]+"( kbd)? />', f"{name}:{number} 快捷键提示要经 <Shortcut>")
                hints.append(re.search(r'keys="([^"]+)"', line).group(1))
                self.assertNotEqual(name, "components/shortcut.tsx")
            if name != "components/shortcut.tsx":
                self.assertNotIn("<kbd", text, name)
        # 用户菜单的设置，桌面端应用菜单的新建 / 设置 / 重新加载 / 缩放，设置里快捷提问的组合键
        self.assertEqual(sorted(hints), sorted(["Ctrl ,", "Ctrl N", "Ctrl ,", "Ctrl R", "Ctrl =", "Ctrl -", "Ctrl 0",
                                                "Ctrl + Alt + Space"]))

    def test_one_touch_detection_for_the_whole_page(self):
        for name, text in self.sources():
            if name != "lib/touch.ts":
                self.assertNotIn('matchMedia("(hover', text, name)
                self.assertNotIn("pointer: coarse", text, name)
        shortcut = read("components/shortcut.tsx")
        self.assertIn("return isDesktopApp() || !touchOnly();", shortcut)
        self.assertIn("if (!showShortcuts()) return null;", shortcut)
        self.assertIn("const TOUCH = touchOnly();", read("components/assistant-ui/thread.tsx"))


# ── 行为检查（2026-10-13 审查修复）──────────────────────────────────────────────────────────────────
# 把一段 TypeScript 测试脚本连同被测源码（按 tsconfig 的 @/ 路径）用 esbuild 打成一个文件交给 node 跑。
# 浏览器接口在「cc-test-setup」里换成桩；stubs 把某些导入整个换掉（例如 socket.io-client）。

NODE = shutil.which("node")
ESBUILD = FRONTEND / "node_modules/esbuild/lib/main.js"

RUNNER = r"""
const esbuild = require(process.env.CC_ESBUILD);
const fs = require("fs");
const { pathToFileURL } = require("url");
const [harness, stubsFile, outfile, srcDir, tsconfig] = process.argv.slice(2);
const stubs = JSON.parse(fs.readFileSync(stubsFile, "utf8"));
const names = Object.keys(stubs).map((n) => n.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&"));
const plugins = [{
  name: "stubs",
  setup(build) {
    build.onResolve({ filter: new RegExp(`^(${names.join("|")})$`) }, (args) => ({ path: args.path, namespace: "stub" }));
    build.onLoad({ filter: /.*/, namespace: "stub" }, (args) => ({ contents: stubs[args.path], loader: "ts", resolveDir: srcDir }));
  },
}];
(async () => {
  await esbuild.build({
    stdin: { contents: fs.readFileSync(harness, "utf8"), resolveDir: srcDir, loader: "ts", sourcefile: "harness.ts" },
    bundle: true, platform: "node", format: "esm", target: "node20", outfile, tsconfig, logLevel: "error", plugins,
    define: { "process.env.NODE_ENV": '"production"' },
  });
  await import(pathToFileURL(outfile).href);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""

# 浏览器接口的桩：window / document / location / fetch（按白名单方法名记下每次调用，回应由 respond 决定）
SETUP = r"""
const g = globalThis as any;
g.window = g;
g.focus = () => {};
g.location = { origin: "https://site.test", hostname: "site.test", href: "https://site.test/assistant" };
g.document = {
  visibilityState: "visible", hidden: false, addEventListener() {}, removeEventListener() {}, hasFocus: () => false,
  documentElement: { lang: "__LANG__", dataset: {} },
};
g.calls = [] as Array<{ method: string; body: any }>;
g.respond = async (_method: string, _body: any): Promise<any> => ({ ok: true, status: 200, json: async () => ({ message: null }) });
g.fetch = async (url: string, init: any = {}) => {
  const method = String(url).split("pearnly_core.assistant.api.")[1]?.split("?")[0] ?? String(url);
  const body = init.body ? JSON.parse(init.body) : null;
  g.calls.push({ method, body });
  return g.respond(method, body);
};
g.sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
// 跑完：先把 HARNESS OK 写出去再退出（推送那几段留着 within() 的计时器，不等它们）
g.done = () => process.stdout.write("HARNESS OK\n", () => process.exit(0));
"""


def run_ts(source, stubs=None, lang="en"):
    """跑一段测试脚本（开头 import "cc-test-setup"）；脚本出错（断言失败）时把输出带进 AssertionError。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "harness.ts").write_text(source, encoding="utf-8")
        (tmp / "runner.cjs").write_text(RUNNER, encoding="utf-8")
        (tmp / "stubs.json").write_text(
            json.dumps({"cc-test-setup": SETUP.replace("__LANG__", lang), **(stubs or {})}), encoding="utf-8")
        result = subprocess.run(
            [NODE, str(tmp / "runner.cjs"), str(tmp / "harness.ts"), str(tmp / "stubs.json"), str(tmp / "harness.mjs"),
             str(SRC), str(FRONTEND / "tsconfig.json")],
            capture_output=True, text=True, timeout=180, env={**os.environ, "CC_ESBUILD": str(ESBUILD)},
        )
        if result.returncode != 0 or "HARNESS OK" not in result.stdout:
            raise AssertionError(f"harness failed\n{result.stdout}\n{result.stderr}")
        return result.stdout


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class BehaviourTests(unittest.TestCase):
    def test_notice_card_keeps_the_thinking_placeholder(self):
        # frontend-6：卡片是 system 消息。回复还没来时组件库在卡片后面照样补「思考中」占位；回复进行中时回复一直是最后一条
        # （不会多出第二个占位）；跑完卡片回到回复后面。用组件库自己的外部数据运行时算出实际显示的消息。
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { ExternalStoreThreadRuntimeCore } from "@assistant-ui/core/internal";
import { withNotices, type NoticeItem, type ThreadMessage } from "@/lib/managed-agents/reducer";

const g = globalThis as any;
const at = (s: number) => new Date(Date.UTC(2026, 9, 12, 8, 0, 0) + s * 1000);
const user: ThreadMessage = { id: "u2", role: "user", content: [{ type: "text", text: "hi" }], createdAt: at(0) };
const reply = (running: boolean): ThreadMessage => ({
  id: "a2", role: "assistant", content: [{ type: "text", text: "working" }], createdAt: at(2),
  status: running ? { type: "running" } : { type: "complete", reason: "stop" },
});
const notice: NoticeItem = { id: "pnotice_1", createdAt: at(5), kind: "cowork.folder.brief", title: "Run finished", text: "12 pushed" };
const older: NoticeItem = { id: "pnotice_0", createdAt: at(-60), kind: "cowork.folder.remind", title: "Tomorrow", text: "19:00" };

function shown(messages: readonly ThreadMessage[], notices: readonly NoticeItem[], isRunning: boolean) {
  const core = new ExternalStoreThreadRuntimeCore(
    { getModelContext: () => ({}), subscribe: () => () => {} } as any,
    { messages: withNotices(messages, notices) as any, isRunning, convertMessage: (m: any) => m } as any,
  );
  return core.messages.map((m: any) => (m.metadata?.isOptimistic ? `placeholder:${m.status?.type}` : `${m.id}:${m.role}`));
}

// 审查场景：发出一句，回复还没到，完成简报推来 —— 以前卡片是 assistant，占位被去掉
assert.deepEqual(shown([user], [notice], true), ["u2:user", "pnotice_1:system", "placeholder:running"]);
// 回复进行中又来一条：回复保持最后一条，不再补第二个占位
assert.deepEqual(shown([user, reply(true)], [notice], true), ["u2:user", "pnotice_1:system", "a2:assistant"]);
// 跑完：卡片在回复后面（刷新后同一位置）
assert.deepEqual(shown([user, reply(false)], [notice], false), ["u2:user", "a2:assistant", "pnotice_1:system"]);
// 比用户消息早的仍插在它前面
assert.deepEqual(shown([user, reply(false)], [older, notice], false), ["pnotice_0:system", "u2:user", "a2:assistant", "pnotice_1:system"]);
// 组件库收下 system 消息并保留卡片信息；同一条提醒同一个对象
const core = new ExternalStoreThreadRuntimeCore(
  { getModelContext: () => ({}), subscribe: () => () => {} } as any,
  { messages: withNotices([user], [notice]) as any, isRunning: false, convertMessage: (m: any) => m } as any,
);
const card: any = core.messages.find((m: any) => m.role === "system");
assert.equal(card.metadata.custom.notice.title, "Run finished");
assert.equal(card.content[0].text, "12 pushed");
assert.equal(withNotices([user], [notice])[1], withNotices([user], [notice])[1]);
g.done();
""")

    def test_page_notification_goes_through_the_service_worker(self):
        # frontend-1/2：安卓 Chrome 的 new Notification 抛 Illegal constructor。浏览器里一律交给后台脚本弹（带深链接），
        # 桌面端窗口才用 new Notification；任何一步出错都不往外抛。
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { deepLink, showNote } from "@/lib/notify";

const g = globalThis as any;
class AndroidNotification {
  static permission = "granted";
  constructor() { throw new TypeError("Failed to construct 'Notification': Illegal constructor."); }
}
const shown: any[] = [];
const worker: any = {
  getRegistration: async (scope: string) => {
    assert.equal(scope, "/assistant");
    return { showNotification: async (title: string, options: any) => void shown.push({ title, ...options }) };
  },
};
g.Notification = AndroidNotification;
Object.defineProperty(g, "navigator", { configurable: true, value: { serviceWorker: worker } });

assert.equal(deepLink("sesn_1"), "/assistant?c=sesn_1");
assert.equal(deepLink(""), "/assistant?view=tasks");
assert.equal(await showNote("CC", "done", "tomo-reply", deepLink("sesn_1")), true);
assert.deepEqual(shown, [{ title: "CC", body: "done", tag: "tomo-reply", data: { url: "/assistant?c=sesn_1" } }]);

// 后台脚本也弹不出来：回 false，不抛
worker.getRegistration = async () => ({ showNotification: async () => { throw new TypeError("No active registration"); } });
assert.equal(await showNote("CC", "x", "notice:pnotice_1", "/assistant?view=tasks"), false);
// 没注册后台脚本、构造函数又不让用：照样不抛
worker.getRegistration = async () => undefined;
assert.equal(await showNote("CC", "x", "notice:pnotice_1", "/assistant?view=tasks"), false);

// 桌面端窗口：有没有后台脚本都用 new Notification，点了打开那段对话
const built: any[] = [];
class DesktopNotification {
  static permission = "granted";
  onclick: null | (() => void) = null;
  constructor(public title: string, public options: any) { built.push(this); }
}
g.Notification = DesktopNotification;
g.qt = { webChannelTransport: {} };
worker.getRegistration = async () => { throw new Error("desktop window must not use the worker"); };
let opened = 0;
assert.equal(await showNote("T", "B", "notice:pnotice_2", "/assistant?c=sesn_2", () => opened++), true);
assert.equal(built.length, 1);
assert.deepEqual(built[0].options, { body: "B", tag: "notice:pnotice_2" });
built[0].onclick();
assert.equal(opened, 1);
delete g.qt;

// 没允许通知：什么都不弹
(DesktopNotification as any).permission = "default";
assert.equal(await showNote("T", "B", "x", "/assistant"), false);
assert.equal(built.length, 1);
g.done();
""")

    def test_realtime_and_activity_survive_a_broken_listener_and_follow_the_view(self):
        # frontend-2：会话的页面先收到，一个监听者出错不挡别的。frontend-5：在定时任务页时当前对话的提醒不算看过，
        # 回到对话页才报 notices_seen。frontend-8：列表说看过了就去掉圆点，请求发出后才到的留着。
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { dispatch, subscribe } from "@/lib/managed-agents/realtime";
import {
  activityOf, clearUnread, noticesShown, onNotice, seedUnread, setActiveSession, setChatView,
} from "@/lib/managed-agents/activity";

const g = globalThis as any;
const notice = (id: string, session: string) =>
  ({ type: "pearnly.notice", id, processed_at: "2026-10-12T08:00:00Z", kind: "k", title: "t", text: "x", session }) as any;

const order: string[] = [];
onNotice(() => { order.push("broken"); throw new TypeError("Illegal constructor"); });
onNotice(() => void order.push("sidebar"));
subscribe("S1", (event: any) => void order.push(`page:${event.id}`), () => {});
dispatch({ session_id: "S1", event: notice("pnotice_1", "S1") });
assert.deepEqual(order, ["page:pnotice_1", "broken", "sidebar"]);
order.length = 0;
subscribe("S1", () => { throw new Error("boom"); }, () => {});
dispatch({ session_id: "S1", event: notice("pnotice_2", "S1") });
assert.deepEqual(order, ["page:pnotice_2", "broken", "sidebar"]);

// 定时任务页：当前对话 S2 推来提醒，卡片在后台对话里也算显示出来了，但没人在看
const seen = () => g.calls.filter((c: any) => c.method === "notices_seen").map((c: any) => c.body.session_id);
setActiveSession("S2");
setChatView(false);
dispatch({ session_id: "S2", event: notice("pnotice_3", "S2") });
noticesShown("S2", "pnotice_3");
await g.sleep(5);
assert.deepEqual(seen(), []);
assert.equal(activityOf("S2"), "notice");
setChatView(true);
await g.sleep(5);
assert.deepEqual(seen(), ["S2"]);
assert.equal(activityOf("S2"), undefined);

// 侧栏列表：别处看过了（unread 为假）去掉圆点；列表请求发出之后才到的提醒留着
seedUnread("S3");
assert.equal(activityOf("S3"), "notice");
clearUnread("S3", Date.now() + 1);
assert.equal(activityOf("S3"), undefined);
const asked = Date.now();
seedUnread("S4");
clearUnread("S4", asked);
assert.equal(activityOf("S4"), "notice");
g.done();
""", stubs={"socket.io-client": "export function io() { return { on() {}, off() {}, connected: true }; }"})

    def test_notice_reload_is_merged_and_waits_for_the_list(self):
        # frontend-4：没加载到的对话来了几条提醒只重拉一次；列表在加载、加载更多或新对话正在建时先不拉；到时已在列表就不拉
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { noticeReloader } from "@/lib/managed-agents/notice-reload";

const g = globalThis as any;
const state: any = { threadIds: ["a"], threadItems: [{ id: "a", remoteId: "a", status: "regular" }], isLoading: false, isLoadingMore: false };
let reloads = 0;
const threads = { getState: () => state, reload: async () => void reloads++ };
const reloader = noticeReloader(() => threads, 10);

reloader.note("old1");
reloader.note("old2");
reloader.note("old1");
reloader.note("a");  // 已在列表
await g.sleep(50);
assert.equal(reloads, 1);  // 以前每条没加载到的提醒各重拉一次

state.isLoadingMore = true;
reloader.note("old3");
await g.sleep(50);
assert.equal(reloads, 1);
state.isLoadingMore = false;
await g.sleep(50);
assert.equal(reloads, 2);

state.threadItems = [...state.threadItems, { id: "__LOCALID_x", remoteId: undefined, status: "regular" }];
reloader.note("old4");
await g.sleep(50);
assert.equal(reloads, 2);
state.threadItems = state.threadItems.filter((item: any) => item.remoteId);
await g.sleep(50);
assert.equal(reloads, 3);

reloader.note("old5");
state.threadIds = [...state.threadIds, "old5"];
state.threadItems = [...state.threadItems, { id: "old5", remoteId: "old5", status: "regular" }];
await g.sleep(50);
assert.equal(reloads, 3);
reloader.stop();
g.done();
""")

    PUSH_STUBS = {
        "@/lib/desktop": "export const isDesktopApp = () => false;",
        "@/lib/install": "export const isIOS = () => false; export const standalone = () => false;",
    }

    PUSH_SETUP = r"""
const g = globalThis as any;
g.PushManager = class {};
g.Notification = { permission: "granted" };
g.unsubscribed = 0;
g.subscription = {
  endpoint: "https://push.test/device-1",
  options: { applicationServerKey: new Uint8Array([4, 1, 2]).buffer },
  toJSON: () => ({ keys: { p256dh: "p", auth: "a" } }),
  unsubscribe: async () => { g.unsubscribed++; return true; },
};
g.existing = null;
g.subscribeResult = async () => g.subscription;
g.registration = {
  pushManager: { getSubscription: async () => g.existing, subscribe: async () => g.subscribeResult() },
};
Object.defineProperty(g, "navigator", {
  configurable: true,
  value: { serviceWorker: { ready: Promise.resolve(g.registration), getRegistration: async () => g.registration } },
});
const ok = (message: any = null) => ({ ok: true, status: 200, json: async () => ({ message }) });
g.respond = async (method: string) => (method === "push_key" ? ok({ key: "BAEC" }) : ok());
"""

    def test_sign_out_forgets_the_device_whatever_the_switch_shows(self):
        # frontend-3 / security-2（页面这一半）：同步还没走完（状态 unknown）时退出也让本站删掉这台设备；
        # 退出之后同步不再把设备认领回来。
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { currentPush, forgetDevice, syncPush } from "@/lib/push";
""" + self.PUSH_SETUP + r"""

g.existing = g.subscription;
assert.equal(currentPush().state, "unknown");
await forgetDevice();
assert.deepEqual(g.calls.map((c: any) => [c.method, c.body?.endpoint]), [["push_unsubscribe", "https://push.test/device-1"]]);
// 退出后（页面还没跳走时）同步不再认领
await syncPush();
assert.equal(g.calls.some((c: any) => c.method === "push_subscribe"), false);
g.done();
""", stubs=self.PUSH_STUBS)

    def test_sign_out_waits_for_a_claim_already_on_the_way(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { forgetDevice, syncPush } from "@/lib/push";
""" + self.PUSH_SETUP + r"""

g.existing = g.subscription;
const events: string[] = [];
let land: () => void = () => {};
g.respond = async (method: string) => {
  if (method === "push_key") return ok({ key: "BAEC" });
  if (method === "push_subscribe") {
    await new Promise<void>((resolve) => { land = resolve; });
    events.push("claimed");
    return ok();
  }
  events.push(method);
  return ok();
};
void syncPush();
while (!g.calls.some((c: any) => c.method === "push_subscribe")) await g.sleep(1);
const leaving = forgetDevice();
await g.sleep(30);
land();
await leaving;
assert.deepEqual(events, ["claimed", "push_unsubscribe"]);  // 晚到的认领不会盖过退出
g.done();
""", stubs=self.PUSH_STUBS)

    def test_turning_on_fails_with_a_reason_and_both_sides_off(self):
        # frontend-7：本站拒收给原文、别的失败给「此设备无法接收通知」；浏览器订阅上了本站没收下就退掉；
        # 只是关掉了询问不算失败；成功清掉原因。
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { currentPush, enablePush, syncPush } from "@/lib/push";
""" + self.PUSH_SETUP + r"""

await syncPush();
assert.equal(currentPush().state, "off");

const refused = JSON.stringify([JSON.stringify({ message: "This device cannot receive notifications." })]);
g.respond = async (method: string) =>
  method === "push_subscribe" ? { ok: false, status: 417, json: async () => ({ _server_messages: refused }) } : ok();
await enablePush();
assert.deepEqual(currentPush(), { state: "off", failure: "This device cannot receive notifications." });
assert.equal(g.unsubscribed, 1);

g.respond = async (method: string) => { if (method === "push_subscribe") throw new TypeError("Failed to fetch"); return ok(); };
await enablePush();
assert.deepEqual(currentPush(), { state: "off", failure: "此设备无法接收通知。" });
assert.equal(g.unsubscribed, 2);

g.Notification.permission = "default";
g.subscribeResult = async () => { throw new DOMException("Registration failed - permission denied", "NotAllowedError"); };
await enablePush();
assert.deepEqual(currentPush(), { state: "off", failure: "" });

g.Notification.permission = "granted";
g.subscribeResult = async () => g.subscription;
g.respond = async () => ok();
await enablePush();
assert.deepEqual(currentPush(), { state: "on", failure: "" });
g.done();
""", stubs=self.PUSH_STUBS, lang="zh")


    # ── 触屏与「通知」一行（用户 2026-10-13，iPhone 主屏幕 app 泰文界面录制时发现）─────────────────────────────────────
    # 设备模拟在页面模块加载前就位：触屏（hover: none）、装没装成主屏幕 app（display-mode / navigator.standalone）、
    # 浏览器有没有网页推送（PushManager / Notification）。用 react-dom/server 渲染真实组件，看显示出来的 HTML。
    IOS_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS {v} like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
              "Version/{s} Mobile/15E148 Safari/604.1")
    IPAD_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 "
               "Safari/605.1.15")
    DEVICES = {
        # iPhone 的 Safari 页签：没有网页推送，要先添加到主屏幕
        "iphone-safari": {"ua": IOS_UA.format(v="18_6", s="18.6"), "touch": True, "standalone": False, "push": False},
        # iPhone 主屏幕 app（iOS 16.4 起有网页推送）
        "iphone-home": {"ua": IOS_UA.format(v="18_6", s="18.6"), "touch": True, "standalone": True, "push": True},
        # iOS 16.3 的主屏幕 app：还没有网页推送
        "iphone-old-home": {"ua": IOS_UA.format(v="16_3", s="16.3"), "touch": True, "standalone": True, "push": False},
        # 安卓 LINE 内置浏览器：没有网页推送
        "android-line": {"ua": "Mozilla/5.0 (Linux; Android 14; Pixel 8; wv) AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Version/4.0 Chrome/129.0.0.0 Mobile Safari/537.36 Line/14.15.0",
                         "touch": True, "standalone": False, "push": False},
        "windows-chrome": {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                 "Chrome/129.0.0.0 Safari/537.36", "touch": False, "standalone": False, "push": True},
    }

    DEVICE = r"""
const g = globalThis as any;
g.device = __DEVICE__;
g.addEventListener = () => {};
g.removeEventListener = () => {};
g.dispatchEvent = () => true;
g.matchMedia = (query: string) => ({
  media: query,
  matches: query === "(display-mode: standalone)" ? g.device.standalone : /hover: none|pointer: coarse/.test(query) && g.device.touch,
  addEventListener() {}, removeEventListener() {},
});
g.existing = null;
g.registration = { pushManager: { getSubscription: async () => g.existing, subscribe: async () => g.existing } };
const nav: any = {
  userAgent: g.device.ua, maxTouchPoints: g.device.touch ? 5 : 0, language: "en",
  serviceWorker: { register: async () => g.registration, ready: Promise.resolve(g.registration), getRegistration: async () => g.registration },
};
if (/iPhone|iPad|Macintosh/.test(g.device.ua) && g.device.touch) nav.standalone = g.device.standalone;
Object.defineProperty(g, "navigator", { configurable: true, value: nav });
if (g.device.push) {
  g.PushManager = class {};
  g.Notification = { permission: g.device.permission ?? "default" };
}
g.ok = (message: any = null) => ({ ok: true, status: 200, json: async () => ({ message }) });
g.respond = async (method: string) => (method === "push_key" ? g.ok({ key: "BAEC" }) : g.ok());
"""

    PAGE = r"""
import "cc-test-setup";
import "cc-device";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";  // 浏览器版：打成 ESM 后不用 require 内置模块
import { Notifications } from "@/components/settings";
import { Shortcut, showShortcuts } from "@/components/shortcut";
import { currentPush, enablePush, syncPush } from "@/lib/push";

const g = globalThis as any;
const html = (type: any, props: any = null) => renderToStaticMarkup(createElement(type, props));
const row = () => html(Notifications);
// 显示出来的文字（按元素分行）与开关那个按钮
const text = (markup: string) =>
  markup.replace(/<[^>]+>/g, "\n").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").split("\n").map((s) => s.trim()).filter(Boolean);
const toggle = (markup: string) => /<button[^>]*role="switch"[^>]*>/.exec(markup)?.[0] ?? "";
"""

    def on_device(self, kind, checks, lang="en", **overrides):
        device = {**self.DEVICES[kind], **overrides}
        run_ts(self.PAGE + checks + "\ng.done();\n", lang=lang, stubs={
            "cc-device": self.DEVICE.replace("__DEVICE__", json.dumps(device)),
            # profile.ts 用 Vite 的 import.meta.glob 取品牌图，这里用不到
            "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;',
        })

    def test_touch_devices_show_no_keyboard_shortcuts(self):
        # iPhone 主屏幕 app（泰文）：用户菜单「การตั้งค่า」旁边不再有「Ctrl ,」；接鼠标的电脑浏览器、CC 桌面端窗口照常显示
        self.on_device("iphone-home", r"""
assert.equal(showShortcuts(), false);
assert.equal(html(Shortcut, { keys: "Ctrl ," }), "");
assert.equal(html(Shortcut, { keys: "Ctrl + Alt + Space", kbd: true }), "");
assert.doesNotMatch(row(), /Ctrl/);

g.device.touch = false;
assert.equal(showShortcuts(), true);
assert.equal(html(Shortcut, { keys: "Ctrl ," }), '<span class="text-ink-3 text-xs">Ctrl ,</span>');
assert.match(html(Shortcut, { keys: "Ctrl + Alt + Space", kbd: true }), /^<kbd class="[^"]*">Ctrl \+ Alt \+ Space<\/kbd>$/);

g.device.touch = true;
g.qt = { webChannelTransport: {} };
assert.equal(showShortcuts(), true);
assert.equal(html(Shortcut, { keys: "Ctrl N" }), '<span class="text-ink-3 text-xs">Ctrl N</span>');
""", lang="th")

    def test_iphone_safari_tab_shows_the_row_with_add_to_home_screen(self):
        self.on_device("iphone-safari", r"""
await syncPush();
assert.equal(currentPush().state, "install");
const markup = row();
assert.deepEqual(text(markup), ["การแจ้งเตือน", "เพิ่ม CC ไปยังหน้าจอโฮมเพื่อเปิดการแจ้งเตือน"]);
assert.match(toggle(markup), /aria-checked="false"/);
assert.match(toggle(markup), /aria-label="การแจ้งเตือน"/);
assert.match(toggle(markup), /disabled=""/);
assert.equal(g.calls.some((c: any) => c.method === "push_key"), false);  // 页签里不去取公钥
""", lang="th")
        # iPad 的 Safari 自称 Mac（有触屏）：同样要先添加到主屏幕
        self.on_device("iphone-safari", r"""
await syncPush();
assert.equal(currentPush().state, "install");
assert.deepEqual(text(row()), ["Notifications", "Add CC to your Home Screen to turn on notifications"]);
""", ua=self.IPAD_UA)

    def test_devices_without_web_push_say_so(self):
        for kind, lang, label, reason in (("iphone-old-home", "en", "Notifications", "Notifications aren't supported on this device"),
                                          ("android-line", "zh", "通知", "此设备不支持通知")):
            with self.subTest(kind):
                self.on_device(kind, r"""
await syncPush();
assert.equal(currentPush().state, "unsupported");
const markup = row();
assert.deepEqual(text(markup), [LABEL, REASON]);
assert.match(toggle(markup), /disabled=""/);
""".replace("LABEL", json.dumps(label)).replace("REASON", json.dumps(reason, ensure_ascii=False)), lang=lang)

    def test_blocked_in_settings_keeps_its_reason(self):
        self.on_device("windows-chrome", r"""
await syncPush();
assert.equal(currentPush().state, "denied");
const markup = row();
assert.deepEqual(text(markup), ["Notifications", "Blocked in browser settings"]);
assert.match(toggle(markup), /disabled=""/);
""", permission="denied")

    def test_supported_devices_keep_the_switch(self):
        # 电脑浏览器、iPhone 主屏幕 app：开关能点，打开照旧（订阅交给本站）
        for kind in ("windows-chrome", "iphone-home"):
            with self.subTest(kind):
                self.on_device(kind, r"""
await syncPush();
assert.equal(currentPush().state, "off");
let markup = row();
assert.deepEqual(text(markup), ["Notifications"]);
assert.doesNotMatch(toggle(markup), / disabled=""/);
assert.match(toggle(markup), /aria-checked="false"/);

g.Notification.permission = "granted";
g.existing = {
  endpoint: "https://push.test/device-1",
  options: { applicationServerKey: new Uint8Array([4, 1, 2]).buffer },
  toJSON: () => ({ keys: { p256dh: "p", auth: "a" } }),
  unsubscribe: async () => true,
};
await enablePush();
assert.equal(currentPush().state, "on");
markup = row();
assert.doesNotMatch(toggle(markup), / disabled=""/);
assert.match(toggle(markup), /aria-checked="true"/);
assert.deepEqual(g.calls.filter((c: any) => c.method === "push_subscribe").map((c: any) => c.body.endpoint), ["https://push.test/device-1"]);
""")

    def test_desktop_window_has_no_row_and_a_slow_start_is_retried(self):
        self.on_device("windows-chrome", r"""
g.qt = { webChannelTransport: {} };
await syncPush();
assert.equal(currentPush().state, "desktop");
assert.equal(row(), "");
""")
        # 公钥一时没取到：不说「不支持」，开关灰着；再同步一次（打开设置时）就好了
        self.on_device("windows-chrome", r"""
g.respond = async () => ({ ok: false, status: 502, json: async () => ({}) });
await syncPush();
assert.equal(currentPush().state, "unknown");
let markup = row();
assert.deepEqual(text(markup), ["Notifications"]);
assert.match(toggle(markup), /disabled=""/);
g.respond = async (method: string) => (method === "push_key" ? g.ok({ key: "BAEC" }) : g.ok());
await syncPush();
assert.equal(currentPush().state, "off");
assert.doesNotMatch(toggle(row()), / disabled=""/);
assert.equal(g.calls.filter((c: any) => c.method === "push_key").length, 2);
""")


    # ── 界面语言（用户 2026-10-13）：站点按共用层规则定页面语言，页面照它显示、每个请求都带着它 ──────────────
    # 页面里记下每个请求带的语言；站点回的提示、步骤说明、术语按这个语言出（pearnly_core.account.preferences.use_page_language）
    LANGUAGE_PROBE = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { lang, steps, t } from "@/i18n";
import { chooseLanguage, get, getMethod, post, postMethod, upload } from "@/lib/frappe";

const g = globalThis as any;
const sent: Array<{ url: string; lang: string; credentials: string }> = [];
const stub = g.fetch;
g.fetch = async (url: string, init: any = {}) => {
  sent.push({ url: String(url), lang: init.headers?.["X-Pearnly-Lang"], credentials: init.credentials });
  return stub(url, init.body instanceof FormData ? {} : init);
};
async function everyRequest() {
  await get("tool_labels");
  await post("send", { session_id: "sesn_1", text: "hi" });
  await upload("upload", { session_id: "sesn_1" }, new File(["x"], "a.txt"));
  await getMethod("pearnly_cowork.folder_intake.api.terms");
  await postMethod("pearnly_cowork.folder_intake.api.run_now", { name: "R-1" });
  return sent.splice(0);
}
"""

    def page_language(self, lang, checks):
        run_ts(self.LANGUAGE_PROBE + checks + "\ng.done();\n", lang=lang)

    def test_first_sign_in_on_a_thai_system_with_a_chinese_account_is_thai(self):
        # 新电脑，Windows 是泰文：登录页按系统语言显示泰文（没点）；账号里以前存的是中文
        shown = ui_language.from_accept_language("th-TH,th;q=0.9,en-US;q=0.8")
        choice = ui_language.sign_in_choice(device="", shown=shown)
        self.assertEqual(choice, "th")  # 这台设备记住泰文，账号语言也改成泰文（账号这一半见 tests/test_assistant.py）
        page = ui_language.interface(device=choice, account=choice, accept_language="th-TH,th;q=0.9")
        self.page_language(page, r"""
assert.equal(lang, "th");
assert.equal(t("newChat"), "สร้างใหม่");
assert.equal(steps.thinkingNow, "กำลังคิด");
for (const request of await everyRequest()) {
  assert.equal(request.lang, "th", request.url);
  assert.equal(request.credentials, "same-origin", request.url);
}
""")

    def test_a_device_that_remembers_chinese_stays_chinese_on_a_thai_system(self):
        self.assertEqual(ui_language.sign_in_choice(device="zh-CN", shown="th"), "")  # 登录页显示泰文也不改
        page = ui_language.interface(device="zh-CN", account="th", accept_language="th-TH,th;q=0.9")
        self.page_language(page, r"""
assert.equal(lang, "zh");
assert.equal(t("newChat"), "新建");
const requests = await everyRequest();
assert.equal(requests.length, 5);
assert.deepEqual([...new Set(requests.map((r) => r.lang))], ["zh"]);
""")

    def test_explicit_pick_is_saved_and_the_page_reopens_in_it(self):
        self.page_language("zh", r"""
let reloaded = 0;
g.location.reload = () => void reloaded++;
await chooseLanguage("th");
assert.deepEqual(g.calls.at(-1), { method: "/api/method/pearnly_core.account.preferences.set_language", body: { language: "th" } });
assert.equal(sent.at(-1)?.lang, "zh");  // 这一次还是旧页面发的
assert.equal(reloaded, 1);
""")
        # 站点收下后记在这台设备上（Cookie）并存成账号语言；重新打开时按设备记住的出泰文，系统是中文也一样
        self.assertEqual(ui_language.interface(device="th", account="th", accept_language="zh-CN,zh"), "th")

    def test_languages_the_page_does_not_have_show_english(self):
        self.page_language("ja", r"""
assert.equal(lang, "en");
assert.equal(t("newChat"), "New chat");
assert.equal((await everyRequest())[0].lang, "en");
""")
        self.page_language("zh-CN", 'assert.equal(lang, "zh");')


if __name__ == "__main__":
    unittest.main()
