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
        # 没挂对话的提醒按服务端给的 view 点开（定时任务页 / 待处理 / 公司主档页，ADR-020）
        self.assertIn('const link = deepLink(notice.session || "", notice.view || "");', alert)
        self.assertIn("openTarget(notice.session || link)", alert)
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
        # 公司主档（2026-10-15）：三种页面语言的词条一样多、一个不缺；设置里「以前授权的文件夹」
        company = block(i18n, "const COMPANY = {")
        sets = {}
        for lang in ("en", "zh", "th"):
            words = block(company, f"  {lang}: {{")
            sets[lang] = keys(words) | keys(block(words, "kind: {"))
            self.assertTrue({"companies", "addCompany", "added", "refreshSets", "notes", "docFolder", "monthFolder", "accountSets",
                             "taxDrafts", "remembered", "noPeriod", "period", "branch", "noBranch", "headOffice", "branchShort",
                             "combinedVat", "moveTo", "showMore", "deleteCompany", "chooseOnComputer", "changeFolder",
                             "removeFolder", "updateDesktop", "folderOk", "folderWaiting", "folderOffline", "folderMissing",
                             "payCash", "payCredit", "vatClaim", "vatExpense", "pending", "handToCC", "handToCCText",
                             "fromHistory", "history", "vatCheck"} <= sets[lang], lang)
            self.assertIn("earlierFolders:", block(settings, f"  {lang}: {{"), lang)
        self.assertEqual(sets["en"], sets["zh"])
        self.assertEqual(sets["en"], sets["th"])
        # 用户可见处叫「CC 桌面版」，不叫小助手 / Companion
        self.assertNotRegex(company, r"小助手|Companion")
        # 侧栏的分组和排序、批量项、组标题菜单（CC 外壳设计 3.4–3.7）：三种语言一样多
        sidebar = block(i18n, "const SIDEBAR = {")
        words = {lang: keys(block(sidebar, f"  {lang}: {{")) for lang in ("en", "zh", "th")}
        self.assertTrue({"groupAndSort", "groupBy", "none", "sortBy", "lastUpdated", "dateCreated", "name", "descending", "ascending",
                         "show", "metadata", "emptyGroups", "resetDefaults", "noMonth", "newChatHere", "pinN", "unpinN", "moveToN",
                         "deleteN"} <= words["en"])
        self.assertEqual(words["en"], words["zh"])
        self.assertEqual(words["en"], words["th"])
        self.assertIn('groupAndSort: "分组和排序", groupBy: "分组依据"', sidebar)
        self.assertIn('resetDefaults: "恢复默认设置"', sidebar)


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
assert.deepEqual(text(markup), ["Notifications", "Blocked in system or browser settings"]);
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


# ── 自动更新被 Windows 拦下（教程问题第 1 条，2026-10-13 录制笔记本）───────────────────────────────────────
# CC 桌面版设置「版本」一行按桌面端报的真实状态走（发现新版 → 安装中 → 被拦 → 手动装上）；网页「连接」只读，如实多一句。

UPDATE_ENV = r"""
const g = globalThis as any;
g.addEventListener = () => {};
g.removeEventListener = () => {};
g.dispatchEvent = () => true;
g.matchMedia = (query: string) => ({ media: query, matches: false, addEventListener() {}, removeEventListener() {} });
Object.defineProperty(g, "navigator", {
  configurable: true, value: { userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", maxTouchPoints: 0, language: "en" },
});
"""

UPDATE_PAGE = r"""
import "cc-test-setup";
import "cc-update-env";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { blockedLine, ComputerSection, VersionRow } from "@/components/settings";

const g = globalThis as any;
const html = (type: any, props: any = null) => renderToStaticMarkup(createElement(type, props));
const text = (markup: string) =>
  markup.replace(/<[^>]+>/g, "\n").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").split("\n").map((s) => s.trim()).filter(Boolean);
// 点某个文字的按钮：静态渲染点不了，从元素树里取它的 onClick
function press(element: any, label: string): boolean {
  if (!element || typeof element !== "object") return false;
  if (Array.isArray(element)) return element.some((child) => press(child, label));
  const props = element.props ?? {};
  if (props.onClick && props.children === label) {
    props.onClick();
    return true;
  }
  return press(props.children, label);
}
const clicked: string[] = [];
const row = (version: string, state: string, next = "1.5.2") => {
  const props = { version, update: { state, version: next }, onUpdate: () => clicked.push("update"), onDownload: () => clicked.push("download") };
  return { lines: text(html(VersionRow, props)), element: VersionRow(props) };
};
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class UpdateBlockedBehaviourTests(unittest.TestCase):
    STUBS = {"@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;',
             "cc-update-env": UPDATE_ENV}

    def run_page(self, checks, lang):
        run_ts(UPDATE_PAGE + checks + "\ng.done();\n", stubs=self.STUBS, lang=lang)

    def test_the_version_row_follows_the_desktop_from_new_version_to_blocked_to_installed_by_hand(self):
        self.run_page(r"""
let view = row("1.5.1", "found");
assert.deepEqual(view.lines, ["版本", "v1.5.1", "发现新版本 v1.5.2", "立即更新"]);
assert.ok(press(view.element, "立即更新"));

view = row("1.5.1", "installing");                    // 停手、启动安装包：没有按钮
assert.deepEqual(view.lines, ["版本", "v1.5.1", "正在安装 v1.5.2…"]);

view = row("1.5.1", "blocked");                       // 被 Windows 拦下：一句话 +「下载新版」，不再给「立即更新」
assert.deepEqual(view.lines, ["版本", "v1.5.1", "Windows 拦截了 v1.5.2 的自动更新", "下载新版"]);
assert.equal(press(view.element, "立即更新"), false);
assert.ok(press(view.element, "下载新版"));
assert.deepEqual(clicked, ["update", "download"]);

view = row("1.5.2", "none", "");                      // 用「下载新版」手动装上：桌面端清掉记录，回到平常
assert.deepEqual(view.lines, ["版本", "v1.5.2", "检查更新"]);
""", lang="zh")

    def test_web_connections_say_it_in_one_line_with_the_time_where_the_reader_is(self):
        self.run_page(r"""
process.env.TZ = "Asia/Bangkok";                      // 读的人在曼谷：时刻按曼谷显示
const at = new Date(Date.now() - 86_400_000);
const computer = { adapter: "express", device: "NOTEBOOK-REC", online: true, version: "1.5.1", folders: [] as string[],
                   update_blocked: { version: "1.5.2", at: at.toISOString().replace(".000Z", "+00:00") } };
const hhmm = new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" }).format(at);
// 文件夹按公司在主档页看（ADR-020，2026-10-15）：网页「连接」每台电脑下不再列文件夹
assert.deepEqual(text(html(ComputerSection, { row: computer })),
  ["NOTEBOOK-REC", "Express", `Windows blocked the automatic update to v1.5.2 · Yesterday ${hhmm}`, "Connected"]);
// 服务端存的是 UTC：11:18 UTC = 曼谷 18:18（英文页面是 12 小时制，同对话里的时间）
assert.match(blockedLine("1.5.2", "2026-10-13T11:18:00+00:00"), / 06:18 PM$/);
// 没被拦、旧版 CC 桌面版不报：没有这一句
for (const blocked of [null, undefined]) {
  assert.deepEqual(text(html(ComputerSection, { row: { ...computer, update_blocked: blocked } })).slice(0, 3),
    ["NOTEBOOK-REC", "Express", "Connected"]);
}
""", lang="en")

    def test_every_page_language_has_the_two_new_words(self):
        settings = block(read("i18n.ts"), "const SETTINGS = {")
        for lang in ("en", "zh", "th"):
            words = block(settings, f"  {lang}: {{")
            self.assertIn("downloadNew:", words, lang)
            self.assertRegex(block(words, "update: {"), r"blocked: \"[^\"]*\{ver\}[^\"]*\"", lang)


# ── 「允许」卡片的卡片规矩（用户 2026-10-13 第 5、12 条）──────────────────────────────────────────────────────
# 一句话改几个定时任务只弹一张卡片，卡片照 Claude 对话里卡片的规矩：按钮最多两个、在底部；改动最多 5 行，多了列前几行
# + 「还有 N 项」；卡内不套滚动；手机点按区不小于 44pt；颜色只用 globals.css 的变量。用 react-dom/server 渲染真实组件。

APPROVAL_CARD = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";  // 浏览器版：打成 ESM 后不用 require 内置模块
import { ApprovalBar, CARD_ROWS, cardRows } from "@/components/tool-uis/approval-bar";

const g = globalThis as any;
// 允许卡片上一个任务一行（服务端 preview_set_folder_tasks_enabled 的形状）
const rows = (n: number) => Array.from({ length: n }, (_, i) => ({
  field: `Task ${i + 1}`, before: "Reminder: Day before 08:00", after: "Reminder: 2 days before 09:00" }));
const card = (n: number) => renderToStaticMarkup(createElement(ApprovalBar, {
  approval: { id: "sevt_1", preview: rows(n) }, respondToApproval: () => {} }));
const items = (html: string) => (html.match(/<li/g) ?? []).length;
const buttons = (html: string) => html.match(/<button[^>]*>/g) ?? [];
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class ApprovalCardBehaviourTests(unittest.TestCase):
    def test_one_card_for_several_tasks_follows_the_card_rules(self):
        run_ts(APPROVAL_CARD + r"""
// 教程里的一句话改 3 个任务的提醒：一张卡片全列出，两个按钮在改动下面
let html = card(3);
assert.equal(items(html), 3);
assert.doesNotMatch(html, /more/);
assert.equal(buttons(html).length, 2);
assert.ok(html.lastIndexOf("</ul>") < html.indexOf("<button"));
// 手机点按区不小于 44pt（与页面上其它圆按钮同一个尺寸变量）
for (const button of buttons(html)) assert.match(button, /pointer-coarse:h-\(--cc-mbtn-size\)/);
// 卡内不套滚动
assert.doesNotMatch(card(20), /overflow|max-h/);
// 正好 5 行：全列；6 行：前 4 行 + 「还有 2 项」，卡片仍是 5 行
assert.equal(items(card(CARD_ROWS)), CARD_ROWS);
html = card(CARD_ROWS + 1);
assert.equal(items(html), CARD_ROWS);
assert.match(html, />Task 4</);
assert.doesNotMatch(html, />Task 5</);
assert.match(html, />2 more</);
// 批量上限 20 个任务：前 4 个 + 「还有 16 项」，两个按钮照旧
html = card(20);
assert.match(html, />16 more</);
assert.equal(buttons(html).length, 2);
assert.deepEqual(cardRows(rows(20)).shown.map((r) => r.field), ["Task 1", "Task 2", "Task 3", "Task 4"]);
assert.deepEqual(cardRows([]), { shown: [], more: 0 });
// 没有改动可列（内置工具的批准）：只有两个按钮
html = renderToStaticMarkup(createElement(ApprovalBar, { approval: { id: "sevt_2" }, respondToApproval: () => {} }));
assert.equal(items(html), 0);
assert.equal(buttons(html).length, 2);
// 点过以后：结论用 globals.css 的成功 / 危险色
const verdict = (approved: boolean) => renderToStaticMarkup(createElement(ApprovalBar, { approval: { id: "x", approved } }));
assert.match(verdict(true), /text-success/);
assert.match(verdict(false), /text-danger/);
g.done();
""")
        run_ts(APPROVAL_CARD + r"""
assert.match(card(6), />还有 2 项</);
g.done();
""", lang="zh")
        run_ts(APPROVAL_CARD + r"""
assert.match(card(6), />อีก 2 รายการ</);
g.done();
""", lang="th")
        source = read("components/tool-uis/approval-bar.tsx")
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|emerald|\bred-\d|\d+px", source))
# ── 结果面板的 Word 预览（2026-10-13 教程第 3 条：「สรุปภาษีแก้วกานดา_ก.ย._2569.docx」只显示下载按钮）────────────────
# 服务端把 .docx 读成纯数据（pearnly_core.assistant.docx_preview，离线检查在 tests/test_assistant.py），页面用按需载入的
# document-view 按文字渲染。下面用 react-dom/server 渲染真实组件，看显示出来的 HTML。

@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class DocumentPreviewBehaviourTests(unittest.TestCase):
    HEAD = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import DocumentView, { safeImage } from "@/components/document-view";
import { ErrorBoundary } from "@/components/error-boundary";
import { DocumentPreview, FileTypeIcon, kindOf, loadPreview } from "@/components/results-panel";

const g = globalThis as any;
const html = (type: any, props: any = null) => renderToStaticMarkup(createElement(type, props));
const tags = (markup: string) => markup.match(/<[^>]+>/g) ?? [];
const file = { id: "file_1", filename: "สรุปภาษีแก้วกานดา_ก.ย._2569.docx", size_bytes: 9000, kind: "output",
               mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", downloadable: true,
               created_at: "2026-10-13T11:00:00Z" } as any;
const DOWNLOAD = /href="\/api\/method\/pearnly_core\.assistant\.api\.download\?session_id=s1&amp;file_id=file_1&amp;inline=0"/;
"""

    def run_page(self, checks):
        run_ts(self.HEAD + checks + "\ng.done();\n")

    def test_docx_is_previewed_and_other_types_keep_their_way(self):
        # 类型判断：.docx 走 Word 预览（对话里的文件卡片也换成文档图标）；老格式 .doc 仍是下载
        self.run_page(r"""
assert.equal(kindOf(file.filename), "doc");
assert.equal(kindOf("REPORT.DOCX"), "doc");
for (const [name, kind] of [["old.doc", "none"], ["GL-05.xlsx", "sheet"], ["r.pdf", "pdf"], ["page.html", "html"],
                            ["note.md", "text"], ["a.docx.zip", "none"]]) assert.equal(kindOf(name), kind, name);
assert.match(html(FileTypeIcon, { filename: file.filename }), /lucide-file-text/);
assert.doesNotMatch(html(FileTypeIcon, { filename: "old.doc" }), /lucide-file-text/);
""")

    def test_preview_failures_fall_back_to_the_download(self):
        self.run_page(r"""
// 服务端拒收 / 出错、网络断：当作没有预览
g.respond = async () => ({ ok: false, status: 500, json: async () => ({ exception: "frappe.exceptions.ValidationError: x" }) });
assert.deepEqual(await loadPreview("s1", "file_1"), { type: "none" });
g.respond = async () => { throw new TypeError("Failed to fetch"); };
assert.deepEqual(await loadPreview("s1", "file_1"), { type: "none" });
const blocks = [{ type: "paragraph", runs: [{ text: "สรุป" }] }];
g.respond = async () => ({ ok: true, status: 200, json: async () => ({ message: { type: "document", blocks } }) });
assert.deepEqual(await loadPreview("s1", "file_1"), { type: "document", blocks });
assert.deepEqual(g.calls.at(-1), { method: "preview", body: null });
// 读不出（none）、没有正文：显示下载按钮（附件下载地址）
for (const preview of [{ type: "none" }, { type: "document", blocks: [] }, { type: "sheets", sheets: [] }]) {
  const markup = html(DocumentPreview, { sessionId: "s1", file, preview });
  assert.match(markup, DOWNLOAD, JSON.stringify(preview));
  assert.match(markup, />Download</);
}
// 还在取：转圈，不给下载
assert.doesNotMatch(html(DocumentPreview, { sessionId: "s1", file, preview: null }), /download/);
// 有正文：渲染那一块包在错误边界里；那一块载不进来（站点刚更新、旧页面要的文件已换名）或渲染出错 → 换成下载
const tree: any = DocumentPreview({ sessionId: "s1", file, preview: { type: "document", blocks } as any });
assert.equal(tree.type, ErrorBoundary);
const boundary = new ErrorBoundary(tree.props);
assert.equal(boundary.render(), tree.props.children);
boundary.state = ErrorBoundary.getDerivedStateFromError();
assert.match(renderToStaticMarkup(boundary.render() as any), DOWNLOAD);
""")

    def test_document_renders_as_text_with_tables_that_scroll_on_phones(self):
        self.run_page(r"""
const png = "data:image/png;base64,iVBORw0KGgo=";
const cell = (text: string, extra: any = {}) => ({ blocks: [{ type: "paragraph", runs: [{ text }], ...extra }] });
const markup = html(DocumentView, { blocks: [
  { type: "paragraph", style: "title", align: "center", runs: [{ text: "สรุปภาษี ก.ย. 2569" }] },
  { type: "paragraph", style: "h2", runs: [{ text: "ภาษีขาย" }] },
  { type: "paragraph", runs: [{ text: "ยอด " }, { text: "19,600.36", bold: true }, { text: " บาท", italic: true, underline: true },
                              { text: "2", sup: true }, { text: "ลิงก์", link: true }] },
  { type: "paragraph", list: { level: 1, marker: "1.a)" }, runs: [{ text: "ข้อย่อย" }] },
  { type: "paragraph", runs: [] },
  { type: "table", rows: [
    { header: true, cells: [cell("รายการ"), cell("จำนวน")] },
    { cells: [cell("รวม", { align: "right" }), { colspan: 2, rowspan: 2, ...cell("1,234.00") }] },
    { cells: [{ blocks: [{ type: "table", rows: [{ cells: [cell("inner")] }] }] }] },
  ] },
  { type: "paragraph", runs: [{ image: png, width: 100, height: 50 }] },
] });
assert.match(markup, /<h1 class="[^"]*text-2xl[^"]*text-center">สรุปภาษี ก\.ย\. 2569<\/h1>/);
assert.match(markup, /<h2 class="[^"]*">ภาษีขาย<\/h2>/);
assert.match(markup, /<p class="mb-2">ยอด <span class="font-bold">19,600\.36<\/span><span class="italic underline underline-offset-2"> บาท<\/span><sup>2<\/sup><span class="underline underline-offset-2">ลิงก์<\/span><\/p>/);
assert.match(markup, /<p class="mb-2 flex gap-2" style="padding-inline-start:1\.5em"><span class="shrink-0 tabular-nums">1\.a\)<\/span><span class="min-w-0 flex-1">ข้อย่อย<\/span><\/p>/);
assert.match(markup, /<p class="mb-2"> <\/p>/);  // 空段落占一行
assert.match(markup, /<td class="[^"]*bg-subtle[^"]*"><p class="mb-0">รายการ<\/p><\/td>/);
assert.match(markup, /<td colSpan="2" rowSpan="2" class="[^"]*"><p class="mb-0">1,234\.00<\/p><\/td>/);
assert.match(markup, /<p class="mb-0 text-right">รวม<\/p>/);
assert.match(markup, /<td class="[^"]*"><div class="my-3 max-w-full overflow-x-auto"><table/);  // 表中表
assert.match(markup, /<img src="data:image\/png;base64,iVBORw0KGgo=" alt="" draggable="false" class="[^"]*max-w-full[^"]*" style="width:100px;aspect-ratio:100 \/ 50"\/>/);
// 手机：整篇竖着滚、不横向撑开；宽表格在自己的框里横着滚；长串文字折行；图片不超过面板宽
assert.match(markup, /^<div class="h-full overflow-x-hidden overflow-y-auto"><article class="[^"]*break-words[^"]*">/);
assert.equal((markup.match(/<div class="my-3 max-w-full overflow-x-auto"><table/g) ?? []).length, 2);
// 颜色只用设计变量（globals.css）：不写死色值
assert.doesNotMatch(markup, /#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(/);
""")

    def test_scripts_links_and_outside_images_never_render(self):
        # 清洗：服务端已只给纯数据；万一数据里混进 HTML、地址或怪样式，这里也只当文字、不出标签与地址
        self.run_page(r"""
const markup = html(DocumentView, { blocks: [
  { type: "paragraph", runs: [{ text: "<script>alert(1)</script>" }, { text: "<img src=x onerror=alert(1)>", link: true }] },
  { type: "paragraph", runs: [
    { image: "javascript:alert(1)" }, { image: "https://evil.example/pixel.png" }, { image: "//evil.example/p.png" },
    { image: "data:image/svg+xml;base64,PHN2ZyBvbmxvYWQ9YWxlcnQoMSk+" }, { image: "data:text/html;base64,PHNjcmlwdD4=" },
    { image: 'data:image/png;base64,AAAA" onerror="alert(1)' }, { image: 42 }, { text: 42 }] },
  { type: "paragraph", style: "script", align: '" onclick="alert(1)', runs: [{ text: "s" }] },
  { type: "paragraph", style: "__proto__", runs: [{ text: "p" }] },
  { type: "iframe", src: "https://evil.example" },
  { type: "table", rows: [{ cells: [{ colspan: '2" onclick="x', rowspan: -1, blocks: [{ type: "paragraph", runs: [{ text: "c" }] }] }] }] },
  { type: "paragraph", list: { level: "<b>", marker: "<b>m</b>" }, runs: [{ text: "x" }] },
] as any });
for (const tag of tags(markup)) {
  assert.match(tag, /^<\/?(div|article|p|span|sup|sub|h[1-6]|table|tbody|tr|td)\b/, tag);
  assert.doesNotMatch(tag, /\son\w+=|href=|src=|javascript:|evil\.example/, tag);
}
assert.match(markup, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
assert.match(markup, /&lt;img src=x onerror=alert\(1\)&gt;/);
assert.match(markup, /<p class="mb-2">s<\/p><p class="mb-2">p<\/p>/);  // 不认识的样式按正文、不当标签名
assert.match(markup, /<span class="shrink-0 tabular-nums">&lt;b&gt;m&lt;\/b&gt;<\/span>/);
assert.match(markup, /<td class="[^"]*"><p class="mb-0">c<\/p><\/td>/);  // 怪的合并数不带出来
assert.equal(safeImage("data:image/webp;base64,UklGRg=="), "data:image/webp;base64,UklGRg==");
assert.equal(safeImage("data:image/jpeg;base64,/9j/4AAQ"), "data:image/jpeg;base64,/9j/4AAQ");
for (const bad of ["data:image/png;base64,AA== x", "DATA:image/png;base64,AA==", "data:image/png,raw", "", null, {}])
  assert.equal(safeImage(bad), null, String(bad));
""")

    def test_the_renderer_is_loaded_on_demand(self):
        # 首屏包不带 Word 预览：results-panel 只按需 import()，别处也不静态导入它；没有新增第三方依赖
        panel = read("components/results-panel.tsx")
        self.assertIn('const DocumentView = lazy(() => import("@/components/document-view"));', panel)
        for path in SRC.rglob("*.ts*"):
            self.assertNotRegex(path.read_text(encoding="utf-8"), r'(?m)^import[^;]*from "@/components/document-view"',
                                str(path.relative_to(SRC)))
        view = read("components/document-view.tsx")
        self.assertEqual(re.findall(r'from "([^"@][^"]*)"', view), ["react"])
        self.assertNotIn("dangerouslySetInnerHTML", view)
        self.assertNotIn("href", view)


# ── 运行状态以服务端为准（2026-10-13 iPhone）──────────────────────────────────────────────────────────
# 主屏幕图标打开的 CC，这一轮在电脑上 15:24 已结束；手机在后台、锁屏收到推送，15:41 回来仍显示在回复（停止按钮、发不了），
# 侧栏重进同一个对话也一样。下面用会话控制器、尾巴、侧栏状态的真代码，接一个假的站点接口（事件记录 + 引擎状态 + 执行记录）。

MA_SETUP = r"""
const g = globalThis as any;
// 站点接口：按方法名与查询参数记下每次调用，回应由 respond(method, args, body) 决定
g.fetch = async (url: string, init: any = {}) => {
  const u = new URL(String(url), "https://site.test");
  const method = u.pathname.split("pearnly_core.assistant.api.")[1] ?? u.pathname;
  const args = Object.fromEntries(u.searchParams);
  const body = init.body ? JSON.parse(init.body) : null;
  g.calls.push({ method, args, body });
  return g.respond(method, args, body);
};
// window（= globalThis）与 document 的事件：测试里手动触发
const events = (target: any) => {
  const map: Record<string, Set<(event: any) => void>> = {};
  target.addEventListener = (type: string, fn: any) => void (map[type] ??= new Set()).add(fn);
  target.removeEventListener = (type: string, fn: any) => void map[type]?.delete(fn);
  target.fire = (type: string, event: any = {}) => [...(map[type] ?? [])].forEach((fn) => fn(event));
};
events(g);
events(g.document);
"""

SOCKET_STUB = r"""
export function io() {
  const handlers: Record<string, Array<(data?: any) => void>> = {};
  const socket: any = { connected: true, on(name: string, fn: any) { (handlers[name] ??= []).push(fn); return socket; }, off() { return socket; } };
  (globalThis as any).socket = {
    drop() { socket.connected = false; (handlers.disconnect ?? []).forEach((fn) => fn()); },
    back() { socket.connected = true; (handlers.connect ?? []).forEach((fn) => fn()); },
  };
  return socket;
}
"""

# 假站点：事件记录（log）、引擎状态（engine）、本站执行记录（runs）。要状态时（status=1）在读完事件之后读，同服务端。
FAKE_SITE = r"""
const ok = (message: any = null) => ({ ok: true, status: 200, json: async () => ({ message }) });
// 引擎给的时间带到微秒
const at = (s: number) => new Date(Date.UTC(2026, 9, 13, 8, 0, 0) + s * 1000).toISOString().replace(".000Z", ".123456Z");
const ev = (id: string, type: string, s: number, extra: any = {}) => ({ id, type, processed_at: at(s), ...extra });
const text = (t: string) => [{ type: "text", text: t }];
const site = {
  log: [] as any[], engine: "idle", runs: {} as Record<string, string>,
  echo: true, toolReply: async (_id: string): Promise<any> => ({ status: "running" }),
  sidebar: {} as Record<string, any>,
};
function runOf() {
  const last = [...site.log].reverse().find((e) => String(e.type).startsWith("session.status_"));
  const parked = site.engine === "idle" && last?.stop_reason?.type === "requires_action" ? [...last.stop_reason.event_ids] : [];
  return {
    status: site.engine === "idle" ? (parked.length ? "requires_action" : "idle") : site.engine,
    parked, awaiting_approval: parked.filter((i: string) => site.runs[i] === "awaiting_approval"),
    executing: parked.filter((i: string) => site.runs[i] === "running"), as_of: site.log.at(-1)?.processed_at ?? null,
  };
}
g.respond = async (method: string, args: any, body: any) => {
  if (method === "events") {
    const i = args.after ? site.log.findIndex((e) => e.id === args.after) : -1;
    const page: any = { events: i >= 0 ? site.log.slice(i + 1) : [...site.log], full: i < 0 };
    if (args.status === "1") page.run = runOf();
    return ok(page);
  }
  if (method === "send") {
    if (site.echo) site.log.push(ev(`echo${site.log.length}`, "user.message", 100 + site.log.length, { content: text(body.text) }));
    return ok();
  }
  if (method === "run_tool") return ok(await site.toolReply(body.tool_use_id));
  if (method === "listing") {
    const ids = String(args.check ?? "").split(",").filter((id) => site.sidebar[id]);
    return ok({ sessions: [], nextCursor: null, runs: Object.fromEntries(ids.map((id) => [id, site.sidebar[id]])) });
  }
  return ok();
};
const eventsCalls = (sid: string) => g.calls.filter((c: any) => c.method === "events" && c.args.session_id === sid);
const statusReads = (sid: string) => eventsCalls(sid).filter((c: any) => c.args.status === "1").length;
const toolCalls = () => g.calls.filter((c: any) => c.method === "run_tool");
const toolPart = (snapshot: any, id: string) => snapshot.messages.flatMap((m: any) => m.content).find((p: any) => p.toolCallId === id);
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class RunStatusBehaviourTests(unittest.TestCase):
    STUBS = {"cc-ma-setup": MA_SETUP, "socket.io-client": SOCKET_STUB}

    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\nimport assert from "node:assert/strict";\n'
               + body.split("\n---\n", 1)[0] + "\nconst g = globalThis as any;\n" + FAKE_SITE
               + body.split("\n---\n", 1)[1] + "\ng.done();\n", stubs=self.STUBS)

    def test_coming_back_after_the_turn_ended_elsewhere_is_not_running(self):
        # 原因（修前）：回到前台补拉的一页里有「停下来等工具 → 工具结果 → 接着跑 → 结束」，页面在并到「停下来」时就提醒
        # 服务端执行（那一页后面就有答复）；答复晚到时 markResuming 把已结束的这一轮又标成在跑，结束事件已去重，重放、
        # 重进都改不回来。现在：回到前台带回服务端状态；晚到的答复只在服务端还停在那一步时才算接着跑。
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { isBusy } from "@/lib/managed-agents/reducer";
---
const S = "sesn_iphone1";
site.log.push(ev("u1", "user.message", 1, { content: text("summarise the bank statement") }),
  ev("r1", "session.status_running", 2), ev("tool1", "agent.custom_tool_use", 3, { name: "pearnly_x", input: {} }),
  ev("p1", "session.status_idle", 4, { stop_reason: { type: "requires_action", event_ids: ["tool1"] } }));
let release: (reply: any) => void = () => {};
site.toolReply = () => new Promise((resolve) => { release = resolve; });
const c = getSessionController(S);
c.attach();
await g.sleep(30);
assert.equal(statusReads(S), 1);  // 打开（整段重放）带一份状态，只读一次
assert.equal(isBusy(c.getSnapshot()), true);
assert.deepEqual(toolCalls().map((x: any) => x.body.tool_use_id), ["tool1"]);  // 页面兜底提醒一次，答复迟迟不回

// 锁屏、在后台：服务端推送线程把工具做完，这一轮在电脑上结束；手机一条推送也没收到
g.document.visibilityState = "hidden";
site.log.push(ev("res1", "user.custom_tool_result", 5, { custom_tool_use_id: "tool1", content: text("12 rows") }),
  ev("r2", "session.status_running", 6), ev("m1", "agent.message", 7, { content: text("Done: 12 rows") }),
  ev("i1", "session.status_idle", 8, { stop_reason: { type: "end_turn" } }));
// 回到前台：马上补拉一次，顺带要状态
g.document.visibilityState = "visible";
g.document.fire("visibilitychange");
await g.sleep(30);
assert.equal(statusReads(S), 2);
assert.equal(eventsCalls(S).at(-1).args.after, "p1");
assert.equal(c.getSnapshot().isRunning, false);
// 晚到的答复：服务端已经不停在这一步，不再标回在回复；卡片里的真结果也不被空结果盖掉
release({ status: "answered" });
await g.sleep(10);
assert.equal(c.getSnapshot().isRunning, false);
assert.equal(isBusy(c.getSnapshot()), false);
assert.equal(toolPart(c.getSnapshot(), "tool1").result.text, "12 rows");
assert.equal(toolCalls().length, 1);
""")

    def test_replay_reentry_reconnect_and_stop_take_the_servers_state(self):
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { isBusy } from "@/lib/managed-agents/reducer";
import { activityOf } from "@/lib/managed-agents/activity";
---
const S = "sesn_stuck1";
site.log.push(ev("u1", "user.message", 1, { content: text("hi") }), ev("r1", "session.status_running", 2),
  ev("m1", "agent.message", 3, { content: text("hello") }), ev("i1", "session.status_idle", 4, { stop_reason: { type: "end_turn" } }));
const c = getSessionController(S);
c.attach();
await g.sleep(30);
assert.equal(c.getSnapshot().isRunning, false);

// 发出了，可这一轮的事件一条也没到（漏了），引擎早已空闲：本页乐观标的「在回复」只有服务端状态能清
site.echo = false;
await c.sendMessage("are you there", []);
assert.equal(c.getSnapshot().isRunning, true);
const before = statusReads(S);
// 1) 整段重放：没有新事件可并，按服务端状态清掉
await c.replay();
assert.equal(statusReads(S), before + 1);
assert.equal(c.getSnapshot().isRunning, false);

// 2) 重进对话（侧栏切走再切回）：重放带回的状态；服务端在跑就保持在跑
await c.sendMessage("again", []);
site.engine = "running";
site.log.push(ev("r2", "session.status_running", 6));
c.detach();
c.attach();
await g.sleep(30);
assert.equal(statusReads(S), before + 2);
assert.equal(c.getSnapshot().isRunning, true);
assert.equal(activityOf(S), "running");  // 侧栏同一个对话跟着改
// 回到前台：服务端仍在跑 → 仍在跑
g.document.fire("visibilitychange");
await g.sleep(30);
assert.equal(statusReads(S), before + 3);
assert.equal(c.getSnapshot().isRunning, true);

// 3) 实时通道断开又连上：这一轮在断线期间结束，「结束」既没推到、事件记录里也还读不到，重连时带回状态
site.engine = "idle";
g.socket.drop();
await g.sleep(30);
assert.equal(c.getSnapshot().isRunning, true);  // 断开时那次补拉不带状态，也没有新事件：不自己猜
g.socket.back();
await g.sleep(30);
assert.equal(statusReads(S), before + 4);
assert.equal(c.getSnapshot().isRunning, false);
assert.equal(activityOf(S), "unread");  // 不是正看着的对话：跑完留点

// 4) 卡在「在回复」时点停止：打断之后核对一次，服务端空闲就恢复发送
site.echo = false;
await c.sendMessage("one more", []);
assert.equal(c.getSnapshot().isRunning, true);
await c.interrupt();
await g.sleep(30);
assert.deepEqual(g.calls.filter((x: any) => x.method === "interrupt").map((x: any) => x.body.session_id), [S]);
assert.equal(statusReads(S), before + 5);
assert.equal(isBusy(c.getSnapshot()), false);

// 5) 自己刚发出时，发出前就要的状态（还是「空闲」）不盖掉这次发送
let hold: () => void = () => {};
const respond = g.respond;
g.respond = async (method: string, args: any, body: any) => {
  if (method === "events" && args.status === "1") await new Promise<void>((resolve) => { hold = resolve; });
  return respond(method, args, body);
};
const pending = c.replay();
await g.sleep(5);
site.echo = true;
await c.sendMessage("latest", []);
hold();
await pending;
assert.equal(c.getSnapshot().isRunning, true);
g.respond = respond;
""")

    def test_requires_action_shows_the_approval_and_an_executing_tool_stays_running(self):
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { isBusy } from "@/lib/managed-agents/reducer";
---
// 停在两步上：一个改共用设置的 Pearnly 工具（本站拦下等「允许」），一个自带工具（等引擎的确认）
const S = "sesn_gate1";
site.log.push(ev("u1", "user.message", 1, { content: text("turn the task on") }), ev("r1", "session.status_running", 2),
  ev("tz", "agent.custom_tool_use", 3, { name: "pearnly_task_switch", input: { task: "T1", enabled: true } }),
  ev("tb", "agent.tool_use", 4, { name: "bash", input: { command: "ls" } }),
  ev("p1", "session.status_idle", 5, { stop_reason: { type: "requires_action", event_ids: ["tz", "tb"] } }));
site.runs = { tz: "awaiting_approval" };
site.toolReply = async () => ({ status: "awaiting_approval", ask: "Turn it on?", preview: [{ field: "Enabled", before: "No", after: "Yes" }] });
const c = getSessionController(S);
c.attach();
await g.sleep(30);
let snap: any = c.getSnapshot();
assert.equal(snap.isRunning, false);
assert.deepEqual([...snap.pendingApprovals].sort(), ["tb", "tz"]);
assert.equal(toolPart(snap, "tz").approval.ask, "Turn it on?");  // 卡片上的问句与改动照旧向服务端要
assert.equal(toolPart(snap, "tb").approval.id, "tb");
assert.equal(isBusy(snap), true);
// 回到前台再核对：门还在，问句不丢
g.document.fire("visibilitychange");
await g.sleep(30);
snap = c.getSnapshot();
assert.equal(statusReads(S), 2);
assert.deepEqual([...snap.pendingApprovals].sort(), ["tb", "tz"]);
assert.equal(toolPart(snap, "tz").approval.ask, "Turn it on?");

// 在别处点了允许：服务端正在执行这一步，自带工具那步也答了（事件还没到）→ 在忙，不再显示按钮、不再提醒执行
site.runs = { tz: "running" };
site.log.push(ev("cb", "user.tool_confirmation", 6, { tool_use_id: "tb", result: "allow" }));
const asked = toolCalls().length;
g.fire("online");
await g.sleep(30);
snap = c.getSnapshot();
assert.equal(statusReads(S), 3);
assert.deepEqual(snap.pendingApprovals, []);
assert.equal(snap.isRunning, true);
assert.equal(toolPart(snap, "tz").approval, undefined);
assert.equal(toolCalls().length, asked);
// 从后台缓存恢复（pageshow persisted）也核对一次；没恢复的 pageshow 不算
g.fire("pageshow", { persisted: false });
await g.sleep(20);
assert.equal(statusReads(S), 3);
site.engine = "running";
g.fire("pageshow", { persisted: true });
await g.sleep(30);
assert.equal(statusReads(S), 4);
assert.equal(c.getSnapshot().isRunning, true);
""")

    def test_status_events_follow_server_time_and_older_snapshots_lose(self):
        self.run_page(r"""
import { applyEvent, applyRunStatus, isBusy, markResuming, project } from "@/lib/managed-agents/reducer";
---
const idle = (id: string, s: number, stop: any = { type: "end_turn" }) => ev(id, "session.status_idle", s, { stop_reason: stop });
// 推送先到了新的「结束」，补拉晚到的旧「在跑」（断线期间漏推的）：只记已见，不再标回在回复
let s = project([ev("u1", "user.message", 1, { content: text("hi") }), ev("r1", "session.status_running", 2), idle("i1", 8)] as any);
s = applyEvent(s, ev("r0", "session.status_running", 6) as any);
assert.equal(s.isRunning, false);
assert.equal(s.seen.has("r0"), true);
// 本页合成的不按时间比：发出时照样标在回复
s = applyEvent(s, { type: "session.status_running", id: "optimistic-run-1", processed_at: "2020-01-01T00:00:00Z" } as any);
assert.equal(s.isRunning, true);
// 比已采用的状态事件旧的服务端状态不用；读的事件覆盖到它（或更新）才用
const run = (status: string, asOf: number, extra: any = {}) =>
  ({ status, parked: [], awaiting_approval: [], executing: [], as_of: at(asOf), ...extra }) as any;
assert.equal(applyRunStatus(s, run("running", 7)), s);
const fresh = applyRunStatus(s, run("idle", 8));
assert.equal(fresh.isRunning, false);
assert.equal(fresh.statusAt >= Date.parse(at(8).replace(".123456", ".123")), true);
// 「接着跑」只在服务端还停在那一步时才标
assert.equal(markResuming(fresh, "tool9"), fresh);
// 等确认的自带工具：显示批准按钮；服务端停在一次还没收到的调用上：算在忙（不自己补门）
let g2 = project([ev("u2", "user.message", 10, { content: text("x") }), ev("tb", "agent.tool_use", 11, { name: "bash", input: {} })] as any);
g2 = applyRunStatus(g2, run("requires_action", 11, { parked: ["tb", "tmissing"] }));
assert.deepEqual(g2.pendingApprovals, ["tb"]);
assert.equal(g2.isRunning, true);
assert.equal(isBusy(g2), true);
// 终止不论先后都算数
const dead = applyEvent(fresh, ev("t0", "session.status_terminated", 1) as any);
assert.equal(dead.isDead, true);
""")

    def test_sidebar_spinners_are_checked_with_the_server_at_the_same_moments(self):
        self.run_page(r"""
import { activityOf, noteEvent, setActiveSession } from "@/lib/managed-agents/activity";
import { sessionListAdapter } from "@/lib/managed-agents/session-list-adapter";
import { watchList } from "@/lib/managed-agents/list-sync";
---
const listings = () => g.calls.filter((c: any) => c.method === "listing");
const empty = { mainThreadId: "", threadIds: [], threadItems: {}, isLoading: false, isLoadingMore: false };
watchList(() => ({ getState: () => empty, reload: async () => {}, getItemById: () => ({}) }) as any);
g.document.fire("visibilitychange");
await g.sleep(20);
// 侧栏没有在转圈的：回到前台也核对一次列表（教程第 7 条：别的设备上新建、删除的对话要并进来），只是不读引擎状态
assert.deepEqual(listings().map((c: any) => c.args.check), [undefined]);
g.calls.length = 0;
// 另一个对话在跑（推送说的），之后「结束」的推送漏了
noteEvent("sesn_side1", ev("r1", "session.status_running", 1) as any);
noteEvent("sesn_open1", ev("r2", "session.status_running", 1) as any);
setActiveSession("sesn_open1");  // 开着的那个由它自己的会话控制器核对
assert.equal(activityOf("sesn_side1"), "running");
site.sidebar.sesn_side1 = { status: "idle", parked: [], awaiting_approval: [], executing: [], as_of: at(9) };
g.document.fire("visibilitychange");
await g.sleep(20);
assert.deepEqual(listings().map((c: any) => c.args.check), ["sesn_side1"]);
assert.equal(activityOf("sesn_side1"), "unread");  // 跑完没看：留点，不再转圈
// 重连：只在断过之后才核对；列表第一页也顺带核对
noteEvent("sesn_side2", ev("r3", "session.status_running", 2) as any);
site.sidebar.sesn_side2 = { status: "requires_action", parked: ["x"], awaiting_approval: ["x"], executing: [], as_of: at(9) };
g.socket.back();
await g.sleep(20);
assert.equal(listings().length, 1);
g.socket.drop();
g.socket.back();
await g.sleep(20);
assert.deepEqual(listings().at(-1).args.check, "sesn_side2");
assert.equal(activityOf("sesn_side2"), "running");  // 停着等处理：照旧算在跑
site.sidebar.sesn_side2 = { status: "idle", parked: [], awaiting_approval: [], executing: [], as_of: at(9) };
await sessionListAdapter.list({} as any);
assert.equal(listings().at(-1).args.check, "sesn_side2");
assert.equal(activityOf("sesn_side2"), "unread");
""")


# ── 侧栏多设备同步与标题（教程第 7、10、11 条，2026-10-13）──────────────────────────────────────────────
# 同一账号两台设备（iPhone 主屏幕 App 与一直开着的 CC 桌面版）：一台新建、改名、加星标、删除对话，服务端按用户推一条
# pearnly.session，另一台的侧栏马上并入；回到前台核对一次第一页。下面用组件库真的对话列表（RemoteThreadListThreadListRuntimeCore）
# 接会话列表接口（session-list-adapter）与同步（list-sync），站点接口是假的（侧栏的行 + 记下每次调用），实时通道是能推事件的桩。

SYNC_SOCKET = r"""
export function io() {
  const handlers: Record<string, Array<(data?: any) => void>> = {};
  const socket: any = { connected: true, on(name: string, fn: any) { (handlers[name] ??= []).push(fn); return socket; }, off() { return socket; } };
  (globalThis as any).socket = {
    emit(name: string, data: any) { (handlers[name] ?? []).forEach((fn) => fn(data)); },
    drop() { socket.connected = false; (handlers.disconnect ?? []).forEach((fn) => fn()); },
    back() { socket.connected = true; (handlers.connect ?? []).forEach((fn) => fn()); },
  };
  return socket;
}
"""

SYNC_SITE = r"""
const ok = (message: any = null) => ({ ok: true, status: 200, json: async () => ({ message }) });
const at = (minutesAgo: number) => new Date(Date.UTC(2026, 9, 13, 13, 0) - minutesAgo * 60000).toISOString();
const row = (id: string, title: string, minutesAgo: number, extra: any = {}) =>
  ({ id, title, starred: false, created_at: at(minutesAgo), updated_at: at(minutesAgo), archived_at: null, unread: false, ...extra });
const site = { sessions: [] as any[], nextCursor: null as string | null, created: "sesn_new001" };
g.respond = async (method: string) => {
  if (method === "listing") return ok({ sessions: site.sessions, nextCursor: site.nextCursor, runs: {} });
  if (method === "create") return ok(row(site.created, "", 0));
  return ok();
};
const listings = () => g.calls.filter((c: any) => c.method === "listing");
const writes = () => g.calls.filter((c: any) => ["rename", "star", "archive", "delete"].includes(c.method));
// 服务端推来一条侧栏同步（信封里 session_id 为空）
const push = (change: string, session: string, extra: any = {}) =>
  g.socket.emit("pearnly_assistant_event", { session_id: "", event: { type: "pearnly.session", change, session, ...extra } });

function sidebar() {
  const core: any = new RemoteThreadListThreadListRuntimeCore(
    { adapter: sessionListAdapter, runtimeHook: (() => null) as any } as any,
    { getModelContext: () => ({}), subscribe: () => () => {} } as any,
  );
  // 组件库的对话运行时要等页面挂上组件才起来；这里没有页面，直接当起好了
  core._hookManager.startThreadRuntime = async () => ({});
  return { core, threads: new ThreadListRuntimeImpl(core) as any };
}
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class SidebarSyncBehaviourTests(unittest.TestCase):
    STUBS = {"cc-ma-setup": MA_SETUP, "socket.io-client": SYNC_SOCKET}
    IMPORTS = r"""
import { RemoteThreadListThreadListRuntimeCore } from "@assistant-ui/core/react";
import { ThreadListRuntimeImpl } from "@assistant-ui/core/internal";
import { sessionListAdapter } from "@/lib/managed-agents/session-list-adapter";
import { reconcile, watchList } from "@/lib/managed-agents/list-sync";
"""

    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\nimport assert from "node:assert/strict";\n' + self.IMPORTS
               + "\nconst g = globalThis as any;\n" + SYNC_SITE + body + "\ng.done();\n", stubs=self.STUBS)

    def test_changes_on_another_device_merge_into_the_sidebar_at_once(self):
        # 2026-10-13 教程：iPhone 新建一段对话，一直开着的笔记本 CC 桌面版侧栏迟迟不出现，要刷新才看到
        self.run_page(r"""
site.sessions = [row("sesn_aaa001", "Old title", 1), row("sesn_bbb001", "Second", 2), row("sesn_ccc001", "Third", 3)];
const { core, threads } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
assert.deepEqual([...core.threadIds], ["sesn_aaa001", "sesn_bbb001", "sesn_ccc001"]);
watchList(() => threads);
const title = (id: string) => core.getItemById(id)?.title;

// 别处改了名（或服务端起好了主题标题）、加了星标、删了：就地并入；服务端已经改好，这边不再发请求
push("titled", "sesn_aaa001", { title: "Supplier invoice summary" });
push("starred", "sesn_bbb001", { starred: true });
push("deleted", "sesn_ccc001");
await g.sleep(20);
assert.equal(title("sesn_aaa001"), "Supplier invoice summary");
assert.equal(core.getItemById("sesn_bbb001").custom.starred, true);
assert.deepEqual([...core.threadIds], ["sesn_aaa001", "sesn_bbb001"]);
assert.equal(core.getItemById("sesn_ccc001"), undefined);
assert.deepEqual(writes(), []);

// 别处新建：几条合成一次重拉，新对话排进侧栏；重拉时旧列表照旧在（不闪骨架由界面管，见下一条检查）
site.sessions = [row("sesn_new101", "เมื่อคืนเป็นยังไงบ้าง", 0), row("sesn_aaa001", "Supplier invoice summary", 1),
  row("sesn_bbb001", "Second", 2, { starred: true })];
push("created", "sesn_new101");
push("titled", "sesn_new101", { title: "เมื่อคืนเป็นยังไงบ้าง" });
await g.sleep(1600);
assert.equal(listings().length, 2);  // 首次加载 + 合成的一次
assert.deepEqual([...core.threadIds], ["sesn_new101", "sesn_aaa001", "sesn_bbb001"]);
// 第一轮结束后服务端起好的主题标题：就地换上
push("titled", "sesn_new101", { title: "ผลงานเมื่อคืน" });
await g.sleep(20);
assert.equal(title("sesn_new101"), "ผลงานเมื่อคืน");
assert.deepEqual(writes(), []);

// 本页用户自己改名照常发请求（并入时的记号用完就收回）；推回来的回声不再改、不再发
await core.rename("sesn_aaa001", "Mine");
push("titled", "sesn_aaa001", { title: "Mine" });
await g.sleep(20);
assert.deepEqual(writes().map((c: any) => [c.method, c.body]), [["rename", { session_id: "sesn_aaa001", title: "Mine" }]]);
await core.delete("sesn_bbb001");
assert.deepEqual(writes().at(-1).body, { session_id: "sesn_bbb001" });
""")

    def test_the_open_conversation_follows_its_titles_and_leaves_when_deleted_elsewhere(self):
        self.run_page(r"""
site.sessions = [row("sesn_aaa001", "Old title", 5)];
const { core, threads } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
watchList(() => threads);
// 本页新建的对话：组件库先给它一个本页编号，建好会话才有对话编号
await core.switchToNewThread();
const local = core.mainThreadId;
site.created = "sesn_mine01";
await core.initialize(local);
assert.equal(core.getItemById(local).remoteId, "sesn_mine01");
// 重拉列表后同一段对话有两行数据（开着的那行、侧栏那行）：推来的标题两行都换
site.sessions = [row("sesn_mine01", "เมื่อคืนเป็นยังไงบ้าง", 0), ...site.sessions];
await core.reload();
const rows = () => Object.values(threads.getState().threadItems).filter((item: any) => item.remoteId === "sesn_mine01");
assert.equal(rows().length, 2);
push("titled", "sesn_mine01", { title: "ผลงานเมื่อคืน" });
await g.sleep(20);
assert.deepEqual(rows().map((item: any) => item.title), ["ผลงานเมื่อคืน", "ผลงานเมื่อคืน"]);
assert.equal(core.getItemById(local).title, "ผลงานเมื่อคืน");  // 顶栏标题跟着换
// 正开着的对话在别处删了：两行都去掉、切到新对话，不再向服务端发删除
push("deleted", "sesn_mine01");
await g.sleep(50);
assert.equal(rows().length, 0);
assert.notEqual(core.mainThreadId, local);
assert.deepEqual(writes(), []);
""")

    def test_coming_back_checks_the_first_page_once(self):
        self.run_page(r"""
site.sessions = [row("sesn_aaa001", "A", 1), row("sesn_bbb001", "B", 2), row("sesn_ccc001", "C", 3)];
const { core, threads } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
watchList(() => threads);
// 锁屏期间手机上改了名、删了一段：回到前台核对一次（侧栏没有在转圈的也核对，不读引擎状态）
site.sessions = [row("sesn_aaa001", "Renamed on phone", 0), row("sesn_ccc001", "C", 3)];
g.document.fire("visibilitychange");
await g.sleep(30);
assert.deepEqual(listings().slice(1).map((c: any) => c.args.check), [undefined]);
assert.equal(core.getItemById("sesn_aaa001").title, "Renamed on phone");
assert.deepEqual([...core.threadIds], ["sesn_aaa001", "sesn_ccc001"]);
assert.deepEqual(writes(), []);
// 实时通道断过又连上：同样核对；新出现的重拉一次
site.sessions = [row("sesn_new202", "From phone", 0), ...site.sessions];
g.socket.drop();
g.socket.back();
await g.sleep(1600);
assert.ok(core.threadIds.includes("sesn_new202"));

const quiet = { note: () => {}, stop: () => {} };
// 有下一页时：比这一页里最早的还旧的不在这一页里是正常的，不去掉；比它新却不在的才是在别处删了
site.sessions = [row("sesn_new202", "From phone", 0)];
reconcile(threads, { sessions: [row("sesn_new202", "From phone", 0), row("sesn_aaa001", "Renamed on phone", 0.5)],
  nextCursor: "2026-10-13 12:59:30|x" } as any, Date.now(), quiet);
await g.sleep(30);
assert.ok(core.threadIds.includes("sesn_ccc001"));
// 这一页发出之后本页自己改过的：不拿旧的一页盖掉
const asked = Date.now() - 1000;
await core.rename("sesn_aaa001", "Changed here");
reconcile(threads, { sessions: [row("sesn_aaa001", "Renamed on phone", 0)], nextCursor: null } as any, asked, quiet);
await g.sleep(30);
assert.equal(core.getItemById("sesn_aaa001").title, "Changed here");
assert.ok(!core.threadIds.includes("sesn_ccc001"));  // 没有下一页：不在的都是在别处删了
""")

    def test_a_new_chat_is_titled_at_once_without_waiting(self):
        # 以前侧栏先显示「新对话」，等服务端概括（最多 12 秒）；现在首条消息立刻就是标题，主题标题之后就地换上
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { dispatch, subscribe } from "@/lib/managed-agents/realtime";
import { activityOf } from "@/lib/managed-agents/activity";
import { sessionListAdapter } from "@/lib/managed-agents/session-list-adapter";
import { AssistantMessageStream } from "assistant-stream";
const g = globalThis as any;
// 同组件库取标题：第一个文字段
const title = async (id: string, messages: any[]) => {
  let out: string | undefined;
  const stream = await sessionListAdapter.generateTitle!(id, messages as any);
  for await (const result of AssistantMessageStream.fromAssistantStream(stream)) {
    out = result.parts.filter((p: any) => p.type === "text")[0]?.text;
  }
  return out;
};
const note = "\n\n[Attached file, mounted read-only in the sandbox:\n- r.jpg → /mnt/session/uploads/f/r.jpg]";
const said = (text: string) => [{ role: "user", content: [{ type: "text", text }] }];
const started = Date.now();
assert.equal(await title("sesn_t1", said("  เมื่อคืน   เป็นยังไงบ้าง" + note)), "เมื่อคืน เป็นยังไงบ้าง");
assert.ok(Date.now() - started < 500);  // 不等服务端
assert.equal(await title("sesn_t2", said(note.trim())), "r.jpg");  // 只发了附件
assert.equal(await title("sesn_t3", []), undefined);  // 什么都没有：不写死「New chat」，侧栏按页面语言显示
// 侧栏同步事件只给侧栏：不进对话页、不动行尾状态；推来的标题记下，之后取标题直接用
const seen: any[] = [];
subscribe("sesn_t1", (event) => seen.push(event), () => {});
dispatch({ session_id: "", event: { type: "pearnly.session", change: "titled", session: "sesn_t1", title: "ผลงานเมื่อคืน" } } as any);
assert.deepEqual(seen, []);
assert.equal(activityOf("sesn_t1"), undefined);
assert.equal(await title("sesn_t1", said("เมื่อคืนเป็นยังไงบ้าง")), "ผลงานเมื่อคืน");
g.done();
""", stubs={"socket.io-client": "export function io() { return { on() {}, off() {}, connected: true }; }"})

    def test_sidebar_rows_show_only_the_title(self):
        # 2026-10-13 用户：侧栏对话前面的气泡图标不要（同 Claude 侧栏只显示标题）
        thread_list = read("components/assistant-ui/thread-list.tsx")
        self.assertNotIn("MessageCircleIcon", thread_list)
        self.assertNotIn("cc-row-icon", thread_list + read("globals.css"))
        item = block(thread_list, "export const ThreadListItem: FC")
        self.assertIn("<ThreadListItemPrimitive.Title", item)
        self.assertIn("<ThreadActivity />", item)  # 行尾在跑、未读圆点照旧
        # 悬停与选中同一个底色（会话列表靠主色字区分选中，设计文档 3.5），行高按变体
        self.assertIn("hover:bg-row-hover", item)
        self.assertIn("data-active:bg-row-hover", item)
        self.assertIn("data-active:text-ink", item)
        self.assertIn('<MoreHorizontalIcon className="size-(--cc-icon-sm)" />', thread_list)  # 行尾「···」图标 16，同 .cc-row-btn
        self.assertIn("has-focus-visible:text-ink", item)  # 键盘焦点同选中一样主色字（设计文档 3.5），悬停不改字色
        self.assertNotIn("hover:text-", item)
        self.assertIn("h-(--cc-row-h)", item)
        # 只有第一次加载显示骨架：之后重拉列表（别处新建了对话）时旧列表留着，不闪
        self.assertIn("s.threads.isLoading && !s.threads.threadIds.length", thread_list)
        realtime = read("lib/managed-agents/realtime.ts")
        self.assertNotIn("pearnly_assistant_title", realtime)  # 标题也走侧栏同步，没有单独的事件
        self.assertIn("watchList(() => runtime.threads)", read("lib/managed-agents/runtime-provider.tsx"))


# ── 公司主档（ADR-020，2026-10-15）：侧栏按公司分组、公司页、主档页、月份与分店、移到、待处理、设置页拆文件夹块 ──────────
# 接口与事件按规格 §4.2 / §12.2 与补充文件（甲 分店、乙 待处理、丙 历史学到的选择）；服务端（A 包）不在本分支，
# 下面用假站点按契约回应。

COMPANY_SOURCES = ("lib/companies.ts", "lib/managed-agents/projects.ts", "lib/managed-agents/sidebar-groups.ts",
                   "components/companies.tsx", "components/company-page.tsx", "components/add-company.tsx",
                   "components/pending.tsx", "components/period-chip.tsx", "components/assistant-ui/projects.tsx",
                   "hooks/use-long-press.ts")


class CompanyMasterSourceTests(unittest.TestCase):
    def test_erp_and_dms_keep_todays_sidebar(self):
        # kind = null（没有声明 provider 的产品）：仍是按日子分组、旧的 listing，没有公司、待处理两行
        thread_list = read("components/assistant-ui/thread-list.tsx")
        self.assertIn("return kind ? <CompanyGroups searchQuery={searchQuery} /> : <DayGroups searchQuery={searchQuery} />;", thread_list)
        adapter = read("lib/managed-agents/session-list-adapter.ts")
        # 普通列表：没有公司分组，或侧栏「分组依据」不是公司（按月份 / 不分组，sidebar-view.ts）
        self.assertIn("if (!(await groupedListing())) {", adapter)
        self.assertIn("return !!(await projectsReady()) && byCompany();", adapter)
        app = read("assistant.tsx")
        self.assertIn("if (!kind) return null;", app.split("function CompanyNav(", 1)[1].split("\nfunction ", 1)[0])

    def test_pin_wording_and_the_three_custom_writes_keep_other_keys(self):
        app, thread_list = read("assistant.tsx"), read("components/assistant-ui/thread-list.tsx")
        both = app + thread_list
        self.assertEqual(both.count("updateCustom({ ...custom, starred: !starred })"), 3)
        self.assertNotIn("updateCustom({ starred", both)
        self.assertNotIn("StarIcon", both)
        self.assertIn("PinIcon", both)
        i18n = read("i18n.ts")
        for word in ('starred: "Pinned"', 'star: "Pin"', 'starred: "已置顶"', 'star: "置顶"', 'starred: "ปักหมุดไว้"'):
            self.assertIn(word, i18n)
        self.assertNotIn("星标", i18n)

    def test_new_chats_from_shortcuts_open_in_recent(self):
        # Ctrl+N、应用菜单、托盘、快捷提问（window.tomo.ask）、顶部「新建」、手机胶囊：都清掉待建的公司
        self.assertIn("setDraft({});\n        aui.threads().switchToNewThread();", read("lib/desktop.ts"))
        self.assertIn("setDraft({});\n            props.onClick?.(e);", read("components/assistant-ui/thread-list.tsx"))
        app = read("assistant.tsx")
        self.assertEqual(app.count("setDraft({}); aui.threads().switchToNewThread();"), 2)  # 应用菜单、手机胶囊
        self.assertIn("setDraft({});\n            aui.threads().switchToNewThread();\n            close();", app)  # 抽屉底部

    def test_only_the_desktop_window_picks_a_folder(self):
        desktop = read("lib/desktop.ts")
        self.assertIn('return (await api.call("pick_folder")) as unknown as PickResult;', desktop)
        self.assertIn('export const COMPANY_FOLDERS = "company_folders";', desktop)
        page = read("components/company-page.tsx")
        folders = page.split("export const FolderSection: FC", 1)[1].split("\n// ----", 1)[0]
        self.assertIn("{app && companyFolders && !hasMine && (", folders)
        self.assertIn("{app && !companyFolders && (", folders)  # 旧桌面版：请更新
        self.assertIn("await bindFolder(company, picked.binding);", folders)  # 桥给的 binding 原样交给服务端
        self.assertIn('bindFolder = (company: string, binding: unknown) => postMethod(API + "folder_bind", { company, binding: JSON.stringify(binding) });',
                      read("lib/companies.ts"))
        # 页面里没有别处调桥选文件夹、也不拼路径
        for name, text in ((p, read(p)) for p in COMPANY_SOURCES):
            if name != "lib/desktop.ts":
                self.assertNotIn('call("pick_folder"', text, name)

    def test_settings_drop_the_folder_block_but_keep_express_and_old_desktops(self):
        settings = read("components/settings.tsx")
        self.assertIn("const companyFolders = !!state.features?.includes(COMPANY_FOLDERS);", settings)
        self.assertIn("{companyFolders && legacy.length > 0 && (", settings)
        self.assertIn("{!companyFolders && main && main.folders !== null && (", settings)  # 旧桌面版保留旧块
        self.assertIn('onClick={() => void run("folder", "remove_folder", { id: root.member, path: root.path })}', settings)
        self.assertIn("<Row label={st.expressFolder}>", settings)  # Express 授权留在设置里
        computer = block(settings, "export function ComputerSection(")
        self.assertNotIn("st.folders", computer)
        self.assertNotIn("row.folders", computer)

    def test_company_styles_only_use_the_design_variables(self):
        colour = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|style=\{")
        for name in COMPANY_SOURCES:
            self.assertIsNone(colour.search(read(name)), name)
        css = read("globals.css")
        start = css.index("/* ── 公司主档（ADR-020")
        section = css[start:css.index("\n}\n", start)]
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", section))
        # 圆角、阴影只引用变量
        for value in re.findall(r"(?:border-radius|box-shadow):\s*([^;]+);", section):
            self.assertTrue(value.strip().startswith("var(--cc-"), value)
        drawer = css[css.index("/* 手机抽屉里的公司组头"):]
        drawer = drawer[:drawer.index("\n/*", 1) if "\n/*" in drawer[1:] else len(drawer)]
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", drawer))

    def test_the_composer_company_bar_does_not_touch_the_thread_file(self):
        # 公司条放在新对话的问候里（assistant.tsx 的 Welcome），不改 thread.tsx（工作状态行那一包的文件）
        self.assertIn("<ComposerCompany />", block(read("assistant.tsx"), "function Welcome()"))
        self.assertNotIn("ComposerCompany", read("components/assistant-ui/thread.tsx"))

    def test_hand_to_cc_and_drafts_prefill_without_sending(self):
        pending = read("components/pending.tsx")
        self.assertIn("handText(items), false);", pending)
        page = read("components/company-page.tsx")
        self.assertIn("co.draftText(d.kind, period ? monthLabel(period) : d.period), false);", page)
        # 主档页输入框是用户自己打的字：发出
        self.assertIn("start({ project: company, period: withPeriod ? period : \"\", scope: withScope ? scope : \"\" }, value, true);", page)

    def test_deep_links_for_companies_tasks_and_pending(self):
        nav = read("lib/nav.ts")
        self.assertIn('export type View = "chat" | "tasks" | "companies" | "company" | "pending";', nav)
        self.assertIn('url.searchParams.delete("company");', nav)
        self.assertIn("useCompanyNav.setState({ taskCompany: company });", nav)


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class CompanyHelpersBehaviourTests(unittest.TestCase):
    STUBS = {"socket.io-client": "export function io() { return { on() { return this; }, off() { return this; }, connected: true }; }"}

    def run_page(self, body, lang="en"):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + body + "\n(globalThis as any).done();\n",
               stubs=self.STUBS, lang=lang)

    def test_months_branches_and_amounts(self):
        self.run_page(r"""
import { branchesOf, branchShort, formatAmount, isPeriod, lastMonth, monthLabel, monthShort, parseCounts, recentMonths } from "@/lib/companies";
// 泰文按佛历；只有月份的短写今年不带年
assert.equal(monthLabel("2026-09"), "ก.ย. 2569");
assert.equal(monthShort("2026-09", new Date(2026, 9, 15)), "ก.ย.");
assert.equal(monthShort("2025-12", new Date(2026, 9, 15)), "ธ.ค. 2568");
assert.equal(monthLabel(""), "ไม่ระบุเดือน");
// 默认上个月；胶囊里本月往前 12 个月
assert.equal(lastMonth(new Date(2026, 0, 10)), "2025-12");
assert.deepEqual(recentMonths(new Date(2026, 9, 15)).slice(0, 2), ["2026-10", "2026-09"]);
assert.equal(recentMonths(new Date(2026, 9, 15)).length, 12);
assert.equal(recentMonths(new Date(2026, 9, 15)).at(-1), "2025-11");
assert.ok(isPeriod("2026-09") && !isPeriod("2026-13") && !isPeriod("2569-9"));
// 分店：总公司走翻译，其余「สาขา 00012」
assert.equal(branchShort("00000"), "สำนักงานใหญ่");
assert.equal(branchShort("00012"), "สาขา 00012");
assert.equal(branchShort(""), "");
// 金额：十进制字符串只插千分位，不经浮点
assert.equal(formatAmount("1234567.5"), "1,234,567.50");
assert.equal(formatAmount("-1000"), "-1,000.00");
assert.equal(formatAmount("0.105"), "0.105");
assert.equal(formatAmount(null), "");
// 待处理计数的几种形状
assert.deepEqual(parseCounts({ total: 8, companies: { comp_a: 3, comp_b: 5 } }), { total: 8, companies: { comp_a: 3, comp_b: 5 } });
assert.deepEqual(parseCounts([{ company: "comp_a", count: 2 }, { company: null, count: 1 }]), { total: 3, companies: { comp_a: 2 } });
// 分店：服务端给了就用；旧服务端按账套上的分店号现分（一套账服务几个分店、每个分店一套账都如实列）
const sets = [{ connection: "c", key: "69EXP", label: "69", branch: "00000" }, { connection: "c", key: "69B1", label: "69 B1", branch: "00001" },
  { connection: "c", key: "68EXP", label: "68", branch: "00000" }];
assert.deepEqual(branchesOf({ account_sets: sets } as any).map((b) => [b.branch, b.account_sets.map((s) => s.key)]),
  [["00000", ["69EXP", "68EXP"]], ["00001", ["69B1"]]]);
assert.deepEqual(branchesOf({ account_sets: sets, branches: [] } as any), []);
""", lang="th")

    def test_months_in_chinese_and_english(self):
        self.run_page(r"""
import { monthLabel } from "@/lib/companies";
assert.equal(monthLabel("2026-09"), "2026年9月");
""", lang="zh")
        self.run_page(r"""
import { branchShort, monthLabel } from "@/lib/companies";
assert.equal(monthLabel("2026-09"), "Sep 2026");
assert.equal(branchShort("00000"), "Head office");
""", lang="en")

    def test_sidebar_sections_put_pinned_companies_then_recent(self):
        self.run_page(r"""
import { companySections, groupOpen, toggleGroup } from "@/lib/managed-agents/sidebar-groups";
const at = (m: number) => Date.UTC(2026, 9, 15, 12, 0) - m * 60000;
const projects = ["A", "B", "C", "D", "E", "F"].map((x, i) => ({ id: `comp_${x}`, title: `Company ${x}`, last_active: new Date(at(100 + i)).toISOString() }));
let i = 0;
const row = (title: string, group: string, minutes: number, starred = false) => ({ index: i++, title, starred, time: at(minutes), group });
const rows = [
  row("pinned in A", "comp_A", 1, true),
  ...Array.from({ length: 7 }, (_, k) => row(`A chat ${k}`, "comp_A", 10 + k)),
  row("B chat", "comp_B", 5),
  row("loose 1", "", 2), row("loose 2", "", 3),
  row("gone company", "comp_deleted", 4),       // 侧栏里没有这家：算最近
];
const sections = companySections(rows, projects)!;
// 公司按我在组里的最近活动排（置顶的那段也算这家的活动）
assert.deepEqual(sections.map((s) => s.key),
  ["pinned", "project:comp_A", "project:comp_B", "project:comp_C", "project:comp_D", "project:comp_E", "project:comp_F", "recents"]);
const a = sections.find((s) => s.key === "project:comp_A") as any;
assert.equal(a.indices.length, 5);                // 每组先 5 条
assert.equal(a.all.length, 7);
assert.equal(a.more, true);
assert.ok(!a.all.includes(0));                    // 置顶的只在「已置顶」
assert.deepEqual((sections.at(-1) as any).indices.length, 3);
// 前 5 家默认展开，第 6 家收着；点开后记 open:project:<编号>，点收第一家记 project:<编号>
const f = sections.find((s) => s.key === "project:comp_F") as any;
assert.equal(groupOpen(f, new Set(), false), false);
assert.equal(groupOpen(f, new Set(), true), true);   // 搜索时都开
const opened = toggleGroup(f, new Set());
assert.deepEqual([...opened], ["open:project:comp_F"]);
assert.equal(groupOpen(f, opened, false), true);
const b = sections[2] as any;
assert.deepEqual([...toggleGroup(b, new Set())], ["project:comp_B"]);
assert.equal(groupOpen(b, new Set(["project:comp_B"]), false), false);
// 搜索同时匹配公司名：公司名命中时这家的对话都算
const found = companySections(rows, projects, { query: "company b" })!;
assert.deepEqual(found.map((s) => s.key), ["project:comp_B"]);
const byTitle = companySections(rows, projects, { query: "loose 2" })!;
assert.deepEqual(byTitle.map((s) => s.key), ["recents"]);
assert.equal(companySections(rows, projects, { query: "nothing" }), null);
// 服务端还有更多的公司：也给「显示更多」
const more = companySections([row("B only", "comp_B", 1)], projects, { serverMore: { comp_B: "x|y" } })!;
assert.equal((more.find((s) => s.key === "project:comp_B") as any).more, true);
""")

    def test_hand_the_rest_to_cc_with_their_month(self):
        self.run_page(r"""
import { handText, monthOfItems } from "@/components/pending";
const item: any = { document: "OCR-0001", company: "comp_a", reason_code: "failed", reason_label: "x", action: "open", date: "2026-09-12" };
const open = [{ ...item, invoice_no: "INV-1", document: "OCR-1", date: "2026-08-30" },
  { ...item, invoice_no: "", counterparty: "Shop", document: "OCR-2", date: "2026-09-01" },
  { ...item, invoice_no: "INV-3", document: "OCR-3", date: "2026-09-02" }];
assert.equal(handText(open), "Handle these: INV-1 [OCR-1]、Shop [OCR-2]、INV-3 [OCR-3]");
assert.equal(monthOfItems(open), "2026-09");
assert.equal(monthOfItems([{ ...item, date: "" }]), "");
""")

    def test_scheduled_tasks_group_by_company_and_seed_from_the_company(self):
        self.run_page(r"""
import { groupTasks, setForPeriod, taskSeedOf } from "@/lib/companies";
import { deepLink } from "@/lib/notify";
const rules = [{ name: "R1" }, { name: "R2" }, { name: "R3" }, { name: "R4" }];
const tasks = { R1: "comp_b", R2: "comp_a", R3: "comp_b" };
// 侧栏顺序的公司在前，其余按名称，归不上公司的放最后（""）
assert.deepEqual(groupTasks(rules, tasks, ["comp_b"], { comp_a: "A", comp_b: "B" }).map(([id, rows]) => [id, rows.map((r) => r.name)]),
  [["comp_b", ["R1", "R3"]], ["comp_a", ["R2"]], ["", ["R4"]]]);
// 新建从公司里建：覆盖这个月的账套；这台电脑绑了文件夹时连同文件夹（只需再选子文件夹）
const sets = [{ connection: "c1", adapter: "express", key: "70EXP", label: "70", period_start: "2027-01-01", period_end: "2027-12-31" },
  { connection: "c1", adapter: "express", key: "69EXP", label: "69", period_start: "2026-01-01", period_end: "2026-12-31" }];
const detail: any = { company: { id: "comp_a", title: "A", adapter: "express" }, account_sets: sets,
  folders: [{ reader: "rdr1", device: "PC", label: "D:\\A", state: "ok", root_id: "R9", folder: "A" }] };
assert.equal(setForPeriod(sets, "2026-09")?.key, "69EXP");
assert.deepEqual(taskSeedOf(detail, "2026-09", ["rdr1"], true),
  { adapter: "express", connection: "c1", account_set_key: "69EXP", folder: "rdr1|R9|A" });
assert.deepEqual(taskSeedOf(detail, "2026-09", ["rdr1"], false), { adapter: "express", connection: "c1", account_set_key: "69EXP" });
assert.equal(taskSeedOf({ ...detail, account_sets: [] }, "2026-09"), null);
// 提醒点开：同服务端 deep_link（属于某家公司的到主档页）
assert.equal(deepLink("", "company:abc1234567"), "/assistant?company=abc1234567");
assert.equal(deepLink("", "pending"), "/assistant?view=pending");
assert.equal(deepLink("", ""), "/assistant?view=tasks");
assert.equal(deepLink("sesn_1", "company:abc"), "/assistant?c=sesn_1");
""")


# 假站点：共用层（会话、projects）与 Cowork 公司接口，按契约回应
COMPANY_SITE = r"""
const ok = (message: any = null) => ({ ok: true, status: 200, json: async () => ({ message }) });
const at = (minutesAgo: number) => new Date(Date.UTC(2026, 9, 15, 13, 0) - minutesAgo * 60000).toISOString();
const row = (id: string, title: string, minutesAgo: number, extra: any = {}) =>
  ({ id, title, starred: false, created_at: at(minutesAgo), updated_at: at(minutesAgo), archived_at: null, unread: false,
     project: "", period: "", scope: "", ...extra });
const site: any = {
  kind: "company" as string | null,
  projects: [{ id: "comp_abc", title: "ABC Co", subtitle: "0105556000001 · 00000", last_active: at(1), count: 2 }],
  sessions: [] as any[], nextCursor: null as string | null, groups: {} as Record<string, string>,
  groupPages: {} as Record<string, any>, created: "sesn_new001", moveFails: false,
};
g.respond = async (method: string, args: any, body: any) => {
  if (method === "projects") return ok(site.kind ? { kind: site.kind, period: true, scope: true, projects: site.projects } : { kind: null, period: false, projects: [] });
  if (method === "listing") {
    if (args.grouped === "1" && args.group !== undefined) return ok(site.groupPages[args.group] ?? { sessions: [], nextCursor: null });
    const out: any = { sessions: site.sessions, nextCursor: site.nextCursor, runs: {} };
    if (args.grouped === "1") out.groups = site.groups;
    return ok(out);
  }
  if (method === "create") return ok(row(site.created, "", 0, body?.project ? { project: body.project, period: body.period ?? "", scope: body.scope ?? "" } : {}));
  if (method === "move") {
    if (site.moveFails) return { ok: false, status: 417, json: async () => ({ exception: "frappe.exceptions.ValidationError: no" }) };
    return ok({ ...row(body.session_id, "", 0), project: body.project, period: body.period, scope: body.scope });
  }
  return ok();
};
const calls = (method: string) => g.calls.filter((c: any) => c.method === method);
const push = (event: any) => g.socket.emit("pearnly_assistant_event", { session_id: "", event });
function sidebar() {
  const core: any = new RemoteThreadListThreadListRuntimeCore(
    { adapter: sessionListAdapter, runtimeHook: (() => null) as any } as any,
    { getModelContext: () => ({}), subscribe: () => () => {} } as any,
  );
  core._hookManager.startThreadRuntime = async () => ({});
  return { core, threads: new ThreadListRuntimeImpl(core) as any };
}
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class CompanySidebarBehaviourTests(unittest.TestCase):
    STUBS = {"cc-ma-setup": MA_SETUP, "socket.io-client": SYNC_SOCKET}
    IMPORTS = r"""
import { RemoteThreadListThreadListRuntimeCore } from "@assistant-ui/core/react";
import { ThreadListRuntimeImpl } from "@assistant-ui/core/internal";
import { sessionListAdapter } from "@/lib/managed-agents/session-list-adapter";
import { watchList } from "@/lib/managed-agents/list-sync";
import { fileSession, filingOf, groupOf, hintTitle, NO_FILING, setDraft, showMore, useProjects } from "@/lib/managed-agents/projects";
"""

    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\nimport assert from "node:assert/strict";\n' + self.IMPORTS
               + "\nconst g = globalThis as any;\n" + COMPANY_SITE + body + "\ng.done();\n", stubs=self.STUBS)

    def test_the_sidebar_lists_by_company_and_new_chats_carry_company_and_month(self):
        self.run_page(r"""
site.sessions = [row("sesn_aaa001", "Input VAT", 1, { project: "comp_abc", period: "2026-09", scope: "00001" }),
                 row("sesn_bbb001", "Loose", 2)];
site.groups = { comp_abc: "2026-10-15 12:00:00|x" };
const { core, threads } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
watchList(() => threads);
// 第一页按公司分组取（grouped=1）；每行的公司、月份、分店记下
assert.equal(calls("listing")[0].args.grouped, "1");
assert.equal(useProjects.getState().kind, "company");
assert.deepEqual(filingOf("sesn_aaa001"), { project: "comp_abc", period: "2026-09", scope: "00001" });
assert.equal(groupOf("sesn_bbb001"), "");
// 组头 ＋：开在这家、月份上个月（组件库第一条消息时建会话）
setDraft({ project: "comp_abc", period: "2026-09" });
await core.switchToNewThread();
const local = core.mainThreadId;
assert.equal(groupOf(local), "");
site.created = "sesn_new101";
const made = core.initialize(local);
assert.equal(groupOf(local), "comp_abc");          // 拿到服务端摘要之前就按本页编号归组，不先闪到「最近」
await made;
assert.deepEqual(calls("create").at(-1).body, { project: "comp_abc", period: "2026-09" });
assert.deepEqual(filingOf("sesn_new101"), { project: "comp_abc", period: "2026-09", scope: "" });
// Ctrl+N、托盘、快捷提问：清掉待建的公司，开在「最近」
setDraft({});
await core.switchToNewThread();
site.created = "sesn_new102";
await core.initialize(core.mainThreadId);
assert.deepEqual(calls("create").at(-1).body, {});
assert.deepEqual(filingOf("sesn_new102"), NO_FILING);
""")

    def test_the_first_chat_in_a_company_shows_its_group_at_once(self):
        # 2026-10-15 测试机：在主档页开这家的第一段对话，侧栏还没有这一组，新对话先闪在「最近」几秒才跳进公司
        self.run_page(r"""
site.sessions = [row("sesn_bbb001", "Loose", 2)];
const { core, threads } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
watchList(() => threads);
assert.ok(!useProjects.getState().projects.some((p) => p.id === "comp_first"));
hintTitle("comp_first", "Sincere Ice Co");           // 主档页读到这家的名称
setDraft({ project: "comp_first", period: "2026-09" });
await core.switchToNewThread();
const local = core.mainThreadId;
site.created = "sesn_new201";
const made = core.initialize(local);
assert.equal(groupOf(local), "comp_first");          // 不等 projects() 回来，这一组马上出现
assert.equal(useProjects.getState().projects.find((p) => p.id === "comp_first")?.title, "Sincere Ice Co");
await made;
""")

    # 服务端的 projects() 按「我有对话的公司」现算：会话建好之前这家不在里面。create 要等 release() 才回（生产约 1–4 秒）。
    SLOW_CREATE = r"""
let release: (ok?: boolean) => void = () => {};
const base = g.respond;
g.respond = async (method: string, args: any, body: any) => {
  if (method !== "create") return base(method, args, body);
  const ok = await new Promise<boolean>((resolve) => { release = (good = true) => resolve(good); });
  if (!ok) return { ok: false, status: 417, json: async () => ({ exception: "frappe.exceptions.ValidationError: no" }) };
  if (body?.project && !site.projects.some((p: any) => p.id === body.project)) {
    site.projects = [{ id: body.project, title: "นิภา เอ็กเช้นจ์ จำกัด", subtitle: "0105556000002 · 00000", last_active: at(0), count: 1 }, ...site.projects];
  }
  return base(method, args, body);
};
const projectReads = () => calls("projects").length;
"""

    def test_the_first_chat_in_a_company_stays_in_its_group_while_the_session_is_created(self):
        # 2026-10-15 生产 14:14（F6）：主档页发这家第一句，新对话先在「最近」约 2 秒才进公司组。原因：建会话之前记归属时就
        # 重取 projects()，那次回复（会话还没建，服务端不会有这家）把页面先搭出来的这一组整组盖掉；会话建好、再记一次归属
        # 才又搭出来。现在：会话建好之前先搭的组不被回复盖掉，建好后再重取一次换成服务端那一行。
        self.run_page(self.SLOW_CREATE + r"""
site.projects = [];
site.sessions = [row("sesn_bbb001", "Loose", 2)];
const { core, threads } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
watchList(() => threads);
hintTitle("comp_nipa", "นิภา เอ็กเช้นจ์");             // 主档页读到这家的名称
setDraft({ project: "comp_nipa", period: "2026-09" });
await core.switchToNewThread();
const local = core.mainThreadId;
site.created = "sesn_new301";
const made = core.initialize(local);
assert.ok(core.threadIds.includes(local));           // 组件库已把这一行列进侧栏
assert.equal(groupOf(local), "comp_nipa");
// 建会话期间：记归属时的重取、回到前台的核对都回来了，回复里还没有这家，这一组也不散
await g.sleep(20);
assert.equal(groupOf(local), "comp_nipa", "after the read sent before the session exists");
g.document.fire("visibilitychange");
await g.sleep(30);
assert.equal(groupOf(local), "comp_nipa", "after a check while the session is being created");
assert.equal(useProjects.getState().projects.find((p) => p.id === "comp_nipa")?.title, "นิภา เอ็กเช้นจ์");
// 会话建好：再重取一次，换成服务端那一行（带副标题），新旧编号都在这一组
const before = projectReads();
release();
await made;
await g.sleep(30);
assert.equal(projectReads(), before + 1);
const nipa = useProjects.getState().projects.filter((p) => p.id === "comp_nipa");
assert.equal(nipa.length, 1);
assert.equal(nipa[0].subtitle, "0105556000002 · 00000");
assert.equal(groupOf(local), "comp_nipa");
assert.equal(groupOf("sesn_new301"), "comp_nipa");
// 之后的重取照服务端：这家没有对话了（别处删了）就不再显示
site.projects = [];
g.document.fire("visibilitychange");
await g.sleep(30);
assert.equal(groupOf("sesn_new301"), "");
""")

    def test_a_chat_in_an_existing_company_group_never_shows_in_recents(self):
        self.run_page(self.SLOW_CREATE + r"""
site.sessions = [row("sesn_aaa001", "Input VAT", 1, { project: "comp_abc", period: "2026-09" })];
const { core, threads } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
watchList(() => threads);
const before = projectReads();
setDraft({ project: "comp_abc", period: "2026-09" });
await core.switchToNewThread();
const local = core.mainThreadId;
site.created = "sesn_new302";
const made = core.initialize(local);
assert.equal(groupOf(local), "comp_abc");
await g.sleep(30);
assert.equal(groupOf(local), "comp_abc");
release();
await made;
await g.sleep(30);
assert.equal(groupOf(local), "comp_abc");
assert.equal(groupOf("sesn_new302"), "comp_abc");
assert.equal(projectReads(), before);                // 侧栏本来就有这家：不必重取
""")

    def test_a_failed_first_chat_leaves_no_empty_company_group(self):
        self.run_page(self.SLOW_CREATE + r"""
site.projects = [];
const { core, threads } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
watchList(() => threads);
hintTitle("comp_nipa", "นิภา เอ็กเช้นจ์");
setDraft({ project: "comp_nipa", period: "2026-09" });
await core.switchToNewThread();
const local = core.mainThreadId;
const made = core.initialize(local);
assert.equal(groupOf(local), "comp_nipa");
release(false);
await assert.rejects(made);
assert.ok(!core.threadIds.includes(local));
assert.equal(groupOf(local), "");
assert.ok(!useProjects.getState().projects.some((p) => p.id === "comp_nipa"));
""")

    def test_moving_into_a_company_not_in_the_sidebar_keeps_the_group_until_the_server_answers(self):
        # 同一个毛病的另一条路：「移到 ▸」一家侧栏还没有的公司，移动请求回来之前重取的回复也不拆先搭的组
        self.run_page(r"""
site.projects = [];
site.sessions = [row("sesn_bbb001", "Loose", 2)];
const base = g.respond;
let release: () => void = () => {};
g.respond = async (method: string, args: any, body: any) => {
  if (method !== "move") return base(method, args, body);
  await new Promise<void>((resolve) => { release = resolve; });
  site.projects = [{ id: "comp_nipa", title: "นิภา เอ็กเช้นจ์ จำกัด", last_active: at(0), count: 1 }];
  return base(method, args, body);
};
const { core, threads } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
watchList(() => threads);
hintTitle("comp_nipa", "นิภา เอ็กเช้นจ์");             // 选公司的菜单里点了这家
const moving = fileSession("sesn_bbb001", { project: "comp_nipa", period: "", scope: "" });
assert.equal(groupOf("sesn_bbb001"), "comp_nipa");
g.document.fire("visibilitychange");
await g.sleep(30);
assert.equal(groupOf("sesn_bbb001"), "comp_nipa");
release();
await moving;
await g.sleep(30);
assert.equal(useProjects.getState().projects.find((p) => p.id === "comp_nipa")?.title, "นิภา เอ็กเช้นจ์ จำกัด");
assert.equal(groupOf("sesn_bbb001"), "comp_nipa");
""")

    def test_moves_renames_and_deletes_merge_at_once(self):
        self.run_page(r"""
site.sessions = [row("sesn_aaa001", "Input VAT", 1, { project: "comp_abc", period: "2026-09" }), row("sesn_bbb001", "Loose", 2)];
const { core, threads } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
watchList(() => threads);
const projectReads = () => calls("projects").length;
// 别处（或本人另一台设备）移动对话、换月份：随推随并
push({ type: "pearnly.project", op: "move", session_id: "sesn_bbb001", project: "comp_abc", period: "2026-08", scope: "" });
await g.sleep(20);
assert.deepEqual(filingOf("sesn_bbb001"), { project: "comp_abc", period: "2026-08", scope: "" });
// 移进侧栏里还没有的公司：重取一次 projects()
const before = projectReads();
site.projects = [...site.projects, { id: "comp_new", title: "New Co", last_active: at(0), count: 1 }];
push({ type: "pearnly.project", op: "move", session_id: "sesn_aaa001", project: "comp_new", period: "" });
await g.sleep(50);
assert.equal(projectReads(), before + 1);
assert.equal(groupOf("sesn_aaa001"), "comp_new");
// 改名：侧栏里有就就地改，没有就忽略
push({ type: "pearnly.project", op: "upsert", project: { id: "comp_abc", title: "ABC Renamed" }, part: "master" });
push({ type: "pearnly.project", op: "upsert", project: { id: "comp_other", title: "Not mine" }, part: "notes" });
await g.sleep(20);
assert.deepEqual(useProjects.getState().projects.map((p) => p.title).sort(), ["ABC Renamed", "New Co"]);
// 删公司：这一组的对话并入最近
push({ type: "pearnly.project", op: "delete", id: "comp_abc" });
await g.sleep(20);
assert.equal(groupOf("sesn_bbb001"), "");
assert.deepEqual(filingOf("sesn_bbb001"), NO_FILING);
assert.ok(!useProjects.getState().projects.some((p) => p.id === "comp_abc"));
// 本页移到 ▸ / 改月份：发 move（不改置顶）；服务端拒了就回到原来的
await fileSession("sesn_aaa001", { project: "comp_new", period: "2026-07", scope: "00002" });
assert.deepEqual(calls("move").at(-1).body, { session_id: "sesn_aaa001", project: "comp_new", period: "2026-07", scope: "00002" });
assert.deepEqual(filingOf("sesn_aaa001"), { project: "comp_new", period: "2026-07", scope: "00002" });
site.moveFails = true;
await assert.rejects(fileSession("sesn_aaa001", { project: "", period: "2026-07", scope: "" }));
assert.deepEqual(filingOf("sesn_aaa001"), { project: "comp_new", period: "2026-07", scope: "00002" });
// 移回最近：月份分店一并清空
site.moveFails = false;
await fileSession("sesn_aaa001", { project: "", period: "2026-07", scope: "00002" });
assert.deepEqual(calls("move").at(-1).body, { session_id: "sesn_aaa001", project: "", period: "", scope: "" });
assert.equal(calls("star").length, 0);
""")

    def test_show_more_pages_only_that_company(self):
        self.run_page(r"""
site.sessions = [...Array.from({ length: 5 }, (_, k) => row(`sesn_a${k}0001`, `A ${k}`, k + 1, { project: "comp_abc" })), row("sesn_r00001", "R", 9)];
site.groups = { comp_abc: "2026-10-15 12:55:00|sesn_a40001" };
site.groupPages.comp_abc = { sessions: [row("sesn_a50001", "A 5", 20, { project: "comp_abc" })], nextCursor: null };
const { core } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
await showMore("comp_abc", 5, () => core.loadMore());
const paged = calls("listing").filter((c: any) => c.args.group !== undefined);
assert.deepEqual(paged.map((c: any) => [c.args.group, c.args.after]), [["comp_abc", "2026-10-15 12:55:00|sesn_a40001"]]);
assert.ok(core.threadIds.includes("sesn_a50001"));
assert.equal(useProjects.getState().shown.comp_abc, 15);
assert.ok(!("comp_abc" in useProjects.getState().more.groups));  // 这家翻完了
""")

    def test_grouping_by_month_or_none_uses_the_plain_listing(self):
        # 侧栏「分组依据」（CC 外壳设计 3.6）：按月份 / 不分组时用普通列表（按上次更新分页），换回公司时用分组接口；换的那一刻整表重拉
        self.run_page(r"""
import { createdOf, setSidebarView } from "@/lib/managed-agents/sidebar-view";
site.sessions = [row("sesn_aaa001", "Input VAT", 1, { project: "comp_abc", period: "2026-09" }), row("sesn_bbb001", "Loose", 2)];
const { core, threads } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
watchList(() => threads);
assert.equal(calls("listing")[0].args.grouped, "1");
assert.equal(createdOf("sesn_aaa001"), Date.parse(site.sessions[0].created_at));   // 排序「创建日期」用
setSidebarView({ by: "month" });
await g.sleep(30);
assert.equal(calls("listing").length, 2);
assert.equal(calls("listing")[1].args.grouped, undefined);
assert.deepEqual(filingOf("sesn_aaa001"), { project: "comp_abc", period: "2026-09", scope: "" });   // 普通列表也带归属
assert.deepEqual(useProjects.getState().more, { recent: null, groups: {} });
// 月份与不分组之间换只是重排，不重拉
setSidebarView({ by: "none" });
await g.sleep(30);
assert.equal(calls("listing").length, 2);
// 回到公司：分组接口
setSidebarView({ by: "company" });
await g.sleep(30);
assert.equal(calls("listing").length, 3);
assert.equal(calls("listing")[2].args.grouped, "1");
""")

    def test_products_without_companies_keep_the_old_listing(self):
        self.run_page(r"""
site.kind = null;
site.sessions = [row("sesn_aaa001", "A", 1)];
const { core } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
assert.equal(useProjects.getState().kind, null);
assert.equal(calls("listing")[0].args.grouped, undefined);
setDraft({ project: "comp_abc", period: "2026-09" });   // 没有公司分组：待建的公司不带给服务端
await core.switchToNewThread();
await core.initialize(core.mainThreadId);
assert.deepEqual(calls("create").at(-1).body, {});
""")


# ── CC 外壳第 1 批 · 左侧栏（docs/work/2026-10-16-cc-shell-design.md 3.1–3.8、3.16、4.2）──────────────────────────────────

class SidebarShellSourceTests(unittest.TestCase):
    def test_only_the_current_page_is_selected_in_the_nav(self):
        app = read("assistant.tsx")
        nav = app.split("function SidebarNav()", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('data-active={(view === "tasks" && !taskCompany) || undefined}', nav)
        company = app.split("function CompanyNav(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('data-active={(view === "pending" && !pendingCompany) || undefined}', company)
        self.assertIn('data-active={view === "companies" || undefined}', company)   # 主档页不再把「公司」选中
        self.assertNotIn('view === "company" ||', company)
        # 带修饰键点对话是多选：不切回对话页
        self.assertIn("if (e.shiftKey || e.metaKey || e.ctrlKey) return;", app.split("function useBackToChatOnPick(", 1)[1].split("\n}\n", 1)[0])

    def test_group_headers_toggle_as_a_whole_and_reveal_their_actions(self):
        group = read("components/assistant-ui/sidebar-group.tsx")
        header = group.split("export const GroupHeader: FC", 1)[1].split("\n);\n", 1)[0]
        self.assertIn('data-roving=""', header)
        self.assertIn("aria-expanded={open}", header)
        self.assertIn("onClick={onToggle}", header)
        self.assertIn('<ChevronRightIcon className="cc-group-caret" aria-hidden />', header)  # 名字后的 12px 箭头
        self.assertLess(header.index("cc-group-name"), header.index("cc-group-caret"))
        projects = read("components/assistant-ui/projects.tsx")
        head = projects.split("export const ProjectHeader: FC", 1)[1].split("\n};\n", 1)[0]
        # 名字不再打开主档页：只在「···」里（打开公司、在这家新建对话）
        self.assertEqual(head.count("openCompany(project.id)"), 1)
        self.assertIn("{co.openCompany}", head)
        self.assertIn("{sb.newChatHere}", head)
        self.assertIn("onClick={() => go(() => openPending(project.id))}", head)   # 待处理数：只看这家
        # 右侧：待处理数、···、分组和排序、新建（新建在最右，悬停才出）
        order = [head.index(x) for x in ('className="cc-group-count"', "cc-group-more", "<GroupSortMenu />", "<GroupNew ")]
        self.assertEqual(order, sorted(order))
        css = read("globals.css")
        self.assertIn(".cc-group-more, .cc-group-new { display: none; }", css)
        self.assertIn(".cc-group-row:is(:hover, :focus-within, [data-open]) :is(.cc-group-more, .cc-group-new)", css)
        self.assertIn('[data-slot="sidebar-inner"]:hover .cc-group-caret', css)
        self.assertIn(".cc-group-row:hover .cc-group-toggle, .cc-group-toggle:focus-visible { color: var(--cc-ink-2); }", css)
        # 触屏平板（宽屏、不能悬停）与手机：箭头、组操作常显
        self.assertIn('@media (hover: none) {\n  [data-slot="sidebar"] { --cc-reveal-rest: 1; }\n'
                      '  [data-slot="sidebar"] :is(.cc-group-more, .cc-group-new) { display: inline-flex; }', css)
        self.assertIn(':root[data-cc-variant="phone"] :is(.cc-group-more, .cc-group-new) { display: inline-flex; }', css)
        self.assertNotIn(".cc-project-name", css + projects)

    def test_rows_fade_pulse_and_take_a_metadata_line(self):
        rows = read("components/assistant-ui/thread-list.tsx")
        item = block(rows, "export const ThreadListItem: FC")
        self.assertIn("data-row-id={localId}", item)
        self.assertIn('data-selected={selected ? "" : undefined}', item)
        self.assertIn('data-roving=""', item)
        self.assertIn("<RowMeta />", item)
        self.assertIn('<span className="cc-row-end">', item)
        self.assertNotIn("FilingTag", rows)
        self.assertNotIn("LoaderIcon className=\"size-3.5 animate-spin\" />}\n    </span>", rows)
        meta = rows.split("const RowMeta: FC", 1)[1].split("\n};\n", 1)[0]
        self.assertIn('by === "company" ? [month, branch] : by === "month" ? [company, branch] : [company, month, branch]', meta)
        self.assertIn("if (!kind || !meta || !filing.project) return null;", meta)
        css = read("globals.css")
        self.assertIn("mask-image: linear-gradient(to right, black calc(100% - var(--cc-row-fade)), transparent);", css)
        self.assertIn("animation: cc-pulse var(--cc-pulse) ease-in-out infinite;", css)
        self.assertIn("width: var(--cc-row-pulse); height: var(--cc-row-pulse);", css)
        self.assertIn('[data-slot="aui_thread-list-item"][data-selected] { background: var(--cc-row-selected); color: var(--cc-ink); }', css)
        self.assertIn("[data-jump-hints] .cc-row-end[data-jump]::after { content: attr(data-jump); }", css)
        # 手机抽屉：行字仍 14（不再放大到 17），行与组标题带 44 由手机变体给
        drawer = css.split("/* 手机抽屉（设计文档 3.16）", 1)[1].split("/* 手机顶栏与抽屉里的圆按钮", 1)[0]
        self.assertNotIn("font-size", drawer)
        self.assertIn('.cc-drawer [data-slot="aui_thread-list-item"] { min-height: var(--cc-row-h);', drawer)

    def test_multi_select_has_no_bulk_bar_and_runs_one_by_one(self):
        rows = read("components/assistant-ui/thread-list.tsx")
        self.assertNotIn("BulkBar", rows)
        bulk = rows.split("const BulkItems: FC", 1)[1]
        self.assertIn("sb.pinN(ids.length)", bulk)
        self.assertIn("sb.unpinN(ids.length)", bulk)
        self.assertIn("sb.deleteN(ids.length)", bulk)
        self.assertIn("<BulkMoveMenu sessions={sessions} onDone={clearSelection} />", bulk)
        self.assertEqual(bulk.count("runInOrder("), 2)
        self.assertIn("await item.updateCustom({ ...custom, starred: !allPinned });", bulk)   # 保留 custom 里别的键
        projects = read("components/assistant-ui/projects.tsx")
        self.assertIn('<SubMenu icon={<Building2Icon />} label={sb.moveToN(sessions.length)} className="cc-menu !min-w-52 max-w-72">', projects)
        self.assertIn('runInOrder(sessions, (id) => fileSession(id, { project, period: "", scope: "" }), report)', projects)
        # 单字母快捷键：按物理键认（泰文键盘下也一样），批量时只有 P、D
        self.assertIn("letterKeys({ KeyP: pin, KeyR: onRename, KeyD: remove }, close)", rows)
        self.assertIn("letterKeys({ KeyP: pinAll, KeyD: deleteAll }, close)", rows)
        keys = read("lib/sidebar-keys.ts")
        self.assertIn('const isDesktopWindow = () => document.documentElement.dataset.ccVariant === "desktop";', keys)
        self.assertIn("if (!isDesktopWindow()) return;", keys)
        self.assertIn("const HINT_DELAY_MS = 300;", keys)
        self.assertIn("<SidebarContent ref={keys}", read("assistant.tsx"))

    def test_collapse_and_grouping_are_remembered_on_this_device(self):
        app = read("assistant.tsx")
        self.assertIn('const SIDEBAR_OPEN_KEY = "cc.sidebar.open";', app)
        self.assertIn("<SidebarProvider style={sidebarWidth.style} open={sidebarOpen} onOpenChange={setSidebarOpen}>", app)
        view = read("lib/managed-agents/sidebar-view.ts")
        self.assertIn("const viewKey = (user: string) => `cc.sidebar.view:${user}`;", view)
        self.assertIn('export const DEFAULT_VIEW: SidebarView = { by: "company", sort: "updated", desc: true, meta: true, empty: true };', view)
        self.assertIn("if (useProjects.getState().kind && grouped(now.by) !== grouped(before.by)) void threads().reload()",
                      read("lib/managed-agents/list-sync.ts"))
        css = read("globals.css")
        self.assertIn('[data-slot="sidebar"][data-state="collapsed"] ~ [data-slot="sidebar-inset"] .cc-topbar > .cc-icon-btn:first-child '
                      "{ border-radius: var(--cc-radius-full); }", css)


STORAGE_STUB = r"""
const store = new Map<string, string>();
(globalThis as any).localStorage = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => void store.set(k, String(v)),
  removeItem: (k: string) => void store.delete(k),
};
(globalThis as any).storage = store;
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class SidebarShellBehaviourTests(unittest.TestCase):
    def run_page(self, body, stubs=None):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + body + "\n(globalThis as any).done();\n",
               stubs={"socket.io-client": "export function io() { return { on() { return this; }, off() { return this; }, connected: true }; }",
                      **(stubs or {})})

    def test_sorting_month_and_flat_groups(self):
        self.run_page(r"""
import { companySections, compareRows, flatSections, monthSections } from "@/lib/managed-agents/sidebar-groups";
const at = (m: number) => Date.UTC(2026, 9, 15, 12, 0) - m * 60000;
const projects = [{ id: "comp_A", title: "Beta Co", last_active: new Date(at(50)).toISOString() },
                  { id: "comp_B", title: "Alpha Co", last_active: new Date(at(60)).toISOString() },
                  { id: "comp_E", title: "Empty Co", last_active: new Date(at(70)).toISOString() }];
const rows = [
  { index: 0, title: "zeta", starred: false, time: at(1), group: "comp_A", created: at(300), period: "2026-09" },
  { index: 1, title: "alpha", starred: false, time: at(5), group: "comp_A", created: at(100), period: "2026-08" },
  { index: 2, title: "mid", starred: false, time: at(3), group: "comp_B", created: at(10), period: "2026-09" },
  { index: 3, title: "loose", starred: false, time: at(2), group: "", created: at(400), period: "" },
  { index: 4, title: "pinned", starred: true, time: at(9), group: "comp_B", created: at(1), period: "2026-07" },
];
const keys = (s: any[] | null) => s!.map((x) => x.key);
const idx = (s: any[] | null, key: string) => s!.find((x) => x.key === key).indices;
// 默认（上次更新、降序）：公司按最近活动，空的公司也显示
let s = companySections(rows, projects);
assert.deepEqual(keys(s), ["pinned", "project:comp_A", "project:comp_B", "project:comp_E", "recents"]);
assert.deepEqual(idx(s, "project:comp_A"), [0, 1]);
// 名称升序：公司按公司名、组内按标题
s = companySections(rows, projects, { sort: { by: "name", desc: false } });
assert.deepEqual(keys(s), ["pinned", "project:comp_B", "project:comp_A", "project:comp_E", "recents"]);
assert.deepEqual(idx(s, "project:comp_A"), [1, 0]);
// 创建日期降序：组内新建的在前；公司按这家最新创建的对话
s = companySections(rows, projects, { sort: { by: "created", desc: true }, empty: false });
assert.deepEqual(keys(s), ["pinned", "project:comp_B", "project:comp_A", "recents"]);   // 关掉空分组：Empty Co 不显示
assert.deepEqual(idx(s, "project:comp_A"), [1, 0]);
// 本页新建、还没有创建时间的行按上次更新算
assert.ok(compareRows({ by: "created", desc: true })({ index: 9, title: "", starred: false, time: at(0), group: "" }, rows[3] as any) < 0);
// 按月份：已置顶 → 新的月份在前 → 未指定月份；升序时旧的在前
s = monthSections(rows, projects);
assert.deepEqual(keys(s), ["pinned", "month:2026-09", "month:2026-08", "month:"]);
assert.deepEqual(idx(s, "month:2026-09"), [0, 2]);
assert.deepEqual(keys(monthSections(rows, projects, { sort: { by: "updated", desc: false } })), ["pinned", "month:2026-08", "month:2026-09", "month:"]);
// 搜索也认公司名
assert.deepEqual(keys(monthSections(rows, projects, { query: "alpha co" })), ["pinned", "month:2026-09"]);
assert.equal(monthSections(rows, projects, { query: "nothing" }), null);
// 不分组：已置顶 + 其余一组
s = flatSections(rows, projects);
assert.deepEqual(keys(s), ["pinned", "recents"]);
assert.deepEqual(idx(s, "recents"), [0, 3, 2, 1]);
""")

    def test_view_preferences_are_kept_per_user(self):
        self.run_page(r"""
import "cc-storage";
import { bindSidebarViewUser, DEFAULT_VIEW, isDefaultView, parseView, resetSidebarView, setSidebarView, useSidebarView } from "@/lib/managed-agents/sidebar-view";
const g = globalThis as any;
assert.deepEqual(parseView({ by: "month", sort: "bogus", desc: false, meta: "x" }), { ...DEFAULT_VIEW, by: "month", desc: false });
assert.deepEqual(parseView(null), DEFAULT_VIEW);
assert.ok(isDefaultView(useSidebarView.getState()));
bindSidebarViewUser("a@x.test");
setSidebarView({ by: "month", meta: false });
assert.equal(g.storage.get("cc.sidebar.user"), "a@x.test");
assert.deepEqual(JSON.parse(g.storage.get("cc.sidebar.view:a@x.test")), { ...DEFAULT_VIEW, by: "month", meta: false });
assert.ok(!isDefaultView(useSidebarView.getState()));
// 换一个人：用他自己的（没记过就是默认）；换回来照旧
bindSidebarViewUser("b@x.test");
assert.deepEqual(useSidebarView.getState(), DEFAULT_VIEW);
bindSidebarViewUser("a@x.test");
assert.equal(useSidebarView.getState().by, "month");
// 恢复默认：不再留着这一项
resetSidebarView();
assert.ok(!g.storage.has("cc.sidebar.view:a@x.test"));
assert.deepEqual(useSidebarView.getState(), DEFAULT_VIEW);
""", stubs={"cc-storage": STORAGE_STUB})

    def test_selection_keys_and_one_by_one(self):
        self.run_page(r"""
import { clearSelection, jumpDigit, nextIndex, rangeBetween, runInOrder, selectRange, toggleIn, toggleSelected, useSidebarSelection } from "@/lib/sidebar-keys";
const order = ["a", "b", "c", "d", "e"];
assert.deepEqual(rangeBetween(order, "b", "d"), ["b", "c", "d"]);
assert.deepEqual(rangeBetween(order, "d", "b"), ["b", "c", "d"]);   // 往上选也按页面先后
assert.deepEqual(rangeBetween(order, null, "c"), ["c"]);
assert.deepEqual(rangeBetween(order, "gone", "c"), ["c"]);
assert.deepEqual(rangeBetween(order, "a", "gone"), []);
assert.deepEqual(toggleIn(["a", "c"], "c"), ["a"]);
assert.deepEqual(toggleIn(["a"], "c"), ["a", "c"]);
// ⌘ / Ctrl 点起头，Shift 点到哪儿选到哪儿（跨组按页面先后）；Esc 清空
toggleSelected("b");
selectRange(order, "e");
assert.deepEqual(useSidebarSelection.getState(), { ids: ["b", "c", "d", "e"], anchor: "b" });
selectRange(order, "a");
assert.deepEqual(useSidebarSelection.getState().ids, ["a", "b"]);
clearSelection();
assert.deepEqual(useSidebarSelection.getState(), { ids: [], anchor: null });
// 方向键到头不绕回
assert.equal(nextIndex("ArrowDown", 4, 5), 4);
assert.equal(nextIndex("ArrowUp", 0, 5), 0);
assert.equal(nextIndex("End", 1, 5), 4);
assert.equal(nextIndex("Home", 3, 5), 0);
assert.equal(nextIndex("Enter", 3, 5), null);
// 跳转按物理键：主键盘、小键盘 1–9；0 不算
assert.equal(jumpDigit("Digit3"), 3);
assert.equal(jumpDigit("Numpad9"), 9);
assert.equal(jumpDigit("Digit0"), 0);
assert.equal(jumpDigit("KeyA"), 0);
// 批量：一条做完再下一条，失败的不挡后面的，只报第一个错
const log: string[] = [];
let running = 0;
const errors: unknown[] = [];
const done = await runInOrder(["x", "y", "z"], async (id) => {
  running++;
  assert.equal(running, 1);
  await (globalThis as any).sleep(5);
  log.push(id);
  running--;
  if (id !== "z") throw new Error(id);
}, (e) => errors.push(e));
assert.deepEqual(log, ["x", "y", "z"]);
assert.equal(done, 1);
assert.equal(errors.length, 1);
""")


# 桌面版桥的桩：state().features 里有没有 company_folders；pick_folder 回一个 binding
DESKTOP_ENV = r"""
const g = globalThis as any;
g.qt = { webChannelTransport: {} };
g.bridgeCalls = [] as any[];
g.tomoDesktop = {
  state(cb: (json: string) => void) { cb(JSON.stringify({ version: "1.6.0", site: "site.test", express_roots: [], members: [], autostart: false,
    hotkey: true, update: { state: "", version: "" }, features: g.FEATURES, legacy_roots: [] })); },
  call(method: string, args: string, cb: (json: string) => void) {
    g.bridgeCalls.push({ method, args: JSON.parse(args) });
    cb(JSON.stringify({ ok: true, binding: { reader: "rdr1", root_id: "r1", folder: "ABC/2569", label: "D:\\Clients\\ABC", share: "", device: "PC-1" },
      name: "ABC", created: false, reported: true }));
  },
  changed: { connect() {}, disconnect() {} },
  done: { connect() {}, disconnect() {} },
};
"""

COMPANY_RENDER = r"""
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { SidebarProvider } from "@/components/ui/sidebar";
import { ChoiceRow, FolderSection, SetsSection } from "@/components/company-page";
import { loadDesktopFeatures } from "@/lib/desktop";

const g = globalThis as any;
const html = (type: any, props: any = null) => renderToStaticMarkup(createElement(SidebarProvider, null, createElement(type, props)));
const text = (markup: string) =>
  markup.replace(/<[^>]+>/g, "\n").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").split("\n").map((s) => s.trim()).filter(Boolean);
const buttons = (markup: string) => [...markup.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((m) => text(m[1]).join(" "));
const folder = { reader: "rdr9", device: "PC-9", label: "\\\\nas\\ABC", state: "ok", month_folder: "2569-09", mine: false };
const noop = async () => {};
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class CompanyPageBehaviourTests(unittest.TestCase):
    STUBS = {"socket.io-client": "export function io() { return { on() { return this; }, off() { return this; }, connected: true }; }",
             "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;'}

    def run_page(self, body, desktop=None, stubs=None):
        env = "cc-desktop-env" if desktop is not None else "cc-browser-env"
        stubs = {**self.STUBS, "cc-desktop-env": DESKTOP_ENV.replace("g.FEATURES", json.dumps(desktop or [])),
                 "cc-browser-env": "export {};", "cc-ma-setup": MA_SETUP, **(stubs or {})}
        run_ts(f'import "cc-test-setup";\nimport "{env}";\nimport "cc-ma-setup";\n' + COMPANY_RENDER + body + "\n(globalThis as any).done();\n",
               stubs=stubs)

    def test_browsers_and_phones_only_read_the_folder(self):
        self.run_page(r"""
assert.deepEqual(await loadDesktopFeatures(), { app: false, companyFolders: false });
const empty = html(FolderSection, { company: "comp_abc", folders: [], reload: noop });
assert.ok(text(empty).includes("Choose on computer"));     // 一行字，不是按钮
assert.deepEqual(buttons(empty), []);
const bound = html(FolderSection, { company: "comp_abc", folders: [folder], reload: noop });
assert.deepEqual(text(bound), ["Documents folder", "PC-9 · \\\\nas\\ABC", "Ready · This month: 2569-09"]);
assert.deepEqual(buttons(bound), []);
""")

    def test_the_desktop_window_picks_and_hands_the_binding_over_unchanged(self):
        self.run_page(r"""
import { pickFolder } from "@/lib/desktop";
import { bindFolder } from "@/lib/companies";
assert.deepEqual(await loadDesktopFeatures(), { app: true, companyFolders: true });
const empty = html(FolderSection, { company: "comp_abc", folders: [], reload: noop });
assert.deepEqual(buttons(empty), ["Choose on computer"]);
// 别的电脑那一行：只看状态；本机还没绑时照样给「在电脑上选择」
const other = html(FolderSection, { company: "comp_abc", folders: [folder], reload: noop });
assert.deepEqual(buttons(other), ["Choose on computer"]);
// 本机这一行：更换… / 移除
const mine = html(FolderSection, { company: "comp_abc", folders: [{ ...folder, mine: true }], reload: noop });
assert.deepEqual(buttons(mine), ["Change…", "Remove"]);
// 桥只收语言、不收路径；给的 binding 原样交给 folder_bind
const picked: any = await pickFolder();
assert.deepEqual(g.bridgeCalls.at(-1), { method: "pick_folder", args: { lang: "en" } });
await bindFolder("comp_abc", picked.binding);
const sent = g.calls.at(-1);
assert.equal(sent.method, "/api/method/pearnly_cowork.companies.api.folder_bind");
assert.deepEqual(JSON.parse(sent.body.binding), picked.binding);
assert.equal(sent.body.company, "comp_abc");
""", desktop=["company_folders"])

    def test_old_desktops_are_asked_to_update(self):
        self.run_page(r"""
assert.deepEqual(await loadDesktopFeatures(), { app: true, companyFolders: false });
const page = html(FolderSection, { company: "comp_abc", folders: [], reload: noop });
assert.deepEqual(buttons(page), ["Update CC Desktop"]);
""", desktop=[])

    def test_choosing_in_place_follows_the_server_contract(self):
        # 跨端契约（审查 2026-10-15 major）：服务端列表只给 choice{connection, account_set_key, kinds}；页面展开时取
        # push.application.api.choices，作答经 assistant.pending_api.answer（记下选择并按 CC 同一条路重推）。假数据就是
        # 服务端测试断言过的真实输出（tests/fixtures/cc-pending-contract.json）。
        fixture = (ROOT / "tests/fixtures/cc-pending-contract.json").read_text(encoding="utf-8")
        self.run_page(r"""
import fixture from "cc-pending-fixture";
import { answerChoices, answered, loadChoices, loadPending, parseCounts, setChoice } from "@/lib/companies";
import { choosable, choosableView, optionsOf } from "@/components/pending";
const item: any = { ...fixture.item, company: "comp_abc" };
assert.equal(choosable(item), true);
assert.equal(choosable({ ...item, action: "open" }), false);
// 展开：按 choice 取要选的项（GET，同复核页）
g.respond = async () => ({ ok: true, status: 200, json: async () => ({ message: fixture.choices }) });
const view = await loadChoices(item);
const asked = g.calls.at(-1);
assert.equal(asked.method, "/api/method/pearnly_cowork.push.application.api.choices");
assert.deepEqual(asked.args, { history: "h1", connection: "C1", account_set_key: "S:\\69EXP\\ABC" });
assert.equal(choosableView(view), true);
// 建议（服务端 suggested 的顺序）排第一、预选；付款用项自带的可选值
const [payment, account] = view.needed;
assert.deepEqual(optionsOf(view, payment).map((o) => o.label), ["Cash", "Credit"]);
assert.deepEqual(optionsOf(view, account).slice(0, 2).map((o) => o.value), ["5390-99", "5320-01"]);
assert.equal(optionsOf(view, account).length, view.options.accounts.length);   // 不重复
// 科目表还没读到（wait）或没有可选值：当场选不了，交给 CC
assert.equal(choosableView({ ...view, wait: { state: "reading", message: "…" } }), false);
assert.equal(choosableView({ ...view, options: { accounts: [] } }), false);
// 作答：只交选了的项，回的结果形状同服务端
g.respond = async () => ({ ok: true, status: 200, json: async () => ({ message: fixture.answer }) });
const result = await answerChoices(item, view, { [`account\n${account.subject}`]: "5390-99" });
const sent = g.calls.at(-1);
assert.equal(sent.method, "/api/method/pearnly_cowork.assistant.pending_api.answer");
assert.deepEqual({ ...sent.body, items: JSON.parse(sent.body.items) }, { document: "h1",
  items: [{ kind: "account", subject: account.subject, value: "5390-99", label: account.label }] });
assert.equal(answered(result), true);
assert.equal(answered({ status: "needs_choice" }), false);
assert.equal(answered({ status: "already_success" }), true);
// 计数：服务端的 {total, companies, unassigned}
assert.deepEqual(parseCounts(fixture.counts), { total: 4, companies: { COMPANY: 2, OTHER: 1 } });
// 待处理按公司取（不给公司 = 全部）；服务端回 {items, count}
g.respond = async () => ({ ok: true, status: 200, json: async () => ({ message: { items: [item], count: 1 } }) });
assert.equal((await loadPending("comp_abc")).length, 1);
assert.equal(g.calls.at(-1).args.company, "comp_abc");
await loadPending();
assert.equal(g.calls.at(-1).args.company, undefined);
// 改付款方式带上页面当前的月份（服务端写进记过它的每份账套与这个月各分店的账套）
g.respond = async () => ({ ok: true, status: 200, json: async () => ({ message: { saved: true } }) });
await setChoice("comp_abc", "k1", "cash", "2026-09");
assert.deepEqual(g.calls.at(-1).body, { company: "comp_abc", key: "k1", value: "cash", period: "2026-09" });
""", stubs={"cc-pending-fixture": "export default " + fixture + ";"})

    def test_task_overview_is_read_once_and_company_groups_hold_only_chats(self):
        # 定时任务总览一次取全部（定时任务页按公司分组用）。公司组里不再有「⏱ 定时任务 N」一行：用户 2026-10-15 夜同意设计文档 4.2，
        # 改由右侧栏「定时任务」卡与公司页「定时任务」标签承担（同 Claude Code：分组里只放会话）
        self.run_page(r"""
import { refreshTaskOverview, useTaskOverview } from "@/lib/companies";
g.respond = async () => ({ ok: true, status: 200, json: async () => ({ message: {
  companies: { comp_abc: { count: 3, alert: true }, comp_xyz: { count: 1, alert: false } }, tasks: { R1: "comp_abc" }, titles: {} } }) });
await refreshTaskOverview();
assert.equal(g.calls.at(-1).method, "/api/method/pearnly_cowork.companies.api.task_overview");   // 一次取全部
assert.deepEqual(useTaskOverview.getState().companies.comp_abc, { count: 3, alert: true });
assert.equal(useTaskOverview.getState().loaded, true);
""")
        for rel in ("components/assistant-ui/projects.tsx", "components/assistant-ui/thread-list.tsx"):
            self.assertNotIn("CompanyTasks", read(rel), rel)
            self.assertNotIn("cc-project-tasks", read(rel), rel)
        self.assertNotIn(".cc-project-tasks", read("globals.css"))

    def test_the_company_page_lists_its_own_chats_from_the_server(self):
        # 主档页「对话」按这家从服务端取（分组列表 group=<公司>，分页），不靠侧栏已加载的几条
        page = read("components/company-page.tsx")
        chats = page.split("export const Conversations: FC", 1)[1].split("\n// ----", 1)[0]
        self.assertIn("await listGrouped({ group: company, after });", chats)
        self.assertIn("onClick={() => void load(next)}", chats)
        self.assertNotIn("threadItems", chats)
        self.run_page(r"""
import { listGrouped } from "@/lib/managed-agents/api";
g.respond = async () => ({ ok: true, status: 200, json: async () => ({ message: { sessions: [], nextCursor: null } }) });
await listGrouped({ group: "comp_abc", after: "x|y" });
assert.deepEqual(g.calls.at(-1).args, { grouped: "1", group: "comp_abc", after: "x|y" });
""")

    def test_new_tasks_start_from_a_company(self):
        tasks = read("components/scheduled-tasks.tsx")
        self.assertIn("openAddCompany((id) => void seedFor(id), co.chooseCompany)", tasks)
        self.assertEqual(tasks.count('onClick={() => newTask(taskCompany)}'), 2)   # 顶上「＋」与空列表的「＋」
        self.assertNotIn("useTaskCompanies", tasks)   # 不再为侧栏每家读一次详情
        self.assertIn("groupTasks(shown, overview.tasks, order, overview.titles)", tasks)
        self.assertIn("co.other", tasks)
        page = read("components/company-page.tsx")
        self.assertIn("taskSeed: taskSeedOf(detail, period, knownReaders(), app && companyFolders),", page)

    def test_branches_group_account_sets_and_saved_choices_show_where_they_came_from(self):
        self.run_page(r"""
import { formatWhen } from "@/lib/companies";
const sets = [{ connection: "c", key: "69HQ", label: "ABC 69", year_label: "69", period_start: "2025-10-01", period_end: "2026-09-30" },
  { connection: "c", key: "69B1", label: "ABC 69 B1", year_label: "69" }];
const branches = [{ branch: "00000", label: "", account_sets: [sets[0]] }, { branch: "00001", label: "Rama 9", account_sets: [sets[1]] }];
assert.deepEqual(text(html(SetsSection, { sets, branches, period: "2026-09" })),
  ["Account sets", "Head office", "ABC 69", "69", "Rama 9", "ABC 69 B1", "69"]);
// 付款方式能直接改（原生下拉）；从 Express 历史学到的只读、可删，写明来源
const pay = renderToStaticMarkup(createElement(ChoiceRow, { choice: { key: "k1", source: "express", kind: "payment", subject_label: "ACME",
  value: "credit", editable: true, year_label: "69" }, onSet() {}, onForget() {} }));
assert.ok(pay.includes("<select"));
assert.deepEqual(text(pay).slice(0, 2), ["ACME", "69"]);
const learned = renderToStaticMarkup(createElement(ChoiceRow, { choice: { key: "k2", source: "history", kind: "account",
  subject_label: "", supplier_name: "ACME", value: "5320", account_label: "5320 Utilities", count: 9, last_date: "2026-09-30" },
  onSet() {}, onForget() {} }));
assert.ok(!learned.includes("<select"));
assert.deepEqual(text(learned), ["ACME", "→ 5320 Utilities", "From Express history · 9 · Sep 30, 2026"]);
// 服务端给带时区的 UTC：按这台设备的时区显示；不带时区的不猜、原样（原先补 Z，晚 7 小时）
assert.equal(formatWhen("2026-10-15T02:00:00Z"),
  new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(Date.UTC(2026, 9, 15, 2))));
assert.equal(formatWhen("2026-10-15 09:00:00"), "2026-10-15 09:00:00");
assert.equal(formatWhen(""), "");
""")



class ShownErrorTests(unittest.TestCase):
    """页面报错只给用户看两种话（2026-10-15 验收 F7：公司页、定时任务页把「(1213, 'Deadlock found…')」原文显示成红色横幅）：
    本站拒收（frappe.throw，已按用户语言翻译的 SiteError）照原文；别的失败只说通用的一句，原文进控制台。"""

    ALLOWED = {"lib/frappe.ts", "lib/managed-agents/session-controller.ts"}  # 前者是唯一出口；后者的原文只进控制台与连接状态

    def test_no_page_shows_a_raw_error_message(self):
        found = []
        for path in sorted(SRC.rglob("*.ts*")):
            rel = path.relative_to(SRC).as_posix()
            if rel in self.ALLOWED or ".test." in rel:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"instanceof Error \? \w+\.message", line):
                    found.append(f"{rel}:{number}")
        self.assertEqual(found, [])
        self.assertIn('export function shownError', read("lib/frappe.ts"))

    def test_the_generic_words_exist_in_every_page_language(self):
        messages = block(read("i18n.ts"), "const MESSAGES = {")
        for lang in ("en", "zh", "th"):
            self.assertIn("somethingWrong", keys(block(messages, f"  {lang}: {{")), lang)

    def test_pending_groups_without_a_company_are_titled_other(self):
        # 待处理里归不上公司的那一组叫「其他」，同定时任务总览（F10）
        self.assertIn('id ? (titles[id] ?? "") : co.other', read("components/pending.tsx"))

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_site_refusals_keep_their_words_and_other_failures_say_the_generic_line(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { SiteError, shownError } from "@/lib/frappe";

const logged: unknown[] = [];
console.debug = (...args: unknown[]) => void logged.push(args);
assert.equal(shownError(new SiteError("ไม่มีสิทธิ์")), "ไม่มีสิทธิ์");
assert.equal(shownError(new Error("(1213, 'Deadlock found when trying to get lock; try restarting transaction')")), "出了点问题，请重试。");
assert.equal(shownError(new TypeError("Failed to fetch")), "出了点问题，请重试。");
assert.equal(shownError("boom"), "出了点问题，请重试。");
assert.equal(shownError(new SiteError("")), "出了点问题，请重试。");
assert.equal(logged.length, 4);
console.log("HARNESS OK");
""", lang="zh")


class CompanyPageLiveTests(unittest.TestCase):
    def test_the_company_page_rechecks_like_the_other_lists(self):
        # 验收 F8：另一台设备「更换…」文件夹后，这页错过那次推送（后台标签页、实时通道断过）就一直停在旧路径。
        # 同待处理、定时任务页：电脑同步文件夹、回到前台、拿回焦点、重连时重取，另有兜底定时
        page = read("components/company-page.tsx")
        self.assertIn("useLiveRefresh(reload, { events: TASK_EVENTS })", page)
        self.assertIn('export const TASK_EVENTS = ["cowork_folder_changed"]', read("lib/companies.ts"))
        live = read("lib/live.ts")
        for hook in ("onConnectionChange", "visibilitychange", "\"focus\""):
            self.assertIn(hook, live)


class PendingChoiceDefaultTests(unittest.TestCase):
    """待处理「当场选」不拿列表第一项充数（验收 F18：没有建议时预选了科目表第一项「11-01-01-00 เงินสด」，直接确认会记错科目）。"""

    def test_the_confirm_needs_every_choice_made(self):
        page = read("components/pending.tsx")
        self.assertNotIn("optionsOf(next, need)[0]", page)
        self.assertIn("initialChoice(next, need)", page)
        self.assertIn('<option value="" disabled>—</option>', page)
        self.assertIn("view.needed.some((need) => !values[choiceKey(need)])", page)

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_only_a_decided_or_suggested_value_is_filled_in(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { initialChoice } from "@/components/pending";

const view = { options: { accounts: [
  { code: "11-01-01-00", name: "เงินสด" }, { code: "53-05-02-00", name: "ค่าทำความสะอาด" }, { code: "53-05-28-00", name: "ค่าใช้จ่ายเบ็ดเตล็ด" },
] } } as any;
const need = (extra: any) => ({ kind: "account", subject: "S", ...extra }) as any;
assert.equal(initialChoice(view, need({})), "");                                        // 没有建议：空着，必须人选
assert.equal(initialChoice(view, need({ suggested: [{ code: "53-05-28-00" }] })), "53-05-28-00");
assert.equal(initialChoice(view, need({ suggested: [{ code: "99-99-99-99" }] })), "");   // 建议不在可选值里：不填
assert.equal(initialChoice(view, need({ current: "53-05-02-00", suggested: [{ code: "53-05-28-00" }] })), "53-05-02-00");
const payment = { kind: "payment", subject: "S", options: [{ code: "cash" }, { code: "credit" }] } as any;
assert.equal(initialChoice({ options: {} } as any, payment), "");                       // 付款方式同样不默认现购
(globalThis as any).done();
""", stubs={"socket.io-client": SOCKET_STUB,
            "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;'})

if __name__ == "__main__":
    unittest.main()
