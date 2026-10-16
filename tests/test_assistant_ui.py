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
                             "payCash", "payCredit", "vatClaim", "vatExpense", "pending", "handToCC", "handToCCText", "handToCCOne",
                             "fromHistory", "history", "vatCheck"} <= sets[lang], lang)
            self.assertIn("earlierFolders:", block(settings, f"  {lang}: {{"), lang)
        self.assertEqual(sets["en"], sets["zh"])
        self.assertEqual(sets["en"], sets["th"])
        # 工具步骤（含跑完的按类摘要）：三种语言的词条一样多
        steps = block(i18n, "const STEPS = {")
        step_sets = {lang: keys(block(steps, f"  {lang}: {{")) | keys(block(block(steps, f"  {lang}: {{"), "summary: {"))
                     for lang in ("en", "zh", "th")}
        self.assertTrue({"failed", "running", "summary", "command", "read", "edit", "search", "fetch", "tool", "times", "join"}
                        <= step_sets["en"])
        self.assertNotIn("steps", step_sets["en"])  # 「N 个步骤」已换成按类摘要（用户 2026-10-15 批）
        self.assertEqual(step_sets["en"], step_sets["zh"])
        self.assertEqual(step_sets["en"], step_sets["th"])
        # 用户可见处叫「CC 桌面版」，不叫小助手 / Companion
        self.assertNotRegex(company, r"小助手|Companion")
        # 侧栏的分组和排序、批量项、组标题菜单（CC 外壳设计 3.4–3.7）：三种语言一样多
        sidebar = block(i18n, "const SIDEBAR = {")
        words = {lang: keys(block(sidebar, f"  {lang}: {{")) for lang in ("en", "zh", "th")}
        self.assertTrue({"groupAndSort", "groupBy", "none", "sortBy", "lastUpdated", "dateCreated", "name", "descending", "ascending",
                         "show", "metadata", "emptyGroups", "resetDefaults", "noMonth", "pinN", "unpinN", "moveToN",
                         "deleteN"} <= words["en"])
        self.assertEqual(words["en"], words["zh"])
        self.assertEqual(words["en"], words["th"])
        self.assertNotIn("newChatHere", i18n)   # 公司组「···」里的「在这家新建对话」去掉了（用户 2026-10-15 夜），词条一并删
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
                if name == "components/shortcut.tsx":
                    # 只有苹果设备的换字表（第 7 轮：Mac 上 Ctrl 显示成 ⌘），这里不写提示
                    self.assertEqual(line, 'const APPLE: Record<string, string> = { Ctrl: "⌘", Alt: "⌥" };', number)
                    continue
                self.assertRegex(line, r'<Shortcut keys="[^"]+"( kbd)? />', f"{name}:{number} 快捷键提示要经 <Shortcut>")
                hints.append(re.search(r'keys="([^"]+)"', line).group(1))
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

    def test_hover_scroll_waits_measures_and_snaps_back(self):
        # 悬停约 150ms 后量：超出多少（加上原有右端渐隐）就滚多少，每秒 40px、至少 0.3s；离开立即回到开头；
        # 放得下的不滚；减少动态效果、触屏、按着键拖进来、行里开着输入框都不滚。
        # 只有键盘操作带来的焦点才滚（鼠标关菜单后焦点被还回来不算，立即回到开头）；滚动中文字变了回到开头重新量。
        # 用户 10-16「统一渐隐」：放不放得下与静止时的末尾渐隐同一个量法（lib/overflow-fade.ts）——只有真的放不下才渐隐、才滚，
        # 比框窄（哪怕不到一个渐隐宽度）的放得下，末尾不淡、也不滚（第 5 轮「渐隐里的也滚」随之不再需要）
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { attachHoverScroll, canHoverScroll } from "@/lib/hover-scroll";

const g = globalThis as any;
let reduce = false;
let touch = false;
g.matchMedia = (q: string) => ({ matches: q.includes("reduced-motion") ? reduce : q.includes("hover: none") ? touch : false });
const vars: Record<string, string> = { "--cc-marquee-delay": "150ms", "--cc-marquee-speed": "40", "--cc-marquee-min": "300ms" };
g.getComputedStyle = (el: any) => ({ getPropertyValue: (n: string) => (el === g.document.documentElement ? vars[n] ?? "" : el.vars?.[n] ?? "") });
g.document.querySelector = () => null;
// 整页的输入方式（键盘 / 指针）与滚动中观察文字变化的桩
const win: Record<string, (e: any) => void> = {};
g.addEventListener = (t: string, f: any) => (win[t] = f);
const observers: Array<{ run: () => void; on: boolean }> = [];
g.MutationObserver = class {
  entry: { run: () => void; on: boolean };
  constructor(run: () => void) { this.entry = { run, on: false }; observers.push(this.entry); }
  observe() { this.entry.on = true; }
  disconnect() { this.entry.on = false; }
};
function row(scroll: number, client: number, end = "") {
  const handlers: Record<string, (e: any) => void> = {};
  const host: any = {
    addEventListener: (t: string, f: any) => (handlers[t] = f), removeEventListener: (t: string) => delete handlers[t],
    querySelector: () => (host.editing ? {} : null), contains: () => false,
  };
  const attrs = new Map<string, string>();
  const style = new Map<string, string>();
  const box: any = {
    isConnected: true, getBoundingClientRect: () => ({ width: box.clientWidth }),
    scrollWidth: scroll, clientWidth: client, offsetWidth: client, vars: { "--cc-marquee-end": end },
    text: scroll,   // 里面那层文字的宽（没有内边距时 = scrollWidth）
    querySelector: () => ({ getBoundingClientRect: () => ({ width: box.text }) }),
    closest: (sel: string) => (sel === "[data-marquee-host]" ? host : null),
    hasAttribute: (n: string) => attrs.has(n), setAttribute: (n: string, v: string) => attrs.set(n, v), removeAttribute: (n: string) => attrs.delete(n),
    style: { setProperty: (n: string, v: string) => style.set(n, v), removeProperty: (n: string) => style.delete(n) },
  };
  return { host, box, handlers, attrs, style, detach: attachHoverScroll(box) };
}
const mouse = { pointerType: "mouse", buttons: 0 };

// 超出 100px、右端原有渐隐 20px：150ms 后开始，滚 120px，用 3s
let r = row(300, 200, "20px");
r.handlers.pointerenter!(mouse);
await g.sleep(60);
assert.equal(r.attrs.has("data-marquee"), false);           // 还没到 150ms
await g.sleep(140);
assert.equal(r.attrs.get("data-marquee"), "run");
assert.equal(r.style.get("--cc-marquee-x"), "-120px");
assert.equal(r.style.get("--cc-marquee-dur"), "3000ms");
r.handlers.pointerleave!({});                                // 离开：立即回到开头
assert.equal(r.attrs.has("data-marquee"), false);
assert.equal(r.style.has("--cc-marquee-x"), false);
r.detach();

// 只超出 4px：最短 0.3s
r = row(204, 200);
r.handlers.pointerenter!(mouse);
await g.sleep(200);
assert.equal(r.style.get("--cc-marquee-dur"), "300ms");
r.detach();

// 比框窄 10px、右端渐隐 20px：放得下（静止时末尾不淡），不滚
r = row(190, 200, "20px");
r.box.scrollWidth = 200;
r.handlers.pointerenter!(mouse);
await g.sleep(200);
assert.equal(r.attrs.has("data-marquee"), false);
r.detach();
// 刚好等宽、只多出 0.4px（取整误差）：放得下，不滚
r = row(200.4, 200, "20px");
r.handlers.pointerenter!(mouse);
await g.sleep(200);
assert.equal(r.attrs.has("data-marquee"), false);
r.detach();
// 只多出 4px、右端渐隐 20px：滚到最后一个字越过渐隐（滚 24px，最短 0.3s）
r = row(204, 200, "20px");
r.handlers.pointerenter!(mouse);
await g.sleep(200);
assert.equal(r.attrs.get("data-marquee"), "run");
assert.equal(r.style.get("--cc-marquee-x"), "-24px");
assert.equal(r.style.get("--cc-marquee-dur"), "600ms");
// 滚动中文字变了：回到开头，还悬停着就重新量（变长了滚得更远）
const watching = observers.filter((o) => o.on);
assert.equal(watching.length, 1);
r.box.text = 300;
watching[0]!.run();
assert.equal(r.attrs.has("data-marquee"), false);
await g.sleep(200);
assert.equal(r.style.get("--cc-marquee-x"), "-120px");
r.handlers.pointerleave!({});
assert.equal(observers.some((o) => o.on), false);          // 不滚了就不再观察
r.detach();

// 放得下的不滚（离渐隐还远）；还没到时间就离开的也不滚
r = row(150, 200, "20px");
r.box.scrollWidth = 200;
r.handlers.pointerenter!(mouse);
await g.sleep(200);
assert.equal(r.attrs.has("data-marquee"), false);
r.detach();
r = row(200, 200);
r.handlers.pointerenter!(mouse);
await g.sleep(200);
assert.equal(r.attrs.has("data-marquee"), false);
r.detach();
r = row(400, 200);
r.handlers.pointerenter!(mouse);
r.handlers.pointerleave!({});
await g.sleep(200);
assert.equal(r.attrs.has("data-marquee"), false);
r.detach();

// 触屏点按、按着键拖进来、行里开着输入框（就地改名）：不滚；按下就回到开头
for (const event of [{ pointerType: "touch", buttons: 0 }, { pointerType: "mouse", buttons: 1 }]) {
  r = row(400, 200);
  r.handlers.pointerenter!(event);
  await g.sleep(200);
  assert.equal(r.attrs.has("data-marquee"), false);
  r.detach();
}
r = row(400, 200);
r.host.editing = true;
r.handlers.pointerenter!(mouse);
await g.sleep(200);
assert.equal(r.attrs.has("data-marquee"), false);
r.detach();
r = row(400, 200);
r.handlers.pointerenter!(mouse);
await g.sleep(200);
r.handlers.pointerdown!({});
assert.equal(r.attrs.has("data-marquee"), false);
r.detach();

// 键盘焦点落到这一行也滚（鼠标点进去的焦点不算）
const visible = { target: { matches: (q: string) => q === ":focus-visible" } };
r = row(400, 200);
win.keydown!({ key: "Tab" });
r.handlers.focusin!({ target: { matches: () => false } });
await g.sleep(200);
assert.equal(r.attrs.has("data-marquee"), false);
// 上一次输入是指针（鼠标关掉菜单，焦点被程序还给「···」，即使带 :focus-visible）：不滚
win.pointerdown!({});
r.handlers.focusin!(visible);
await g.sleep(200);
assert.equal(r.attrs.has("data-marquee"), false);
r.detach();
// 悬停滚着、点「···」开菜单（按下回到开头）、鼠标关菜单焦点还回来、鼠标已不在这一行：停在开头
r = row(400, 200);
r.handlers.pointerenter!(mouse);
await g.sleep(200);
assert.equal(r.attrs.get("data-marquee"), "run");
win.pointerdown!({});
r.handlers.pointerdown!({});
r.handlers.pointerleave!({});
r.handlers.focusin!(visible);
await g.sleep(200);
assert.equal(r.attrs.has("data-marquee"), false);
r.detach();
// 真正的键盘操作（Tab、方向键）带来的焦点：滚
r = row(400, 200);
win.keydown!({ key: "ArrowDown" });
r.handlers.focusin!(visible);
await g.sleep(200);
assert.equal(r.attrs.get("data-marquee"), "run");
r.handlers.focusout!({ relatedTarget: null });
assert.equal(r.attrs.has("data-marquee"), false);
r.detach();

// 系统减少动态效果、触屏设备：一律不滚
assert.equal(canHoverScroll(), true);
reduce = true;
assert.equal(canHoverScroll(), false);
r = row(400, 200);
r.handlers.pointerenter!(mouse);
await g.sleep(200);
assert.equal(r.attrs.has("data-marquee"), false);
r.detach();
reduce = false;
touch = true;
assert.equal(canHoverScroll(), false);
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
// 苹果设备接了键盘：按 ⌘ / ⌥ 显示（第 7 轮）
assert.equal(html(Shortcut, { keys: "Ctrl ," }), '<span class="text-ink-3 text-xs">⌘ ,</span>');
assert.match(html(Shortcut, { keys: "Ctrl + Alt + Space", kbd: true }), /^<kbd class="[^"]*">⌘ \+ ⌥ \+ Space<\/kbd>$/);

g.device.touch = true;
g.qt = { webChannelTransport: {} };
assert.equal(showShortcuts(), true);
assert.equal(html(Shortcut, { keys: "Ctrl N" }), '<span class="text-ink-3 text-xs">⌘ N</span>');
""", lang="th")

    def test_shortcuts_use_command_on_mac_and_ctrl_elsewhere(self):
        # 测试窗口第 7 轮：Mac 上用户菜单的「设置」写成「Ctrl ,」，应为「⌘ ,」；Windows / Linux 照旧 Ctrl
        self.on_device("windows-chrome", r"""
import { appleKeys, keysFor } from "@/components/shortcut";
assert.equal(appleKeys(), false);
assert.equal(html(Shortcut, { keys: "Ctrl ," }), '<span class="text-ink-3 text-xs">Ctrl ,</span>');
assert.match(html(Shortcut, { keys: "Ctrl + Alt + Space", kbd: true }), />Ctrl \+ Alt \+ Space<\/kbd>$/);
assert.equal(keysFor("Ctrl R", true), "⌘ R");
assert.equal(keysFor("Ctrl + Alt + Space", true), "⌘ + ⌥ + Space");
assert.equal(keysFor("Ctrl =", false), "Ctrl =");
(navigator as any).userAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36";
assert.equal(appleKeys(), true);
assert.equal(html(Shortcut, { keys: "Ctrl ," }), '<span class="text-ink-3 text-xs">⌘ ,</span>');
(navigator as any).userAgentData = { platform: "Linux" };
assert.equal(appleKeys(), false);
assert.equal(html(Shortcut, { keys: "Ctrl ," }), '<span class="text-ink-3 text-xs">Ctrl ,</span>');
""")

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

    def test_pdf_pages_are_server_drawn_images_where_pdf_cannot_be_embedded(self):
        # 第 5 轮测试 2：安卓 Chrome 不能 iframe 显示 PDF（只有图标、内部地址和「打开」）：不能内嵌的设备按页取服务端画的图，
        # 看得见才取（lazy），第一页画不出给下载；电脑上原生查看器照用
        self.run_page(r"""
import { nativePdf, PdfPages, PDF_PAGES_SHOWN } from "@/components/results-panel";
import { pageUrl } from "@/lib/managed-agents/api";
const pdf = { ...file, id: "file_P", filename: "ใบกำกับ.pdf", pages: 3 };
assert.equal(pageUrl("s1", "file_P", 2, "page"), "/api/method/pearnly_core.assistant.api.page_image?session_id=s1&file_id=file_P&page=2&size=page");
const markup = html(PdfPages, { sessionId: "s1", file: pdf });
assert.equal((markup.match(/<img class="cc-pdf-page"/g) ?? []).length, 3);
assert.match(markup, /^<div class="cc-pdf-pages" data-slot="cc-pdf-pages"><img class="cc-pdf-page" src="\/api\/method\/pearnly_core\.assistant\.api\.page_image\?session_id=s1&amp;file_id=file_P&amp;page=1&amp;size=page" alt="ใบกำกับ\.pdf · 1\/3" loading="lazy" decoding="async" draggable="false"\/>/);
assert.doesNotMatch(markup, /pearnly_core\.assistant\.api\.download|iframe/);
// 页数很多：列前 PDF_PAGES_SHOWN 页，其余给下载
const many = html(PdfPages, { sessionId: "s1", file: { ...pdf, pages: PDF_PAGES_SHOWN + 5 } });
assert.equal((many.match(/<img /g) ?? []).length, PDF_PAGES_SHOWN);
assert.match(many, /href="\/api\/method\/pearnly_core\.assistant\.api\.download\?session_id=s1&amp;file_id=file_P&amp;inline=0"/);
// 列表里没有页数（结果文件）：先转圈，问服务端（preview 给 {type: "pdf", pages}）
assert.doesNotMatch(html(PdfPages, { sessionId: "s1", file: { ...pdf, pages: undefined } }), /<img/);
g.respond = async () => ({ ok: true, status: 200, json: async () => ({ message: { type: "pdf", pages: 2 } }) });
assert.deepEqual(await loadPreview("s1", "file_P"), { type: "pdf", pages: 2 });
// 浏览器自带 PDF 查看器（pdfViewerEnabled）、能悬停的设备：原生查看器；安卓 Chrome（pdfViewerEnabled 为假）按页看图
Object.defineProperty(globalThis.navigator, "pdfViewerEnabled", { value: false, configurable: true });
assert.equal(nativePdf(), false);
Object.defineProperty(globalThis.navigator, "pdfViewerEnabled", { value: true, configurable: true });
g.matchMedia = (query: string) => ({ matches: query === "(hover: none)", addEventListener() {}, removeEventListener() {} });
if (g.window) g.window.matchMedia = g.matchMedia;
assert.equal(nativePdf(), false);   // 触屏（iPhone / iPad 的 Safari 在 iframe 里只显示第一页）
g.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
if (g.window) g.window.matchMedia = g.matchMedia;
assert.equal(nativePdf(), true);
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
        item = thread_list.split("\nconst Row: FC", 1)[1].split("\n};\n", 1)[0]   # 置顶行、可拖到别的公司的行、不能拖的行都是这一份（ThreadListItem 按所在的组选）
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
                   "components/companies.tsx", "components/company-page.tsx", "components/company-cards.tsx",
                   "components/session-panel.tsx", "lib/company-panel.ts", "components/add-company.tsx",
                   "components/pending.tsx", "components/period-chip.tsx", "components/assistant-ui/projects.tsx",
                   "hooks/use-long-press.ts", "components/month-ledger.tsx", "lib/month.ts")


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
        page = read("components/company-cards.tsx")  # 卡片内容（会话栏与公司页共用，CC 外壳 2026-10-16）
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
        # 唯一的行内样式是拖动时 dnd-kit 算出的位移，只在共用拖动里（components/ui/drag.tsx），各处展开它给的属性
        for name in COMPANY_SOURCES:
            self.assertIsNone(colour.search(read(name)), name)
        drag = read("components/ui/drag.tsx")
        self.assertIn("style: { transform: CSS.Translate.toString(sortable.transform), transition: sortable.transition } as CSSProperties,", drag)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", drag))
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

    def test_the_composer_chin_is_a_generic_slot_filled_by_the_product(self):
        # 设计文档 3.12 / 4.3：公司、月份、分店放在输入框下巴。thread.tsx 只有通用的 Chin 插槽（不知道公司），
        # 内容由 assistant.tsx 接线的 ComposerChin 给；问候里不再有公司条；ERP / DMS（没有公司分组）不出现
        page = read("assistant.tsx")
        self.assertNotIn("ComposerCompany", page)
        self.assertIn("<Thread components={{ Welcome, Loading: ThreadLoading, ToolFallback: ManagedAgentsToolFallback, Chin: ComposerChin }} />",
                      page)
        self.assertNotIn("ComposerChin", block(page, "function Welcome()"))
        thread = read("components/assistant-ui/thread.tsx")
        for word in ("ComposerCompany", "ComposerChin", "projects", "useProjects", "co."):
            self.assertNotIn(word, thread, word)
        self.assertIn("  Chin?: ComponentType | undefined;", thread)
        self.assertIn('      {Chin && (\n        <div className="cc-composer-chin" data-slot="cc-composer-chin">\n          <Chin />\n'
                      '        </div>\n      )}', thread)
        projects = read("components/assistant-ui/projects.tsx")
        self.assertNotIn("ComposerCompany", projects)
        chin = block(projects, "export const ComposerChin: FC = () => {")
        self.assertIn("if (!kind) return null;", chin)
        self.assertIn("return fresh ? <DraftChin /> : <SessionChin />;", chin)
        # 新对话：「公司 ▾」选公司（至少有一家公司才出现），菜单向上开；月份、分店是草稿
        draft = block(projects, "const DraftChin: FC = () => {")
        self.assertIn("if (!(sidebarHasOne || any || draft.project)) return null;", draft)
        self.assertIn('<DropdownMenu.Content side="top" align="start" sideOffset={6} className="cc-menu !min-w-52 max-w-72">', draft)
        self.assertIn('<PeriodChip look="cc-chin-btn" side="top" value={draft.period} onChange={(period) => setDraft({ ...draft, period })} />',
                      draft)
        # 已有对话：公司名打开右侧会话栏并展开公司卡（与顶栏「公司名 ›」同一个 openCompanyCard），月份、分店改了就 move；
        # 月份、分店只在下巴：标题栏不再有 ConversationFiling
        session = block(projects, "const SessionChin: FC = () => {")
        self.assertIn('<button type="button" className="cc-chin-btn" data-slot="cc-chin-company" disabled={!remoteId} onClick={openCompanyCard}>', session)
        self.assertIn('<FilingChips look="cc-chin-btn" side="top" />', session)
        self.assertIn('import { openCompanyCard } from "@/lib/company-panel";', projects)
        self.assertIn('export const openCompanyCard = () => showCard("company");', read("lib/company-panel.ts"))
        self.assertNotIn("cc:open-company-card", projects)
        self.assertNotIn("ConversationFiling", projects)
        self.assertNotIn("ConversationFiling", page)
        self.assertIn("const change = (next: Partial<Filing>) => void fileSession(remoteId, { ...filing, ...next }).catch(report);",
                      projects.split("const FilingChips: FC<", 1)[1].split("\n};", 1)[0])
        # 手机胶囊菜单只留「移到…」：月份在下巴里
        mobile = block(projects, "export const MobileFilingItems: FC = () => {")
        self.assertIn("return <MoveToMenu sessionId={remoteId} localId={localId} />;", mobile)
        self.assertNotIn("co.period", mobile)
        css = read("globals.css")
        self.assertIn("  .cc-composer-chin:empty { display: none; }", css)
        self.assertIn(".cc-mshell .cc-composer-chin { min-height: var(--cc-chin-h-phone); }", css)
        chin_css = css.split("\n  .cc-composer-chin {", 1)[1].split("}", 1)[0]
        for part in ("min-height: var(--cc-chin-h);", "margin-inline: calc(var(--cc-chin-out) * -1);", "padding: var(--cc-chin-pad);"):
            self.assertIn(part, chin_css)
        button = css.split("\n  .cc-chin-btn {", 1)[1].split("}", 1)[0]
        for part in ("height: var(--cc-chin-btn);",
                     "border-radius: var(--cc-radius-chin); color: var(--cc-ink-2); font-size: var(--cc-chin-font);"):
            self.assertIn(part, button)
        # 输入框（设计文档 3.12，电脑）：一行里「＋」、输入、发送，单行高 44、内边距 8、按钮 28；圆角 14
        self.assertIn('className="cc-composer cc-composer-inline"', thread)
        self.assertIn("--cc-radius-composer: var(--code-radius-input);", css)
        self.assertIn('grid-template-areas: "files files files" "add input send";', css)
        # 内边距扣掉细线边框：border-box 下单行正好 44，不是 45
        self.assertIn("align-items: end; gap: 0; min-height: var(--cc-composer-h); "
                      "padding: calc(var(--cc-composer-pad) - var(--cc-hairline));", css)
        # 新对话里选了公司，下巴里的公司名用正文色，和「公司」占位分开
        self.assertIn(".cc-chin-btn[data-chosen] { color: var(--cc-ink); }", css)
        self.assertIn(":root:not([data-cc-variant=\"phone\"]) .cc-composer-inline :is(.cc-attach, .cc-send) "
                      "{ width: var(--cc-composer-btn); height: var(--cc-composer-btn); }", css)
        # 公司页的输入框同一套格子：「＋」要带 aui-composer-add-attachment 才落在 add 格，和光标、发送同一行（用户 10-16 截图：「＋」跑到上一行）
        company_page = read("components/company-page.tsx")
        self.assertIn('className="cc-composer cc-composer-inline"', company_page)
        self.assertIn('className="aui-composer-add-attachment cc-attach"', company_page)
        self.assertIn('className="aui-composer-send-slot', company_page)

    def test_hand_to_cc_and_drafts_prefill_without_sending(self):
        pending = read("components/pending.tsx")
        self.assertIn("handText(items), false);", pending)
        self.assertIn("co.draftText(d.kind, period ? monthLabel(period) : d.period), false);", read("components/company-cards.tsx"))
        page = read("components/company-page.tsx")
        # 主档页输入框是用户自己打的字：发出
        self.assertIn('const filing = () => ({ project: company, period: withPeriod ? period : "", scope: withScope ? scope : "" });', page)
        self.assertIn("start(filing(), value, true);", page)
        # 审查第 4 轮：Safari 选字的回车（keyCode 229）不发送；「＋」选的文件带进这家的新对话输入框（不替用户发出）
        self.assertIn('if (e.key === "Enter" && !e.shiftKey && !isImeEnter(e)) {', page)
        self.assertIn("start(filing(), value, false, picked);", page)
        self.assertNotIn("!e.nativeEvent.isComposing) {\n              e.preventDefault();\n              send();", page)
        projects = read("components/assistant-ui/projects.tsx")
        self.assertIn("for (const file of files) void composer.addAttachment(file).catch(() => undefined);", projects)
        self.assertIn("if (send && !files.length) composer.send();", projects)
        # 让 CC 起草的月份 = 公司页的月份（同输入框、「月结」卡）
        self.assertIn('start({ project: company, period: pageMonth, scope: "" }, co.draftNotesText, false)', read("components/company-cards.tsx"))

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
// 单号 · 对方 · 金额 · 日期，几张时每张一行；不写内部记录名（测试窗口第 3 轮）
assert.equal(handText(open), "Handle these: \nINV-1 · 2026-08-30\nShop · 2026-09-01\nINV-3 · 2026-09-02");
assert.equal(handText([{ ...open[0], counterparty: "Makro", amount: "1070.5" }]), "Handle this one: INV-1 · Makro · 1,070.50 · 2026-08-30");
assert.ok(!/OCR-\d/.test(handText(open)));
// 四项都没有时写文件名（复审第 3 轮），不丢这张、也不写记录名
assert.equal(handText([...open, { ...item, document: "OCR-4", date: "", filename: "scan.pdf" }]).split("\n").length, 5);
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
        pending = app.split("function PendingNav(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('data-active={(view === "pending" && !pendingCompany) || undefined}', pending)
        self.assertIn("if (!kind || total <= 0) return null;", pending)
        company = app.split("function CompanyNav(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('data-active={view === "companies" || undefined}', company)   # 主档页不再把「公司」选中
        self.assertNotIn('view === "company" ||', company)
        # 顺序（设计文档 4.2）：新建、待处理 N、定时任务、公司；手机抽屉同序
        order = [nav.index(x) for x in ("<ThreadListNew ", "<PendingNav />", "hasScheduledTasks(profile)", "<CompanyNav />")]
        self.assertEqual(order, sorted(order))
        drawer = app.split('<div className="cc-drawer ', 1)[1].split("<ThreadList ", 1)[0]
        order = [drawer.index(x) for x in ("<PendingNav onPick={close} />", "hasScheduledTasks(profile)", "<CompanyNav onPick={close} />")]
        self.assertEqual(order, sorted(order))
        # 带修饰键点对话是多选（只有电脑上的 Cowork）：不切回对话页；ERP / DMS、手机照常切回
        self.assertIn("const multi = !!useProjects((s) => s.kind) && !isMobile;", app.split("function useBackToChatOnPick(", 1)[1].split("\n}\n", 1)[0])
        self.assertIn("if (multi && (e.shiftKey || e.metaKey || e.ctrlKey)) return;", app.split("function useBackToChatOnPick(", 1)[1].split("\n}\n", 1)[0])

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
        self.assertIn("onClick={() => go(() => openPending(project.id))}", head)   # 待处理数：只看这家
        # 右侧：待处理数、···（分组和排序）、新建（新建在最右，悬停才出）
        order = [head.index(x) for x in ('className="cc-group-count"', "<GroupSortMenu open={menuOpen} onOpenChange={setMenuOpen} alignHead={isMobile} />", "<GroupNew ")]
        self.assertEqual(order, sorted(order))
        css = read("globals.css")
        self.assertIn(".cc-group-more, .cc-group-new { display: none; }", css)
        # 「···」菜单开着时（指针、焦点都在菜单里）「＋」照样露着、「···」不挪位（第 5 轮视觉审查：最近组打开「···」后「＋」消失）
        self.assertIn('.cc-group-row:is(:hover, :focus-within, [data-open], :has(.cc-group-more[data-state="open"])) :is(.cc-group-more, .cc-group-new)', css)
        self.assertIn('[data-slot="sidebar-inner"]:hover .cc-group-caret', css)
        self.assertIn(".cc-group-row:hover .cc-group-toggle, .cc-group-toggle:focus-visible { color: var(--cc-ink-2); }", css)
        # 触屏平板（宽屏、不能悬停）与手机：箭头、组操作常显
        self.assertIn('@media (hover: none) {\n  [data-slot="sidebar"] { --cc-reveal-rest: 1; }\n'
                      '  [data-slot="sidebar"] :is(.cc-group-more, .cc-group-new) { display: inline-flex; }', css)
        self.assertIn(':root[data-cc-variant="phone"] :is(.cc-group-more, .cc-group-new) { display: inline-flex; }', css)
        self.assertNotIn(".cc-project-name", css + projects)

    def test_company_headers_follow_the_user_sidebar_of_2026_10_15(self):
        # 用户 2026-10-15 夜（参考 DeepSeek 桌面端侧栏）：「只改我提到的地方 其它不要动」
        group = read("components/assistant-ui/sidebar-group.tsx")
        projects = read("components/assistant-ui/projects.tsx")
        rows = read("components/assistant-ui/thread-list.tsx")
        css = read("globals.css")
        head = projects.split("export const ProjectHeader: FC", 1)[1].split("\n};\n", 1)[0]
        header = group.split("export const GroupHeader: FC", 1)[1].split("\n);\n", 1)[0]
        # A：公司组头最左是公司图标（同原来「打开公司」那个 Building2Icon），然后公司名；名字同会话行标题的字号字色，
        #    这家的对话行缩进到公司名起点；已置顶 / 最近 / 月份不带图标、不缩进
        self.assertIn('icon={<Building2Icon className="cc-group-icon" aria-hidden />}', head)
        self.assertLess(header.index("{icon}"), header.index('<Marquee className="cc-group-name">{label}</Marquee>'))
        self.assertLess(header.index("cc-group-name"), header.index("cc-group-caret"))
        self.assertIn(".cc-group-icon { flex-shrink: 0; width: var(--cc-icon-sm); height: var(--cc-icon-sm); margin-inline-end: var(--cc-row-gap); color: var(--cc-ink-3); }", css)
        self.assertIn(".cc-project-row .cc-group-toggle { color: var(--cc-ink-2); font-size: var(--cc-row-font); line-height: var(--cc-row-lh); font-weight: normal; }", css)
        self.assertIn('[data-slot="cc-project-items"] { --cc-row-indent: calc(var(--cc-icon-sm) + 0.25rem + var(--cc-row-gap)); }', css)
        self.assertIn(".cc-row-trigger { gap: 0.25rem; padding-inline: calc(var(--cc-row-pad) + var(--cc-row-indent, 0px)) 0.25rem; }", css)
        self.assertIn('.cc-drawer [data-slot="aui_thread-list-item-trigger"] { padding-inline-start: calc(0.75rem + var(--cc-row-indent, 0px)); }', css)
        self.assertIn("margin-inline: calc(var(--cc-row-pad) + var(--cc-row-indent, 0px) - var(--cc-inline-edit-px)) 0.25rem;", css)
        # 「显示更多」也缩进到公司名文字起点（用户 10-16 看预览后定）
        self.assertIn("padding-inline: calc(var(--cc-row-pad) + var(--cc-row-indent, 0px)) var(--cc-row-pad);", css)
        self.assertIn('.cc-drawer [data-slot="cc-project-items"] .cc-show-more { padding-inline-start: calc(0.75rem + var(--cc-row-indent, 0px)); }', css)
        self.assertEqual(rows.count('data-slot="cc-project-items"'), 1)   # 只有公司组里的行缩进
        self.assertNotIn("icon=", rows.split("const CompanyGroups: FC", 1)[1].split("\nconst DayGroups", 1)[0])
        # B：组名、会话行标题与元数据行放不下时悬停滚动（共用的 Marquee）；行与组标题是触发的「这一行」
        self.assertIn('<Marquee data-slot="aui_thread-list-item-title" className="cc-row-title">', rows)
        self.assertIn('<Marquee className="cc-row-meta" data-slot="cc-row-meta">', rows)
        self.assertIn('data-slot="aui_thread-list-item"\n      data-marquee-host=""', rows)
        self.assertIn('<div data-slot="cc-group" data-marquee-host=""', header)
        # C：公司组「···」不再有打开公司、在这家新建对话，换成分组和排序；长按组头、菜单键打开的也是它
        self.assertNotIn("openCompany", head)
        self.assertNotIn("newChatHere", head + group)
        self.assertNotIn("DropdownMenu.Item", head)
        self.assertIn("<GroupSortMenu open={menuOpen} onOpenChange={setMenuOpen} alignHead={isMobile} />", head)
        # 长按出菜单；拿起后手指动了（真的在拖）就收起菜单（第 6 轮第 2 项）
        self.assertIn("const press = useLongPress(() => setMenuOpen(true), () => setMenuOpen(false));", head)
        self.assertIn("onMenuKey={() => setMenuOpen(true)}", head)
        menu = group.split("export const GroupSortMenu: FC", 1)[1]
        self.assertIn('className="cc-row-btn cc-group-more" aria-label={t("more")} title={t("more")}', menu)
        self.assertIn("<MoreHorizontalIcon />", menu)
        order = [menu.index(x) for x in ("sb.groupBy", "sb.sortBy", "sb.show", "{sb.resetDefaults}")]
        self.assertEqual(order, sorted(order))
        # 恢复默认设置：跟默认一样、也没手排过公司组 / 置顶（第 5 轮）才不可点
        self.assertIn("disabled={isDefaultView(view) && !manual}", menu)
        # D：「＋」照旧：在这家开新对话（月份 = 上个月）
        self.assertIn("<GroupNew onClick={() => newChatIn(project.id)} />", head)
        self.assertIn('start(project ? { project, period: period ? lastMonth() : "" } : NO_FILING);', projects)
        # E：漏斗按钮（分组和排序）从所有组标题上去掉；已置顶 / 最近 / 月份组标题右侧是同一个「···」，「最近」的「＋」保留
        for source in (group, projects, rows):
            self.assertNotIn("ListFilterIcon", source)
            self.assertNotIn("cc-group-sort\"", source)
        others = rows.split("const CompanyGroups: FC", 1)[1].split("\nconst DayGroups", 1)[0]
        # 已置顶 / 最近 / 月份组标题：受控的「···」，菜单键也打开它（第 5 轮视觉审查），「最近」另有「＋」
        section_header = others.split("const SectionHeader: FC", 1)[1].split("\n};\n", 1)[0]
        self.assertIn('<GroupHeader label={label} open={open} onToggle={onToggle} data-open={menuOpen || undefined} onMenuKey={() => setMenuOpen(true)}>\n'
                      '      <GroupSortMenu open={menuOpen} onOpenChange={setMenuOpen} />\n'
                      '      {onNew && <GroupNew onClick={onNew} />}', section_header)
        self.assertEqual(others.count('onNew={() => newChatIn("")}'), 2)   # 最近（按公司，拖动中临时的「最近」同一处 / 不分组）
        self.assertNotIn("<GroupSortMenu />", others)
        # 公司页仍有路：会话栏公司卡的名称（顶栏「公司名 ›」、手机胶囊菜单打开会话栏），公司列表页的卡片
        self.assertIn("onClick={() => openCompany(c.id)}", read("components/session-panel.tsx"))
        self.assertIn("onClick={() => openCompany(row.id)}", read("components/companies.tsx"))

    def test_truncated_single_lines_scroll_on_hover(self):
        # 悬停滚动（用户 2026-10-15 夜，参考 DeepSeek 桌面端录屏）：一个共用钩子 + 组件，参数在 globals.css
        hook = read("lib/hover-scroll.ts")
        marquee = read("components/ui/marquee.tsx")
        css = read("globals.css")
        self.assertIn('!window.matchMedia("(prefers-reduced-motion: reduce)").matches', hook)
        self.assertIn("!touchOnly()", hook)
        self.assertIn('if (event.pointerType === "touch" || event.buttons) return;', hook)
        self.assertIn('host.querySelector("input, textarea")', hook)   # 就地改名时不滚
        self.assertIn("isDragging()", hook)                             # 拖动中不滚（共用拖动的标记）
        # 键盘焦点也滚，但只认真正的键盘操作（第 5 轮：鼠标关菜单后焦点被还回来不算）
        self.assertIn('if (!keyboardLast || !target?.matches?.(":focus-visible")) {', hook)
        self.assertNotIn("new ResizeObserver", hook)                    # 悬停时才量
        # 放不放得下按「框宽 - 右端渐隐」比；滚动中只观察这一个元素的文字变化
        self.assertIn("const distance = distanceOf(box);", hook)
        self.assertIn("return overflows(over) ? over + fade : 0;", hook)        # 放得下不滚；放不下滚到越过末尾渐隐
        self.assertIn("scrollDistance(overflowOf(box), ", hook)                     # 与静止时的渐隐同一个量法
        self.assertIn("changes.observe(box, { childList: true, characterData: true, subtree: true });", hook)
        self.assertIn("useHoverScroll<HTMLElement>()", marquee)
        self.assertIn('<span className="cc-marquee-text">{children}</span>', marquee)
        for line in ("--cc-marquee-delay: 150ms;", "--cc-marquee-speed: 40;", "--cc-marquee-min: 300ms;", "--cc-marquee-fade: var(--code-pad-5);"):
            self.assertIn(line, css)
        self.assertIn("transform: translateX(var(--cc-marquee-x, 0px)); transition: transform var(--cc-marquee-dur, 0ms) linear;", css)
        self.assertIn("mask-image: linear-gradient(to right, transparent, black var(--cc-marquee-fade), black calc(100% - var(--cc-marquee-end)), transparent);", css)
        self.assertIn(".cc-marquee, .cc-fade-cell { --cc-marquee-end: var(--cc-row-fade); text-overflow: clip; }", css)   # 右端保留末尾渐隐
        self.assertIn("@media (prefers-reduced-motion: reduce), (hover: none) {\n  .cc-marquee[data-marquee] > .cc-marquee-text { transform: none; transition: none; }", css)
        # 用到的地方：标题栏对话名、会话栏行、公司页对话行、菜单里的公司名
        self.assertIn('<Marquee className="truncate">{title || t("untitled")}</Marquee>', read("assistant.tsx"))
        self.assertIn('<Marquee className="min-w-0 flex-1 truncate">{c.title}</Marquee>', read("components/session-panel.tsx"))
        self.assertIn('<Marquee className="cc-row-title">{row.title || t("untitled")}</Marquee>', read("components/company-page.tsx"))
        self.assertIn('<Marquee className="min-w-0 flex-1 truncate">{label}</Marquee>\n      {id === current && <CheckIcon />}',
                      read("components/assistant-ui/projects.tsx"))

    def test_rows_fade_pulse_and_take_a_metadata_line(self):
        rows = read("components/assistant-ui/thread-list.tsx")
        item = rows.split("\nconst Row: FC", 1)[1].split("\n};\n", 1)[0]
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
        # 标题超长末尾渐隐：同所有单行截断的文字，只在放不下时（[data-overflow]，用户 10-16「统一渐隐」）
        self.assertIn(".cc-row-title { overflow: hidden; white-space: nowrap; }", css)
        self.assertIn(":is(.cc-marquee, .cc-fade-cell)[data-overflow] {\n  -webkit-mask-image: linear-gradient(to right, black calc(100% - var(--cc-marquee-end)), transparent);", css)
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
        # 已经在那家的不动（第 5 轮审查，同拖动整批）
        self.assertIn('runInOrder(notIn(sessions, project), (id) => fileSession(id, { project, period: "", scope: "" }), report)', projects)
        # 单字母快捷键：按物理键认（泰文键盘下也一样），批量时只有 P、D
        self.assertIn("onKeyDown={menuLetterKeys({ KeyP: pin, KeyR: onRename, KeyD: remove }, close)}", rows)
        self.assertIn("onKeyDown={menuLetterKeys({ KeyP: pinAll, KeyD: deleteAll }, close)}", rows)
        self.assertNotIn("function letterKeys", rows)
        keys = read("lib/sidebar-keys.ts")
        self.assertIn('const isDesktopWindow = () => document.documentElement.dataset.ccVariant === "desktop";', keys)
        # 多选、批量、⌘ / Ctrl + 1–9 只在有公司分组的产品（ERP / DMS 的侧栏不变）
        self.assertIn("if (!isDesktopWindow() || !companies()) return;", keys)
        self.assertIn("if (event.shiftKey && companies() && to.matches(ROW_TRIGGER) && target.matches(ROW_TRIGGER)) {", keys)
        self.assertIn("if (isMobile || !companies || (event.target as HTMLElement).closest(", rows)
        self.assertIn("const bulk = companies && selection.length > 1 && selection.includes(localId);", rows)
        self.assertIn("selectRange(rowOrder(event.currentTarget), localId, currentRow(event.currentTarget));", rows)
        self.assertIn("const keys = useSidebarKeys(companies);", read("assistant.tsx"))
        # ERP / DMS 的日期组开关也在 roving 那一组里（↑ ↓ 不跳过、Tab 不单独停）
        day = rows.split("const DayGroups: FC", 1)[1].split("\n};\n", 1)[0]
        self.assertIn('data-slot="aui_thread-list-group-label"\n          data-roving=""\n          className="cc-section-label cc-section-toggle"', day)
        # 组标题右侧操作（tabIndex -1）键盘从组开关进：已展开时 →，操作之间 ← →，最左再 ← 回组开关
        self.assertIn('else if (event.key === "ArrowRight") groupActions(target)[0]?.focus();', keys)
        self.assertIn("if (target.matches(GROUP_ACTION)) return onActionKey(event, target);", keys)
        self.assertIn("if (next < 0) focus(toggle);", keys)
        self.assertIn("const HINT_DELAY_MS = 300;", keys)
        self.assertIn("<SidebarContent ref={keys}", read("assistant.tsx"))
        # 侧栏增删合到一帧做一次：重排 roving、选中的里去掉看不见的行；弹窗、菜单开着时 Esc 不清多选、⌘ / Ctrl + 数字不切对话
        self.assertIn("const kept = keepRendered(selection, rowOrder(root));", keys)
        self.assertIn("const observer = new MutationObserver(onMutation);", keys)
        self.assertNotIn("new MutationObserver(sync)", keys)
        # 拖动中的 Esc 只取消拖动，不清多选（审查第 4 轮；第 4 轮测试机第 9 项：取消拖动的那一下 Esc 本身也认得出，见 isDragEscape）
        # 一次只关一层（第 5 轮第 4 项）：上面一层已经用掉这个 Esc（标记已处理）就不清多选
        self.assertIn("if (useSidebarSelection.getState().ids.length && !event.isComposing && !event.defaultPrevented && !layered() && !isDragEscape(event)) {", keys)
        self.assertIn("if (event.altKey || event.metaKey || event.ctrlKey || isDragging()) return;", keys)   # 键盘拖动中方向键归拖动
        self.assertIn("|| event.altKey || event.shiftKey || layered()) return;", keys)

    def test_collapse_and_grouping_are_remembered_on_this_device(self):
        app = read("assistant.tsx")
        self.assertIn('const SIDEBAR_OPEN_KEY = "cc.sidebar.open";', app)
        self.assertIn("<SidebarProvider style={sidebarWidth.style} open={sidebarOpen} onOpenChange={setSidebarOpen}>", app)
        view = read("lib/managed-agents/sidebar-view.ts")
        self.assertIn("const viewKey = (user: string) => `cc.sidebar.view:${user}`;", view)
        self.assertIn('export const DEFAULT_VIEW: SidebarView = { by: "company", sort: "updated", desc: true, meta: false, empty: true };', view)
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
// 还不知道是谁时改的：先只在本页生效，知道是谁时按这次改的存给这个人（不被读回的默认覆盖）
setSidebarView({ sort: "name" });
assert.equal(g.storage.size, 0);
bindSidebarViewUser("z@x.test");
assert.equal(useSidebarView.getState().sort, "name");
assert.deepEqual(JSON.parse(g.storage.get("cc.sidebar.view:z@x.test")), { ...DEFAULT_VIEW, sort: "name" });
resetSidebarView();
bindSidebarViewUser("a@x.test");
setSidebarView({ by: "month", meta: true });
assert.equal(g.storage.get("cc.sidebar.user"), "a@x.test");
assert.deepEqual(JSON.parse(g.storage.get("cc.sidebar.view:a@x.test")), { ...DEFAULT_VIEW, by: "month", meta: true });
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

    def test_group_folds_are_kept_per_user_on_every_device(self):
        # 测试窗口第 3 轮（手机）：重开 App 后各组都收着、电脑却记得。折叠按用户记在本机（同「分组和排序」），首屏就按这台设备上次
        # 登录的人读；电脑、手机同一份 store；有对话的前 5 家默认展开，没有对话的空公司组不占位
        seed = r"""
const s = (globalThis as any).storage as Map<string, string>;
s.set("cc.sidebar.user", "a@x.test");
s.set("cc.sidebar.collapsed:a@x.test", JSON.stringify(["project:comp_A", "open:project:comp_F"]));
s.set("cc.sidebar.collapsed", JSON.stringify(["project:comp_B"]));
"""
        self.run_page(r"""
import "cc-storage";
import "cc-seed";
import { bindFoldUser, foldKey, toggleFold, toggleFoldSection, useSidebarFold } from "@/lib/managed-agents/sidebar-fold";
const g = globalThis as any;
const fold = () => [...useSidebarFold.getState().collapsed].sort();
// 重开：资料还没到，首屏就是上次那个人记下的
assert.deepEqual(fold(), ["open:project:comp_F", "project:comp_A"]);
toggleFoldSection({ key: "project:comp_A", rank: 0 });
assert.deepEqual(JSON.parse(g.storage.get(foldKey("a@x.test"))), ["open:project:comp_F"]);
bindFoldUser("a@x.test");   // 同一人：不变
assert.deepEqual(fold(), ["open:project:comp_F"]);
// 换一个人：他没记过 → 用设备上原来的那份；之后记在他自己的键
bindFoldUser("b@x.test");
assert.deepEqual(fold(), ["project:comp_B"]);
toggleFold("2026-10-15");
assert.deepEqual(JSON.parse(g.storage.get(foldKey("b@x.test"))), ["project:comp_B", "2026-10-15"]);
bindFoldUser("a@x.test");
assert.deepEqual(fold(), ["open:project:comp_F"]);
""", stubs={"cc-storage": STORAGE_STUB, "cc-seed": seed})
        # 这台设备第一次：还不知道是谁时点的、改版前设备上的那份，登录后存给这个人
        self.run_page(r"""
import "cc-storage";
import "cc-seed";
import { bindFoldUser, foldKey, toggleFold, useSidebarFold } from "@/lib/managed-agents/sidebar-fold";
const g = globalThis as any;
assert.deepEqual([...useSidebarFold.getState().collapsed], ["recents"]);
toggleFold("pinned");
assert.deepEqual(JSON.parse(g.storage.get("cc.sidebar.collapsed")), ["recents", "pinned"]);
bindFoldUser("c@x.test");
assert.deepEqual(JSON.parse(g.storage.get(foldKey("c@x.test"))), ["recents", "pinned"]);
""", stubs={"cc-storage": STORAGE_STUB,
            "cc-seed": '(globalThis as any).storage.set("cc.sidebar.collapsed", JSON.stringify(["recents"]));'})
        self.run_page(r"""
import { companySections, groupOpen } from "@/lib/managed-agents/sidebar-groups";
const at = (m: number) => Date.UTC(2026, 9, 15, 12, 0) - m * 60000;
// 7 家空公司活动更近（排在前面），有对话的 G、H 在后：G、H 仍是默认展开的前两家，空公司组默认收着
const projects = ["A", "B", "C", "D", "E", "F", "G", "H"].map((x, i) => ({ id: `comp_${x}`, title: `Company ${x}`, last_active: new Date(at(i)).toISOString() }));
const rows = [{ index: 0, title: "g", starred: false, time: at(500), group: "comp_G" }, { index: 1, title: "h", starred: false, time: at(600), group: "comp_H" }];
const sections = companySections(rows, projects)!;
const open = (key: string) => groupOpen(sections.find((s) => s.key === key) as any, new Set(), false);
assert.deepEqual(sections.map((s) => s.key).slice(0, 3), ["project:comp_A", "project:comp_B", "project:comp_C"]);
assert.equal(open("project:comp_G"), true);
assert.equal(open("project:comp_H"), true);
assert.equal(open("project:comp_A"), false);
// 服务端说这家还有更多（本页还没取到它的对话）：也算有对话
const more = companySections([], projects, { serverMore: { comp_H: "x|y" } })!;
assert.equal(groupOpen(more.find((s) => s.key === "project:comp_H") as any, new Set(), false), true);
// 公司先到、对话还没到：照公司顺序前 5 家展开，不是全收着
const early = companySections([], projects)!;
const earlyOpen = (key: string) => groupOpen(early.find((s) => s.key === key) as any, new Set(), false);
assert.deepEqual(["A", "B", "C", "D", "E", "F"].map((x) => earlyOpen(`project:comp_${x}`)), [true, true, true, true, true, false]);
""")
        tree = read("components/assistant-ui/thread-list.tsx")
        self.assertIn("const collapsed = useSidebarFold((s) => s.collapsed);", tree)
        self.assertIn("bindFoldUser(user);", tree)
        self.assertNotIn("localStorage", tree)

    def test_menu_letters_only_fire_on_the_menu_itself(self):
        # 审查 B1：子菜单（「移到 ▸」「将 N 项移到…」）里的键沿组件树冒到行菜单的 onKeyDown；在里面按 D 不能删对话、按 P 不能置顶
        self.run_page(r"""
import { menuLetterKeys, ownMenuKey } from "@/lib/sidebar-keys";
type Node = { attrs: Record<string, string>; parent: Node | null; closest(sel: string): Node | null };
const el = (attrs: Record<string, string>, parent: Node | null = null): Node => ({
  attrs, parent,
  closest(sel: string) {
    const [, name, value] = /^\[([\w-]+)="([^"]*)"\]$/.exec(sel)!;
    for (let n: Node | null = this; n; n = n.parent) if (n.attrs[name] === value) return n;
    return null;
  },
});
const menu = el({ role: "menu" });
const item = el({ role: "menuitem" }, menu);
const sub = el({ role: "menu" }, menu);                                // 宽屏：旁边展开的子菜单（组件树上在行菜单里）
const subItem = el({ role: "menuitem" }, sub);
const subInput = el({}, sub);
const page = el({ "data-slot": "cc-menu-subpage" }, menu);             // 窄屏：原菜单里换上的子菜单那一页
const pageItem = el({ role: "menuitem" }, page);
assert.ok(ownMenuKey(item, menu));
assert.ok(!ownMenuKey(subItem, menu));
assert.ok(!ownMenuKey(subInput, menu));
assert.ok(!ownMenuKey(pageItem, menu));
assert.ok(!ownMenuKey(null, menu));
const ran: string[] = [];
let closed = 0;
const keys = menuLetterKeys({ KeyP: () => ran.push("pin"), KeyD: () => ran.push("delete") }, () => closed++);
const press = (code: string, target: Node, mods: Partial<Record<"metaKey" | "ctrlKey" | "altKey", boolean>> = {}) => {
  let prevented = false;
  keys({ code, target: target as any, currentTarget: menu as any, metaKey: false, ctrlKey: false, altKey: false, ...mods,
         preventDefault: () => { prevented = true; } });
  return prevented;
};
assert.equal(press("KeyD", subItem), false);
assert.equal(press("KeyD", pageItem), false);
assert.equal(press("KeyP", subItem), false);
assert.deepEqual(ran, []);
assert.equal(closed, 0);
assert.equal(press("KeyD", item, { metaKey: true }), false);          // 带修饰键不是快捷键
assert.equal(press("KeyR", item), false);                              // 没给的字母不管
assert.equal(press("KeyD", item), true);
assert.deepEqual(ran, ["delete"]);
assert.equal(closed, 1);
""")

    def test_selection_keys_and_one_by_one(self):
        self.run_page(r"""
import { clearSelection, jumpDigit, keepRendered, nextIndex, rangeBetween, runInOrder, selectRange, toggleIn, toggleSelected, useSidebarSelection } from "@/lib/sidebar-keys";
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
// 第一次 Shift 点：从正开着的那一行选到点的这一行（同 Claude Code）；正开着的不在眼前就只选点的这一行
selectRange(order, "d", "b");
assert.deepEqual(useSidebarSelection.getState(), { ids: ["b", "c", "d"], anchor: "b" });
clearSelection();
selectRange(order, "d", "gone");
assert.deepEqual(useSidebarSelection.getState(), { ids: ["d"], anchor: "d" });
clearSelection();
// 收起分组、搜索筛掉、换分组、别处删掉：选中的只留眼前还排着的行，批量操作不碰看不见的；起点不在了一并清掉
assert.equal(keepRendered({ ids: ["a", "b"], anchor: "a" }, order), null);
assert.deepEqual(keepRendered({ ids: ["a", "x", "b"], anchor: "a" }, order), { ids: ["a", "b"], anchor: "a" });
assert.deepEqual(keepRendered({ ids: ["x", "b"], anchor: "x" }, order), { ids: ["b"], anchor: null });
assert.deepEqual(keepRendered({ ids: ["x", "y"], anchor: "x" }, order), { ids: [], anchor: null });
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

    def test_company_choices_show_their_scope_and_rows_can_be_set_for_the_whole_company(self):
        # 数字会计员第 2 期：公司规则写明「全公司 / 分店 X」、付款可用下拉直接改；账套行与历史行多一个「设为全公司」
        self.run_page(r"""
import { promoteChoice } from "@/lib/companies";
import { choiceLine, settledView } from "@/components/pending";
const row = (choice: any, promote = true) => renderToStaticMarkup(createElement(ChoiceRow, { choice, onSet() {}, onForget() {},
  ...(promote ? { onPromote() {} } : {}) }));
const rule = row({ key: "r1", source: "rule", kind: "payment", subject_label: "ACME", value: "cash", editable: true, branch: "", set_by: "a" });
assert.ok(rule.includes("<select"));
assert.deepEqual(text(rule).slice(0, 2), ["ACME", "Whole company · a"]);
assert.ok(!buttons(rule).includes("Set for whole company"));   // 规则本身不再提升
assert.equal(text(row({ key: "r2", source: "rule", kind: "account", subject_label: "ACME", value: "5300", branch: "00012" }))[2],
  "Branch 00012");
assert.equal(text(row({ key: "r3", source: "rule", kind: "account", subject_label: "ACME", value: "5300", branch: "00000" }))[2],
  "Head office");
const express = { key: "k1", source: "express", kind: "payment", subject_label: "ACME", value: "credit", editable: true,
  year_label: "69", promotable: true };
assert.ok(buttons(row(express)).includes("Set for whole company"));
assert.ok(!buttons(row(express, false)).includes("Set for whole company"));   // 不给 onPromote 就不显示
const learned = { key: "k2", source: "history", kind: "account", subject_label: "", supplier_name: "ACME", value: "5320",
  count: 9, last_date: "2026-09-30", promotable: true };
assert.ok(buttons(row(learned)).includes("Set for whole company"));
g.respond = async () => ({ ok: true, status: 200, json: async () => ({ message: { saved: true } }) });
await promoteChoice("comp_abc", "k2", "2026-09");
assert.equal(g.calls.at(-1).method, "/api/method/pearnly_cowork.companies.api.choice_promote");
assert.deepEqual(g.calls.at(-1).body, { company: "comp_abc", key: "k2", period: "2026-09" });
// 当场选：公司规则定下了要选的（needed 为空）→ 直接推送；科目表在读时照旧交给 CC
assert.equal(settledView({ needed: [] } as any), true);
assert.equal(settledView({ needed: [], wait: { state: "reading", message: "" } } as any), false);
assert.equal(settledView({ needed: [{ kind: "account", subject: "s" }] } as any), false);
// 第 5 轮测试 7：行上的说明按展开后读到的要选项写；规则定下了、没有要选的（按钮是「推送」）不再写「付款方式 · 费用科目」
const stuck: any = { document: "D1", action: "choose", choice: { connection: "C1", kinds: ["payment", "account"] } };
assert.equal(choiceLine(stuck, null), "Payment · Expense account");
assert.equal(choiceLine(stuck, { needed: [] } as any), "");
assert.equal(choiceLine(stuck, { needed: [{ kind: "account", subject: "s" }, { kind: "account", subject: "t" }] } as any), "Expense account");
assert.equal(choiceLine(stuck, { needed: [], wait: { state: "reading", message: "" } } as any), "Payment · Expense account");
""")

    def test_browsers_and_phones_only_read_the_folder(self):
        self.run_page(r"""
assert.deepEqual(await loadDesktopFeatures(), { app: false, companyFolders: false });
const empty = html(FolderSection, { company: "comp_abc", folders: [], reload: noop });
assert.ok(text(empty).includes("Choose on computer"));     // 一行字，不是按钮
assert.deepEqual(buttons(empty), ["Documents folder"]);    // 唯一的按钮是卡头的折叠开关（卡片外框，CC 外壳）
const bound = html(FolderSection, { company: "comp_abc", folders: [folder], reload: noop });
assert.deepEqual(text(bound), ["Documents folder", "Ready", "PC-9 · \\\\nas\\ABC", "Ready · This month: 2569-09"]);   // 卡头摘要：一个文件夹写状态
assert.deepEqual(buttons(bound), ["Documents folder"]);
// 会话栏（hideEmpty）：浏览器、手机里没有绑定的文件夹时整张卡不显示；绑了的照样显示
assert.deepEqual(text(html(FolderSection, { company: "comp_abc", folders: [], reload: noop, hideEmpty: true })), []);
assert.deepEqual(text(html(FolderSection, { company: "comp_abc", folders: [folder], reload: noop, hideEmpty: true }))[0], "Documents folder");
""")

    def test_the_desktop_window_picks_and_hands_the_binding_over_unchanged(self):
        self.run_page(r"""
import { pickFolder } from "@/lib/desktop";
import { bindFolder } from "@/lib/companies";
assert.deepEqual(await loadDesktopFeatures(), { app: true, companyFolders: true });
const empty = html(FolderSection, { company: "comp_abc", folders: [], reload: noop });
assert.deepEqual(buttons(empty), ["Documents folder", "Choose on computer"]);   // 第一个是卡头的折叠开关
// 别的电脑那一行：只看状态；本机还没绑时照样给「在电脑上选择」
const other = html(FolderSection, { company: "comp_abc", folders: [folder], reload: noop });
assert.deepEqual(buttons(other), ["Documents folder", "Choose on computer"]);
// 本机这一行：更换… / 移除
const mine = html(FolderSection, { company: "comp_abc", folders: [{ ...folder, mine: true }], reload: noop });
assert.deepEqual(buttons(mine), ["Documents folder", "Change…", "Remove"]);
// 桌面版窗口里会话栏（hideEmpty）照样显示：这里能选
assert.deepEqual(buttons(html(FolderSection, { company: "comp_abc", folders: [], reload: noop, hideEmpty: true })), ["Documents folder", "Choose on computer"]);
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
assert.deepEqual(buttons(page), ["Documents folder", "Update CC Desktop"]);   // 第一个是卡头的折叠开关
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
        # 公司页的对话按这家从服务端取（分组列表 group=<公司>，分页），不靠侧栏已加载的几条
        page = read("components/company-page.tsx")
        chats = page.split("export const Conversations: FC", 1)[1].split("\n// ----", 1)[0]
        # 顺带核对侧栏以为在跑的那几个（同侧栏的 check）：读回的运行状态里有等你点「允许」的标「等你处理」
        self.assertIn("const check = runningSessions();", chats)
        self.assertIn("await listGrouped({ group: company, after, check });", chats)
        # 读回的运行状态交给 noteRun 校正实时状态（同侧栏 absorb），之后才开始跑的补读一次
        self.assertIn("for (const [id, run] of Object.entries(found ?? {})) noteRun(id, run);", chats)
        self.assertIn("absorbRuns(check, page.runs);", chats)
        self.assertIn("listGrouped({ group: company, check: ids })", chats)
        # 加载中转圈、没有对话时说一句，不是一片空白
        self.assertIn("if (!rows) return <LoaderIcon", chats)
        self.assertIn('{!rows.length && <p className="cc-company-empty">{co.noChats}</p>}', chats)
        self.assertIn("const mark = chatMark(chatState(activities[row.id], runs[row.id], row.unread), activities[row.id]);", chats)
        self.assertIn("onClick={() => void load(next)}", chats)
        self.assertNotIn("threadItems", chats)
        # 别家的移动不重取；移进这家、从这一页移走的才重取，侧栏条数变化与推送合成一次
        self.assertIn("if (change.project === company || rowsNow.current?.some((row) => row.id === change.session_id)) reload();", chats)
        self.assertNotIn('change.op === "move" ? void load()', chats)
        self.assertIn("soon.current = window.setTimeout(() => void load(), RELOAD_MERGE_MS);", chats)
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
        self.assertIn("taskSeed: taskSeedOf(detail, period, knownReaders(), app && companyFolders),", read("components/company-cards.tsx"))

    def test_branches_group_account_sets_and_saved_choices_show_where_they_came_from(self):
        self.run_page(r"""
import { formatWhen } from "@/lib/companies";
const sets = [{ connection: "c", key: "69HQ", label: "ABC 69", year_label: "69", period_start: "2025-10-01", period_end: "2026-09-30" },
  { connection: "c", key: "69B1", label: "ABC 69 B1", year_label: "69" }];
const branches = [{ branch: "00000", label: "", account_sets: [sets[0]] }, { branch: "00001", label: "Rama 9", account_sets: [sets[1]] }];
assert.deepEqual(text(html(SetsSection, { sets, branches, period: "2026-09" })),
  ["Account sets", "2 account sets", "Head office", "ABC 69", "69", "Rama 9", "ABC 69 B1", "69"]);   // 卡头摘要：账套数
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
        # 审查第 4 轮：公司页与它的会话栏共用一份详情（usePanelDetail），只有先挂上的那一处跟着事件重取
        self.assertIn("const { detail, error, reload } = usePanelDetail(company, period);", page)
        self.assertNotIn("loadDetail(", page)
        panel = read("lib/company-panel.ts")
        self.assertIn("useLiveRefresh(() => (owner() ? reload() : undefined), { events: TASK_EVENTS });", panel)
        self.assertIn("if (!inflight.has(key)) void reload();", panel)
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


# ── 附件卡片与就地改名（2026-10-16 CC 外壳验收，Q 线）──────────────────────────────────────────────────────────
# 你发的消息里每个附件一张卡片（缩略图、文件名一行、「PDF · N 页」/ 大小），点开在右侧面板预览（含 PDF，修 S3）；读屏名字跟页面语言。
# 标题与侧栏行的就地改名同 Claude Code：同字号、一行高、透明底、无边框、圆角 3、聚焦只有 1.5px 蓝描边；回车存、Esc 放弃；
# 32 的标题栏、侧栏搜索框里的聚焦不被裁掉（紧凑描边）。

AUI_STUB = r"""
import { createElement } from "react";
const g = globalThis as any;
export const useAuiState = (select: any) => select(g.auiState);
function Item({ a, render }: any) {
  g.auiState = { attachment: a };
  return render({ attachment: a });
}
export const MessagePrimitive = {
  Attachments: ({ children }: any) => (g.items ?? []).map((a: any, i: number) => createElement(Item, { key: i, a, render: children })),
};
export const ComposerPrimitive = { Attachments: () => null, AddAttachment: () => null };
export const AttachmentPrimitive = { Root: () => null, Remove: () => null, Name: () => null };
"""

ATTACH_PAGE = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { AttachmentCard, attachmentCard, attachmentTypeLine, formatSize, UserMessageAttachments } from "@/components/assistant-ui/attachment";
import { ResultsContext } from "@/components/results-panel";
import { createAttachmentAdapter, localMeta, uploadFileId } from "@/lib/managed-agents/attachments";

const g = globalThis as any;
const opened: any[] = [];
const results = (uploads: any[] = [], sessionId: string | null = "s1") =>
  ({ sessionId, files: [], uploads, openFile: () => {}, openUpload: (f: any) => opened.push(f) });
const bubble = (items: any[], value: any) => {
  g.items = items;
  return renderToStaticMarkup(createElement(ResultsContext.Provider, { value }, createElement(UserMessageAttachments)));
};
const att = (name: string, path: string) => ({ id: path, type: /\.(png|jpe?g)$/.test(name) ? "image" : "document", name,
  contentType: "", status: { type: "complete" }, content: [] });
const PDF = "/mnt/session/uploads/file_011CPdf/statement.pdf";
const XLSX = "/mnt/session/uploads/file_011CXls/GL-05.xlsx";
const JPG = "/mnt/session/uploads/file_011CJpg/slip.jpg";
const listed = [
  { id: "file_011CPdf", filename: "statement.pdf", size_bytes: 52000, pages: 3, kind: "upload", downloadable: false, mime_type: "application/pdf", created_at: "2026-10-16 09:00:00" },
  { id: "file_011CXls", filename: "GL-05.xlsx", size_bytes: 12800, kind: "upload", downloadable: false, mime_type: "application/octet-stream", created_at: "x" },
];
"""


class AttachmentCardSourceTests(unittest.TestCase):
    def test_message_attachments_are_cards_that_open_in_the_dock(self):
        source = read("components/assistant-ui/attachment.tsx")
        self.assertNotIn("attachment${", source)          # 英文拼出来的「Document attachment」读屏名字没了
        self.assertNotIn("Image Attachment Preview", source)
        self.assertIn('t("attachment")', source)
        self.assertIn('t("imagePreview")', source)
        self.assertIn("{() => <MessageAttachment />}", source)
        self.assertIn("{() => <ComposerAttachment />}", source)
        self.assertIn("<AttachmentRemove />", source)       # 输入框里的移除、上传中、失败照旧
        self.assertIn("aui-attachment-tile-uploading", source)
        self.assertIn("aui-attachment-tile-error", source)
        self.assertIn("results.openUpload(open)", source)
        # 右侧面板：附件不在结果文件里，临时多列点开的那一个；右上角「文件 N」仍只数结果
        page = read("assistant.tsx")
        hook = page.split("function useResultsPanel()", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("const openUpload = useCallback((file: SessionFile) => {", hook)
        self.assertIn("[...outputs, upload]", hook)
        self.assertIn("{results.outputs.length}", page)
        self.assertIn("files: results.outputs,", page)
        self.assertIn('setUploads(list.filter((f) => f.kind === "upload"));', read("components/results-panel.tsx"))

    def test_every_language_has_the_attachment_words(self):
        messages = block(read("i18n.ts"), "const MESSAGES = {")
        for lang in ("en", "zh", "th"):
            words = block(messages, f"  {lang}: {{")
            for key in ("attachment", "uploading", "imagePreview", "pageCount", "onePage"):
                self.assertRegex(words, rf"\n    {key}: \"[^\"]+\"", (lang, key))
            self.assertRegex(words, r'attachment: "[^"]*\{0\}[^"]*"', lang)
            self.assertRegex(words, r'pageCount: "[^"]*\{0\}[^"]*"', lang)

    def test_in_place_rename_looks_like_claude_code_and_focus_is_never_clipped(self):
        rows = read("components/assistant-ui/thread-list.tsx")
        rename = rows.split("export const ThreadListItemRename", 1)[1].split("\n};\n", 1)[0]
        self.assertIn('className = "cc-row-edit"', rename)
        self.assertIn('className={cn("cc-inline-edit", className)}', rename)
        self.assertNotIn("cc-input", rename)
        self.assertIn('if (e.key === "Enter" && !isImeEnter(e))', rename)   # 输入法选字的回车不保存
        self.assertIn("finish(false)", rename.split('"Escape"', 1)[1])       # Esc 放弃
        self.assertIn("if (finished.current) return;", rename)                # 卸下时的失焦不存第二次
        self.assertIn('className="cc-title-edit"', read("assistant.tsx"))
        css = read("globals.css")
        self.assertIn("--code-radius-3: 3px;", css)
        self.assertIn("--code-focus-w: 1.5px;", css)
        edit = css.split("  .cc-inline-edit {", 1)[1].split("}", 1)[0]
        for rule in ("height: var(--cc-row-lh)", "border: 0", "border-radius: var(--cc-radius-inline-edit)", "background: transparent"):
            self.assertIn(rule, edit)
        focus = css.split("  .cc-inline-edit:focus {", 1)[1].split("}", 1)[0]
        self.assertIn("outline: var(--cc-focus-w) solid var(--cc-focus-ring)", focus)
        self.assertNotIn("shadow", focus)                                       # 没有 6px 光晕
        title = css.split("  .cc-title-edit {", 1)[1].split("}", 1)[0]
        self.assertIn("height: var(--cc-title-lh)", title)
        self.assertIn("font-size: var(--cc-title-font)", title)
        self.assertIn("font-weight: 500", title)
        self.assertIn("--cc-title-font: var(--code-text-footnote);", css)      # 13
        self.assertIn("--cc-title-lh: var(--code-text-panel-lh);", css)        # 19
        self.assertIn("font-size: var(--cc-title-font); line-height: var(--cc-title-lh); font-weight: 500;",
                      css.split("  .cc-title-btn {", 1)[1].split("}", 1)[0])
        # 32 的标题栏里、侧栏搜索框：紧凑的里描边，不画会被窗口顶边裁掉的光晕
        self.assertIn("--cc-shadow-focus-inset: inset 0 0 0 var(--cc-focus-w) var(--cc-focus-ring);", css)
        self.assertRegex(css, r":is\(\.cc-topbar, \.cc-titlebar\) :is\([^)]*\.cc-title-btn[^)]*\):focus-visible \{\s*box-shadow: var\(--cc-shadow-focus-inset\);")
        self.assertIn(".cc-search:focus-within { box-shadow: var(--cc-shadow-focus-inset); }", css)


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class AttachmentCardBehaviourTests(unittest.TestCase):
    def run_page(self, checks, lang="en"):
        run_ts(ATTACH_PAGE + checks + "\ng.done();\n", stubs={"@assistant-ui/react": AUI_STUB}, lang=lang)

    def test_cards_show_name_type_line_and_open_the_upload(self):
        self.run_page(r"""
assert.equal(uploadFileId(PDF), "file_011CPdf");
assert.equal(uploadFileId("statement.pdf"), null);
assert.equal(formatSize(900), "900 B");
assert.equal(formatSize(12800), "13 KB");
assert.equal(formatSize(3.4 * 1024 * 1024), "3.4 MB");
assert.equal(attachmentTypeLine("statement.pdf", 52000, 3), "PDF · 3 pages");
assert.equal(attachmentTypeLine("one.PDF", 1, 1), "PDF · 1 page");
assert.equal(attachmentTypeLine("statement.pdf", 52000), "PDF");          // 页数读不出：只写类型，不写大小
assert.equal(attachmentTypeLine("GL-05.xlsx", 12800), "XLSX · 13 KB");
assert.equal(attachmentTypeLine("GL-05.xlsx"), "XLSX");

// 气泡：每个文件一张卡片按钮，读屏名字是「Attachment: 名字 · 类型说明」；PDF 的缩略图是服务端画的第一页（第 5 轮测试 1），
// 从页顶显示（data-page）；别的类型是类型方块
const html = bubble([att("statement.pdf", PDF), att("GL-05.xlsx", XLSX)], results(listed));
assert.equal((html.match(/<button type="button" class="cc-attach-card"/g) ?? []).length, 2);
assert.match(html, /aria-label="Attachment: statement\.pdf · PDF · 3 pages"/);
assert.match(html, /<span class="cc-marquee cc-attach-name"><span class="cc-marquee-text">statement\.pdf<\/span><\/span><span class="cc-marquee cc-attach-type"><span class="cc-marquee-text">PDF · 3 pages<\/span><\/span>/);
assert.match(html, /<span class="cc-marquee cc-attach-type"><span class="cc-marquee-text">XLSX · 13 KB<\/span><\/span>/);
assert.match(html, /<span class="cc-attach-thumb" aria-hidden="true" data-page="true"><img src="\/api\/method\/pearnly_core\.assistant\.api\.page_image\?session_id=s1&amp;file_id=file_011CPdf&amp;page=1&amp;size=thumb" alt="" loading="lazy"/);
assert.match(html, /<span class="cc-attach-tile"><svg[^>]*lucide-file-spreadsheet[^>]*>.*?<\/svg><span>XLSX<\/span><\/span>/);
assert.equal(attachmentCard(PDF, "statement.pdf", results(listed)).page, true);
assert.equal(attachmentCard(XLSX, "GL-05.xlsx", results(listed)).page, undefined);
// 第 5 轮审查：本站没留存的老附件（列表没给大小、也不是本页刚发的）不取缩略图（引擎不给下载，只会白问再 404）
const OLD = "/mnt/session/uploads/file_011COld/old.pdf";
const unkept = attachmentCard(OLD, "old.pdf", results([{ ...listed[0], id: "file_011COld", filename: "old.pdf", size_bytes: undefined, pages: undefined }]));
assert.equal(unkept.page, undefined);
assert.equal(unkept.image, undefined);
assert.equal(attachmentCard(OLD, "old.pdf", results([])).page, undefined);   // 列表还没到：先是类型方块
assert.doesNotMatch(html, /Document attachment|attachment,/);
assert.doesNotMatch(html, /#[0-9a-fA-F]{3,8}\b|rgb\(/);

// 点开：交给右侧面板的是这个附件（文件编号来自沙箱位置），带名字、大小、页数
const card = attachmentCard(PDF, "statement.pdf", results(listed));
assert.deepEqual(card.open, { id: "file_011CPdf", filename: "statement.pdf", size_bytes: 52000, mime_type: "application/pdf",
  downloadable: false, created_at: "2026-10-16 09:00:00", kind: "upload", pages: 3 });
const element: any = AttachmentCard({ name: "statement.pdf", ...card, onOpen: () => opened.push(card.open) });
assert.equal(element.type, "button");
element.props.onClick();
assert.equal(opened.at(-1).id, "file_011CPdf");

// 图片：刷新后用服务端留存的那份当缩略图（内联地址）
const image = attachmentCard(JPG, "slip.jpg", results([]));
assert.equal(image.image, "/api/method/pearnly_core.assistant.api.download?session_id=s1&file_id=file_011CJpg&inline=1");
assert.match(bubble([att("slip.jpg", JPG)], results([])), /<img src="\/api\/method\/pearnly_core\.assistant\.api\.download\?session_id=s1&amp;file_id=file_011CJpg&amp;inline=1" alt="" loading="lazy"/);

// 没有会话、认不出文件编号：只是一张卡片，不可点
assert.equal(attachmentCard(PDF, "statement.pdf", results(listed, null)).open, undefined);
const plain = bubble([att("old.pdf", "old.pdf")], results(listed));
assert.doesNotMatch(plain, /<button/);
assert.match(plain, /<div class="cc-attach-card" role="img" aria-label="Attachment: old\.pdf · PDF"/);
// 认不出文件编号的 PDF：没有缩略图可取，是类型方块
assert.match(plain, /<span class="cc-attach-tile"><svg[^>]*lucide-file-text[^>]*>.*?<\/svg><span>PDF<\/span><\/span>/);
assert.doesNotMatch(plain, /page_image/);
""")

    def test_reader_names_follow_the_page_language(self):
        self.run_page(r"""
const html = bubble([att("statement.pdf", PDF)], results(listed));
assert.match(html, /aria-label="附件：statement\.pdf · PDF · 3 页"/);
assert.equal(attachmentTypeLine("a.pdf", 1, 1), "PDF · 1 页");
""", lang="zh")
        self.run_page(r"""
assert.match(bubble([att("statement.pdf", PDF)], results(listed)), /aria-label="ไฟล์แนบ: statement\.pdf · PDF · 3 หน้า"/);
""", lang="th")

    def test_a_fresh_upload_shows_its_pages_before_the_file_list_is_read_again(self):
        self.run_page(r"""
g.URL.createObjectURL = () => "blob:local";
g.fetch = async () => ({ ok: true, status: 200, json: async () => ({ message: {
  fileId: "file_011CNew", filename: "new.pdf", mountPath: "/mnt/session/uploads/file_011CNew/new.pdf", size_bytes: 4096, pages: 2 } }) });
const adapter = createAttachmentAdapter(async () => "s1");
const file = new File([new Uint8Array(4096)], "new.pdf", { type: "application/pdf" });
const steps: any[] = [];
for await (const step of adapter.add!({ file }) as any) steps.push(step.status.type);
assert.deepEqual(steps, ["running", "requires-action"]);
assert.deepEqual(localMeta("/mnt/session/uploads/file_011CNew/new.pdf"), { size: 4096, pages: 2 });
assert.equal(attachmentCard("/mnt/session/uploads/file_011CNew/new.pdf", "new.pdf", results([])).typeLine, "PDF · 2 pages");
// 本页刚发的（本站已留存）列表没到也取第一页缩略图
assert.equal(attachmentCard("/mnt/session/uploads/file_011CNew/new.pdf", "new.pdf", results([])).page, true);
""")


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class SidebarDragTests(unittest.TestCase):
    """侧栏拖动（第 5 轮，用户 10-16 选「公司可拖 + 置顶可拖」）与共用拖动的规则（components/ui/drag.tsx）。"""

    def run_page(self, body, stubs=None):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + body + "\n(globalThis as any).done();\n",
               stubs={"socket.io-client": "export function io() { return { on() { return this; }, off() { return this; }, connected: true }; }",
                      **(stubs or {})})

    def test_manual_orders_new_companies_and_new_pins_on_top(self):
        self.run_page(STORAGE_STUB + r"""
import { arrange, bindOrderUser, mergeOrder, moveBy, moveTo, orderKey, parseOrder, setCompanyOrder, setPinnedOrder, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import { companySections, flatSections, monthSections } from "@/lib/managed-agents/sidebar-groups";
// 纯函数：挪到落点、挪一格、合并（这次没显示的接在后面）、按手排的顺序排（新的在最上面）
assert.deepEqual(moveTo(["a", "b", "c", "d"], "a", "c"), ["b", "c", "a", "d"]);
assert.deepEqual(moveTo(["a", "b", "c", "d"], "d", "b"), ["a", "d", "b", "c"]);
assert.equal(moveTo(["a", "b"], "a", "a"), null);
assert.equal(moveTo(["a", "b"], "x", "a"), null);
assert.deepEqual(moveBy(["a", "b", "c"], "b", -1), ["b", "a", "c"]);
assert.equal(moveBy(["a", "b", "c"], "a", -1), null);         // 到头了
assert.deepEqual(mergeOrder(["c", "a"], ["a", "b", "c", "d"]), ["c", "a", "b", "d"]);
const items = [{ id: "new1" }, { id: "b" }, { id: "a" }, { id: "new2" }];
assert.deepEqual(arrange(items, (x) => x.id, ["a", "b"]).map((x) => x.id), ["new1", "new2", "a", "b"]);
assert.deepEqual(arrange(items, (x) => x.id, []).map((x) => x.id), ["new1", "b", "a", "new2"]);   // 没手排过：原样
assert.deepEqual(parseOrder('{"companies":["a",1,"a"],"pinned":"x"}'), { companies: ["a"], pinned: [] });
assert.deepEqual(parseOrder("not json"), { companies: [], pinned: [] });

// 分组：手排过的公司按手排的顺序，新出现的公司排最上面；组里的对话照排序方式排
const at = (m: number) => Date.UTC(2026, 9, 16, 12, 0) - m * 60000;
const projects = [{ id: "comp_A", title: "A Co", last_active: new Date(at(1)).toISOString() },
                  { id: "comp_B", title: "B Co", last_active: new Date(at(2)).toISOString() },
                  { id: "comp_C", title: "C Co", last_active: new Date(at(3)).toISOString() }];
const rows = [
  { index: 0, title: "a1", starred: false, time: at(1), group: "comp_A", key: "s0" },
  { index: 1, title: "c1", starred: false, time: at(3), group: "comp_C", key: "s1" },
  { index: 2, title: "p1", starred: true, time: at(4), group: "comp_B", key: "s2" },
  { index: 3, title: "p2", starred: true, time: at(5), group: "", key: "s3" },
  { index: 4, title: "p3", starred: true, time: at(6), group: "", key: "s4" },
];
const keys = (s: any[] | null) => s!.map((x) => x.key);
assert.deepEqual(keys(companySections(rows, projects)), ["pinned", "project:comp_A", "project:comp_B", "project:comp_C"]);
let s = companySections(rows, projects, { companyOrder: ["comp_C", "comp_A"] });
assert.deepEqual(keys(s), ["pinned", "project:comp_B", "project:comp_C", "project:comp_A"]);   // comp_B 没排过：最上面
// 前 5 家默认展开按新顺序数（没有对话的 comp_B 不占位）
assert.deepEqual(s!.filter((x: any) => x.type === "project").map((x: any) => x.rank), [5, 0, 1]);
// 置顶按手排的先后，新置顶的在最上面（三种分组方式一样）
const pinnedOf = (s: any[] | null) => s!.find((x) => x.key === "pinned").indices;
assert.deepEqual(pinnedOf(companySections(rows, projects)), [2, 3, 4]);
assert.deepEqual(pinnedOf(companySections(rows, projects, { pinnedOrder: ["s4", "s2"] })), [3, 4, 2]);
assert.deepEqual(pinnedOf(monthSections(rows, projects, { pinnedOrder: ["s4", "s2"] })), [3, 4, 2]);
assert.deepEqual(pinnedOf(flatSections(rows, projects, { pinnedOrder: ["s4", "s2"] })), [3, 4, 2]);

// 按人记住（同卡片顺序：本机、键带用户）；还不知道是谁时本次生效、不写进不带人的键
setCompanyOrder(["comp_C", "comp_A"]);
assert.deepEqual(useSidebarOrder.getState().companies, ["comp_C", "comp_A"]);
assert.equal(storage.size, 0);
bindOrderUser("skin3@x");
assert.deepEqual(useSidebarOrder.getState(), { companies: [], pinned: [] });   // 换成这个人记下的（还没有）
setCompanyOrder(["comp_B", "comp_A"]);
setPinnedOrder(["s4"]);
assert.deepEqual(JSON.parse(storage.get(orderKey("skin3@x"))!), { companies: ["comp_B", "comp_A"], pinned: ["s4"] });
setCompanyOrder(["comp_A", "comp_B"]);
assert.deepEqual(useSidebarOrder.getState().companies, ["comp_A", "comp_B"]);
storage.set(orderKey("other@x"), JSON.stringify({ companies: ["comp_C"], pinned: [] }));
bindOrderUser("other@x");
assert.deepEqual(useSidebarOrder.getState().companies, ["comp_C"]);
""")

    def test_unpinned_chats_leave_the_manual_order_and_reset_clears_it(self):
        # 第 5 轮审查：取消置顶不从手排顺序里去掉，再置顶时回到旧位置（不在最上面）；「恢复默认设置」也不清手排顺序
        self.run_page(STORAGE_STUB + r"""
import { bindOrderUser, clearSidebarOrder, forgetPinned, hasManualOrder, orderKey, setCompanyOrder, setPinnedOrder, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import { companySections } from "@/lib/managed-agents/sidebar-groups";
import { DEFAULT_VIEW, resetSidebarView, setSidebarView, useSidebarView } from "@/lib/managed-agents/sidebar-view";
bindOrderUser("skin3@x");
const row = (index: number, key: string, starred: boolean) => ({ index, title: key, starred, time: 1000 - index, group: "", key });
// 置顶 A、B、C，把 A 拖到最后：[B, C, A]
setPinnedOrder(["sB", "sC", "sA"]);
const pinnedOf = (rows: any[]) => companySections(rows, [], { pinnedOrder: useSidebarOrder.getState().pinned })!.find((x: any) => x.key === "pinned")!.indices;
// 取消 A 的置顶 → 从记下的顺序里去掉（不认得的键不动）
forgetPinned(["sA", "nope"]);
assert.deepEqual(useSidebarOrder.getState().pinned, ["sB", "sC"]);
assert.deepEqual(JSON.parse(storage.get(orderKey("skin3@x"))!).pinned, ["sB", "sC"]);
// 再置顶 D 和 A：两条都是新置顶的，排最上面（它们之间照排序方式）
assert.deepEqual(pinnedOf([row(0, "sA", true), row(1, "sB", true), row(2, "sC", true), row(3, "sD", true)]), [0, 3, 1, 2]);
// 恢复默认设置：分组和排序回默认，手排的公司组、置顶顺序一起清掉，本机记下的也删掉
setCompanyOrder(["comp_B", "comp_A"]);
setSidebarView({ sort: "name" });
assert.equal(hasManualOrder(useSidebarOrder.getState()), true);
resetSidebarView();
assert.deepEqual(useSidebarView.getState(), DEFAULT_VIEW);
assert.deepEqual(useSidebarOrder.getState(), { companies: [], pinned: [] });
assert.equal(hasManualOrder(useSidebarOrder.getState()), false);
assert.equal(storage.has(orderKey("skin3@x")), false);
clearSidebarOrder();                                   // 本来就空：不再写
assert.equal(storage.has(orderKey("skin3@x")), false);
""")
        rows = read("components/assistant-ui/thread-list.tsx")
        groups = rows.split("const CompanyGroups: FC", 1)[1].split("\nconst DayGroups", 1)[0]
        # 侧栏按已加载的行收拾：不再置顶的（单条、批量、别的设备）从手排顺序里去掉
        self.assertIn("return item && !item.custom?.starred ? [item.remoteId ?? id] : [];", groups)
        self.assertIn("if (gone.length) forgetPinned(gone);", groups)
        # 「恢复默认设置」：分组和排序跟默认一样、也没手排过才不可点
        self.assertIn("disabled={isDefaultView(view) && !manual} onSelect={resetSidebarView}", read("components/assistant-ui/sidebar-group.tsx"))

    def test_personal_layout_follows_the_user_across_devices(self):
        # 测试窗口第 7 轮第 2 项：侧栏手排顺序、分组和排序、会话栏卡片顺序与开合只记在本机。改成按人存到服务端（layout / save_layout），
        # 打开时取、改了就存、后写的为准；本机缓存照样先顶着、离线照样能用；折叠仍按设备；服务端没有时把本机原有的迁上去。
        # 第 7 轮审查：只存改了的项（服务端按项合并），本机改了还没存上的项不被服务端的盖掉，别的项照样换成服务端的
        self.run_page(STORAGE_STUB + r"""
import { bindOrderUser, forgetPinned, orderKey, setCompanyOrder, setPinnedOrder, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import { bindSidebarViewUser, DEFAULT_VIEW, setSidebarView, useSidebarView } from "@/lib/managed-agents/sidebar-view";
import { toggleFold } from "@/lib/managed-agents/sidebar-fold";
import { loadPanelPrefs, panelKey, setCardOrder, setPanelOpen, toggleCard, usePanel } from "@/lib/company-panel";
import { collectPrefs, REFETCH_AFTER, SAVE_DELAY, syncPrefs } from "@/lib/prefs-sync";
const g = globalThis as any;
let server: any = null;
let offline = false;
// 服务端按项合并（pearnly_core.assistant.prefs.save）
const merge = (old: any, sent: any) => {
  const out: any = { ...(old ?? {}) };
  for (const [part, fields] of Object.entries(sent)) out[part] = { ...(out[part] ?? {}), ...(fields as any) };
  return out;
};
g.respond = async (method: string, body: any) => {
  if (offline) throw new TypeError("Failed to fetch");
  if (method === "layout") return g.ok({ prefs: server });
  if (method === "save_layout") { server = merge(server, body.prefs); return g.ok({ prefs: server }); }
  return g.ok(null);
};
g.ok = (message: any) => ({ ok: true, status: 200, json: async () => ({ message }) });
const saves = () => g.calls.filter((c: any) => c.method === "save_layout");
const sent = () => saves().map((c: any) => JSON.parse(JSON.stringify(c.body.prefs)));

// 这台设备上原来记下的（旧版本只记本机）：第一次登录、服务端还没有 → 整份迁上去
storage.set("cc.sidebar.user", "skin3@x");
storage.set(orderKey("skin3@x"), JSON.stringify({ companies: ["comp_B", "comp_A"], pinned: ["s4"] }));
storage.set("cc.sidebar.view:skin3@x", JSON.stringify({ ...DEFAULT_VIEW, sort: "name" }));
bindOrderUser("skin3@x");
bindSidebarViewUser("skin3@x");
loadPanelPrefs("skin3@x");
await syncPrefs("skin3@x");
assert.deepEqual(g.calls.map((c: any) => c.method), ["layout", "save_layout"]);
assert.deepEqual(Object.keys(server).sort(), ["cards", "order", "view"]);   // 折叠不同步
assert.deepEqual(server.order, { companies: ["comp_B", "comp_A"], pinned: ["s4"] });
assert.equal(server.view.sort, "name");
assert.deepEqual(server.cards.collapsed, []);
assert.equal("open" in server.cards, false);                                   // 停靠时开着没有按设备记

// 改了就存（连着几下合成一次），只交改了的项；折叠、停靠开关不触发
g.calls.length = 0;
setCompanyOrder(["comp_A", "comp_B"]);
toggleCard("notes");
setSidebarView({ desc: false });
toggleFold("project:comp_A");
setPanelOpen(false);
await g.sleep(SAVE_DELAY + 50);
assert.equal(saves().length, 1);
assert.deepEqual(sent()[0], { order: { companies: ["comp_A", "comp_B"] }, cards: { collapsed: ["notes"] }, view: { desc: false } });
assert.deepEqual(server.order.pinned, ["s4"]);                                   // 没交的项服务端照旧
assert.deepEqual(server.order.companies, ["comp_A", "comp_B"]);
assert.deepEqual(server.cards.collapsed, ["notes"]);
assert.equal(server.view.desc, false);
g.calls.length = 0;
toggleFold("project:comp_B");
setPanelOpen(true);
await g.sleep(SAVE_DELAY + 50);
assert.equal(saves().length, 0);

// 另一台设备上改了（服务端是后写的）：这台打开 / 回到前台时换成服务端的，并写进本机缓存，不再回存
server = { order: { companies: ["comp_C"], pinned: [] }, view: { ...DEFAULT_VIEW, by: "month" }, cards: { order: ["tasks", "company"], collapsed: ["company"] } };
g.calls.length = 0;
await syncPrefs("skin3@x");
assert.deepEqual(g.calls.map((c: any) => c.method), ["layout"]);
assert.deepEqual(useSidebarOrder.getState().companies, ["comp_C"]);
assert.equal(useSidebarView.getState().by, "month");
assert.equal(usePanel.getState().order[0], "tasks");
assert.equal(usePanel.getState().order.length, 9);
assert.deepEqual(usePanel.getState().collapsed, ["company"]);
assert.deepEqual(JSON.parse(storage.get(orderKey("skin3@x"))!).companies, ["comp_C"]);
assert.equal(JSON.parse(storage.get("cc.sidebar.view:skin3@x")!).by, "month");
assert.deepEqual(JSON.parse(storage.get(panelKey("skin3@x"))!).collapsed, ["company"]);
await g.sleep(SAVE_DELAY + 50);
assert.equal(saves().length, 0);

// 离线时改的：本机照样生效、记着没存上；下次打开不被服务端旧的盖掉，补存本机的（后写的为准）
offline = true;
setPinnedOrder(["s9"]);
await g.sleep(SAVE_DELAY + 50);
assert.deepEqual(useSidebarOrder.getState().pinned, ["s9"]);
assert.deepEqual(JSON.parse(storage.get("cc.prefs.unsaved:skin3@x")!), ["order.pinned"]);   // 记着哪项没存上
offline = false;
server = { ...server, view: { ...server.view, sort: "created" } };                // 这期间别的设备改了排序方式
g.calls.length = 0;
await syncPrefs("skin3@x");
assert.deepEqual(g.calls.map((c: any) => c.method), ["layout", "save_layout"]);   // 先取：别的项换成服务端的，再补存没存上的那项
assert.deepEqual(sent(), [{ order: { pinned: ["s9"] } }]);
assert.deepEqual(server.order, { companies: ["comp_C"], pinned: ["s9"] });
assert.equal(useSidebarView.getState().sort, "created");
assert.deepEqual(useSidebarOrder.getState().pinned, ["s9"]);
assert.equal(storage.has("cc.prefs.unsaved:skin3@x"), false);

// 取的时候本机又改了：改的那项本机的为准，存上去；没改的项照样换成服务端的
server = { order: { companies: ["old"], pinned: [] } };
g.calls.length = 0;
const pending = syncPrefs("skin3@x");
setCardOrder(["outputs", "company"]);
await pending;
assert.equal(usePanel.getState().order[0], "outputs");
assert.deepEqual(useSidebarOrder.getState().companies, ["old"]);
assert.equal(server.cards.order[0], "outputs");
assert.deepEqual(server.cards, { order: collectPrefs().cards.order });
assert.deepEqual(sent(), [{ cards: { order: collectPrefs().cards.order } }]);

// 取的时候按数据自动收拾的（取消置顶了从手排顺序里去掉）不算人改的：照样换成服务端的（别的设备改的不被盖掉）
setPinnedOrder(["s1", "s2"]);
await g.sleep(SAVE_DELAY + 50);
server = { order: { companies: ["comp_Z"], pinned: ["s2", "s1"] } };
g.calls.length = 0;
const tidy = syncPrefs("skin3@x");
forgetPinned(["s1"]);
await tidy;
assert.deepEqual(useSidebarOrder.getState(), { companies: ["comp_Z"], pinned: ["s2", "s1"] });
assert.deepEqual(g.calls.map((c: any) => c.method), ["layout"]);

// 第 7 轮审查第 1 条：一直开着、很久没取过的设备上，别的设备取消置顶推过来（按数据收拾）。不拿本机过时的公司组顺序盖服务端：
// 先取最新的、换进来，侧栏照新的那份再收拾一遍，这时才存，只存置顶这一项
const realNow = performance.now.bind(performance);
let skew = 0;
(performance as any).now = () => realNow() + skew;
skew += REFETCH_AFTER * 3;
server = { ...server, order: { companies: ["comp_new"], pinned: ["s2", "s1"] } };
g.calls.length = 0;
forgetPinned(["s1"]);
assert.deepEqual(useSidebarOrder.getState().pinned, ["s2"]);                       // 本机马上去掉
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(g.calls.map((c: any) => c.method), ["layout"]);                  // 先取，不存
assert.deepEqual(useSidebarOrder.getState(), { companies: ["comp_new"], pinned: ["s2", "s1"] });
forgetPinned(["s1"]);                                                             // 侧栏照新的那份再收拾（刚取过）
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(sent(), [{ order: { pinned: ["s2"] } }]);
assert.deepEqual(server.order, { companies: ["comp_new"], pinned: ["s2"] });
""")
        # 打开页面知道是谁了就取；接口只读写自己的
        assistant = read("assistant.tsx")
        self.assertIn('useEffect(() => void syncPrefs(profile?.user ?? ""), [profile?.user]);', assistant)
        fold = read("lib/managed-agents/sidebar-fold.ts")
        self.assertNotIn("prefs-sync", fold)

    def test_layout_sync_never_pushes_a_stale_layout_and_retries_failed_fetches(self):
        # 第 7 轮审查第 5、7 条：资料没到之前侧栏先收拾了（取消置顶的从手排顺序里去掉）不算人改的，知道是谁以后照常取、换成服务端的，
        # 不把本机旧的整份存上去；取失败不算取过（联网、拿到焦点时再取）；同时叫几次只取一次
        self.run_page(STORAGE_STUB + r"""
import { bindOrderUser, forgetPinned, orderKey, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import { bindSidebarViewUser, DEFAULT_VIEW, useSidebarView } from "@/lib/managed-agents/sidebar-view";
import { loadPanelPrefs } from "@/lib/company-panel";
import { SAVE_DELAY, syncPrefs } from "@/lib/prefs-sync";
const g = globalThis as any;
const listeners: Record<string, () => void> = {};
g.addEventListener = (type: string, fn: () => void) => void (listeners[type] = fn);
g.document.addEventListener = (type: string, fn: () => void) => void (listeners[type] = fn);
let server: any = { order: { companies: ["comp_A2"], pinned: ["s1"] }, view: { ...DEFAULT_VIEW, by: "month" } };
let offline = false;
g.ok = (message: any) => ({ ok: true, status: 200, json: async () => ({ message }) });
g.respond = async (method: string) => {
  if (offline) throw new TypeError("Failed to fetch");
  if (method === "layout") { await g.sleep(20); return g.ok({ prefs: server }); }
  return g.ok({ prefs: server });
};
const methods = () => g.calls.map((c: any) => c.method);

storage.set(orderKey("skin3@x"), JSON.stringify({ companies: ["comp_old"], pinned: ["sX", "s1"] }));
bindOrderUser("skin3@x");
bindSidebarViewUser("skin3@x");
loadPanelPrefs("skin3@x");
forgetPinned(["sX"]);                                  // 侧栏列表先回来（资料还没到）：只改本机
assert.deepEqual(useSidebarOrder.getState().pinned, ["s1"]);
await g.sleep(SAVE_DELAY + 50);
const first = syncPrefs("skin3@x");
assert.equal(syncPrefs("skin3@x"), first);             // 同时叫两次只取一次
await first;
assert.deepEqual(methods(), ["layout"]);               // 不存本机旧的
assert.deepEqual(useSidebarOrder.getState(), { companies: ["comp_A2"], pinned: ["s1"] });
assert.equal(useSidebarView.getState().by, "month");
assert.equal(storage.has("cc.prefs.unsaved:skin3@x"), false);

// 取失败（离线）不算取过：联网、拿到焦点时马上再取（不等 REFETCH_AFTER）
g.calls.length = 0;
offline = true;
await syncPrefs("other@x");                          // 换了人（还没取过）：取不到，照本机的
offline = false;
server = { view: { ...DEFAULT_VIEW, sort: "name" } };
listeners.online();
await g.sleep(60);
assert.deepEqual(methods(), ["layout", "layout"]);
assert.equal(useSidebarView.getState().sort, "name");
listeners.focus();                                     // 刚取过：不再取
await g.sleep(60);
assert.deepEqual(methods(), ["layout", "layout"]);
""")

    def test_drop_is_announced_by_what_really_happened(self):
        # 测试窗口第 7 轮第 1 项：真的移过去了（发了移动请求、对话已在新组），最后却念「已取消移动」（电脑拖到组头、手机拖到行上、
        # 自动滚动后落到别组、键盘 ↑ + 空格，共 5 次）。根因：拖动库先调放下的处理、后发播报；落位已播完时放下的处理当场把这一项
        # 交给调用方并把空档清回 null，播报读到的成了「原组」。改为按放下那一刻定下的去处念
        self.run_page(r"""
import { dragAnnouncements } from "@/components/ui/drag";
import { sidebarAccepts } from "@/lib/managed-agents/sidebar-drag";
const texts = { instructions: "", pickedUp: (n: string) => `up ${n}`, movedTo: () => "moved", droppedAt: () => "dropped",
  cancelled: (n: string) => `cancel ${n}`, over: (n: string, g: string) => `over ${n} ${g}`, droppedIn: (n: string, g: string) => `in ${n} ${g}` };
const row = { id: "row:t1", kind: "row", label: "lelo", group: "A" };
const company = (id: string) => ({ id: `company:${id}`, kind: "company", label: id, list: "companies", group: id });
const entry = (d: any) => ({ id: d.id, data: { current: { drag: d } } });
let placed: any = company("B");
let dropped: any = undefined;
const say = dragAnnouncements(texts, { accepts: sidebarAccepts, placed: () => placed, lifted: () => row, dropped: () => dropped });
say.onDragStart!({ active: entry(row) } as any);
assert.equal(say.onDragOver!({ active: entry(row), over: entry(company("B")) } as any), "over lelo B");
// 放下：先交给调用方（空档清回 null），播报在后——按放下那一刻定下的去处念
dropped = company("B");
placed = null;
assert.equal(say.onDragEnd!({ active: entry({ ...row, group: "B" }), over: entry(company("B")) } as any), "in lelo B");
// 拖回原组放下：没移，念取消
dropped = null;
placed = company("B");
assert.equal(say.onDragEnd!({ active: entry(row), over: entry(company("A")) } as any), "cancel lelo");
// 没记（旧的调用方）：照旧按此刻的空档
dropped = undefined;
assert.equal(say.onDragEnd!({ active: entry(row), over: null } as any), "in lelo B");
""")
        drag = read("components/ui/drag.tsx")
        end = drag.split("const onDragEnd = ({ active, over }: DragEndEvent) => {", 1)[1].split("\n  };\n", 1)[0]
        # 先记去处、再交（settleMove 落位已播完时当场交、清空档）
        self.assertLess(end.index("dropped.current = target && latest.current.accepts(from, target) ? target : null;"),
                        end.index("const queued = settleMove("))
        self.assertIn("dropped: () => dropped.current,", drag)
        self.assertIn("if (end && over.id === active.id) return texts.cancelled(name(active)); // 落回原位：没动", drag)

    def test_the_float_turns_see_through_over_a_folded_group_head(self):
        # 第 7 轮第 3 项：手机上浮层停在收着的组头上，盖住了组头的高亮。停在只能高亮组头的落点上时浮层半透明，离开就恢复（共用拖动）
        drag = read("components/ui/drag.tsx")
        self.assertIn('setPeek(!!active && !!placed && typeof document !== "undefined" && !!document.querySelector?.("[data-drop-target]"));', drag)
        self.assertIn('data-peek={peek ? "" : undefined}', drag)
        css = read("globals.css")
        self.assertIn("--cc-drag-peek: 0.5;", css)
        self.assertIn(".cc-drag-float[data-peek] .cc-drag-lift { opacity: var(--cc-drag-peek); }", css)
        # 过渡沿用浮层已有的 opacity 过渡（动效变量）；落到组头时的淡出写在它后面，照样盖过它
        lift = css.split("\n.cc-drag-lift {", 1)[1].split("}", 1)[0]
        self.assertIn("opacity var(--cc-dur) var(--cc-ease)", lift)
        self.assertLess(css.index(".cc-drag-float[data-peek] .cc-drag-lift"), css.index('.cc-drag-overlay[data-dropping="into"] .cc-drag-lift'))

    def test_group_and_sort_choices_say_which_one_is_picked(self):
        # 第 7 轮第 5 项：子菜单选项原是普通 menuitem，读屏听不出当前选的是哪个：单选用 menuitemradio、勾选用 menuitemcheckbox，带
        # aria-checked（组件库 RadioItem / CheckboxItem），✓ 的样子不变；「显示」里的两项点了菜单不关
        group = read("components/assistant-ui/sidebar-group.tsx")
        menu = group.split("export const GroupSortMenu", 1)[1]
        self.assertEqual(menu.count("<DropdownMenu.RadioGroup"), 3)
        self.assertIn("<DropdownMenu.RadioItem className=\"cc-menu-item\" value={value}>", group)
        self.assertIn("<DropdownMenu.CheckboxItem className=\"cc-menu-item\" checked={on} onCheckedChange={onChange} onSelect={(event) => event.preventDefault()}>", group)
        self.assertEqual(group.count("<DropdownMenu.ItemIndicator asChild>\n      <CheckIcon />"), 2)
        self.assertNotIn("onPick", group)
        self.run_page(r"""
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { DropdownMenu as Radix } from "radix-ui";
// 组件库的单选、勾选项：role 与 aria-checked
const html = renderToStaticMarkup(createElement(Radix.Root, { open: true, modal: false },
  createElement(Radix.Content, { forceMount: true },
    createElement(Radix.RadioGroup, { value: "b" }, createElement(Radix.RadioItem, { value: "a" }, "A"), createElement(Radix.RadioItem, { value: "b" }, "B")),
    createElement(Radix.CheckboxItem, { checked: true }, "C"))));
assert.match(html, /role="menuitemradio" aria-checked="false"/);
assert.match(html, /role="menuitemradio" aria-checked="true"/);
assert.match(html, /role="menuitemcheckbox" aria-checked="true"/);
""", stubs={})

    def test_phone_company_menu_lines_up_with_the_head(self):
        # 第 7 轮第 6 项：手机长按公司组头的菜单按「···」右对齐，左边缘正好落在组里各行文字起点右边几像素，各行首字从菜单左边
        # 露出一点。手机上公司组头的菜单改为左边贴着组头开关的左边，盖住各行的起点
        self.run_page(r"""
import { headOffset } from "@/components/assistant-ui/sidebar-group";
assert.equal(headOffset({ left: 240 }, { left: 16 }), -224);
assert.equal(headOffset(null, { left: 16 }), null);
assert.equal(headOffset({ left: 240 }, undefined), null);
""")
        group = read("components/assistant-ui/sidebar-group.tsx")
        self.assertIn('align={head ? "start" : "end"}', group)
        self.assertIn("alignOffset={head ? offset : 0}", group)
        self.assertIn('const head = button?.closest(".cc-group-row")?.querySelector(".cc-group-toggle");', group)

    def test_what_can_be_dropped_where(self):
        self.run_page(r"""
import { sidebarAccepts } from "@/lib/managed-agents/sidebar-drag";
const row = (group: string) => ({ id: "row:t1", kind: "row", label: "Chat", group });
const company = (id: string) => ({ id: `company:${id}`, kind: "company", label: id, list: "companies", group: id });
const recents = { id: "group:recents", kind: "recents", label: "Recents", group: "" };
const pin = (id: string) => ({ id: `pin:${id}`, kind: "pin", label: id, list: "pins" });
// 对话行：别的公司组、最近；原来那一组不算（拖回原组不动），置顶不是落点
assert.equal(sidebarAccepts(row("comp_A"), company("comp_B")), true);
assert.equal(sidebarAccepts(row("comp_A"), recents), true);
assert.equal(sidebarAccepts(row("comp_A"), company("comp_A")), false);
assert.equal(sidebarAccepts(row(""), recents), false);
assert.equal(sidebarAccepts(row(""), company("comp_A")), true);
assert.equal(sidebarAccepts(row("comp_A"), pin("t2")), false);
// 置顶、公司组只在自己那一列里调序
assert.equal(sidebarAccepts(pin("t1"), pin("t2")), true);
assert.equal(sidebarAccepts(pin("t1"), company("comp_A")), false);
assert.equal(sidebarAccepts(company("comp_A"), company("comp_B")), true);
assert.equal(sidebarAccepts(company("comp_A"), recents), false);
assert.equal(sidebarAccepts(company("comp_A"), pin("t1")), false);
""")

    def test_dropping_into_a_company_moves_one_by_one_and_shows_the_server_text(self):
        self.run_page(r"""
const g = globalThis as any;
import { moveDragged } from "@/lib/managed-agents/sidebar-drag";
import { filingOf, noteFilings, useProjects } from "@/lib/managed-agents/projects";
import { useSidebarSelection } from "@/lib/sidebar-keys";
const ok = (body: any) => ({ ok: true, status: 200, json: async () => ({ message: body }) });
let inFlight = 0;
let most = 0;
const refused = JSON.stringify([JSON.stringify({ message: "ไม่มีสิทธิ์ในบริษัทนี้" })]);
g.respond = async (method: string, body: any) => {
  if (method !== "move") return ok(null);
  inFlight++;
  most = Math.max(most, inFlight);
  await g.sleep(5);
  inFlight--;
  if (body.session_id === "sesn_b") return { ok: false, status: 417, json: async () => ({ _server_messages: refused }) };
  return ok({ id: body.session_id, project: body.project, period: body.period, scope: body.scope });
};
useProjects.setState({ kind: "company", period: true, scope: true } as any);
// 一条：同「移到 ▸」，月份、分店留空
await moveDragged(["sesn_a"], "comp_B", false);
const moves = () => g.calls.filter((c: any) => c.method === "move").map((c: any) => c.body);
assert.deepEqual(moves()[0], { session_id: "sesn_a", project: "comp_B", period: "", scope: "" });
assert.equal(filingOf("sesn_a").project, "comp_B");
// 多选整批：一条做完再下一条（不并发）；中间一条被拒（权限）不挡后面的，侧栏出错行写服务端原文，做完清空多选
useSidebarSelection.setState({ ids: ["t1", "t2", "t3"], anchor: "t1" });
noteFilings({ sesn_b: { project: "comp_A", period: "2026-09", scope: "" }, sesn_c: { project: "comp_A", period: "", scope: "00012" } });
await moveDragged(["sesn_a", "sesn_b", "sesn_c"], "", true);
assert.equal(most, 1);
assert.deepEqual(moves().slice(1).map((m: any) => [m.session_id, m.project]), [["sesn_a", ""], ["sesn_b", ""], ["sesn_c", ""]]);
assert.equal(useProjects.getState().error, "ไม่มีสิทธิ์ในบริษัทนี้");
assert.equal(filingOf("sesn_b").project, "comp_A");              // 被拒的那条退回原来的公司
assert.equal(filingOf("sesn_c").project, "");
assert.deepEqual(useSidebarSelection.getState().ids, []);
// 第 5 轮审查：整批里已经在目标公司的不动（不发请求、月份分店不清空；同拖回原组不动），其余照样逐条移
noteFilings({ sesn_x: { project: "comp_A", period: "", scope: "" },
              sesn_y: { project: "comp_B", period: "2026-09", scope: "" }, sesn_z: { project: "comp_B", period: "2026-09", scope: "00012" } });
const before = moves().length;
await moveDragged(["sesn_x", "sesn_y", "sesn_z"], "comp_B", true);
assert.deepEqual(moves().slice(before).map((m: any) => m.session_id), ["sesn_x"]);
assert.deepEqual(filingOf("sesn_y"), { project: "comp_B", period: "2026-09", scope: "" });
assert.deepEqual(filingOf("sesn_z"), { project: "comp_B", period: "2026-09", scope: "00012" });
// 移出公司：本来就在「最近」的不动
const before2 = moves().length;
await moveDragged(["sesn_a", "sesn_x"], "", true);
assert.deepEqual(moves().slice(before2).map((m: any) => [m.session_id, m.project]), [["sesn_x", ""]]);
""")
        # 行菜单「将 N 项移到…」同一个过滤
        self.assertIn("void runInOrder(notIn(sessions, project), (id) => fileSession(id, { project, period: \"\", scope: \"\" }), report).then(onDone);",
                      read("components/assistant-ui/projects.tsx"))

    def test_escape_while_dragging_only_cancels_the_drag(self):
        # 第 4 轮测试机第 9 项：键盘拿起卡片按 Esc，拖动库在 document 上收到就结束了拖动，挂在 window 上的侧栏 Esc 随后才收到同一个
        # 事件——只看「正在拖」会以为没在拖、清掉多选。现在拖动期间在 window 捕获阶段先记下这次 Esc
        self.run_page(r"""
import { isDragEscape, isDragging, setDragging } from "@/lib/layers";
const g = globalThis as any;
const capture: Array<(e: any) => void> = [];
g.addEventListener = (type: string, run: any, useCapture: boolean) => type === "keydown" && useCapture && capture.push(run);
g.removeEventListener = (type: string, run: any) => capture.splice(capture.indexOf(run), 1);
setDragging(true);
assert.equal(isDragging(), true);
const esc = { key: "Escape" };
for (const run of [...capture]) run(esc);         // window 捕获阶段（最先）
setDragging(false);                                // 拖动库在 document 上处理了：结束拖动
assert.equal(isDragging(), false);
assert.equal(isDragEscape(esc as any), true);      // 后面 window 上的处理仍认得这是取消拖动的 Esc
assert.equal(isDragEscape({ key: "Escape" } as any), false);   // 不在拖动时的 Esc 照常
assert.equal(capture.length, 0);                   // 拖完就摘掉监听
""")

    def test_escape_after_release_is_not_a_drag_escape(self):
        # 审查：松手后到落位播完（约 0.2 秒，ReleaseFirst）还算在拖，这时按 Esc 被当成取消拖动的 Esc 记下，浮着的会话栏不关、
        # 侧栏多选不清，要再按一次。现在开始等落位时（releaseDrag）不再记 Esc，isDragEscape 也不再因为「还在拖」回真；Esc 照样
        # 当场结束拖动（press），不吞这个事件。拖动中按下、已经记下的 Esc 照旧算
        self.run_page(r"""
import { ReleaseFirst } from "@/components/ui/drag";
import { isDragEscape, isDragging, setDragging } from "@/lib/layers";
const g = globalThis as any;
const capture = new Map<string, Array<(e: any) => void>>();
g.addEventListener = (type: string, run: any) => capture.set(type, [...(capture.get(type) ?? []), run]);
g.removeEventListener = (type: string, run: any) => capture.set(type, (capture.get(type) ?? []).filter((f) => f !== run));
const fire = (type: string, e: any) => { for (const run of [...(capture.get(type) ?? [])]) run(e); };
const animation = { playState: "running", finished: new Promise(() => {}) } as any;
setDragging(true);
const during = { key: "Escape" };
fire("keydown", during);                                  // 拖动中按的 Esc：记下（取消拖动用）
assert.equal(isDragEscape(during as any), true);
const letGo = new ReleaseFirst();
let ended = 0;
letGo.hold(() => (ended++, setDragging(false)), animation, 1000);
assert.equal(isDragging(), true);                         // 还在等落位
const after = { key: "Escape" };
assert.equal(isDragEscape(after as any), false);          // 已经放手：之后的 Esc 不是取消拖动的
fire("keydown", after);                                   // 按下 Esc：当场结束拖动（press），不记成取消拖动的
assert.equal(ended, 1);
assert.equal(isDragging(), false);
assert.equal(isDragEscape(after as any), false);          // 会话栏、侧栏多选照常处理这一下
assert.equal(isDragEscape(during as any), true);          // 拖动中按下的那次照旧算
// 下一次拖动重新记
setDragging(true);
const next = { key: "Escape" };
fire("keydown", next);
assert.equal(isDragEscape(next as any), true);
setDragging(false);
""")
        self.assertIn("    releaseDrag();\n", read("components/ui/drag.tsx"))
        self.assertIn("return escapes.has(event) || (isDragging() && !released);", read("lib/layers.ts"))

    def test_lifted_overlay_sits_under_menus_and_over_drawers(self):
        # 审查：手机按住不动 250ms 拿起、500ms 弹出长按菜单（行、公司组头）。浮层原来用拖动库默认的 z-index 999，菜单是 60，
        # 菜单落在拿起的那一项（公司组是组头加整组的行）范围里时被浮层盖住、字叠在一起，要等松手落位播完才露出来。现在浮层在
        # 抽屉、手机会话栏（50）之上，在菜单（60）之下
        drag = read("components/ui/drag.tsx")
        self.assertIn("export const OVERLAY_Z = 55;", drag)
        self.assertIn('<DragOverlay dropAnimation={drop} transition={keyboardMove} zIndex={OVERLAY_Z} className="cc-drag-overlay">', drag)
        css = read("globals.css")
        menu = re.search(r"\.cc-menu \{\s*z-index: (\d+);", css)
        phone = re.search(r'\.cc-panel\[data-mode="phone"\] \{\s*position: fixed; inset: 0; z-index: (\d+);', css)
        self.assertIsNotNone(menu)
        self.assertIsNotNone(phone)
        self.assertLess(int(phone.group(1)), 55)
        self.assertLess(55, int(menu.group(1)))

    def test_touch_hold_lifts_at_250ms_and_moving_wins_over_the_long_press_menu(self):
        # 第 6 轮第 2 项：原来侧栏的行按住 250ms 后要再动才拿起、按到 500ms 就归菜单，慢慢开始拖（按住 0.4s 后每步只移 8px，或
        # 按住 0.7s）的人一直进菜单、再也拖不起来。现在按住 250ms（5px 内）就拿起（行、组头、置顶、卡片把手同一规则）；之后手指
        # 离按下处动出 10px（同长按菜单作废的距离）就是在拖（记到 layers，长按菜单不出、已经出来的收起，见下一个用例）；没到
        # 250ms 就动 = 在滚动。审查：原来动出 3px 就算在拖，拿起前允许漂 5px，按住时手指自然一抖长按菜单就出不来了
        self.run_page(r"""
import { HoldTouchSensor, TOUCH_DRAG_SLOP } from "@/components/ui/drag";
import { isDragMoving, setDragging } from "@/lib/layers";
const g = globalThis as any;
g.document.querySelector = () => null;
g.addEventListener = () => {};
g.removeEventListener = () => {};
g.getSelection = () => ({ removeAllRanges() {} });
g.MouseEvent = class { type: string; init: any; constructor(type: string, init: any) { this.type = type; this.init = init; } };
assert.equal(TOUCH_DRAG_SLOP, 10);
const clicksOff: string[] = [];
function press(options: any = {}, isConnected = true) {
  const on: Record<string, (e: any) => void> = {};
  const log: string[] = [];
  const target = { isConnected, addEventListener: (t: string, f: any) => (on[t] = f), removeEventListener: (t: string) => delete on[t],
                   dispatchEvent: (e: any) => { log.push(`${e.type}@${e.init.clientX},${e.init.clientY}${e.init.bubbles ? " bubbles" : ""}`); return true; } };
  g.document.addEventListener = () => {};
  g.document.removeEventListener = (type: string) => clicksOff.push(type);
  new HoldTouchSensor({
    active: "row:t1", event: { target, touches: [{ clientX: 100, clientY: 100 }] }, options: { delay: 250, tolerance: 5, ...options },
    onStart: () => { log.push("start"); setDragging(true); }, onMove: () => log.push("move"),
    onEnd: () => { log.push("end"); setDragging(false); }, onCancel: () => { log.push("cancel"); setDragging(false); },
    onAbort: () => log.push("abort"), onPending: () => {},
  } as any);
  const move = (x: number, y: number) => { let prevented = false; on.touchmove?.({ touches: [{ clientX: x, clientY: y }], cancelable: true, preventDefault: () => (prevented = true) }); return prevented; };
  return { on, log, move };
}
// 按住 250ms（5px 内）就拿起：不用再动
let p = press();
await g.sleep(280);
assert.deepEqual(p.log, ["start"]);
assert.equal(isDragMoving(), false);                  // 还没动：长按菜单照旧可以出
assert.equal(p.move(101, 102), true);                 // 拿起后滑动不滚页面；动了 2px 还不算在拖
assert.equal(isDragMoving(), false);
assert.equal(p.move(100, 108), true);                 // 慢慢动，第一步 8px：还在长按菜单作废的距离里
assert.equal(isDragMoving(), false);
assert.equal(p.move(100, 116), true);                 // 第二步（16px）：在拖了
assert.equal(isDragMoving(), true);
assert.deepEqual(p.log, ["start", "move", "move", "move"]);
p.on.touchend!({});
assert.deepEqual(p.log, ["start", "move", "move", "move", "end"]);
assert.equal(isDragMoving(), false);                  // 结束时清掉
// 审查：拿起前手指漂了 4px（没出 5px，照样拿起），拿起后再抖 1–2px：不算在拖，长按菜单照旧能出
p = press();
await g.sleep(100);
p.move(103, 103);
await g.sleep(180);
assert.deepEqual(p.log, ["start"]);
for (const [x, y] of [[104, 104], [102, 105], [105, 103], [103, 106]]) p.move(x, y);
assert.equal(isDragMoving(), false);
p.on.touchend!({ cancelable: true, preventDefault() {} });
// 按住 0.7s 才动：照样拖得起来（原来归菜单、取消）
p = press();
await g.sleep(700);
p.move(100, 130);
assert.deepEqual(p.log, ["start", "move"]);
assert.equal(isDragMoving(), true);
p.on.touchend!({});
// 没到 250ms 就动出 5px：是在滚动，不拿起
p = press();
await g.sleep(100);
assert.equal(p.move(100, 120), false);
assert.deepEqual(p.log, ["abort", "cancel"]);
// 审查：拿起后没动、没到长按菜单的时间（500ms）就松手 = 点按（动作慢的人点按常超过 250ms）：取消拖动（浮层落回原位），系统的
// click 不要（拦下 touchend），在按下的那个元素上补一个冒泡的 click（行打开对话、组头折叠）；拦点击的监听当场摘掉，不等 50ms
for (const ms of [280, 450]) {
  p = press();
  await g.sleep(ms);
  let prevented = false;
  clicksOff.length = 0;
  p.on.touchend!({ cancelable: true, preventDefault: () => (prevented = true) });
  assert.deepEqual(p.log, ["start", "cancel", "click@100,100 bubbles"], `${ms}ms`);
  assert.equal(prevented, true);
  assert.ok(clicksOff.includes("click") && clicksOff.includes("selectionchange"));
}
// 拿起后抖了一下（没出 10px）也还是点按
p = press();
await g.sleep(280);
p.move(102, 104);
p.on.touchend!({ cancelable: true, preventDefault() {} });
assert.deepEqual(p.log, ["start", "move", "cancel", "click@100,100 bubbles"]);
// 元素已经不在页面上：只取消，不补点击
p = press({}, false);
await g.sleep(280);
p.on.touchend!({ cancelable: true, preventDefault() {} });
assert.deepEqual(p.log, ["start", "cancel"]);
// 按住不动到菜单时间（500ms，菜单出来了）再松手：照常结束（浮层原地落下，菜单留着），不补点击、松手后的 click 照旧拦下
p = press();
await g.sleep(560);
clicksOff.length = 0;
p.on.touchend!({ cancelable: true, preventDefault() {} });
assert.deepEqual(p.log, ["start", "end"]);
assert.equal(clicksOff.includes("click"), false);
// 拿起后动过（在拖）再松手：照常放下，不是点按
p = press();
await g.sleep(280);
p.move(100, 130);
p.on.touchend!({ cancelable: true, preventDefault() {} });
assert.deepEqual(p.log, ["start", "move", "end"]);
// 没按够就松手：点按照旧（没拿起）
p = press();
await g.sleep(50);
p.on.touchend!({});
assert.deepEqual(p.log, ["abort", "end"]);
// 放下先交给 beforeEnd（先播落位，播完才结束拖动）；没拿起的不经过它
let held: (() => void) | null = null;
p = press({ beforeEnd: (end: () => void) => { p.log.push("before"); held = end; } });
await g.sleep(280);
p.move(100, 140);
p.on.touchend!({});
assert.deepEqual(p.log, ["start", "move", "before"]);
held!();
assert.deepEqual(p.log, ["start", "move", "before", "end"]);
p = press({ beforeEnd: () => p.log.push("before") });
await g.sleep(50);
p.on.touchend!({});
assert.deepEqual(p.log, ["abort", "end"]);
""")
        drag = read("components/ui/drag.tsx")
        self.assertNotIn("moveToLift", drag)
        # 「在拖」与「菜单不出」同一个距离、点按与长按菜单同一个时间：都取 hooks/use-long-press.ts 的，不另写一份
        self.assertIn('import { LONG_PRESS_MS, LONG_PRESS_SLOP } from "@/hooks/use-long-press";', drag)
        self.assertIn("export const TOUCH_DRAG_SLOP = LONG_PRESS_SLOP;", drag)
        self.assertIn("now() - this.pressedAt < LONG_PRESS_MS", drag)
        self.assertIn("useSensor(HoldTouchSensor, { ...TOUCH_START, beforeEnd }),", drag)
        press = read("hooks/use-long-press.ts")
        self.assertIn("export const LONG_PRESS_MS = 500;", press)
        self.assertIn("export const LONG_PRESS_SLOP = 10;", press)

    def test_long_press_menu_opens_only_while_the_lifted_row_stays_still(self):
        # 第 6 轮第 2 项：拿起后一直不动、按到菜单时间（500ms）才出菜单；拿起后动了就不出；菜单出来后手指再动（真的拖起来了）
        # 就收起菜单、接着拖。没在拖（搜索中不能拖、没存到服务端的行）照旧按 10px 判断是在滚
        self.run_page(r"""
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { useLongPress } from "@/hooks/use-long-press";
import { markDragMoved, setDragging } from "@/lib/layers";
const g = globalThis as any;
g.addEventListener = () => {};
g.removeEventListener = () => {};
g.getSelection = () => ({ removeAllRanges() {} });
let opened = 0;
let closed = 0;
let press: any;
renderToStaticMarkup(createElement(() => ((press = useLongPress(() => opened++, () => closed++)), null)));
const at = (y: number, timeStamp = 0) => ({ timeStamp, touches: [{ clientX: 10, clientY: y }], preventDefault() {} });
// 拿起了（250ms）、一直不动：500ms 出菜单
press.onTouchStart(at(10));
setDragging(true);
await g.sleep(560);
assert.deepEqual([opened, closed], [1, 0]);
// 菜单出来后手指又动了：共用拖动先记下在拖，这里收起菜单（只收一次）
markDragMoved();
press.onTouchMove(at(18));
press.onTouchMove(at(30));
assert.deepEqual([opened, closed], [1, 1]);
setDragging(false);
// 拿起后动了（慢慢动，没出 10px）：在拖，不出菜单
press.onTouchStart(at(10));
setDragging(true);
await g.sleep(300);
markDragMoved();
press.onTouchMove(at(18));
await g.sleep(260);
assert.equal(opened, 1);
setDragging(false);
// 不能拖的行（没拿起）：按住不动照旧出菜单，动出 10px 是在滚
press.onTouchStart(at(10));
await g.sleep(560);
assert.equal(opened, 2);
press.onTouchStart(at(10));
press.onTouchMove(at(40));
await g.sleep(560);
assert.equal(opened, 2);
""")
        # 侧栏的行、置顶行（同一个行组件）与公司组头都接上「动了就收起菜单」
        self.assertIn("const press = useLongPress(() => setMenuOpen(true), () => setMenuOpen(false));", read("components/assistant-ui/thread-list.tsx"))
        self.assertIn("const press = useLongPress(() => setMenuOpen(true), () => setMenuOpen(false));", read("components/assistant-ui/projects.tsx"))
        layers = read("lib/layers.ts")
        self.assertIn("export function markDragMoved(): void {", layers)
        self.assertIn("return moved && isDragging();", layers)

    def test_lifted_copy_keeps_the_ancestor_scoped_styles(self):
        # 第 5 轮审查：浮层挂在 body 上，克隆丢了祖先给的变量与规则（公司组里对话行的 --cc-row-indent、手机抽屉 .cc-drawer 下的
        # 内边距与字色），拿起时文字左跳、变淡；公司组克隆带着类上的组上间距，整块下移、底部被裁。现在克隆外面套一层层祖先的空壳
        # （只抄元素本身、display: contents 不占版面），克隆根的外边距清零
        self.run_page(r"""
import { cloneOf } from "@/components/ui/drag";
const g = globalThis as any;
type El = { tag: string; attrs: Map<string, string>; style: Map<string, string>; kids: El[]; parentElement: El | null; [k: string]: any };
function el(tag: string, attrs: Record<string, string> = {}, kids: El[] = []): El {
  const e: El = {
    tag, attrs: new Map(Object.entries(attrs)), style: new Map(), kids: [], parentElement: null,
    removeAttribute(n: string) { e.attrs.delete(n); },
    appendChild(k: El) { k.parentElement = e; e.kids.push(k); return k; },
    cloneNode(deep: boolean) {
      const c = el(tag, Object.fromEntries(e.attrs));
      for (const [k, v] of e.style) c.style.set(k, v);
      if (deep) for (const k of e.kids) c.appendChild(k.cloneNode(true));
      return c;
    },
    querySelectorAll() { const all: El[] = []; const walk = (x: El) => x.kids.forEach((k) => (all.push(k), walk(k))); walk(e); return all; },
  };
  e.style = Object.assign(new Map<string, string>(), {
    setProperty(n: string, v: string, p?: string) { (e.style as any).set(n, p ? `${v} !${p}` : v); },
    removeProperty(n: string) { (e.style as any).delete(n); },
  }) as any;
  for (const k of kids) e.appendChild(k);
  return e;
}
const html = el("html");
const body = html.appendChild(el("body"));
g.document.body = body;
g.document.documentElement = html;
// 抽屉 → 公司组 → 这组的行区域 → 一行（行里的开关是把手）
const sheet = body.appendChild(el("div", { role: "dialog", "data-state": "open", "aria-modal": "true" }));
const drawer = sheet.appendChild(el("div", { class: "cc-drawer", id: "drawer" }));
const section = drawer.appendChild(el("div", { class: "cc-group-section", "data-drop-target": "" }));
const items = section.appendChild(el("div", { "data-slot": "cc-project-items", class: "cc-group-items" }));
const row = items.appendChild(el("li", { "data-slot": "aui_thread-list-item", "data-row-id": "t1", "data-selected": "", "data-drag-source": "gap" },
  [el("button", { "data-slot": "aui_thread-list-item-trigger", "data-roving": "", "data-drag-handle": "", "aria-describedby": "dnd-1" })]));
row.style.set("transform", "translate3d(0, 4px, 0)");
row.style.set("transition", "transform 200ms");
const shell = cloneOf(row as any) as any as El;
// 外面是一层层祖先的空壳，从抽屉起（不含 body），各层不占版面、只带自己的类与 data-slot
const chain: El[] = [];
for (let at: El | undefined = shell; at; at = at.kids[0]) chain.push(at);
assert.deepEqual(chain.map((x) => x.tag), ["div", "div", "div", "div", "li", "button"]);
// 外壳不算一层弹层（按 role 数开着的层不多一份），状态类属性照留（样式按它）
assert.equal(chain[0]!.attrs.has("role"), false);
assert.equal(chain[0]!.attrs.has("aria-modal"), false);
assert.equal(chain[0]!.attrs.get("data-state"), "open");
chain.shift();
assert.deepEqual(chain.slice(0, 3).map((x) => x.attrs.get("class")), ["cc-drawer", "cc-group-section", "cc-group-items"]);
assert.equal(chain[2]!.attrs.get("data-slot"), "cc-project-items");
for (const wrap of chain.slice(0, 3)) {
  assert.equal(wrap.kids.length, 1);                              // 不带兄弟
  assert.equal(wrap.style.get("display"), "contents !important");
}
assert.equal(chain[0]!.attrs.has("id"), false);                   // 会被别处按属性找到的标记都去掉
assert.equal(chain[1]!.attrs.has("data-drop-target"), false);
// 克隆本身：去掉标记与拖动位移，外边距清零（盖过类上的组上间距）
const copy = chain[3]!;
for (const name of ["data-row-id", "data-selected", "data-drag-source"]) assert.equal(copy.attrs.has(name), false, name);
assert.equal(copy.attrs.get("data-slot"), "aui_thread-list-item");
assert.equal(copy.style.has("transform"), false);
assert.equal(copy.style.has("transition"), false);
assert.equal(copy.style.get("margin"), "0");
for (const name of ["data-roving", "data-drag-handle", "aria-describedby"]) assert.equal(chain[4]!.attrs.has(name), false, name);
// 原来那一项不动
assert.equal(row.parentElement, items);
assert.equal(row.attrs.get("data-row-id"), "t1");
""")

    def test_sidebar_drag_wiring(self):
        rows = read("components/assistant-ui/thread-list.tsx")
        groups = rows.split("const CompanyGroups: FC", 1)[1].split("\nconst DayGroups", 1)[0]
        self.assertIn("return moveDragged(sessions, group, bulk, (id) => {", groups)
        # 一个拖动区（手机按住 250ms 拿起，同会话栏卡片；长按菜单另见 use-long-press）；置顶一列、公司组一列；最近是落点；
        # 对话的空档换组时整列一起滑
        self.assertIn('<DragArea accepts={sidebarAccepts} onDrop={onDrop} onPlace={onPlace} onActive={onActive} flow={FLOW} texts={texts}>', groups)
        self.assertNotIn('touch="move"', rows)
        self.assertIn('<DragList id="pins" items={pinIds}>', groups)
        self.assertIn('<DragList id="companies" items={companyIds}>', groups)
        self.assertIn('const target = useDropTarget({ id: RECENTS_TARGET, kind: "recents", label: t("recents"), group: "" }, !open || !!fold || !!shut);', groups)
        # 公司组整块拖（组头开关是把手），搜索时不能调序；Alt+↑/↓ 挪一格
        self.assertIn('{ id: companyDrag(project.id), kind: "company", label: project.title, list: "companies", group: project.id },', groups)
        self.assertIn("{ disabled: locked, ownFocus: true, shut: !open || !!fold || !!shut },", groups)
        # Alt+↑/↓ 挪完念新位置（第 5 轮第 2 项：同键盘拖动的播报）
        self.assertIn("handle={drag.handle} onMove={locked ? undefined : (delta) => drag.moved(onMove(delta))}", groups)
        self.assertIn("onMove={(delta) => spotIn(saveCompanies(moveBy(companyIds, companyDrag(section.project.id), delta)), companyDrag(section.project.id))}", groups)
        # 拖对话时没有「最近」组就临时出一个（同一处渲染：空档进了它也不换组件）；多选里拖一条，其余选中的也变淡
        self.assertIn("const out = noRecents && (lifted || place?.group === \"\") ? withRecents(sections) : sections;", groups)
        self.assertIn("open={temp || open}\n              fold={leavesResults(\"\")}\n              shut={!!shutOutside?.has(section.key)}\n              onToggle={temp ? () => undefined : toggle}", groups)
        self.assertNotIn("recentsTarget", groups)
        self.assertIn('document.documentElement.dataset.ccDragBulk = "";', groups)
        # 行：置顶可排、公司里 / 最近的可拖到别的公司（没存到服务端的不能拖）；从「···」、输入框按下不拿起
        self.assertIn("const ROW_SKIP = '[data-slot=\"aui_thread-list-item-more\"], [role=\"menu\"], input';", rows)
        self.assertIn('useSortableItem({ id, kind: "pin", label: title, list: "pins" }, { disabled: scope.locked, ownFocus: true, skip: ROW_SKIP });', rows)
        self.assertIn("{ disabled: !remoteId, ownFocus: true, skip: ROW_SKIP },", rows)
        row = rows.split("\nconst Row: FC", 1)[1].split("\n};\n", 1)[0]
        self.assertIn("if (onMove && altMove(e, onMove)) return;", row)
        self.assertIn("onTouchStart={chain(press.onTouchStart, renaming ? undefined : dragTouch)}", row)
        self.assertIn("ref={handleRef}", row)
        # 长按菜单：拿起后手指动了（真的在拖）就不出、已经出来的收起；抽屉横滑不跟拖动抢
        self.assertIn("if (isDragMoving()) return;", read("hooks/use-long-press.ts"))
        self.assertIn("if (isDragging() && !g.locked) {", read("components/mobile-shell.tsx"))
        # 组头：开关是把手，菜单键优先，Alt+↑/↓ 其次，其余交给拖动
        header = read("components/assistant-ui/sidebar-group.tsx").split("export const GroupHeader: FC", 1)[1].split("\n);\n", 1)[0]
        self.assertIn("{...handle}", header)
        self.assertIn("if (onMove && altMove(e, onMove)) return;\n        handle?.onKeyDown?.(e);", header)
        # 三种语言的读屏播报
        i18n = read("i18n.ts")
        sidebar = block(i18n, "const SIDEBAR = {")
        for lang in ("en", "zh", "th"):
            words = keys(block(block(sidebar, f"  {lang}: {{"), "drag: {"))
            self.assertEqual(words, {"instructions", "pickedUp", "movedTo", "droppedAt", "cancelled", "over", "droppedIn", "items"}, lang)

    # ---- 用户 10-16「对话的拖动效果怎么没和其它拖动效果统一呢」：对话行与其他拖动同一套效果（拿起留空档、空档跟去别的组、
    # 那组让开原组合上、松手滑进空档、落位后才发请求、失败带过渡退回）。headless Chrome 实测帧数据见任务记录。

    def test_dragged_chat_gap_follows_the_sort_order(self):
        # 空档去别的组：按它移过去以后按排序方式排的位置（那组「显示更多」之外的放在已显示的最后），原组拿掉；名次、展开、
        # 「显示更多」不变；没有「最近」组时补一个；放下后那一组眼前的各行留在眼前（keep），不把最后一条挤进「显示更多」
        self.run_page(r"""
import { companySections, liftRow, withRecents } from "@/lib/managed-agents/sidebar-groups";
const at = (m: number) => Date.UTC(2026, 9, 16, 12, 0) - m * 60000;
const projects = [{ id: "A", title: "Alpha" }, { id: "B", title: "Beta" }];
const rows = [
  { index: 0, title: "a1", starred: false, time: at(1), group: "A", key: "s_a1" },
  { index: 1, title: "a2", starred: false, time: at(10), group: "A", key: "s_a2" },
  { index: 2, title: "a3", starred: false, time: at(20), group: "A", key: "s_a3" },
  ...[9, 16, 23, 30, 37, 44, 51].map((m, i) => ({ index: 3 + i, title: `b${i + 1}`, starred: false, time: at(m), group: "B", key: `s_b${i + 1}` })),
  { index: 10, title: "r1", starred: false, time: at(5), group: "", key: "s_r1" },
  { index: 11, title: "r2", starred: false, time: at(60), group: "", key: "s_r2" },
];
const base = companySections(rows, projects)!;
const of = (s: any[], key: string) => s.find((x) => x.key === key);
assert.deepEqual(of(base, "project:B").indices, [3, 4, 5, 6, 7]);            // 先显示 5 条
// a2（10 分钟前）拖到 B：排在 b1（9）与 b2（16）之间；A 拿掉它；B 的名次、「显示更多」不变
let s = liftRow(base, rows, 1, "B");
assert.deepEqual(of(s, "project:A").indices, [0, 2]);
assert.deepEqual(of(s, "project:A").all, [0, 2]);
assert.deepEqual(of(s, "project:B").indices, [3, 1, 4, 5, 6, 7]);
assert.equal(of(s, "project:B").rank, of(base, "project:B").rank);
assert.equal(of(s, "project:B").more, true);
assert.deepEqual(of(base, "project:A").indices, [0, 1, 2]);                   // 原来的不改
// 按名称升序：位置按名称算
s = liftRow(companySections(rows, projects, { sort: { by: "name", desc: false } })!, rows, 1, "B", { by: "name", desc: false });
assert.deepEqual(of(s, "project:B").indices, [1, 3, 4, 5, 6, 7]);             // 「a2」排在「b1」前
// r2（60 分钟前）排在 B 的「显示更多」之外：放在已显示的最后
s = liftRow(base, rows, 11, "B");
assert.deepEqual(of(s, "project:B").indices, [3, 4, 5, 6, 7, 11]);
assert.deepEqual(of(s, "recents").indices, [10]);
// 拖到「最近」：按排序方式插进最近（r1 5 分钟、a2 10 分钟、r2 60 分钟）
assert.deepEqual(of(liftRow(base, rows, 1, ""), "recents").indices, [10, 1, 11]);
// 眼前没有「最近」组：补一个（在最后）再排进去；只是拿着时补一个空的
const noRecents = companySections(rows.filter((r) => r.group), projects)!;
assert.equal(of(noRecents, "recents"), undefined);
assert.deepEqual(withRecents(noRecents).at(-1), { type: "recents", key: "recents", indices: [] });
assert.equal(withRecents(base), base);
assert.deepEqual(of(liftRow(noRecents, rows, 1, ""), "recents").indices, [1]);
// 回到原组 = 原样；去的组不在眼前（没有这家）= 原样
assert.deepEqual(of(liftRow(base, rows, 1, "A"), "project:A").indices, [0, 1, 2]);
assert.equal(liftRow(base, rows, 1, "Z"), base);
// 放下后那一组眼前的各行留在眼前：多了一条也不把 b5 挤进「显示更多」
const kept = companySections(rows.map((r) => (r.index === 1 ? { ...r, group: "B" } : r)), projects, { keep: ["s_b1", "s_a2", "s_b2", "s_b3", "s_b4", "s_b5"] })!;
assert.deepEqual(of(kept, "project:B").indices, [3, 1, 4, 5, 6, 7]);
assert.equal(of(kept, "project:B").more, true);
// 刚拖进来、排在「显示更多」之外的那一条：接在已显示的最后（落在哪留在哪）
const keptFar = companySections(rows.map((r) => (r.index === 11 ? { ...r, group: "B" } : r)), projects, { keep: ["s_r2"] })!;
assert.deepEqual(of(keptFar, "project:B").indices, [3, 4, 5, 6, 7, 11]);
// 在已显示的前面那一段里才按位置插：接在后面留着的（kept）不算
assert.deepEqual(of(liftRow(keptFar, rows, 1, "B"), "project:B").indices, [3, 1, 4, 5, 6, 7, 11]);
""")

    def test_where_the_gap_goes_and_what_is_announced(self):
        # 在组之间移动：指针落在别的能放的组 → 空档去那里；原来那组 → 回原位；哪组都不在（组与组之间、列外）→ 留在上一次的地方
        self.run_page(r"""
import { dragAnnouncements, isHome, isMover, placeOf } from "@/components/ui/drag";
import { sidebarAccepts } from "@/lib/managed-agents/sidebar-drag";
const row = { id: "row:t1", kind: "row", label: "Chat", group: "A" };
const company = (id: string) => ({ id: `company:${id}`, kind: "company", label: id, list: "companies", group: id });
const recents = { id: "group:recents", kind: "recents", label: "Recents", group: "" };
assert.equal(isMover(row), true);
assert.equal(isMover({ id: "pin:t1", kind: "pin", label: "x", list: "pins" }), false);   // 排序项不是
assert.equal(isMover({ id: "card", kind: "card", label: "x", list: "cards" }), false);
assert.equal(isHome(row, company("A")), true);
assert.equal(isHome(row, company("B")), false);
assert.deepEqual(placeOf(row, company("B"), sidebarAccepts), company("B"));
assert.deepEqual(placeOf(row, recents, sidebarAccepts), recents);
assert.equal(placeOf(row, company("A"), sidebarAccepts), null);
assert.equal(placeOf(row, null, sidebarAccepts), undefined);
assert.equal(placeOf(row, { id: "pin:t2", kind: "pin", label: "p", list: "pins" }, sidebarAccepts), undefined);
// 读屏：去别的组念「将移到」，去过别的组再回到原组念「将移到「原组」」（第 6 轮：原来念「已拿起」），拿起时那一次「经过」
// 原组不念（留着「已拿起」）；放下按空档所在：别的组念「已移到」，原组念取消
const texts = { instructions: "", pickedUp: (n: string) => `up ${n}`, movedTo: () => "moved", droppedAt: () => "dropped",
  cancelled: (n: string) => `cancel ${n}`, over: (n: string, g: string) => `over ${n} ${g}`, droppedIn: (n: string, g: string) => `in ${n} ${g}` };
let placed: any = null;
const say = dragAnnouncements(texts, { accepts: sidebarAccepts, placed: () => placed, lifted: () => row });
const entry = (d: any) => ({ id: d.id, data: { current: { drag: d } } });
assert.equal(say.onDragStart!({ active: entry(row) } as any), "up Chat");
assert.equal(say.onDragOver!({ active: entry(row), over: entry(company("A")) } as any), undefined);   // 拿起时经过原组：不念
assert.equal(say.onDragOver!({ active: entry({ ...row, group: "B" }), over: entry(company("B")) } as any), "over Chat B");   // 按拿起时的认
assert.equal(say.onDragOver!({ active: entry(row), over: entry(company("A")) } as any), "over Chat A");   // 拖回原组
assert.equal(say.onDragStart!({ active: entry(row) } as any), "up Chat");                            // 新的一次拿起：重新算
assert.equal(say.onDragOver!({ active: entry(row), over: entry(company("A")) } as any), undefined);
assert.equal(say.onDragOver!({ active: entry(row), over: null } as any), undefined);
placed = company("B");
assert.equal(say.onDragEnd!({ active: entry(row), over: null } as any), "in Chat B");
placed = null;
assert.equal(say.onDragEnd!({ active: entry(row), over: entry(company("B")) } as any), "cancel Chat");
""")

    def test_drop_lands_first_then_hands_over(self):
        # 放下：浮层落到空档（或收着的组的组头并收起），播完才交给调用方（发移动请求）；没播（被打断）时过一会儿自己交；
        # 减少动态效果时立即交；新拿起时先把没交的交掉
        self.run_page(r"""
import { dropAnimation, Landing } from "@/components/ui/drag";
const g = globalThis as any;
g.getComputedStyle = () => ({ transform: "none", transformOrigin: "0px 0px" });
const log: string[] = [];
const landing = new Landing();
landing.queue(() => log.push("first"), 30);
assert.equal(landing.pending, true);
landing.queue(() => log.push("second"), 30);                 // 新的一次：先把上一次交掉
assert.deepEqual(log, ["first"]);
await g.sleep(50);                                           // 没播落位：过一会儿自己交
assert.deepEqual(log, ["first", "second"]);
landing.land();                                              // 交过的不再交
assert.deepEqual(log, ["first", "second"]);

const node = (rect: any) => ({ dataset: {} as Record<string, string>, getBoundingClientRect: () => ({ ...rect, width: 200, right: rect.left + 200, bottom: rect.top + rect.height }), ownerDocument: null });
const animations: any[] = [];
const overlay = { ...node({ top: 466, left: 10, height: 32 }), animate(frames: any, opts: any) { const a: any = { frames, opts, startTime: null }; animations.push(a); return a; } };
const motion = { duration: 200, easing: "cubic-bezier(.32, .72, 0, 1)" };
g.document.timeline = { currentTime: 1000 };
const args = (active: any) => ({ active: { id: "row:t1", node: active, rect: { top: 398, left: 10, width: 200, height: 32 } },
  dragOverlay: { node: overlay, rect: { top: 466, left: 10, width: 200, height: 32 } }, transform: { x: 0, y: 186, scaleX: 1, scaleY: 1 } });
// 落到空档：同 dnd-kit 默认（浮层从放手处滑到落点，动效变量），落点那一项播完前不显示；播完先交、再显示
let landed = 0;
const gap = node({ top: 398, left: 10, height: 32 });
const done = dropAnimation(motion, () => { landed++; assert.equal("dragLanding" in gap.dataset, true); }, () => null)(args(gap) as any) as Promise<void>;
assert.equal(gap.dataset.dragLanding, "");
assert.equal(overlay.dataset.dropping, "");
assert.deepEqual(animations[0].opts, { duration: 200, easing: "cubic-bezier(.32, .72, 0, 1)", fill: "forwards" });
assert.equal(animations[0].frames[1].transform, "translate3d(0px, 118px, 0) scaleX(1) scaleY(1)");   // 186 - (466 - 398)
assert.equal(animations[0].startTime, null);                  // 结束拖动那次提交里才播的（取消）：从画出来的那一帧起算，不回拨
assert.equal(landed, 0);
animations[0].onfinish();
await done;
assert.equal(landed, 1);
assert.equal("dragLanding" in gap.dataset, false);
// 落点不是空档本身（去的组收着 → 组头；空档在可见范围外 → 可见范围的边上，见 clampIntoView）：落到那里并收起
// （data-dropping="into"），位置按 into 给的框
dropAnimation(motion, () => landed++, () => ({ top: 614, left: 10 }))(args(node({ top: 280, left: 10, height: 32 })) as any);
assert.equal(overlay.dataset.dropping, "into");
assert.equal(animations[1].frames[1].transform, "translate3d(0px, 334px, 0) scaleX(1) scaleY(1)");   // 186 + (614 - 466)
// 已经在落点：不播，立即交
const here = { ...args(node({ top: 466, left: 10, height: 32 })) };
here.active.rect = { top: 466, left: 10, width: 200, height: 32 };
assert.equal(dropAnimation(motion, () => landed++, () => null)({ ...here, transform: { x: 0, y: 0, scaleX: 1, scaleY: 1 } } as any), undefined);
assert.equal(landed, 2);
// 减少动态效果：不播，立即交
assert.equal(dropAnimation(null, () => landed++, () => null)(args(node({ top: 398, left: 10, height: 32 })) as any), undefined);
assert.equal(landed, 3);
assert.equal(animations.length, 2);
""")

    def test_release_starts_the_landing_and_ends_the_drag_after_it(self):
        # 第 6 轮第 1 项：松手那一下只起播落位（landOverlay：量好落点、Web Animations 只动 transform、从松手这一刻起算），
        # 结束拖动（拖动库与调用方的重画：整列重排、换组、量位置、发请求）等落位播完才做（ReleaseFirst）。原来落位在结束拖动那次
        # 提交里起播、又回拨到上一帧，侧栏重画一整列卡多久，帧一出来落位就走了多少（6 倍节流约 100ms 一帧不出、出来已到 92%）
        self.run_page(r"""
import { dropAnimation, landOverlay, ReleaseFirst, sortShift } from "@/components/ui/drag";
const g = globalThis as any;
g.getComputedStyle = (el: any) => ({ transform: el.computed ?? "none", transformOrigin: "0px 0px" });
Object.defineProperty(g, "performance", { value: { now: () => 5000 }, configurable: true });
const box = (top: number, extra: any = {}) => ({ dataset: {} as Record<string, string>, style: {} as Record<string, string>, isConnected: true,
  getBoundingClientRect: () => ({ top, left: 10, width: 200, height: 32, right: 210, bottom: top + 32 }), ...extra });
const animations: any[] = [];
const animate = (frames: any, opts: any) => {
  let done: (a: any) => void = () => undefined;
  const a: any = { frames, opts, startTime: null, playState: "running", finished: new Promise((resolve) => (done = resolve)) };
  a.finish = () => { a.playState = "finished"; done(a); };
  animations.push(a);
  return a;
};
const float = box(466);
const overlay = box(466, { computed: "matrix(1, 0, 0, 1, 0, 186)", children: { length: 1 }, firstElementChild: float, animate });
const motion = { duration: 200, easing: "cubic-bezier(.32, .72, 0, 1)" };
// 排序：那一项正由让位的位移挪在新位置上（新顺序还没提交）：落到版面位置 + 位移
const card = box(300, { style: { transform: "translate3d(0px, 34px, 0)" } });
assert.equal(sortShift(card as any), 34);
assert.equal(sortShift(box(1) as any), 0);
let asked: any[] = [];
const a = landOverlay(overlay as any, card as any, motion, (node, shift) => (asked.push(shift), null))!;
assert.deepEqual(asked, [34]);                                  // 落点按让位后的位置判断（可见范围的边、收着的组）
assert.equal(a.frames[0].transform, "translate3d(0px, 186px, 0) scaleX(1) scaleY(1)");
assert.equal(a.frames[1].transform, "translate3d(0px, 54px, 0) scaleX(1) scaleY(1)");   // 186 - (466 - (300 + 34))
assert.deepEqual(a.opts, { duration: 200, easing: "cubic-bezier(.32, .72, 0, 1)", fill: "forwards" });
assert.equal(a.startTime, 5000);                                // 从松手这一刻起算（不回拨到上一帧，也不等合成线程回报）
assert.equal(overlay.dataset.dropping, "");                     // 略放大、阴影随落位收回
// 落点不是那一项本身（收着的组的组头、可见范围的边）：落到那里并收起
const b = landOverlay(overlay as any, box(280) as any, motion, () => ({ top: 614, left: 10 }))!;
assert.equal(b.frames[1].transform, "translate3d(0px, 334px, 0) scaleX(1) scaleY(1)");  // 186 + (614 - 466)
assert.equal(overlay.dataset.dropping, "into");
// 已在落点、减少动态效果、量不到：不播（调用方立即结束拖动）
const count = animations.length;
assert.equal(landOverlay(overlay as any, box(466) as any, motion, () => null), null);
assert.equal(landOverlay(overlay as any, card as any, null, () => null), null);
assert.equal(landOverlay({ ...overlay, computed: "none" } as any, card as any, motion, () => null), null);
assert.equal(landOverlay(overlay as any, { ...card, isConnected: false } as any, motion, () => null), null);
assert.equal(animations.length, count);

// 拖动库随后结束拖动、要播它那一次：不再另播，接着等松手时起播的那一次；落点那一项在播完前不显示
let landed = 0;
const c = landOverlay(overlay as any, card as any, motion, () => null)!;
const before = animations.length;
const wait = dropAnimation(motion, () => landed++, () => null)({ active: { id: "card", node: card, rect: { top: 334, left: 10, width: 200, height: 32 } },
  dragOverlay: { node: overlay, rect: { top: 466, left: 10, width: 200, height: 32 } }, transform: { x: 0, y: 120, scaleX: 1, scaleY: 1 } } as any) as Promise<void>;
assert.equal(animations.length, before);
assert.equal(card.dataset.dragLanding, "");
assert.equal(landed, 0);
c.finish();
await wait;
assert.equal(landed, 1);
assert.equal("dragLanding" in card.dataset, false);
// 已经播完：立即交，不再播
const d = landOverlay(overlay as any, card as any, motion, () => null)!;
d.finish();
assert.equal(dropAnimation(motion, () => landed++, () => null)({ active: { id: "card", node: card, rect: { top: 334, left: 10, width: 200, height: 32 } },
  dragOverlay: { node: overlay, rect: { top: 466, left: 10, width: 200, height: 32 } }, transform: { x: 0, y: 54, scaleX: 1, scaleY: 1 } } as any), undefined);
assert.equal(landed, 2);
assert.equal(animations.length, before + 1);

// 先落位、再结束拖动
const listeners = new Map<string, () => void>();
g.addEventListener = (type: string, run: () => void) => listeners.set(type, run);
g.removeEventListener = (type: string) => listeners.delete(type);
const letGo = new ReleaseFirst();
const log: any[] = [];
letGo.hold(() => log.push(["end", letGo.landed]), null, 1000);           // 没播落位：立即结束
assert.deepEqual(log, [["end", false]]);
let frozen = 0;
const e = animate([], {});
letGo.hold(() => log.push(["end", letGo.landed]), e, 1000, () => (frozen++, () => frozen--));
assert.equal(letGo.waiting, true);
assert.equal(frozen, 1);                                         // 等着的时候自动滚动不滚
assert.deepEqual([...listeners.keys()].sort(), ["keydown", "pointerdown", "touchstart"]);
assert.equal(log.length, 1);                                     // 落位没播完：还没结束拖动
e.finish();
await g.sleep(0);
assert.deepEqual(log.at(-1), ["end", true]);                     // 播完才结束；这一次 landed（组之间移动的当场交给调用方）
assert.deepEqual([frozen, listeners.size, letGo.waiting, letGo.landed], [0, 0, false, false]);
// 落位中又按下了（要开始下一次）：当场结束，不算已落完；之后播完不再结束一次
const f = animate([], {});
letGo.hold(() => log.push(["again", letGo.landed]), f, 1000);
listeners.get("pointerdown")!();
assert.deepEqual(log.at(-1), ["again", false]);
f.finish();
await g.sleep(0);
assert.equal(log.filter((x) => x[0] === "again").length, 1);
// 动画没在走（页面在后台）：过 wait 毫秒自己结束
letGo.hold(() => log.push(["late", letGo.landed]), animate([], {}), 20);
await g.sleep(40);
assert.deepEqual(log.at(-1), ["late", false]);
// 新的一次放下：上一次还在等的先结束
letGo.hold(() => log.push(["first", letGo.landed]), animate([], {}), 1000);
letGo.hold(() => log.push(["second", letGo.landed]), null, 1000);
assert.deepEqual(log.slice(-2), [["first", false], ["second", false]]);
""")
        drag = read("components/ui/drag.tsx")
        # 落点在松手时量：排序时加上让位的位移；收着的组 / 可见范围外照旧（into 收 shift）
        self.assertIn("const rect = fold || !shift ? base : { ...base, top: base.top + shift, bottom: base.bottom + shift };", drag)
        # 卸下时还在等落位的放下当场结束
        self.assertIn("letGo.flush(false); // 卸下时还在等落位的放下当场结束", drag)

    def test_the_column_slides_when_the_gap_changes_group(self):
        # 空档换了组：位置变了的行、组头从原来的位置带过渡滑到新位置（FLIP，Web Animations 播 translate，不碰行内样式）；
        # 换组重挂的同一行按 data-drag-id 接着滑；空档、正在落位的不滑；滚动不算移动；正在滑的再变就从此刻的位置接着滑。
        # 审查：盯着的时候关掉滚动锚定（不然变动在可见区上方时浏览器改 scrollTop，没动的行先跳再滑回、下面的行直接跳），停止时还原；
        # 让位的起点不回拨到上一帧（同排序项的 CSS 过渡，主线程忙时不吃掉前段）
        self.run_page(r"""
import { flowLayout } from "@/components/ui/drag";
const g = globalThis as any;
let observe: (() => void) | null = null;
g.MutationObserver = class { constructor(run: () => void) { observe = run; } observe() {} disconnect() { observe = null; } };
const shifts = new Map<any, number>();
g.getComputedStyle = (el: any) => ({ translate: shifts.has(el) ? `0px ${shifts.get(el)}px` : "none" });
const root: any = { scrollTop: 0, style: { overflowAnchor: "" }, getBoundingClientRect: () => ({ top: 100 }), els: [] as any[], querySelectorAll: () => root.els };
g.document.timeline = { currentTime: 1000 };
const el = (top: number, id = "", still = false) => {
  const e: any = { top, dataset: id ? { dragId: id } : {}, anims: [] as any[], matches: () => still,
    getBoundingClientRect: () => ({ top: e.top + 100 - root.scrollTop + (shifts.get(e) ?? 0) }),
    animate(frames: any, opts: any) { const a: any = { frames, opts, startTime: null, cancel() { a.cancelled = true; } }; e.anims.push(a); return a; } };
  return e;
};
const a3 = el(68, "row:a3"), headB = el(118), b1 = el(152, "row:b1"), b2 = el(186, "row:b2"), gap = el(34, "row:a2", true);
root.els = [gap, a3, headB, b1, b2];
const motion = { duration: 200, easing: "ease" };
const stop = flowLayout(root, "x", motion);
assert.equal(root.style.overflowAnchor, "none");
// 空档从 A 去了 B（a2 换组重挂成新的一项）：A 合上（a3、B 组头、b1 上移 34），B 让开（b2 不动）
a3.top = 34; headB.top = 84; b1.top = 118;
const moved = el(152, "row:a2", true);
root.els = [a3, headB, b1, moved, b2];
observe!();
assert.deepEqual(a3.anims.map((x: any) => x.frames), [[{ translate: "0 34px" }, { translate: "0 0" }]]);
assert.deepEqual(a3.anims[0].opts, motion);
assert.equal(a3.anims[0].startTime, null);              // 起点不回拨（从画出来的那一帧起算）
assert.equal(headB.anims.length, 1);
assert.equal(b1.anims.length, 1);
assert.equal(b2.anims.length, 0);                     // 没动的不滑
assert.equal(moved.anims.length, 0);                   // 空档看不见，不滑
// 滚动不算移动
root.scrollTop = 50;
observe!();
assert.equal(a3.anims.length, 1);
// 正在滑（此刻还差 20px）又变了：从此刻看到的位置接着滑，不跳
shifts.set(a3, 20);
a3.top = 68;
observe!();
assert.equal(a3.anims[0].cancelled, true);
assert.deepEqual(a3.anims[1].frames, [{ translate: "0 -14px" }, { translate: "0 0" }]);   // 34 + 20 - 68
shifts.delete(a3);
// 失败退回：那一行换回原组（重挂成新的一项、看得见了），从它在 B 的位置滑回去
const back = el(34, "row:a2");
root.els = [back, a3, headB, b1, b2];
observe!();
assert.deepEqual(back.anims[0].frames, [{ translate: "0 118px" }, { translate: "0 0" }]);   // 152 - 34
stop();
assert.equal(observe, null);
assert.equal(root.style.overflowAnchor, "");          // 还原
""")

    def test_dragged_chat_rows_use_the_shared_gap_and_landing(self):
        drag = read("components/ui/drag.tsx")
        # 对话行：同排序项一样原位留空档（不再是淡影），换组重挂的同一行按 data-drag-id 认
        self.assertIn('node: { ref: draggable.setNodeRef, "data-drag-id": data.id, "data-drag-source": draggable.isDragging ? "gap" : undefined },', drag)
        self.assertNotIn('"ghost"', drag)
        # 在组之间移动：空档的组随指针（onDragOver → placeOf），放下按空档所在；落位播完才交（Landing），请求做完前整列仍跟着动
        self.assertIn("const target = placeOf(from, dataOf(over), latest.current.accepts);", drag)
        self.assertIn("settleMove(from, placedRef.current, latest.current.accepts, {", drag)   # 行为见 test_a_dropped_chat_is_handed_over_only_after_it_lands
        self.assertIn("place(from, null); // 取消：空档回原位，浮层滑回去", drag)
        # 拿起时的那份说明一直用到底（换组重挂的行 group 变了，原组仍按拿起时的）
        self.assertIn("lifted.current = data ?? null;", drag)
        # 收着的组：空档要去时整块高亮，落位落到组头
        self.assertIn("const crossing = useContext(PlacedContext) === data.id && !!options.shut;", drag)
        self.assertIn('return { ref: droppable.setNodeRef, "data-drop-target": placed && shut ? "" : undefined };', drag)
        self.assertIn("const fold = !!group && !group.contains(node);", drag)
        # 审查：落点在滚动区可见范围外时落到可见范围的边上并收起，不飞出列表；拖动库那一次的 scrollIntoView 不让滚
        self.assertIn("const shown = clampIntoView(rect, viewOf(node));", drag)
        self.assertIn("return fold || shown !== rect ? shown : null;", drag)
        self.assertEqual(drag.count("holdStill(stash.current.node, landWait);"), 2)   # 松手、取消
        # 审查：空档换组后在可见范围外就平滑滚进来
        self.assertIn("if (gap?.isConnected) revealGap(gap, !!transition);", drag)
        # 整列一起滑只在组之间移动的那段时间；空档换组后重量各组（碰撞按新位置）
        self.assertIn("{flow && <Flow selector={flow} running={busy > 0} transition={transition} placed={placed?.id ?? null} />}", drag)
        self.assertIn("measureDroppableContainers(containers.current.getEnabled().map((entry) => entry.id));", drag)
        # 第 6 轮：落位在松手那一下起播（landOverlay，从松手这一刻起算），结束拖动等落位播完（ReleaseFirst）；不再回拨到上一帧
        # （startNow：结束拖动那次提交卡多久，帧一出来落位就走了多少）。整列让位同样不回拨（同排序项的 CSS 过渡）
        self.assertNotIn("startNow", drag)
        self.assertIn("fromNow(animation);", drag)
        self.assertIn("useSensor(LandingMouseSensor, { activationConstraint: MOUSE_START, beforeEnd }),", drag)
        self.assertIn("useSensor(LandingKeyboardSensor, { coordinateGetter: coordinates, keyboardCodes: KEYS, beforeEnd }),", drag)
        self.assertIn("letGo.hold(end, animation, landWait, node ? () => freezeScroll(node) : undefined);", drag)
        # 先量再改根节点的拖动标记（反过来量时整页样式、布局重算：6 倍节流 25ms）；落位已播完的在组之间移动当场交给调用方
        end = drag.split("const onDragEnd = ({ active, over }: DragEndEvent) => {", 1)[1].split("\n  };\n", 1)[0]
        self.assertLess(end.index("holdStill(stash.current.node, landWait);"), end.index("finish();"))
        self.assertIn("if (queued && landed) landing.land();", end)
        cancel = drag.split("onDragCancel={({ active }) => {", 1)[1].split("}}", 1)[0]
        self.assertLess(cancel.index("holdStill(stash.current.node, landWait);"), cancel.index("finish();"))
        rows = read("components/assistant-ui/thread-list.tsx")
        groups = rows.split("const CompanyGroups: FC", 1)[1].split("\nconst DayGroups", 1)[0]
        self.assertIn("const FLOW = '[data-slot=\"aui_thread-list-item\"], .cc-group-row, .cc-show-more';", rows)
        # 空档只在去的组开着时排进去（收着的只高亮组头、原位空档留着）；按当前排序方式
        self.assertIn("return index >= 0 && open && stays ? liftRow(out, rows, index, place.group, { by: view.sort, desc: view.desc }) : out;", groups)
        # 搜索时移过去就不在结果里的：不排空档，同收着的组
        self.assertIn("const stays = !searching || (!!row && matchesAfterMove(row, place.group, projects, query));", groups)
        self.assertIn("fold={leavesResults(section.project.id)}", groups)
        self.assertIn('setPlace(active.kind === "row" && target ? { row: fromDrag(active.id), group: target.group ?? "" } : null);', groups)
        # 多选整批：拖的这一条先移（它已在空档里），其余逐条跟过去；放下后刚拖进去的这一行留在眼前（keepInView：那组多显示一条
        # 或单独留这一条），被拒就撤掉；换搜索、排序、分组或点「显示更多」清掉留着的
        self.assertIn("const { sessions, bulk } = draggedSessions(active, useSidebarSelection.getState().ids, (id) => itemsById.get(id)?.remoteId);", groups)
        self.assertIn("const moving = notIn(sessions, group).flatMap((id) => (indexOf.has(id) ? [indexOf.get(id)!] : []));", groups)
        self.assertIn("const { bump, keep } = keepInView(into, threadIds.indexOf(fromDrag(active.id)), moving, rows, shownOf(group), searching, sort);", groups)
        self.assertIn('const undo = new Map(bump.map((index) => [keyOf.get(index) ?? "", showOneMore(group)]));', groups)
        self.assertIn("undo.get(id)?.();", groups)
        self.assertIn("useEffect(() => setKept((list) => (list.length ? [] : list)), [query, view.by, view.sort, view.desc]);", groups)
        self.assertIn("setKept((list) => list.filter((entry) => entry.group !== section.project.id));", groups)
        self.assertNotIn("into?.indices ?? []", groups)
        css = read("globals.css")
        # 多选里一起拖的其余各行淡影；拖的那一条自己是空档（不被淡影规则盖住）；落到收着的组收起
        self.assertIn(':root[data-cc-drag-bulk] [data-slot="aui_thread-list-item"][data-selected]:not([data-drag-source], [data-drag-landing]) { opacity: var(--cc-drag-ghost); }', css)
        self.assertIn('.cc-drag-overlay[data-dropping="into"] .cc-drag-lift { scale: var(--cc-drag-fold); opacity: 0; }', css)
        self.assertNotIn('[data-drag-source="ghost"]', css)

    # ---- 审查（对话拖动统一之后）：落位后才交、拖回原组不交、失败退回；放下后刚拖进去的那一行留在眼前（不整组留、会清掉）；
    # 留着的那一段按排序方式排；搜索时移过去就不在结果里的同收着的组；落点不出可见范围

    def test_a_dropped_chat_is_handed_over_only_after_it_lands(self):
        # 共用拖动在组之间移动的放下（settleMove）：落位动画（dropAnimation）播完之前不交给调用方（不发移动请求）；拖回原组 /
        # 取消不交；交过才把空档清回 null；请求失败照样收尾（整列停止跟着动）；没播落位时过一会儿自己交
        self.run_page(r"""
import { dropAnimation, Landing, settleMove } from "@/components/ui/drag";
import { sidebarAccepts } from "@/lib/managed-agents/sidebar-drag";
const g = globalThis as any;
g.getComputedStyle = () => ({ transform: "none", transformOrigin: "0px 0px" });
const row = { id: "row:t1", kind: "row", label: "x", group: "comp_A", session: "sesn_1" };
const company = (id: string) => ({ id: `company:${id}`, kind: "company", label: id, list: "companies", group: id });
let log: any[] = [];
const landing = new Landing();
const hooks = (work?: () => unknown, wait = 1000) => ({ landing, wait, drop: (target: any) => { log.push(["drop", target.group]); return work?.(); },
  clear: () => log.push(["clear"]), done: () => log.push(["done"]) });
// 空档在原组（null）：不交，空档回原位、收尾
assert.equal(settleMove(row, null, sidebarAccepts, hooks()), false);
assert.deepEqual(log, [["clear"], ["done"]]);
// 不能放的（原来那一组）：同样不交
log = [];
assert.equal(settleMove(row, company("comp_A"), sidebarAccepts, hooks()), false);
assert.deepEqual(log, [["clear"], ["done"]]);
// 别的组：排上，落位播完之前不交；落位动画的 onfinish 一到才交（发请求），交过才清空档；请求做完才收尾
log = [];
let finish: () => void = () => undefined;
const request = new Promise<void>((resolve) => (finish = resolve));
assert.equal(settleMove(row, company("comp_B"), sidebarAccepts, hooks(() => request)), true);
const animations: any[] = [];
const box = (top: number) => ({ dataset: {} as Record<string, string>, getBoundingClientRect: () => ({ top, left: 10, width: 200, height: 32, right: 210, bottom: top + 32 }) });
const overlay = { ...box(466), animate(frames: any, opts: any) { const a: any = { frames, opts, startTime: null }; animations.push(a); return a; } };
const motion = { duration: 200, easing: "ease" };
dropAnimation(motion, () => landing.land(), () => null)({ active: { id: "row:t1", node: box(398), rect: { top: 398, left: 10, width: 200, height: 32 } },
  dragOverlay: { node: overlay, rect: { top: 466, left: 10, width: 200, height: 32 } }, transform: { x: 0, y: 186, scaleX: 1, scaleY: 1 } } as any);
assert.deepEqual(log, []);                                    // 还在落：没有请求
assert.equal(landing.pending, true);
animations[0].onfinish();
assert.deepEqual(log, [["drop", "comp_B"], ["clear"]]);       // 落完：发请求，空档清回（调用方已按移过去的样子排好）
await g.sleep(0);
assert.deepEqual(log.at(-1), ["clear"]);                      // 请求没回：整列仍跟着动
finish();
await g.sleep(0);
assert.deepEqual(log.at(-1), ["done"]);
// 请求失败：照样收尾，不往外抛
log = [];
settleMove(row, company("comp_C"), sidebarAccepts, hooks(() => Promise.reject(new Error("no"))));
landing.land();
await g.sleep(0);
assert.deepEqual(log, [["drop", "comp_C"], ["clear"], ["done"]]);
// 落位没播（被打断、量不到）：过 wait 自己交
log = [];
settleMove(row, company("comp_B"), sidebarAccepts, hooks(undefined, 20));
assert.deepEqual(log, []);
await g.sleep(40);
assert.deepEqual(log, [["drop", "comp_B"], ["clear"], ["done"]]);
""")

    def test_dragged_chat_moves_first_and_a_refused_move_goes_back(self):
        # 放下后要移的：拖的这一条在前，多选整批其余随后（还没存到服务端的跳过）；逐条、不并发，已在目标公司的不发；
        # 被拒的那条退回原组（filingOf），侧栏出错行写服务端原文，并告诉侧栏是哪一条（撤掉为它多显示的一条）
        self.run_page(r"""
const g = globalThis as any;
import { draggedSessions, moveDragged } from "@/lib/managed-agents/sidebar-drag";
import { filingOf, noteFilings, useProjects } from "@/lib/managed-agents/projects";
const remote: Record<string, string> = { t1: "sesn_1", t2: "sesn_2", t3: "sesn_3" };
const row = (count?: number) => ({ id: "row:t2", kind: "row", label: "x", group: "comp_A", session: "sesn_2", count });
// 一条：只有它
assert.deepEqual(draggedSessions(row(), ["t1", "t3"], (id) => remote[id]), { sessions: ["sesn_2"], bulk: false });
// 多选里拖其中一条：拖的这条在前，其余按选中的先后；本页新建还没存的（t4）跳过
assert.deepEqual(draggedSessions(row(3), ["t1", "t2", "t3", "t4"], (id) => remote[id]), { sessions: ["sesn_2", "sesn_1", "sesn_3"], bulk: true });
// 拖的这条不在多选里：只移它
assert.deepEqual(draggedSessions(row(2), ["t1", "t3"], (id) => remote[id]), { sessions: ["sesn_2"], bulk: false });

const ok = (body: any) => ({ ok: true, status: 200, json: async () => ({ message: body }) });
const refused = JSON.stringify([JSON.stringify({ message: "ไม่มีสิทธิ์ในบริษัทนี้" })]);
let inFlight = 0;
let most = 0;
g.respond = async (method: string, body: any) => {
  if (method !== "move") return ok(null);
  inFlight++;
  most = Math.max(most, inFlight);
  await g.sleep(3);
  inFlight--;
  if (body.session_id === "sesn_2") return { ok: false, status: 417, json: async () => ({ _server_messages: refused }) };
  return ok({ id: body.session_id, project: body.project, period: body.period, scope: body.scope });
};
useProjects.setState({ kind: "company", period: true, scope: true } as any);
noteFilings({ sesn_1: { project: "comp_A", period: "", scope: "" }, sesn_2: { project: "comp_A", period: "2026-09", scope: "" },
              sesn_3: { project: "comp_B", period: "2026-09", scope: "00012" } });
const failed: string[] = [];
const moving = moveDragged(["sesn_2", "sesn_1", "sesn_3"], "comp_B", true, (id) => failed.push(id));
assert.equal(filingOf("sesn_2").project, "comp_B");          // 本页先挪过去（落位时已在空档里）
await moving;
const moves = g.calls.filter((c: any) => c.method === "move").map((c: any) => c.body.session_id);
assert.deepEqual(moves, ["sesn_2", "sesn_1"]);               // 拖的那条第一个；已在目标公司的 sesn_3 不发
assert.equal(most, 1);                                        // 不并发
assert.deepEqual(failed, ["sesn_2"]);
assert.deepEqual(filingOf("sesn_2"), { project: "comp_A", period: "2026-09", scope: "" });   // 退回原组
assert.equal(filingOf("sesn_1").project, "comp_B");
assert.deepEqual(filingOf("sesn_3"), { project: "comp_B", period: "2026-09", scope: "00012" });
assert.equal(useProjects.getState().error, "ไม่มีสิทธิ์ในบริษัทนี้");
""")

    def test_a_dropped_chat_stays_in_view_without_growing_the_group(self):
        # 放下后移进去的各行（拖的这一条、多选整批的其余各条）：排在眼前最后一条之前的每一条让那组多显示一条（同「显示更多」的计数，
        # 被拒可撤），眼前原有的一条都不被挤进「显示更多」；排在「显示更多」之外的单独留着（keep），不再把整组眼前的行都留下；
        # 搜索时、最近、收着没排进去、那组放得下的都不用。留着的那一段按排序方式排，拖动中的摆法（liftRow）与放下后
        # （companySections）一致，连着两次拖进同一组也不换位
        self.run_page(r"""
import { companySections, keepInView, liftRow, matchesAfterMove } from "@/lib/managed-agents/sidebar-groups";
import { shownOf, showMore, showOneMore, useProjects } from "@/lib/managed-agents/projects";
const at = (m: number) => Date.UTC(2026, 9, 16, 12, 0) - m * 60000;
const projects = [{ id: "A", title: "Alpha" }, { id: "B", title: "Beta" }];
const base = [
  { index: 0, title: "a1", starred: false, time: at(1), group: "A", key: "s_a1" },
  { index: 1, title: "a2", starred: false, time: at(10), group: "A", key: "s_a2" },
  { index: 2, title: "x", starred: false, time: at(47), group: "A", key: "s_x" },
  ...[9, 16, 23, 30, 37, 44, 51].map((m, i) => ({ index: 3 + i, title: `b${i + 1}`, starred: false, time: at(m), group: "B", key: `s_b${i + 1}` })),
  { index: 10, title: "r2", starred: false, time: at(60), group: "", key: "s_r2" },
  { index: 11, title: "a3", starred: false, time: at(20), group: "A", key: "s_a3" },
];
const of = (s: any[], key: string) => s.find((x) => x.key === key);
const moved = (rows: any[], indices: number[], group: string) => rows.map((r) => (indices.includes(r.index) ? { ...r, group } : r));
const keepOf = (s: any[], dragged: number, moving: number[], shown = 5, searching = false) => keepInView(of(s, "project:B"), dragged, moving, base, shown, searching);

// a2（10 分钟前）拖进 B：排在眼前最后一条（b5）之前 → 多显示一条
let s = liftRow(companySections(base, projects)!, base, 1, "B");
assert.deepEqual(keepOf(s, 1, [1]), { bump: [1], keep: [] });
assert.equal(shownOf("B"), 5);
const undo = showOneMore("B");
assert.equal(shownOf("B"), 6);
let after = companySections(moved(base, [1], "B"), projects, { shown: useProjects.getState().shown })!;
assert.deepEqual(of(after, "project:B").indices, of(s, "project:B").indices);   // 落位后同拖动中的摆法：b5 没被挤走
assert.deepEqual(of(after, "project:B").indices, [3, 1, 4, 5, 6, 7]);
undo();                                                       // 被拒、退回原组：撤掉
assert.equal(shownOf("B"), 5);
undo();                                                       // 撤过的不再撤
assert.equal(shownOf("B"), 5);
const again = showOneMore("B");
void showMore("B", 7, async () => undefined);                 // 这期间点了「显示更多」：那是人自己要的，不撤
again();
assert.equal(shownOf("B"), 16);
useProjects.setState({ shown: {} } as any);

// 多选整批 a2、a3（20 分钟前）拖进 B：两条都排在 b5 之前 → 多显示两条，落位、跟过去以后 b5 仍在眼前
assert.deepEqual(keepOf(s, 1, [1, 11]), { bump: [1, 11], keep: [] });
after = companySections(moved(base, [1, 11], "B"), projects, { shown: { B: 7 } })!;
assert.deepEqual(of(after, "project:B").indices, [3, 1, 4, 11, 5, 6, 7]);
// 整批里 a2 在前、x（47 分钟前）排在「显示更多」之外：a2 多显示一条，x 单独留着
assert.deepEqual(keepOf(s, 1, [1, 2]), { bump: [1], keep: [2] });

// r2（60 分钟前）拖进 B：排在「显示更多」之外 → 单独留这一条（只这一条，不是整组）
s = liftRow(companySections(base, projects)!, base, 10, "B");
assert.deepEqual(keepOf(s, 10, [10]), { bump: [], keep: [10] });
assert.deepEqual(of(s, "project:B").indices, [3, 4, 5, 6, 7, 10]);
let rows = moved(base, [10], "B");
after = companySections(rows, projects, { keep: ["s_r2"] })!;
assert.deepEqual(of(after, "project:B").indices, [3, 4, 5, 6, 7, 10]);
assert.equal(of(after, "project:B").more, true);
// 接着把 x（47 分钟前）也拖进 B：留着的那一段按排序方式排（x 在 r2 前），拖动中与落位后一样，不换位
s = liftRow(after, rows, 2, "B");
assert.deepEqual(of(s, "project:B").indices, [3, 4, 5, 6, 7, 2, 10]);
assert.deepEqual(keepInView(of(s, "project:B"), 2, [2], rows, 5, false), { bump: [], keep: [2] });
after = companySections(moved(rows, [2], "B"), projects, { keep: ["s_r2", "s_x"] })!;
assert.deepEqual(of(after, "project:B").indices, [3, 4, 5, 6, 7, 2, 10]);
// 没有留着的：换排序方式（调用方清掉 keep）后只显示前 5 条，不多显示
assert.equal(of(companySections(moved(rows, [2], "B"), projects, { sort: { by: "name", desc: false } })!, "project:B").indices.length, 5);

// 不用的：搜索时（全显示）、最近（不限条数）、去的组收着（没排进去）、那组放得下
s = liftRow(companySections(base, projects)!, base, 1, "B");
assert.deepEqual(keepOf(s, 1, [1], 5, true), { bump: [], keep: [] });
assert.deepEqual(keepInView(of(liftRow(companySections(base, projects)!, base, 1, ""), "recents"), 1, [1], base, 5, false), { bump: [], keep: [] });
assert.deepEqual(keepOf(companySections(base, projects)!, 1, [1]), { bump: [], keep: [] });
const small = liftRow(companySections(base, projects)!, base, 3, "A");
assert.deepEqual(keepInView(of(small, "project:A"), 3, [3], base, 5, false), { bump: [], keep: [] });   // A 连它 5 条，放得下
// 原来那组没满：先占空位，满了才多显示（A 有 4 条，拖进两条都排在前面 → 多显示一条）
const two = companySections(base.map((r) => (r.index === 11 ? { ...r, group: "A" } : r)), projects)!;
assert.deepEqual(keepInView(of(liftRow(two, base, 3, "A"), "project:A"), 3, [3, 4], base, 5, false), { bump: [3], keep: [] });

// 搜索时移过去还在不在结果里：标题或那家公司名命中
const hit = { index: 2, title: "x", starred: false, time: at(47), group: "A", key: "s_x" };
assert.equal(matchesAfterMove(hit, "B", projects, "alp"), false);   // 靠公司名 Alpha 命中的，移到 Beta 就不在了
assert.equal(matchesAfterMove(hit, "A", projects, "alp"), true);
assert.equal(matchesAfterMove(hit, "B", projects, "bet"), true);
assert.equal(matchesAfterMove({ ...hit, title: "alpine" }, "", projects, "alp"), true);
assert.equal(matchesAfterMove(hit, "", projects, "alp"), false);
assert.equal(matchesAfterMove(hit, "B", projects, ""), true);       // 不搜索都在
""")

    def test_the_landing_stays_inside_the_list(self):
        # 落点在滚动区可见范围外：落到可见范围的边上（上下边贴边），在里面的不动
        self.run_page(r"""
import { clampIntoView, inView } from "@/components/ui/drag";
const view = { top: 88, bottom: 700 };
const rect = (top: number) => ({ top, left: 10, width: 200, height: 34, right: 210, bottom: top + 34 });
assert.equal(inView(rect(88), view), true);
assert.equal(inView(rect(666), view), true);
assert.equal(inView(rect(87.8), view), true);                // 差半像素不算出界
assert.equal(inView(rect(48), view), false);
assert.equal(inView(rect(680), view), false);
const inside = rect(300);
assert.equal(clampIntoView(inside, view), inside);           // 在里面：同一个框
assert.deepEqual(clampIntoView(rect(48), view), rect(88));   // 在上面：贴上边
assert.deepEqual(clampIntoView(rect(900), view), rect(666)); // 在下面：贴下边
assert.deepEqual(clampIntoView({ ...rect(10), height: 800 }, view).top, 88);   // 比范围还高：贴上边
""")

    def test_new_chat_puts_the_cursor_in_the_composer(self):
        # 测试窗口：点组头「＋」新建后焦点停在按钮上。照 Claude，新建后光标直接进输入框（侧栏、顶部新建、公司页、手机）
        projects = read("components/assistant-ui/projects.tsx")
        self.assertIn('const COMPOSER_INPUT = "textarea.aui-composer-input";', projects)
        start = projects.split("export function useStartIn()", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("if (!send) focusComposer();", start)
        self.assertIn("else focusComposer(false);", start)
        self.assertIn("focusComposer();", read("components/assistant-ui/thread-list.tsx").split("export const ThreadListNew", 1)[1])
        self.run_page(r"""
import { focusComposer } from "@/components/assistant-ui/projects";
const g = globalThis as any;
let focused = 0;
g.document.querySelector = (q: string) => (q === "textarea.aui-composer-input" ? { focus: () => focused++ } : null);
g.requestAnimationFrame = (run: () => void) => setTimeout(run, 0);
focusComposer();
assert.equal(focused, 1);           // 点击当下就聚焦（手机上这样才弹键盘）
await g.sleep(20);
assert.equal(focused, 2);           // 新对话换上来后再聚焦一次
""")


class UserAsks20261016Tests(unittest.TestCase):
    """用户 2026-10-16 三件：单行放不下统一末尾渐隐；聚焦不要光晕；侧栏不列选了文件还没发的空对话。"""

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_single_lines_fade_only_when_they_do_not_fit(self):
        # 「一行放不下时，侧栏末尾是渐隐，右侧栏是省略号，统一渐隐」：整页一个 ResizeObserver 量框与文字，放不下才设 data-overflow
        # （CSS 用 mask 渐隐末尾）；放得下（含按内容收缩宽度、刚好等宽、只差取整误差）末尾不淡；文字变了、框变了重量
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { attachOverflowFade, markOverflow, overflowOf, watchOverflow } from "@/lib/overflow-fade";

const g = globalThis as any;
g.getComputedStyle = (el: any) => ({ paddingLeft: el.pad ?? "0px", paddingRight: el.pad ?? "0px", getPropertyValue: () => "" });
const observers: any[] = [];
g.ResizeObserver = class {
  run: (entries: any[]) => void;
  seen = new Set<any>();
  constructor(run: (entries: any[]) => void) { this.run = run; observers.push(this); }
  observe(el: any) { this.seen.add(el); }
  unobserve(el: any) { this.seen.delete(el); }
  fire(els: any[]) { this.run(els.filter((el) => this.seen.has(el)).map((target) => ({ target }))); }
};
let writes = 0;
function marquee(text: number, client: number, pad?: string) {
  const attrs = new Map<string, string>();
  const inner: any = { width: text, getBoundingClientRect: () => ({ width: inner.width * box.scale }) };
  const box: any = {
    isConnected: true, clientWidth: client, offsetWidth: client, scale: 1, pad, attrs, inner,
    getBoundingClientRect: () => ({ width: box.offsetWidth * box.scale }),
    querySelector: (q: string) => (q === ":scope > .cc-marquee-text" ? inner : null),
    hasAttribute: (n: string) => attrs.has(n),
    toggleAttribute: (n: string, on: boolean) => { writes++; on ? attrs.set(n, "") : attrs.delete(n); return on; },
  };
  return box;
}
const fades = (box: any) => box.attrs.has("data-overflow");

const long = marquee(300, 200);          // 放不下
const short = marquee(150, 200);         // 放得下（比框窄）
const hug = marquee(120, 120);           // 按内容收缩宽度：框 = 文字宽
const edge = marquee(200.4, 200);        // 只差取整误差
const one = marquee(201, 200);           // 多 1px 就算放不下
const padded = marquee(190, 200, "8px"); // 文字 190 + 左右内边距 16 > 框 200
const inline = marquee(300, 0);          // 行内元素（不截断）
const boxes = [long, short, hug, edge, one, padded, inline];
const detach = boxes.map((box) => attachOverflowFade(box));
assert.equal(observers.length, 1);                          // 整页一个观察器，不是每一行一个
const ro = observers[0];
assert.ok(boxes.every((box) => ro.seen.has(box) && ro.seen.has(box.inner)));   // 框与文字两层都观察
ro.fire(boxes);
assert.deepEqual(boxes.map(fades), [true, false, false, false, true, true, false]);
assert.equal(overflowOf(long), 100);
// 文字变短（改名、计数变了）→ 文字那层尺寸变，去掉渐隐；框变窄 → 加上
long.inner.width = 180;
ro.fire([long.inner]);
assert.equal(fades(long), false);
short.clientWidth = short.offsetWidth = 100;
ro.fire([short]);
assert.equal(fades(short), true);
// 拖动浮层放大 1.02：量到的宽同比放大，按缩放换回版面宽，放得下的不因此渐隐
hug.scale = 1.02;
ro.fire([hug]);
assert.equal(fades(hug), false);
// 没变的不写属性（只在变了时写）
writes = 0;
ro.fire(boxes);
assert.equal(writes, 0);
// 卸下：两层都不再观察
detach[0]!();
assert.ok(!ro.seen.has(long) && !ro.seen.has(long.inner));
// 表格（结果文件预览，最多 500 × 40 格）：整张表一处登记，表变了把所有格子重量（格子没有里层文字，比 scrollWidth）
const cell = (scroll: number, client: number) => ({ ...marquee(0, client), querySelector: () => null, scrollWidth: scroll });
const cells = [cell(300, 288), cell(80, 120)];
const table = {};
const off = watchOverflow([table], () => cells);
assert.equal(observers.length, 1);
ro.fire([table]);
assert.deepEqual(cells.map(fades), [true, false]);
off();
assert.ok(!ro.seen.has(table));
markOverflow([]);
g.done();
""")

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_sidebar_hides_unsent_drafts_but_keeps_the_open_one(self):
        # 选了文件但还没发的空对话（输入框加附件要先建会话）不进侧栏行；当前打开的那条（或与它同一个服务端编号的另一行）照常显示
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { unsentDrafts } from "@/lib/managed-agents/sidebar-groups";
const items = [
  { id: "a", remoteId: "r1", title: "Invoice 9" },
  { id: "b", remoteId: "r2", title: "" },      // 选了文件、没发
  { id: "c", remoteId: "r3" },                 // 标题还没有
  { id: "d", remoteId: "r4", title: "  " },
  { id: "e", remoteId: "r2", title: "" },      // 同一段对话重拉列表后的另一行
  { id: "f", title: "" },                      // 本页新建、还没存到服务端
];
const sorted = (set: Set<string>) => [...set].sort();
assert.deepEqual(sorted(unsentDrafts(items, "a")), ["b", "c", "d", "e", "f"]);
assert.deepEqual(sorted(unsentDrafts(items, "b")), ["c", "d", "f"]);   // 当前打开的 b 与它的另一行 e 照常显示
assert.deepEqual(sorted(unsentDrafts(items, "f")), ["b", "c", "d", "e"]);
assert.deepEqual(sorted(unsentDrafts(items, undefined)), ["b", "c", "d", "e", "f"]);
(globalThis as any).done();
""")
        rows = read("components/assistant-ui/thread-list.tsx")
        # 侧栏拼行的两处（按公司 / 月份 / 不分组的 Cowork、按日子的 ERP / DMS）都滤；只在拼行时滤，不动服务端列表与同步
        self.assertIn("return useMemo(() => unsentDrafts(threadItems, mainId), [threadItems, mainId]);", rows)
        self.assertIn("      if (unsent.has(id)) return [];", rows.split("const CompanyGroups: FC", 1)[1].split("\nconst DayGroups", 1)[0])
        self.assertIn("          !unsent.has(id) &&", rows.split("const DayGroups: FC", 1)[1])
        for rel in ("lib/managed-agents/list-sync.ts", "lib/managed-agents/session-list-adapter.ts", "lib/managed-agents/projects.ts"):
            self.assertNotIn("unsent", read(rel), rel)

    def test_every_single_line_truncation_fades(self):
        marquee = read("components/ui/marquee.tsx")
        fade = read("lib/overflow-fade.ts")
        css = read("globals.css")
        self.assertIn("useEffect(() => (ref.current ? attachOverflowFade(ref.current) : undefined), [ref]);", marquee)
        self.assertEqual(fade.count("new ResizeObserver"), 1)
        self.assertIn("export const overflows = (over: number) => over > 0.5;", fade)
        # 静止时：放不下才渐隐（宽度沿用侧栏的 --cc-row-fade），不用省略号；文字一直是 inline-block（观察器量得到它）
        self.assertIn(".cc-marquee-text { display: inline-block; white-space: nowrap; }", css)
        self.assertIn("mask-image: linear-gradient(to right, black calc(100% - var(--cc-marquee-end)), transparent);", css)
        self.assertNotIn("ellipsis", css.replace("各组件类里的省略号", ""))
        self.assertNotIn("text-overflow: ellipsis", css)
        # 原来还在用 truncate / 省略号的单行：卡头摘要、列表页标题、对话框标题、菜单项、设置里的 Express 路径、工作状态行、表格格子
        self.assertIn('<Marquee id={summaryId} className="cc-panel-card-summary">', read("components/company-cards.tsx"))
        for rel, title in (("components/companies.tsx", "{co.companies}"), ("components/pending.tsx", "{co.pending}"),
                           ("components/scheduled-tasks.tsx", '{tr("cowork.folder.title")}')):
            self.assertIn(f'<h1 className="cc-page-title"><Marquee className="cc-page-title-text">{title}</Marquee></h1>', read(rel), rel)
        for rel in ("components/add-company.tsx", "components/scheduled-tasks.tsx"):
            self.assertIn('<DialogPrimitive.Title className="cc-greeting min-w-0 text-xl">\n              <Marquee className="block truncate">{title}</Marquee>', read(rel), rel)
        # 父项、单选项、勾选项（第 7 轮：单选 / 勾选分成 RadioItem / CheckboxItem 两个）
        self.assertEqual(read("components/assistant-ui/sidebar-group.tsx").count('<Marquee className="min-w-0 flex-1 truncate">{label}</Marquee>'), 3)
        self.assertIn('<Marquee className={cn("max-w-[22rem] truncate text-sm"', read("components/settings.tsx"))
        self.assertIn('<Marquee className="typing-dots-label ms-2.5 min-w-0 truncate" aria-hidden="true">{text}</Marquee>', read("components/assistant-ui/thread.tsx"))
        panel = read("components/results-panel.tsx")
        self.assertIn('"cc-fade-cell max-w-72 border border-line px-2.5 py-1.5"', panel)
        self.assertIn('watchOverflow([el], () => el.querySelectorAll<HTMLElement>("td.cc-fade-cell"))', panel)
        # 其余还写着 truncate 的只能在 Marquee 上（盖掉省略号）；保留原样的：文件下拉框（原生控件）、没用到的官方侧栏组件
        allowed = {("components/results-panel.tsx", "<select"), ("components/ui/sidebar.tsx", "[&>span:last-child]:truncate")}
        for path in sorted(SRC.rglob("*.tsx")):
            rel = path.relative_to(SRC).as_posix()
            text = path.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines()):
                if not re.search(r"\btruncate\b", line) or "Marquee" in line:
                    continue
                context = "\n".join(text.splitlines()[max(0, i - 3):i + 1])
                self.assertTrue(any(rel == f and key in context for f, key in allowed), f"{rel}:{i + 1}: {line.strip()}")
        # 多行截断（line-clamp）不在这次范围，保持原样
        self.assertIn("line-clamp-2", read("components/assistant-ui/thread.tsx"))


class Round5SmallFixesTests(unittest.TestCase):
    """测试窗口第 5 轮（lab 20b9f387）五处小问题：聚焦描边、组头 Alt+↑/↓ 播报、拿起的播报、Esc 一次一层、渐隐补量。"""

    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + body + "\n(globalThis as any).done();\n",
               stubs={"socket.io-client": "export function io() { return { on() { return this; }, off() { return this; }, connected: true }; }"})

    def test_every_focusable_control_shows_the_focus_outline(self):
        # 用户 10-16：聚焦只有蓝色描边、没有光晕（--cc-shadow-focus，1px）。第 5 轮查出几处能聚焦却没有任何描边
        css = read("globals.css")
        self.assertIn("--cc-shadow-focus: inset 0 0 0 1px var(--cc-bg-app), 0 0 0 1px var(--cc-focus-ring);", css)
        base = block(css, "@layer base {")
        # 兜底：能聚焦的控件没写自己的聚焦样式时也是同一个描边（输入框的描边在外框上，不在这里）
        self.assertIn(':where(button, a[href], summary, [role="button"], [role="link"], [role="tab"], [role="switch"], [role="checkbox"], [role="radio"],\n'
                      '    [role="separator"][tabindex], [tabindex]:not([tabindex="-1"], input, textarea, select, [contenteditable])):focus-visible {\n'
                      "    outline: none;\n"
                      "    box-shadow: var(--cc-shadow-focus);", base)
        # 兜底同时去掉浏览器自带的 outline:auto：原来靠它显示焦点的按钮（待处理组头、任务行、输入框「＋」）不再是两道
        self.assertNotIn("0 0 0 3px", base)
        # 点名的几处：组头待处理数、记住的选择「设为全公司」（.cc-link-btn）、定时任务卡的任务行（.cc-list-row）、显示更多、组按钮、胶囊、标题栏公司名
        for rule in (".cc-group-count:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     ".cc-link-btn:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     "button.cc-list-row:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     ".cc-show-more:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     ".cc-group-btn:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     ".cc-chip-btn:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     ".cc-title-company:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     # 消息下面的「复制」、知识库表单的「取消」本来就有（Button 的 focus-visible:shadow-focus、.cc-btn）
                     ".cc-btn:focus-visible { box-shadow: var(--cc-shadow-focus); }"):
            self.assertIn(rule, css)
        self.assertIn("focus-visible:shadow-focus", read("components/ui/button.tsx"))
        # 侧栏宽度拖把（分隔条）：键盘聚焦时那根线换成聚焦蓝、1px
        self.assertIn(".cc-split:focus-visible::before { background: var(--cc-focus-ring); width: 1px; }", css)
        # 兜底去掉 outline:auto 后，自带阴影（盖过兜底的 box-shadow）的按钮要自己写聚焦描边，否则一点都看不见：
        # 设置里分段选择的选中格（shadow-sm）、手机圆按钮与抽屉「新建」胶囊（手机顶栏 / 抽屉的圆按钮规则不分层，聚焦规则也跟在后面）
        self.assertIn('"flex items-center rounded-md px-3 py-1 text-sm [&_svg]:size-4 focus-visible:shadow-focus",', read("components/settings.tsx"))
        self.assertIn(".cc-round-btn:focus-visible, .cc-pill:focus-visible { box-shadow: var(--cc-shadow-focus); }", block(css, "@layer components {"))
        mobile = css.index(".cc-mtopbar .cc-round-btn, .cc-mdrawer .cc-round-btn {")
        self.assertGreater(css.index(".cc-mtopbar .cc-round-btn:focus-visible, .cc-mdrawer .cc-round-btn:focus-visible { box-shadow: var(--cc-shadow-focus); }"), mobile)
        # 只有那根线：12px 宽的按住区不套兜底描边（审查：原来是一个 12px 蓝框套着中间蓝线，三条竖线）
        self.assertIn(".cc-split:focus-visible { box-shadow: none; }", block(css, "@layer components {"))
        # 32 标题栏、侧栏搜索框、就地改名仍是 1.5px 里描边
        self.assertIn("box-shadow: var(--cc-shadow-focus-inset);", css)
        self.assertIn(".cc-search:focus-within { box-shadow: var(--cc-shadow-focus-inset); }", css)
        self.assertIn(".cc-inline-edit:focus { outline: var(--cc-focus-w) solid var(--cc-focus-ring); outline-offset: 0; }", css)
        # 侧栏行尾「···」不在 Tab 顺序里，但被聚焦（菜单关了焦点还回来）时自己也露出来
        rows = read("components/assistant-ui/thread-list.tsx")
        more = rows.split('data-slot="aui_thread-list-item-more"\n', 1)[1].split("\n", 1)[0]
        self.assertIn("focus-visible:opacity-100", more)
        self.assertIn("group-has-focus-visible/item:opacity-100", more)

    def test_lifting_announces_picked_up_not_a_position(self):
        # 第 5 轮第 3 项：公司组头空格拿起念成「移到第 3 位，共 4 项」。拖动库拿起后紧跟着报一次「经过」，经过的是拿起的那一项自己
        # （排序时它在原位）：这一次不念，留着「已拿起」。挪开过再回到原位照常念位置；下一次拿起重新算
        self.run_page(r"""
import { dragAnnouncements } from "@/components/ui/drag";
const texts = { instructions: "", pickedUp: (n: string) => `up ${n}`, movedTo: (n: string, a: number, t: number) => `${n} to ${a}/${t}`,
  droppedAt: (n: string, a: number, t: number) => `${n} at ${a}/${t}`, cancelled: (n: string) => `cancel ${n}` };
const items = ["company:a", "company:b", "company:c", "company:d"];
const entry = (id: string, index: number) => ({ id, data: { current: { drag: { id, kind: "company", label: id.slice(8), list: "companies", group: id.slice(8) }, sortable: { index, items } } } });
for (const say of [dragAnnouncements(texts), dragAnnouncements(texts, { accepts: (a, b) => a.list === b.list, placed: () => null })]) {
  const c = entry("company:c", 2);
  assert.equal(say.onDragStart!({ active: c } as any), "up c");
  assert.equal(say.onDragOver!({ active: c, over: c } as any), undefined);             // 拿起当下经过自己：不念，留着「已拿起」
  assert.equal(say.onDragOver!({ active: c, over: entry("company:d", 3) } as any), "c to 4/4");
  assert.equal(say.onDragOver!({ active: c, over: c } as any), "c to 3/4");             // 挪开过再回到原位：念位置
  assert.equal(say.onDragEnd!({ active: c, over: c } as any), "cancel c");             // 落回原位：没动，念取消（第 7 轮）
  assert.equal(say.onDragEnd!({ active: c, over: entry("company:a", 0) } as any), "c at 1/4");   // 真的挪了：念放到第几位
  assert.equal(say.onDragEnd!({ active: c, over: null } as any), "cancel c");
  assert.equal(say.onDragStart!({ active: c } as any), "up c");                        // 再拿起：重新算
  assert.equal(say.onDragOver!({ active: c, over: c } as any), undefined);
  assert.equal(say.onDragCancel!({ active: c } as any), "cancel c");
}
""")
        # 卡片、置顶同一份（共用拖动）
        drag = read("components/ui/drag.tsx")
        self.assertIn("if (!end && over && over.id === active.id && !moved) return undefined;", drag)

    def test_alt_arrow_moves_are_announced_like_dragging(self):
        # 第 5 轮第 2 项：公司组头 Alt+↑/↓ 挪一格时读屏什么也没念。挪完念新位置，文案同键盘拖动的 movedTo（三语）；置顶、卡片同样
        self.run_page(r"""
import { spotIn } from "@/components/ui/drag";
assert.deepEqual(spotIn(["a", "b", "c"], "b"), { at: 2, total: 3 });
assert.equal(spotIn(null, "b"), null);          // 到头了没挪：不念
assert.equal(spotIn(["a"], "x"), null);
""")
        drag = read("components/ui/drag.tsx")
        self.assertIn('<div role="status" aria-live="assertive" aria-atomic="true" className="sr-only" data-slot="cc-move-live">', drag)
        self.assertIn("const text = texts.movedTo(name, spot.at, spot.total);", drag)
        self.assertIn("if (spot) say?.(data.label, spot);", drag)
        rows = read("components/assistant-ui/thread-list.tsx")
        self.assertIn("onMove={scope.locked ? undefined : (delta) => drag.moved(scope.move(id, delta))}", rows)
        self.assertIn('move: (id, delta) => spotIn(savePins(moveBy(pinIds, id, delta)), id)', rows)
        cards = read("components/company-cards.tsx")
        # 先挪再念：没有 sortable（不播报）时也照样挪，不能被可选链连同挪动一起短路
        self.assertIn("const spot = control.move(id, delta);\n      sortable?.moved(spot);", cards)
        self.assertNotIn("sortable?.moved(control.move(", cards)
        panel = read("components/session-panel.tsx")
        self.assertIn("return shown.indexOf(id) === visible.indexOf(id) ? null : spotIn(visible, id);", panel)

    def test_escape_closes_one_layer_at_a_time(self):
        # 第 5 轮第 4 项：窄窗口多选 2 条、打开浮动会话栏、键盘拿起卡片：第一下 Esc 只取消拖动；第二下原来把会话栏和多选一起清掉。
        # 关掉一层的处理都标记已处理，下面一层见到就不动：第二下只关会话栏，第三下才清多选
        panel = read("components/session-panel.tsx")
        self.assertIn("      if (isDragEscape(e)) return;\n      e.preventDefault();\n      onClose();", panel)
        keys = read("lib/sidebar-keys.ts")
        self.assertIn("!event.defaultPrevented && !layered() && !isDragEscape(event)", keys)
        # 会话栏在 document 上、多选在 window 上（同一个 Esc 先到会话栏）
        self.assertIn('document.addEventListener("keydown", onKey);', panel)
        self.assertIn('window.addEventListener("keydown", onWindowKey);', keys)
        # 改名、知识库编辑框的 Esc 本来就标记已处理
        self.assertIn('if (e.key === "Escape" && !e.nativeEvent.isComposing) {\n          e.preventDefault();\n          finish(false);', read("components/assistant-ui/thread-list.tsx"))
        self.assertIn("if (!action) return;\n          e.preventDefault();", read("components/company-cards.tsx"))

    def test_overflow_fade_is_rechecked_when_the_observer_would_miss_it(self):
        # 第 5 轮第 5 项：页面在后台时刷新，电脑上一个 data-overflow 也没有。观察器只在尺寸变了时回调：回到前台、字体载完、窗口改宽窄、
        # 根节点或侧栏宽度变了时全部重量；仍只有整页一个观察器
        self.run_page(r"""
const g = globalThis as any;
const on: Record<string, Array<() => void>> = {};
const listen = (type: string, run: () => void) => (on[type] ??= []).push(run);
const fire = (type: string) => (on[type] ?? []).forEach((run) => run());
const sidebar = { isConnected: true };
let fontsReady!: () => void;
g.document.visibilityState = "visible";
g.document.addEventListener = listen;
g.document.querySelector = (q: string) => (q.includes("sidebar-container") ? sidebar : null);
g.document.documentElement.isConnected = true;
g.document.fonts = { ready: new Promise<void>((r) => (fontsReady = r)), addEventListener: (type: string, run: () => void) => listen("fonts:" + type, run) };
g.addEventListener = listen;
g.requestAnimationFrame = (run: () => void) => setTimeout(run, 0);
g.getComputedStyle = () => ({ paddingLeft: "0px", paddingRight: "0px" });
const observers: any[] = [];
g.ResizeObserver = class {
  run: (entries: any[]) => void;
  seen = new Set<any>();
  constructor(run: (entries: any[]) => void) { this.run = run; observers.push(this); }
  observe(el: any) { this.seen.add(el); }
  unobserve(el: any) { this.seen.delete(el); }
};
const { attachOverflowFade } = await import("@/lib/overflow-fade");
function marquee(text: number, client: number) {
  const attrs = new Set<string>();
  const inner: any = { width: text, getBoundingClientRect: () => ({ width: inner.width }) };
  const box: any = { isConnected: true, clientWidth: client, offsetWidth: client, inner, attrs,
    getBoundingClientRect: () => ({ width: box.offsetWidth }),
    querySelector: () => inner, hasAttribute: (n: string) => attrs.has(n),
    toggleAttribute: (n: string, v: boolean) => (v ? attrs.add(n) : attrs.delete(n), v) };
  return box;
}
const a = marquee(300, 200), b = marquee(100, 200);
attachOverflowFade(a); attachOverflowFade(b);
assert.equal(observers.length, 1);                       // 仍是整页一个观察器
const ro = observers[0];
assert.ok(ro.seen.has(g.document.documentElement) && ro.seen.has(sidebar));   // 多看根节点与侧栏
const fades = () => [a.attrs.has("data-overflow"), b.attrs.has("data-overflow")];
assert.deepEqual(fades(), [false, false]);               // 观察器还没回调（页面在后台）
g.document.visibilityState = "hidden"; fire("visibilitychange"); await g.sleep(5);
assert.deepEqual(fades(), [false, false]);               // 在后台：不量
g.document.visibilityState = "visible"; fire("visibilitychange"); await g.sleep(5);
assert.deepEqual(fades(), [true, false]);                // 回到前台：补量
b.inner.width = 260; fire("fonts:loadingdone"); await g.sleep(5);
assert.deepEqual(fades(), [true, true]);                 // 字体载完、文字变宽
a.clientWidth = a.offsetWidth = 400; ro.run([{ target: g.document.documentElement }]);
assert.deepEqual(fades(), [false, true]);                // 窗口改宽（根节点回调）：放得下的去掉渐隐
assert.equal(on.resize, undefined);                      // 不另听 resize（根节点观察已覆盖，否则每帧量两遍）
b.clientWidth = b.offsetWidth = 300; ro.run([{ target: sidebar }]);
assert.deepEqual(fades(), [false, false]);               // 侧栏变宽（只有侧栏回调）：全部重量
a.clientWidth = a.offsetWidth = 150; ro.run([{ target: g.document.documentElement }]);
assert.deepEqual(fades(), [true, false]);                // 根节点变了：全部重量
a.clientWidth = a.offsetWidth = 500; fontsReady(); await g.sleep(5);
assert.deepEqual(fades(), [false, false]);               // 字体全部载完（fonts.ready）
a.clientWidth = a.offsetWidth = 100; fire("pageshow"); await g.sleep(5);
assert.deepEqual(fades(), [true, false]);                // 从往返缓存回来
""")
        fade = read("lib/overflow-fade.ts")
        self.assertEqual(fade.count("new ResizeObserver"), 1)
        self.assertNotIn('"resize"', fade)


# 第 6 轮测试窗口（lab 1badde29）：关掉一层后焦点回到打开它的控件；搜索时拖向收着的组组头照样高亮
LISTEN_STUB = r"""
const g = globalThis as any;
g.listened = new Map<string, Array<(e: any) => void>>();
g.addEventListener = (type: string, run: (e: any) => void) => g.listened.set(type, [...(g.listened.get(type) ?? []), run]);
g.removeEventListener = () => {};
export {};
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class Round6Tests(unittest.TestCase):
    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport "cc-listen";\nimport assert from "node:assert/strict";\n' + body
               + "\n(globalThis as any).done();\n", stubs={"cc-listen": LISTEN_STUB})

    def test_focus_goes_back_to_what_opened_the_layer(self):
        # 第 6 轮第 4 项：Esc 关浮动会话栏、关设置弹窗之后焦点落到 BODY。设置弹窗没有 Trigger，组件库关掉时找不到还焦点的去处
        # （它自己还拦下了默认的去处）；会话栏卸下时焦点在里面，随它一起没了。现在打开时记下谁打开的（菜单项打开的记菜单按钮），
        # 关掉后还给它；它不在了回会话栏开关 / 齿轮。键盘关的才出焦点描边
        self.run_page(r"""
import { giveFocusBack, opener, returnFocusAfterClose } from "@/lib/focus-return";
const g = globalThis as any;
const focused: any[] = [];
g.CSS = { escape: (text: string) => text.replace(/:/g, "\\:") };
const body = { name: "body" };
const el = (name: string, extra: any = {}) => {
  const e: any = { name, isConnected: true, getClientRects: () => [1], closest: (sel: string) => extra.parents?.[sel] ?? null,
    focus(options: any) { focused.push([name, options]); g.document.activeElement = e; }, ...extra };
  return e;
};
const gear = el("gear"), toggle = el("toggle"), trigger = el("user-menu");
const item = el("menu-item", { parents: { '[role="menu"]': { id: "radix-:r1:" } } });
g.document.body = body;
g.document.querySelector = (sel: string) => (sel === '[aria-controls="radix-\\:r1\\:"]' ? trigger : null);
g.document.querySelectorAll = (sel: string) => [sel.includes("cc-panel-toggle") ? toggle : null, sel.includes("cc-settings-button") ? gear : null].filter(Boolean);
// 谁打开的：焦点所在的控件；在下拉菜单里时记那个菜单的按钮（菜单项随菜单卸下）；没有焦点回 null
g.document.activeElement = gear;
assert.equal(opener(), gear);
g.document.activeElement = item;
assert.equal(opener(), trigger);
g.document.activeElement = body;
assert.equal(opener(), null);
// 还给它；鼠标关的不出描边（focusVisible: false），键盘关的出
g.listened.get("pointerdown")![0]!({});
assert.equal(giveFocusBack(gear, "[data-slot=cc-settings-button]"), gear);
assert.deepEqual(focused.at(-1), ["gear", { preventScroll: true, focusVisible: false }]);
g.listened.get("keydown")![0]!({ key: "Escape" });
giveFocusBack(gear, "[data-slot=cc-settings-button]");
assert.deepEqual(focused.at(-1), ["gear", { preventScroll: true, focusVisible: true }]);
g.listened.get("keydown")![0]!({ key: ",", ctrlKey: true });   // 快捷键本身不算（随后的焦点仍按上一次的键盘 / 鼠标）
// 它不在了（卸下、藏起、在 inert 里）：回 fallback 里第一个看得见的
assert.equal(giveFocusBack(el("gone", { isConnected: false }), '[data-slot="cc-panel-toggle"]'), toggle);
assert.equal(giveFocusBack(el("hidden", { getClientRects: () => [] }), '[data-slot="cc-settings-button"]'), gear);
assert.equal(giveFocusBack(el("inert", { parents: { "[inert]": {} } }), '[data-slot="cc-panel-toggle"]'), toggle);
assert.equal(giveFocusBack(null, "[data-slot=nothing]"), null);
// 一层卸下后：焦点原来在这一层里（卸下后掉到 BODY）才还；用户已经点去了别处（输入框）就不抢
focused.length = 0;
g.document.activeElement = body;
returnFocusAfterClose(true, el("gone", { isConnected: false }), '[data-slot="cc-panel-toggle"]');
await g.sleep(0);
assert.deepEqual(focused.map((f) => f[0]), ["toggle"]);
const composer = el("composer");
g.document.activeElement = composer;
returnFocusAfterClose(false, gear, '[data-slot="cc-panel-toggle"]');
await g.sleep(0);
assert.deepEqual(focused.map((f) => f[0]), ["toggle"]);
// 审查：去处按给的先后找，不按页面上的先后。用户菜单按钮在页面上排在齿轮前面：记不到打开者（焦点在 BODY 时按 Ctrl+,、
// Safari 鼠标点按钮不拿焦点）时设置关掉先回齿轮，齿轮不在（窄屏藏起）才回用户菜单按钮
const page = [trigger, gear];
g.document.querySelectorAll = (sel: string) => page.filter((e) => (e === gear ? sel.includes("cc-settings-button") : sel.includes("cc-user-menu")));
const back = ['[data-slot="cc-settings-button"]', '[data-slot="cc-user-menu"]'];
assert.equal(giveFocusBack(null, back), gear);
assert.equal(giveFocusBack(null, back.join(", ")), trigger);        // 合成一个选择器就按页面先后：是用户菜单按钮（原来的问题）
gear.getClientRects = () => [];
assert.equal(giveFocusBack(null, back), trigger);
assert.equal(giveFocusBack(el("gone", { isConnected: false }), back), trigger);
""")
        settings = read("components/settings.tsx")
        self.assertIn("onCloseAutoFocus={(event) => {\n            event.preventDefault();\n            giveFocusBack(back.current, SETTINGS_BACK);", settings)
        self.assertEqual(settings.count("remember();\n"), 2)        # 齿轮 / 菜单 / 公司页按钮（事件）与 Ctrl+, 两处
        self.assertIn("if (!content.current?.contains(from)) back.current = from;", settings)
        self.assertIn("const SETTINGS_BACK = ['[data-slot=\"cc-settings-button\"]', '[data-slot=\"cc-user-menu\"]'] as const;", settings)
        panel = read("components/session-panel.tsx")
        self.assertIn('const [from] = useState(() => (mode === "docked" ? null : opener()));', panel)
        self.assertIn("returnFocusAfterClose(!!box.current?.contains(document.activeElement), from, PANEL_TOGGLE);", panel)
        app = read("assistant.tsx")
        for slot in ('data-slot="cc-settings-button"', 'data-slot="cc-user-menu"', 'data-slot="cc-panel-toggle"'):
            self.assertIn(slot, app)

    def test_search_highlights_target_groups_that_are_collapsed_outside_search(self):
        # 第 6 轮第 5 项：搜索时各组都展开显示，把对话拖向收着的「最近」组头不亮（不搜索时亮）。现在不搜索时收着的组（用户收起的、
        # 排在前几家之后默认收着的）在搜索时组头照样高亮；空档照旧排进展开的组里、落进空档（搜索时看得见）。公司组同一条
        rows = read("components/assistant-ui/thread-list.tsx")
        groups = rows.split("const CompanyGroups: FC", 1)[1].split("\nconst DayGroups", 1)[0]
        self.assertIn("if (!searching || !byCompany) return null;", groups)
        self.assertIn("const plain = companySections(rows, projects, { shown, serverMore, sort, empty: view.empty, companyOrder: order.companies, pinnedOrder: order.pinned }) ?? [];", groups)
        self.assertIn('return new Set(plain.flatMap((section) => (section.type !== "pinned" && !groupOpen(section, collapsed, false) ? [section.key] : [])));', groups)
        self.assertIn("shut={!!shutOutside?.has(section.key)}", groups)
        self.assertEqual(groups.count("shut={!!shutOutside?.has(section.key)}"), 2)   # 公司组、最近
        self.assertIn("{ disabled: locked, ownFocus: true, shut: !open || !!fold || !!shut },", groups)
        self.assertIn('useDropTarget({ id: RECENTS_TARGET, kind: "recents", label: t("recents"), group: "" }, !open || !!fold || !!shut);', groups)
        # 空档排不排进去仍只看展开与搜索结果（不因为高亮改落点）
        self.assertIn("return index >= 0 && open && stays ? liftRow(out, rows, index, place.group, { by: view.sort, desc: view.desc }) : out;", groups)

if __name__ == "__main__":
    unittest.main()
