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
        # 卡片的回答（pearnly.answer，第 11 轮 C22）同样是本站事件：只走推送，不当锚点
        # 执行记录（pearnly.tool_run，第 11 轮 A 段）也是：推来、并在读事件那页最后，都不当锚点
        self.assertIn("export type FeedEvent = StreamEvent | NoticeEvent | AnswerEvent | ToolRunEvent;", events)
        self.assertIn('type: "pearnly.answer";', events)
        self.assertIn('type: "pearnly.tool_run";', events)
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
        self.assertIn('push.failure && <div className="text-danger text-caption">{push.failure}</div>', settings)
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
                             "combinedVat", "showMore", "deleteCompany", "chooseOnComputer", "changeFolder",
                             "removeFolder", "updateDesktop", "folderOk", "folderWaiting", "folderOffline", "folderMissing",
                             "payCash", "payCredit", "vatClaim", "vatExpense", "pending", "handToCC", "handToCCText", "handToCCOne",
                             "fromHistory", "history", "vatCheck"} <= sets[lang], lang)
            self.assertIn("earlierFolders:", block(settings, f"  {lang}: {{"), lang)
        self.assertEqual(sets["en"], sets["zh"])
        self.assertEqual(sets["en"], sets["th"])
        self.assertNotIn("moveTo", sets["en"])   # 用户 10-17：「移到」已删，词条一并去掉
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
                         "show", "metadata", "emptyGroups", "resetDefaults", "noMonth", "pinN", "unpinN",
                         "deleteN"} <= words["en"])
        self.assertNotIn("moveToN", words["en"])   # 用户 10-17：多选的「移到… (N)」删了
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
        # 三种触屏判定各管各的（标准化记录 3.4）：lib/touch.ts 的 touchOnly()（hover: none）管「没有悬停」时的行为；
        # lib/variant.ts 写 <html data-cc-touch>（粗指针且不是桌面窗口，或手机变体）管尺寸与点按区；别处都不再各写一套
        for name, text in self.sources():
            if name != "lib/touch.ts":
                self.assertNotIn('matchMedia("(hover', text, name)
            if name != "lib/variant.ts":
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
assert.equal(html(Shortcut, { keys: "Ctrl ," }), '<span class="cc-menu-key">⌘ ,</span>');
assert.match(html(Shortcut, { keys: "Ctrl + Alt + Space", kbd: true }), /^<kbd class="[^"]*">⌘ \+ ⌥ \+ Space<\/kbd>$/);

g.device.touch = true;
g.qt = { webChannelTransport: {} };
assert.equal(showShortcuts(), true);
assert.equal(html(Shortcut, { keys: "Ctrl N" }), '<span class="cc-menu-key">⌘ N</span>');
""", lang="th")

    def test_shortcuts_use_command_on_mac_and_ctrl_elsewhere(self):
        # 测试窗口第 7 轮：Mac 上用户菜单的「设置」写成「Ctrl ,」，应为「⌘ ,」；Windows / Linux 照旧 Ctrl
        self.on_device("windows-chrome", r"""
import { appleKeys, keysFor } from "@/components/shortcut";
assert.equal(appleKeys(), false);
assert.equal(html(Shortcut, { keys: "Ctrl ," }), '<span class="cc-menu-key">Ctrl ,</span>');
assert.match(html(Shortcut, { keys: "Ctrl + Alt + Space", kbd: true }), />Ctrl \+ Alt \+ Space<\/kbd>$/);
assert.equal(keysFor("Ctrl R", true), "⌘ R");
assert.equal(keysFor("Ctrl + Alt + Space", true), "⌘ + ⌥ + Space");
assert.equal(keysFor("Ctrl =", false), "Ctrl =");
(navigator as any).userAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36";
assert.equal(appleKeys(), true);
assert.equal(html(Shortcut, { keys: "Ctrl ," }), '<span class="cc-menu-key">⌘ ,</span>');
(navigator as any).userAgentData = { platform: "Linux" };
assert.equal(appleKeys(), false);
assert.equal(html(Shortcut, { keys: "Ctrl ," }), '<span class="cc-menu-key">Ctrl ,</span>');
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
import { blockedLine, ComputerSection, KeepAwakeRows, VersionRow } from "@/components/settings";

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

    def test_the_pool_list_offers_remove_only_where_the_server_allows_it(self):
        # 第 6 批「电脑」：登记人或管理员（服务端 can_remove）才有「···」→ 移除；10-16 起没有「常驻」（ADR-021 第 56 条）
        self.run_page(r"""
const computer = { adapter: "express", device: "ACC-02", online: false, version: "1.7.0", folders: [] as string[], id: "CCD-2",
  connection: "CWEC-1", can_remove: true };
const shown = html(ComputerSection, { row: computer, onRemove: () => {} });
assert.deepEqual(text(shown), ["ACC-02", "Express", "Disconnected"]);
assert.match(shown, /aria-label="More"/);
const plain = html(ComputerSection, { row: { ...computer, can_remove: false } });
assert.deepEqual(text(plain), ["ACC-02", "Express", "Disconnected"]);
assert.doesNotMatch(plain, /aria-label="More"/);
""", lang="en")

    def test_batch_6_review_fixes_in_the_settings_page(self):
        """第 6 批审查：桌面版「此电脑 → 连接」下的「电脑」一节没有电脑时整节不出现（不在桌面版里请人下载桌面版，第 7 条）；
        成员被另一台电脑认领时写现有「已在另一台电脑连接」+「重试」（第 8 条）；电脑被移除过写「电脑已移除」+「连接」，点了才
        rejoin（第 2 条）；名单候选取 `member_choices`（与 set_members 同口径，第 6 条）。"""
        source = read("components/settings.tsx")
        self.assertIn("<PoolComputers inDesktop />", source)
        self.assertIn("if (inDesktop && !rows.length) return <AllMembers heading />;", source)
        self.assertNotIn("<Heading>{st.computers}</Heading>\n      </div>\n      <PoolComputers />", source)
        self.assertIn('deviceState === "claimed_elsewhere" ? st.inUse : st.notConnected', source)
        self.assertIn("onConnect({ rejoin: true })", source)
        self.assertIn("note(co.folderGone", source)
        self.assertIn("pearnly_cowork.connections.api.member_choices", source)
        self.assertNotIn("team.api.api_list_members", source)

    def test_batch_6_review_fixes_in_the_task_pages(self):
        """第 6 批审查：表单不写「跟随公司当年账套」（只在列表，第 9 条）；推往的账套写成「账套名：张数」用分号隔开（第 10 条）；
        Desk 列表结果列也列推往的账套、Desk 表单按账套提示「CC 桌面版需更新」（第 11 条）；列表与补回的下拉项带读文件夹的电脑名
        （第 14 条）。"""
        tasks = read("components/scheduled-tasks.tsx")
        self.assertIn('const accountHint = needsUpdate ? tr("intake.agent.state.needsUpdate") : undefined;', tasks)
        self.assertIn('tr("cowork.folder.pair", row.account_label, row.tickets)).join("; ")', tasks)
        self.assertIn("[rule.reader_label, rule.folder || rule.root_label", tasks)
        desk = (ROOT / "apps/pearnly_cowork/pearnly_cowork/public/desk/intake-folders.js").read_text(encoding="utf-8")
        self.assertNotIn('description: rule?.company ? ft("cowork.folder.followCompany")', desk)
        self.assertIn('ft("cowork.folder.pair", item.account_label, item.tickets)).join("; ")', desk)
        self.assertIn("account_sets_text(row.results)].filter(Boolean)", desk)
        self.assertIn('p.intake_targets.account_note(connection(), key)', desk)
        self.assertIn("format: (value, row) => folder_text(row)", desk)

    def test_the_new_words_of_batch_6_are_in_every_page_language(self):
        settings = block(read("i18n.ts"), "const SETTINGS = {")
        company = block(read("i18n.ts"), "const COMPANY = {")
        for lang in ("en", "zh", "th"):
            words = block(settings, f"  {lang}: {{")
            for word in ("computers:", "needsUpdate:", "allMembers:", "members:"):
                self.assertIn(word, words, lang)
            for word in ("folderGone:", "followCompany:"):
                self.assertIn(word, block(company, f"  {lang}: {{"), lang)

    def test_every_page_language_has_the_two_new_words(self):
        settings = block(read("i18n.ts"), "const SETTINGS = {")
        for lang in ("en", "zh", "th"):
            words = block(settings, f"  {lang}: {{")
            self.assertIn("downloadNew:", words, lang)
            self.assertRegex(block(words, "update: {"), r"blocked: \"[^\"]*\{ver\}[^\"]*\"", lang)


# ── 「允许」卡片的卡片规矩（用户 2026-10-13 第 5、12 条；10-17 照 Claude 的问题卡片）──────────────────────────────────
# 一句话改几个定时任务只弹一张卡片，卡片照 Claude 对话里卡片的规矩：按钮最多两个、在底部靠右（「拒绝」次要在左、「允许」主要
# 在右，都不带图标，.cc-card-btn）；改动最多 5 行，多了列前几行 + 「还有 N 项」；卡内不套滚动；手机点按区不小于 44pt；
# 颜色只用 globals.css 的变量。点完卡片留在原处，按钮行换成所选那一项（当场与重开对话同一个样子）。用 react-dom/server 渲染真实组件。

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
const buttons = (html: string) => html.match(/<button[^>]*>.*?<\/button>/g) ?? [];
"""

# 卡片要的工具名字（站点按语言给的，lib/tool-labels.ts）：存公司选择每次都要允许；库存科目组带了 acccod 才要（服务端 approval: ["acccod"]）
TOOL_LABELS_STUB = r"""
export type ToolLabels = any;
export const LABELS = {
  done: { cowork_company_choice_save: "记下了公司选择", cowork_stock_group: "Checked the stock account group",
          cowork_company_note_save: "Saved a company note" },
  running: {},
  ask: { cowork_company_choice_save: "保存为公司选择？", cowork_stock_group: "Change the inventory account group?",
         cowork_company_note_save: "Save this to the company's notes?" },
  ask_when: { cowork_company_note_save: { note: "Update this company note?" } },
  gate: { cowork_stock_group: ["acccod"] },
  ready: true,
};
export function useToolLabels() { return LABELS; }
export function toolLabel(labels: any, toolName: string, running: boolean): string {
  return (running ? labels.running[toolName] : undefined) ?? labels.done[toolName] ?? toolName;
}
"""

# 手机 / 电脑：测试里由 globalThis.phone 定（组件库的 matchMedia 在 node 里没有）
USE_MOBILE_STUB = r"""
export const MOBILE_BREAKPOINT = 768;
export const MOBILE_QUERY = "(max-width: 767px)";
export function useIsMobile(): boolean { return (globalThis as any).phone === true; }
"""

# 手机浮层读的是当前对话的状态与控制器：换成测试里给的状态，点击记下来
SHEET_STUBS = {
    "@/lib/managed-agents/runtime-provider": r"""
export function useActiveSessionSnapshot() { return (globalThis as any).snapshot; }
""",
    "@/lib/managed-agents/session-controller": r"""
export function getSessionController(sessionId: string) {
  return { respondToApproval: (id: string, approved: boolean) => (globalThis as any).answers.push([sessionId, id, approved]) };
}
""",
}


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class ApprovalCardBehaviourTests(unittest.TestCase):
    STUBS = {"@/lib/tool-labels": TOOL_LABELS_STUB, "@/hooks/use-mobile": USE_MOBILE_STUB}

    def test_one_card_for_several_tasks_follows_the_card_rules(self):
        run_ts(APPROVAL_CARD + r"""
// 教程里的一句话改 3 个任务的提醒：一张卡片全列出，两个按钮在改动下面
let html = card(3);
assert.equal(items(html), 3);
assert.doesNotMatch(html, /more/);
assert.equal(buttons(html).length, 2);
assert.ok(html.lastIndexOf("</ul>") < html.indexOf("<button"));
// 按钮照 Claude：底部靠右，「拒绝」在左（次要，没有 data-primary）、「允许」在右（主要）；同一个卡片按钮类，不带图标
assert.match(html, /<div class="cc-card-actions">/);
const [deny, allow] = buttons(html);
assert.match(deny!, /^<button type="button" class="cc-card-btn">Deny<\/button>$/);
assert.match(allow!, /^<button type="button" class="cc-card-btn" data-primary="">Allow<\/button>$/);
assert.doesNotMatch(html, /<svg/);
// 改动清单不另套一层带边框的框（卡里只一层框，细线隔开：.cc-ask-changes）
assert.match(html, /<ul class="cc-ask-changes">/);
assert.doesNotMatch(html, /rounded-md border/);
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
// 点过以后：按钮行换成一行淡色字——所选那一项；没有绿勾红叉、没有红绿色
const answered = (approved: boolean) => renderToStaticMarkup(createElement(ApprovalBar, { approval: { id: "x", approved } }));
assert.equal(answered(true), '<div data-slot="cc-ask-answer" class="cc-ask-answer">Allow</div>');
assert.equal(answered(false), '<div data-slot="cc-ask-answer" class="cc-ask-answer">Deny</div>');
for (const approved of [true, false]) assert.doesNotMatch(answered(approved), /svg|success|danger|button/);
// 允许了、这一步没做成：所选那一项 + 危险色「失败」
html = renderToStaticMarkup(createElement(ApprovalBar, { approval: { id: "x", approved: true }, card: { phase: "answered", approved: true, failed: true } }));
assert.equal(html, '<div data-slot="cc-ask-answer" class="cc-ask-answer">Allow · <span class="cc-step-failed">Failed</span></div>');
// 确认请求没送到：按钮还在、可再点，上方一行原因（同对话里发不出去那一行）
html = renderToStaticMarkup(createElement(ApprovalBar, {
  approval: { id: "x", preview: rows(1), failure: "Something went wrong. Try again." }, respondToApproval: () => {} }));
assert.equal(buttons(html).length, 2);
assert.ok(html.indexOf('role="alert"') < html.indexOf("<button"));
assert.match(html, /<p class="line-clamp-\(--cc-alert-lines\)">Something went wrong\. Try again\.<\/p>/);
g.done();
""")
        run_ts(APPROVAL_CARD + r"""
assert.match(card(6), />还有 2 项</);
assert.match(card(1), />拒绝<\/button><button[^>]*>允许</);
g.done();
""", lang="zh")
        run_ts(APPROVAL_CARD + r"""
assert.match(card(6), />อีก 2 รายการ</);
g.done();
""", lang="th")
        source = read("components/tool-uis/approval-bar.tsx")
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|emerald|\bred-\d|\d+px", source))
        self.assertNotIn("@/components/ui/button", source)
        self.assertNotIn("lucide-react", source)
        # 卡片按钮：电脑 24 高（设计系统 xs 档，6.webp「跳过 / 提交」，第 11 轮按钮排查 G4）、左右 8、13px、字重 500、两钮间距 8；
        # 手机（粗指针 / 手机外壳）大号全圆角胶囊 44（8.webp 约 42–43，取点按区下限）、16px
        css = read("globals.css")
        rule = lambda selector: css.split("\n  " + selector + " {", 1)[1].split("}", 1)[0]
        self.assertIn("--cc-card-btn-h: var(--code-control-xs);", css)
        self.assertIn("--cc-card-btn-h-phone: var(--cc-tap);", css)
        self.assertIn("--cc-card-btn-font-phone: var(--code-text-prose);", css)
        btn = rule(".cc-card-btn")
        # 次要按钮是一块看得出的浅灰实心（叠色 2），悬停再深一档（叠色 3）：浅色模式下也有悬停反馈（第 10 轮审查）
        for part in ("height: var(--cc-card-btn-h);", "padding: 0 var(--cc-space-8);", "background: var(--cc-alpha-2);", "font-size: var(--cc-text-footnote);",
                     "font-weight: var(--cc-weight-medium);"):
            self.assertIn(part, btn)
        self.assertIn(".cc-card-btn[data-icon] { width: var(--cc-card-btn-h);", css)
        # 手机浮层右下的「允许」至少 112 宽（8.webp「提交」约 113）
        self.assertIn(".cc-ask-sheet .cc-card-btn[data-primary] { min-width: var(--cc-ask-sheet-btn-min); }", css)
        self.assertIn("--cc-ask-sheet-btn-min: 7rem;", css)
        self.assertNotIn("border:", btn)
        self.assertIn("  .cc-card-btn:hover { background: var(--cc-alpha-3); }", css)
        self.assertIn("  .cc-card-btn[data-primary] { background: var(--cc-ink); color: var(--cc-bg-app); }", css)
        self.assertIn("justify-content: flex-end; gap: var(--cc-space-8);", rule(".cc-card-actions"))
        # 触屏（手机变体，或粗指针且不是桌面窗口：data-cc-touch，V3 起桌面窗口在触屏上照电脑值）一份
        touch = rule(':root[data-cc-touch] .cc-card-btn')
        self.assertIn("height: var(--cc-card-btn-h-phone);", touch)
        self.assertIn("border-radius: var(--cc-radius-full); font-size: var(--cc-card-btn-font-phone);", touch)
        self.assertNotIn("@media (pointer: coarse) {\n    .cc-card-btn", css)
        # 问话正文色；等回答时常规字重，点完加粗（照 Claude）；问话不是按钮，没有悬停底与箭头
        question = rule(".cc-ask-q")
        self.assertIn("color: var(--cc-ink);", question)
        self.assertIn("font-weight: var(--cc-weight-regular);", question)
        self.assertIn('  .cc-ask:not([data-phase="ask"]) .cc-ask-q { padding-bottom: var(--cc-space-2); font-weight: var(--cc-weight-semibold); }', css)
        self.assertNotIn(".cc-ask-q.cc-step-row", css)

    def test_the_answered_card_looks_the_same_live_and_reopened(self):
        # 点完卡片留在原处（不进折叠的步骤组）：问话 + 所选那一项。当场点的与重开对话（历史里没有点击记录，按结果认）同一个样子
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { approvalCard, firstPendingApproval, gatedCall } from "@/lib/approval-card";
import { askText, StepRow, undoneText } from "@/components/tool-uis/steps";
import { LABELS } from "@/lib/tool-labels";
const g = globalThis as any;
const json = (value: unknown) => ({ text: JSON.stringify(value), isError: false });
const saved = json({ status: "saved" });
const denied = json({ status: "denied", message: "The user did not allow this change." });
const notSaved = json({ status: "not_saved", reason: "x" });
// 出卡片前就核出要拒绝（没问过用户）：服务端交回时带标记、按出错（pearnly_core tools.not_asked）
const notAsked = { text: JSON.stringify({ status: "not_saved", reason: "x", approval: "not_asked" }), isError: true };
const failedRun = { text: "The tool failed. Try again later.", isError: true };
const step = (extra: any) => renderToStaticMarkup(createElement(StepRow as any, {
  type: "tool-call", toolCallId: "t1", toolName: "cowork_company_choice_save", args: { choice: "A" }, argsText: "{}",
  status: { type: "complete" }, respondToApproval: () => {}, ...extra }));
const ask = "保存为公司选择？";
// 当场 == 重开：允许、拒绝、允许了却没存上
for (const [live, reopened, want] of [
  [{ approval: { id: "t1", ask, approved: true }, result: saved }, { result: saved }, "允许"],
  [{ approval: { id: "t1", ask, approved: false }, result: denied }, { result: denied }, "拒绝"],
  [{ approval: { id: "t1", ask, approved: true }, result: notSaved }, { result: notSaved }, "允许 · "],
  // 允许了、执行时出错（结果标了出错）：当场与重开同样是「允许 · 失败」的卡片（第 10 轮审查）
  [{ approval: { id: "t1", ask, approved: true }, result: failedRun }, { result: failedRun }, "允许 · "],
] as const) {
  const html = step(live);
  assert.equal(html, step(reopened), want);
  // 问话不是按钮、没有箭头（照 Claude 的问题卡片）
  assert.match(html, new RegExp(`<p class="cc-ask-q">${ask}</p>`));
  assert.match(html, new RegExp(`<div data-slot="cc-ask-answer" class="cc-ask-answer">${want}`));
  assert.doesNotMatch(html, /<button|<svg|已允许|已拒绝|记下了公司选择|text-success/);
  assert.match(html, /data-phase="answered"/);
}
assert.match(step({ result: notSaved }), /允许 · <span class="cc-step-failed">失败<\/span>/);
assert.match(step({ result: failedRun }), /允许 · <span class="cc-step-failed">失败<\/span>/);
// 出卡片前就核出要拒绝的：不是卡片，照常是步骤组里的一行；写问话（去掉疑问）+ 行尾「失败」，不写做完的说法（第 8 轮审查，
// 第 10 轮审查：重开对话时又写回了「记下了公司选择」）
assert.equal(approvalCard({ toolName: "cowork_company_choice_save", args: {}, result: notAsked, status: { type: "complete" } }, LABELS), null);
assert.equal(undoneText("cowork_company_choice_save", {}, notAsked, LABELS), "保存为公司选择");
assert.equal(undoneText("cowork_company_choice_save", {}, saved, LABELS), undefined);
assert.equal(undoneText("bash", {}, notSaved, LABELS), undefined);          // 不要允许的工具照常写步骤名
let html = "";
// 答案以服务端结果为准：本页点了「允许」、结果写明拒绝 → 「拒绝」；本页点了「拒绝」、结果是做过了的 → 「允许」
assert.match(step({ approval: { id: "t1", ask, approved: true }, result: denied }), /class="cc-ask-answer">拒绝</);
assert.match(step({ approval: { id: "t1", ask, approved: false }, result: saved }), /class="cc-ask-answer">允许</);
// 确认还没送到：所选那一项先淡一档
assert.match(step({ approval: { id: "t1", ask, approved: true, sending: true } }),
  /<div data-slot="cc-ask-answer" class="cc-ask-answer" data-unconfirmed="">允许<\/div>/);
// 没答就没了（中断、回合结束撤掉的门；重开对话时同样没有结果）：问话 + 淡色「已取消」，不写做完的说法
for (const ended of [{ type: "complete" }, { type: "incomplete", reason: "cancelled" }]) {
  html = step({ status: ended });
  assert.match(html, /data-phase="cancelled"/);
  assert.match(html, /<p class="cc-ask-q">保存为公司选择？<\/p><div data-slot="cc-ask-answer" class="cc-ask-answer">已取消<\/div>/);
  assert.doesNotMatch(html, /记下了公司选择|<button/);
}
// 等回答：问话正文色，下面两个按钮；电脑上在卡片里
html = step({ approval: { id: "t1", ask, preview: [{ field: "公司", before: "", after: "A" }] } });
assert.match(html, /data-phase="ask"/);
assert.match(html, />拒绝<\/button><button type="button" class="cc-card-btn" data-primary="">允许<\/button>/);
// 手机：等回答时这一步不在对话流里（浮在输入框上方，照 Claude 手机端：问话与改动不画两遍）
g.phone = true;
html = step({ approval: { id: "t1", ask, preview: [{ field: "公司", before: "", after: "A" }] } });
assert.equal(html, "");
// 手机上点完的：问话上方小号淡色「问题」（照 Claude 手机端）
html = step({ approval: { id: "t1", ask, approved: true }, result: saved });
assert.match(html, /^<div class="aui-step cc-ask" data-phase="answered"><div class="cc-ask-label">问题<\/div><p class="cc-ask-q">/);
g.phone = false;
// 问法随参数变的（ask_when）：重开对话时同当场一样按参数选问句（第 10 轮审查：公司备注改一条时问「Update this company note?」）
const note = (args: any) => renderToStaticMarkup(createElement(StepRow as any, {
  type: "tool-call", toolCallId: "n1", toolName: "cowork_company_note_save", args, argsText: "{}",
  status: { type: "complete" }, respondToApproval: () => {}, result: saved }));
assert.match(note({ note: "n1", text: "x" }), /<p class="cc-ask-q">Update this company note\?<\/p>/);
assert.match(note({ text: "x" }), /<p class="cc-ask-q">Save this to the company&#x27;s notes\?<\/p>/);
assert.equal(askText("cowork_company_note_save", { note: "n1" }, undefined, LABELS), "Update this company note?");
assert.equal(askText("cowork_company_note_save", { note: "" }, undefined, LABELS), "Save this to the company's notes?");
assert.equal(askText("cowork_company_note_save", { note: "n1" }, "当场带来的", LABELS), "当场带来的");
// 哪些是卡片：本页点过的、结果写明拒绝的、要「允许」的本站工具已有结果的；给了要允许的参数才算（同服务端 needs_approval）
const part = (extra: any) => ({ toolName: "cowork_company_choice_save", args: {}, ...extra });
assert.deepEqual(approvalCard(part({ approval: { id: "t1" } }), LABELS), { phase: "ask" });
assert.deepEqual(approvalCard(part({ result: saved }), LABELS), { phase: "answered", approved: true, failed: false });
assert.deepEqual(approvalCard(part({ result: denied }), LABELS), { phase: "answered", approved: false, failed: false });
assert.equal(approvalCard(part({}), LABELS), null);                      // 还没结果、没出卡片：照常是一步
assert.equal(approvalCard(part({ status: { type: "running" } }), LABELS), null);           // 还在跑：门还会来
assert.equal(approvalCard(part({ status: { type: "requires-action" } }), LABELS), null);   // 停下来等：门还会来
assert.deepEqual(approvalCard(part({ status: { type: "complete" } }), LABELS), { phase: "cancelled" });
assert.equal(approvalCard(part({ result: notAsked }), LABELS), null);   // 出卡片前就核出拒绝（带标记）：没出过卡片
assert.deepEqual(approvalCard(part({ result: failedRun }), LABELS), { phase: "answered", approved: true, failed: true });
assert.equal(approvalCard({ toolName: "bash", args: {}, result: saved }, LABELS), null);    // 不要允许的工具
assert.equal(gatedCall("cowork_stock_group", { connection: "C1" }, LABELS), false);
assert.equal(gatedCall("cowork_stock_group", { acccod: "1100" }, LABELS), true);
assert.equal(gatedCall("cowork_stock_group", { acccod: "" }, LABELS), false);
assert.equal(approvalCard({ toolName: "cowork_stock_group", args: {}, result: saved }, LABELS), null);
assert.deepEqual(approvalCard({ toolName: "cowork_stock_group", args: { acccod: "1100" }, result: saved }, LABELS),
  { phase: "answered", approved: true, failed: false });
// 手机浮层只浮最早一张还在等的
const msgs = [
  { role: "assistant", content: [{ type: "tool-call", toolCallId: "a", approval: { id: "a", approved: true } },
                                 { type: "tool-call", toolCallId: "b", approval: { id: "b" } }] },
  { role: "assistant", content: [{ type: "tool-call", toolCallId: "c", approval: { id: "c" } }] },
];
assert.equal(firstPendingApproval(msgs as any)?.toolCallId, "b");
g.done();
""", stubs=self.STUBS, lang="zh")
        thread = read("components/assistant-ui/thread.tsx")
        self.assertIn('part.type === "tool-call" && approvalCard(part as unknown as CardPart, labels) ? [] : groupByBase(part, context)', thread)
        # 步骤组里那一行（出卡片前就核出要拒绝的）写问话 + 失败，不写做完的说法
        steps_src = read("components/tool-uis/steps.tsx")
        plain = steps_src[steps_src.index("const PlainStep: FC"):steps_src.index("export function askText")]
        self.assertIn("const undone = undoneText(toolName, args as Args, result, labels);", plain)
        self.assertIn("{undone ?? step.verb}", plain)
        # 手机上还在等回答的那张连同外框都不画（浮在输入框上方）；工具名字没取回前，要靠它认卡片的回复先不画
        self.assertIn("<LoneStep part={part as unknown as CardPart}>", thread)
        self.assertIn('if (phone && part && approvalCard(part, labels)?.phase === "ask") return null;', steps_src)
        self.assertIn("{!waitForLabels && (", thread)

    def test_phone_sheet_floats_the_earliest_card_and_the_cross_denies(self):
        # 手机等回答时卡片浮在输入框上方（照 Claude 手机端）：问话大号字、右上圆形 × = 拒绝、右下大号胶囊「允许」，改动照常列；
        # 多张只浮最早一张；电脑不浮；不是弹窗（Esc / 返回键不当拒绝）
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { ApprovalSheet } from "@/components/tool-uis/approval-sheet";
const g = globalThis as any;
g.answers = [];
const tool = (id: string, extra: any = {}) => ({ type: "tool-call", toolCallId: id, toolName: "cowork_company_choice_save", args: {}, ...extra });
g.snapshot = { sessionId: "sesn_1", messages: [
  { role: "assistant", content: [tool("t0", { approval: { id: "t0", approved: true } }),
    tool("t1", { approval: { id: "t1", ask: "保存为公司选择？", preview: [{ field: "公司", before: "", after: "A" }], failure: "网络不通" } })] },
  { role: "assistant", content: [tool("t2", { approval: { id: "t2", ask: "第二张？" } })] },
] };
// 组件里的钩子在测试里都是普通函数（站点状态、控制器、手机判断换成了桩）：直接调用，拿到画出来的元素树
const walk = (node: any, out: any[] = []): any[] => {
  if (Array.isArray(node)) { for (const n of node) walk(n, out); return out; }
  if (node && typeof node === "object" && node.props) { out.push(node); walk(node.props.children, out); }
  return out;
};
g.phone = false;
assert.equal(ApprovalSheet(), null);     // 电脑不浮
g.phone = true;
const tree = ApprovalSheet() as any;
assert.equal(tree.props["data-slot"], "cc-ask-sheet");
assert.equal(tree.props.className, "cc-ask-sheet");
assert.equal(tree.props["aria-label"], "保存为公司选择？");   // 最早一张还在等的
const nodes = walk(tree);
const btns = nodes.filter((n) => n.type === "button");
assert.equal(btns.length, 2);
const [cross, allow] = btns;
assert.equal(cross.props["aria-label"], "拒绝");
assert.equal(cross.props["data-icon"], "");
assert.equal(allow.props["data-primary"], "");
assert.equal(allow.props.children, "允许");
assert.ok(nodes.some((n) => n.props.className === "cc-ask-sheet-q" && n.props.children === "保存为公司选择？"));
assert.ok(nodes.some((n) => n.props.preview?.[0]?.field === "公司"));   // 改动照常列
assert.ok(nodes.some((n) => n.props.message === "网络不通"));            // 没送到的原因在按钮上方
cross.props.onClick();
allow.props.onClick();
assert.deepEqual(g.answers, [["sesn_1", "t1", false], ["sesn_1", "t1", true]]);
// 不是弹窗：没有 Esc / 返回键处理，只有 ×
assert.ok(!nodes.some((n) => n.props.onKeyDown || n.props.role === "dialog"));
// 答完：浮下一张；都答完浮层消失
g.snapshot.messages[0].content[1].approval = { id: "t1", approved: true };
assert.equal((ApprovalSheet() as any).props["aria-label"], "第二张？");
g.snapshot.messages[1].content[0].approval = { id: "t2", approved: false };
assert.equal(ApprovalSheet(), null);
g.done();
""", stubs={**self.STUBS, **SHEET_STUBS}, lang="zh")
        thread = read("components/assistant-ui/thread.tsx")
        # 跟着输入框走：放在对话底部输入框那一块里，输入框正上方
        self.assertIn("<SendFailure />\n            <ApprovalSheet />\n            <Composer />", thread)
        css = read("globals.css")
        sheet = css.split("\n  .cc-ask-sheet {", 1)[1].split("}", 1)[0]
        self.assertIn("border-radius: var(--cc-radius-sheet);", sheet)
        self.assertIn("background: var(--cc-bg-popover);", sheet)
        self.assertIn("--cc-radius-sheet: 1.5rem;", css)   # 8.webp 边缘拟合约 23.5（第 11 轮按钮排查 L9）
        self.assertIn("font-weight: var(--cc-weight-semibold);", css.split("\n  .cc-ask-sheet-q {", 1)[1].split("}", 1)[0])   # 问话粗体（照 Claude 手机端）

    def test_needs_input_notice_and_install_banner_use_the_card_buttons(self):
        thread = read("components/assistant-ui/thread.tsx")
        # 电脑上等回答时工作状态行换成橙色小点 +「需要您的输入」（照 Claude，在卡片下方）；手机上由输入框上方的浮层代替，不显示
        indicator = thread[thread.index("const RunIndicator: FC"):thread.index("/** 显示时每秒走一次的本页时钟")]
        self.assertIn('<span className="cc-needs-input-dot" aria-hidden="true" />\n        <span className="cc-needs-input">{t("needsInput")}</span>', indicator)
        self.assertIn("  if (waiting) {\n    if (phone) return null;", indicator)
        css = read("globals.css")
        self.assertIn("background: var(--cc-brand);", css.split("\n  .cc-needs-input-dot {", 1)[1].split("}", 1)[0])
        # 胶囊的字与圆点同一个品牌橙、浅橙底（第 10 轮审查：原来是警示色的橄榄褐 / 金黄，与圆点不同色）
        pill = css.split("\n  .cc-needs-input {", 1)[1].split("}", 1)[0]
        self.assertIn("background: var(--cc-brand-soft);", pill)
        self.assertIn("color: var(--cc-brand);", pill)
        i18n = read("i18n.ts")
        for word in ('needsInput: "Needs your input"', 'needsInput: "需要您的输入"', 'needsInput: "ต้องการข้อมูลจากคุณ"'):
            self.assertIn(word, i18n)
        # 「已允许 / 已拒绝」不再用（点完写所选那一项），词条删掉
        self.assertNotRegex(i18n, r"\b(allowed|denied):")
        # 新对话页的安装提示：卡片按钮（「添加」主要、× 次要的圆钮），尺寸同允许卡
        install = read("components/install-prompt.tsx")
        banner = install[install.index("export function InstallBanner"):]
        self.assertIn('<button type="button" className="cc-card-btn" data-primary="" onClick={run}>\n        {LABEL[target]()}', banner)
        self.assertIn('className="cc-card-btn"\n        data-icon=""', banner)
        self.assertNotIn("cc-install-main", css + install)
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
assert.match(markup, /<p class="mt-\(--cc-md-block\) first:mt-0">ยอด <span class="font-bold">19,600\.36<\/span><span class="italic underline underline-offset-\(--cc-link-offset\)"> บาท<\/span><sup>2<\/sup><span class="underline underline-offset-\(--cc-link-offset\)">ลิงก์<\/span><\/p>/);
assert.match(markup, /<p class="mt-\(--cc-md-block\) first:mt-0 flex gap-\(--cc-space-8\)" style="padding-inline-start:calc\(var\(--cc-md-list-indent\) \* 1\)"><span class="shrink-0 tabular-nums">1\.a\)<\/span><span class="min-w-0 flex-1">ข้อย่อย<\/span><\/p>/);
assert.match(markup, /<p class="mt-\(--cc-md-block\) first:mt-0"> <\/p>/);  // 空段落占一行
assert.match(markup, /<td class="[^"]*bg-subtle[^"]*"><p>รายการ<\/p><\/td>/);
assert.match(markup, /<td colSpan="2" rowSpan="2" class="[^"]*"><p>1,234\.00<\/p><\/td>/);
assert.match(markup, /<p class="text-right">รวม<\/p>/);
assert.match(markup, /<td class="[^"]*"><div class="mt-\(--cc-md-block\) first:mt-0 max-w-full overflow-x-auto"><table/);  // 表中表
assert.match(markup, /<img src="data:image\/png;base64,iVBORw0KGgo=" alt="" draggable="false" class="[^"]*max-w-full[^"]*" style="width:100px;aspect-ratio:100 \/ 50"\/>/);
// 手机：整篇竖着滚、不横向撑开；宽表格在自己的框里横着滚；长串文字折行；图片不超过面板宽
assert.match(markup, /^<div class="h-full overflow-x-hidden overflow-y-auto"><article class="[^"]*break-words[^"]*">/);
assert.equal((markup.match(/<div class="mt-\(--cc-md-block\) first:mt-0 max-w-full overflow-x-auto"><table/g) ?? []).length, 2);
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
assert.match(markup, /<p class="mt-\(--cc-md-block\) first:mt-0">s<\/p><p class="mt-\(--cc-md-block\) first:mt-0">p<\/p>/);  // 不认识的样式按正文、不当标签名
assert.match(markup, /<span class="shrink-0 tabular-nums">&lt;b&gt;m&lt;\/b&gt;<\/span>/);
assert.match(markup, /<td class="[^"]*"><p>c<\/p><\/td>/);  // 怪的合并数不带出来
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
    // 服务端存的正文去掉首尾空白（contracts.message_text），回声同样
    if (site.echo) site.log.push(ev(`echo${site.log.length}`, "user.message", 100 + site.log.length, { content: text(String(body.text).trim()) }));
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
// 别处答过：这边按钮立即消失，卡片留着点完的样子（定下「允许」，问话照旧，用户 10-17）
assert.equal(toolPart(snap, "tz").approval.approved, true);
assert.equal(toolPart(snap, "tz").approval.ask, "Turn it on?");
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
        item = thread_list.split("\nconst Row = memo(function Row(", 1)[1].split("\nconst RowMeta: FC", 1)[0]   # 置顶行、组里可拖的行、不能拖的行都是这一份（ThreadListItem 按所在的组选）
        self.assertIn("<ThreadListItemPrimitive.Title", item)
        self.assertIn("<ThreadActivity />", item)  # 行尾在跑、未读圆点照旧
        # 悬停与选中同一个底色（会话列表靠主色字区分选中，设计文档 3.5），行高按变体：写在共用类 .cc-thread-row（标准化 B6）
        self.assertIn('className="cc-thread-row group/item"', item)
        row_css = read("globals.css").split("  .cc-thread-row {", 1)[1].split("\n  /*", 1)[0]
        self.assertIn("min-height: var(--cc-row-h);", row_css)
        self.assertIn(".cc-thread-row:is(:hover, :focus-visible, [data-active], :has(:focus-visible), :has([data-state=\"open\"])) { background: var(--cc-row-hover); }", row_css)
        self.assertIn(".cc-thread-row:is([data-active], :has(:focus-visible)) { color: var(--cc-ink); }", row_css)
        self.assertNotIn(":hover) { color", row_css)
        # 行尾「···」是行里的图标按钮 .cc-row-btn（24、圆角 6、淡色、图标 16；标准化 B6 / V29，原来是组件库 Button）
        more = thread_list.split('data-slot="aui_thread-list-item-more"\n', 1)[1].split("\n", 1)[0]
        self.assertIn('className="cc-row-btn absolute ', more)
        self.assertIn("<MoreHorizontalIcon />", thread_list)
        self.assertNotIn("@/components/ui/button", thread_list)
        self.assertNotIn("hover:text-", item)  # 键盘焦点同选中一样主色字（设计文档 3.5，见上面 .cc-thread-row），悬停不改字色
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
        # 三处单条置顶（行菜单、手机胶囊菜单、标题栏菜单）都经 toggleStar → starChat（第 10 轮审查：取消置顶回组顶由这一道记下；
        # 第 10 轮 B 段：失败写进侧栏出错行），starChat 用展开写法写 custom，别的键不冲掉
        self.assertEqual(both.count("toggleStar(aui.threadListItem(), !starred)"), 3)
        self.assertIn("void starChat(item, starred).catch((error: unknown) => setError(shownError(error)));", thread_list)
        self.assertNotIn("updateCustom({ starred", both)
        self.assertIn("const run = async () => void (await item.updateCustom({ ...custom, starred }));", read("lib/managed-agents/sidebar-order.ts"))
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
        # 唯一的行内样式是拖动算出的位移，只在共用拖动里（components/ui/drag.tsx），各处展开它给的属性
        for name in COMPANY_SOURCES:
            self.assertIsNone(colour.search(read(name)), name)
        drag = read("components/ui/drag.tsx")
        # 第 11 轮 B14 起位移、过渡不放进 node 的 style，由共用拖动直接写到那一项的节点上（applyShift），仍只此一处（第 12 轮：拖动区
        # 在经过的项变了时写，滑回途中不写）
        self.assertIn("applyShift(node, shiftStyle({ x: 0, y }, transition));", drag)
        self.assertIn('return { transform: settling || (!x && !y) ? "" : `translate3d(${x}px, ${y}px, 0px)`, transition: transition ?? "" };', drag)
        self.assertIn("if (!d || d.settle) return;", drag.split("  private shifts() {", 1)[1].split("\n  }\n", 1)[0])
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
        # 手机没有下巴（用户 10-17：基础信息放上面，同线上旧版）
        self.assertIn("const { isMobile } = useSidebar();", chin)
        self.assertIn("if (!kind || isMobile) return null;", chin)
        self.assertIn("return fresh ? <DraftChin /> : <SessionChin />;", chin)
        # 新对话：「公司 ▾」选公司（至少有一家公司才出现），下巴里菜单向上开；月份、分店是草稿
        draft = projects.split('const DraftChin: FC<{ side?: "top" | "bottom" }> = ({ side = "top" }) => {', 1)[1].split("\n};", 1)[0]
        self.assertIn("if (!(sidebarHasOne || any || draft.project)) return null;", draft)
        self.assertIn('<DropdownMenu.Content side={side} align="start" sideOffset={menuOffset()} className="cc-menu min-w-(--cc-menu-w-lg) max-w-(--cc-menu-w-max)">', draft)
        self.assertIn('<PeriodChip look="cc-chin-btn" side={side} value={draft.period} onChange={(period) => setDraft({ ...draft, period })} />',
                      draft)
        # 手机新对话：同一行放在问候下面（WelcomeFiling），菜单向下开；只在手机、Cowork、新对话
        welcome = block(projects, "export const WelcomeFiling: FC = () => {")
        self.assertIn("if (!kind || !fresh || !isMobile) return null;", welcome)
        self.assertIn('<div className="cc-welcome-filing" data-slot="cc-welcome-filing">\n      <DraftChin side="bottom" />', welcome)
        self.assertIn("<WelcomeFiling />", block(page, "function Welcome()"))
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
        self.assertIn('const change = (next: Partial<Pick<Filing, "period" | "scope">>) => void fileSession(remoteId, next).catch(report);',
                      projects.split("const FilingChips: FC<", 1)[1].split("\n};", 1)[0])
        # 用户 10-17：没挂公司的已有对话不能再归到公司——下巴空着（新对话开之前在下巴 / 手机问候下面选公司照旧）
        self.assertIn("if (!project) return null;", session)
        self.assertNotIn("UnfiledChin", projects)
        # 手机已有对话：「…」里的「处理月份」「分店」子菜单（挂在公司下才有；分店只在不止一个分店时），各项与胶囊共用
        mobile = block(projects, "export const MobileFilingItems: FC = () => {")
        self.assertIn("if (!kind || !remoteId || !filing.project) return null;", mobile)
        self.assertIn('<SubMenu icon={<CalendarIcon />} label={co.period}', mobile)
        self.assertIn("<PeriodChoices value={filing.period} onChange={(period) => change({ period })} />", mobile)
        self.assertIn("{withScope && branches.length > 1 && (", mobile)
        self.assertIn("<BranchChoices branches={branches} value={filing.scope} onChange={(scope) => change({ scope })} />", mobile)
        self.assertNotIn("MoveToMenu", mobile)
        chips = read("components/period-chip.tsx")
        self.assertIn("<PeriodChoices value={value} onChange={onChange} allowNone={allowNone} />",
                      chips.split("export const PeriodChip: FC<", 1)[1].split("\n};", 1)[0])
        self.assertIn("<BranchChoices branches={branches} value={value} onChange={onChange} />",
                      chips.split("export const BranchChip: FC<", 1)[1].split("\n};", 1)[0])
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
        # 附件一行从输入那一列开始（与输入的字对齐，照 Claude 桌面版 7.webp：离框左约 44、离框顶约 12，下隔 16；第 11 轮按钮排查 M7）
        self.assertIn('grid-template-areas: ". files files" "add input send";', css)
        self.assertIn("grid-area: files; padding: calc(var(--cc-composer-pad) / 2) var(--cc-composer-pad) var(--cc-space-16);", css)
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
// 本页改月份、分店：发 move，带它现在的公司（对话不能换公司，用户 10-17；不改置顶）；服务端拒了就回到原来的
await fileSession("sesn_aaa001", { period: "2026-07", scope: "00002" });
assert.deepEqual(calls("move").at(-1).body, { session_id: "sesn_aaa001", project: "comp_new", period: "2026-07", scope: "00002" });
assert.deepEqual(filingOf("sesn_aaa001"), { project: "comp_new", period: "2026-07", scope: "00002" });
site.moveFails = true;
await assert.rejects(fileSession("sesn_aaa001", { period: "2026-06" }));
assert.deepEqual(filingOf("sesn_aaa001"), { project: "comp_new", period: "2026-07", scope: "00002" });
site.moveFails = false;
// 在「最近」的对话没有月份、分店：不发
const moved = calls("move").length;
await fileSession("sesn_bbb001", { period: "2026-07" });
assert.equal(calls("move").length, moved);
assert.equal(calls("star").length, 0);
""")

    def test_chats_given_early_for_a_manual_order_do_not_count_as_the_page_end(self):
        # 审查：服务端第一页把组里手排过的旧对话一并给（ordered）。核对「本该在第一页却没有」时按它们算这组翻到哪儿，会把
        # 「显示更多」翻到、比它们新的那几条当成在别处删了
        self.run_page(r"""
import { reconcile } from "@/lib/managed-agents/list-sync";
const abc = (id: string, m: number) => row(id, id, m, { project: "comp_abc" });
site.sessions = [abc("sesn_abc001", 1), abc("sesn_abc002", 2), abc("sesn_abc006", 6), abc("sesn_abc007", 7), abc("sesn_old100", 100)];
site.groups = { comp_abc: "x" };
const { core, threads } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
const quiet = { note: () => {}, stop: () => {} };
const first = { sessions: [abc("sesn_abc001", 1), abc("sesn_abc002", 2), abc("sesn_old100", 100)], nextCursor: null, groups: { comp_abc: "x" } };
reconcile(threads, { ...first, ordered: ["sesn_old100"] } as any, Date.now(), quiet);
await g.sleep(30);
for (const id of ["sesn_abc006", "sesn_abc007", "sesn_old100"]) assert.ok(core.threadIds.includes(id), id);
// 对照：不标出来时按 100 分钟前算，6、7 分钟前的被当成删了
reconcile(threads, first as any, Date.now(), quiet);
await g.sleep(30);
assert.ok(!core.threadIds.includes("sesn_abc006"));
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
        head = projects.split("export const ProjectHeader = memo(function ProjectHeader(", 1)[1].split("\n});\n", 1)[0]
        self.assertIn("onClick={() => go(() => openPending(project.id))}", head)   # 待处理数：只看这家
        # 右侧：待处理数、···（分组和排序）、新建（新建在最右，悬停才出）
        order = [head.index(x) for x in ('className="cc-group-count"', "<GroupSortMenu open={menuOpen} modal={menuModal} onOpenChange={setMenuOpen} alignHead={isMobile} />", "<GroupNew ")]
        self.assertEqual(order, sorted(order))
        css = read("globals.css")
        self.assertIn(".cc-group-more, .cc-group-new { display: none; }", css)
        # 「···」菜单开着时（指针、焦点都在菜单里）「＋」照样露着、「···」不挪位（第 5 轮视觉审查：最近组打开「···」后「＋」消失）
        self.assertIn('.cc-group-row:is(:hover, :focus-within, [data-open], :has(.cc-group-more[data-state="open"])) :is(.cc-group-more, .cc-group-new)', css)
        self.assertIn('[data-slot="sidebar-inner"]:hover .cc-group-caret', css)
        self.assertIn(".cc-group-row:hover .cc-group-toggle, .cc-group-toggle:focus-visible { color: var(--cc-ink-2); }", css)
        # 触屏平板（宽屏、不能悬停）与手机：箭头、组操作常显
        self.assertIn('@media (hover: none) {\n  [data-slot="sidebar"] { --cc-reveal-rest: 1; /* 现有值·CC 自定：常显 */ }\n'
                      '  [data-slot="sidebar"] :is(.cc-group-more, .cc-group-new) { display: inline-flex; }', css)
        self.assertIn(':root[data-cc-variant="phone"] :is(.cc-group-more, .cc-group-new) { display: inline-flex; }', css)
        self.assertNotIn(".cc-project-name", css + projects)

    def test_company_headers_follow_the_user_sidebar_of_2026_10_15(self):
        # 用户 2026-10-15 夜（参考 DeepSeek 桌面端侧栏）：「只改我提到的地方 其它不要动」
        group = read("components/assistant-ui/sidebar-group.tsx")
        projects = read("components/assistant-ui/projects.tsx")
        rows = read("components/assistant-ui/thread-list.tsx")
        css = read("globals.css")
        head = projects.split("export const ProjectHeader = memo(function ProjectHeader(", 1)[1].split("\n});\n", 1)[0]
        header = group.split("export const GroupHeader: FC", 1)[1].split("\n);\n", 1)[0]
        # A：公司组头最左是公司图标（同原来「打开公司」那个 Building2Icon），然后公司名；名字同会话行标题的字号字色，
        #    这家的对话行缩进到公司名起点；已置顶 / 最近 / 月份不带图标、不缩进
        self.assertIn('icon={<Building2Icon className="cc-group-icon" aria-hidden />}', head)
        self.assertLess(header.index("{icon}"), header.index('<Marquee className="cc-group-name">{label}</Marquee>'))
        self.assertLess(header.index("cc-group-name"), header.index("cc-group-caret"))
        self.assertIn(".cc-group-icon { flex-shrink: 0; width: var(--cc-icon-sm); height: var(--cc-icon-sm); margin-inline-end: var(--cc-row-gap); color: var(--cc-ink-3); }", css)
        self.assertIn(".cc-project-row .cc-group-toggle { color: var(--cc-ink-2); font-size: var(--cc-row-font); line-height: var(--cc-row-lh); font-weight: normal; }", css)
        self.assertIn('[data-slot="cc-project-items"] { --cc-row-indent: calc(var(--cc-icon-sm) + var(--cc-space-4) + var(--cc-row-gap)); }', css)
        self.assertIn(".cc-row-trigger { gap: var(--cc-space-4); padding-inline: calc(var(--cc-row-pad) + var(--cc-row-indent, 0px)) var(--cc-space-4); }", css)
        self.assertIn('.cc-drawer [data-slot="aui_thread-list-item-trigger"] { padding-inline-start: calc(var(--cc-space-12) + var(--cc-row-indent, 0px)); }', css)
        self.assertIn("margin-inline: calc(var(--cc-row-pad) + var(--cc-row-indent, 0px) - var(--cc-inline-edit-px)) var(--cc-space-4);", css)
        # 「显示更多」也缩进到公司名文字起点（用户 10-16 看预览后定）
        self.assertIn("padding-inline: calc(var(--cc-row-pad) + var(--cc-row-indent, 0px)) var(--cc-row-pad);", css)
        self.assertIn('.cc-drawer [data-slot="cc-project-items"] .cc-show-more { padding-inline-start: calc(var(--cc-space-12) + var(--cc-row-indent, 0px)); }', css)
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
        self.assertIn("<GroupSortMenu open={menuOpen} modal={menuModal} onOpenChange={setMenuOpen} alignHead={isMobile} />", head)
        # 长按出菜单；拿起后手指动了（真的在拖）就收起菜单（第 6 轮第 2 项）
        self.assertIn("const { open: menuOpen, setOpen: setMenuOpen, modal: menuModal, press } = useLongPressMenu();", head)
        self.assertIn("onMenuKey={() => setMenuOpen(true)}", head)
        menu = group.split("export const GroupSortMenu = memo(function GroupSortMenu(", 1)[1]
        self.assertIn('className="cc-row-btn cc-group-more" aria-label={t("more")} title={t("more")}', menu)
        self.assertIn("<MoreHorizontalIcon />", menu)
        order = [menu.index(x) for x in ("sb.groupBy", "sb.sortBy", "sb.show", "{sb.resetDefaults}")]
        self.assertEqual(order, sorted(order))
        # 恢复默认设置：跟默认一样、也没手排过公司组 / 置顶（第 5 轮）才不可点
        self.assertIn("disabled={isDefaultView(view) && !manual}", menu)
        # D：「＋」照旧：在这家开新对话（月份 = 上个月）
        self.assertIn("<GroupNew onClick={onNew} />", head)
        self.assertIn("const onNew = useCallback(() => newChat.current(project.id), [project.id]);", head)
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
        self.assertIn("transform: translateX(var(--cc-marquee-x)); transition: transform var(--cc-marquee-dur) var(--cc-marquee-ease);", css)
        self.assertIn("--cc-marquee-ease: linear;", css)   # 匀速；运行时写入的平移与时长在运行时层登记了默认值 0
        self.assertIn("--cc-marquee-x: 0px;", css)
        self.assertIn("--cc-marquee-dur: 0ms;", css)
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
        item = rows.split("\nconst Row = memo(function Row(", 1)[1].split("\nconst RowMeta: FC", 1)[0]
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
        self.assertIn("animation: cc-pulse var(--cc-pulse) var(--cc-ease-pulse) infinite;", css)
        self.assertIn("--cc-ease-pulse: ease-in-out;", css)
        self.assertIn("width: var(--cc-row-pulse); height: var(--cc-row-pulse);", css)
        self.assertIn('[data-slot="aui_thread-list-item"][data-selected] { background: var(--cc-row-selected); color: var(--cc-ink); }', css)
        self.assertIn("[data-jump-hints] .cc-row-end[data-jump]::after { content: attr(data-jump); }", css)
        # 手机抽屉：行字仍 14（不再放大到 17），行与组标题带 44 由手机变体给
        drawer = css.split("/* 手机抽屉（设计文档 3.16）", 1)[1].split("/* 手机顶栏与抽屉里的圆按钮", 1)[0]
        self.assertNotIn("font-size", drawer)
        self.assertIn('.cc-drawer [data-slot="aui_thread-list-item"] { min-height: var(--cc-row-h);', drawer)

    def test_deleting_chats_asks_once_first(self):
        """删除对话先确认一次（用户 10-17，同 Claude 手机 app）：标题「删除对话？」/ 多选「删除 N 个对话？」、「取消 / 删除」、
        不加说明；四个入口和菜单里的 D 键都先问，弹窗挂在页面根上（菜单关、行卸载后仍在）。"""
        host = (SRC / "components/delete-chats.tsx").read_text(encoding="utf-8")
        app = (SRC / "assistant.tsx").read_text(encoding="utf-8")
        rows = (SRC / "components/assistant-ui/thread-list.tsx").read_text(encoding="utf-8")
        i18n = (SRC / "i18n.ts").read_text(encoding="utf-8")
        self.assertIn("<DeleteChatsHost />", app)
        self.assertEqual(app.count("deleteChat(aui.threadListItem())"), 2)  # 电脑标题栏「···」、手机右上「…」
        self.assertIn("const remove = () => deleteChat(aui.threadListItem());", rows)  # 行「···」与手机长按菜单
        self.assertIn("askDelete(ids, () => runInOrder(ids,", rows)  # 多选「删除 N」
        for source in (app, rows):
            self.assertNotIn("onSelect={() => void aui.threadListItem().delete()}", source)
        self.assertNotIn("const deleteAll = () => void runInOrder(", rows)
        self.assertIn('n > 1 ? `删除 ${n} 个对话？` : "删除对话？"', i18n)
        self.assertIn('n > 1 ? `Delete ${n} chats?` : "Delete chat?"', i18n)
        self.assertIn('n > 1 ? `ลบ ${n} แชท?` : "ลบแชท?"', i18n)
        self.assertIn('{t("cancel")}', host)
        self.assertIn('{t("delete")}', host)
        # 不加说明（第 11 轮 E37：原来挂一个和标题一样的隐藏说明，读屏念两遍）
        self.assertNotIn("Description", host)
        self.assertIn("aria-describedby={undefined}", host)
        self.assertIn("requestAnimationFrame(", host)  # 等菜单收起再开

    def test_multi_select_has_no_bulk_bar_and_runs_one_by_one(self):
        rows = read("components/assistant-ui/thread-list.tsx")
        self.assertNotIn("BulkBar", rows)
        bulk = rows.split("const BulkItems: FC", 1)[1]
        self.assertIn("sb.pinN(ids.length)", bulk)
        self.assertIn("sb.unpinN(ids.length)", bulk)
        self.assertIn("sb.deleteN(ids.length)", bulk)
        self.assertEqual(bulk.count("runInOrder("), 2)
        self.assertIn("await starChat(item, !allPinned, [...back]);", bulk)   # starChat 保留 custom 里别的键
        # 用户 10-17：多选菜单的「移到… (N)」删了（对话不能换公司）
        self.assertNotIn("BulkMoveMenu", rows)
        projects = read("components/assistant-ui/projects.tsx")
        for gone in ("BulkMoveMenu", "MoveToMenu", "moveToN", "notIn("):
            self.assertNotIn(gone, projects)
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
        # ERP / DMS 的日期组开关也在 roving 那一组里（↑ ↓ 不跳过、Tab 不单独停）：电脑上同 Cowork 的组标题（GroupHeader 的开关带
        # data-roving，第 11 轮按钮排查 M6）
        day = rows.split("const DayGroups: FC", 1)[1].split("\n};\n", 1)[0]
        self.assertIn("<GroupHeader label={group.label} open={open} onToggle={() => toggle(group.key)} />", day)
        self.assertNotIn("cc-section-toggle", day)
        header = read("components/assistant-ui/sidebar-group.tsx").split("export const GroupHeader", 1)[1].split("\n);", 1)[0]
        self.assertIn('data-roving=""\n      data-slot="aui_thread-list-group-label"', header)
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


# 多台电脑（ADR-021 第 6 批）：1.7.0 的桥（features 含 devices，state().device 带设备密钥），成员与 register 的回复由测试给
MULTI_DESKTOP_ENV = r"""
const g = globalThis as any;
g.qt = { webChannelTransport: {} };
g.bridgeCalls = [] as any[];
g.STATE = { version: "1.7.0", site: "", express_roots: ["C:\\\\EXP"], members: [], autostart: false, hotkey: true,
  update: { state: "", version: "" }, features: ["company_folders", "devices"], legacy_roots: [],
  device: { key: "K".repeat(43), name: "ACC-02" } };
g.tomoDesktop = {
  state(cb: (json: string) => void) { cb(JSON.stringify(g.STATE)); },
  call(method: string, args: string, cb: (json: string) => void) {
    g.bridgeCalls.push({ method, args: JSON.parse(args) });
    cb(JSON.stringify({ ok: true }));
  },
  changed: { connect() {}, disconnect() {} },
  done: { connect() {}, disconnect() {} },
};
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class MultiDesktopBehaviourTests(unittest.TestCase):
    """对话页每次打开带设备密钥调 register v2（第 5 节第 7 条的页面一侧）、exists 时按本机成员决定要不要 rekey、旧版的
    update_needed；读者编号写进文件夹下拉的值、主档页按电脑名认本机那一行。"""

    def run_page(self, body):
        stubs = {"socket.io-client": "export function io() { return { on() { return this; }, off() { return this; }, connected: true }; }",
                 "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;',
                 "cc-multi-desktop-env": MULTI_DESKTOP_ENV}
        run_ts('import "cc-test-setup";\nimport "cc-multi-desktop-env";\nimport assert from "node:assert/strict";\n'
               'const g = globalThis as any;\n' + body + "\n(globalThis as any).done();\n", stubs=stubs)

    def test_every_open_registers_with_this_computers_device_key(self):
        self.run_page(r"""
import { connectComputer, currentLink, desktop, needsKey, thisComputer } from "@/lib/desktop";
const REGISTER = "/api/method/pearnly_cowork.companion.api.register";
const ok = (message: any) => ({ ok: true, status: 200, json: async () => ({ message }) });
let replies: any[] = [];
g.respond = async (method: string) => (method === REGISTER ? ok(replies.shift()) : ok(null));
const registered = () => g.calls.filter((c: any) => c.method === REGISTER).map((c: any) => c.body);
const pairs = () => g.bridgeCalls.filter((c: any) => c.method === "pair");
const api = (await desktop())!;
assert.deepEqual(thisComputer(g.STATE), { key: "K".repeat(43), name: "ACC-02" });
assert.equal(thisComputer({ ...g.STATE, features: ["company_folders"] }), null);   // 1.6 的桥：走旧路

// 1) 新电脑：带设备密钥与电脑名登记，created 的密钥交给桌面端配对
replies = [{ status: "created", token: "exp_C1_new" }];
await connectComputer(api);
let body = registered().at(-1);
assert.equal(body.device_key, "K".repeat(43));
assert.equal(body.device_name, "ACC-02");
assert.equal(body.express, true);
assert.ok(!("rekey" in body) && !("replace" in body));
assert.equal(pairs().at(-1).args.code.split("@")[0], "exp_C1_new");
assert.equal(currentLink().kind, "idle");

// 2) 每次打开都登记（服务端记最近登录的人）：成员都好着、回 exists → 不配对、不 rekey
g.STATE.members = [{ id: "m1", name: "Express", kind: "express", paused: false, status: "online", folders: null }];
assert.equal(needsKey(g.STATE), false);
replies = [{ status: "exists", device: "CCD-1" }];
let before = pairs().length;
await connectComputer(api);
assert.equal(registered().length, 2);
assert.equal(pairs().length, before);
assert.equal(currentLink().kind, "idle");

// 3) 服务端说这把密钥不再用（superseded）：带 rekey 登记，新密钥交给桌面端（同一条连接的成员换上，device_state 清掉）
g.STATE.members = [{ ...g.STATE.members[0], status: "attention", device_state: "superseded" }];
replies = [{ status: "created", token: "exp_C1_rekeyed" }];
await connectComputer(api);
assert.equal(registered().at(-1).rekey, true);
assert.equal(pairs().at(-1).args.code.split("@")[0], "exp_C1_rekeyed");

// 4) 本机没有成员（重装过）、服务端说这台已有密钥（exists）：换一个请求号再带 rekey 登记一次
g.STATE.members = [];
replies = [{ status: "exists", device: "CCD-1" }, { status: "created", token: "exp_C1_back" }];
await connectComputer(api);
const [first, second] = registered().slice(-2);
assert.ok(!first.rekey && second.rekey === true);
assert.notEqual(first.request_key, second.request_key);
assert.equal(pairs().at(-1).args.code.split("@")[0], "exp_C1_back");

// 5) 配对码抄到两台（claimed_elsewhere）：这台登记成自己的电脑（不 rekey），created 就配对
g.STATE.members = [{ id: "m1", name: "Express", kind: "express", paused: false, status: "attention", folders: null,
                     device_state: "claimed_elsewhere" }];
replies = [{ status: "created", token: "exp_C1_own" }];
await connectComputer(api);
assert.ok(!registered().at(-1).rekey);
assert.equal(pairs().at(-1).args.code.split("@")[0], "exp_C1_own");

// 6) 不是登记人也不是管理员 rekey：服务端 403 → 现有「需要管理员账号」
g.STATE.members = [];
g.respond = async (method: string) => (method === REGISTER
  ? (registered().length % 2 ? ok({ status: "exists" }) : { ok: false, status: 403, json: async () => ({}) }) : ok(null));
await connectComputer(api);
assert.equal(currentLink().kind, "not_admin");

// 7) 旧版桥（没有 devices）：不带设备密钥；池里已有新版认领过的电脑 → 需更新
g.STATE.features = ["company_folders"];
g.respond = async (method: string) => (method === REGISTER ? ok({ status: "update_needed" }) : ok(null));
before = pairs().length;
await connectComputer(api);
assert.ok(!("device_key" in registered().at(-1)));
assert.equal(currentLink().kind, "update_needed");
assert.equal(pairs().length, before);

// 8) 被「电脑」列表移除过（第 6 批审查第 2 条）：打开页面的自动登记不带 rejoin，服务端只回 removed，不配对；
//    人在设置里点「连接」才带 rejoin，作为新电脑回来
g.STATE.features = ["company_folders", "devices"];
g.STATE.members = [{ id: "m1", name: "Express", kind: "express", paused: false, status: "error", folders: null }];
replies = [{ status: "removed", device: "CCD-1" }];
g.respond = async (method: string) => (method === REGISTER ? ok(replies.shift()) : ok(null));
before = pairs().length;
await connectComputer(api);
assert.ok(!("rejoin" in registered().at(-1)));
assert.equal(currentLink().kind, "removed");
assert.equal(pairs().length, before);
replies = [{ status: "created", token: "exp_C1_rejoined" }];
await connectComputer(api, { rejoin: true });
assert.equal(registered().at(-1).rejoin, true);
assert.equal(pairs().at(-1).args.code.split("@")[0], "exp_C1_rejoined");
assert.equal(currentLink().kind, "idle");
""")

    def test_reader_ids_go_into_folder_values_and_this_computers_row_is_found_by_name(self):
        self.run_page(r"""
import { folderIsMine, folderParts, folderValue, taskSeedOf } from "@/lib/companies";
// 读者编号本身带竖线、路径可能带竖线和反斜杠：编码后拆得回原样
const value = folderValue("CCD-2|CWEC-1", "r1", "ABC|x\\2569");
assert.deepEqual(folderParts(value), { reader: "CCD-2|CWEC-1", root_id: "r1", folder: "ABC|x\\2569" });
assert.deepEqual(folderParts(folderValue("CWEC-1", "r1")), { reader: "CWEC-1", root_id: "r1", folder: "" });
// 同一条连接上两台电脑各绑一行：本机按电脑名认（服务端给了 mine 以它为准）；旧桌面版没有电脑名时照旧按连接
const mine = { reader: "CWEC-1", device: "ACC-02", reader_id: "CCD-2|CWEC-1", label: "D:\\A", state: "ok" as const };
const theirs = { ...mine, device: "ACC-01", reader_id: "CCD-1|CWEC-1" };
assert.equal(folderIsMine(theirs, ["CWEC-1"], "ACC-02"), false);
assert.equal(folderIsMine(mine, ["CWEC-1"], "ACC-02"), true);
assert.equal(folderIsMine(theirs, ["CWEC-1"]), true);
assert.equal(folderIsMine({ ...theirs, mine: true }, [], "ACC-02"), true);
// 主档页「＋」预填：本机那一行的读者编号进文件夹的值；读它的电脑已被移除的那一行不拿来预填
const detail: any = { company: { id: "c", title: "ABC", adapter: "express" },
  account_sets: [{ connection: "CWEC-1", adapter: "express", key: "k69", label: "ABC 69", period_start: "2026-01-01", period_end: "2026-12-31" }],
  folders: [{ ...theirs, root_id: "r1", folder: "x" }, { ...mine, root_id: "r2", folder: "y" }] };
assert.deepEqual(folderParts(taskSeedOf(detail, "2026-09", ["CWEC-1"], true, "ACC-02")!.folder!),
  { reader: "CCD-2|CWEC-1", root_id: "r2", folder: "y" });
detail.folders[1].reader_gone = true;
assert.equal(taskSeedOf(detail, "2026-09", ["CWEC-1"], true, "ACC-02")!.folder, undefined);
""")


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
        # 数字会计员第 2 期：公司规则写明「全公司 / 分店 X」、付款可直接改；账套行与历史行多一个「设为全公司」。
        # 用户 10-17 改版：收起时一条一行（对方名 + 右侧的值），按钮、说明都在点开以后
        self.run_page(r"""
import { promoteChoice } from "@/lib/companies";
import { choiceLine, settledView } from "@/components/pending";
const row = (choice: any, promote = true, open = true) => renderToStaticMarkup(createElement(ChoiceRow, { choice, open, onSet() {}, onForget() {},
  ...(promote ? { onPromote() {} } : {}) }));
const rule = row({ key: "r1", source: "rule", kind: "payment", subject_label: "ACME", value: "cash", editable: true, branch: "", set_by: "a" });
assert.ok(!rule.includes("<select"));   // 两段式代替下拉框
assert.deepEqual(text(rule).slice(0, 3), ["ACME", "Cash", "Whole company · a"]);
assert.deepEqual(buttons(rule), ["ACME Cash", "Cash", "Credit", "Delete"]);   // 规则本身不再提升
assert.equal(text(row({ key: "r2", source: "rule", kind: "account", subject_label: "ACME", value: "5300", branch: "00012" }))[2],
  "Branch 00012");
assert.equal(text(row({ key: "r3", source: "rule", kind: "account", subject_label: "ACME", value: "5300", branch: "00000" }))[2],
  "Head office");
const express = { key: "k1", source: "express", kind: "payment", subject_label: "ACME", value: "credit", editable: true,
  year_label: "69", promotable: true };
assert.ok(buttons(row(express)).includes("Set for whole company"));
assert.ok(!buttons(row(express, false)).includes("Set for whole company"));   // 不给 onPromote 就不显示
assert.ok(!buttons(row({ ...express, promotable: false })).includes("Set for whole company"));   // 不能提升的不显示
assert.deepEqual(buttons(row(express, true, false)), ["ACME Credit"]);   // 收起时只有整行这一个按钮
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

    def test_saved_choices_are_one_line_each_and_open_one_at_a_time(self):
        # 用户 10-17「记住的选择照这个样子改」：收起时一条一行、值在右、不常驻按钮；整行是开合按钮（原生按钮，回车 / 空格开合），
        # 一张卡一次只开一条；点开后两段式改现 / 赊（调 onSet，点已选的那段不调）、删除调 onForget、设为全公司只在能提升时出现
        self.run_page(r"""
import { accountFull, choiceAfter, choiceFullLine, choiceValue, toggleChoice } from "@/components/company-cards";
// 不经渲染直接拿元素树（ChoiceRow 不用 hook），找出按钮按下去
const walk = (node: any, out: any[] = []): any[] => {
  if (Array.isArray(node)) node.forEach((n) => walk(n, out));
  else if (node && typeof node === "object") {
    if (node.type === "button") out.push(node);
    walk(node.props?.children, out);
  }
  return out;
};
const label = (b: any) => [b.props.children].flat().filter((c: any) => typeof c === "string").join("");
const calls: string[] = [];
const props = (choice: any, open: boolean, promote = true) => ({ choice, open, onToggle: () => calls.push("toggle"),
  onSet: (v: string) => calls.push("set:" + v), onForget: () => calls.push("forget"), ...(promote ? { onPromote: () => calls.push("promote") } : {}) });
const pay = { key: "k1", source: "express", kind: "payment", subject_label: "ACME", value: "cash", editable: true, year_label: "69", promotable: true };
// 收起：一行、整行一个按钮（aria-expanded=false），值在名字右边；没有说明、没有别的按钮
const closed = renderToStaticMarkup(createElement(ChoiceRow, props(pay, false)));
assert.match(closed, /^<div class="cc-choice" data-slot="cc-choice" data-source="express" data-choice-key="k1"><button type="button" class="cc-choice-row" data-slot="cc-choice-row" aria-expanded="false">/);
// 值也是渐隐的单行（同左边的对方名，Marquee）
assert.ok(closed.indexOf("ACME") < closed.indexOf('<span class="cc-marquee cc-choice-value" data-slot="cc-choice-value"><span class="cc-marquee-text">Cash</span>'));
assert.ok(!closed.includes("cc-choice-detail") && !closed.includes("<select"));
assert.equal(walk((ChoiceRow as any)(props(pay, false))).length, 1);
// 点开：整块填底（data-open）、aria-expanded=true；按钮依次是两段式、设为全公司、删除
const opened = renderToStaticMarkup(createElement(ChoiceRow, props(pay, true)));
assert.ok(opened.includes('data-open=""') && opened.includes('aria-expanded="true"'));
const [whole, cash, credit, promote, forget] = walk((ChoiceRow as any)(props(pay, true)));
assert.deepEqual([cash, credit, promote, forget].map(label), ["Cash", "Credit", "Set for whole company", "Delete"]);
assert.deepEqual([cash.props["aria-pressed"], credit.props["aria-pressed"]], [true, false]);
whole.props.onClick(); cash.props.onClick(); credit.props.onClick(); promote.props.onClick(); forget.props.onClick();
assert.deepEqual(calls, ["toggle", "set:credit", "promote", "forget"]);   // 点已选的「现购」不调 onSet
// 设为全公司：不能提升、或没给 onPromote 都不出现；只读的（历史）没有两段式，只有删除
assert.deepEqual(walk((ChoiceRow as any)(props({ ...pay, promotable: false }, true))).map(label).slice(1), ["Cash", "Credit", "Delete"]);
assert.deepEqual(walk((ChoiceRow as any)(props(pay, true, false))).map(label).slice(1), ["Cash", "Credit", "Delete"]);
const history = { key: "k2", source: "history", kind: "account", subject_label: "", supplier_name: "ACME", value: "53-05-25-00",
  value_label: "53-05-25-00 Repairs", account_label: "53-05-25-00 Repairs", count: 6, last_date: "2026-09-30", promotable: true };
assert.deepEqual(walk((ChoiceRow as any)(props(history, true))).map(label).slice(1), ["Set for whole company", "Delete"]);
// 值：科目（含 Express 历史）只写科目码，现 / 赊写选项名；完整写法「科目码 科目名」（名称没带码的补上，没有名称只写码）
assert.equal(choiceValue(history), "53-05-25-00");
assert.equal(choiceValue({ kind: "payment", value: "credit", value_label: "credit" }), "Credit");
// 代码类（对方档、存货、部门、收款科目）收起时也只写代码，完整的「代码 名称」放在点开后的说明（第 10 轮审查：280 宽里对方名被挤到约 90）
for (const kind of ["party", "item", "department", "receipt"]) {
  assert.equal(choiceValue({ kind, value: "S001", value_label: "S001 ACME" }), "S001", kind);
  assert.equal(choiceFullLine({ kind, value: "S001", value_label: "S001 ACME" }, "ACME"), "ACME → S001 ACME", kind);
}
// POS 版式的值是内部的 auto / person：不写
assert.equal(choiceValue({ kind: "pos_layout", value: "auto", value_label: "" }), "");
// Express 账套行的科目只有代码、没有名称：点开后不再写一遍「对方 → 代码」
assert.equal(choiceFullLine({ kind: "account", value: "53-05-25-00", value_label: "53-05-25-00" }, "ACME"), "");
assert.equal(choiceFullLine(history, "ACME"), "ACME → 53-05-25-00 Repairs");
assert.equal(choiceFullLine({ kind: "payment", value: "cash", value_label: "Cash" }, "ACME"), "");
assert.equal(accountFull(history), "53-05-25-00 Repairs");
assert.equal(accountFull({ value: "5300", value_label: "Repairs" }), "5300 Repairs");
assert.equal(accountFull({ value: "5300", value_label: "5300" }), "5300");
// 一次只开一条：点开着的收起，点别的换成它
assert.equal(toggleChoice("", "k1"), "k1");
assert.equal(toggleChoice("k1", "k1"), "");
assert.equal(toggleChoice("k1", "k2"), "k2");
// 请求没回来时：点开那一条的按钮再点不理（连点「删除」不会发两次）；不设 disabled（第 11 轮 D28：设了刚按的那一格失焦，
// 键盘焦点掉到页面别处），按钮行标 aria-busy
calls.length = 0;
const busyRow = walk((ChoiceRow as any)({ ...props(pay, true), busy: true })).slice(1);
assert.ok(busyRow.every((b) => !b.props.disabled));
busyRow.forEach((b) => b.props.onClick());
assert.deepEqual(calls, []);
assert.match(renderToStaticMarkup(createElement(ChoiceRow, { ...props(pay, true), busy: true })), /<div class="cc-card-actions" aria-busy="true">/);
assert.doesNotMatch(renderToStaticMarkup(createElement(ChoiceRow, props(pay, true))), /aria-busy|disabled/);
// 点开后对方名整段换行显示（手机上没有悬停滚动，收起时看不全）
assert.match(renderToStaticMarkup(createElement(ChoiceRow, props(pay, true))), /<span class="cc-choice-name">ACME<\/span>/);
// 删掉 / 提升走了一条后焦点放到下一条，没有下一条放上一条，一条不剩回 ""
assert.equal(choiceAfter(["a", "b", "c"], "b"), "c");
assert.equal(choiceAfter(["a", "b", "c"], "c"), "b");
assert.equal(choiceAfter(["a"], "a"), "");
""")
        # 卡片管展开：一次一条；删掉 / 设为全公司成功后那条不在了，展开着的收起
        cards = read("components/company-cards.tsx")
        section = cards[cards.index("export const ChoicesSection"):cards.index("export const ruleScope")]
        self.assertIn("open={open === c.key}", section)
        self.assertIn("onToggle={() => setOpen((o) => toggleChoice(o, c.key))}", section)
        self.assertIn("if (gone) setOpen((o) => (o === gone ? \"\" : o));", section)
        self.assertIn("busy={busy === c.key}", section)
        # 两段式点下立即显示新选择，没成功这一条退回原样、卡内报错（第 11 轮 D28）
        change = section[section.index("const change = async (c: Choice, v: string) => {"):]
        self.assertLess(change.index("setList((rows) => setChoiceShown(rows, c.key, v));"),
                        change.index("await setChoice(company, c.key, v, period);"))
        # 第 11 轮审查：没成功只退回这一条的值（不整条换回点下那一刻的）；请求期间整页重取到的旧值不把刚选的盖回去
        self.assertIn("setList((rows) => setChoiceShown(rows, c.key, c.value));", change)
        self.assertNotIn("row.key === c.key ? c : row", change)
        self.assertIn("useEffect(() => setList(withChanging(choices ?? [])), [choices]);", section)
        self.assertLess(change.index("changing.current.set(c.key, v);"), change.index("await setChoice(company, c.key, v, period);"))
        self.assertIn("void reload().finally(() => changing.current.get(c.key) === v && changing.current.delete(c.key));", change)
        self.assertIn("onSet={(v) => void change(c, v)}", section)
        self.assertIn("if (gone) setFocusKey(choiceAfter(", section)
        self.assertIn('row?.querySelector<HTMLElement>(".cc-choice-row")?.focus();', section)
        self.assertEqual(section.count("(rows) => rows.filter((row) => row.key !== c.key), c.key)"), 2)
        # 电脑行约 28（小号控件）、手机 44；点开后的按钮是卡片按钮（手机 48 胶囊）
        css = read("globals.css")
        self.assertIn("min-height: var(--cc-control-sm); padding: var(--cc-space-2) var(--cc-space-4);", block(css, ".cc-choice-row {"))
        self.assertIn(':root[data-cc-touch] .cc-choice-row { min-height: var(--cc-tap); }', css)
        self.assertIn(".cc-choice[data-open] { background: var(--cc-bg-hover); }", css)
        # 点开的整块已是悬停底，行自己不再叠一层；值最多占四成五（左边的对方名留得下）
        self.assertIn(".cc-choice[data-open] .cc-choice-row:hover { background: transparent; }", css)
        self.assertIn("max-width: var(--cc-choice-value-max);", block(css, ".cc-choice-value {"))
        self.assertIn("--cc-choice-value-max: 45%;", css)
        self.assertNotIn(".cc-panel-card-body .cc-choice-row", css)

    def test_a_saved_choice_says_why_on_its_own_row(self):
        # 第 11 轮 F40：断网时改「现购 | 赊购」值退回原值，原因「出了点问题，请重试。」写在整张卡最底下（Express 历史下面，右侧栏没滚动
        # 时在视区外，y=1047 > 768），只看到值跳回去。现在原因写在出错的那一条里：点开着时紧挨按钮下面，收着时在行下面；
        # 「删除」「设为全公司」没成功同样
        self.run_page(r"""
const pay = { key: "k1", source: "express", kind: "payment", subject_label: "ACME", value: "cash", editable: true, year_label: "69", promotable: true };
const props = (open: boolean, error = "") => ({ choice: pay, open, error, onToggle() {}, onSet() {}, onForget() {}, onPromote() {} });
const opened = renderToStaticMarkup(createElement(ChoiceRow, props(true, "Something went wrong. Try again.")));
const alert = '<p role="alert" class="cc-choice-error text-danger" data-slot="cc-choice-error">Something went wrong. Try again.</p>';
assert.ok(opened.includes('</button></div>' + alert + '</div></div>'), opened);       // 按钮行（cc-card-actions）紧接着就是原因，在这一条里
assert.ok(opened.indexOf('data-slot="cc-choice-forget"') < opened.indexOf(alert));
const closed = renderToStaticMarkup(createElement(ChoiceRow, props(false, "Something went wrong. Try again.")));
assert.ok(closed.endsWith('</button>' + alert + '</div>'), closed);                    // 收着：行下面
assert.ok(!renderToStaticMarkup(createElement(ChoiceRow, props(true))).includes('role="alert"'));
""")
        cards = read("components/company-cards.tsx")
        section = cards[cards.index("export const ChoicesSection"):cards.index("export const ruleScope")]
        # 原因按条记，只交给出错的那一条；卡片底下不再有一行原因
        self.assertIn('const [error, setError] = useState<{ key: string; message: string } | null>(null);', section)
        self.assertIn('error={error?.key === c.key ? error.message : ""}', section)
        self.assertIn("setError({ key, message: shownError(e) });", section)            # 删除、设为全公司
        self.assertIn("setError({ key: c.key, message: shownError(e) });", section)     # 现 / 赊
        self.assertNotIn('{error && <p role="alert"', section)
        css = read("globals.css")
        self.assertIn(".cc-choice-detail > .cc-choice-error { padding-top: var(--cc-space-6); }", css)

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
        self.assertIn("taskSeed: taskSeedOf(detail, period, knownReaders(), app && companyFolders, deviceName),",
                      read("components/company-cards.tsx"))

    def test_branches_group_account_sets_and_saved_choices_show_where_they_came_from(self):
        self.run_page(r"""
import { formatWhen } from "@/lib/companies";
const sets = [{ connection: "c", key: "69HQ", label: "ABC 69", year_label: "69", period_start: "2025-10-01", period_end: "2026-09-30" },
  { connection: "c", key: "69B1", label: "ABC 69 B1", year_label: "69" }];
const branches = [{ branch: "00000", label: "", account_sets: [sets[0]] }, { branch: "00001", label: "Rama 9", account_sets: [sets[1]] }];
assert.deepEqual(text(html(SetsSection, { sets, branches, period: "2026-09" })),
  ["Account sets", "2 account sets", "Head office", "ABC 69", "69", "Rama 9", "ABC 69 B1", "69"]);   // 卡头摘要：账套数
// 付款方式能直接改（点开后两段式）；从 Express 历史学到的只读、可删，写明来源；收起时一行 = 对方名 + 值（科目只写科目码）
const pay = (open: boolean) => renderToStaticMarkup(createElement(ChoiceRow, { choice: { key: "k1", source: "express", kind: "payment",
  subject_label: "ACME", value: "credit", editable: true, year_label: "69" }, open, onSet() {}, onForget() {} }));
assert.deepEqual(text(pay(false)), ["ACME", "Credit"]);
assert.deepEqual(text(pay(true)), ["ACME", "Credit", "69", "Cash", "Credit", "Delete"]);
assert.ok(pay(true).includes('aria-pressed="true">Credit</button>'));
const learned = (open: boolean) => renderToStaticMarkup(createElement(ChoiceRow, { choice: { key: "k2", source: "history", kind: "account",
  subject_label: "", supplier_name: "ACME", value: "5320", account_label: "5320 Utilities", count: 9, last_date: "2026-09-30" },
  open, onSet() {}, onForget() {} }));
assert.deepEqual(text(learned(false)), ["ACME", "5320"]);
assert.deepEqual(text(learned(true)), ["ACME", "5320", "From Express history · 9 · Sep 30, 2026", "ACME → 5320 Utilities", "Delete"]);
// 按公司记的入账方式（可改、可删）、现用账套（只删，改选走待处理 / CC）与 Express 历史定下的入账方式（只删）：用户 2026-10-17
// 「加上，方便看和删」。行样式以外壳为准：展开的入账方式行出两段按钮，现用账套行没有分段、只有删除
import { choiceSection, setChoiceShown } from "@/components/company-page";
import { co } from "@/i18n";
const posting = { key: "p1", source: "policy", kind: "posting", subject_label: "Goods documents", value: "stock", editable: true };
const book = { key: "p2", source: "policy", kind: "current_book", subject_label: "Current year · Head office", value: "s:\\69exp\\abc",
  value_label: "ABC 69", year_label: "69EXP", editable: false };
const learnedPosting = { key: "p3", source: "history", kind: "posting", subject_label: "ACME", value: "service",
  value_label: "Post as expense", editable: false, count: 4 };
assert.deepEqual([choiceSection(posting), choiceSection(book), choiceSection(learnedPosting),
  choiceSection({ source: "history", kind: "account" })],
  ["posting", "current_book", "posting_history", "history"]);
assert.deepEqual([co.kind.posting, co.kind.current_book, co.kind.posting_history],
  ["Posting", "Account set in use", "Posting history"]);
// 入账方式两段可改（选项在 EDITABLE）：改后显示名换成刚选的；现用账套不在 EDITABLE，显示名不动
assert.equal(setChoiceShown([posting], "p1", "service")[0].value_label, "Post as expense");
assert.equal(setChoiceShown([posting], "p1", "stock")[0].value_label, "Post to stock");
assert.equal(setChoiceShown([book], "p2", "s:\\68exp\\abc")[0].value_label, "ABC 69");
const policyRow = (choice: Record<string, unknown>, open: boolean) => renderToStaticMarkup(createElement(ChoiceRow, { choice, open,
  onSet() {}, onForget() {} }));
assert.deepEqual(text(policyRow(posting, false)), ["Goods documents", "Post to stock"]);
assert.deepEqual(text(policyRow(posting, true)), ["Goods documents", "Post to stock", "Post to stock", "Post as expense", "Delete"]);
assert.ok(policyRow(posting, true).includes('aria-pressed="true">Post to stock</button>'));
assert.ok(!policyRow(book, true).includes("cc-choice-seg"));
assert.deepEqual(text(policyRow(book, true)), ["Current year · Head office", "ABC 69", "69EXP", "Delete"]);
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
        self.assertIn("font-weight: var(--cc-weight-medium)", title)
        self.assertIn("--cc-title-font: var(--code-text-footnote);", css)      # 13
        self.assertIn("--cc-title-lh: var(--code-text-panel-lh);", css)        # 19
        self.assertIn("font-size: var(--cc-title-font); line-height: var(--cc-title-lh); font-weight: var(--cc-weight-medium);",
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


# 假站点加上个人排列（layout / save_layout，服务端按项合并、交 null 的项删掉）与公司列表（projects）
PIN_RACE_SITE = r"""
const merge = (old: any, sent: any) => {
  const out: any = { ...(old ?? {}) };
  for (const [part, fields] of Object.entries(sent)) {
    out[part] = { ...(out[part] ?? {}) };
    for (const [field, value] of Object.entries(fields as any)) {
      if (value === null) delete out[part][field];
      else out[part][field] = value;
    }
  }
  return out;
};
let server: any = null;
let projectsReply: any = null;
const listingReply = g.respond;
g.respond = async (method: string, args: any, body: any) => {
  if (method === "layout") return ok({ prefs: server });
  if (method === "save_layout") { server = merge(server, body.prefs); return ok({ prefs: server }); }
  if (method === "projects") return ok(projectsReply);
  return listingReply(method, args, body);
};
const saves = () => g.calls.filter((c: any) => c.method === "save_layout").map((c: any) => c.body.prefs);
const realNow = performance.now.bind(performance);
let skew = 0;
(performance as any).now = () => realNow() + skew;
// 侧栏按数据收拾：照抄 thread-list.tsx CompanyGroups 里那两个副作用（行 = 组件库此刻的对话列表；没有拖进已置顶还没回的）
const tidy = (core: any) => {
  const order = useSidebarOrder.getState();
  const unpinned: string[] = [];
  const pinnedNow: string[] = [];
  for (const id of core.threadIds) {
    const item = core.getItemById(id);
    if (!item) continue;
    const key = item.remoteId ?? id;
    if (starPending(key)) continue;
    if (item.custom?.starred) pinnedNow.push(key);
    else unpinned.push(key);
  }
  const gone = unpinned.filter((key) => order.pinned.includes(key));
  if (gone.length) forgetPinned(gone);
  const listed = new Set(Object.values(order.groups).flatMap((group: any) => group.keys));
  const left = pinnedNow.filter((key) => listed.has(key));
  if (left.length) forgetInGroups(left, true);
};
const tidyGroups = () => {
  const { kind, projects } = useProjects.getState();
  if (!kind) return;
  const known = new Set(projects.map((project: any) => project.id));
  const gone = Object.keys(useSidebarOrder.getState().groups).filter((group) => group && !known.has(group));
  if (gone.length) forgetGroups(gone);
};
async function open(sessions: any[], layout: any) {
  site.sessions = sessions;
  server = layout;
  bindOrderUser("u@x");
  const { core, threads } = sidebar();
  core.__internal_load();
  await core.getLoadThreadsPromise();
  watchList(() => threads);
  await syncPrefs("u@x");
  tidy(core);
  await g.sleep(SAVE_DELAY + 50);
  assert.deepEqual(saves(), []);
  return core;
}
// 过了一阵（REFETCH_AFTER 以上）：电脑一直开着的窗口拿到焦点（不触发 visibilitychange）/ 回到前台
async function later(event: "focus" | "visibilitychange") {
  skew += REFETCH_AFTER + 1000;
  if (event === "focus") g.fire("focus");
  else g.document.fire("visibilitychange");
  await g.sleep(60);
}
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class PinOrderRaceTests(unittest.TestCase):
    """测试窗口 10-18（测试机实测 + lab 日志：02:28:43 手机最后一次 save_layout 回 506 字节，02:29:39 电脑提交一次回 448 字节）：手机置顶
    几条并排好置顶顺序（服务端 5 条），一直开着的电脑（对话列表是打开时读的）拿到焦点、只重取了布局，侧栏按本页旧的置顶标记把手机
    新置顶的几条当成「取消置顶了」从置顶顺序里去掉、存上去，服务端只剩 1 条。按数据收拾、要存上去的，依据（对话列表、公司列表）
    不能比被改的那份布局旧。真的组件库对话列表 + 会话列表接口 + 侧栏同步 + 偏好同步，站点接口是假的；侧栏的收拾照抄 thread-list.tsx。"""

    STUBS = {"cc-ma-setup": MA_SETUP, "socket.io-client": SYNC_SOCKET}
    IMPORTS = r"""
import { RemoteThreadListThreadListRuntimeCore } from "@assistant-ui/core/react";
import { ThreadListRuntimeImpl } from "@assistant-ui/core/internal";
import { sessionListAdapter } from "@/lib/managed-agents/session-list-adapter";
import { watchList } from "@/lib/managed-agents/list-sync";
import { bindOrderUser, forgetGroups, forgetInGroups, forgetPinned, starChat, starPending, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import { refreshProjects, useProjects } from "@/lib/managed-agents/projects";
import { REFETCH_AFTER, SAVE_DELAY, syncPrefs } from "@/lib/prefs-sync";
"""

    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\nimport assert from "node:assert/strict";\n' + self.IMPORTS
               + "\nconst g = globalThis as any;\n" + SYNC_SITE + PIN_RACE_SITE + body + "\ng.done();\n", stubs=self.STUBS)

    def test_a_stale_chat_list_never_shortens_the_pinned_order_another_device_saved(self):
        self.run_page(r"""
const core = await open([row("s1", "One", 1, { starred: true }), row("s2", "Two", 2), row("s3", "Three", 3), row("s4", "Four", 4),
  row("s5", "Five", 5)], { order: { companies: [], pinned: ["s1"] } });
// 手机上置顶了 s2–s5、排好置顶顺序（服务端 5 条）；电脑的推送漏了，本页的对话列表还是打开时读的
site.sessions = site.sessions.map((s: any) => ({ ...s, starred: true }));
server.order.pinned = ["s3", "s1", "s5", "s2", "s4"];
await later("focus");
assert.equal(g.calls.filter((c: any) => c.method === "listing").length, 1);   // 拿到焦点只重取布局，对话列表不重读
assert.deepEqual(useSidebarOrder.getState().pinned, ["s3", "s1", "s5", "s2", "s4"]);
tidy(core);                                                                   // 本页 s2–s5 还标着没置顶
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(saves(), []);                                                // 不存
assert.deepEqual(server.order.pinned, ["s3", "s1", "s5", "s2", "s4"]);        // 服务端的置顶顺序不被缩短
assert.deepEqual(useSidebarOrder.getState().pinned, ["s3", "s1", "s5", "s2", "s4"]);   // 本机也不改：列表更新后照这份排
// 回到前台：对话列表按服务端核对一次（s2–s4 换成置顶）；这期间手机上又取消了 s5，那一台的收拾没存上
site.sessions = site.sessions.map((s: any) => (s.id === "s5" ? { ...s, starred: false } : s));
await later("visibilitychange");
assert.deepEqual(["s1", "s2", "s3", "s4", "s5"].map((id) => core.getItemById(id).custom.starred), [true, true, true, true, false]);
tidy(core);                                                                   // 依据比布局新：照常收拾、存上去
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(saves(), [{ order: { pinned: ["s3", "s1", "s2", "s4"] } }]);
assert.deepEqual(server.order.pinned, ["s3", "s1", "s2", "s4"]);
""")
        # 照抄的那段与侧栏里的副作用一致
        rows = read("components/assistant-ui/thread-list.tsx")
        effect = rows.split("const unpinned: string[] = [];", 1)[1].split("}, [threadIds", 1)[0]
        for line in ("if (starPending(key)) continue;", "if (item.custom?.starred) pinnedNow.push(key);",
                     "else if (!pinning.has(key)) unpinned.push(key);",
                     "const gone = unpinned.filter((key) => order.pinned.includes(key));", "if (gone.length) forgetPinned(gone);",
                     "const left = pinnedNow.filter((key) => listed.has(key));", "if (left.length) forgetInGroups(left, true);"):
            self.assertIn(line, effect)
        groups = rows.split("// 侧栏里没有这家公司了", 1)[1].split("}, [kind, projects, order.groups]);", 1)[0]
        self.assertIn("if (gone.length) forgetGroups(gone);", groups)

    def test_a_stale_pin_never_takes_a_chat_out_of_the_group_another_device_put_it_back_in(self):
        # 同一副作用的另一半：本页还标着置顶的，「置顶了的从组里手排的先后里去掉」同样不能拿旧标记存
        self.run_page(r"""
const core = await open([row("s1", "One", 1, { starred: true }), row("s7", "Seven", 7), row("s8", "Eight", 8)],
  { order: { companies: [], pinned: ["s1"], "group:": { keys: ["s7", "s8"], at: 100 } } });
// 手机上取消置顶 s1：回到它那一组（最近）的最上面，手机存上；电脑的推送漏了，本页 s1 还标着置顶
site.sessions = site.sessions.map((s: any) => (s.id === "s1" ? { ...s, starred: false } : s));
server.order = { companies: [], pinned: [], "group:": { keys: ["s1", "s7", "s8"], at: 100, top: { n: 1, at: 200 } } };
await later("focus");
assert.deepEqual(useSidebarOrder.getState().groups[""]!.keys, ["s1", "s7", "s8"]);
tidy(core);
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(saves(), []);
assert.deepEqual(server.order["group:"].keys, ["s1", "s7", "s8"]);
assert.deepEqual(useSidebarOrder.getState().groups[""]!.keys, ["s1", "s7", "s8"]);
// 回到前台核对：s1 换成没置顶，留在组顶
await later("visibilitychange");
assert.equal(core.getItemById("s1").custom.starred, false);
tidy(core);
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(saves(), []);
assert.deepEqual(server.order["group:"].keys, ["s1", "s7", "s8"]);
""")

    def test_the_check_on_coming_back_unpins_what_the_first_page_no_longer_pins(self):
        # 第一页带全部置顶的：本页标着置顶、却不在这一页里的（很旧，别的设备上取消、推送漏了），核对后照服务端换成没置顶。原来留着
        # 「置顶」，对话列表核对过、依据算新的，侧栏照样把它从组里去掉、存上去
        self.run_page(r"""
const core = await open([row("s1", "One", 1), row("s2", "Two", 2), row("s9", "Nine", 90, { starred: true })],
  { order: { companies: [], pinned: ["s9"], "group:": { keys: ["s2"], at: 100 } } });
site.sessions = [row("s1", "One", 1), row("s2", "Two", 2)];
site.nextCursor = "2026-10-13 12:57:00|x";
server.order = { companies: [], pinned: [], "group:": { keys: ["s9", "s2"], at: 100, top: { n: 1, at: 200 } } };
await later("visibilitychange");
assert.ok(core.threadIds.includes("s9"));                                     // 比这一页最早的还旧：照旧留着（可能在后面几页）
assert.equal(core.getItemById("s9").custom.starred, false);
tidy(core);
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(saves(), []);
assert.deepEqual(server.order["group:"].keys, ["s9", "s2"]);
""")

    def test_own_unpins_and_pushed_unpins_are_still_tidied_and_saved(self):
        # 本机取消置顶（依据是本机刚得到的服务端确认）、别的设备上取消推过来的：照旧当场收拾；布局很久没取过时先取一次，取回来侧栏
        # 照新的那份再收拾时照样收拾、存上去（对话列表本身是很久以前读的也一样）
        self.run_page(r"""
const core = await open([row("s1", "One", 1, { starred: true }), row("s2", "Two", 2, { starred: true }),
  row("s3", "Three", 3, { starred: true })], { order: { companies: [], pinned: ["s1", "s2", "s3"] } });
skew += REFETCH_AFTER * 3;
const item = (id: string) => ({ getState: () => core.getItemById(id), updateCustom: (custom: any) => core.updateCustom(id, custom) });
await starChat(item("s2"), false);
assert.deepEqual(useSidebarOrder.getState().pinned, ["s1", "s3"]);           // 当场去掉
await g.sleep(SAVE_DELAY + 100);                                              // 先取一次布局（它还在）
assert.deepEqual(useSidebarOrder.getState().pinned, ["s1", "s2", "s3"]);
tidy(core);
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(server.order.pinned, ["s1", "s3"]);
// 别的设备上取消置顶 s3、推过来（那一台的收拾没存上）
skew += REFETCH_AFTER * 3;
push("starred", "s3", { starred: false });
await g.sleep(20);
tidy(core);
assert.deepEqual(useSidebarOrder.getState().pinned, ["s1"]);
await g.sleep(SAVE_DELAY + 100);
tidy(core);
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(server.order.pinned, ["s1"]);
// 本机置顶 s2：确认了从组里手排的先后里去掉，同样先取再存
server.order["group:"] = { keys: ["s2", "s3"], at: 100 };
skew += REFETCH_AFTER * 3;
await syncPrefs("u@x");
skew += REFETCH_AFTER * 3;
await starChat(item("s2"), true);
await g.sleep(SAVE_DELAY + 100);
tidy(core);
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(server.order["group:"].keys, ["s3"]);
""")

    def test_a_stale_company_list_never_drops_a_group_another_device_arranged(self):
        # 公司组整组去掉（侧栏没有这家公司了）同一条：公司列表比布局旧时不收拾；公司列表重取以后照新的收拾、存上去
        self.run_page(r"""
projectsReply = { kind: "company", projects: [{ id: "CO-A", title: "A" }, { id: "CO-Y", title: "Y" }] };
server = { order: { companies: [], pinned: [], "group:CO-A": { keys: ["s1"], at: 1 }, "group:CO-Y": { keys: ["s2"], at: 1 } } };
bindOrderUser("u@x");
await refreshProjects();
await syncPrefs("u@x");
tidyGroups();
await g.sleep(SAVE_DELAY + 50);
assert.deepEqual(saves(), []);
// 手机上在一家新公司 CO-Z 开了第一段对话、排了这一组；电脑的公司列表是打开时读的，还没有 CO-Z
server.order["group:CO-Z"] = { keys: ["s3", "s4"], at: 5 };
await later("focus");
assert.ok("CO-Z" in useSidebarOrder.getState().groups);
tidyGroups();
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(saves(), []);
assert.deepEqual(server.order["group:CO-Z"], { keys: ["s3", "s4"], at: 5 });
assert.ok("CO-Z" in useSidebarOrder.getState().groups);
// 公司列表重取以后（有了 CO-Z，CO-Y 没了）：照新的收拾、存上去
projectsReply = { kind: "company", projects: [{ id: "CO-A", title: "A" }, { id: "CO-Z", title: "Z" }] };
await refreshProjects();
tidyGroups();
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(saves(), [{ order: { "group:CO-Y": null } }]);
assert.equal("group:CO-Y" in server.order, false);
assert.deepEqual(server.order["group:CO-Z"], { keys: ["s3", "s4"], at: 5 });
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
assert.deepEqual(parseOrder('{"companies":["a",1,"a"],"pinned":"x"}'), { companies: ["a"], pinned: [], groups: {} });
assert.deepEqual(parseOrder("not json"), { companies: [], pinned: [], groups: {} });
// 组里手排的先后：形状不对、空的组不要
assert.deepEqual(parseOrder('{"groups":{"comp_A":{"keys":["s1",2,"s1"],"at":5},"":{"keys":["s9"]},"bad":1,"empty":{"keys":[],"at":1}}}').groups,
                 { comp_A: { keys: ["s1"], at: 5 }, "": { keys: ["s9"], at: 0 } });

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
assert.deepEqual(useSidebarOrder.getState(), { companies: [], pinned: [], groups: {} });   // 换成这个人记下的（还没有）
setCompanyOrder(["comp_B", "comp_A"]);
setPinnedOrder(["s4"]);
assert.deepEqual(JSON.parse(storage.get(orderKey("skin3@x"))!), { companies: ["comp_B", "comp_A"], pinned: ["s4"], groups: {} });
setCompanyOrder(["comp_A", "comp_B"]);
assert.deepEqual(useSidebarOrder.getState().companies, ["comp_A", "comp_B"]);
storage.set(orderKey("other@x"), JSON.stringify({ companies: ["comp_C"], pinned: [] }));
bindOrderUser("other@x");
assert.deepEqual(useSidebarOrder.getState().companies, ["comp_C"]);
""")

    def test_unpinned_chats_leave_the_manual_order_and_reset_clears_it(self):
        # 第 5 轮审查：取消置顶不从手排顺序里去掉，再置顶时回到旧位置（不在最上面）；「恢复默认设置」也不清手排顺序
        self.run_page(STORAGE_STUB + r"""
import { bindOrderUser, clearSidebarOrder, forgetPinned, hasManualOrder, noteListRead, orderKey, setCompanyOrder, setGroupOrder, setPinnedOrder, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import { companySections } from "@/lib/managed-agents/sidebar-groups";
import { DEFAULT_VIEW, resetSidebarView, setSidebarView, useSidebarView } from "@/lib/managed-agents/sidebar-view";
bindOrderUser("skin3@x");
const row = (index: number, key: string, starred: boolean) => ({ index, title: key, starred, time: 1000 - index, group: "", key });
// 置顶 A、B、C，把 A 拖到最后：[B, C, A]
setPinnedOrder(["sB", "sC", "sA"]);
const pinnedOf = (rows: any[]) => companySections(rows, [], { pinnedOrder: useSidebarOrder.getState().pinned })!.find((x: any) => x.key === "pinned")!.indices;
// 取消 A 的置顶 → 从记下的顺序里去掉（不认得的键不动；侧栏刚读过对话列表，按它收拾）
noteListRead(performance.now());
forgetPinned(["sA", "nope"]);
assert.deepEqual(useSidebarOrder.getState().pinned, ["sB", "sC"]);
assert.deepEqual(JSON.parse(storage.get(orderKey("skin3@x"))!).pinned, ["sB", "sC"]);
// 再置顶 D 和 A：两条都是新置顶的，排最上面（它们之间照排序方式）
assert.deepEqual(pinnedOf([row(0, "sA", true), row(1, "sB", true), row(2, "sC", true), row(3, "sD", true)]), [0, 3, 1, 2]);
// 恢复默认设置：分组和排序回默认，手排的公司组、置顶与组里的顺序一起清掉，本机记下的也删掉
setCompanyOrder(["comp_B", "comp_A"]);
setGroupOrder("comp_A", ["sA"]);
setSidebarView({ sort: "name" });
assert.equal(hasManualOrder(useSidebarOrder.getState()), true);
resetSidebarView();
assert.deepEqual(useSidebarView.getState(), DEFAULT_VIEW);
assert.deepEqual(useSidebarOrder.getState(), { companies: [], pinned: [], groups: {} });
assert.equal(hasManualOrder(useSidebarOrder.getState()), false);
assert.equal(storage.has(orderKey("skin3@x")), false);
clearSidebarOrder();                                   // 本来就空：不再写
assert.equal(storage.has(orderKey("skin3@x")), false);
""")
        rows = read("components/assistant-ui/thread-list.tsx")
        groups = rows.split("const CompanyGroups: FC", 1)[1].split("\nconst DayGroups", 1)[0]
        # 侧栏按已加载的行收拾：不再置顶的（单条、批量、别的设备）从手排顺序里去掉，正在置顶（拖进已置顶、服务端还没回）的不算；
        # 置顶了的从组里手排的先后里去掉
        self.assertIn("else if (!pinning.has(key)) unpinned.push(key);", groups)
        self.assertIn("if (gone.length) forgetPinned(gone);", groups)
        self.assertIn("if (left.length) forgetInGroups(left, true);", groups)
        # 「恢复默认设置」：分组和排序跟默认一样、也没手排过才不可点
        self.assertIn("disabled={isDefaultView(view) && !manual} onSelect={resetSidebarView}", read("components/assistant-ui/sidebar-group.tsx"))

    def test_personal_layout_follows_the_user_across_devices(self):
        # 测试窗口第 7 轮第 2 项：侧栏手排顺序、分组和排序、会话栏卡片顺序与开合只记在本机。改成按人存到服务端（layout / save_layout），
        # 打开时取、改了就存、后写的为准；本机缓存照样先顶着、离线照样能用；折叠仍按设备；服务端没有时把本机原有的迁上去。
        # 第 7 轮审查：只存改了的项（服务端按项合并），本机改了还没存上的项不被服务端的盖掉，别的项照样换成服务端的
        self.run_page(STORAGE_STUB + r"""
import { bindOrderUser, forgetInGroups, forgetPinned, noteListRead, orderKey, setCompanyOrder, setGroupOrder, setPinnedOrder, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import { bindSidebarViewUser, DEFAULT_VIEW, setSidebarView, useSidebarView } from "@/lib/managed-agents/sidebar-view";
import { toggleFold } from "@/lib/managed-agents/sidebar-fold";
import { loadPanelPrefs, panelKey, setCardOrder, setPanelOpen, toggleCard, usePanel } from "@/lib/company-panel";
import { collectPrefs, REFETCH_AFTER, SAVE_DELAY, syncPrefs } from "@/lib/prefs-sync";
const g = globalThis as any;
let server: any = null;
let offline = false;
// 服务端按项合并（pearnly_core.assistant.prefs.save）：交 null 的项删掉
const merge = (old: any, sent: any) => {
  const out: any = { ...(old ?? {}) };
  for (const [part, fields] of Object.entries(sent)) {
    out[part] = { ...(out[part] ?? {}) };
    for (const [field, value] of Object.entries(fields as any)) {
      if (value === null) delete out[part][field];
      else out[part][field] = value;
    }
  }
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
storage.set(orderKey("skin3@x"), JSON.stringify({ companies: ["comp_B", "comp_A"], pinned: ["s4"] }));   // 旧版本记的没有组里的
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
// 组里手排的先后：每一组各是一项（group:<公司编号>，最近 group:），只交改了的那一组（审查：各组合成一项时，一直开着、没重取过的
// 设备改一组就把整份交上去，冲掉别的设备在其他组排好的）
setGroupOrder("comp_C", ["s7", "s5"], 1234);
await g.sleep(SAVE_DELAY + 50);
assert.deepEqual(sent().at(-1), { order: { "group:comp_C": { keys: ["s7", "s5"], at: 1234 } } });
assert.deepEqual(server.order["group:comp_C"], { keys: ["s7", "s5"], at: 1234 });
assert.deepEqual(server.order.pinned, ["s9"]);
// 另一台设备这期间排了 comp_D；这台没重取过，接着改 comp_C：只交 comp_C，comp_D 照旧在
server.order["group:comp_D"] = { keys: ["s8"], at: 50 };
setGroupOrder("comp_C", ["s5", "s7"], 1300);
await g.sleep(SAVE_DELAY + 50);
assert.deepEqual(sent().at(-1), { order: { "group:comp_C": { keys: ["s5", "s7"], at: 1300 } } });
assert.deepEqual(server.order["group:comp_D"], { keys: ["s8"], at: 50 });
// 去掉一组（这组的对话都置顶了、删了）：交 null，服务端删掉这一项
setGroupOrder("comp_E", ["s6"], 1);
await g.sleep(SAVE_DELAY + 50);
forgetInGroups(["s6"]);
await g.sleep(SAVE_DELAY + 50);
assert.deepEqual(sent().at(-1), { order: { "group:comp_E": null } });
assert.equal("group:comp_E" in server.order, false);
// 组里的各项以服务端为准：别的设备清掉了 comp_C、comp_D（服务端没有了）、排了最近，这台取到后照服务端的（本机没改的组去掉）
delete server.order["group:comp_C"];
delete server.order["group:comp_D"];
server.order["group:"] = { keys: ["s3"], at: 99 };
await syncPrefs("skin3@x");
assert.deepEqual(useSidebarOrder.getState().groups, { "": { keys: ["s3"], at: 99 } });
assert.equal(useSidebarView.getState().sort, "created");
assert.deepEqual(useSidebarOrder.getState().pinned, ["s9"]);
assert.equal(storage.has("cc.prefs.unsaved:skin3@x"), false);

// 离线时排了一组、这期间别的设备清掉了全部组里的手排：联网后只补存这台改的那一组，别的设备清掉的不复活（审查：原来整份盖回去）
offline = true;
setGroupOrder("comp_Y", ["s2"], 7);
await g.sleep(SAVE_DELAY + 50);
offline = false;
delete server.order["group:"];
g.calls.length = 0;
await syncPrefs("skin3@x");
assert.deepEqual(sent(), [{ order: { "group:comp_Y": { keys: ["s2"], at: 7 } } }]);
assert.deepEqual(useSidebarOrder.getState().groups, { comp_Y: { keys: ["s2"], at: 7 } });
assert.equal("group:" in server.order, false);
server.order = { ...server.order, "group:": { keys: ["s3"], at: 99 } };
delete server.order["group:comp_Y"];
await syncPrefs("skin3@x");

// 取的时候本机又改了：改的那项本机的为准，存上去；没改的项照样换成服务端的
server = { order: { companies: ["old"], pinned: [], "group:": { keys: ["s3"], at: 99 } } };
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
server = { order: { companies: ["comp_Z"], pinned: ["s2", "s1"], "group:": { keys: ["s3"], at: 99 } } };
g.calls.length = 0;
const tidy = syncPrefs("skin3@x");
forgetPinned(["s1"]);
await tidy;
assert.deepEqual(useSidebarOrder.getState(), { companies: ["comp_Z"], pinned: ["s2", "s1"], groups: { "": { keys: ["s3"], at: 99 } } });
assert.deepEqual(g.calls.map((c: any) => c.method), ["layout"]);

// 第 7 轮审查第 1 条：一直开着、很久没取过的设备上，别的设备取消置顶推过来（按数据收拾）。不拿本机过时的公司组顺序盖服务端：
// 先取最新的、换进来，侧栏照新的那份再收拾一遍，这时才存，只存置顶这一项
const realNow = performance.now.bind(performance);
let skew = 0;
(performance as any).now = () => realNow() + skew;
skew += REFETCH_AFTER * 3;
server = { ...server, order: { companies: ["comp_new"], pinned: ["s2", "s1"], "group:": { keys: ["s3"], at: 99 } } };
g.calls.length = 0;
noteListRead(performance.now(), ["s1"]);                                          // 推来的（list-sync 记下这一行此刻的置顶标记）
forgetPinned(["s1"]);
assert.deepEqual(useSidebarOrder.getState().pinned, ["s2"]);                       // 本机马上去掉
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(g.calls.map((c: any) => c.method), ["layout"]);                  // 先取，不存
assert.deepEqual(useSidebarOrder.getState(), { companies: ["comp_new"], pinned: ["s2", "s1"], groups: { "": { keys: ["s3"], at: 99 } } });
forgetPinned(["s1"]);                                                             // 侧栏照新的那份再收拾（刚取过）
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(sent(), [{ order: { pinned: ["s2"] } }]);
assert.deepEqual(server.order, { companies: ["comp_new"], pinned: ["s2"], "group:": { keys: ["s3"], at: 99 } });
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
import { bindOrderUser, forgetPinned, noteListRead, orderKey, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
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
noteListRead(performance.now());
forgetPinned(["sX"]);                                  // 侧栏列表先回来（资料还没到）：只改本机
assert.deepEqual(useSidebarOrder.getState().pinned, ["s1"]);
await g.sleep(SAVE_DELAY + 50);
const first = syncPrefs("skin3@x");
assert.equal(syncPrefs("skin3@x"), first);             // 同时叫两次只取一次
await first;
assert.deepEqual(methods(), ["layout"]);               // 不存本机旧的
assert.deepEqual(useSidebarOrder.getState(), { companies: ["comp_A2"], pinned: ["s1"], groups: {} });
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
        # 测试窗口第 7 轮第 1 项：真的放下了却念「已取消移动」。放下按实际结果念：对话拖进已置顶（此刻排在置顶那一列里、经过的是
        # 它自己）念已置顶；排序的念放到第几位；落回原位、落在原来那一组的整块上念取消（用户 10-17：对话只在本组排、或拖进置顶）
        self.run_page(r"""
import { dragAnnouncements } from "@/components/ui/drag";
const texts = { instructions: "", pickedUp: (n: string) => `up ${n}`, movedTo: (n: string, a: number, t: number) => `moved ${n} ${a}/${t}`,
  droppedAt: (n: string, a: number, t: number) => `dropped ${n} ${a}/${t}`,
  cancelled: (n: string) => `cancel ${n}`, over: (n: string, g: string) => `over ${n} ${g}`, droppedIn: (n: string, g: string) => `in ${n} ${g}` };
const row = { id: "row:t1", kind: "row", label: "lelo", list: "rows:A", group: "A" };
// 排序项：data 里有 sortable（containerId 是此刻所在的那一列）
const entry = (d: any, container?: string, index = 0, items: string[] = []) =>
  ({ id: d.id, data: { current: { drag: d, ...(container ? { sortable: { containerId: container, index, items } } : {}) } } });
const pin = { id: "pin:p1", kind: "pin", label: "p1", list: "pins" };
const zone = (list: string, label: string, shut = false) => ({ id: `zone:${list}`, kind: "group", label, list, zone: true, shut });
const say = dragAnnouncements(texts, { lifted: () => row, zoneLabel: (list: string) => (list === "pins" ? "Pinned" : undefined) });
say.onDragStart!({ active: entry(row, "rows:A") } as any);
assert.equal(say.onDragOver!({ active: entry(row, "rows:A"), over: entry(row, "rows:A", 0, ["row:t1", "row:t2"]) } as any), undefined);  // 拿起时那一次经过自己
assert.equal(say.onDragOver!({ active: entry(row, "rows:A"), over: entry(pin, "pins", 0, ["pin:p1"]) } as any), "over lelo Pinned");
// 已排进置顶、停在自己的位置上放下：念已置顶
assert.equal(say.onDragEnd!({ active: entry(row, "pins"), over: entry(row, "pins", 1, ["pin:p1", "row:t1"]) } as any), "in lelo Pinned");
// 已置顶收着：指着整块（组头高亮），放下同样念已置顶
assert.equal(say.onDragEnd!({ active: entry(row, "rows:A"), over: entry(zone("pins", "Pinned", true)) } as any), "in lelo Pinned");
// 在本组排：念位置；落回原位、落在本组的整块上：取消
say.onDragStart!({ active: entry(row, "rows:A") } as any);
assert.equal(say.onDragOver!({ active: entry(row, "rows:A"), over: entry({ ...row, id: "row:t2" }, "rows:A", 1, ["row:t1", "row:t2"]) } as any), "moved lelo 2/2");
assert.equal(say.onDragEnd!({ active: entry(row, "rows:A"), over: entry({ ...row, id: "row:t2" }, "rows:A", 1, ["row:t1", "row:t2"]) } as any), "dropped lelo 2/2");
assert.equal(say.onDragEnd!({ active: entry(row, "rows:A"), over: entry(row, "rows:A", 0, ["row:t1", "row:t2"]) } as any), "cancel lelo");
assert.equal(say.onDragEnd!({ active: entry(row, "rows:A"), over: entry(zone("rows:A", "A Co")) } as any), "cancel lelo");
assert.equal(say.onDragEnd!({ active: entry(row, "rows:A"), over: null } as any), "cancel lelo");
""")
        drag = read("components/ui/drag.tsx")
        end = drag.split("  private dropped() {", 1)[1].split("\n  }\n", 1)[0]
        # 停在自己的位置上：还在原来那一列 = 没动；已排进另一列 = 交那一列的整块（调用方按排好的位置算）
        self.assertIn("const zone = into && into !== from.list ? this.zoneOf(into) : undefined;", end)
        self.assertIn("if (zone) this.props.onDrop(from, zone);", end)
        # 念的是放下那一刻的落点（结束拖动前取好）
        self.assertLess(end.index("this.say.onDragEnd("), end.index("this.finish(d, () => {"))
        self.assertIn("if (end && (over.id === active.id || to?.zone)) return texts.cancelled(name(active));", drag)
        for gone in ("dropped.current", "settleMove", "placedRef", "class Landing", "isMover", "placeOf("):
            self.assertNotIn(gone, drag)

    def test_announcements_name_the_lifted_item_even_after_it_is_gone(self):
        # 测试窗口第 12 轮 D15：键盘拿起公司组头后用鼠标换分组方式（按月份），被拖那一组卸下、此刻查不到它的说明，之后念「已取消
        # 移动「」。」「已置顶「」。」——名字是空的、放下还念成置顶。名字一律用拿起时记下的；落点那一项也没了按没有落点念（取消）
        self.run_page(r"""
import { dragAnnouncements } from "@/components/ui/drag";
const texts = { instructions: "", pickedUp: (n: string) => `up ${n}`, movedTo: (n: string, a: number, t: number) => `moved ${n} ${a}/${t}`,
  droppedAt: (n: string, a: number, t: number) => `dropped ${n} ${a}/${t}`,
  cancelled: (n: string) => `cancel ${n}`, over: (n: string, g: string) => `over ${n} ${g}`, droppedIn: (n: string, g: string) => `in ${n} ${g}` };
const lifted = { id: "company:1010", kind: "company", label: "1010 Co", list: "companies", group: "1010" };
const say = dragAnnouncements(texts, { lifted: () => lifted });
// 此刻页面上没有这一项了（卸下、注销）：说明查不到
const gone = (id: string) => ({ id, data: { current: { drag: undefined } } });
assert.equal(say.onDragStart!({ active: gone("company:1010") } as any), "up 1010 Co");
assert.equal(say.onDragEnd!({ active: gone("company:1010"), over: gone("company:1010") } as any), "cancel 1010 Co");
assert.equal(say.onDragCancel!({ active: gone("company:1010") } as any), "cancel 1010 Co");
// 瞄着的那一组也没了：拖动中不念（留着上一句），放下按没有落点念取消，不念「已置顶」
assert.equal(say.onDragOver!({ active: gone("company:1010"), over: gone("company:2020") } as any), undefined);
assert.equal(say.onDragEnd!({ active: gone("company:1010"), over: gone("company:2020") } as any), "cancel 1010 Co");
// 拖动中改了名（推送）：念的照样是拿起时的名字
const items = ["company:1010", "company:2020"];
const renamed = { id: "company:1010", data: { current: { drag: { ...lifted, label: "1010 New" }, sortable: { containerId: "companies", index: 0, items } } } };
const other = { id: "company:2020", data: { current: { drag: { id: "company:2020", kind: "company", label: "2020 Co", list: "companies" }, sortable: { containerId: "companies", index: 1, items } } } };
say.onDragStart!({ active: renamed } as any);
assert.equal(say.onDragOver!({ active: renamed, over: other } as any), "moved 1010 Co 2/2");
assert.equal(say.onDragEnd!({ active: renamed, over: other } as any), "dropped 1010 Co 2/2");
""")

    def test_the_float_turns_see_through_over_a_folded_group_head(self):
        # 第 7 轮第 3 项：手机上浮层停在收着的组头上，盖住了组头的高亮。停在只能高亮组头的落点上时浮层半透明，离开就恢复（共用拖动）
        drag = read("components/ui/drag.tsx")
        # 收着的整块被指着（组头带 data-drop-target）那一次提交里浮层带 data-peek，离开、结束时去掉（自写引擎：浮层是自己建的节点）
        zone = drag.split("export function useDropZone(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("if (!over || !shut) return;\n    engine.peek(true);\n    return () => engine.peek(false);", zone)
        self.assertIn('return { ref, "data-drop-target": over && shut ? ("" as const) : undefined };', zone)
        peek = drag.split("  peek(on: boolean) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn('if (on) float.dataset.peek = "";\n    else delete float.dataset.peek;', peek)
        css = read("globals.css")
        self.assertIn("--cc-drag-peek: 0.15;", css)   # 第 8 轮：再淡、字隐去（Round8Tests）
        self.assertIn(".cc-drag-overlay:not([data-dropping]) .cc-drag-float[data-peek] .cc-drag-lift { opacity: var(--cc-drag-peek); }", css)
        # 过渡沿用浮层已有的 opacity 过渡（动效变量）；落到组头时的淡出写在它后面，照样盖过它
        lift = css.split("\n.cc-drag-lift {", 1)[1].split("}", 1)[0]
        self.assertIn("opacity var(--cc-dur) var(--cc-ease)", lift)
        self.assertLess(css.index(".cc-drag-overlay:not([data-dropping]) .cc-drag-float[data-peek] .cc-drag-lift"), css.index('.cc-drag-overlay[data-dropping="into"] .cc-drag-lift'))

    def test_group_and_sort_choices_say_which_one_is_picked(self):
        # 第 7 轮第 5 项：子菜单选项原是普通 menuitem，读屏听不出当前选的是哪个：单选用 menuitemradio、勾选用 menuitemcheckbox，带
        # aria-checked（组件库 RadioItem / CheckboxItem），✓ 的样子不变；「显示」里的两项点了菜单不关
        group = read("components/assistant-ui/sidebar-group.tsx")
        menu = group.split("export const GroupSortMenu", 1)[1]
        self.assertEqual(menu.count("<DropdownMenu.RadioGroup"), 3)
        self.assertIn("<DropdownMenu.RadioItem className=\"cc-menu-item\" value={value} onSelect={onSelect}>", group)
        # 用户 10-17：排序方式里点任一项（含已选的那一项，RadioGroup 的 onValueChange 这时不调）= 组里手排的先后都不要了
        sort = menu.split("label={sb.sortBy}", 1)[1].split("</SubMenu>", 1)[0]
        self.assertEqual(sort.count("onSelect={clearGroupOrders}"), 3)
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
        # 用户 10-17：对话行只能落在自己那一组（行；整块只是回到这一组）或已置顶（置顶的行、整块），不能落到别的公司组、别的组头；
        # 多选而选中的不全在这一组时本组的行不是落点（只能置顶）
        self.run_page(r"""
import { pinDrop, pinSpot, placeBlock, sidebarAccepts } from "@/lib/managed-agents/sidebar-drag";
const row = (group: string, id = "row:t1") => ({ id, kind: "row", label: "Chat", list: `rows:${group}`, group });
const company = (id: string) => ({ id: `company:${id}`, kind: "company", label: id, list: "companies", group: id });
const zone = (list: string) => ({ id: `zone:${list}`, kind: "group", label: list, list, zone: true });
const pin = (id: string) => ({ id: `pin:${id}`, kind: "pin", label: id, list: "pins" });
assert.equal(sidebarAccepts(row("comp_A"), row("comp_A", "row:t2")), true);
assert.equal(sidebarAccepts(row("comp_A"), row("comp_B", "row:t2")), false);    // 别的公司的行
assert.equal(sidebarAccepts(row("comp_A"), row("", "row:t2")), false);          // 「最近」的行
assert.equal(sidebarAccepts(row(""), row("", "row:t2")), true);                  // 本来就在最近：在最近里排
assert.equal(sidebarAccepts(row("comp_A"), company("comp_B")), false);           // 公司组头不是落点
assert.equal(sidebarAccepts(row("comp_A"), company("comp_A")), false);
assert.equal(sidebarAccepts(row("comp_A"), zone("rows:comp_B")), false);
assert.equal(sidebarAccepts(row("comp_A"), zone("rows:")), false);
assert.equal(sidebarAccepts(row("comp_A"), zone("rows:comp_A")), true);         // 回到自己那一组
assert.equal(sidebarAccepts(row("comp_A"), pin("t2")), true);                   // 拖进已置顶
assert.equal(sidebarAccepts(row("comp_A"), zone("pins")), true);
// 多选、选中的不全在这一组：本组的行不是落点，只能置顶（整块照样能回来）
assert.equal(sidebarAccepts(row("comp_A"), row("comp_A", "row:t2"), false), false);
assert.equal(sidebarAccepts(row("comp_A"), pin("t2"), false), true);
assert.equal(sidebarAccepts(row("comp_A"), zone("rows:comp_A"), false), true);
// 置顶、公司组只在自己那一列里调序（不认整块）
assert.equal(sidebarAccepts(pin("t1"), pin("t2")), true);
assert.equal(sidebarAccepts(pin("t1"), zone("pins")), false);
assert.equal(sidebarAccepts(pin("t1"), row("comp_A")), false);
assert.equal(sidebarAccepts(pin("t1"), company("comp_A")), false);
assert.equal(sidebarAccepts(company("comp_A"), company("comp_B")), true);
assert.equal(sidebarAccepts(company("comp_A"), zone("rows:comp_B")), false);
assert.equal(sidebarAccepts(company("comp_A"), pin("t1")), false);

// 拖进已置顶还没放下时排第几位：指在置顶某一行上 = 它的位置（下半截 = 它后面）；指在整块上 = 上半截最前、下半截最后
const pins = ["pin:a", "pin:b", "pin:c"];
assert.equal(pinSpot(pins, pin("b"), false), 1);
assert.equal(pinSpot(pins, pin("b"), true), 2);
assert.equal(pinSpot(pins, zone("pins"), false), 0);
assert.equal(pinSpot(pins, zone("pins"), true), 3);
assert.equal(pinSpot([], zone("pins"), true), 0);
// 放下时置顶的新先后：已排进来的留在排好的位置，落在置顶的另一行上挪到那里；没排进来（已置顶收着）放最上面
assert.deepEqual(pinDrop(["pin:a", "row:t1", "pin:b"], "row:t1", zone("pins")), ["pin:a", "row:t1", "pin:b"]);
assert.deepEqual(pinDrop(["pin:a", "row:t1", "pin:b"], "row:t1", { ...row("comp_A"), id: "row:t1" }), ["pin:a", "row:t1", "pin:b"]);
assert.deepEqual(pinDrop(["pin:a", "row:t1", "pin:b"], "row:t1", pin("b")), ["pin:a", "pin:b", "row:t1"]);
assert.deepEqual(pinDrop(["pin:a", "pin:b"], "row:t1", { ...zone("pins"), shut: true }), ["row:t1", "pin:a", "pin:b"]);
// 多选整批作为一块排到拖的那一条放下的位置（按侧栏里的先后），其余各条从原处拿掉
assert.deepEqual(placeBlock(["a", "x", "b", "y", "c"], "x", ["x", "y"]), ["a", "x", "y", "b", "c"]);
assert.deepEqual(placeBlock(["a", "b", "x", "c"], "x", ["y", "x", "z"]), ["a", "b", "y", "x", "z", "c"]);   // 原来不在这一列的（置顶时组里的）也排进来
assert.deepEqual(placeBlock(["a", "x"], "x", ["x"]), ["a", "x"]);
assert.deepEqual(placeBlock(["a", "b"], "x", ["x", "y"]), ["a", "b"]);
""")

    def test_month_and_branch_change_keeps_the_company(self):
        # 用户 10-17：对话不能换公司。下巴、手机「…」里改月份、分店照常 move，请求带它现在的公司（服务端核对，不同即拒绝）；
        # 没挂公司的不发请求；服务端拒绝时退回原来的、侧栏出错行写原文
        self.run_page(r"""
const g = globalThis as any;
import { fileSession, filingOf, noteFilings, useProjects } from "@/lib/managed-agents/projects";
const ok = (body: any) => ({ ok: true, status: 200, json: async () => ({ message: body }) });
const refused = JSON.stringify([JSON.stringify({ message: "A conversation cannot be moved to another company." })]);
g.respond = async (method: string, body: any) => {
  if (method !== "move") return ok(null);
  if (body.session_id === "sesn_bad") return { ok: false, status: 417, json: async () => ({ _server_messages: refused }) };
  return ok({ id: body.session_id, project: body.project, period: body.period, scope: body.scope });
};
useProjects.setState({ kind: "company", period: true, scope: true } as any);
const moves = () => g.calls.filter((c: any) => c.method === "move").map((c: any) => c.body);
noteFilings({ sesn_a: { project: "comp_A", period: "2026-09", scope: "" }, sesn_r: { project: "", period: "", scope: "" },
              sesn_bad: { project: "comp_B", period: "", scope: "" } });
await fileSession("sesn_a", { period: "2026-08" });
assert.deepEqual(moves().at(-1), { session_id: "sesn_a", project: "comp_A", period: "2026-08", scope: "" });
await fileSession("sesn_a", { scope: "00012" });
assert.deepEqual(filingOf("sesn_a"), { project: "comp_A", period: "2026-08", scope: "00012" });
const before = moves().length;
await fileSession("sesn_r", { period: "2026-08" });                 // 没挂公司：没有月份、分店，不发请求
await fileSession("sesn_a", { period: "2026-08" });                 // 没变：不发
assert.equal(moves().length, before);
await assert.rejects(fileSession("sesn_bad", { period: "2026-07" }));
assert.deepEqual(filingOf("sesn_bad"), { project: "comp_B", period: "", scope: "" });
""")
        projects = read("lib/managed-agents/projects.ts")
        self.assertIn('export async function fileSession(sessionId: string, change: Partial<Pick<Filing, "period" | "scope">>): Promise<void> {', projects)
        self.assertNotIn("export function notIn(", projects)
        self.assertNotIn("showOneMore", projects)

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
        overlay = drag.split("function liftOverlay(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('overlay.className = "cc-drag-overlay";', overlay)
        # 层级在 globals.css 的 --cc-z-drag，拿起时读（标准化记录 2.6）；OVERLAY_Z 是读不到时的回退值，与 globals.css 同值
        self.assertIn("style.zIndex = String(overlayZ());", overlay)
        self.assertIn('export const overlayZ = () => fixedPx("--cc-z-drag", OVERLAY_Z);', drag)
        css = read("globals.css")
        self.assertRegex(css, r"(?m)^\s*--cc-z-drag: 55;")
        # 层级 B1 起写成刻度层的变量（--cc-z-*），按变量取值
        level = lambda m: m and re.search(r"(?m)^\s*" + re.escape(m.group(1)) + r":\s*(\d+);", css)
        menu = level(re.search(r"\.cc-menu \{\s*z-index: var\((--cc-z-[\w-]+)\);", css))
        phone = level(re.search(r'\.cc-panel\[data-mode="phone"\] \{\s*position: fixed; inset: 0; z-index: var\((--cc-z-[\w-]+)\);', css))
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
        self.assertIn("touch: { ...TOUCH_START, beforeEnd, beforeCancel },", drag)
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
        self.assertIn("const { open: menuOpen, setOpen: setMenuOpen, modal: menuModal, press } = useLongPressMenu();", read("components/assistant-ui/thread-list.tsx"))
        self.assertIn("const { open: menuOpen, setOpen: setMenuOpen, modal: menuModal, press } = useLongPressMenu();", read("components/assistant-ui/projects.tsx"))
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
        # 一个拖动区（手机按住 250ms 拿起，同会话栏卡片；长按菜单另见 use-long-press）；置顶一列、公司组一列、每组的行各一列；
        # 已置顶、各组整块是落点；对话拖进已置顶时整列一起滑。用户 10-17：没有跨公司、移到别的公司的逻辑
        self.assertIn("<DragArea accepts={accepts} onDrop={onDrop} onOver={onOver} onActive={onActive} onReturn={onReturn} onPointer={onPointer} flow={FLOW} texts={texts}>", groups)
        self.assertIn("const accepts = (active: DragData, target: DragData) => sidebarAccepts(active, target, wholeBatch.current);", groups)
        self.assertNotIn('touch="move"', rows)
        self.assertIn("<DragList id={PINS} items={pinIds}>", groups)
        self.assertIn('<DragList id="companies" items={companyIds}>', groups)
        self.assertIn("<DragList id={rowList(section.project.id)} items={rowIdsOf(section.project.id)}>", groups)
        self.assertIn('<DragList id={rowList("")} items={rowIdsOf("")}>', groups)
        self.assertIn('const zone = useDropZone({ id: zoneDrag(PINS), kind: "pins", label: t("starred"), list: PINS }, !open);', groups)
        self.assertIn('const zone = useDropZone({ id: zoneDrag(rowList("")), kind: "group", label: t("recents"), list: rowList("") }, !open);', groups)
        self.assertIn('const zone = useDropZone({ id: zoneDrag(rowList(project.id)), kind: "group", label: project.title, list: rowList(project.id) }, !open);', groups)
        for gone in ("moveDragged", "fileSession", "keepInView", "showOneMore", "liftRow", "withRecents", "matchesAfterMove", "leavesResults",
                     "shutOutside", "setKept", "onPlace", "RECENTS_TARGET", "useDropTarget", "useDragItem", "MoveToMenu", "BulkMoveMenu"):
            self.assertNotIn(gone, rows)
        # 公司组整块拖（组头开关是把手），搜索时不能调序；Alt+↑/↓ 挪一格
        self.assertIn('{ id: companyDrag(project.id), kind: "company", label: project.title, list: "companies", group: project.id },', groups)
        self.assertIn("{ disabled: locked, ownFocus: true },", groups)
        # Alt+↑/↓ 挪完念新位置（第 5 轮第 2 项：同键盘拖动的播报）
        self.assertIn("handle={drag.handle} onMove={locked ? undefined : move}", groups)
        self.assertIn("const move = useCallback((delta: -1 | 1) => said(latestMove.current(delta)), [said]);", groups)
        self.assertIn("onMove={(delta) => spotIn(saveCompanies(moveBy(companyIds, companyDrag(section.project.id), delta)), companyDrag(section.project.id))}", groups)
        # 拖对话、没有「已置顶」时，指针拖到侧栏顶上才临时出一个空的（开着、点了不收）：拿起那一刻不出（审查：插在最上面把整列往下推，
        # 拿起的那一行离开指针），键盘拖动不出（DragArea 只在指针拖动时交 onPointer）；指针往下走远了（没排进去时）再收起。
        # 拖进去还没放下的那一行排在置顶里；多选里拖一条，其余选中的也变淡
        self.assertIn("return reveal ? withPinned(sections) : sections; // 只在拖对话时出（onPointer）", groups)
        self.assertIn('if (active.kind !== "row" || !byCompany || !tempPins) return;', groups)   # reveal 只由拖对话打开
        # 拿起、放下不重画整列（第 10 轮 B 段）：拖的是什么不记成状态
        self.assertNotIn("setLifting", groups)
        point = groups.split("const onPointer = (", 1)[1].split("\n  };\n", 1)[0]
        self.assertIn('if (active.kind !== "row" || !byCompany || !tempPins) return;', point)
        self.assertIn("const top = first.current?.nextElementSibling;", point)
        # 按版面位置量（组这一块不滑，组头按版面高度）：滑动中的位置会让刚收起的组头被当成指针还在它上半截，来回出、收
        self.assertIn("const head = (top.firstElementChild as HTMLElement | null)?.offsetHeight ?? box.height;", point)
        self.assertIn("if (y < box.top + head / 2) setReveal(true);", point)
        self.assertIn("} else if (!guestRef.current && y > box.bottom + box.height) setReveal(false);", point)
        self.assertIn("<span ref={first} hidden />", groups)
        self.assertIn("setReveal(false);", groups.split("const onActive = (", 1)[1].split("\n  };\n", 1)[0])
        self.assertIn("if (guest) return pinGuest(sections, guest.index, guest.at);", groups)
        self.assertIn("<PinnedGroup open={pinsOpen} onToggle={tempPins ? () => undefined : () => toggleSection({ key: \"pinned\" })}>", groups)
        self.assertIn('document.documentElement.dataset.ccDragBulk = "";', groups)
        # 指针进了已置顶（指着置顶的某一行或整块）就排进去，回到自己那一组就挪回去；收着的已置顶只高亮组头
        over = groups.split("const onOver = (", 1)[1].split("\n  };\n", 1)[0]
        self.assertIn("if (now || over.shut) return;", over)
        self.assertIn("placeGuest({ local, index, at: pinSpot(pinIds, over, below), group: active.group });", over)
        self.assertIn("} else if (now && over.list === active.list) placeGuest(null);", over)
        # 没有落点（别的公司组、两组之间）：拖进置顶的那一行挪回去，松手不置顶（审查：原来留在置顶，在别的公司上松手也置顶了）
        self.assertIn("if (!over) {\n      if (now) placeGuest(null);\n      return;\n    }", over)
        # 放下：落在已置顶 = 置顶（整批按侧栏里的先后排进落点，先存顺序、从组里去掉，再逐条置顶）；落在本组的行 = 组里排先后
        drop = groups.split("const onDrop = (", 1)[1].split("\n  };\n", 1)[0]
        self.assertIn("if (target.list === PINS) pinDropped(active, target);", drop)
        self.assertIn('else if (target.kind === "row" && target.list === active.list) groupDropped(active, target);', drop)
        pin = groups.split("const pinDropped = (", 1)[1].split("\n  };\n", 1)[0]
        self.assertIn("const keys = placeBlock(pinDrop(pinIds, active.id, target).map(keyOfDrag), anchor, block);", pin)
        self.assertLess(pin.index("setPinnedOrder(keys);"), pin.index("void runInOrder("))
        self.assertIn("forgetInGroups(block);", pin)
        self.assertIn("if (!custom?.starred) await item.updateCustom({ ...custom, starred: true });", pin)
        group = groups.split("const groupDropped = (", 1)[1].split("\n  };\n", 1)[0]
        # at 取这组此刻各行里最新的时间（不用本机时钟）；拖的这一条与整块记进去，落点不因条数上限丢
        self.assertIn("setGroupOrder(group, placeBlock(next.map(keyOfDrag), keyOfDrag(active.id), block), stampOf(group), [keyOfDrag(active.id), ...block]);", group)
        self.assertIn("return groupStamp(indices.flatMap((index) => rowAt.get(index) ?? ([] as SidebarRow[])), { by: view.sort, desc: view.desc });", groups)
        self.assertIn("return spotIn(save(group, moveBy(idsOf(group), id, delta), [id]), id);", groups)
        # 侧栏里没有这家公司了：这一组手排的先后整组去掉（公司列表读到了才算，最近不算）
        self.assertIn('const gone = Object.keys(order.groups).filter((group) => group && !known.has(group));', groups)
        self.assertIn("if (gone.length) forgetGroups(gone);", groups)
        # 选中的不全在拖的这一条那一组：本组不是落点
        self.assertIn("wholeBatch.current = !bulk || locals.every((id) => groupOfLocal.get(id) === groupOfLocal.get(fromDrag(active!.id)));", groups)
        # 行：置顶可排、公司里 / 最近的在本组排或拖进置顶（没存到服务端的、搜索时不能拖）；从「···」、输入框按下不拿起
        self.assertIn("const ROW_SKIP = '[data-slot=\"aui_thread-list-item-more\"], [role=\"menu\"], input';", rows)
        self.assertIn('useSortableItem({ id, kind: "pin", label: title, list: PINS }, { disabled: scope.locked, ownFocus: true, skip: ROW_SKIP });', rows)
        self.assertIn("const locked = scope.locked || !remoteId;", rows)
        self.assertIn("{ id, kind: \"row\", label: count ? sb.drag.items(count) : title, list: rowList(scope.group), group: scope.group, count: count || undefined, session: remoteId },", rows)
        self.assertIn("if (scope?.mode === \"pin\") return scope.guest?.local === localId ? <GroupRow scope={scope.guest.scope} /> : <PinnedRow scope={scope} />;", rows)
        row = rows.split("\nconst Row = memo(function Row(", 1)[1].split("\nconst RowMeta: FC", 1)[0]
        self.assertIn("if (onMove && altMove(e, onMove)) return;", row)
        self.assertIn("onTouchStart={chain(press.onTouchStart, renaming ? undefined : dragTouch)}", row)
        self.assertIn("ref={handleRef}", row)
        # 长按菜单：拿起后手指动了（真的在拖）就不出、已经出来的收起——共用拖动收到那一下就当场告诉长按（第 13 轮测试机起，不在到点
        # 时回头问；到点后等下一帧开头再出，见 Round13LongPressRaceTests）；抽屉横滑不跟拖动抢
        hook = read("hooks/use-long-press.ts")
        self.assertIn("watchDragMoved(owner, () => {", hook)
        self.assertIn("frame.current = nextFrame(open);", hook)
        self.assertNotIn("isDragMoving", hook)
        self.assertIn("  moved = true;\n  watcher?.run();", read("lib/layers.ts"))
        self.assertIn("if (isDragging() && !g.locked) {", read("components/mobile-shell.tsx"))
        # 组头：开关是把手，菜单键优先，Alt+↑/↓ 其次，其余交给拖动
        header = read("components/assistant-ui/sidebar-group.tsx").split("export const GroupHeader: FC", 1)[1].split("\n);\n", 1)[0]
        self.assertIn("{...handle}", header)
        self.assertIn("if (onMove && altMove(e, onMove)) return;\n        handle?.onKeyDown?.(e);", header)
        # 三种语言的读屏播报（拖进已置顶念「将置顶」「已置顶」，不再念移到别的公司）
        i18n = read("i18n.ts")
        sidebar = block(i18n, "const SIDEBAR = {")
        for lang in ("en", "zh", "th"):
            words = keys(block(block(sidebar, f"  {lang}: {{"), "drag: {"))
            self.assertEqual(words, {"instructions", "pickedUp", "movedTo", "droppedAt", "cancelled", "over", "droppedIn", "items"}, lang)
        self.assertIn("over: (name: string, _group: string) => `「${name}」将置顶。`,", i18n)
        self.assertIn("droppedIn: (name: string, _group: string) => `已置顶「${name}」。`,", i18n)
        for gone in ("moveToN", "moveTo:", "将移到", "已把「"):
            self.assertNotIn(gone, i18n)

    # ---- 用户 10-17「侧栏对话行改为仅可以在当前公司环境拖拽排序，不可以跨公司……只可以在当前公司自由排序或者拖到置顶」

    def test_manual_order_inside_a_group(self):
        # 组里手排的显示：手排列表里的按列表；不在列表里的，比存下那一刻新的排最上面（新对话出现在顶上），更旧的（「显示更多」后才取
        # 到的）接在后面，各自按排序方式；没手排过的组照旧；在「分组和排序」里选排序方式 = 清掉各组的手排
        self.run_page(STORAGE_STUB + r"""
import { arrangeGroup, companySections, groupStamp, pinGuest, withPinned } from "@/lib/managed-agents/sidebar-groups";
import { bindOrderUser, capList, clearGroupOrders, forgetGroups, forgetInGroups, GROUP_LIMIT, LIST_BYTES, setGroupOrder, SYNC_LIMIT, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import { refreshProjects } from "@/lib/managed-agents/projects";
const at = (m: number) => Date.UTC(2026, 9, 16, 12, 0) - m * 60000;
const projects = [{ id: "A", title: "Alpha" }, { id: "B", title: "Beta" }];
const row = (index: number, key: string, group: string, m: number, extra: any = {}) =>
  ({ index, title: key, starred: false, time: at(m), group, key, ...extra });
const rows = [row(0, "a1", "A", 10), row(1, "a2", "A", 20), row(2, "a3", "A", 30), row(3, "b1", "B", 5), row(4, "r1", "", 7), row(5, "r2", "", 9)];
const of = (s: any[] | null, key: string) => s!.find((x) => x.key === key);
// 手排过：a3、a1（存下那一刻这组最新的是 a1，10 分钟前：groupStamp）；a2（20 分钟前，比存下时旧）接在后面。（原来写成 15 分钟前，
// 那样 a1 是存下以后又有动静的，现在照常浮顶，见 Round11Tests）
const saved = { keys: ["a3", "a1"], at: at(10) };
assert.deepEqual(arrangeGroup(rows.slice(0, 3), saved).map((r) => r.key), ["a3", "a1", "a2"]);
// 新对话（比存下时新）排最上面，各自按排序方式（新的在前）
const fresh = [row(6, "n1", "A", 1), row(7, "n2", "A", 2), ...rows.slice(0, 3)];
assert.deepEqual(arrangeGroup(fresh, saved).map((r) => r.key), ["n1", "n2", "a3", "a1", "a2"]);
// 「显示更多」后才取到的更旧的：接在手排的后面（升序时同样按排序方式排在它们之间）
const older = [...rows.slice(0, 3), row(8, "o1", "A", 90), row(9, "o2", "A", 80)];
assert.deepEqual(arrangeGroup(older, saved).map((r) => r.key), ["a3", "a1", "a2", "o1", "o2"]);
// 按创建日期排：用创建时间比存下的时刻
const made = [row(10, "c1", "A", 1, { created: at(40) }), ...rows.slice(0, 3)];
assert.deepEqual(arrangeGroup(made, saved, { by: "created", desc: true }).map((r) => r.key), ["a3", "a1", "c1", "a2"]);
assert.deepEqual(arrangeGroup(made, saved, { by: "updated", desc: true }).map((r) => r.key), ["c1", "a3", "a1", "a2"]);
// 没手排过：原样
assert.deepEqual(arrangeGroup(rows.slice(0, 3), undefined).map((r) => r.key), ["a1", "a2", "a3"]);
// 分组里：A、最近按各自手排，B 没排过照排序方式；「显示更多」按摆好的先后取前几条
const s = companySections(rows, projects, { groupOrders: { A: saved, "": { keys: ["r2", "r1"], at: at(6) } }, shown: { A: 2 } });
assert.deepEqual(of(s, "project:A").all, [2, 0, 1]);
assert.deepEqual(of(s, "project:A").indices, [2, 0]);
assert.equal(of(s, "project:A").more, true);
assert.deepEqual(of(s, "recents").indices, [5, 4]);
assert.deepEqual(of(s, "project:B").indices, [3]);
// 拖对话、指针到了顶上时补一个空的已置顶（最上面）；拖进去还没放下的那一行从它那一组拿掉、排在置顶的第 at 位
const noPins = companySections(rows, projects)!;
assert.deepEqual(withPinned(noPins)[0], { type: "pinned", key: "pinned", indices: [] });
const guest = pinGuest(noPins, 1, 0);
assert.deepEqual(of(guest, "pinned").indices, [1]);
assert.deepEqual(of(guest, "project:A").indices, [0, 2]);
assert.deepEqual(of(guest, "project:A").all, [0, 2]);
const pinned = companySections([...rows, row(11, "p1", "B", 3, { starred: true }), row(12, "p2", "", 4, { starred: true })], projects)!;
assert.deepEqual(of(pinGuest(pinned, 4, 1), "pinned").indices, [11, 4, 12]);
assert.deepEqual(of(pinGuest(pinned, 4, 9), "pinned").indices, [11, 12, 4]);
assert.deepEqual(of(pinGuest(pinned, 4, 1), "recents").indices, [5]);

// 存下的 at：这组各行里最新的那个时间（按排序方式用的时间；本页新建、没有时间的不算），不用本机时钟（审查：本机时钟快了，存下后
// 几分钟内新建的对话被当成更旧，手排 5 条以上时藏进「显示更多」）
assert.equal(groupStamp(rows.slice(0, 3)), at(10));
assert.equal(groupStamp([...rows.slice(0, 3), row(13, "x", "A", 0, { time: Number.MAX_SAFE_INTEGER })]), at(10));
assert.equal(groupStamp(made, { by: "created", desc: true }), at(10));   // c1 创建在 40 分钟前，a1 没有创建时间按上次更新
assert.equal(groupStamp([]), 0);
// 本机时钟快了也一样：存下时 A 最新的是 a1（10 分钟前），之后服务端时间 5 分钟前新建的 n3 排最上面
const stamp = groupStamp(rows.slice(0, 3));
assert.deepEqual(arrangeGroup([row(14, "n3", "A", 5), ...rows.slice(0, 3)], { keys: ["a3", "a1", "a2"], at: stamp }).map((r) => r.key), ["n3", "a3", "a1", "a2"]);
// 同一时刻（不比它新）的不算新对话
assert.deepEqual(arrangeGroup([row(15, "same", "A", 10), ...rows.slice(1, 3)], { keys: ["a3", "a2"], at: stamp }).map((r) => r.key), ["a3", "a2", "same"]);

// 存：按组记、带 at，这次没显示的接在后面；置顶、删除的去掉；公司没了整组去掉；选排序方式清掉
bindOrderUser("skin3@x");
setGroupOrder("A", ["a3", "a1"], 100);
setGroupOrder("A", ["a1", "a3"], 200);
setGroupOrder("", ["r2"], 150);
assert.deepEqual(useSidebarOrder.getState().groups, { A: { keys: ["a1", "a3"], at: 200 }, "": { keys: ["r2"], at: 150 } });
setGroupOrder("A", ["a2"], 300);
assert.deepEqual(useSidebarOrder.getState().groups.A, { keys: ["a2", "a1", "a3"], at: 300 });
forgetInGroups(["a1", "r2"]);
assert.deepEqual(useSidebarOrder.getState().groups, { A: { keys: ["a2", "a3"], at: 300 } });   // 空了的组整组不要
setGroupOrder("B", ["b1"], 400);
await refreshProjects();                                 // 侧栏刚读过公司列表：按它收拾
forgetGroups(["B", "gone"]);
assert.deepEqual(Object.keys(useSidebarOrder.getState().groups), ["A"]);
clearGroupOrders();
assert.deepEqual(useSidebarOrder.getState().groups, {});
// 每组最多记 GROUP_LIMIT 条；拖到更后面时记到落点为止（审查：拖到第 65 位，截到 60 条后它不在里面，松手跳回去）
const long = Array.from({ length: 70 }, (_, k) => `s${k}`);
setGroupOrder("A", long, 1);
assert.equal(useSidebarOrder.getState().groups.A!.keys.length, GROUP_LIMIT);
const dragged = [...long.slice(0, 4), ...long.slice(5, 65), "s4", ...long.slice(65)];          // s4 拖到第 65 位
setGroupOrder("A", dragged, 1, ["s4"]);
assert.equal(useSidebarOrder.getState().groups.A!.keys.length, 65);
assert.equal(useSidebarOrder.getState().groups.A!.keys[64], "s4");
const block = [...long.slice(0, 60), "s61", "s62", "s60", ...long.slice(63)];                  // 多选一块排到第 61–63 位
setGroupOrder("A", block, 1, ["s60", "s61", "s62"]);
assert.deepEqual(useSidebarOrder.getState().groups.A!.keys.slice(60), ["s61", "s62", "s60"]);
setGroupOrder("A", ["s1"], 1, ["s1"]);                                                         // 落点在前面：照旧最多 GROUP_LIMIT 条
assert.equal(useSidebarOrder.getState().groups.A!.keys.length, GROUP_LIMIT);
// 公司组、置顶交给服务端前按条数、字节数截（审查：编号最长 128 字符、各列表到上限时整份超过服务端 32 KB，整次被拒、所有偏好都存
// 不上）。组里的各组合起来由服务端截（prefs.py 的 GROUPS_BYTES）
const longest = Array.from({ length: SYNC_LIMIT }, (_, i) => `${"x".repeat(124)}${String(i).padStart(4, "0")}`);
const cut = capList(longest);
assert.ok(new TextEncoder().encode(JSON.stringify(cut)).length <= LIST_BYTES);
assert.ok(cut.length > 40 && cut.length < SYNC_LIMIT);
assert.deepEqual(cut, longest.slice(0, cut.length));                                          // 留前面的（最旧的先丢）
assert.equal(capList(Array.from({ length: 400 }, (_, i) => `s${i}`)).length, SYNC_LIMIT);
assert.deepEqual(capList(["a", "b"]), ["a", "b"]);
""")

    def test_drag_pool_keeps_rows_in_their_group_or_the_pinned_section(self):
        # 共用拖动认哪些落点（碰撞、限位共用）：键盘只在原来那一列；指针进了哪一列的整块就只认那一列此刻的各项（没有别的项或收着时
        # 认整块）；有能放的整块而指针不在任何一块里（别的公司组、别的组头、不属于自己的最近、两组之间）就没有落点（审查：原来留在
        # 此刻所在的那一列，排到本组首尾存成手排，拖进过置顶的在别的公司上松手也置顶了）；没有整块落点的（卡片、公司组、置顶）照旧
        self.run_page(r"""
import { narrowPool } from "@/components/ui/drag";
const entry = (id: string, drag: any, container?: string) =>
  ({ id, data: { current: { drag: { id, ...drag }, ...(container ? { sortable: { containerId: container, index: 0, items: [] } } : {}) } } });
const row = { kind: "row", label: "t1", list: "rows:A", group: "A" };
const self = (container: string) => entry("row:t1", row, container);
const a2 = entry("row:t2", { kind: "row", list: "rows:A" }, "rows:A");
const p1 = entry("pin:p1", { kind: "pin", list: "pins" }, "pins");
const pins = (shut = false) => entry("zone:pins", { kind: "pins", list: "pins", zone: true, shut });
const home = entry("zone:rows:A", { kind: "group", list: "rows:A", zone: true });
const rects = new Map<any, any>([["zone:pins", { top: 0, height: 100 }], ["zone:rows:A", { top: 200, height: 100 }], ["zone:rows:B", { top: 400, height: 100 }]]);
const ids = (pool: any[]) => pool.map((e) => e.id).sort();
const at = (y: number | null, keyboard = false) => ({ keyboard, y, rects });
assert.deepEqual(ids(narrowPool([self("rows:A"), a2, p1, pins(), home] as any, "row:t1", row as any, at(50))), ["pin:p1"]);   // 指针在已置顶里
assert.deepEqual(ids(narrowPool([self("rows:A"), a2, p1, pins(), home] as any, "row:t1", row as any, at(250))), ["row:t1", "row:t2"]);
assert.deepEqual(ids(narrowPool([self("pins"), a2, p1, pins(), home] as any, "row:t1", row as any, at(50))), ["pin:p1", "row:t1"]);
assert.deepEqual(ids(narrowPool([self("pins"), a2, p1, pins(), home] as any, "row:t1", row as any, at(150))), []);   // 两块之间：没有落点（拖进过置顶也不留）
assert.deepEqual(ids(narrowPool([self("rows:A"), a2, p1, pins(), home] as any, "row:t1", row as any, at(150))), []);
// 别的公司组（B 的整块不在能放的里）、它的组头、不属于自己的最近：都没有落点，松手滑回原位、不排到本组首尾、不写组里的手排
assert.deepEqual(ids(narrowPool([self("rows:A"), a2, p1, pins(), home] as any, "row:t1", row as any, at(450))), []);
assert.deepEqual(ids(narrowPool([self("pins"), a2, p1, pins(), home] as any, "row:t1", row as any, at(420))), []);
// 没有整块落点的拖动（卡片、公司组、置顶只在一列里排）：照旧认此刻所在那一列
const c1 = entry("card:c1", { kind: "card", list: "cards" }, "cards");
const c2 = entry("card:c2", { kind: "card", list: "cards" }, "cards");
assert.deepEqual(ids(narrowPool([c1, c2] as any, "card:c1", { id: "card:c1", kind: "card", label: "c1", list: "cards" } as any, at(999))), ["card:c1", "card:c2"]);
assert.deepEqual(ids(narrowPool([self("pins"), p1, pins(), home] as any, "row:t1", row as any, at(250))), ["zone:rows:A"]);       // 组里没别的：认整块（回来）
assert.deepEqual(ids(narrowPool([self("rows:A"), a2, pins(), home] as any, "row:t1", row as any, at(50))), ["zone:pins"]);       // 已置顶是空的
assert.deepEqual(ids(narrowPool([self("rows:A"), a2, p1, pins(true), home] as any, "row:t1", row as any, at(50))), ["zone:pins"]);  // 收着
// 键盘：只在原来那一列（不拖进置顶）
assert.deepEqual(ids(narrowPool([self("rows:A"), a2, p1, pins(), home] as any, "row:t1", row as any, at(50, true))), ["row:t1", "row:t2"]);
""")
        drag = read("components/ui/drag.tsx")
        # 拖动区：碰撞按 narrowPool 收窄后，指针取最近的中心、键盘取瞄着的那一项；键盘方向键只在本列（排序项的那一列）；指针拖动
        # 经过时交给调用方（onOver）
        collide = drag.split("  private collide() {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("const pool = narrowPool(entries, d.id, d.data, { keyboard, y: d.pointer?.y ?? box.top + box.height / 2, rects });", collide)
        self.assertIn("closestCenter({ collisionRect: box, droppableRects: rects, droppableContainers: pool })[0]", collide)
        step = drag.split("  private step(event: KeyboardEvent) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("return !!item && !item.zone && item.list === list;", step)
        # 经过的是它自己不交（无界面 Chrome 复刻侧栏实测：排进置顶重挂以后经过自己，它的说明还是原来那一组，交了就被当成回到
        # 原组，一进一出来回跳、最后放下排到置顶最上面）
        over = drag.split("  private overChanged(d: Session, next: string | null) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn('if (d.kind !== "keyboard" && next !== d.id) {', over)
        self.assertIn("this.props.onOver?.(d.data, to, below);", over)
        self.assertNotIn("pointerWithin", drag)
        # 能放的各处只认登记着、没禁用、调用方说能放的（它自己有 list 就算）
        pool = drag.split("  private candidatesFor(d: Session): string[] {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("if (d.data.list) out.push(item.id);", pool)
        self.assertIn("if (item.disabled) continue;", pool)
        self.assertIn("if (this.props.accepts(d.data, item.data)) out.push(item.id);", pool)

    def test_drop_lands_first_then_hands_over(self):
        # 放下：浮层落到空档（或收着的那一列的组头并收起）；减少动态效果时不播
        self.run_page(r"""
import { dropAnimation } from "@/components/ui/drag";
const g = globalThis as any;
g.getComputedStyle = () => ({ transform: "none", transformOrigin: "0px 0px" });
const node = (rect: any) => ({ dataset: {} as Record<string, string>, getBoundingClientRect: () => ({ ...rect, width: 200, right: rect.left + 200, bottom: rect.top + rect.height }), ownerDocument: null });
const animations: any[] = [];
const overlay = { ...node({ top: 466, left: 10, height: 32 }), animate(frames: any, opts: any) { const a: any = { frames, opts, startTime: null }; animations.push(a); return a; } };
const motion = { duration: 200, easing: "cubic-bezier(.32, .72, 0, 1)" };
g.document.timeline = { currentTime: 1000 };
const args = (active: any) => ({ active: { id: "row:t1", node: active, rect: { top: 398, left: 10, width: 200, height: 32 } },
  dragOverlay: { node: overlay, rect: { top: 466, left: 10, width: 200, height: 32 } }, transform: { x: 0, y: 186, scaleX: 1, scaleY: 1 } });
// 落到空档：同 dnd-kit 默认（浮层从放手处滑到落点，动效变量），落点那一项播完前不显示
const gap = node({ top: 398, left: 10, height: 32 });
const done = dropAnimation(motion, () => null)(args(gap) as any) as Promise<void>;
assert.equal(gap.dataset.dragLanding, "");
assert.equal(overlay.dataset.dropping, "");
assert.deepEqual(animations[0].opts, { duration: 200, easing: "cubic-bezier(.32, .72, 0, 1)", fill: "forwards" });
assert.equal(animations[0].frames[1].transform, "translate3d(0px, 118px, 0) scaleX(1) scaleY(1)");   // 186 - (466 - 398)
assert.equal(animations[0].startTime, null);                  // 结束拖动那次提交里才播的（取消）：从画出来的那一帧起算，不回拨
animations[0].onfinish();
await done;
assert.equal("dragLanding" in gap.dataset, false);
// 落点不是空档本身（已置顶收着 → 组头；空档在可见范围外 → 可见范围的边上，见 clampIntoView）：落到那里并收起
// （data-dropping="into"），位置按 into 给的框
dropAnimation(motion, () => ({ top: 614, left: 10 }))(args(node({ top: 280, left: 10, height: 32 })) as any);
assert.equal(overlay.dataset.dropping, "into");
assert.equal(animations[1].frames[1].transform, "translate3d(0px, 334px, 0) scaleX(1) scaleY(1)");   // 186 + (614 - 466)
// 已经在落点：不播
const here = { ...args(node({ top: 466, left: 10, height: 32 })) };
here.active.rect = { top: 466, left: 10, width: 200, height: 32 };
assert.equal(dropAnimation(motion, () => null)({ ...here, transform: { x: 0, y: 0, scaleX: 1, scaleY: 1 } } as any), undefined);
// 拖进已置顶以后按 Esc：拖动库给的还是置顶里已经卸下的那一份，原来那一组重挂了一份新的（审查：滑回的 0.2 秒里同一段对话两份）。
// 按拖动编号在此刻的页面里找：新的那一份播完前不显示，浮层落到它那里
const gone = { ...node({ top: 0, left: 0, height: 0 }), isConnected: false };
const home = { ...node({ top: 258, left: 10, height: 32 }), isConnected: true };
home.dataset.dragId = "row:t1";
const other = { ...node({ top: 300, left: 10, height: 32 }), isConnected: true };
other.dataset.dragId = "row:t2";
g.document.querySelectorAll = (selector: string) => (selector === "[data-drag-id]" ? [other, home] : []);
const back = args(gone);
const cancelled = dropAnimation(motion, () => null)(back as any) as Promise<void>;
assert.equal(home.dataset.dragLanding, "");
assert.equal("dragLanding" in gone.dataset, false);
assert.equal(animations[2].frames[1].transform, "translate3d(0px, -22px, 0) scaleX(1) scaleY(1)");   // 186 - (466 - 258)
animations[2].onfinish();
await cancelled;
assert.equal("dragLanding" in home.dataset, false);
// 减少动态效果：不播
assert.equal(dropAnimation(null, () => null)(args(node({ top: 398, left: 10, height: 32 })) as any), undefined);
assert.equal(animations.length, 3);
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
// 已在落点（第 9 轮：拖到首尾被限位停在落点上）：照样播（位移为 0，略放大、阴影收回），结束拖动同样等它播完
const still = landOverlay(overlay as any, box(466) as any, motion, () => null)!;
assert.equal(still.frames[0].transform, still.frames[1].transform);
assert.equal(still.opts.duration, 200);
// 减少动态效果、量不到：不播（调用方立即结束拖动）
const count = animations.length;
assert.equal(landOverlay(overlay as any, card as any, null, () => null), null);
assert.equal(landOverlay({ ...overlay, computed: "none" } as any, card as any, motion, () => null), null);
assert.equal(landOverlay(overlay as any, { ...card, isConnected: false } as any, motion, () => null), null);
assert.equal(animations.length, count);

// 拖动库随后结束拖动、要播它那一次：不再另播，接着等松手时起播的那一次；落点那一项在播完前不显示
const c = landOverlay(overlay as any, card as any, motion, () => null)!;
const before = animations.length;
const wait = dropAnimation(motion, () => null)({ active: { id: "card", node: card, rect: { top: 334, left: 10, width: 200, height: 32 } },
  dragOverlay: { node: overlay, rect: { top: 466, left: 10, width: 200, height: 32 } }, transform: { x: 0, y: 120, scaleX: 1, scaleY: 1 } } as any) as Promise<void>;
assert.equal(animations.length, before);
assert.equal(card.dataset.dragLanding, "");
c.finish();
await wait;
assert.equal("dragLanding" in card.dataset, false);
// 已经播完：不再播
const d = landOverlay(overlay as any, card as any, motion, () => null)!;
d.finish();
assert.equal(dropAnimation(motion, () => null)({ active: { id: "card", node: card, rect: { top: 334, left: 10, width: 200, height: 32 } },
  dragOverlay: { node: overlay, rect: { top: 466, left: 10, width: 200, height: 32 } }, transform: { x: 0, y: 54, scaleX: 1, scaleY: 1 } } as any), undefined);
assert.equal(animations.length, before + 1);

// 先落位、再结束拖动
const listeners = new Map<string, () => void>();
g.addEventListener = (type: string, run: () => void) => listeners.set(type, run);
g.removeEventListener = (type: string) => listeners.delete(type);
const letGo = new ReleaseFirst();
const log: any[] = [];
letGo.hold(() => log.push(["end"]), null, 1000);           // 没播落位：立即结束
assert.deepEqual(log, [["end"]]);
let frozen = 0;
const e = animate([], {});
letGo.hold(() => log.push(["end"]), e, 1000, () => (frozen++, () => frozen--));
assert.equal(letGo.waiting, true);
assert.equal(frozen, 1);                                         // 等着的时候自动滚动不滚
assert.deepEqual([...listeners.keys()].sort(), ["keydown", "pointerdown", "touchstart"]);
assert.equal(log.length, 1);                                     // 落位没播完：还没结束拖动
e.finish();
await g.sleep(0);
assert.equal(log.length, 2);                                     // 播完才结束
assert.deepEqual([frozen, listeners.size, letGo.waiting], [0, 0, false]);
// 落位中又按下了（要开始下一次）：当场结束；之后播完不再结束一次
const f = animate([], {});
letGo.hold(() => log.push(["again"]), f, 1000);
listeners.get("pointerdown")!();
assert.deepEqual(log.at(-1), ["again"]);
f.finish();
await g.sleep(0);
assert.equal(log.filter((x) => x[0] === "again").length, 1);
// 动画没在走（页面在后台）：过 wait 毫秒自己结束
letGo.hold(() => log.push(["late"]), animate([], {}), 20);
await g.sleep(40);
assert.deepEqual(log.at(-1), ["late"]);
// 新的一次放下：上一次还在等的先结束
letGo.hold(() => log.push(["first"]), animate([], {}), 1000);
letGo.hold(() => log.push(["second"]), null, 1000);
assert.deepEqual(log.slice(-2), [["first"], ["second"]]);
""")
        drag = read("components/ui/drag.tsx")
        # 落点在松手时量：排序时加上让位的位移；收着的组 / 可见范围外照旧（into 收 shift）
        self.assertIn("const rect = fold || !shift ? base : { ...base, top: base.top + shift, bottom: base.bottom + shift };", drag)
        # 卸下时还在等落位的放下当场结束
        self.assertIn("letGo.flush(); // 卸下时还在等落位的放下当场结束", drag)

    def test_the_column_slides_when_the_gap_changes_group(self):
        # 空档换了组（对话拖进已置顶 / 挪回自己那一组）：位置变了的行、组头从原来的位置带过渡滑到新位置（FLIP，Web Animations 播 translate，不碰行内样式）；
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
let changed = 0;
const stop = flowLayout(root, "x", motion, () => changed++);
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
assert.equal(changed, 1);                              // 版面变过一次：拖动区据此重量各落点
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

    def test_the_column_slide_ignores_a_sort_shift_sliding_back(self):
        # 第 12 轮审查：对话行拖进已置顶后取消，已置顶里让过位的行带过渡滑回（行内 transform 的 CSS 过渡），紧接着挪回原组整列重挂。
        # 整列滑动记的是版面位置，不把正在滑回的让位当成版面挪了（原来再用 translate 反向滑一遍，两段叠起来先冲过头约 19px 再回来）；
        # 版面真挪了的照常滑
        self.run_page(r"""
import { flowLayout } from "@/components/ui/drag";
const g = globalThis as any;
let observe: (() => void) | null = null;
g.MutationObserver = class { constructor(run: () => void) { observe = run; } observe() {} disconnect() { observe = null; } };
// 计算样式：transform 是让位此刻播到的位置（CSS 过渡中间值），translate 是整列滑动
g.getComputedStyle = (el: any) => ({ translate: "none", transform: el.now ? `matrix(1, 0, 0, 1, 0, ${el.now})` : "none" });
const root: any = { scrollTop: 0, style: { overflowAnchor: "" }, getBoundingClientRect: () => ({ top: 0 }), els: [] as any[], querySelectorAll: () => root.els };
const el = (top: number, id: string) => {
  const e: any = { top, now: 0, dataset: { dragId: id }, anims: [] as any[], matches: () => false,
    getBoundingClientRect: () => ({ top: e.top + e.now }),
    animate(frames: any, opts: any) { const a: any = { frames, opts, cancel() {} }; e.anims.push(a); return a; } };
  return e;
};
const a1 = el(280, "pin:a1"), guest = el(314, "row:b"), head = el(348, "head:g");
root.els = [a1, guest, head];
a1.now = 34;                                            // 拖进置顶：a1 让到下面（版面 280，看到 314）
const stop = flowLayout(root, "x", { duration: 200, easing: "ease" });
// 取消：a1 的让位开始带过渡滑回（此刻还差 30px），被拖的那一行挪回原组，后面的组头上移
a1.now = 30;
head.top = 314;
root.els = [a1, head];
observe!();
assert.equal(a1.anims.length, 0);                       // a1 的版面没变：不另起整列滑动
assert.deepEqual(head.anims.map((x: any) => x.frames), [[{ translate: "0 34px" }, { translate: "0 0" }]]);   // 版面真挪了的照常滑
stop();
""")

    def test_dragged_chat_rows_use_the_shared_gap_and_landing(self):
        drag = read("components/ui/drag.tsx")
        # 对话行与其他排序项同一份（useSortableItem）：原位留空档（不再是淡影），拖进另一列在那里重挂的同一行按 data-drag-id 认
        self.assertIn('"data-drag-id": drag.id,', drag)
        self.assertIn('"data-drag-source": isDragging ? ("gap" as const) : undefined,', drag)
        self.assertNotIn('"ghost"', drag)
        # 用户 10-17：组之间移动（空档按排序方式去别的组、落位后发移动请求）整套去掉
        for gone in ("useDragItem", "useDropTarget", "settleMove", "class Landing", "PlacedContext", "revealGap", "onPlace", "running={busy > 0}"):
            self.assertNotIn(gone, drag)
        # 拿起时的那份说明一直用到底（拖进置顶重挂的行仍按原来那一列认）
        self.assertIn("  /** 拿起时的那份说明：拖进另一列的那一项会在那一列里重挂，拖动中一直按拿起时的认（原来那一列不变）。 */\n  data: DragData;", drag)
        self.assertIn("lifted: (active) => (this.drag && this.drag.id === String(active.id) ? this.drag.data : dataOf(active)),", drag)
        # 收着的那一列整块：指着它时整块高亮、浮层半透明，落位落到组头并收起
        self.assertIn('return { ref, "data-drop-target": over && shut ? ("" as const) : undefined };', drag)
        self.assertIn("const shut = next && to?.zone && to.shut ? next : null;", drag)
        self.assertIn("const fold = !!zone && !zone.contains(node);", drag)
        # 审查：落点在滚动区可见范围外时落到可见范围的边上并收起，不飞出列表（自写引擎落位前不滚落点那一项，列表不跳）
        self.assertIn("const shown = clampIntoView(rect, viewOf(node));", drag)
        self.assertIn("return fold || shown !== rect ? shown : null;", drag)
        self.assertNotIn("scrollIntoView(", drag.split("  private finish(", 1)[1].split("\n  }\n", 1)[0])
        self.assertNotIn("scrollIntoView(", drag.split("  private release(", 1)[1].split("\n  }\n", 1)[0])
        # 整列一起滑：拖动中（拿起到结束：结束拖动的同步提交之前停）；版面变了就在下一帧重量各落点（碰撞按新位置）
        watch = drag.split("  private watch(d: Session) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("const stop = flowLayout(root, this.flow, this.transition, () => this.remeasureSoon());", watch)
        # 拿起那一帧画完以后才开始盯（第 11 轮 B14：拿起那次提交里先量整列要当场算整页样式、布局）
        self.assertIn("afterPaint(() => this.drag === d && this.watch(d));", drag)
        again = drag.split("  private remeasureSoon() {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("if (!d || d.cancelling || d.queued) return;", again)
        self.assertIn("afterPaint(() => {", again)
        self.assertIn("this.measure(d, true);", again)
        finish = drag.split("  private finish(d: Session, work: () => void) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertLess(finish.index("for (const stop of d.stops.splice(0)) stop();"), finish.index("flushSync(run)"))
        # 第 6 轮：落位在松手那一下起播（landOverlay，从松手这一刻起算），结束拖动等落位播完（ReleaseFirst）；不再回拨到上一帧
        self.assertNotIn("startNow", drag)
        self.assertIn("fromNow(animation);", drag)
        self.assertIn("mouse: { activationConstraint: MOUSE_START, beforeEnd, beforeCancel },", drag)
        # 键盘另带 owns（第 12 轮 D15：只认按在把手上的键），放下、取消同样先交 beforeEnd / beforeCancel
        self.assertIn("keyboard: { keyboardCodes: KEYS, scrollBehavior: KEYBOARD_SCROLL, step, owns, beforeEnd, beforeCancel },", drag)
        self.assertIn("this.letGo.hold(end, animation, this.landWait, () => freezeScroll(d.scrollers));", drag)
        rows = read("components/assistant-ui/thread-list.tsx")
        self.assertIn("const FLOW = '[data-slot=\"aui_thread-list-item\"], .cc-group-row, .cc-show-more';", rows)
        css = read("globals.css")
        # 多选里一起拖的其余各行淡影；拖的那一条自己是空档（不被淡影规则盖住）；落到收着的那一列收起
        self.assertIn(':root[data-cc-drag-bulk] [data-slot="aui_thread-list-item"][data-selected]:not([data-drag-source], [data-drag-landing]) { opacity: var(--cc-drag-ghost); }', css)
        self.assertIn('.cc-drag-overlay[data-dropping="into"] .cc-drag-lift { scale: var(--cc-drag-fold); opacity: 0; }', css)
        self.assertNotIn('[data-drag-source="ghost"]', css)

    def test_fast_arrow_keys_each_move_one_step(self):
        # 测试窗口：键盘拖动时连按（间隔约 30ms）的方向键被吞掉一些，0.4–0.6 秒按一下每下都动。原因：拖动库的键盘传感器在目标
        # 落在滚动区下半截（往上拖时上半截）时不挪浮层、改成平滑滚动滚动区，下一下按在滚动途中，从滚到一半的位置再加一格。
        # 无界面 Chrome 复现（40 行、500px 滚动区、从第 9 行起连按 4 下）：平滑滚动只挪 2 格，立即滚动 4 格都挪
        drag = read("components/ui/drag.tsx")
        self.assertIn('export const KEYBOARD_SCROLL: ScrollBehavior = "auto";', drag)   # 不用 "instant"（旧 WebView 不认，scrollTo 报错）
        self.assertNotRegex(read("globals.css"), r"scroll-behavior:\s*smooth")              # auto 照 CSS：各滚动区都不平滑滚
        self.assertIn("scrollBehavior: KEYBOARD_SCROLL", drag)
        # 自写的键盘拖动每次按键现读这一项：目标落在滚动区下半截（往上时上半截）就立即滚到位，每一下都从滚好的位置算
        step = drag.split("  private step(event: KeyboardEvent) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("el.scrollTo({ top: next, behavior: this.options.keyboard.scrollBehavior });", step)
        self.assertIn("el.scrollBy({ top: -scrollDelta, behavior: this.options.keyboard.scrollBehavior });", step)
        self.assertIn("if (d.scroller) d.scrolled = scrollTopOf(d.scroller);", step)   # 按下这一下现读滚动位置，不等滚动事件
        self.assertNotIn("smooth", drag)

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

class NoAlwaysOnTests(unittest.TestCase):
    """用户 10-16（ADR-021 第 56、58 条）：「常驻模式」从界面拿掉——替别人接活、开机锁屏都没有了，电脑列表不再写「常驻」；
    旧桌面端的状态里还带 always_on 也不显示。防睡眠照 Claude 改成「CC 开着时保持电脑唤醒」「用电池时也保持」（KeepAwakeTests）。"""

    def test_the_word_and_the_switch_are_gone(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in (FRONTEND / "src").rglob("*.ts*"))
        self.assertNotIn("alwaysOn", source)
        self.assertNotIn("always_on", source)
        i18n = (FRONTEND / "src/i18n.ts").read_text(encoding="utf-8")
        self.assertIn('autostart: "开机时自动启动 CC"', i18n)
        self.assertNotIn("常驻", i18n)


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class KeepAwakeTests(unittest.TestCase):
    """「此电脑 → 系统」里的两个开关（同 Claude 桌面版「Keep computer awake」「Keep awake on battery power」，companion
    keep_awake.py）：只在 CC 桌面版里（有桥才有「此电脑」），旧版桌面端的状态没有 keep_awake 就不显示；只有开关和标签。"""

    STUBS = UpdateBlockedBehaviourTests.STUBS

    def run_page(self, checks, lang):
        run_ts(UPDATE_PAGE + checks + "\ng.done();\n", stubs=self.STUBS, lang=lang)

    def test_two_switches_only_when_the_desktop_reports_them(self):
        self.run_page(r"""
const changed: string[] = [];
const props = (state: any) => ({ state, onChange: (method: string, on: boolean) => changed.push(`${method}:${on}`) });
const shown = html(KeepAwakeRows, props({ keep_awake: true, keep_awake_on_battery: false }));
assert.deepEqual(text(shown), ["CC 开着时保持电脑唤醒", "用电池时也保持"]);
assert.deepEqual([...shown.matchAll(/role="switch" aria-checked="(\w+)"/g)].map((m) => m[1]), ["true", "false"]);
// 点开关：桥方法 keep_awake / keep_awake_on_battery
const element: any = KeepAwakeRows(props({ keep_awake: true, keep_awake_on_battery: false }));
const switches = element.props.children.map((row: any) => row.props.children);
switches[0].props.onChange(false);
switches[1].props.onChange(true);
assert.deepEqual(changed, ["keep_awake:false", "keep_awake_on_battery:true"]);
assert.equal(html(KeepAwakeRows, props({})), "");   // 旧版桌面端（没有这一项）：不显示
""", lang="zh")
        self.run_page(r"""
assert.deepEqual(text(html(KeepAwakeRows, { state: { keep_awake: false, keep_awake_on_battery: true }, onChange() {} })),
  ["Keep computer awake while CC is open", "Keep awake on battery power"]);
""", lang="en")

    def test_only_in_this_computer_system_section(self):
        source = (FRONTEND / "src/components/settings.tsx").read_text(encoding="utf-8")
        self.assertEqual(source.count("<KeepAwakeRows"), 1)
        system = source.split("function System(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("<KeepAwakeRows", system)
        self.assertIn('{current === "system" && api && <System api={api} />}', source)   # 有桥才有「此电脑 → 系统」
        i18n = (FRONTEND / "src/i18n.ts").read_text(encoding="utf-8")
        for line in ('keepAwake: "CC 开着时保持电脑唤醒", keepAwakeOnBattery: "用电池时也保持"',
                     'keepAwake: "Keep computer awake while CC is open", keepAwakeOnBattery: "Keep awake on battery power"',
                     'keepAwake: "ไม่ให้คอมพิวเตอร์สลีปขณะเปิด CC", keepAwakeOnBattery: "รวมถึงขณะใช้แบตเตอรี่"'):
            self.assertIn(line, i18n)


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
            self.assertIn('<DialogPrimitive.Title className="cc-dialog-title">\n              <Marquee className="block truncate">{title}</Marquee>', read(rel), rel)
        # 父项、单选项、勾选项（第 7 轮：单选 / 勾选分成 RadioItem / CheckboxItem 两个）
        self.assertEqual(read("components/assistant-ui/sidebar-group.tsx").count('<Marquee className="min-w-0 flex-1 truncate">{label}</Marquee>'), 3)
        self.assertIn('<Marquee className="typing-dots-label ms-(--cc-dots-gap) min-w-0 truncate" aria-hidden="true">{text}</Marquee>', read("components/assistant-ui/thread.tsx"))
        self.assertIn('<Marquee className={cn("max-w-(--cc-settings-value-max) truncate text-body"', read("components/settings.tsx"))
        panel = read("components/results-panel.tsx")
        self.assertIn('"cc-fade-cell border border-line"', panel)
        self.assertIn('className="cc-table border-collapse text-(length:--cc-text-sm)" data-kind="sheet"', panel)  # 最宽 288 由 .cc-table[data-kind="sheet"] 给（V13）
        self.assertIn('watchOverflow([el], () => el.querySelectorAll<HTMLElement>("td.cc-fade-cell"))', panel)
        # 其余还写着 truncate 的只能在 Marquee 上（盖掉省略号）；保留原样的：文件下拉框（原生控件）。没用到的官方侧栏菜单按钮
        # （[&>span:last-child]:truncate）标准化 B2 已删
        allowed = {("components/results-panel.tsx", "<select")}
        for path in sorted(SRC.rglob("*.tsx")):
            rel = path.relative_to(SRC).as_posix()
            text = path.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines()):
                if not re.search(r"\btruncate\b", line) or "Marquee" in line:
                    continue
                context = "\n".join(text.splitlines()[max(0, i - 3):i + 1])
                self.assertTrue(any(rel == f and key in context for f, key in allowed), f"{rel}:{i + 1}: {line.strip()}")
        # 多行截断（line-clamp）不在这次范围，保持原样
        self.assertIn("line-clamp-(--cc-alert-lines)", read("components/assistant-ui/thread.tsx"))


class Round5SmallFixesTests(unittest.TestCase):
    """测试窗口第 5 轮（lab 20b9f387）五处小问题：聚焦描边、组头 Alt+↑/↓ 播报、拿起的播报、Esc 一次一层、渐隐补量。"""

    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + body + "\n(globalThis as any).done();\n",
               stubs={"socket.io-client": "export function io() { return { on() { return this; }, off() { return this; }, connected: true }; }"})

    def test_menu_items_show_the_highlight_not_a_blue_ring(self):
        # 用户 10-16：「这个边框颜色不要太泛滥吧」——菜单项、下拉选项只用底色表示当前项，不画蓝框
        css = read("globals.css")
        self.assertIn(':is(.cc-menu-item, [role="menuitem"], [role="menuitemradio"], [role="menuitemcheckbox"], [role="option"]):is(:focus, :focus-visible) {', css)
        rule = css.split('[role="option"]):is(:focus, :focus-visible) {', 1)[1].split("}", 1)[0]
        self.assertIn("outline: none; box-shadow: none;", rule)

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
        # 点名的几处：组头待处理数、记住的选择的行（.cc-choice-row）、文件夹「更换… / 移除」（.cc-link-btn）、定时任务卡的任务行（.cc-list-row）、显示更多、
        # 行按钮（侧栏出错 × 第 11 轮起改用 .cc-row-btn，.cc-group-btn 已删）、知识库一条、胶囊、标题栏公司名
        for rule in (".cc-group-count:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     ".cc-link-btn:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     ".cc-choice-row:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     "button.cc-list-row:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     ".cc-show-more:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     ".cc-row-btn:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     ".cc-note-text:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     ".cc-chip-btn:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     ".cc-title-company:focus-visible { box-shadow: var(--cc-shadow-focus); }",
                     # 消息下面的「复制」、知识库表单的「取消」本来就有（Button 的 focus-visible:shadow-focus、.cc-btn）
                     ".cc-btn:focus-visible { box-shadow: var(--cc-shadow-focus); }"):
            self.assertIn(rule, css)
        self.assertIn("focus-visible:shadow-focus", read("components/ui/button.tsx"))
        # 侧栏宽度拖把（分隔条）：键盘聚焦时那根线换成聚焦蓝、1px
        self.assertIn(".cc-split:focus-visible::before { background: var(--cc-focus-ring); width: var(--cc-ring-w); }", css)
        # 兜底去掉 outline:auto 后，自带阴影（盖过兜底的 box-shadow）的按钮要自己写聚焦描边，否则一点都看不见：
        # 设置里分段选择的选中格（shadow-sm）、手机圆按钮与抽屉「新建」胶囊（手机顶栏 / 抽屉的圆按钮规则不分层，聚焦规则也跟在后面）
        # 第 11 轮按钮排查 H26：文字格与图标格一样高 28、手机点按区 44（cc-tap）
        self.assertIn('"cc-tap flex h-(--cc-control-sm) items-center justify-center rounded-md px-(--cc-space-12) py-(--cc-space-4) text-body [&_svg]:size-(--cc-icon-sm) focus-visible:shadow-focus",',
                      read("components/settings.tsx"))
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
for (const say of [dragAnnouncements(texts), dragAnnouncements(texts, { zoneLabel: () => "Pinned" })]) {
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
        self.assertIn("if (!end && over.id === active.id && !moved) return undefined;", drag)

    def test_alt_arrow_moves_are_announced_like_dragging(self):
        # 第 5 轮第 2 项：公司组头 Alt+↑/↓ 挪一格时读屏什么也没念。挪完念新位置，文案同键盘拖动的 movedTo（三语）；置顶、卡片同样
        self.run_page(r"""
import { spotIn } from "@/components/ui/drag";
assert.deepEqual(spotIn(["a", "b", "c"], "b"), { at: 2, total: 3 });
assert.equal(spotIn(null, "b"), null);          // 到头了没挪：不念
assert.equal(spotIn(["a"], "x"), null);
""")
        drag = read("components/ui/drag.tsx")
        self.assertIn('<div role="status" aria-live="assertive" aria-atomic="true" className="cc-move-live sr-only">', drag)
        self.assertIn("const text = texts.movedTo(name, spot.at, spot.total);", drag)
        self.assertIn("if (spot) say(label, spot);", drag)
        self.assertIn("engine.sayMove = useMemo(", drag)   # 拖动区给的播报（同一个拖动区一份播报区）
        rows = read("components/assistant-ui/thread-list.tsx")
        # 同一份回调（第 10 轮 B 段：行记住，拖动库重画时不跟着重画）
        self.assertEqual(rows.count("const onMove = useCallback((delta: -1 | 1) => said(scope.move(id, delta)), [said, scope, id]);"), 2)
        self.assertIn("<Row drag={drag} onMove={scope.locked ? undefined : onMove} />", rows)
        self.assertIn('move: (id, delta) => spotIn(pinsNow.current.save(moveBy(pinsNow.current.ids, id, delta)), id)', rows)
        self.assertIn("pinsNow.current = { ids: pinIds, save: savePins };", rows)
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

    def test_search_locks_every_sidebar_drag(self):
        # 第 6 轮第 5 项原是搜索时把对话拖向收着的组头要高亮。用户 10-17 起对话只在本组排、拖进置顶，搜索时对话行与置顶、公司组
        # 一样锁住不能拖（搜索结果里的先后不是组里的先后），那一套搜索时的高亮、移过去就不在结果里的处理一并去掉
        rows = read("components/assistant-ui/thread-list.tsx")
        groups = rows.split("const CompanyGroups: FC", 1)[1].split("\nconst DayGroups", 1)[0]
        self.assertIn('scope = { mode: "row", group, locked: searching, move };', groups)
        self.assertIn("      locked: searching,\n", groups)        # 置顶（pinScope）
        self.assertIn("              locked={searching}\n", groups)        # 公司组
        self.assertIn("const locked = scope.locked || !remoteId;", rows)
        for gone in ("shutOutside", "leavesResults", "matchesAfterMove", "fold={"):
            self.assertNotIn(gone, groups)


class Round8Tests(unittest.TestCase):
    """测试窗口第 8 轮（lab 49de1497，手机 CDP + 电脑）查出的前端几处。"""

    def run_page(self, body, lang="en"):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + body
               + "\n(globalThis as any).done();\n", lang=lang)

    def test_keyboard_drag_keeps_focus_on_the_dragged_item(self):
        # 第 1 项：从「最近」空格拿起一行，↑ 进别的公司组后焦点落到邻行（组件库对话行自带的 ↑ ↓ 导航挪的；原来那一行换组重挂、
        # 节点卸下），之后方向键只移焦点、拖动卡住。现在：拖动中按在把手上的方向键只归拖动（标成已处理，组件库的导航不接手）；
        # 键盘拿起的那一项重挂后焦点跟到新节点；放下 / 取消后焦点留在移动后的那一行（用户 10-17 起键盘只在本组里挪，不会落进收着的组）
        drag = read("components/ui/drag.tsx")
        self.assertIn('const DRAG_KEYS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"]);', drag)
        self.assertIn("      if (isDragging() && DRAG_KEYS.has(event.key)) event.preventDefault();\n"
                      '      else if (!disabled) engine.press("keyboard", id, event);', drag)
        # 键盘拿起的那一项（stateOf 为 2）：焦点跟着它的把手，重挂过的跟到新的把手上
        self.assertIn('return d && d.id === id ? (d.kind === "keyboard" ? 2 : 1) : 0;', drag)
        self.assertIn("if (this.stateOf(item.id) === 2 && node?.isConnected && document.activeElement !== node) node.focus({ preventScroll: true });", drag)
        item = drag.split("export function useSortableItem(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("if (state === 2) engine.follow(item);", item)
        self.assertIn("const handleRef = useCallback((node: HTMLElement | null) => engine.attachHandle(item, node), [engine, item]);", item)
        self.assertEqual(drag.count("keepFocusOn(d.id, d.event);"), 2)   # 放下（键盘不拖进另一列，不会落进收着的整块）、取消
        self.assertIn("  if (isDragging() || !event.altKey", drag)                     # Alt+方向键在拖动中也归拖动
        rows = read("components/assistant-ui/thread-list.tsx")
        self.assertIn("        if (renaming) return;\n"
                      "        // 键盘拖动中：键只归拖动（方向键由把手标成已处理，组件库行自带的 ↑ ↓ 不移焦点；不开菜单）\n"
                      "        if (isDragging()) return dragKey?.(e);", rows)
        # 第 8 轮审查：公司组头拖动中同样只归拖动（菜单键不开「···」，同对话行）
        group = read("components/assistant-ui/sidebar-group.tsx")
        self.assertIn("      onKeyDown={(e) => {\n"
                      "        // 键盘拖动中：键只归拖动（同对话行；不开「···」、Alt+方向键不挪组）\n"
                      "        if (isDragging()) return handle?.onKeyDown?.(e);\n"
                      "        if (onMenuKey && isMenuKey(e)) {", group)
        self.run_page(r"""
import { keepFocusOn, rememberHandle } from "@/components/ui/drag";
const g = globalThis as any;
let frames: Array<() => void> = [];
g.requestAnimationFrame = (fn: () => void) => (frames.push(fn), frames.length);
g.cancelAnimationFrame = () => {};
const presses: Array<() => void> = [];
g.addEventListener = (type: string, fn: () => void) => void (type === "pointerdown" && presses.push(fn));
const tick = () => { const run = frames; frames = []; for (const f of run) f(); };
const body = { name: "body", isConnected: true };
g.document.body = body;
const focused: string[] = [];
const el = (name: string, extra: any = {}) => {
  const e: any = { name, isConnected: true, getClientRects: () => [1], focus() { focused.push(name); g.document.activeElement = e; }, ...extra };
  return e;
};
// 键盘放下：这一项换组重挂（新节点先挂、旧节点后卸也不丢登记），焦点掉到 body → 接回新节点
const before = el("old"), after = el("new");
rememberHandle("row:a", before);
rememberHandle("row:a", after);
rememberHandle("row:a", null, before);
g.document.activeElement = body;
keepFocusOn("row:a", { key: " " } as any);
tick();
assert.deepEqual(focused, ["new"]);
// 人自己把焦点挪去了别处：不抢
g.document.activeElement = el("composer");
tick();
assert.deepEqual(focused, ["new"]);
// 这一行不在眼前（看不见）：不抢，焦点留在原处
rememberHandle("row:b", el("hidden", { getClientRects: () => [] }));
keepFocusOn("row:b", { key: "Enter" } as any);
g.document.activeElement = body;
tick();
assert.deepEqual(focused, ["new"]);
// 鼠标、手指拿起的不管
focused.length = 0;
keepFocusOn("row:a", { clientX: 1 } as any);
g.document.activeElement = body;
tick();
assert.deepEqual(focused, []);
// 点了别处（pointerdown）以后不再接回
keepFocusOn("row:a", { key: " " } as any);
presses.at(-1)!();
tick();
assert.deepEqual(focused, []);
""")

    def test_the_float_steps_aside_over_a_folded_group_head(self):
        # 第 6 项：手机把对话拖到收着的组头上，浮层半透明（0.5）后浮层的字与组头的字叠在一起。现在浮层的字、条数隐去，只剩很淡的
        # 一层底（0.15），组头的字与高亮清楚可读；共用拖动一条规则（卡片、组头、置顶、对话都走它）
        css = read("globals.css")
        self.assertIn("--cc-drag-peek: 0.15;", css)
        # 第 8 轮审查：让开只在拖动中——取消（data-dropping=""，滑回原位）时字与条数回来，不是空白的底滑回去；落进收着的组
        # （"into"）时字仍隐着、底随落位淡掉（into 那条 opacity: 0 不被让开那条盖过）
        self.assertIn(".cc-drag-overlay:not([data-dropping]) .cc-drag-float[data-peek] .cc-drag-lift { opacity: var(--cc-drag-peek); }\n"
                      ".cc-drag-lift > * { transition: opacity var(--cc-dur-fast) var(--cc-ease); }\n"
                      ".cc-drag-overlay:not([data-dropping=\"\"]) .cc-drag-float[data-peek] .cc-drag-lift > * { opacity: 0; }", css)
        self.assertNotIn("\n.cc-drag-float[data-peek]", css)
        self.assertIn("@media (prefers-reduced-motion: reduce) { .cc-drag-lift, .cc-drag-lift > * { animation: none; transition: none; } }", css)
        overlay = read("components/ui/drag.tsx").split("function liftOverlay(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('if ((data.count ?? 0) > 1) {\n    const count = document.createElement("span");\n    count.className = "cc-drag-count";', overlay)

    def test_a_repeated_preview_start_reuses_its_part_and_a_retry_drops_half_lines(self):
        # 第 4 项「การ」残句：同一条回复的预览从头再推一遍（推送线程重接）时，原来那段接着用、清空重写，不另开一段留下前几个字；
        # 出错重试（status_rescheduled）时还没写完的预览不会再有正式事件收口，清掉
        self.run_page(r"""
import { applyEvent, project } from "@/lib/managed-agents/reducer";
const at = (s: number) => `2026-10-16T10:00:${String(s).padStart(2, "0")}Z`;
const texts = (state: any) => state.messages.flatMap((m: any) => (m.role === "assistant" ? m.content : [])).filter((p: any) => p.type === "text").map((p: any) => p.text);
const start = (id: string) => ({ type: "event_start", event: { id, type: "agent.message" } }) as any;
const delta = (id: string, text: string) => ({ type: "event_delta", event_id: id, delta: { type: "content_delta", content: { type: "text", text } } }) as any;
let s = project([{ id: "u1", type: "user.message", processed_at: at(1), content: [{ type: "text", text: "hi" }] }] as any);
s = applyEvent(s, start("m1"));
s = applyEvent(s, delta("m1", "การ"));
s = applyEvent(s, start("m1"));                 // 重接后从头再来
s = applyEvent(s, delta("m1", "การบันทึก"));
s = applyEvent(s, delta("m1", "ไม่สำเร็จ"));
s = applyEvent(s, { id: "m1", type: "agent.message", processed_at: at(3), content: [{ type: "text", text: "การบันทึกไม่สำเร็จ" }] } as any);
assert.deepEqual(texts(s).filter(Boolean), ["การบันทึกไม่สำเร็จ"]);
// 出错重试：前一次的半句清掉，重试的那条照常
s = applyEvent(s, start("m2"));
s = applyEvent(s, delta("m2", "การ"));
s = applyEvent(s, { id: "rs1", type: "session.status_rescheduled", processed_at: at(4) } as any);
assert.equal(s.previews.size, 0);
s = applyEvent(s, start("m3"));
s = applyEvent(s, { id: "m3", type: "agent.message", processed_at: at(6), content: [{ type: "text", text: "ok" }] } as any);
assert.deepEqual(texts(s).filter(Boolean), ["การบันทึกไม่สำเร็จ", "ok"]);
assert.equal(s.isRunning, true);
// 第 8 轮审查：span 先结束（补「 …」收口）、rescheduled 后到——半句同样清掉
s = applyEvent(s, start("m4"));
s = applyEvent(s, delta("m4", "การ"));
s = applyEvent(s, { id: "sp1", type: "span.model_request_end", is_error: true, processed_at: at(7) } as any);
assert.deepEqual(texts(s).filter(Boolean), ["การบันทึกไม่สำเร็จ", "ok", "การ …"]);
s = applyEvent(s, { id: "rs2", type: "session.status_rescheduled", processed_at: at(8) } as any);
assert.deepEqual(texts(s).filter(Boolean), ["การบันทึกไม่สำเร็จ", "ok"]);
assert.equal(s.cutPreviews.length, 0);
// 出错后不再重试（本轮结束）：「 …」留着，说明这句没说完
s = applyEvent(s, start("m5"));
s = applyEvent(s, delta("m5", "ยัง"));
s = applyEvent(s, { id: "sp2", type: "span.model_request_end", is_error: true, processed_at: at(9) } as any);
s = applyEvent(s, { id: "id1", type: "session.status_idle", processed_at: at(10), stop_reason: { type: "end_turn" } } as any);
assert.deepEqual(texts(s).filter(Boolean), ["การบันทึกไม่สำเร็จ", "ok", "ยัง …"]);
assert.equal(s.cutPreviews.length, 0);
""")

    def test_company_detail_retries_a_passing_failure_before_showing_an_error(self):
        # 第 5 项：手机第一次打开公司信息报「出了点问题」，关掉重开就好。同时发出的还有月结、待处理与排列同步；「出了点问题」只在
        # 不是本站拒收时出现（网络断开、网关错误、数据库死锁 1213 / 等锁超时 1205——10-15 生产公司页出现过）。现在公司详情碰到
        # 这类一时的失败隔一会儿再取（最多 3 次），再取时先收起上一次的报错；本站拒收（带原文）照常马上显示
        panel = read("lib/company-panel.ts")
        self.assertIn("const next = await withRetry(() => loadDetail(company, period));", panel)
        self.assertIn('if (useDetails.getState()[key]?.error) useDetails.setState((before) => ({ [key]: { detail: before[key]?.detail ?? null, error: "" } }));', panel)
        self.run_page(r"""
import { RequestError, SiteError, transientError, withRetry } from "@/lib/frappe";
assert.equal(transientError(new TypeError("Failed to fetch")), true);
assert.equal(transientError(new RequestError("request failed (502)", 502)), true);
assert.equal(transientError(new RequestError("(1213, 'Deadlock found when trying to get lock')", 417)), true);
assert.equal(transientError(new RequestError("Lock wait timeout exceeded", 417)), true);
assert.equal(transientError(new RequestError("request failed (404)", 404)), false);
assert.equal(transientError(new SiteError("ไม่มีสิทธิ์")), false);
let calls = 0;
const flaky = async () => { calls++; if (calls < 3) throw new TypeError("Failed to fetch"); return "detail"; };
assert.equal(await withRetry(flaky, 3, 1), "detail");
assert.equal(calls, 3);
calls = 0;
await assert.rejects(withRetry(async () => { calls++; throw new SiteError("ไม่มีสิทธิ์"); }, 3, 1), SiteError);
assert.equal(calls, 1);                           // 本站拒收不重试
calls = 0;
await assert.rejects(withRetry(async () => { calls++; throw new RequestError("request failed (503)", 503); }, 3, 1), RequestError);
assert.equal(calls, 3);                           // 最多 3 次
// 站点回了错误码、没有原文：带上状态
g.respond = async () => ({ ok: false, status: 502, json: async () => null });
const { get } = await import("@/lib/frappe");
await assert.rejects(get("layout"), (error: any) => error instanceof RequestError && error.status === 502);
""".replace("g.respond", "(globalThis as any).respond"))


PREFS_STUB = r"""
const g = globalThis as any;
g.prefChanges = [] as Array<{ name: string; fields: string[]; tidy: boolean }>;
g.prefParts = new Map<string, any>();
export function registerPref(name: string, part: any) { g.prefParts.set(name, part); }
export function prefChanged(name: string, fields: readonly string[] = [], tidy = false) { g.prefChanges.push({ name, fields: [...fields], tidy }); return true; }
export const clock = () => performance.now();
export const tidyBasis = (_seen: number) => true;
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class Round10DragTests(unittest.TestCase):
    """测试窗口第 9 轮新报（10-17 并入第 10 轮）：公司组头键盘 Esc、手机松手卡顿；取消置顶回到组顶。第 10 轮审查后改：取消照
    同一道先滑回（鼠标、手机也是）、滑回途中排序项不写让位位移、真没挪过的放下当场结束；取消置顶由取消这件事本身记下。"""

    def run_page(self, body, lang="en", stubs=None):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + body
               + "\n(globalThis as any).done();\n", lang=lang, stubs=stubs)

    def test_release_at_the_target_and_cancel_land_before_the_redraw(self):
        # 手机把公司组头拖过列表首尾：浮层被限位停在的正是落点，原来 landOverlay 判「已在落点」不播、当场结束拖动，松手那一帧
        # 重画整列（无界面 Chrome 6 倍节流复现 87–99ms，手机 102 / 122ms）。现在被限位（浮层有位移）照样播，等落定再结束；真的
        # 没挪过（浮层位移为 0：原地放下、长按出菜单后松手）当场结束（审查：焦点、播报、手机外壳的边缘手势不为它晚 0.2 秒）。
        # 取消：原来由拖动库在取消那次提交里滑回，Esc 那一帧重画整列；现在三种传感器同一道先滑回（settleBack），取消等滑回播完
        drag = read("components/ui/drag.tsx")
        land = drag.split("export function landOverlay(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("if (!always && !fold && !transform.x && !transform.y && frames[0]!.transform === frames[1]!.transform) return null;", land)
        self.assertIn("return landOverlay(overlay, node, transition, into, true);", drag)
        self.assertIn("...(beforeCancel || release ? { onCancel: cancel } : {}),", drag)
        self.assertIn("if (beforeCancel) beforeCancel(props.onCancel);\n        else props.onCancel();", drag)
        self.assertIn("mouse: { activationConstraint: MOUSE_START, beforeEnd, beforeCancel },", drag)
        self.assertIn("touch: { ...TOUCH_START, beforeEnd, beforeCancel },", drag)
        self.assertEqual(drag.count("beforeEnd, beforeCancel },"), 3)
        # 只在那一项还在拿起时那一列里时先滑回（指针拖进了另一列：先挪回原列再滑回，见 Round10BTests）；滑回途中排序项不写让位位移
        self.assertIn("const home = !!node?.isConnected && !!from.list && this.items.get(d.id)?.list === from.list;", drag)
        self.assertIn("const animation = home && node ? settleBack(d.overlay, node, this.transition, homeInto) : null;", drag)
        shifts = drag.split("  private shifts() {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("if (!d || d.settle) return;", shifts)   # 滑回途中（settle）不写位移
        self.assertIn("if (d.cancelling || d.released) return;", drag.split("  private move(at: Coordinates) {", 1)[1].split("\n  }\n", 1)[0])
        self.assertIn('return { transform: settling || (!x && !y) ? "" : `translate3d(${x}px, ${y}px, 0px)`, transition: transition ?? "" };', drag)
        # 文件头不再把约 1 秒的空帧定性成「那里出帧的问题」（审查：没坐实）
        self.assertNotIn("是那里出帧的问题", drag)
        self.run_page(r"""
import { landOverlay, settleBack } from "@/components/ui/drag";
const g = globalThis as any;
g.getComputedStyle = (el: any) => ({ transform: el.computed ?? "none", transformOrigin: "0px 0px" });
Object.defineProperty(g, "performance", { value: { now: () => 7000 }, configurable: true });
const box = (top: number, extra: any = {}) => ({ dataset: {} as Record<string, string>, style: {} as Record<string, string>, isConnected: true,
  getBoundingClientRect: () => ({ top, left: 10, width: 200, height: 40, right: 210, bottom: top + 40 }), ...extra });
const animations: any[] = [];
const animate = (frames: any, opts: any) => {
  const a: any = { frames, opts, startTime: null, playState: "running", finished: new Promise(() => undefined) };
  animations.push(a);
  return a;
};
const motion = { duration: 200, easing: "ease" };
// 被限位停在落点上（拖过最上面一家：浮层上移了 200、正停在第一位）：照样播，位移为 0
const clamped = box(100, { computed: "matrix(1, 0, 0, 1, 0, -200)", children: { length: 1 }, firstElementChild: box(100), animate });
const c = landOverlay(clamped as any, box(100) as any, motion, () => null)!;
assert.equal(c.frames[0].transform, c.frames[1].transform);
assert.equal(c.opts.duration, 200);
// 真的没挪过（浮层位移为 0、就在原位）：不播，当场结束
const still = box(300, { computed: "matrix(1, 0, 0, 1, 0, 0)", children: { length: 1 }, firstElementChild: box(300), animate });
const count = animations.length;
assert.equal(landOverlay(still as any, box(300) as any, motion, () => null), null);
assert.equal(animations.length, count);
// 没挪过、但落点不在可见范围里（落到边上并收起）：照样播
assert.ok(landOverlay(still as any, box(300) as any, motion, () => ({ top: 500, left: 10 })));

// 取消：公司组 A 拿起后按 ↓：A（空档）让位到下面（+120），B 让位到上面（-200）；另一列（某组里的对话）的位移不归这次
const a = box(100, { dataset: { dragList: "companies" }, style: { transform: "translate3d(0px, 120px, 0)" } });
const b = box(300, { dataset: { dragList: "companies" }, style: { transform: "translate3d(0px, -200px, 0)" } });
const other = box(500, { dataset: { dragList: "rows:CO-1" }, style: { transform: "translate3d(0px, 34px, 0)" } });
g.document.querySelectorAll = (selector: string) => (selector === "[data-drag-list]" ? [a, b, other] : []);
const overlay = box(220, { computed: "matrix(1, 0, 0, 1, 0, 120)", children: { length: 1 }, firstElementChild: box(220), animate });
const back = settleBack(overlay as any, a as any, motion, () => null)!;
// 这一列各项去掉让位位移（行内过渡照旧带它们滑回）；别的列不动
assert.deepEqual([a.style.transform, b.style.transform, other.style.transform], ["", "", "translate3d(0px, 34px, 0)"]);
// 浮层滑回 A 的原位（版面位置，不再加让位位移）：120 - (220 - 100) = 0；从 Esc 这一刻起算，略放大、阴影收回
assert.equal(back.frames[0].transform, "translate3d(0px, 120px, 0) scaleX(1) scaleY(1)");
assert.equal(back.frames[1].transform, "translate3d(0px, 0px, 0) scaleX(1) scaleY(1)");
assert.equal(back.startTime, 7000);
assert.equal(overlay.dataset.dropping, "");
// 拿起就按 Esc（没挪过）：取消照样播一段收回略放大、阴影（取消那次提交在这之后）
const home = box(220, { computed: "matrix(1, 0, 0, 1, 0, 0)", children: { length: 1 }, firstElementChild: box(220), animate });
const idle = settleBack(home as any, box(220, { dataset: { dragList: "companies" } }) as any, motion, () => null)!;
assert.equal(idle.frames[0].transform, idle.frames[1].transform);
// 减少动态效果：不播，调用方当场取消
assert.equal(settleBack(overlay as any, a as any, null, () => null), null);
""")

    def test_cancel_waits_for_the_slide_back_in_every_sensor(self):
        # 审查：键盘取消推迟原来只有源码断言。这里真跑拖动库的键盘传感器（包了一层 landFirst）：Esc 那一下只交给 beforeCancel，
        # 原来的 onCancel 不同步调，滑回播完（beforeCancel 交回的回调）才调。手机：拿起后 touchcancel / 转屏同样先交 beforeCancel；
        # 点按（拿起后没动就松手）当场取消、补点击。先落位再结束（ReleaseFirst）：滑回播完才取消，期间又按下（要开始下一次）当场取消
        self.run_page(r"""
import { HoldTouchSensor, LandingKeyboardSensor, ReleaseFirst } from "@/components/ui/drag";
const g = globalThis as any;
const docOn: Record<string, (e: any) => void> = {};
const winOn = new Map<string, Set<(e: any) => void>>();
g.document.addEventListener = (type: string, run: any) => (docOn[type] = run);
g.document.removeEventListener = (type: string) => delete docOn[type];
g.document.querySelector = () => null;
g.addEventListener = (type: string, run: any) => { if (!winOn.has(type)) winOn.set(type, new Set()); winOn.get(type)!.add(run); };
g.removeEventListener = (type: string, run: any) => winOn.get(type)?.delete(run);
g.getSelection = () => ({ removeAllRanges() {} });
g.KeyboardEvent = class { code: string; key: string; target = null; constructor(code: string) { this.code = code; this.key = code; } preventDefault() {} };
g.MouseEvent = class { type: string; init: any; constructor(type: string, init: any) { this.type = type; this.init = init; } };

// 键盘：空格拿起、Esc
const log: string[] = [];
let back: (() => void) | null = null;
new (LandingKeyboardSensor as any)({
  active: "company:A", activeNode: { node: { current: null } }, event: { target: null }, context: { current: {} },
  options: { keyboardCodes: { start: ["Space"], cancel: ["Escape"], end: ["Space", "Enter"] },
             beforeEnd: (end: () => void) => end(), beforeCancel: (cancel: () => void) => { log.push("slide back"); back = cancel; } },
  onStart: () => log.push("start"), onMove: () => log.push("move"), onEnd: () => log.push("end"), onCancel: () => log.push("cancel"),
  onAbort: () => log.push("abort"), onPending: () => {},
});
await g.sleep(5);                                       // 拖动库在下一个任务里才收 keydown
docOn.keydown!(new g.KeyboardEvent("Escape"));
assert.deepEqual(log, ["start", "slide back"]);         // 原来的 onCancel 没有在 Esc 这一下调
back!();
assert.deepEqual(log, ["start", "slide back", "cancel"]);

// 手机：拿起后 touchcancel → 先滑回；点按 → 当场取消、补点击
function press(options: any = {}) {
  const on: Record<string, (e: any) => void> = {};
  const out: string[] = [];
  const target = { isConnected: true, addEventListener: (t: string, f: any) => (on[t] = f), removeEventListener: (t: string) => delete on[t],
                   dispatchEvent: (e: any) => { out.push(e.type); return true; } };
  new HoldTouchSensor({
    active: "row:t1", event: { target, touches: [{ clientX: 100, clientY: 100 }] }, options: { delay: 250, tolerance: 5, ...options },
    onStart: () => out.push("start"), onMove: () => out.push("move"), onEnd: () => out.push("end"), onCancel: () => out.push("cancel"),
    onAbort: () => out.push("abort"), onPending: () => {},
  } as any);
  return { on, out };
}
let held: (() => void) | null = null;
const slide = { beforeCancel: (cancel: () => void) => { held = cancel; } };
let p = press(slide);
await g.sleep(280);
p.on.touchmove!({ touches: [{ clientX: 100, clientY: 140 }], cancelable: true, preventDefault() {} });
p.on.touchcancel!({});
assert.deepEqual(p.out, ["start", "move"]);
held!();
assert.deepEqual(p.out, ["start", "move", "cancel"]);
held = null;
p = press(slide);
await g.sleep(280);
p.on.touchend!({ cancelable: true, preventDefault() {} });   // 点按：不经 beforeCancel
assert.deepEqual(p.out, ["start", "cancel", "click"]);
assert.equal(held, null);
p = press(slide);
await g.sleep(50);
p.on.touchmove!({ touches: [{ clientX: 100, clientY: 130 }], cancelable: true, preventDefault() {} });   // 没拿起就动：在滚动
assert.deepEqual(p.out, ["abort", "cancel"]);
assert.equal(held, null);

// 先滑回再取消：动画播完才调；期间按下键（要开始下一次）当场调
const animation = () => { let done: () => void = () => undefined; const a: any = { finished: new Promise<void>((r) => (done = r)) }; a.finish = () => done(); return a; };
const letGo = new ReleaseFirst();
const seen: string[] = [];
const one = animation();
letGo.hold(() => seen.push("cancel 1"), one, 1000);
assert.deepEqual(seen, []);
one.finish();
await g.sleep(0);
assert.deepEqual(seen, ["cancel 1"]);
letGo.hold(() => seen.push("cancel 2"), animation(), 1000);
for (const run of winOn.get("keydown") ?? []) run({});
assert.deepEqual(seen, ["cancel 1", "cancel 2"]);
""")

    def test_an_unpinned_chat_returns_to_the_top_of_its_group(self):
        # 取消置顶（行菜单、P 键、手机长按菜单、多选、标题栏菜单、别的设备）：这段对话回到它那一组的最上面。原来手排过的组里它
        # 不在手排列表里、又比存下时旧，排到手排的后面，组里超过 5 条时被「显示更多」挡住。审查后：由取消置顶这件事本身记下
        # （不靠「上一次渲染是置顶、这次不是」猜：置顶失败撤回乐观更新不算）；只插到手排列表最前面、记成组顶那一段（top），别的行
        # 不写进手排，比放上去那一刻还新的对话照常排在它上面；各种分组方式、搜索中同一种做法
        self.run_page(STORAGE_STUB + r"""
import { applyLifts, bindOrderUser, forgetInGroups, LIFT_TTL, noteUnpinned, orderKey, setGroupOrder, starChat, unpinChat, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import { arrangeGroup, companySections } from "@/lib/managed-agents/sidebar-groups";
const g = globalThis as any;
let clock = 1000;
Object.defineProperty(g, "performance", { value: { now: () => clock }, configurable: true });
bindOrderUser("skin3@x");
const co = [{ id: "CO-2", title: "B", last_active: "2026-10-17T00:00:00Z" }];
const row = (index: number, key: string, time: number) => ({ index, title: key, starred: false, time, group: "CO-2", key });
let rows = [row(0, "s9", 900), row(1, "s10", 890), row(2, "s11", 880), row(3, "s12", 870), row(4, "s13", 800), row(5, "s14", 790), row(6, "s20", 950)];
const shown = () => {
  const section: any = companySections(rows, co as any, { groupOrders: useSidebarOrder.getState().groups })!.find((x: any) => x.type === "project");
  return section.indices.map((i: number) => rows[i]!.key);
};
const spot = (stamp: number) => (key: string) => (rows.some((r) => r.key === key) ? { group: "CO-2", stamp } : null);
const fresh = () => {
  useSidebarOrder.setState({ companies: [], pinned: [], groups: {} }, true);
  setGroupOrder("CO-2", ["s10", "s9", "s11", "s12", "s14"], 900, ["s10"]);
  g.prefChanges.length = 0;
};
fresh();
// 修前：s13（旧对话、不在手排里）排在手排的后面，被「显示更多」挡住；s20 比存下时新，排最上面
assert.deepEqual(shown(), ["s20", "s10", "s9", "s11", "s12"]);

// 本机取消置顶：请求发出就先挪（只改本机），服务端确认后才存、同步
let answer: (ok: boolean) => void = () => undefined;
const request = () => new Promise<void>((resolve, reject) => (answer = (ok) => (ok ? resolve() : reject(new Error("offline")))));
let done = unpinChat("s13", request);
applyLifts(() => "starred");                            // 组件库还没换成没置顶：等着
assert.deepEqual(shown(), ["s20", "s10", "s9", "s11", "s12"]);
applyLifts(spot(950));
assert.deepEqual(useSidebarOrder.getState().groups["CO-2"], { keys: ["s13", "s10", "s9", "s11", "s12", "s14"], at: 900, top: { n: 1, at: 950 } });
assert.deepEqual(shown(), ["s13", "s20", "s10", "s9", "s11"]);   // 在组顶；s20 没被写进手排、紧跟在它下面
assert.deepEqual(g.prefChanges, []);                    // 还没存、没同步
assert.equal(JSON.parse(storage.get(orderKey("skin3@x"))!).groups["CO-2"].keys[0], "s13");   // 本机缓存照写（刷新后照样）
answer(true);
await done;
assert.deepEqual(g.prefChanges, [{ name: "order", fields: ["group:CO-2"], tidy: false }]);
// 之后才有动静的对话照常排在它上面
rows = [...rows, row(7, "s30", 1000)];
assert.deepEqual(shown(), ["s30", "s13", "s20", "s10", "s9"]);
rows = rows.filter((r) => r.key !== "s30");
// 再在这一组拖一下：拖完眼前的先后整份记下，组顶那一段不再单算
setGroupOrder("CO-2", ["s20", "s13", "s10"], 960, ["s20"]);
assert.equal(useSidebarOrder.getState().groups["CO-2"]!.top, undefined);

// 请求失败（离线、服务端出错，组件库撤回置顶）：撤回这次挪动，不存
fresh();
let before = useSidebarOrder.getState().groups["CO-2"];
done = unpinChat("s13", request);
applyLifts(spot(950));
assert.equal(useSidebarOrder.getState().groups["CO-2"]!.keys[0], "s13");
answer(false);
await assert.rejects(done);
assert.equal(useSidebarOrder.getState().groups["CO-2"], before);
assert.deepEqual(g.prefChanges, []);
// 组件库先撤回（侧栏先看到又是置顶）：同样撤回
fresh();
before = useSidebarOrder.getState().groups["CO-2"];
done = unpinChat("s13", request);
applyLifts(spot(950));
applyLifts(() => "starred");
assert.equal(useSidebarOrder.getState().groups["CO-2"], before);
answer(false);
await assert.rejects(done);
assert.equal(useSidebarOrder.getState().groups["CO-2"], before);
// 置顶失败（乐观地先置顶、失败撤回成没置顶）：不是取消置顶，不挪、不存
fresh();
before = useSidebarOrder.getState().groups["CO-2"];
const item = { getState: () => ({ id: "local-s13", remoteId: "s13", custom: { starred: false, mark: 1 } }), updateCustom: async (custom: any) => { g.sent = custom; throw new Error("offline"); } };
await assert.rejects(starChat(item, true));
assert.deepEqual(g.sent, { starred: true, mark: 1 });   // 展开写法：别的键不冲掉
applyLifts(spot(950));
assert.equal(useSidebarOrder.getState().groups["CO-2"], before);
assert.deepEqual(g.prefChanges, []);

// 多选一起取消（按侧栏里原来的先后，后一条排在同批先回来的后面）：一次处理两条也照这个先后（审查：原来同一次只剩最后一条在组顶）
fresh();
const ok = () => Promise.resolve();
const both = [unpinChat("s14", ok, ["s13"]), unpinChat("s13", ok)];   // 先记下的后处理也一样
applyLifts(spot(950));
await Promise.all(both);
assert.deepEqual(useSidebarOrder.getState().groups["CO-2"], { keys: ["s13", "s14", "s10", "s9", "s11", "s12"], at: 900, top: { n: 2, at: 950 } });
assert.deepEqual(shown(), ["s13", "s14", "s20", "s10", "s9"]);
// 组顶那一段里的一条又置顶了（从组里去掉）：那一段少一条
forgetInGroups(["s13"], true);
assert.deepEqual(useSidebarOrder.getState().groups["CO-2"]!.top, { n: 1, at: 950 });

// 别的设备上取消的：只改本机显示、不存不同步（第 10 轮 B 段审查：以发起那一台存的为准）；服务端那份换进来时还没有它（那一台
// 还没存上）就再挪一次，有了才算收拾完
fresh();
const old = { keys: ["s10", "s9", "s11", "s12", "s14"], at: 900 };
noteUnpinned("s13", false);
applyLifts(spot(950));
assert.equal(useSidebarOrder.getState().groups["CO-2"]!.keys[0], "s13");
assert.deepEqual(g.prefChanges, []);
g.prefParts.get("order").apply({ companies: [], pinned: [], "group:CO-2": old }, "skin3@x");   // 先取到的是旧的那份
assert.equal(useSidebarOrder.getState().groups["CO-2"]!.keys[0], "s10");
applyLifts(spot(950));                                  // 侧栏随顺序变化再收拾一遍：又回到组顶
assert.deepEqual(shown().slice(0, 2), ["s13", "s20"]);
const theirs = { keys: ["s13", "s10", "s9", "s11", "s12", "s14"], at: 900, top: { n: 1, at: 950 } };
g.prefParts.get("order").apply({ companies: [], pinned: [], "group:CO-2": theirs }, "skin3@x");   // 那一台存上了
g.prefParts.get("order").apply({ companies: [], pinned: [], "group:CO-2": old }, "skin3@x");
applyLifts(spot(950));                                  // 收拾完了：不再挪
assert.equal(useSidebarOrder.getState().groups["CO-2"]!.keys[0], "s10");
// 别的设备上取消的回到组顶以后、人又在这一组把它拖到别处：不再挪回去（人排好的为准），服务端那份换进来时还没有它也一样
fresh();
noteUnpinned("s13", false);
applyLifts(spot(950));
setGroupOrder("CO-2", ["s10", "s13", "s20"], 960, ["s13"]);
g.prefParts.get("order").apply({ companies: [], pinned: [], "group:CO-2": old }, "skin3@x");
applyLifts(spot(950));
assert.equal(useSidebarOrder.getState().groups["CO-2"]!.keys[0], "s10");
// 记号有时效：一直没出现在侧栏的（归档的、别的产品的）过时就丢
noteUnpinned("s99", false);
clock += LIFT_TTL + 1;
rows = [...rows, row(8, "s99", 700)];
applyLifts(spot(950));
assert.equal(useSidebarOrder.getState().groups["CO-2"]!.keys.includes("s99"), false);
// 那一组没手排过：照排序方式，不写手排
noteUnpinned("s5", true);
applyLifts(() => ({ group: "CO-9", stamp: 10 }));
assert.equal("CO-9" in useSidebarOrder.getState().groups, false);

// 组顶那一段：比 at 新、不比 top.at 新的排在它下面；比 top.at 新的排在它上面；本机缓存与同步都带上 top
const r = (key: string, time: number, index: number) => ({ index, title: key, starred: false, time, group: "CO-2", key });
const arranged = arrangeGroup([r("n1", 990, 0), r("f1", 940, 1), r("k2", 880, 2), r("k1", 800, 3), r("o1", 700, 4)].sort((a, b) => b.time - a.time) as any,
  { keys: ["k1", "k2"], at: 900, top: { n: 1, at: 950 } }).map((x: any) => x.key);
assert.deepEqual(arranged, ["n1", "k1", "f1", "k2", "o1"]);
const saved = JSON.parse(storage.get(orderKey("skin3@x"))!);
assert.ok(Object.values(saved.groups).every((group: any) => !("top" in group) || (group.top.n > 0 && typeof group.top.at === "number")));
assert.deepEqual(g.prefParts.get("order").read()["group:CO-2"].keys.length > 0, true);
""", stubs={"@/lib/prefs-sync": PREFS_STUB})
        rows = read("components/assistant-ui/thread-list.tsx")
        groups = rows.split("const CompanyGroups: FC", 1)[1].split("\nconst DayGroups", 1)[0]
        # 侧栏照数据挪，放在「置顶了的从组里手排的先后里去掉」之前（请求失败撤回置顶时先撤回挪动）；不再按置顶状态的变化猜
        lift = groups.index("useEffect(() => applyLifts(spotRef.current), [liftTick, threadIds, itemsById, order.groups, pinning]);")
        self.assertLess(lift, groups.index("if (left.length) forgetInGroups(left, true);"))
        self.assertNotIn("starredBefore", groups)
        self.assertIn("return { group, stamp: groupStamp(peers, { by: view.sort, desc: view.desc }) };", groups)
        # 各入口同一道（starChat）：行菜单与 P 键、多选「取消置顶 N」（按侧栏眼前的先后）、手机胶囊菜单与标题栏菜单
        self.assertIn("const pin = () => toggleStar(aui.threadListItem(), !starred);", rows)
        self.assertIn("await starChat(item, !allPinned, [...back]);", rows)
        self.assertNotIn("updateCustom({ ...custom, starred: !", rows)
        shell = read("assistant.tsx")
        self.assertEqual(shell.count("onSelect={() => toggleStar(aui.threadListItem(), !starred)}>"), 2)
        self.assertNotIn("starred: !starred", shell)
        # 别的设备上取消的（侧栏同步推来）：只记在侧栏里的（归档的不记）
        sync = read("lib/managed-agents/list-sync.ts")
        self.assertIn('if (!starred && row.remoteId && row.status === "regular") noteUnpinned(row.remoteId, false);', sync)



@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class Round10BTests(unittest.TestCase):
    """测试窗口第 10 轮 B 段：「上次更新」按真的最后活动排、离线取消置顶不跳位、拖进已置顶后 Esc 与拿起那一刻的长帧、手机长按菜单
    点穿。"""

    def run_page(self, body, stubs=None):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + body
               + "\n(globalThis as any).done();\n", stubs=stubs)

    def test_a_failed_pin_or_unpin_leaves_the_manual_order_alone(self):
        # 手机断网取消置顶第 2 位：原来乐观更新那一下就把它从置顶顺序里去掉并同步，失败撤回后成了「新置顶的」排第 1；现在请求没回
        # 之前不收拾（starPending），确认了才收拾；明知断网不先挪走再撤回
        self.run_page(STORAGE_STUB + r"""
import { bindOrderUser, setGroupOrder, setPinnedOrder, starChat, starPending, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
const g = globalThis as any;
Object.defineProperty(g, "performance", { value: { now: () => 1000 }, configurable: true });
bindOrderUser("u@x");
setPinnedOrder(["s1", "s2", "s3"]);
g.prefChanges.length = 0;
let answer: (ok: boolean) => void = () => undefined;
const item = (starred: boolean) => ({
  getState: () => ({ id: "l2", remoteId: "s2", custom: { starred, keep: 1 } }),
  updateCustom: (custom: any) => new Promise<void>((resolve, reject) => {
    g.sent = custom;
    answer = (ok) => (ok ? resolve() : reject(new TypeError("Failed to fetch")));
  }),
});
let pending = starChat(item(true), false);
assert.equal(starPending("s2"), true);                       // 侧栏这段时间不收拾它
assert.deepEqual(useSidebarOrder.getState().pinned, ["s1", "s2", "s3"]);
answer(false);
await assert.rejects(pending);
assert.equal(starPending("s2"), false);
assert.deepEqual(useSidebarOrder.getState().pinned, ["s1", "s2", "s3"]);   // 失败：原样在第 2 位
assert.deepEqual(g.prefChanges.filter((c: any) => c.fields.includes("pinned")), []);   // 也没同步出去
pending = starChat(item(true), false);
answer(true);
await pending;
assert.deepEqual(useSidebarOrder.getState().pinned, ["s1", "s3"]);         // 确认了才去掉（按数据收拾）
assert.ok(g.prefChanges.some((c: any) => c.fields.includes("pinned") && c.tidy));
assert.deepEqual(g.sent, { starred: false, keep: 1 });
// 置顶：确认了才从组里手排的先后里去掉；失败不动
setGroupOrder("CO", ["s2", "s4"], 900, ["s2"]);
pending = starChat(item(false), true);
assert.deepEqual(useSidebarOrder.getState().groups.CO!.keys, ["s2", "s4"]);
answer(false);
await assert.rejects(pending);
assert.deepEqual(useSidebarOrder.getState().groups.CO!.keys, ["s2", "s4"]);
pending = starChat(item(false), true);
answer(true);
await pending;
assert.deepEqual(useSidebarOrder.getState().groups.CO!.keys, ["s4"]);
// 明知断网：不发请求（组件库不会先乐观地挪走再撤回），当场失败
let sent = false;
const offline = { getState: () => ({ id: "l3", remoteId: "s3", custom: { starred: true } }), updateCustom: async () => void (sent = true) };
Object.defineProperty(g, "navigator", { value: { onLine: false }, configurable: true });
await assert.rejects(starChat(offline, false), TypeError);
assert.equal(sent, false);
assert.equal(starPending("s3"), false);
""", stubs={"@/lib/prefs-sync": PREFS_STUB})
        rows = read("components/assistant-ui/thread-list.tsx")
        groups = rows.split("const CompanyGroups: FC", 1)[1].split("\nconst DayGroups", 1)[0]
        tidy = groups.split("const unpinned: string[] = [];", 1)[1].split("}, [threadIds", 1)[0]
        self.assertIn("if (starPending(key)) continue;", tidy)
        self.assertLess(tidy.index("if (starPending(key)) continue;"), tidy.index("if (item.custom?.starred) pinnedNow.push(key);"))
        # 失败写进侧栏出错行（通用句：shownError 只把本站拒收的原文给人看）
        self.assertIn("void starChat(item, starred).catch((error: unknown) => setError(shownError(error)));", rows)

    def test_a_touch_outside_an_open_menu_only_closes_it(self):
        # 手机长按出菜单后点在菜单外的另一条对话上：原来菜单关了、那条对话也打开了（组件库按下就关菜单、撤掉整页的
        # pointer-events: none，手机随后补发的 click 落到下面那一行）。现在非鼠标按下关菜单时吞掉紧跟着的那一次 click
        self.run_page(r"""
import { closeWithoutClickThrough, swallowNextClick } from "@/components/ui/menu";
const g = globalThis as any;
const page = new EventTarget();
g.addEventListener = page.addEventListener.bind(page);
g.removeEventListener = page.removeEventListener.bind(page);
const click = () => {
  const event = new Event("click", { cancelable: true });
  page.dispatchEvent(event);
  return event.defaultPrevented;
};
swallowNextClick(page as any, 800);
assert.equal(click(), true);                            // 关菜单的这一下不落到下面
assert.equal(click(), false);                           // 只吞这一次
swallowNextClick(page as any, 800);
page.dispatchEvent(new Event("pointerdown"));           // 没等到 click（滑成了滚动）就又按下：是新的一下
assert.equal(click(), false);
swallowNextClick(page as any, 20);
await g.sleep(40);                                      // 到时摘掉
assert.equal(click(), false);
const outside = (pointerType: string, prevented = false) =>
  ({ defaultPrevented: prevented, detail: { originalEvent: { pointerType } } }) as any;
closeWithoutClickThrough(outside("mouse"));            // 鼠标：照组件库默认（按下、松开不在同一个元素上，本来就没有 click）
assert.equal(click(), false);
closeWithoutClickThrough(outside("touch", true));       // 调用方拦下、菜单不关：不吞
assert.equal(click(), false);
closeWithoutClickThrough(outside("touch"));
assert.equal(click(), true);
closeWithoutClickThrough(outside("pen"));
assert.equal(click(), true);
""")
        menu = read("components/ui/menu.tsx")
        content = menu.split("\nfunction Content(", 1)[1].split("\nfunction SubContent(", 1)[0]
        self.assertIn("onPointerDownOutside?.(event);\n        closeWithoutClickThrough(event);", content)
        # 各处菜单都从这里取（侧栏行、组头、右侧栏卡片、标题栏）；子菜单不吞（点回父菜单的另一项要照常选中）
        self.assertNotIn("closeWithoutClickThrough", menu.split("\nfunction SubContent(", 1)[1].split("\n}\n", 1)[0])
        for name in ("components/assistant-ui/thread-list.tsx", "components/assistant-ui/projects.tsx",
                     "components/assistant-ui/sidebar-group.tsx", "components/company-cards.tsx"):
            source = read(name)
            if "DropdownMenu" in source:
                self.assertIn('from "@/components/ui/menu"', source, name)

    def test_the_drag_cursor_waits_for_the_lift_frame_to_paint(self):
        # 拿起那一刻：整页 grabbing（第 11 轮 B14 起是鼠标拖动的挡板，原来是根节点标记）一盖，要重算样式；挪到这一帧画完以后。
        # 拖动中各处判断问 isDragging()，不看挡板
        self.run_page(r"""
import { dragShieldShown, isDragging, setDragShield, setDragging } from "@/lib/layers";
const g = globalThis as any;
const page = new EventTarget();
g.addEventListener = page.addEventListener.bind(page);
g.removeEventListener = page.removeEventListener.bind(page);
const shield = { hidden: false } as any;
setDragShield(shield);
assert.equal(shield.hidden, true);                      // 平时藏着
setDragging(true);
assert.equal(isDragging(), true);
assert.equal(shield.hidden, true);                      // 拿起这一帧不盖
await g.sleep(10);
assert.equal(shield.hidden, false);
assert.equal(dragShieldShown(), true);
setDragging(false);
assert.equal(isDragging(), false);
assert.equal(shield.hidden, true);
setDragging(true);
setDragging(false);                                     // 盖之前就结束了：不再盖
await g.sleep(10);
assert.equal(shield.hidden, true);
setDragShield(null);
""")
        css = read("globals.css")
        self.assertIn(".cc-drag-shield { position: fixed; inset: 0; z-index: var(--cc-z-drag-shield); cursor: grabbing; }", css)   # 整页 grabbing 不变
        self.assertNotIn(":root[data-cc-drag]", css)

    def test_escape_after_dragging_into_pins_slides_back_before_the_redraw(self):
        # 对话拖进已置顶后按 Esc：原来由拖动库在 Esc 那一下同步取消、整列重画（Esc 那一帧 148 / 149ms）。现在 Esc 那一下只起播浮层
        # 滑回（按拿起时的位置）、停住碰撞与自动滚动；画完这一帧再请侧栏把它挪回原来那一组（onReturn），重挂出来那次提交里核对落点、
        # 对不上就从此刻改滑；取消等滑回播完
        drag = read("components/ui/drag.tsx")
        cancel = drag.split("  private beforeCancel(cancel: () => void) {", 1)[1].split("\n  }\n", 1)[0]
        cross = cancel.split("if (!home && from.list && this.transition && onReturn) {", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("const slide = slideOverlay(d.overlay, homeBox(d.start, scrolled, node), this.transition);", cross)
        # 已经取消：之后的 Esc 照常（当场放手）；挡板先不撤（"keep"），Esc 那一帧与下一帧挪回原组的整列重画里都不再叠一次侧栏的悬停
        # 重算，起播滑回时再撤（第 11 轮 B14 审查）
        self.assertIn('releaseDrag("keep");', cross)
        self.assertLess(cross.index("slideOverlay("), cross.index('releaseDrag("keep");'))
        self.assertIn("afterPaint(() => this.homing === entry && onReturn(from));", cross)
        self.assertIn("unfreeze: freezeScroll(d.scrollers),", cross)
        self.assertNotIn("letGo.hold(", cross)                 # 这一下不取消
        self.assertIn("d.cancelling = true;", cancel)
        self.assertIn("if (!d || d.cancelling || !d.data.list) return;", drag)                # 碰撞不再算（也就不再经过哪里）
        self.assertIn("if (!d || d.cancelling || d.queued) return;", drag)                    # 不重量落点
        self.assertIn("if (this.drag !== d || d.cancelling || d.released) return;", drag)     # 排到下一帧的那次也不量
        # 挪回原列的那一项重挂出来（登记它的那次提交里）就起播滑回
        register = drag.split("  register(item: Item, data: DragData, list: string | undefined, disabled: boolean) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("if (homing && homing.id === item.id && item.list === homing.list && item.node?.isConnected) {", register)
        self.assertIn("this.homeLanded(homing, item.node);", register)
        self.assertIn("this.letGo.hold(entry.cancel, animation, this.landWait, () => entry.unfreeze);", drag)
        # 「这一项此刻在哪一列」按它真的排在的那一列（拖进置顶重挂的那一行是置顶那一列）
        self.assertIn('"data-drag-list": where,', drag)
        self.assertIn("const where = list ?? drag.list;", drag)
        rows = read("components/assistant-ui/thread-list.tsx")
        self.assertIn("const onReturn = () => {\n    placeGuest(null);\n    setReveal(false);\n  };", rows)
        self.run_page(r"""
import { slideOverlay } from "@/components/ui/drag";
const g = globalThis as any;
g.getComputedStyle = (el: any) => ({ transform: el.computed ?? "none", transformOrigin: "0px 0px" });
Object.defineProperty(g, "performance", { value: { now: () => 5000 }, configurable: true });
const made: any[] = [];
const overlay: any = { dataset: {}, isConnected: true, computed: "matrix(1.02, 0, 0, 1.02, 0, -180)", children: [],
  getBoundingClientRect: () => ({ top: 120, left: 10, width: 200, height: 32, right: 210, bottom: 152 }),
  animate: (frames: any, opts: any) => { const a: any = { frames, opts, startTime: null }; made.push(a); return a; } };
const anim = slideOverlay(overlay, { top: 300, left: 10 }, { duration: 200, easing: "ease" });
assert.ok(anim);
assert.equal(made.length, 1);
assert.equal(anim.startTime, 5000);                     // 从 Esc 这一刻起算
assert.equal(overlay.dataset.dropping, "");             // 略放大、阴影同时收回
assert.match(made[0].frames[1].transform, /translate3d\(0px, 0px, 0\) scaleX\(1\) scaleY\(1\)/);   // 落到 300（此刻 120 处、位移 -180）
assert.equal(slideOverlay(overlay, { top: 300, left: 10 }, null), null);   // 减少动态效果：不播
""")

    def test_lifting_does_not_redraw_the_whole_list(self):
        # 拿起那一刻（测试窗口 160–186ms）：拖动库重画所有能拖的行与组头。行里的字、行菜单、组头、「···」「新建」记住；拖动的
        # 位移与把手按内容记住（没变就是同一份）；侧栏不把「拖的是什么」记成状态；主题色观察器不在每次增删时读计算样式
        drag = read("components/ui/drag.tsx")
        item = drag.split("export function useSortableItem(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("const node = useMemo(", item)
        self.assertIn("const handle = useMemo(", item)
        self.assertIn("return useMemo(() => ({ moved, node, handle, dragging: isDragging }), [moved, node, handle, isDragging]);", item)
        rows = read("components/assistant-ui/thread-list.tsx")
        for memo in ("const Row = memo(function Row(", "const RowLabel = memo(function RowLabel()",
                     "const ThreadListItemMore = memo(function ThreadListItemMore("):
            self.assertIn(memo, rows)
        self.assertIn("<RowLabel />", rows)
        self.assertIn("onRename={startRename}", rows)
        self.assertIn("<RowScopeContext.Provider value={pinScope}>", rows)
        self.assertIn("export const ProjectHeader = memo(function ProjectHeader(", read("components/assistant-ui/projects.tsx"))
        group = read("components/assistant-ui/sidebar-group.tsx")
        self.assertIn("export const GroupNew = memo(function GroupNew(", group)
        self.assertIn("export const GroupSortMenu = memo(function GroupSortMenu(", group)
        self.assertIn("export const Marquee = memo(function Marquee(", read("components/ui/marquee.tsx"))
        main = read("main.tsx")
        self.assertIn("if (!force && key === painted) return;", main)
        self.assertIn("new MutationObserver(() => themeColor()).observe(document.body", main)

    def test_messages_float_a_chat_like_its_last_update(self):
        # 「上次更新」：服务端在用户发出消息、CC 回复时记最后活动时间（列表 updated_at）；本页推来的新消息当场浮顶，不等重拉
        activity = read("lib/managed-agents/activity.ts")
        self.assertIn('const MESSAGES = new Set(["user.message", "agent.message"]);', activity)
        self.assertIn("if (type && MESSAGES.has(type)) {\n    bump(sessionId, (event as { processed_at?: string | null }).processed_at);", activity)
        self.assertIn("bump(sessionId, notice.processed_at);", activity)
        self.assertIn("「上次更新」按它排", read("components/assistant-ui/thread-list.tsx"))


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class Round11Tests(unittest.TestCase):
    """第 10 轮 B 段审查（10-17）：手排过的组里有新动静照常浮顶、核对列表补上漏收的动静、没加载的对话来了消息重拉、别的设备上
    取消置顶只改本机显示且同批按原先后、取消置顶后置顶顺序同步不被冲掉、浮顶不用本机时钟、长按菜单不按模态开、整页样式重算的
    两处 CSS、传感器选项记住、主题色读到了才记、不按公司分组的侧栏也显示出错行。"""

    def run_page(self, body, stubs=None):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + body
               + "\n(globalThis as any).done();\n", stubs=stubs)

    def test_new_activity_floats_rows_of_a_hand_ordered_group(self):
        # 拖过一次以后组里已加载的都记进手排；原来它们有新消息也不再浮顶（审查）。现在按「上次更新」排时，存下（放上组顶）以后
        # 又有动静的照新出现的算；创建日期、名称排时不因新消息挪
        self.run_page(r"""
import { arrangeGroup } from "@/lib/managed-agents/sidebar-groups";
const r = (key: string, time: number, index: number, extra: any = {}) => ({ index, title: key, starred: false, time, group: "A", key, ...extra });
const sorted = (rows: any[]) => [...rows].sort((a, b) => b.time - a.time);
const saved = { keys: ["k1", "k2", "k3"], at: 900 };
// 没有新动静：照手排
assert.deepEqual(arrangeGroup(sorted([r("k1", 800, 0), r("k2", 900, 1), r("k3", 700, 2)]) as any, saved).map((x: any) => x.key), ["k1", "k2", "k3"]);
// k3 存下以后有了新消息（上次更新 950 > at 900）：浮到最上面，其余照手排
assert.deepEqual(arrangeGroup(sorted([r("k1", 800, 0), r("k2", 900, 1), r("k3", 950, 2)]) as any, saved).map((x: any) => x.key), ["k3", "k1", "k2"]);
// 和新对话一样按时间排在新对话之间
assert.deepEqual(arrangeGroup(sorted([r("n1", 990, 3), r("k1", 800, 0), r("k2", 900, 1), r("k3", 950, 2)]) as any, saved).map((x: any) => x.key), ["n1", "k3", "k1", "k2"]);
// 组顶那一段（取消置顶回来的 t1，放上去那一刻 980）：比 at 新、不比 top.at 新的动静排在它下面；比 top.at 新的排在它上面
const lifted = { keys: ["t1", "k1", "k2"], at: 900, top: { n: 1, at: 980 } };
const rows = (k2: number, t1 = 850) => sorted([r("t1", t1, 0), r("k1", 800, 1), r("k2", k2, 2)]) as any;
assert.deepEqual(arrangeGroup(rows(950), lifted).map((x: any) => x.key), ["t1", "k2", "k1"]);
assert.deepEqual(arrangeGroup(rows(990), lifted).map((x: any) => x.key), ["k2", "t1", "k1"]);
// 组顶那一段里的它自己之后又有动静（比 top.at 新）：照样在最上面
assert.deepEqual(arrangeGroup(rows(800, 995), lifted).map((x: any) => x.key), ["t1", "k1", "k2"]);
// 按创建日期、名称排：手排的不因新消息挪
const made = sorted([r("k1", 800, 0, { created: 100 }), r("k2", 900, 1, { created: 200 }), r("k3", 950, 2, { created: 300 })]) as any;
assert.deepEqual(arrangeGroup(made, saved, { by: "created", desc: true }).map((x: any) => x.key), ["k1", "k2", "k3"]);
assert.deepEqual(arrangeGroup(made, saved, { by: "name", desc: false }).map((x: any) => x.key), ["k1", "k2", "k3"]);
""")

    def test_remote_unpins_show_locally_in_batch_order_and_are_not_saved(self):
        # 测试窗口第 10 轮 B 段：电脑多选取消置顶显示「Express、กฎ」，服务端却存成「กฎ、Express」。原因（审查）：另一台设备收到推送后
        # 每条当成单独一条回组顶（先后倒过来），再按收拾存上去。现在别处来的只改本机显示、不存，同批（BATCH_MS 内）按推来的先后
        self.run_page(STORAGE_STUB + r"""
import { applyLifts, BATCH_MS, bindOrderUser, LIFT_TTL, noteUnpinned, setGroupOrder, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
const g = globalThis as any;
let clock = 1000;
Object.defineProperty(g, "performance", { value: { now: () => clock }, configurable: true });
bindOrderUser("u@x");
setGroupOrder("CO", ["s9"], 900);
g.prefChanges.length = 0;
const spot = (key: string) => (key.startsWith("s") ? { group: "CO", stamp: 950 } : null);
noteUnpinned("sA", false);
clock += 300;
noteUnpinned("sB", false);
applyLifts(spot);
assert.deepEqual(useSidebarOrder.getState().groups["CO"]!.keys, ["sA", "sB", "s9"]);   // 同发起那一台的先后
assert.deepEqual(g.prefChanges, []);                                                       // 不存、不同步
// 不在同一批（隔了 BATCH_MS 以上）的照常回到最上面
clock += BATCH_MS + 1;
noteUnpinned("sC", false);
applyLifts(spot);
assert.deepEqual(useSidebarOrder.getState().groups["CO"]!.keys.slice(0, 3), ["sC", "sA", "sB"]);
assert.deepEqual(g.prefChanges, []);
// 发起那一台存上的那份换进来：照它，收拾完了不再挪
g.prefParts.get("order").apply({ companies: [], pinned: [], "group:CO": { keys: ["sA", "sB", "sC", "s9"], at: 900, top: { n: 3, at: 950 } } }, "u@x");
applyLifts(spot);
assert.deepEqual(useSidebarOrder.getState().groups["CO"]!.keys, ["sA", "sB", "sC", "s9"]);
assert.deepEqual(g.prefChanges, []);
clock += LIFT_TTL;
""", stubs={"@/lib/prefs-sync": PREFS_STUB})

    def test_an_unpin_still_fetches_before_tidying_the_pinned_order(self):
        # 审查：取消置顶成功后先按数据收拾置顶顺序（要先取一次服务端那份）、紧接着存那一组；prefs-sync 只有一个计时器，后排的存把
        # 取冲掉，服务端的置顶顺序一直留着它。现在先存那一组、再收拾，排着的「先取再存」也不被之后排的只存换掉
        self.run_page(STORAGE_STUB + r"""
import { applyLifts, bindOrderUser, forgetPinned, unpinChat, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import { SAVE_DELAY, syncPrefs, TIDY_FRESH } from "@/lib/prefs-sync";
const g = globalThis as any;
let server: any = { order: { companies: [], pinned: ["s1", "s2", "s3"], "group:CO": { keys: ["s9"], at: 900 } } };
const merge = (old: any, sent: any) => {
  const out: any = { ...(old ?? {}) };
  for (const [part, fields] of Object.entries(sent)) out[part] = { ...(out[part] ?? {}), ...(fields as any) };
  return out;
};
g.respond = async (method: string, body: any) => {
  if (method === "layout") return g.ok({ prefs: server });
  if (method === "save_layout") { server = merge(server, body.prefs); return g.ok({ prefs: server }); }
  return g.ok(null);
};
g.ok = (message: any) => ({ ok: true, status: 200, json: async () => ({ message }) });
const realNow = performance.now.bind(performance);
let skew = 0;
(performance as any).now = () => realNow() + skew;
bindOrderUser("skin3@x");
await syncPrefs("skin3@x");
assert.deepEqual(useSidebarOrder.getState().pinned, ["s1", "s2", "s3"]);
skew += TIDY_FRESH * 5;                                   // 离上次取过很久：收拾要先取
g.calls.length = 0;
let answer: () => void = () => undefined;
const done = unpinChat("s2", () => new Promise<void>((resolve) => (answer = resolve)));
applyLifts((key) => (key === "s2" ? { group: "CO", stamp: 950 } : null));
answer();
await done;
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(g.calls.map((c: any) => c.method), ["layout", "save_layout"]);   // 先取，取完把挪好的那一组一并存上
assert.deepEqual(server.order["group:CO"].keys, ["s2", "s9"]);
// 换成服务端那份以后侧栏照新的那份再收拾一遍（这时刚取过）：置顶顺序去掉它，存上去
forgetPinned(["s2"]);
await g.sleep(SAVE_DELAY + 100);
assert.deepEqual(server.order.pinned, ["s1", "s3"]);
""")
        sync = read("lib/prefs-sync.ts")
        self.assertIn("refetch ||= again;", sync)
        self.assertIn("if (timer !== null && !refetch) {", sync)
        order = read("lib/managed-agents/sidebar-order.ts")
        unpin = order.split("export async function unpinChat(", 1)[1].split("\n}\n", 1)[0]
        self.assertLess(unpin.index("unpinSettled(key, true);"), unpin.index("forgetPinned([key]);"))

    def test_coming_back_and_unknown_chats_follow_new_activity(self):
        # 审查：回前台、重连的核对只改标题星标，不改最后活动时间（断开期间漏的推送不补发）；推来的新消息属于还没加载的对话时只记了
        # 浮顶时间，不出行；浮顶在事件没有 processed_at 时用本机时钟
        run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\nimport assert from "node:assert/strict";\n'
               + SidebarSyncBehaviourTests.IMPORTS + 'import { bumpOf } from "@/lib/managed-agents/activity";\n'
               + "\nconst g = globalThis as any;\n" + SYNC_SITE + r"""
site.sessions = [row("sesn_aaa001", "A", 1), row("sesn_bbb001", "B", 5)];
const { core, threads } = sidebar();
core.__internal_load();
await core.getLoadThreadsPromise();
watchList(() => threads);
const quiet = { note: () => {}, stop: () => {} };
// 锁屏时在电脑上给 B 发了消息：核对时服务端给的上次更新比本页的新，照它浮
reconcile(threads, { sessions: [row("sesn_bbb001", "B", 0), row("sesn_aaa001", "A", 1)], nextCursor: null } as any, Date.now(), quiet);
assert.equal(bumpOf("sesn_bbb001"), Date.parse(at(0)));
assert.equal(bumpOf("sesn_aaa001"), undefined);            // 没变的不动
// 推来的消息没有服务端时间：不浮（不用本机时钟）
g.socket.emit("pearnly_assistant_event", { session_id: "sesn_aaa001", event: { type: "agent.message", id: "sevt_x1", content: [] } });
assert.equal(bumpOf("sesn_aaa001"), undefined);
g.socket.emit("pearnly_assistant_event", { session_id: "sesn_aaa001", event: { type: "agent.message", id: "sevt_x2", processed_at: at(-1), content: [] } });
assert.equal(bumpOf("sesn_aaa001"), Date.parse(at(-1)));
// 还没加载的旧对话来了消息：重拉一次，它出现在侧栏
const before = listings().length;
site.sessions = [row("sesn_old900", "Old", -2), ...site.sessions];
g.socket.emit("pearnly_assistant_event", { session_id: "sesn_old900", event: { type: "user.message", id: "sevt_x3", processed_at: at(-2), content: [] } });
await g.sleep(1600);
assert.equal(listings().length, before + 1);
assert.ok(core.threadIds.includes("sesn_old900"));
// 拉完仍不在列表里的（别的产品、已归档）：只为它拉这一次
for (const id of ["sevt_g1", "sevt_g2"]) g.socket.emit("pearnly_assistant_event", { session_id: "sesn_ghost1", event: { type: "agent.message", id, processed_at: at(-3), content: [] } });
await g.sleep(1600);
g.socket.emit("pearnly_assistant_event", { session_id: "sesn_ghost1", event: { type: "agent.message", id: "sevt_g3", processed_at: at(-4), content: [] } });
await g.sleep(1600);
assert.equal(listings().length, before + 2);
g.done();
""", stubs=SidebarSyncBehaviourTests.STUBS)

    def test_long_press_menus_open_without_the_modal_page_work(self):
        # 审查：手机按住不动到 500ms 出长按菜单那一帧修前修后都是 100ms 以上。查清：模态菜单的整页禁止滚动样式表、body 的
        # pointer-events、其余节点 aria-hidden 各引起一次整页样式重算；再加上根元素做 :has() 锚点（html:has(.cc-mshell)）与通配的
        # 相邻兄弟规则（.cc-reply > * + *），往 body 末尾挂菜单、拖动浮层都让整页约 940 个元素重算
        hook = read("hooks/use-long-press.ts")
        self.assertIn("export function useLongPressMenu()", hook)
        self.assertIn("() => setMenu({ open: true, modal: false }),", hook)
        self.assertIn("setMenu((now) => (open ? { open: true, modal: true } : { ...now, open: false }))", hook)
        rows = read("components/assistant-ui/thread-list.tsx")
        self.assertIn("const { open: menuOpen, setOpen: setMenuOpen, modal: menuModal, press } = useLongPressMenu();", rows)
        self.assertIn("<ThreadListItemMore open={menuOpen} modal={menuModal} onOpenChange={setMenuOpen} onRename={startRename} />", rows)
        self.assertIn("<DropdownMenu.Root open={open} onOpenChange={onOpenChange} modal={modal}>", rows)
        self.assertIn("<DropdownMenu.Root open={open} onOpenChange={onOpenChange} modal={modal}>", read("components/assistant-ui/sidebar-group.tsx"))
        self.assertNotIn("useLongPress(", rows + read("components/assistant-ui/projects.tsx"))
        css = read("globals.css")
        self.assertNotRegex(css, r"(?m)^\s*html:has\(")
        self.assertIn("html[data-cc-mshell], html[data-cc-mshell] body { height: 100%; overflow: hidden; overscroll-behavior: none; }", css)
        self.assertNotRegex(css, r">\s*\*\s*\+\s*\*")
        shell = read("components/mobile-shell.tsx")
        self.assertIn('root.dataset.ccMshell = "";', shell)
        self.assertIn("delete root.dataset.ccMshell;", shell)

    def test_review_small_fixes(self):
        drag = read("components/ui/drag.tsx")
        # 输入方式的选项在拖动区建一次（DragEngine 构造时），DragArea 重画只换回调（engine.props）：把手不跟着换
        self.assertIn("  constructor(transition: Motion | null, numbered = true) {", drag)
        self.assertIn("    this.options = {\n      mouse: { activationConstraint: MOUSE_START, beforeEnd, beforeCancel },", drag)
        self.assertIn("const engine = useMemo(() => new DragEngine(motion()), []);\n  engine.props = { accepts, onDrop, onOver, onActive, onReturn, onPointer };", drag)
        item = drag.split("export function useSortableItem(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("[engine, drag.id, handleRef, disabled, ownFocus, skip, isDragging],", item)
        rows = read("components/assistant-ui/thread-list.tsx")
        self.assertNotIn("[drag, scope, id]", rows)
        self.assertNotIn("drag.moved(latestMove.current(delta)), [drag]", rows)
        # 主题色：读到了才记下（样式表没生效时读到空，下一次再读）
        main = read("main.tsx")
        self.assertLess(main.index("if (!value) return;\n  painted = key;"), main.index("for (const meta of document.querySelectorAll"))
        # 不按公司分组的侧栏也显示出错行
        self.assertIn("    <SidebarError />\n    <DayRows {...props} />", rows)
        # 浮顶只用服务端时间
        activity = read("lib/managed-agents/activity.ts")
        self.assertNotIn("Date.now()", activity.split("function bump(", 1)[1].split("\n}\n", 1)[0])


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class Round11DragFramesTests(unittest.TestCase):
    """第 11 轮 B14（10-17，A14 真 Chrome）：拿起、出 / 收长按菜单、拖进置顶、Esc 下一帧、落定的长帧。共用拖动一处改，所有拖动处
    一起受益：让位的位移与过渡不经 React 传给各行、播报区与浮层不挂在会盯着的地方、拖动库拿起时不再多量多观察、整页光标只给鼠标
    且松手就去、整列滑动拿起后一帧才开始；侧栏整列重画时没变的组与组头不重画；Esc 后不再留下空的「已置顶」。"""

    def run_page(self, body, stubs=None):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + body
               + "\n(globalThis as any).done();\n", stubs=stubs)

    def test_rows_keep_the_same_props_when_sorting_starts(self):
        # 原来位移、过渡放在 node 的 style 里：拖动库一开始排序每一项的过渡从无变成字符串，node 变了，整列行与组头全部重画。
        # 现在 node 只有 ref 与标记（按内容记住），位移、过渡在提交时直接写到节点上，没在让位的项一次也不写
        drag = read("components/ui/drag.tsx")
        item = drag.split("export function useSortableItem(", 1)[1].split("\n}\n", 1)[0]
        node = item.split("const node = useMemo(", 1)[1].split(");", 1)[0]
        self.assertNotIn("style", node)
        self.assertIn("[ref, drag.id, where, isDragging]", node)
        # 位移、过渡不经 React：拖动区在经过的项变了时直接写到节点上（applyShift），各项不订阅拖动的上下文、只订阅自己
        self.assertNotIn("applyShift", item)
        self.assertNotIn("useContext(", item.replace("useContext(AreaContext)", "").replace("useContext(ListContext)", ""))
        self.assertIn("const state = useSyncExternalStore(subscribe, () => engine.stateOf(drag.id), () => 0);", item)
        shifts = drag.split("  private shifts() {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("applyShift(node, shiftStyle({ x: 0, y }, transition));", shifts)
        finish = drag.split("  private finish(d: Session, work: () => void) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertLess(finish.index("flushSync(run)"), finish.index("if (node) applyShift(node, NO_SHIFT);"))   # 新顺序提交后当场归位
        self.run_page(r"""
import { applyShift } from "@/components/ui/drag";
const writes: string[] = [];
const fake = () => {
  const values: Record<string, string> = { transform: "", transition: "" };
  const style: any = {};
  for (const name of ["transform", "transition"]) {
    Object.defineProperty(style, name, { get: () => values[name], set: (v: string) => { writes.push(`${name}=${v}`); values[name] = v; } });
  }
  return { style } as any;
};
const T = "transform 200ms ease";
const row = fake();
applyShift(row, { transform: "", transition: T });                     // 拿起：开始排序、这一项不让位，一次也不写
assert.deepEqual(writes, []);
applyShift(row, { transform: "translate3d(0px, 34px, 0)", transition: T });   // 让位：先过渡、再位移
assert.deepEqual(writes, [`transition=${T}`, "transform=translate3d(0px, 34px, 0)"]);
writes.length = 0;
applyShift(row, { transform: "translate3d(0px, 34px, 0)", transition: T });   // 没变：不写
assert.deepEqual(writes, []);
applyShift(row, { transform: "", transition: T });                     // 回原位：带着过渡滑回
assert.deepEqual(writes, ["transform="]);
writes.length = 0;
applyShift(row, { transform: "", transition: "" });                    // 结束：留着的过渡清掉
assert.deepEqual(writes, ["transition="]);
writes.length = 0;
const moved = fake();
applyShift(moved, { transform: "translate3d(0px, -34px, 0)", transition: T });
writes.length = 0;
applyShift(moved, { transform: "", transition: "" });                  // 结束那次提交（新顺序同时排好）：不带过渡、当场归位
assert.deepEqual(writes, ["transition=", "transform="]);
applyShift(null, { transform: "x", transition: T });                   // 还没挂上：不动
""")

    def test_items_that_do_not_move_are_never_written(self):
        # 第 11 轮 B14 审查：拿起时拖动库给每一项 {x:0, y:0}，原来换算成 "translate3d(0px, 0px, 0)" 照样写到每一项上（键盘拿起组头、
        # 鼠标拿起行时不让位的各项也重算样式）；浏览器读回会把 0 写成 0px、过渡补成完整写法，拿读回的比永远不等，每次提交都重写
        self.run_page(r"""
import { applyShift, shiftStyle } from "@/components/ui/drag";
const writes: string[] = [];
// 假样式：写进去的值按浏览器的写法读回（位移第三个参数 0 → 0px；过渡 200ms → 0.2s）
const normal = (name: string, v: string) =>
  name === "transform" ? v.replace(/, 0\)$/, ", 0px)") : v.replace(/(\d+)ms/g, (_, n) => `${Number(n) / 1000}s`);
const fake = () => {
  const values: Record<string, string> = { transform: "", transition: "" };
  const style: any = {};
  for (const name of ["transform", "transition"]) {
    Object.defineProperty(style, name, { get: () => values[name], set: (v: string) => { writes.push(`${name}=${v}`); values[name] = normal(name, v); } });
  }
  return { style } as any;
};
const T = "transform 200ms ease";
const zero = { x: 0, y: 0, scaleX: 1, scaleY: 1 };
assert.deepEqual(shiftStyle(zero, T), { transform: "", transition: T });
assert.deepEqual(shiftStyle({ x: 0.3, y: -0.4, scaleX: 1, scaleY: 1 }, T), { transform: "", transition: T });   // 取整后是零
assert.deepEqual(shiftStyle(null, null), { transform: "", transition: "" });
assert.deepEqual(shiftStyle({ x: 0, y: 34, scaleX: 1, scaleY: 1 }, T, true), { transform: "", transition: T });  // 滑回途中
const still = fake();
applyShift(still, shiftStyle(zero, T));                     // 拿起：开始排序、这一项不让位，一次也不写
applyShift(still, shiftStyle(zero, T));
assert.deepEqual(writes, []);
const row = fake();
const moved = shiftStyle({ x: 0, y: 34, scaleX: 1, scaleY: 1 }, T);
assert.equal(moved.transform, "translate3d(0px, 34px, 0px)");
applyShift(row, moved);                                     // 让位：先过渡、再位移
assert.deepEqual(writes, [`transition=${T}`, "transform=translate3d(0px, 34px, 0px)"]);
writes.length = 0;
applyShift(row, shiftStyle({ x: 0, y: 34, scaleX: 1, scaleY: 1 }, T));   // 读回的写法变了，值没变：不再写
assert.deepEqual(writes, []);
const legacy = fake();                                      // 位移串与读回的写法不同（第三个参数 0）：同样只写一次
applyShift(legacy, { transform: "translate3d(0px, 34px, 0)", transition: T });
writes.length = 0;
applyShift(legacy, { transform: "translate3d(0px, 34px, 0)", transition: T });
assert.deepEqual(writes, []);
row.style.transform = "";                                   // 别处直接清掉了位移（取消滑回时 settleBack）
writes.length = 0;
applyShift(row, moved);                                     // 再要这段位移：照读回的比，补写
assert.deepEqual(writes, ["transform=translate3d(0px, 34px, 0px)"]);
writes.length = 0;
applyShift(row, shiftStyle(zero, ""));                      // 结束：不带过渡、当场归位
assert.deepEqual(writes, ["transition=", "transform="]);
""")

    def test_drag_cursor_only_for_the_mouse_and_gone_at_release(self):
        # 整页 grabbing 与不出悬停靠鼠标拖动的挡板（第 11 轮 B14 审查起；原来是根节点标记，加、去各要整页约 800 个元素重算样式）：手机、
        # 键盘拖动不盖；拿起那一帧画完才盖，松手开始落位那一帧画完就撤，不留到落定那次整列重画里；Esc 后要挪回原组的那一下先不撤
        # （"keep"：Esc 那一帧与下一帧整列重挂都不叠一次悬停重算），起播滑回时那一帧画完再撤
        self.run_page(r"""
import { dragShieldShown, isDragEscape, isDragging, releaseDrag, setDragShield, setDragging } from "@/lib/layers";
const g = globalThis as any;
const page = new EventTarget();
g.addEventListener = page.addEventListener.bind(page);
g.removeEventListener = page.removeEventListener.bind(page);
const shield = { hidden: true } as any;
setDragShield(shield);
setDragging(true, false);                                // 手机 / 键盘
await g.sleep(10);
assert.equal(isDragging(), true);
assert.equal(shield.hidden, true);
setDragging(false);
setDragging(true);                                       // 鼠标：这一帧画完以后盖
await g.sleep(10);
assert.equal(shield.hidden, false);
releaseDrag();                                           // 松手：这一帧画完以后撤
assert.equal(shield.hidden, false);
await g.sleep(10);
assert.equal(shield.hidden, true);
assert.equal(dragShieldShown(), false);
assert.equal(isDragging(), true);                        // 还在等落位：逻辑上仍在拖
setDragging(false);
setDragging(true);
await g.sleep(10);
releaseDrag("keep");                                     // Esc 挪回原组：当场放手（之后的 Esc 照常），挡板先不撤
const esc = new Event("keydown");
assert.equal(isDragEscape(esc), false);
await g.sleep(10);
assert.equal(shield.hidden, false);
releaseDrag();                                           // 起播滑回（ReleaseFirst.hold）：这一帧画完撤
await g.sleep(10);
assert.equal(shield.hidden, true);
setDragging(false);
setDragging(true);
await g.sleep(10);
releaseDrag("keep");
setDragging(false);                                      // 没有滑回、当场结束：结束时撤
assert.equal(shield.hidden, true);
setDragging(true);
releaseDrag();                                           // 还没盖上就松手了：不再盖
await g.sleep(10);
assert.equal(shield.hidden, true);
setDragging(true);
releaseDrag("keep");                                     // 还没盖上就 Esc：同样不再盖
await g.sleep(10);
assert.equal(shield.hidden, true);
setDragging(false);
setDragShield(null);
""")
        drag = read("components/ui/drag.tsx")
        self.assertIn("setDragging(true, byMouse(activatorEvent));", drag)
        self.assertIn('const byMouse = (event: Event | null | undefined) => !!event && !("key" in event) && !("touches" in event);', drag)
        # 起播滑回那一下（homeLanded → letGo.hold → ReleaseFirst.hold）再放一次手，挡板在那一帧画完后撤
        hold = drag.split("export class ReleaseFirst {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("    releaseDrag();\n", hold)
        landed = drag.split("  private homeLanded(entry: Homing, node: HTMLElement | null) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("this.letGo.hold(entry.cancel, animation, this.landWait, () => entry.unfreeze);", landed)
        css = read("globals.css")
        # 浮层不接指针：拿起、落定时整个侧栏不换悬停、不整块重算样式；悬停与光标归挡板
        self.assertIn(".cc-drag-overlay { pointer-events: none; }", css)
        self.assertIn(".cc-drag-shield { position: fixed; inset: 0; z-index: var(--cc-z-drag-shield); cursor: grabbing; }", css)
        self.assertNotIn(":root[data-cc-drag]", css)

    def test_the_mouse_drag_shield_blocks_hover_and_passes_the_wheel_on(self):
        # 第 11 轮 B14 审查：浮层不接指针以后，鼠标拖动中指针下面的行、收着的「已置顶」组头会露出悬停底、「···」、深色字与折叠箭头；
        # 现在拖动层里一块透明挡板（在浮层下面）接住指针：光标 grabbing、下面什么都不悬停；落点照旧只按 data-drop-target 高亮。
        # 挡着时滚轮交给指针下面最近的滚动区（侧栏、会话栏拖着时照样能滚）
        drag = read("components/ui/drag.tsx")
        layer = drag.split("export function dragLayer(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("layer.appendChild(dragShield());", layer)
        shield = drag.split("function dragShield(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('el.className = "cc-drag-shield";', shield)
        self.assertIn('el.addEventListener("wheel", (event) => wheelUnder(el, event), { passive: true });', shield)
        self.assertIn("setDragShield(el);", shield)
        self.assertIn("export const OVERLAY_Z = 55;", drag)        # 挡板 54 在浮层下面
        self.run_page(r"""
import { wheelUnder } from "@/components/ui/drag";
const g = globalThis as any;
g.HTMLElement ??= class {};
const scrolled: any[] = [];
const box = (h: number, ch: number, overflowY: string, parent: any = null) => {
  const el = Object.create(g.HTMLElement.prototype);
  Object.defineProperty(el, "scrollHeight", { value: h });
  Object.defineProperty(el, "clientHeight", { value: ch });
  Object.defineProperty(el, "parentElement", { value: parent });
  el.overflowY = overflowY;
  el.scrollBy = (o: any) => scrolled.push([el, o]);
  return el;
};
g.getComputedStyle = (el: any) => ({ overflowY: el.overflowY });
g.window.innerHeight = 800;
const list = box(2000, 600, "auto");
const group = box(300, 300, "visible", list);
const row = box(32, 32, "visible", group);
const shield = {};
g.document.elementsFromPoint = () => [shield, row, group, list];
wheelUnder(shield as any, { deltaMode: 0, deltaX: 0, deltaY: 120, clientX: 10, clientY: 10 } as any);
assert.equal(scrolled.length, 1);
assert.equal(scrolled[0][0], list);
assert.deepEqual(scrolled[0][1], { top: 120, left: 0 });
wheelUnder(shield as any, { deltaMode: 1, deltaX: 0, deltaY: 3, clientX: 10, clientY: 10 } as any);   // 按行滚
assert.deepEqual(scrolled[1][1], { top: 48, left: 0 });
const plain = box(10, 10, "visible", null);
g.document.elementsFromPoint = () => [shield, plain];
wheelUnder(shield as any, { deltaMode: 0, deltaX: 0, deltaY: 50, clientX: 10, clientY: 10 } as any);   // 下面没有能滚的：不动
assert.equal(scrolled.length, 2);
""")

    def test_only_flow_areas_skip_size_watching(self):
        # 第 11 轮 B14 审查：原来所有拖动区都关了各项、各落点的尺寸观察器；会话栏（没有整列滑动）的卡片拖动中会变高（本月卡定时刷新、
        # 公司页转圈换成内容、输出卡跟着对话变长），不观察就按旧尺寸算碰撞与让位。现在只有给了 flow 的区（侧栏：行高固定，增删、挪列
        # 由整列滑动重量）才关
        drag = read("components/ui/drag.tsx")
        # 自写引擎：平时谁都不观察；拿起那一帧画完以后，有整列滑动的区盯版面（flowLayout），没有的区给能放的各项挂一个尺寸观察器
        # （首轮回调是挂上时的，不算变化），变了下一帧重量；结束拖动时摘掉
        watch = drag.split("  private watch(d: Session) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("if (this.flow) {", watch)
        self.assertIn("} else if (typeof ResizeObserver === \"function\") {", watch)
        self.assertIn("if (first) first = false;\n        else this.remeasureSoon();", watch)
        self.assertIn("for (const id of d.candidates) {", watch)
        self.assertIn("d.stops.push(() => observer.disconnect());", watch)
        self.assertEqual(drag.count("new ResizeObserver("), 1)
        for name in ("export function useSortableItem(", "export function useDropZone("):
            self.assertNotIn("ResizeObserver", drag.split(name, 1)[1].split("\n}\n", 1)[0])
        panel = read("components/session-panel.tsx")
        self.assertIn("<DragArea accepts={sameColumn} onDrop={onDrop} texts={texts}>", panel)   # 会话栏：没有 flow，照拖动库默认观察
        rows = read("components/assistant-ui/thread-list.tsx")
        self.assertIn("flow={FLOW} texts={texts}>", rows)               # 侧栏：有 flow，关掉

    def test_measuring_reads_the_layout_position_and_the_overlay_sits_in_its_own_layer(self):
        # 量落点按版面位置：去掉排序让位的 transform，也去掉整列滑动（flowLayout）正在播的 translate
        self.run_page(r"""
import { dragLayer, layoutRect } from "@/components/ui/drag";
const g = globalThis as any;
g.getComputedStyle = (el: any) => ({ transform: el.transform ?? "none", transformOrigin: "0px 0px", translate: el.translate ?? "none" });
const node = (top: number, extra: any = {}) => ({ getBoundingClientRect: () => ({ top, left: 10, width: 200, height: 34, right: 210, bottom: top + 34 }), ...extra });
assert.equal(layoutRect(node(100) as any).top, 100);
assert.equal(layoutRect(node(130, { translate: "0px 30px" }) as any).top, 100);          // 正在滑：滑动起点的位移去掉
assert.equal(layoutRect(node(130, { translate: "0px 30px" }) as any).bottom, 134);
assert.equal(layoutRect(node(164, { transform: "matrix(1, 0, 0, 1, 0, 34)", translate: "0px 30px" }) as any).top, 100);
// 浮层那一层：body 下面一个，建一次；被移走了再建
const appended: any[] = [];
g.document.body = { appendChild: (el: any) => { el.isConnected = true; appended.push(el); } };
g.document.createElement = () => {
  const el: any = { dataset: {} as Record<string, string>, isConnected: false, children: [] as any[], attrs: {} as Record<string, string> };
  el.appendChild = (child: any) => el.children.push(child);
  el.setAttribute = (name: string, value: string) => { el.attrs[name] = value; };
  el.addEventListener = () => {};
  return el;
};
const layer = dragLayer();
assert.equal(dragLayer(), layer);
assert.equal(appended.length, 1);
assert.equal((layer as any).className, "cc-drag-layer");
// 鼠标拖动的挡板与浮层同在这一层里，平时藏着
assert.equal((layer as any).children[0].dataset.slot, "cc-drag-shield");
assert.equal((layer as any).children[0].hidden, true);
(layer as any).isConnected = false;
assert.notEqual(dragLayer(), layer);
assert.equal(appended.length, 2);
""")
        drag = read("components/ui/drag.tsx")
        # 量能放的各处：拿起时只读框与行内样式（liftRect）；整列刚滑过、版面变了以后的重量按版面位置（layoutRect）
        measure = drag.split("  private measure(d: Session, full: boolean) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("d.rects.set(id, full ? layoutRect(node) : liftRect(node));", measure)
        # 播报区与说明在拖动区挂上时挂到 body 上，常驻（原来就地挂在侧栏、会话栏的滚动区里，每念一句都是一次增删）
        mount = drag.split("  mount(): () => void {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("document.body.append(hidden, live);", mount)
        self.assertIn("dragLayer();", mount)
        for name in ("layoutRect(node)", "layoutRect(fold ? zone! : node)"):
            self.assertIn(name, drag)

    def test_whole_list_redraws_skip_unchanged_groups(self):
        # 拖进已置顶、Esc 挪回原组、落定存顺序时侧栏整列重画：没变的组的行、组头不跟着重画；一列的各项按内容记住
        drag = read("components/ui/drag.tsx")
        lst = drag.split("export const DragList:", 1)[1].split("\n};\n", 1)[0]
        self.assertIn('const key = items.join("\\u0000");', lst)
        self.assertIn("const same = useMemo(() => items, [key]);", lst)
        self.assertIn("engine.setList(id, same);", lst)
        self.assertIn("}, [engine, id, same]);", lst)
        # 各项从这一列只拿到它的编号（不随 items 变）：一列的先后变了，各项不跟着重画
        self.assertIn("<ListContext.Provider value={id}>{children}</ListContext.Provider>", lst)
        rows = read("components/assistant-ui/thread-list.tsx")
        self.assertIn("const Items = memo(function Items(", rows)
        self.assertIn("components={ITEM_COMPONENTS}", rows.split("const Items = memo(function Items(", 1)[1].split("\n});", 1)[0])
        self.assertIn("const ITEM_COMPONENTS = { ThreadListItem };", rows)
        group = rows.split("const CompanyGroup: FC<", 1)[1].split("\n};\n", 1)[0]
        self.assertIn("const toggle = useCallback(() => latestToggle.current(), []);", group)
        self.assertIn("const shownSessions = useMemo(() => sessions, [sessionsKey]);", group)
        self.assertIn("onToggle={toggle} sessions={shownSessions}", group)
        # 整列滑动在拿起那一帧画完以后才开始盯
        lift = drag.split("  private lift(id: string, activatorEvent: Event, kind: Kind, at: Coordinates) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("afterPaint(() => this.drag === d && this.watch(d));", lift)
        self.assertNotIn("flowLayout(", lift)

    def test_pointer_is_not_reported_after_the_drag_ends(self):
        # 拖进已置顶后按 Esc：结束拖动那次提交里拖动库清掉落点，PointerWatch 随后还会跑一次；原来照样把停着的位置交给侧栏，侧栏又
        # 临时出一个空的「已置顶」留着。现在只在拿起到结束之间交
        drag = read("components/ui/drag.tsx")
        # 自写引擎：指针位置只在算落点时交（collide），算落点只在拖动中、没在等滑回时；结束拖动先把这次拖动清掉再提交
        collide = drag.split("  private collide() {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("if (!d || d.cancelling || !d.data.list) return;", collide)
        self.assertIn("if (!keyboard && d.pointer) this.props.onPointer?.(d.data, d.pointer.y);", collide)
        finish = drag.split("  private finish(d: Session, work: () => void) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertLess(finish.index("this.drag = null;"), finish.index("flushSync(run)"))
        self.assertIn("if (!d?.scroller) return;", drag.split("  private readonly onScroll = () => {", 1)[1].split("\n  };\n", 1)[0])
        # 侧栏收起临时的已置顶仍在挪回原组那一下（onReturn），随滑回一起
        rows = read("components/assistant-ui/thread-list.tsx")
        self.assertIn("const onReturn = () => {\n    placeGuest(null);\n    setReveal(false);\n  };", rows)


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class Round11CardTests(unittest.TestCase):
    """测试窗口第 10 轮实测（lab 97553dd2）：C20 卡片没答就停止写成「允许 · 失败」；C22 另一台设备 6–17 秒才变成点完；
    E30 断网点停止后已发出的那句被放回输入框。"""
    STUBS = {"cc-ma-setup": MA_SETUP, "socket.io-client": SOCKET_STUB}

    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\nimport assert from "node:assert/strict";\n'
               + body.split("\n---\n", 1)[0] + "\nconst g = globalThis as any;\n" + FAKE_SITE
               + body.split("\n---\n", 1)[1] + "\ng.done();\n", stubs=self.STUBS)

    def test_a_card_left_unanswered_by_stop_reads_cancelled_live_and_reopened(self):
        # 卡片出来后没答、直接点停止：以前当成「允许了却失败」（红字），重开一样；实际没点、也没改。
        # 第 11 轮 A 段改成真实先后（lab 9798f54e 抓的事件）：点停止时引擎先代发一条出错的中断结果（agent.tool_result，不是本站的
        # user.custom_tool_result），几十毫秒后才是 user.interrupt。中断结果不算点了「允许」：本页与重开都是问话 + 淡色「已取消」；
        # 晚到的问句不再把按钮放回来；本站执行记录（pearnly.tool_run）到了以它为准
        self.run_page(r"""
import { applyEvent, gateCustomTool, project } from "@/lib/managed-agents/reducer";
import { approvalCard } from "@/lib/approval-card";
---
const gates = { ask: { pearnly_task_switch: "Turn it on?" } };
const part = (state: any) => state.messages.flatMap((m: any) => m.content).find((p: any) => p.toolCallId === "tz");
const head = [ev("u1", "user.message", 1, { content: text("turn T1 on") }), ev("r1", "session.status_running", 2),
  ev("tz", "agent.custom_tool_use", 3, { name: "pearnly_task_switch", input: { task: "T1", enabled: true } }),
  ev("p1", "session.status_idle", 4, { stop_reason: { type: "requires_action", event_ids: ["tz"] } })];
const result = (isError: boolean, body: string, s = 6) =>
  ev("res", "user.custom_tool_result", s, { custom_tool_use_id: "tz", content: text(body), is_error: isError });
const record = (status: string, s: number) => ev(`ptoolrun_tz_${status}`, "pearnly.tool_run", s, { tool_use_id: "tz", status });
// 真实先后：平台的中断结果 → user.interrupt → 回合结束
const stop = [ev("int", "agent.tool_result", 5, { tool_use_id: "tz", is_error: true,
  content: text("Tool execution was interrupted before completion. Please retry.") }),
  ev("i1", "user.interrupt", 5.05), ev("p2", "session.status_idle", 7, { stop_reason: { type: "end_turn" } })];
// 本页：卡片等回答（问句向服务端要来的），点停止
let live = gateCustomTool(project(head as any), "tz", { ask: "Turn it on?" });
assert.deepEqual(approvalCard(part(live), gates), { phase: "ask" });
live = project(stop as any, live);
assert.deepEqual(approvalCard(part(live), gates), { phase: "cancelled" });
assert.equal(part(live).approval, undefined);
assert.deepEqual([live.pendingApprovals, live.pendingCustomTools, live.parkedIds, live.isRunning], [[], [], [], false]);
live = applyEvent(live, record("cancelled", 8) as any);                              // 本站作废的记录随后到：照旧
assert.deepEqual(approvalCard(part(live), gates), { phase: "cancelled" });
// 重开对话：历史里没有点击记录，那一步也还没有门（问句要向服务端要）；执行记录接在这页最后
const reopened = project([...head, ...stop, record("cancelled", 8)] as any);
assert.deepEqual(approvalCard(part(reopened), gates), { phase: "cancelled" });
assert.equal(gateCustomTool(reopened, "tz", { ask: "Turn it on?" }), reopened);   // 晚到的问句不放回按钮
assert.deepEqual(approvalCard(part(project([...head, ...stop] as any)), gates), { phase: "cancelled" });  // 记录还没到也一样
// 对照：没停止、点了「允许」执行出错的，照旧「允许 · 失败」；结果写明拒绝的照旧「拒绝」
const ran = project([...head, result(true, "The tool failed. Try again later."),
  ev("p2", "session.status_idle", 7, { stop_reason: { type: "end_turn" } }), record("error", 8)] as any);
assert.deepEqual(approvalCard(part(ran), gates), { phase: "answered", approved: true, failed: true });
const denied = project([...head, result(false, JSON.stringify({ status: "denied", message: "The user did not allow this change." }), 4.5),
  ev("i1", "user.interrupt", 5), record("denied", 6)] as any);
assert.deepEqual(approvalCard(part(denied), gates), { phase: "answered", approved: false, failed: false });
// 已答的（本页点了「允许」、还没结果）不因停止改成「已取消」，也不因平台的中断结果写成「失败」
let answered = gateCustomTool(project(head as any), "tz", { ask: "Turn it on?" });
answered = applyEvent(answered, ev("optimistic-confirm-1", "user.tool_confirmation", 4.5, { tool_use_id: "tz", result: "allow" }) as any);
const stopped = project(stop as any, answered);
assert.equal(part(stopped).approval.approved, true);
assert.equal(part(stopped).cancelled, undefined);
assert.equal(part(stopped).interrupted, true);
assert.equal(approvalCard(part(stopped), gates)?.phase, "answered");
assert.equal((approvalCard(part(stopped), gates) as any).approved, true);
assert.equal((approvalCard(part(stopped), gates) as any).failed, false);
// 第 11 轮审查 / A2：点了「允许」、本站执行成功（引擎收到的却是平台的中断结果）：本页与重开都是「允许」（重开时历史里没有点击
// 记录，中断结果那一刻照样标了 cancelled，不能因此写成「已取消」——写入已经发生了，以本站记录为准）
const liveRan = applyEvent(stopped, record("done", 8) as any);
assert.deepEqual(approvalCard(part(liveRan), gates), { phase: "answered", approved: true, failed: false });
const reopenedRan = project([...head, ...stop, record("done", 8)] as any);
assert.equal(part(reopenedRan).cancelled, true);
assert.deepEqual(approvalCard(part(reopenedRan), gates), { phase: "answered", approved: true, failed: false });
// 不要「允许」的工具被停止：照常是步骤组里的一行，不是卡片
const plain = project([ev("tp", "agent.custom_tool_use", 3, { name: "pearnly_read", input: {} }),
  ev("p1", "session.status_idle", 4, { stop_reason: { type: "requires_action", event_ids: ["tp"] } }),
  ev("int2", "agent.tool_result", 5, { tool_use_id: "tp", is_error: true, content: text("interrupted") }),
  ev("i1", "user.interrupt", 5.05)] as any);
assert.equal(approvalCard(plain.messages[0]!.content[0] as any, gates), null);
""")

    def test_an_answer_on_another_device_settles_the_card_from_the_push(self):
        # 另一台设备点了「允许」：服务端回答落库就推 pearnly.answer（toolrun.decide），这边不等工具执行完、不等补拉，
        # 收到推送当场定下（按钮没了、卡片是点完的样子），也不再算等这边点
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { dispatch } from "@/lib/managed-agents/realtime";
import { approvalCard } from "@/lib/approval-card";
---
const S = "sesn_c22";
site.log.push(ev("u1", "user.message", 1, { content: text("turn T1 on") }), ev("r1", "session.status_running", 2),
  ev("tz", "agent.custom_tool_use", 3, { name: "pearnly_task_switch", input: { task: "T1", enabled: true } }),
  ev("p1", "session.status_idle", 4, { stop_reason: { type: "requires_action", event_ids: ["tz"] } }));
site.runs = { tz: "awaiting_approval" };
site.toolReply = async () => ({ status: "awaiting_approval", ask: "Turn it on?", preview: [] });
const c = getSessionController(S);
c.attach();
await g.sleep(30);
assert.deepEqual(c.getSnapshot().pendingApprovals, ["tz"]);
const reads = eventsCalls(S).length;
dispatch({ session_id: S, event: { type: "pearnly.answer", id: "panswer_tz", tool_use_id: "tz", result: "allow", processed_at: at(5) } as any });
await g.sleep(0);
const snap: any = c.getSnapshot();
assert.equal(eventsCalls(S).length, reads);            // 没靠补拉
assert.deepEqual(snap.pendingApprovals, []);
assert.equal(toolPart(snap, "tz").approval.approved, true);
assert.equal(toolPart(snap, "tz").approval.sending, undefined);
assert.equal(toolPart(snap, "tz").approval.ask, "Turn it on?");
assert.deepEqual(approvalCard(toolPart(snap, "tz"), { ask: { pearnly_task_switch: "Turn it on?" } }),
  { phase: "answered", approved: true, failed: false });
assert.equal(snap.isRunning, true);                    // 答完接着跑
// 别的对话的推送不碰这一段
dispatch({ session_id: "sesn_other", event: { type: "pearnly.answer", id: "panswer_x", tool_use_id: "tz", result: "deny", processed_at: at(6) } as any });
await g.sleep(0);
assert.equal(toolPart(c.getSnapshot(), "tz").approval.approved, true);
""")

    def test_a_message_the_server_already_has_is_not_rolled_back(self):
        # 回复进行中断网：发出请求的回应没回来，但回声早已到了（服务端收下了）。以前照样当没发出、字放回输入框，再按发送重复一条；
        # 现在算发出了（不抛、气泡留着）；回声没到的照旧撤回、抛出（发送没成功、字放回）
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
---
const S = "sesn_e30";
site.log.push(ev("u0", "user.message", 1, { content: text("earlier") }), ev("i0", "session.status_idle", 2, { stop_reason: { type: "end_turn" } }));
const c = getSessionController(S);
c.attach();
await g.sleep(30);
const respond = g.respond;
const users = () => c.getSnapshot().messages.filter((m: any) => m.role === "user").map((m: any) => m.content[0]?.text);
g.respond = async (method: string, args: any, body: any) => {
  if (method !== "send") return respond(method, args, body);
  site.log.push(ev("echo1", "user.message", 3, { content: text(body.text) }), ev("r1", "session.status_running", 4));
  c.refreshStatus();
  await g.sleep(30);
  throw new TypeError("Failed to fetch");
};
await c.sendMessage("hello", []);
assert.deepEqual(users(), ["earlier", "hello"]);
g.respond = async (method: string, args: any, body: any) => {
  if (method === "send") throw new TypeError("Failed to fetch");
  return respond(method, args, body);
};
await assert.rejects(c.sendMessage("again", []), /Failed to fetch/);
assert.deepEqual(users(), ["earlier", "hello"]);
""")

    def test_the_new_chat_stop_failure_only_says_why(self):
        # 新对话发出后补的打断没成功：同停止键，只写停止的原因，不当发送失败、不放回输入框
        provider = read("lib/managed-agents/runtime-provider.tsx")
        self.assertIn("else await newChat.run(ensureSession, send, async (id) => {\n"
                      "            await attemptStop(threadId, () => getSessionController(id).interrupt());", provider)
        controller = read("lib/managed-agents/session-controller.ts")
        self.assertIn("if (!this.state.echoedOptimistic.has(optimisticId)) {", controller)

# 测试窗口第 11 轮 A 段在 lab（9798f54e，真引擎）抓的原始事件（会话 sesn_01X2K4vJBNvR361Cvjxap2Nw；A1、A2、A4 的序列完全一样），
# 编号与时间照抄。点停止时引擎先代发一条出错的中断结果（agent.tool_result，不是本站的 user.custom_tool_result），比 user.interrupt
# 早 47 毫秒。前面的用户消息、开跑两条是补的上下文。
REAL_A = r"""
const TOOL = "sevt_01UDVUzpwDom3EXfH9XEWYJm";
const NAME = "cowork_set_folder_task_enabled";
const GATES = { ask: { [NAME]: "Turn this folder task on or off?" } };
const ASK = { ask: "Turn this folder task on or off?", preview: [{ field: "Enabled", before: "Yes", after: "No" }] };
const HEAD = [
  { id: "sevt_u1", type: "user.message", processed_at: "2026-10-17T10:57:40.000000Z", content: [{ type: "text", text: "停用 fleigbs7p9" }] },
  { id: "sevt_r1", type: "session.status_running", processed_at: "2026-10-17T10:57:40.100000Z" },
  { id: TOOL, type: "agent.custom_tool_use", name: NAME, input: { enabled: false, task: "fleigbs7p9" },
    processed_at: "2026-10-17T10:57:45.204327Z" },
  { id: "sevt_thread_idle_1", type: "session.thread_status_idle", processed_at: "2026-10-17T10:57:45.308Z",
    stop_reason: { type: "requires_action", event_ids: [TOOL] } },
  { id: "sevt_idle_1", type: "session.status_idle", processed_at: "2026-10-17T10:57:45.308Z",
    stop_reason: { type: "requires_action", event_ids: [TOOL] } },
];
const STOP = [
  { id: "sevt_01VZ5u2hu3mRxat5JEvW9wcf", type: "agent.tool_result", tool_use_id: TOOL, is_error: true,
    content: [{ type: "text", text: "Tool execution was interrupted before completion. Please retry." }],
    processed_at: "2026-10-17T10:57:53.063300Z" },
  { id: "sevt_interrupt_1", type: "user.interrupt", processed_at: "2026-10-17T10:57:53.110517Z" },
  { id: "sevt_thread_idle_2", type: "session.thread_status_idle", processed_at: "2026-10-17T10:57:53.213Z",
    stop_reason: { type: "end_turn" } },
  { id: "sevt_idle_2", type: "session.status_idle", processed_at: "2026-10-17T10:57:53.213Z", stop_reason: { type: "end_turn" } },
];
const RECORD = (status: string, at = "2026-10-17T10:57:54Z") =>
  ({ type: "pearnly.tool_run", id: `ptoolrun_${TOOL}_${status}`, tool_use_id: TOOL, status, processed_at: at });
const card = (state: any) => approvalCard(state.messages.flatMap((m: any) => m.content).find((p: any) => p.toolCallId === TOOL), GATES);
const tool = (state: any) => state.messages.flatMap((m: any) => m.content).find((p: any) => p.toolCallId === TOOL);
const ALLOWED = { phase: "answered", approved: true, failed: false };
const FAILED = { phase: "answered", approved: true, failed: true };
const DENIED = { phase: "answered", approved: false, failed: false };
const CANCELLED = { phase: "cancelled" };
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class Round11ACardTests(unittest.TestCase):
    """第 11 轮 A 段：本站拦下的卡片最后的样子以本站执行记录为准（pearnly.tool_run）。A1 没答就停止写成「允许 · 失败」；
    A2 点「允许」后马上停止，本站执行成功，卡片却写「允许 · 失败」；A4 别的设备已停止，断网的手机联网后点「允许」，两端写
    「允许 · 失败」、服务端回 500。夹具用测试窗口抓的真实事件（REAL_A）。"""
    STUBS = {"cc-ma-setup": MA_SETUP, "socket.io-client": SOCKET_STUB}

    def run_pure(self, body):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n'
               'import { applyEvent, gateCustomTool, project, restoreGates } from "@/lib/managed-agents/reducer";\n'
               'import { approvalCard } from "@/lib/approval-card";\n'
               + REAL_A + body + "\n(globalThis as any).done();\n")

    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\nimport assert from "node:assert/strict";\n'
               + body.split("\n---\n", 1)[0] + "\nconst g = globalThis as any;\n" + FAKE_SITE + REAL_A
               + body.split("\n---\n", 1)[1] + "\ng.done();\n", stubs=self.STUBS)

    def test_a1_unanswered_then_stop_reads_cancelled_live_reopened_and_after_the_record(self):
        self.run_pure(r"""
// 本页：卡片出来（问句向服务端要来），没答，按停止：事件一条条到
let live = gateCustomTool(project(HEAD as any), TOOL, ASK);
assert.deepEqual(card(live), { phase: "ask" });
assert.deepEqual(live.pendingApprovals, [TOOL]);
live = applyEvent(live, STOP[0] as any);                 // 平台的中断结果先到：不是点了「允许」
assert.deepEqual(card(live), CANCELLED);
assert.equal(tool(live).approval, undefined);
assert.deepEqual([live.pendingApprovals, live.pendingCustomTools, live.parkedIds], [[], [], []]);
for (const event of STOP.slice(1)) live = applyEvent(live, event as any);
assert.deepEqual(card(live), CANCELLED);
assert.equal(live.isRunning, false);
live = applyEvent(live, RECORD("cancelled") as any);     // 本站作废的记录（cancel_waiting 推来）
assert.deepEqual(card(live), CANCELLED);
assert.equal(gateCustomTool(live, TOOL, ASK), live);      // 晚到的问句不放回按钮
assert.equal(restoreGates(live, [TOOL], "x"), live);      // 确认没送到也不放回
// 重开对话：执行记录随历史接在这页最后；记录没到时也一样
assert.deepEqual(card(project([...HEAD, ...STOP, RECORD("cancelled")] as any)), CANCELLED);
assert.deepEqual(card(project([...HEAD, ...STOP] as any)), CANCELLED);
// 重开时那一步还在等问句（页面要去要）：中断结果之后不再欠
assert.deepEqual(project([...HEAD, STOP[0]] as any).pendingCustomTools, []);
""")

    def test_a2_allowed_then_stop_reads_allowed_whenever_the_record_lands(self):
        self.run_pure(r"""
const clicked = () => {
  let state = gateCustomTool(project(HEAD as any), TOOL, ASK);
  state = applyEvent(state, { type: "user.tool_confirmation", id: "optimistic-confirm-1", tool_use_id: TOOL, result: "allow",
    processed_at: "2026-10-17T10:57:52.900Z" } as any);
  return applyEvent(state, { type: "pearnly.answer", id: `panswer_${TOOL}`, tool_use_id: TOOL, result: "allow",
    processed_at: "2026-10-17T10:57:52.950Z" } as any);
};
// 记录在中断结果之后到：等记录时照「允许」，不写「失败」
let after = clicked();
for (const event of STOP) after = applyEvent(after, event as any);
assert.equal(card(after)?.phase, "answered");
assert.equal((card(after) as any).approved, true);
assert.equal((card(after) as any).failed, false);
after = applyEvent(after, RECORD("done") as any);
assert.deepEqual(card(after), ALLOWED);
assert.equal(tool(after).approval.ask, ASK.ask);          // 点完的卡片照写问话
// 记录在中断结果之前到
let before = applyEvent(clicked(), RECORD("done", "2026-10-17T10:57:53.000Z") as any);
assert.deepEqual(card(before), ALLOWED);
for (const event of STOP) before = applyEvent(before, event as any);
assert.deepEqual(card(before), ALLOWED);
assert.equal(tool(before).run, "done");
// 重开（另一台设备、刷新）：历史里没有点击记录，执行记录随历史到
assert.deepEqual(card(project([...HEAD, ...STOP, RECORD("done")] as any)), ALLOWED);
// 执行出错的才写「允许 · 失败」（本页、重开都一样）
let failed = clicked();
for (const event of [...STOP, RECORD("error")]) failed = applyEvent(failed, event as any);
assert.deepEqual(card(failed), FAILED);
assert.deepEqual(card(project([...HEAD, ...STOP, RECORD("error")] as any)), FAILED);
// 执行中（记录 running）：「允许」，淡一档；晚到、读得早的旧状态不把已定下的改回去
assert.deepEqual(card(applyEvent(clicked(), RECORD("running") as any)), { ...ALLOWED, sending: true });
assert.deepEqual(card(applyEvent(after, RECORD("running") as any)), ALLOWED);
assert.deepEqual(card(applyEvent(after, RECORD("awaiting_approval") as any)), ALLOWED);
// 记录先于调用本身到（推送比历史先折进来）：建这一步时补上
assert.deepEqual(card(project([RECORD("done"), ...HEAD, ...STOP] as any)), ALLOWED);
""")

    def test_another_device_polling_gets_the_record_that_settles_after_the_turn_ended(self):
        """审查（第 11 轮 A 段）：另一台设备 B 没点卡片、推送漏收，只靠补拉。B 读到的页里是中断结果和执行记录 running（工具还在
        本站执行），随后 user.interrupt、end_turn；记录在引擎收尾之后才改成 done / error，由下一次补拉（服务端按锚点之后改过的记录
        带回，`sessions._run_rows` 的 since）送来。卡片从淡色「允许」定成「允许」或「允许 · 失败」，不停在淡色。"""
        self.run_pure(r"""
const later = (status: string) => RECORD(status, "2026-10-17T10:57:58Z");
const polled = () => {
  let b = gateCustomTool(project(HEAD as any), TOOL, ASK);     // B 上卡片还在问（没点）
  assert.deepEqual(card(b), { phase: "ask" });
  b = applyEvent(b, STOP[0] as any);                            // 补拉第 1 页：平台代发的中断结果
  b = applyEvent(b, RECORD("running", "2026-10-17T10:57:52Z") as any);  // 同一页末尾：本站执行记录 running
  assert.deepEqual(card(b), { ...ALLOWED, sending: true });     // 别处点了「允许」、正在执行：淡色「允许」
  for (const event of STOP.slice(1)) b = applyEvent(b, event as any);  // user.interrupt、end_turn
  assert.deepEqual(card(b), { ...ALLOWED, sending: true });
  assert.equal(b.isRunning, false);
  assert.deepEqual([b.pendingApprovals, b.pendingCustomTools], [[], []]);
  return b;
};
// 补拉第 2 页（锚点 end_turn，引擎没有新事件）：只带回 done 的记录
let done = applyEvent(polled(), later("done") as any);
assert.deepEqual(card(done), ALLOWED);
assert.equal(tool(done).run, "done");
// 同一条记录重复带回（锚点前推的余量）：没有副作用
assert.equal(applyEvent(done, later("done") as any).messages, done.messages);
// 执行出错：「允许 · 失败」
assert.deepEqual(card(applyEvent(polled(), later("error") as any)), FAILED);
// 重开时一样
assert.deepEqual(card(project([...HEAD, STOP[0], RECORD("running", "2026-10-17T10:57:52Z"), ...STOP.slice(1), later("error")] as any)),
                 FAILED);
""")

    def test_denied_and_a_stale_card_settled_by_the_record(self):
        self.run_pure(r"""
// 拒绝：本站交回的拒绝结果 + 执行记录 denied →「拒绝」（本页、重开）
const denyResult = { type: "user.custom_tool_result", id: "sevt_deny_res", custom_tool_use_id: TOOL, is_error: false,
  content: [{ type: "text", text: JSON.stringify({ status: "denied", message: "The user did not allow this change." }) }],
  processed_at: "2026-10-17T10:57:50Z" };
let denied = gateCustomTool(project(HEAD as any), TOOL, ASK);
denied = applyEvent(denied, { type: "user.tool_confirmation", id: "optimistic-confirm-2", tool_use_id: TOOL, result: "deny",
  processed_at: "2026-10-17T10:57:49Z" } as any);
denied = applyEvent(denied, RECORD("denied") as any);
assert.deepEqual(card(denied), DENIED);
assert.deepEqual(card(project([...HEAD, denyResult, RECORD("denied")] as any)), DENIED);
// A4：断网的手机上卡片还在问（别的设备已停止，事件全错过）；收到作废记录 → 「已取消」、没有按钮、不再等这边点
let stale = gateCustomTool(project(HEAD as any), TOOL, ASK);
stale = applyEvent(stale, RECORD("cancelled") as any);
assert.deepEqual(card(stale), CANCELLED);
assert.equal(tool(stale).approval, undefined);
assert.deepEqual([stale.pendingApprovals, stale.parkedIds], [[], []]);
// 手机先乐观点了「允许」，confirm 回 cancelled（页面照它折一条记录）：同样「已取消」
let clicked = gateCustomTool(project(HEAD as any), TOOL, ASK);
clicked = applyEvent(clicked, { type: "user.tool_confirmation", id: "optimistic-confirm-3", tool_use_id: TOOL, result: "allow",
  processed_at: "2026-10-17T10:58:30Z" } as any);
clicked = applyEvent(clicked, RECORD("cancelled", null as any) as any);
assert.deepEqual(card(clicked), CANCELLED);
assert.equal(restoreGates(clicked, [TOOL], "x"), clicked);
// 之后补齐的事件（中断结果、停止）不改它
for (const event of STOP) clicked = applyEvent(clicked, event as any);
assert.deepEqual(card(clicked), CANCELLED);
// 出卡片前就核出要拒绝的（结果带没问过用户的标记）：执行记录是 error，照旧不是卡片
const notAsked = project([...HEAD, { type: "user.custom_tool_result", id: "sevt_na", custom_tool_use_id: TOOL, is_error: true,
  content: [{ type: "text", text: JSON.stringify({ status: "not_saved", approval: "not_asked" }) }],
  processed_at: "2026-10-17T10:57:46Z" }, RECORD("error")] as any);
assert.equal(card(notAsked), null);
""")

    def test_tools_that_are_not_this_sites_cards_keep_their_old_behaviour(self):
        self.run_pure(r"""
const parts = (state: any) => state.messages.flatMap((m: any) => m.content).filter((p: any) => p.type === "tool-call");
const at = (s: number) => `2026-10-17T11:00:${String(s).padStart(2, "0")}.000000Z`;
// 内置工具：要确认的，结果到了就是放行过（确认的回声晚到也一样）；不进执行记录
const builtin = project([
  { id: "b1", type: "agent.tool_use", name: "bash", input: { command: "ls" }, processed_at: at(1) },
  { id: "bi", type: "session.status_idle", processed_at: at(2), stop_reason: { type: "requires_action", event_ids: ["b1"] } },
  { id: "br", type: "agent.tool_result", tool_use_id: "b1", is_error: false, content: [{ type: "text", text: "a.txt" }], processed_at: at(3) },
] as any);
assert.equal(parts(builtin)[0].approval.approved, true);
assert.equal(parts(builtin)[0].interrupted, undefined);
assert.equal(parts(builtin)[0].result.text, "a.txt");
// MCP：同上
const mcp = project([
  { id: "m1", type: "agent.mcp_tool_use", name: "search", mcp_server_name: "docs", input: {}, processed_at: at(1) },
  { id: "mi", type: "session.status_idle", processed_at: at(2), stop_reason: { type: "requires_action", event_ids: ["m1"] } },
  { id: "mr", type: "agent.mcp_tool_result", mcp_tool_use_id: "m1", is_error: false, content: [{ type: "text", text: "ok" }], processed_at: at(3) },
] as any);
assert.equal(parts(mcp)[0].approval.approved, true);
// 内置工具出错的结果照旧是它的结果（不是中断标记）
const builtinErr = project([
  { id: "b2", type: "agent.tool_use", name: "bash", input: {}, processed_at: at(1) },
  { id: "br2", type: "agent.tool_result", tool_use_id: "b2", is_error: true, content: [{ type: "text", text: "exit 1" }], processed_at: at(2) },
] as any);
assert.equal(parts(builtinErr)[0].isError, true);
assert.equal(parts(builtinErr)[0].interrupted, undefined);
// 不要「允许」的自定义工具：本站结果照常；被停止时那条中断结果留给步骤行看（出错），不是卡片
const plain = project([
  { id: "c1", type: "agent.custom_tool_use", name: "cowork_list_documents", input: {}, processed_at: at(1) },
  { id: "ci", type: "session.status_idle", processed_at: at(2), stop_reason: { type: "requires_action", event_ids: ["c1"] } },
  { id: "cr", type: "agent.tool_result", tool_use_id: "c1", is_error: true,
    content: [{ type: "text", text: "Tool execution was interrupted before completion. Please retry." }], processed_at: at(3) },
  { id: "cx", type: "user.interrupt", processed_at: at(4) },
  { type: "pearnly.tool_run", id: "ptoolrun_c1_done", tool_use_id: "c1", status: "done", processed_at: at(5) },
] as any);
assert.equal(approvalCard(parts(plain)[0], GATES), null);
assert.equal(parts(plain)[0].isError, true);
const plainDone = project([
  { id: "c2", type: "agent.custom_tool_use", name: "cowork_list_documents", input: {}, processed_at: at(1) },
  { id: "cr2", type: "user.custom_tool_result", custom_tool_use_id: "c2", is_error: false, content: [{ type: "text", text: "[]" }], processed_at: at(2) },
] as any);
assert.equal(approvalCard(parts(plainDone)[0], GATES), null);
assert.equal(parts(plainDone)[0].result.text, "[]");
""")

    def test_a4_a_stale_allow_gets_the_servers_state_not_an_error(self):
        # 断网的手机联网后点了「允许」：服务端不转给引擎，回此刻的状态（cancelled）。卡片当场写「已取消」、没有按钮、不出错误行；
        # 执行完的回 done 写「允许」；真正的错误（网络）照旧放回按钮、写原因
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { approvalCard } from "@/lib/approval-card";
---
const respond = g.respond;
let confirmReply: any = { confirmed: 1, runs: { [TOOL]: "cancelled" } };
g.respond = async (method: string, args: any, body: any) => {
  if (method === "confirm") {
    if (confirmReply instanceof Error) throw confirmReply;
    return ok(confirmReply);
  }
  return respond(method, args, body);
};
const open = async (sid: string) => {
  site.log.length = 0;
  site.log.push(...HEAD);
  site.runs = { [TOOL]: "awaiting_approval" };
  site.toolReply = async () => ({ status: "awaiting_approval", ...ASK });
  const c = getSessionController(sid);
  c.attach();
  await g.sleep(30);
  assert.deepEqual(card(c.getSnapshot()), { phase: "ask" });
  return c;
};
const phone = await open("sesn_a4_phone");
phone.respondToApproval(TOOL, true);
await g.sleep(300);
let snap: any = phone.getSnapshot();
assert.deepEqual(card(snap), CANCELLED);
assert.equal(tool(snap).approval, undefined);
assert.deepEqual(snap.pendingApprovals, []);
assert.equal(snap.transportError, null);
const confirmCall = g.calls.filter((c: any) => c.method === "confirm").at(-1);
assert.deepEqual(JSON.parse(confirmCall.body.confirmations), [{ tool_use_id: TOOL, result: "allow" }]);
// 服务端回 done：「允许」
confirmReply = { confirmed: 1, runs: { [TOOL]: "done" } };
const other = await open("sesn_a4_other");
other.respondToApproval(TOOL, true);
await g.sleep(300);
assert.deepEqual(card(other.getSnapshot()), ALLOWED);
// 旧服务端（没有 runs）：照旧
confirmReply = { confirmed: 1 };
const old = await open("sesn_a4_old");
old.respondToApproval(TOOL, true);
await g.sleep(300);
assert.equal(tool(old.getSnapshot()).approval.approved, true);
// 真正的错误（网络）：按钮放回，按钮上方写原因
confirmReply = new TypeError("Failed to fetch");
const offline = await open("sesn_a4_offline");
offline.respondToApproval(TOOL, true);
await g.sleep(300);
snap = offline.getSnapshot();
assert.deepEqual(card(snap), { phase: "ask" });
assert.ok(tool(snap).approval.failure);
""")

    def test_the_pushed_record_settles_the_card_on_every_open_device(self):
        # 本站执行记录推来（toolrun._finish / decide 拒绝 / cancel_waiting）：开着这段对话的设备当场定下，不等补拉
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { dispatch } from "@/lib/managed-agents/realtime";
import { approvalCard } from "@/lib/approval-card";
---
const S = "sesn_01X2K4vJBNvR361Cvjxap2Nw";
site.log.push(...HEAD);
site.runs = { [TOOL]: "awaiting_approval" };
site.toolReply = async () => ({ status: "awaiting_approval", ...ASK });
const c = getSessionController(S);
c.attach();
await g.sleep(30);
assert.deepEqual(card(c.getSnapshot()), { phase: "ask" });
const reads = eventsCalls(S).length;
// 别的设备上点停止：平台的中断结果、停止、作废记录依次推来
for (const event of STOP) dispatch({ session_id: S, event: event as any });
dispatch({ session_id: S, event: RECORD("cancelled") as any });
await g.sleep(0);
assert.equal(eventsCalls(S).length, reads);              // 没靠补拉
assert.deepEqual(card(c.getSnapshot()), CANCELLED);
assert.deepEqual(c.getSnapshot().pendingApprovals, []);
// 重开（整段重放，执行记录接在这页最后，同服务端 sessions.events）
site.log.push(...STOP, RECORD("done"));
const again = getSessionController("sesn_reopen_a2");
again.attach();
await g.sleep(30);
assert.deepEqual(card(again.getSnapshot()), ALLOWED);
""")


class Round11ButtonsLikeClaudeTests(unittest.TestCase):
    """第 11 轮：按钮三端照 Claude（排查报告 G1–G15，用户 10-17）。电脑端已和 Claude 一致的值不动，手机档另加两条
    （手机变体、粗指针且不是桌面窗口）；数值见 docs/work/2026-10-16-cc-shell-design.md 3.18。"""

    def setUp(self):
        self.css = read("globals.css")

    def test_phone_tap_sizes_for_shared_controls(self):
        css = self.css
        # G2 / G3：文字按钮、输入框、图标按钮手机 44，电脑仍 32（--cc-control 不改）；电脑对话顶栏、桌面标题栏里的图标按钮不跟
        self.assertIn("--cc-control: var(--code-control);", css)
        self.assertIn(':root[data-cc-touch] :is(.cc-btn, .cc-input) { height: var(--cc-tap); }', css)
        # 手机变体与粗指针两份合成一份触屏规则（V4：以除开电脑顶栏、桌面标题栏的那份为准）
        self.assertIn(':root[data-cc-touch] .cc-icon-btn:not(:is(.cc-topbar, .cc-titlebar) *) { width: var(--cc-tap); height: var(--cc-tap); }', css)
        self.assertNotIn(':root[data-cc-variant="phone"] .cc-icon-btn {', css)
        self.assertIn(':root[data-cc-touch] .cc-tap { min-height: var(--cc-tap); min-width: var(--cc-tap); }', css)
        # 组件里不再用 h-8 把 .cc-btn / .cc-input 钉在 32
        self.assertNotIn("cc-btn h-8", read("components/scheduled-tasks.tsx"))
        self.assertNotIn("cc-input h-8", read("components/assistant-ui/projects.tsx"))
        # G6：抽屉组头操作与待处理数 44、不留缝；G7：「显示更多」跟行高
        self.assertIn(".cc-drawer .cc-group-actions { gap: 0; padding-inline-end: 0; }", css)
        self.assertIn(".cc-drawer .cc-group-actions .cc-row-btn { width: var(--cc-tap); height: var(--cc-tap); }", css)
        self.assertIn("min-height: var(--cc-row-h); padding-inline:", block(css, "  .cc-show-more {"))
        self.assertIn(".cc-panel-card-body .cc-show-more { min-height: var(--cc-panel-row); }", css)
        # G5：会话栏手机档（卡头、行尾小按钮 44），电脑 26 / 20 不变
        self.assertIn(':root[data-cc-touch] .cc-panel { --cc-panel-head: var(--cc-tap); --cc-panel-btn: var(--cc-tap); }', css)
        self.assertIn("--cc-panel-head: var(--code-panel-head);", css)
        # G8 / G10：手机胶囊 36（点按区补到 44）、列表页小按钮 44
        self.assertIn("--cc-chin-btn-phone: 2.25rem;", css)
        self.assertIn("inset: calc((var(--cc-chin-btn-phone) - var(--cc-tap)) / 2) 0;", css)
        self.assertIn("height: var(--cc-tap); padding-inline: var(--cc-btn-sm-px-phone); border-radius: var(--cc-radius-full);", css)
        self.assertIn("--cc-btn-sm-px-phone: 14px;", css)

    def test_row_more_only_takes_taps_where_it_can_hover(self):
        # G13：手机、触屏靠长按出菜单，透明的「···」不接点按；能悬停的设备悬停这一行才接
        self.assertIn('[data-slot="aui_thread-list-item-more"]:not([data-state="open"], :focus-visible) { pointer-events: none; }', self.css)
        self.assertIn('@media (hover: hover) {\n  [data-slot="aui_thread-list-item"]:hover [data-slot="aui_thread-list-item-more"] { pointer-events: auto; }\n}', self.css)

    def test_mobile_chat_title_has_two_lines_and_no_click(self):
        # M8：手机对话页顶栏中间两行标题（对话名 16 字重 600、公司名 13 淡色），屏幕居中，不可点
        app = read("assistant.tsx")
        self.assertIn("{inChat ? <MobileChatTitle /> : <span />}", app)
        title = read("components/assistant-ui/projects.tsx").split("export const MobileChatTitle: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn('<Marquee className="cc-mtitle-name">{title || t("untitled")}</Marquee>', title)
        self.assertIn('{kind && project && company && <Marquee className="cc-mtitle-sub">{company}</Marquee>}', title)
        self.assertNotIn("onClick", title)
        # 第 11 轮审查：两侧同宽（取右上胶囊宽），标题长了也在屏幕正中（原来 1fr auto 1fr 时长标题偏左约 20）
        self.assertIn("grid-template-columns: var(--cc-mpill-w) minmax(0, 1fr) var(--cc-mpill-w);", block(self.css, "  .cc-mtopbar {"))
        self.assertNotIn("grid-template-columns: 1fr auto 1fr", block(self.css, "  .cc-mtopbar {"))
        self.assertIn("--cc-mpill-w: calc(2 * var(--cc-mpill-btn) + 2 * var(--cc-space-4) + 2 * var(--cc-hairline));", self.css)
        self.assertIn("width: var(--cc-mpill-btn); height: 100%;", self.css)
        self.assertIn("--cc-mtitle-font: var(--code-text-prose);", self.css)
        self.assertIn("pointer-events: none;", block(self.css, "  .cc-mtitle {"))

    def test_composer_attachment_tile_like_claude(self):
        # M7：输入框里的附件方块 128（手机 72），图片整张放进方块，与输入的字对齐；移除 × 20，手机点按区 44
        css = self.css
        self.assertIn("--cc-attach-tile: 8rem;", css)
        self.assertIn("--cc-attach-tile-phone: 4.5rem;", css)
        self.assertIn("width: var(--cc-attach-tile); height: var(--cc-attach-tile);", block(css, "  .cc-compose-tile {"))
        # 图片四周留约 5 的边（7.webp），整张放进去不裁
        self.assertIn(".cc-compose-tile > img { width: 100%; height: 100%; padding: var(--cc-compose-img-inset); object-fit: contain; }", css)
        self.assertIn("--cc-compose-img-inset: 0.3125rem;", css)
        tile = read("components/assistant-ui/attachment.tsx")
        self.assertIn('className="aui-attachment-tile cc-compose-tile"', tile)
        self.assertIn("cc-compose-x", tile)
        self.assertIn("size-(--cc-control-2xs) items-center justify-center rounded-full", tile)  # 右上 20 的圆形移除
        # 位置写在 globals.css（手机要挪到外角，工具类盖不住）：组件上不再写 end-1.5 top-1.5
        self.assertNotIn("end-1.5 top-1.5", tile)
        self.assertIn(".cc-compose-x { top: var(--cc-space-6); inset-inline-end: var(--cc-space-6); }", css)
        self.assertNotIn("size-14", tile)
        # 图片预览的关闭是自己的圆钮（电脑 32、手机 44）
        self.assertIn("showCloseButton={false}", tile)
        self.assertIn('<DialogClose className="cc-icon-btn cc-tap cc-preview-close"', tile)

    def test_keyboard_composer_and_company_composer_on_phone(self):
        # M9：键盘弹出、输入框展开时离屏边 8
        # 第 11 轮复量只借 8 时是 9（页面卡片的细线）：连这条细线一起借
        self.assertIn("width: calc(100% + var(--cc-keyboard-edge) * 2 + 2 * var(--cc-hairline)); margin-inline: calc(var(--cc-keyboard-edge) * -1 - var(--cc-hairline));",
                      block(self.css, ':root[data-cc-keyboard] .cc-mmain .cc-composer:not([data-collapsed]) {'))
        # M10：公司页输入框同对话页，没在输入、也没有字时收成一行胶囊
        page = read("components/company-page.tsx")
        self.assertIn('data-collapsed={!typing && !text ? "" : undefined}', page)

    def test_desktop_fixes(self):
        css = self.css
        # M3：桌面标题栏左侧四颗 26、圆角 6、图标 16
        self.assertIn(".cc-titlebar .cc-icon-btn { width: var(--cc-bar-btn); height: var(--cc-bar-btn); border-radius: var(--cc-radius-row-ctl); }", css)
        # M5：「新建」28 槽里 20 的圆
        self.assertIn('<span className="cc-new-dot">', read("components/assistant-ui/thread-list.tsx"))
        # L1：托盘用户按钮 32；L2「需要您的输入」上下 1；L3「更换」「移除」20；L5 候选行同侧栏行
        self.assertIn('className="cc-nav-item h-(--cc-tray-btn) min-w-0 flex-1"', read("assistant.tsx"))
        self.assertIn("padding: var(--cc-needs-input-py) var(--cc-space-6);", block(css, "  .cc-needs-input {"))
        self.assertIn("--cc-needs-input-py: 1px;", css)
        self.assertIn("height: var(--cc-panel-btn); padding: 0 var(--cc-space-6);", block(css, "  .cc-link-btn {"))
        self.assertIn('[data-slot="cc-candidates"] .cc-list-row { min-height: var(--cc-row-h);', css)
        # M11：定时任务行「▶」「···」同侧栏行内按钮
        tasks = read("components/scheduled-tasks.tsx")
        self.assertEqual(tasks.count('className="cc-row-btn cc-tap"'), 2)
        # H21 / H24：筛选 ×、「默认值」折叠标题
        self.assertEqual(tasks.count('className="cc-chip-x"'), 1)
        self.assertIn('<summary className="cc-disclosure">', tasks)
        self.assertIn('className="cc-chip-x"', read("components/pending.tsx"))
        # L10 / H2：知识库编辑与待处理的按钮是卡片按钮
        self.assertIn('<button type="button" className="cc-card-btn" data-primary="" disabled={!canSave}', read("components/company-cards.tsx"))
        # 当场选三颗 + 列表底部的「交给 CC」（第 11 轮 D32：电脑也是 24 的卡片按钮）+ 多电脑分支待选账套行（AccountSetRow）的「确认」「交给 CC」两颗
        self.assertEqual(read("components/pending.tsx").count('className="cc-card-btn self-'), 7)  # 第 3 期提议行展开后的「推送」
        # 设置「电脑」列表每台电脑行尾「···」：电脑 24（.cc-row-btn，标准化 V22；原来手写 28），手机点按区 44（.cc-tap）
        self.assertIn('className="cc-row-btn cc-tap"', read("components/settings.tsx"))
        self.assertIn('<button type="button" className="cc-card-btn self-start" data-slot="cc-hand-to-cc"', read("components/pending.tsx"))
        # D26：触屏平板（网页布局 + 粗指针）对话顶栏 44、顶栏图标钮与侧栏收起钮点按区 44，鼠标与桌面版不跟
        # 触屏判定 B1 起统一成 data-cc-touch（手机变体，或粗指针且不是桌面窗口；lib/variant.ts），网页布局再加 data-cc-variant="web"
        coarse = css.split("触屏平板（网页布局 + 触屏", 1)[1].split("\n\n", 1)[0]
        # 第 11 轮合并审查：顶栏高的变量写在 @layer components 里敌不过不分层的 :root 定义（顶栏仍 32、按钮已 44）——挪到不分层的
        # 触屏变量块里，components 层不再给 --cc-topbar-h 赋值
        unlayered = css.split("\n@layer base {", 1)[0]
        self.assertIn(':root[data-cc-touch][data-cc-variant="web"] { --cc-topbar-h: var(--cc-tap); }', unlayered)
        self.assertNotIn("--cc-topbar-h:", coarse)
        layered = css.split("\n@layer components {", 1)[1]
        self.assertNotRegex(layered, r"--cc-topbar-h\s*:")
        self.assertIn(':root[data-cc-touch][data-cc-variant="web"] .cc-topbar :is(.cc-icon-btn, .cc-row-btn) { width: var(--cc-tap); height: var(--cc-tap); }', coarse)
        self.assertIn(':root[data-cc-touch][data-cc-variant="web"] [data-slot="cc-sidebar-toggle"] { width: var(--cc-tap); height: var(--cc-tap); }', coarse)
        self.assertIn('data-slot="cc-sidebar-toggle"', read("assistant.tsx"))
        # 第 11 轮重测失效追踪 trace：按属性名做键的通配兄弟选择器（[data-slot=…] ~ *）让 body 下任何增删（菜单 portal 挂上卸下）整页
        # 样式失效，A14 收菜单那一帧约 40ms；改成按类名的紧挨兄弟，body 下的常驻节点（拖动层、拖动播报区）也不用 data-slot 标识
        self.assertNotRegex(re.sub(r"/\*.*?\*/", "", css, flags=re.S), r"\[data-slot=[^\]]+\]\s*~\s*\*")
        self.assertIn('.cc-mmain .cc-status + .cc-thread-slot [data-slot="aui_thread-viewport"] { padding-top: 0; }', css)
        self.assertIn('<div data-slot="cc-status" className="cc-status ', read("assistant.tsx"))
        self.assertIn('<div className="cc-thread-slot min-h-0 flex-1">', read("assistant.tsx"))
        drag = read("components/ui/drag.tsx")
        self.assertNotIn('data-slot="cc-move-live"', drag)
        self.assertNotIn('layer.dataset.slot', drag)


class Round11ReviewTests(unittest.TestCase):
    """第 11 轮三段合起来的审查意见（按钮段复量 + 行为审查）里成立、已修的几条。"""

    css = read("globals.css")

    def test_phone_attachment_remove_sits_on_the_outer_corner_and_leaves_the_middle_to_preview(self):
        # 审查（复量 blocker）：手机 72 方块上 × 的点按区往四周扩 12，盖住了方块中心（点中间删掉了附件）；而且附件一行没有上内边距，
        # 点按区被滚动容器裁成 38×44。现在 × 挪到方块右上外角（伸出 6），点按区只往外 6、往左下 18：44×44，覆盖方块的
        # x 40–72、y 0–32，中心 (36, 36) 不在里面；附件一行上、右各留 12，正好装下伸出去的部分
        css = self.css
        # 手机变体与粗指针（不是桌面窗口）合成一份 :root[data-cc-touch]（标准化记录 B1）；伸出 6、往左下扩 18（= 44 − 20 − 6）
        self.assertIn(":root[data-cc-touch] .cc-compose-x { top: calc(var(--cc-space-6) * -1); inset-inline-end: calc(var(--cc-space-6) * -1); }", css)
        after = block(css, ':root[data-cc-touch] .cc-compose-x::after {')
        self.assertIn("inset-block: calc(var(--cc-space-6) * -1) calc((var(--cc-tap) - var(--cc-control-2xs) - var(--cc-space-6)) * -1);", after)
        self.assertIn("inset-inline: calc((var(--cc-tap) - var(--cc-control-2xs) - var(--cc-space-6)) * -1) calc(var(--cc-space-6) * -1);", after)
        self.assertNotIn("inset: -0.75rem", css.split(".cc-compose-x {", 1)[1].split("/* 列表页", 1)[0])
        # 点按区几何：× 20、外角伸出 6；往上 6、往右 6、往下 18、往左 18
        tile, x, out = 72, 20, 6
        left, right = tile + out - x - 18, tile + out + 6
        top, bottom = -out - 6, -out + x + 18
        self.assertEqual((right - left, bottom - top), (44, 44))
        self.assertFalse(left <= tile / 2 <= right and top <= tile / 2 <= bottom)   # 方块中心不归 ×
        self.assertEqual((-top, right - tile), (12, 12))                            # 伸出去的正好是留出的 12
        self.assertIn(':root[data-cc-touch] .cc-composer-inline > .aui-composer-attachments { padding-block-start: var(--cc-space-12); padding-inline-end: var(--cc-space-12); }', css)
        # 触屏这一条要排在电脑附件一行的内边距之后（同样的优先级，后写的生效）
        self.assertLess(css.index(':root:not([data-cc-variant="phone"]) .cc-composer-inline > .aui-composer-attachments {'),
                        css.index(':root[data-cc-touch] .cc-composer-inline > .aui-composer-attachments {'))

    def test_welcome_chips_are_spaced_8_on_phones(self):
        # 审查：G8 写了问候下胶囊间距 4 → 8（手机胶囊 36 带底色后 4 显挤），漏了
        self.assertIn(':root[data-cc-touch] .cc-welcome-filing { gap: var(--cc-space-8); }', self.css)

    def test_company_composer_folds_on_android_back(self):
        # 审查：公司页输入框加了收成胶囊，却没接安卓返回键收键盘（KEYBOARD_CLOSED），返回后仍是展开的
        page = read("components/company-page.tsx")
        composer = page[page.index("const CompanyComposer: FC"):]
        self.assertIn('import { KEYBOARD_CLOSED } from "@/lib/viewport";', page)
        self.assertIn("emptyNow.current = !text;", composer)
        self.assertIn("if (emptyNow.current && document.activeElement === box.current) box.current?.blur();", composer)
        self.assertIn("window.addEventListener(KEYBOARD_CLOSED, closed);", composer)
        self.assertIn("return () => window.removeEventListener(KEYBOARD_CLOSED, closed);", composer)

    def test_scheduled_task_target_cards_are_tappable_edge_to_edge(self):
        # 审查（复量附录 #83）：写入目标卡（MR.ERP / Express）的内边距在卡片上，按钮只有 21–38 高；挪进按钮，手机至少 44
        tasks = read("components/scheduled-tasks.tsx")
        self.assertIn('<div key={a.adapter} className="cc-card flex items-center justify-between gap-(--cc-space-8)" data-selected={picked || undefined}>', tasks)
        self.assertIn('"cc-tap flex min-w-0 flex-1 flex-col items-start justify-center self-stretch rounded-md px-(--cc-space-12) py-(--cc-target-card-py) text-start disabled:cursor-default",', tasks)
        self.assertIn('<button type="button" className="cc-btn me-(--cc-space-12)" onClick={() => setConnectOpen(a.adapter)}>', tasks)
        self.assertNotIn("cc-card flex items-center justify-between gap-(--cc-space-8) px-", tasks)


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class Round11EFTests(unittest.TestCase):
    """测试窗口第 11 轮 E / F 段（lab 9798f54e）：E37 删除确认读屏念两遍标题、打开时焦点在 ×、关掉后焦点掉到页面最外层；
    F40「记住的选择」出错的原因写在整张卡最底下（右侧栏没滚动时在视区外）。"""
    STUBS = {"socket.io-client": "export function io() { return { on() { return this; }, off() { return this; }, connected: true }; }",
             "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;'}

    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\n' + COMPANY_RENDER + body + "\n(globalThis as any).done();\n",
               stubs={**self.STUBS, "cc-ma-setup": MA_SETUP})

    def test_delete_confirm_says_its_title_once_and_gives_focus_back(self):
        self.run_page(r"""
import { backTarget, rowAfterDelete } from "@/components/delete-chats";
// 极简的元素：closest / querySelector 按选择器认几个固定的 data-slot 与 role
const el = (attrs: Record<string, string>, parent: any = null, children: any[] = []): any => {
  const node: any = { attrs, parent, children, dataset: {} as Record<string, string>, isConnected: true,
    getAttribute: (k: string) => attrs[k] ?? null,
    matches: (sel: string) => sel.split(",").some((one) => {
      const m = one.trim().match(/^\[([a-z-]+)(?:="([^"]*)")?\]$/);
      return !!m && (m[2] === undefined ? m[1] in attrs : attrs[m[1]] === m[2]);
    }),
    closest: (sel: string) => { for (let n = node; n; n = n.parent) if (n.matches(sel)) return n; return null; },
    querySelector: (sel: string) => { for (const c of node.children) { if (c.matches(sel)) return c; const d = c.querySelector(sel); if (d) return d; } return null; },
  };
  if (attrs["data-row-id"]) node.dataset.rowId = attrs["data-row-id"];
  children.forEach((c) => { c.parent = node; });
  return node;
};
const byId: Record<string, any> = {};
g.document.getElementById = (id: string) => byId[id] ?? null;
g.document.body = el({});
// 侧栏行的菜单：触发钮是行尾「···」，回到行本身（行的开关按钮，键盘停的地方）
const rowTrigger = el({ "data-slot": "aui_thread-list-item-trigger" });
const more = el({ "data-slot": "aui_thread-list-item-more", id: "radix-1" });
const row = el({ "data-slot": "aui_thread-list-item", "data-row-id": "t2" }, null, [rowTrigger, more]);
byId["radix-1"] = more;
const item = el({ role: "menuitem" }, el({ role: "menu", "aria-labelledby": "radix-1" }));
assert.deepEqual(backTarget(item), { el: rowTrigger, row: "t2" });
assert.equal(row.querySelector('[data-slot="aui_thread-list-item-trigger"]'), rowTrigger);
// 标题栏「···」、手机「…」：回到那个按钮
const titleMore = el({ id: "radix-9", "aria-haspopup": "menu" });
byId["radix-9"] = titleMore;
assert.deepEqual(backTarget(el({ role: "menuitem" }, el({ role: "menu", "aria-labelledby": "radix-9" }))), { el: titleMore, row: "" });
// 不在菜单里（直接在行上按键）：就是当时的焦点；焦点在页面最外层：没有可回的
assert.deepEqual(backTarget(rowTrigger), { el: rowTrigger, row: "t2" });
assert.deepEqual(backTarget(g.document.body), { el: null, row: "" });
// 删了以后：侧栏里删掉的之后第一条，没有就之前最近一条；侧栏里看不到它们回 ""
assert.equal(rowAfterDelete(["a", "b", "c", "d"], ["b"]), "c");
assert.equal(rowAfterDelete(["a", "b", "c", "d"], ["b", "c"]), "d");
assert.equal(rowAfterDelete(["a", "b", "c", "d"], ["c", "d"]), "b");
assert.equal(rowAfterDelete(["a", "b", "c", "d"], ["d", "b"]), "c");
assert.equal(rowAfterDelete(["a"], ["a"]), "");
assert.equal(rowAfterDelete([], ["a"]), "");
""")
        host = read("components/delete-chats.tsx")
        # 打开时焦点在「取消」；关掉（取消 / Esc / × / 遮罩）回到打开它的地方；删了去下一条（当前对话被删进输入框）
        self.assertIn("onOpenAutoFocus={(event) => {\n            event.preventDefault();\n            cancel.current?.focus();", host)
        self.assertIn('<button ref={cancel} type="button" className="cc-btn" data-slot="cc-delete-chats-no">{t("cancel")}</button>', host)
        close = host.split("onCloseAutoFocus={(event) => {", 1)[1].split("}}", 1)[0]
        self.assertIn("event.preventDefault();", close)
        self.assertIn("else focusBack(asked.current?.back);", close)
        ask = host.split("export function askDelete(", 1)[1].split("\n}\n", 1)[0]
        self.assertLess(ask.index("const back = backTarget();"), ask.index("requestAnimationFrame("))   # 菜单关之前记下
        confirm = host.split("const confirm = () => {", 1)[1].split("\n  };\n", 1)[0]
        self.assertIn("after.current = focusAfterDelete(current.ids, current.back, () => aui.threads().getState().mainThreadId);", confirm)
        self.assertLess(confirm.index("focusAfterDelete("), confirm.index("current.run();"))   # 删之前算好下一条
        self.assertLess(confirm.index("close();"), confirm.index("current.run();"))
        self.assertIn("askDelete([item.getState().id], () => item.delete());", host)

    def test_focus_after_a_delete_lands_on_a_row_or_the_composer(self):
        # 审查（第 11 轮 E37 修复）：在定时任务 / 公司 / 主档 / 待处理页删掉正开着的对话，这些页没有输入框，焦点掉到页面最外层；
        # 触屏上删了正开着的对话程序聚焦输入框会弹键盘（手机打开不弹键盘）。现在：对话页（有输入框）、非触屏删了正开着的才进输入框，
        # 其余去侧栏下一条（没有就上一条），侧栏里看不到就回打开它的地方
        self.run_page(r"""
import { focusAfterDelete } from "@/components/delete-chats";
g.CSS = { escape: (s: string) => s };
g.requestAnimationFrame = (fn: () => void) => setTimeout(fn, 1);
let touch = false;
g.matchMedia = (q: string) => ({ matches: q === "(hover: none)" && touch });
const node = (name: string) => ({ name, isConnected: true, focus() { g.document.activeElement = this; } });
let rows: string[] = [];
let sidebarShown = true;
let composer: any = null;
const triggers: Record<string, any> = {};
const sidebar = { closest: () => null, querySelectorAll: () => rows.map((id) => ({ dataset: { rowId: id } })) };
g.document.querySelector = (sel: string) => {
  if (sel === '[data-slot="aui_thread-list-root"]') return sidebarShown ? sidebar : null;
  if (sel === "textarea.aui-composer-input") return composer;
  const m = sel.match(/^\[data-slot="aui_thread-list-item"\]\[data-row-id="([^"]+)"\] \[data-slot="aui_thread-list-item-trigger"\]$/);
  return m && sidebarShown && rows.includes(m[1]) ? (triggers[m[1]] ??= node(`row:${m[1]}`)) : null;
};
// 组件库删正开着的：先切到新对话（main 变了），再从侧栏移走
let main = "";
const run = async (ids: string[], back: any, opts: { switchTo?: string; composerAfter?: any } = {}) => {
  g.document.activeElement = g.document.body;
  const go = focusAfterDelete(ids, back, () => main);
  go();
  await g.sleep(3);
  if (opts.switchTo !== undefined) main = opts.switchTo;
  if ("composerAfter" in opts) composer = opts.composerAfter;
  rows = rows.filter((id) => !ids.includes(id));
  await g.sleep(30);
  return g.document.activeElement?.name ?? "body";
};
const more = node("title-more");
const input = node("composer");

// ① 电脑、定时任务页（没有输入框）删正开着的 b：去侧栏下一条 c，不掉到 body
rows = ["a", "b", "c"]; main = "b"; composer = null;
assert.equal(await run(["b"], { el: more, row: "" }, { switchTo: "new" }), "row:c");
// ② 电脑、对话页删正开着的 b：切到新对话后进输入框
rows = ["a", "b", "c"]; main = "b"; composer = node("old-composer");
assert.equal(await run(["b"], { el: more, row: "" }, { switchTo: "new", composerAfter: input }), "composer");
// ③ 电脑、对话页删正开着的，切过去时视图被切走（没有输入框了）：去侧栏下一条
rows = ["a", "b", "c"]; main = "b"; composer = node("old-composer");
assert.equal(await run(["b"], { el: more, row: "" }, { switchTo: "new", composerAfter: null }), "row:c");
// ④ 删的不是正开着的（最后一条）：去上一条
rows = ["a", "b", "c"]; main = "a"; composer = input;
assert.equal(await run(["c"], { el: more, row: "c" }), "row:b");
// ⑤ 侧栏里看不到（收起）：回打开它的地方；那里也没了才进输入框
rows = ["a", "b"]; main = "a"; sidebarShown = false; composer = input;
assert.equal(await run(["b"], { el: more, row: "" }), "title-more");
assert.equal(await run(["b"], { el: { isConnected: false, focus() {} }, row: "" }), "composer");
// ⑥ 触屏：删正开着的不进输入框（不弹键盘）——抽屉开着去下一条，抽屉关着回右上「…」，都没有就不放
touch = true; sidebarShown = true;
rows = ["a", "b", "c"]; main = "b"; composer = node("old-composer");
assert.equal(await run(["b"], { el: more, row: "" }, { switchTo: "new", composerAfter: input }), "row:c");
sidebarShown = false; rows = ["a", "b"]; main = "b";
assert.equal(await run(["b"], { el: more, row: "" }, { switchTo: "new", composerAfter: input }), "title-more");
assert.equal(await run(["a"], { el: { isConnected: false, focus() {} }, row: "" }), "body");
""")

    def test_focus_after_a_delete_skips_a_sidebar_that_cannot_be_seen(self):
        # 第 11 轮合并交叉审查 5：删除后焦点去「侧栏下一条」以前不看侧栏看不看得到——手机抽屉关着（inert）时聚焦不上、焦点掉到
        # 页面最外层；iPad 侧栏收起时落进屏幕外的侧栏。现在：侧栏在 inert 里或所在侧栏收起时不去侧栏；聚焦后核对真的到了，
        # 没到就接着回打开它的地方或输入框
        self.run_page(r"""
import { focusAfterDelete, sidebarVisible } from "@/components/delete-chats";
g.CSS = { escape: (s: string) => s };
g.requestAnimationFrame = (fn: () => void) => setTimeout(fn, 1);
g.matchMedia = () => ({ matches: false });
let inert = false;
let collapsed = false;
const node = (name: string, inertNode = false): any => ({ name, isConnected: true,
  focus() { if (!(inertNode && inert)) g.document.activeElement = this; } });
let rows: string[] = [];
const triggers: Record<string, any> = {};
const frame = { getAttribute: (k: string) => (k === "data-state" ? (collapsed ? "collapsed" : "expanded") : null) };
const sidebar = {
  closest: (sel: string) => (sel === "[inert]" ? (inert ? {} : null) : sel === '[data-slot="sidebar"]' ? frame : null),
  querySelectorAll: () => rows.map((id) => ({ dataset: { rowId: id } })),
};
let composer: any = null;
g.document.querySelector = (sel: string) => {
  if (sel === '[data-slot="aui_thread-list-root"]') return sidebar;
  if (sel === "textarea.aui-composer-input") return composer;
  const m = sel.match(/^\[data-slot="aui_thread-list-item"\]\[data-row-id="([^"]+)"\] \[data-slot="aui_thread-list-item-trigger"\]$/);
  return m && rows.includes(m[1]) ? (triggers[m[1]] ??= node(`row:${m[1]}`, true)) : null;
};
const run = async (ids: string[], back: any) => {
  g.document.activeElement = g.document.body;
  focusAfterDelete(ids, back, () => "a")();
  rows = rows.filter((id) => !ids.includes(id));
  await g.sleep(20);
  return g.document.activeElement?.name ?? "body";
};
const more = node("title-more");
// 看得到：照旧去下一条
rows = ["a", "b", "c"];
assert.equal(sidebarVisible(sidebar), true);
assert.equal(await run(["b"], { el: more, row: "" }), "row:c");
// ① 手机抽屉关着（侧栏在 inert 里）：不去侧栏，回右上「…」
inert = true; rows = ["a", "b", "c"];
assert.equal(sidebarVisible(sidebar), false);
assert.equal(await run(["b"], { el: more, row: "" }), "title-more");
inert = false;
// ② iPad / 电脑侧栏收起（在屏幕外）：不落进侧栏，回标题栏「···」；那里也没了进输入框
collapsed = true; rows = ["a", "b", "c"];
assert.equal(sidebarVisible(sidebar), false);
assert.equal(await run(["b"], { el: more, row: "" }), "title-more");
composer = node("composer"); rows = ["a", "b", "c"];
assert.equal(await run(["b"], { el: { isConnected: false, focus() {} }, row: "" }), "composer");
collapsed = false;
// ③ 侧栏看得到但那一行聚焦不上（例如正在收起）：核对没到，接着试上一条；都聚焦不上才回打开它的地方（第 11 轮重测 E 段：
// 近的先试，一条聚焦不上就试下一条）
rows = ["a", "b", "c"];
triggers.c.focus = () => {};
assert.equal(await run(["b"], { el: node("title-more-2"), row: "" }), "row:a");
rows = ["a", "b", "c"];
triggers.a.focus = () => {};
assert.equal(await run(["b"], { el: node("title-more-3"), row: "" }), "title-more-3");
// 没有侧栏：不可见
assert.equal(sidebarVisible(null), false);
""")

    def test_focus_after_a_delete_follows_the_rows_in_view_and_survives_a_redraw(self):
        # 第 11 轮重测 E 段：侧栏搜索「删测」后删掉结果里最后一条（Z1），焦点落到 body、没到上一条（Y1）。现在：下一条 / 上一条按侧栏
        # 里眼前的行算（不占位置的不算），近的先试、聚焦不上就试下一条；落到一行后几帧内那一行被重画换掉（焦点掉到 body）按行号找回
        self.run_page(r"""
import { focusAfterDelete, rowsAfterDelete } from "@/components/delete-chats";
g.CSS = { escape: (s: string) => s };
g.requestAnimationFrame = (fn: () => void) => setTimeout(fn, 1);
g.matchMedia = (q: string) => ({ matches: q === "(hover: none)" });   // 测试窗口：触屏（手机 CDP 模拟电脑布局）
g.document.body = { name: "body" };
type R = { id: string; shown: boolean };
let rows: R[] = [];
const triggers: Record<string, any> = {};
const makeTrigger = (id: string) => (triggers[id] = { name: `row:${id}`, isConnected: true,
  focus() { if (rows.some((r) => r.id === id && r.shown)) g.document.activeElement = this; } });
const sidebar = { closest: () => null,
  querySelectorAll: () => rows.map((r) => ({ dataset: { rowId: r.id }, getClientRects: () => (r.shown ? [{}] : []) })) };
g.document.querySelector = (sel: string) => {
  if (sel === '[data-slot="aui_thread-list-root"]') return sidebar;
  const m = sel.match(/^\[data-slot="aui_thread-list-item"\]\[data-row-id="([^"]+)"\] \[data-slot="aui_thread-list-item-trigger"\]$/);
  return m && rows.some((r) => r.id === m[1]) ? (triggers[m[1]] ?? makeTrigger(m[1])) : null;
};
const gone = { isConnected: false, focus() {} };
const start = (ids: string[]) => {
  g.document.activeElement = g.document.body;
  focusAfterDelete(ids, { el: gone, row: "" }, () => "elsewhere")();
  rows = rows.filter((r) => !ids.includes(r.id));
};
const shown = (...ids: string[]) => ids.map((id) => ({ id, shown: true }));
const at = () => g.document.activeElement?.name ?? "body";

// 先后：之后的各条（近的先），再是之前的各条（近的先）
assert.deepEqual(rowsAfterDelete(["a", "b", "c", "d"], ["b"]), ["c", "d", "a"]);
assert.deepEqual(rowsAfterDelete(["a", "b", "c", "d"], ["c", "d"]), ["b", "a"]);
assert.deepEqual(rowsAfterDelete(["a", "b"], ["x"]), []);
// ① 搜索结果里最后一条 Z1，后面还挂着眼前看不到的行：到上一条 Y1（以前去了看不到的那一行，聚焦不上，焦点掉到 body）
rows = [...shown("Y2", "Y1", "Z1"), { id: "N", shown: false }];
start(["Z1"]);
await g.sleep(20);
assert.equal(at(), "row:Y1");
// ② 近的那一条聚焦不上：接着试下一条
rows = shown("a", "b", "c", "d");
makeTrigger("c").focus = () => {};
start(["b"]);
await g.sleep(20);
assert.equal(at(), "row:d");
// ③ 落到那一行后列表重画换掉了它（焦点掉到 body）：按行号找回到新换上的那一行
rows = shown("a", "b", "c");
delete triggers.c;
start(["b"]);
await g.sleep(8);
assert.equal(at(), "row:c");
const old = triggers.c;
old.isConnected = false;
makeTrigger("c");
g.document.activeElement = g.document.body;
await g.sleep(10);
assert.equal(g.document.activeElement, triggers.c);
assert.notEqual(g.document.activeElement, old);
// ④ 用户自己把焦点挪到别处：不再拉回
g.document.activeElement = { name: "search" };
await g.sleep(10);
assert.equal(at(), "search");
""")

    def test_deleting_the_open_chat_on_touch_lands_on_the_top_bar_toggle(self):
        # 第 11 轮重测 E 段：手机抽屉关着从右上「…」删当前对话、iPad 侧栏收起时从顶栏「对话选项 ···」删当前对话：回到新对话页，
        # 打开它的按钮跟着卸下、侧栏看不到、触屏不进输入框（不弹键盘），焦点掉到 body。现在：先等组件库切到新对话，放不上时落在顶栏的
        # 导航钮（手机的菜单钮、侧栏收起时的展开钮，cc-nav-toggle）
        self.run_page(r"""
import { focusAfterDelete, NAV_TOGGLE } from "@/components/delete-chats";
g.CSS = { escape: (s: string) => s };
g.requestAnimationFrame = (fn: () => void) => setTimeout(fn, 1);
let touch = true;
g.matchMedia = (q: string) => ({ matches: q === "(hover: none)" && touch });
g.document.body = { name: "body" };
let inert = false;
let collapsed = false;
const frame = { getAttribute: (k: string) => (k === "data-state" ? (collapsed ? "collapsed" : "expanded") : null) };
let rows: string[] = ["a", "b", "c"];
const node = (name: string, inertNode = false): any => ({ name, isConnected: true,
  focus() { if (this.isConnected && !(inertNode && (inert || collapsed))) g.document.activeElement = this; } });
const triggers: Record<string, any> = {};
const sidebar = {
  closest: (sel: string) => (sel === "[inert]" ? (inert ? {} : null) : sel === '[data-slot="sidebar"]' ? frame : null),
  querySelectorAll: () => rows.map((id) => ({ dataset: { rowId: id } })),
};
let toggle: any = null;
let composer: any = null;
g.document.querySelector = (sel: string) => {
  if (sel === '[data-slot="aui_thread-list-root"]') return sidebar;
  if (sel === NAV_TOGGLE) return toggle;
  if (sel === "textarea.aui-composer-input") return composer;
  const m = sel.match(/^\[data-slot="aui_thread-list-item"\]\[data-row-id="([^"]+)"\] \[data-slot="aui_thread-list-item-trigger"\]$/);
  return m && rows.includes(m[1]) ? (triggers[m[1]] ??= node(`row:${m[1]}`, true)) : null;
};
assert.equal(NAV_TOGGLE, '[data-slot="cc-nav-toggle"]');
// 组件库删正开着的：先切到新对话（打开它的按钮随之卸下，焦点在它上面就掉到 body），再从侧栏移走
let main = "b";
const run = async (opener: any) => {
  g.document.activeElement = g.document.body;
  focusAfterDelete(["b"], { el: opener, row: "" }, () => main)();
  await g.sleep(3);
  main = "new";
  opener.isConnected = false;
  if (g.document.activeElement === opener) g.document.activeElement = g.document.body;
  rows = rows.filter((id) => id !== "b");
  await g.sleep(30);
  return g.document.activeElement?.name ?? "body";
};

// ① 手机、抽屉关着（侧栏 inert）：从右上「…」删当前对话 → 顶栏菜单钮，不进输入框
inert = true; main = "b"; rows = ["a", "b", "c"]; toggle = node("menu"); composer = node("composer");
assert.equal(await run(node("pill-more")), "menu");
inert = false;
// ② iPad 侧栏收起：从顶栏「对话选项 ···」删当前对话 → 展开侧栏钮，不进收起的侧栏、不进输入框
collapsed = true; main = "b"; rows = ["a", "b", "c"]; toggle = node("expand");
assert.equal(await run(node("title-more")), "expand");
// ③ 电脑（非触屏）同样收起：对话页照旧进新输入框
touch = false; main = "b"; rows = ["a", "b", "c"];
assert.equal(await run(node("title-more")), "composer");
// ④ 侧栏看得到：照旧去侧栏下一条
touch = true; collapsed = false; main = "b"; rows = ["a", "b", "c"];
assert.equal(await run(node("pill-more")), "row:c");
""")

    def test_a_collapsed_sidebar_is_inert_and_hands_focus_to_the_top_bar(self):
        # 第 11 轮重测 E 段：电脑上收起侧栏后，行在屏幕外（left=-274）却没有 inert / aria-hidden，还能 focus()，Tab 会走进看不见的侧栏。
        # 现在：收起（offcanvas）时整块 inert，展开恢复；收起那一刻焦点在侧栏里就交给顶栏的展开钮。手机抽屉照旧 inert
        sidebar = read("components/ui/sidebar.tsx")
        # 侧栏只有 offcanvas 一种收起（标准化 B2 删了没人用的图标档、不收起档）
        self.assertIn('const offscreen = !isMobile && state === "collapsed";', sidebar)
        container = sidebar.split('data-slot="sidebar-container"', 1)
        self.assertIn("ref={container}", container[0][-200:])
        self.assertIn("inert={offscreen || undefined}", container[1][:200])
        effect = sidebar.split("React.useLayoutEffect(() => {", 1)[1].split("}, [offscreen]);", 1)[0]
        self.assertIn("handed.current = handOffSidebarFocus(container.current);", effect)
        self.assertIn("const NAV_TOGGLE = '[data-slot=\"cc-nav-toggle\"]';", read("lib/sidebar-focus.ts"))
        self.assertIn("document.querySelector<HTMLElement>(NAV_TOGGLE)?.focus();", read("lib/sidebar-focus.ts"))
        # 展开钮与 ⌘B 不在侧栏里：顶栏展开钮、桌面端标题栏（侧栏收起时那一份）、手机菜单钮都标成顶栏导航钮
        page = read("assistant.tsx")
        bar = page[page.index("function MainTopBar"):page.index("function MobileChatPill")]
        self.assertIn('<button type="button" className="cc-round-btn" data-slot="cc-nav-toggle" onClick={() => setOpenMobile(true)} aria-label={t("menu")}>', bar)
        self.assertIn('<button type="button" className="cc-icon-btn" data-slot="cc-nav-toggle" onClick={toggleSidebar} aria-label={t("sidebar")} title={t("sidebar")}>', bar)
        self.assertIn("{!open && <TitleBarLeft nav={nav} navToggle />}", bar)
        self.assertIn('data-slot={navToggle ? "cc-nav-toggle" : undefined}', page)
        self.assertIn("event.key === SIDEBAR_KEYBOARD_SHORTCUT &&", sidebar)
        self.assertIn('window.addEventListener("keydown", handleKeyDown)', sidebar)
        # 手机抽屉照旧
        self.assertIn('<aside className="cc-mdrawer" aria-hidden={!open} inert={!open || undefined}>', read("components/mobile-shell.tsx"))

    def test_expanding_the_sidebar_gives_focus_back(self):
        # 第 11 轮 E 段补审：收起时焦点交给顶栏展开钮，但展开钮只在收起时才有，展开当次就卸下，焦点掉到 body（⌘B 收起再 ⌘B 展开、
        # 在展开钮上按 Enter / 空格展开）。现在：展开那一刻焦点丢了才拿回来——回到收起前那一行（行重画按行号找回），没有就落在侧栏的
        # 收起钮（NAV_FALLBACK），再没有就是侧栏里第一个能聚焦的；用户展开期间自己放到别处的焦点不动
        self.run_page(r"""
import { handOffSidebarFocus, takeBackSidebarFocus, NAV_FALLBACK, NAV_TOGGLE } from "@/lib/sidebar-focus";
g.CSS = { escape: (s: string) => s };
g.document.body = { name: "body", isConnected: true };
g.document.documentElement = { ...g.document.documentElement, isConnected: true };
let inert = false;   // 收起时侧栏容器 inert
const inBox = new Set<any>();
const node = (name: string, opts: { inside?: boolean; row?: string } = {}): any => {
  const n: any = { name, isConnected: true,
    focus() { if (this.isConnected && !(opts.inside && inert)) g.document.activeElement = this; },
    closest: (sel: string) => (sel === '[data-slot="aui_thread-list-item"]' && opts.row ? { dataset: { rowId: opts.row } } : null) };
  if (opts.inside) inBox.add(n);
  return n;
};
const triggers: Record<string, any> = {};
let fallback: any = null;
let first: any = null;
const box = {
  contains: (el: any) => inBox.has(el),
  querySelector: (sel: string) => {
    const m = sel.match(/^\[data-slot="aui_thread-list-item"\]\[data-row-id="([^"]+)"\] \[data-slot="aui_thread-list-item-trigger"\]$/);
    if (m) return triggers[m[1]] ?? null;
    if (sel === NAV_FALLBACK) return fallback;
    if (sel.startsWith("button")) return first;
    return null;
  },
};
let toggle: any = null;
g.document.querySelector = (sel: string) => (sel === NAV_TOGGLE ? toggle : null);
// 收起：顶栏展开钮挂上、侧栏 inert（同一次提交），再交接；展开：展开钮卸下（焦点在它上面就掉到 body）、inert 去掉，再拿回
const collapse = () => { toggle = node("expand"); inert = true; return handOffSidebarFocus(box); };
const expand = (handed: any) => {
  inert = false;
  toggle.isConnected = false;
  if (g.document.activeElement === toggle) g.document.activeElement = g.document.body;
  toggle = null;
  takeBackSidebarFocus(box, handed);
  return g.document.activeElement?.name ?? "body";
};
fallback = node("sidebar-toggle", { inside: true });
first = node("app-menu", { inside: true });

// ① 焦点在侧栏行上 ⌘B 收起：交给展开钮；再 ⌘B 展开：回到原来那一行（以前掉到 body）
triggers.b = node("row:b", { inside: true, row: "b" });
triggers.b.focus();
let handed = collapse();
assert.equal(g.document.activeElement.name, "expand");
assert.equal(expand(handed), "row:b");
// ② 收起期间那一行被重画换掉：按行号找回新的那一行
triggers.b.focus();
handed = collapse();
triggers.b.isConnected = false;
inBox.delete(triggers.b);
triggers.b = node("row:b-new", { inside: true, row: "b" });
assert.equal(expand(handed), "row:b-new");
// ③ 收起时焦点不在侧栏（在输入框）：不交接；Tab 到展开钮上按 Enter 展开：落在侧栏收起钮，不在 body
const composer = node("composer");
composer.focus();
handed = collapse();
assert.equal(handed, null);
assert.equal(g.document.activeElement.name, "composer");
toggle.focus();
assert.equal(expand(handed), "sidebar-toggle");
// ④ 侧栏里没有标出的落点（例如桌面端标题栏没标）：侧栏里第一个能聚焦的
fallback = null;
toggle = null;
triggers.b.focus();
handed = collapse();
triggers.b.isConnected = false;
delete triggers.b;
assert.equal(expand(handed), "app-menu");
fallback = node("sidebar-toggle", { inside: true });
// ⑤ 收起期间用户自己把焦点放到输入框：展开不抢
triggers.c = node("row:c", { inside: true, row: "c" });
triggers.c.focus();
handed = collapse();
composer.focus();
assert.equal(expand(handed), "composer");
""")
        sidebar = read("components/ui/sidebar.tsx")
        effect = sidebar.split("React.useLayoutEffect(() => {", 1)[1].split("}, [offscreen]);", 1)[0]
        # 只在收起 / 展开那一刻交接（首次挂上不动焦点）；展开时拿回，用完清空
        self.assertIn("if (wasOffscreen.current === offscreen) return;", effect)
        self.assertLess(effect.index("wasOffscreen.current = offscreen;"), effect.index("handOffSidebarFocus("))
        self.assertIn("takeBackSidebarFocus(container.current, handed.current);\n    handed.current = null;", effect)
        # 三个落点：网页侧栏收起钮（不改 data-slot，globals.css 按它定尺寸）、桌面端侧栏标题栏的侧栏开关、手机抽屉的圆形搜索钮
        page = read("assistant.tsx")
        self.assertIn('data-slot="cc-sidebar-toggle" data-cc-nav-fallback="" onClick={toggleSidebar}', page)
        self.assertIn('data-slot={navToggle ? "cc-nav-toggle" : undefined} data-cc-nav-fallback={navToggle ? undefined : ""}', page)
        drawer = page[page.index("function MobileDrawer"):page.index("function isTextField")]
        self.assertIn('className="cc-round-btn" data-cc-nav-fallback="" onClick={() => setSearching((v) => !v)} aria-label={t("search")}', drawer)
        self.assertIn('[data-slot="cc-sidebar-toggle"]', read("globals.css"))

    def test_deleting_the_last_visible_row_lands_on_the_sidebar_fallback(self):
        # 第 11 轮 E 段补审：侧栏 / 抽屉看得到、但删完一行不剩（手机抽屉开着搜索只剩一条、iPad 侧栏展开删最后一条可见行、电脑在定时任务
        # 页删最后一条可见行）时，落点只剩顶栏导航钮，而它此时不在（侧栏展开）或在 inert 里（抽屉开着时的对话区），焦点掉到 body。
        # 现在先试侧栏 / 抽屉里一直在的那一颗（NAV_FALLBACK），看不到的那份在 inert 里聚焦不上，再试顶栏导航钮
        self.run_page(r"""
import { focusAfterDelete, NAV_FALLBACK, NAV_TOGGLE } from "@/components/delete-chats";
g.CSS = { escape: (s: string) => s };
g.requestAnimationFrame = (fn: () => void) => setTimeout(fn, 1);
let touch = true;
g.matchMedia = (q: string) => ({ matches: q === "(hover: none)" && touch });
g.document.body = { name: "body" };
let drawerOpen = true;     // 手机：抽屉开着时对话区 inert，关着时抽屉 inert
let collapsed = false;     // 电脑 / iPad：侧栏收起时侧栏 inert
let mobile = true;
const sidebarInert = () => (mobile ? !drawerOpen : collapsed);
const mainInert = () => mobile && drawerOpen;
const node = (name: string, where: "sidebar" | "main"): any => ({ name, isConnected: true,
  focus() { if (this.isConnected && !(where === "sidebar" ? sidebarInert() : mainInert())) g.document.activeElement = this; } });
const frame = { getAttribute: (k: string) => (k === "data-state" ? (collapsed ? "collapsed" : "expanded") : null) };
let rows: string[] = [];
const triggers: Record<string, any> = {};
const sidebar = {
  closest: (sel: string) => (sel === "[inert]" ? (sidebarInert() ? {} : null) : sel === '[data-slot="sidebar"]' && !mobile ? frame : null),
  querySelectorAll: () => rows.map((id) => ({ dataset: { rowId: id } })),
};
let fallback: any = null;
let toggle: any = null;
let composer: any = null;
g.document.querySelector = (sel: string) => {
  if (sel === '[data-slot="aui_thread-list-root"]') return sidebar;
  if (sel === NAV_FALLBACK) return fallback;
  if (sel === NAV_TOGGLE) return toggle;
  if (sel === "textarea.aui-composer-input") return composer;
  const m = sel.match(/^\[data-slot="aui_thread-list-item"\]\[data-row-id="([^"]+)"\] \[data-slot="aui_thread-list-item-trigger"\]$/);
  return m && rows.includes(m[1]) ? (triggers[m[1]] ??= node(`row:${m[1]}`, "sidebar")) : null;
};
assert.equal(NAV_FALLBACK, "[data-cc-nav-fallback]");
// 删的是行里「···」打开的：那一行跟着移走，打开处也没了
const run = async (id: string, main = "elsewhere") => {
  const opener = node("row-more", "sidebar");
  g.document.activeElement = g.document.body;
  focusAfterDelete([id], { el: opener, row: id }, () => main)();
  opener.isConnected = false;
  rows = rows.filter((r) => r !== id);
  await g.sleep(20);
  return g.document.activeElement?.name ?? "body";
};

// ① 手机、抽屉开着、搜索只剩一条：删掉落在抽屉的圆形搜索钮（顶栏菜单钮在 inert 的对话区里，以前掉到 body）
mobile = true; drawerOpen = true; rows = ["a"];
fallback = node("drawer-search", "sidebar"); toggle = node("menu", "main"); composer = node("composer", "main");
assert.equal(await run("a"), "drawer-search");
// ② iPad（触屏）侧栏展开，删掉最后一条可见行：页面上没有展开钮，落在侧栏收起钮
mobile = false; drawerOpen = false; collapsed = false; rows = ["a"];
fallback = node("sidebar-toggle", "sidebar"); toggle = null; composer = node("composer", "main");
assert.equal(await run("a"), "sidebar-toggle");
// ③ 电脑在定时任务页（没有输入框）删掉侧栏最后一条可见行：落在侧栏收起钮
touch = false; rows = ["a"]; composer = null;
assert.equal(await run("a"), "sidebar-toggle");
// ④ 侧栏看不到（收起，侧栏落点在 inert 里）：照旧落在顶栏展开钮
collapsed = true; rows = ["a"]; toggle = node("expand", "main");
assert.equal(await run("a"), "expand");
// ⑤ 手机抽屉关着（抽屉落点在 inert 里）删当前对话：照旧落在顶栏菜单钮
touch = true; mobile = true; drawerOpen = false; collapsed = false; rows = ["a"];
fallback = node("drawer-search", "sidebar"); toggle = node("menu", "main");
assert.equal(await run("a"), "menu");
""")
        host = read("components/delete-chats.tsx")
        land = host.split("const land = () => {", 1)[1].split("\n  };\n", 1)[0]
        self.assertLess(land.index("focused(document.querySelector<HTMLElement>(NAV_FALLBACK))"),
                        land.index("focused(document.querySelector<HTMLElement>(NAV_TOGGLE))"))
        self.assertLess(land.index("toComposer()"), land.index("NAV_FALLBACK"))


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class Round11RetestBDragTests(unittest.TestCase):
    """测试窗口第 11 轮重测 B 段（lab 84da6f81，CDP 1024×768 真鼠标、真键盘）：拿起过的拖动按 Esc 取消后在原行上松开打开了对话；
    会话栏第一张卡「公司」（展开着）空格拿起 ↓ 再空格放下念「已取消移动」、顺序不变。都在共用拖动（drag.tsx）里修，所有拖动处一起好。"""

    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + body
               + "\n(globalThis as any).done();\n")

    def test_a_lifted_gesture_never_ends_in_a_click(self):
        # 真跑拖动库的鼠标传感器（包了 landFirst）与手机长按传感器：拿起后按 Esc（手机还有转屏、切到后台）取消，按键 / 手指还按着；
        # 原来拖动库只把点击拦到取消后 50ms，第 11 轮 B14 起挡板又在滑回时撤掉，之后在原来那一行上松开，系统补的点击打开了对话 /
        # 折叠了组。现在一直拦到松开以后；没拿起就取消、普通点击、下一次按下照常
        self.run_page(r"""
import { HoldTouchSensor, LandingMouseSensor } from "@/components/ui/drag";
const g = globalThis as any;
const doc: any = new EventTarget();
doc.visibilityState = "visible";
doc.documentElement = { dataset: {} };
doc.getSelection = () => ({ removeAllRanges() {} });
g.document = doc;
const page = new EventTarget();
g.addEventListener = page.addEventListener.bind(page);
g.removeEventListener = page.removeEventListener.bind(page);
g.getSelection = () => ({ removeAllRanges() {} });
const fire = (type: string, extra: any = {}) => {
  const event = Object.assign(new Event(type, { cancelable: true }), extra);
  doc.dispatchEvent(event);
  return event;
};
// 系统的 click：在 document 的捕获阶段被拦（拖动库只停传播，这里拦了还取消默认动作），就不会到行、组头上
const clicked = () => {
  const event = fire("click");
  return !(event.defaultPrevented || event.cancelBubble);
};
const log: string[] = [];
const mouse = (beforeCancel: any) =>
  new (LandingMouseSensor as any)({
    active: "row:t1", activeNode: { node: { current: null } }, event: { target: new EventTarget(), clientX: 100, clientY: 100 },
    context: { current: {} }, options: { activationConstraint: { distance: 3 }, beforeEnd: (end: () => void) => end(), beforeCancel },
    onStart: () => log.push("start"), onMove: () => log.push("move"), onEnd: () => log.push("end"), onCancel: () => log.push("cancel"),
    onAbort: () => log.push("abort"), onPending: () => {},
  });
const escape = () => fire("keydown", { code: "Escape", key: "Escape" });

// 鼠标拿起、拖开 25px、Esc：先滑回再取消；按键还按着
let back: (() => void) | null = null;
mouse((cancel: () => void) => { log.push("slide back"); back = cancel; });
fire("mousemove", { clientX: 100, clientY: 125 });
assert.deepEqual(log, ["start"]);                     // 动够 3px 拿起
escape();
assert.deepEqual(log, ["start", "slide back"]);
back!();
await g.sleep(120);                                   // 滑回播完以后很久才松开
assert.equal(clicked(), false);                       // 松开前（按着往回移）也不算
fire("pointerup");
fire("mouseup");
assert.equal(clicked(), false);                       // 松开紧跟着的那次点击：不打开对话、不折叠组
await g.sleep(80);
assert.equal(clicked(), true);                        // 之后的点击照常
// 没拿起（没动够 3px）就按 Esc：之后松开照常是点击
log.length = 0;
mouse(undefined);
escape();
assert.deepEqual(log, ["abort", "cancel"]);
await g.sleep(80);
fire("mouseup");
assert.equal(clicked(), true);
// 拿起后取消、又按下了（下一次手势）：不再拦
mouse(undefined);
fire("mousemove", { clientX: 100, clientY: 125 });
escape();
await g.sleep(80);
assert.equal(clicked(), false);
fire("pointerdown");
assert.equal(clicked(), true);
// 拿起后照常放下：拖动库自己把松手后的点击拦掉，过后照常
mouse(undefined);
fire("mousemove", { clientX: 100, clientY: 125 });
fire("mouseup");
assert.equal(clicked(), false);
await g.sleep(80);
assert.equal(clicked(), true);

// 手机：按住拿起后 Esc（外接键盘）/ 转屏 / 切到后台取消，手指还按着：松开那一下同样不当点击
const touch = (options: any = {}) => {
  const target = new EventTarget();
  new HoldTouchSensor({
    active: "row:t1", event: { target, touches: [{ clientX: 100, clientY: 100 }] }, options: { delay: 10, tolerance: 5, ...options },
    onStart: () => log.push("start"), onMove: () => log.push("move"), onEnd: () => log.push("end"), onCancel: () => log.push("cancel"),
    onAbort: () => log.push("abort"), onPending: () => {},
  } as any);
  return target;
};
log.length = 0;
let held: (() => void) | null = null;
touch({ beforeCancel: (cancel: () => void) => { held = cancel; } });
await g.sleep(30);
escape();
held!();
assert.deepEqual(log, ["start", "cancel"]);
await g.sleep(120);
assert.equal(clicked(), false);
fire("touchend");
assert.equal(clicked(), false);
await g.sleep(80);
assert.equal(clicked(), true);
// 系统收回了这次触摸（touchcancel）：手势已经结束，不再拦
const gone = touch();
await g.sleep(30);
gone.dispatchEvent(new Event("touchcancel"));
await g.sleep(80);
assert.equal(clicked(), true);
""")
        drag = read("components/ui/drag.tsx")
        self.assertIn('export const LandingMouseSensor = landFirst<MouseSensorOptions>(MouseSensor, "mouse");', drag)
        self.assertIn("if (release && self?.activated) blockReleaseClick(release);", drag)
        self.assertIn('if (cancelled && this.lifted && !tap && !over) blockReleaseClick("touch");', drag)

    def test_release_block_never_outlives_a_release_the_page_cannot_see(self):
        # 第 11 轮重测 B 段交叉审查：按着鼠标 / 手指切到后台（切标签页、窗口被挡住），拖动库按 visibilitychange 取消，原来照样挂上
        # 「松开前不当点击」；松开发生在别的页面里，本页收不到 mouseup / touchend，拦截一直不撤——切回来键盘回车 / 空格点按钮被吞、
        # 输入框打字时光标被清。现在：已在后台就不挂；挂着时切到后台当场撤；鼠标再动时左键已松开就当松开了；键盘激活的点击和
        # 输入框里的选区一直不管
        self.run_page(r"""
import { HoldTouchSensor, LandingMouseSensor } from "@/components/ui/drag";
const g = globalThis as any;
const doc: any = new EventTarget();
doc.visibilityState = "visible";
doc.documentElement = { dataset: {} };
doc.activeElement = null;
let cleared = 0;
const selection = () => ({ removeAllRanges() { cleared += 1; } });
doc.getSelection = selection;
g.document = doc;
const page = new EventTarget();
g.addEventListener = page.addEventListener.bind(page);
g.removeEventListener = page.removeEventListener.bind(page);
g.getSelection = selection;
const fire = (type: string, extra: any = {}) => {
  const event = Object.assign(new Event(type, { cancelable: true }), extra);
  doc.dispatchEvent(event);
  return event;
};
const clicked = (extra: any = { detail: 1 }) => {
  const event = fire("click", extra);
  return !(event.defaultPrevented || event.cancelBubble);
};
// 拖动库的鼠标传感器在 window 上听 visibilitychange，本仓的拦截在 document 上听：两边都发
const hide = () => { doc.visibilityState = "hidden"; fire("visibilitychange"); page.dispatchEvent(new Event("visibilitychange")); };
const show = () => { doc.visibilityState = "visible"; fire("visibilitychange"); page.dispatchEvent(new Event("visibilitychange")); };
const log: string[] = [];
const mouse = () =>
  new (LandingMouseSensor as any)({
    active: "row:t1", activeNode: { node: { current: null } }, event: { target: new EventTarget(), clientX: 100, clientY: 100 },
    context: { current: {} }, options: { activationConstraint: { distance: 3 }, beforeEnd: (end: () => void) => end(),
      beforeCancel: (cancel: () => void) => { log.push("slide back"); cancel(); } },
    onStart: () => log.push("start"), onMove: () => log.push("move"), onEnd: () => log.push("end"), onCancel: () => log.push("cancel"),
    onAbort: () => log.push("abort"), onPending: () => {},
  });
const lift = () => {
  mouse();
  fire("mousemove", { clientX: 100, clientY: 125, buttons: 1 });
};
const escape = () => fire("keydown", { code: "Escape", key: "Escape" });

// 鼠标拿起后一直按着切到后台：拖动库取消（先滑回），松开在别处，切回来以后点击照常（拖动库自己那 50ms 过后）
lift();
hide();
assert.deepEqual(log, ["start", "slide back", "cancel"]);
show();
await g.sleep(80);
assert.equal(clicked(), true);
assert.equal(clicked({ detail: 0 }), true);
// 先 Esc 取消、按着再切到后台：拦截当场撤
log.length = 0;
lift();
escape();
await g.sleep(80);
assert.equal(clicked(), false);                       // 还按着：照旧拦
hide();
show();
assert.equal(clicked(), true);
// 松开那一下没传回本页：鼠标再动时左键已松开，就当松开了（松开后 50ms 撤）；按着动照旧拦
lift();
escape();
await g.sleep(80);
fire("mousemove", { clientX: 90, clientY: 100, buttons: 1 });
fire("pointermove", { clientX: 90, clientY: 100, buttons: 1 });
await g.sleep(80);
assert.equal(clicked(), false);
fire("mousemove", { clientX: 90, clientY: 100, buttons: 0 });
assert.equal(clicked(), false);                       // 紧跟着的点击还拦
await g.sleep(80);
assert.equal(clicked(), true);
// 拦着的时候：键盘激活的点击（detail 为 0）照常；焦点在输入框里不清选区，在别处照旧清
lift();
escape();
await g.sleep(80);
assert.equal(clicked(), false);
assert.equal(clicked({ detail: 0 }), true);
cleared = 0;
doc.activeElement = { tagName: "TEXTAREA", isContentEditable: false };
fire("selectionchange");
assert.equal(cleared, 0);
doc.activeElement = { tagName: "DIV", isContentEditable: true };
fire("selectionchange");
assert.equal(cleared, 0);
doc.activeElement = { tagName: "BUTTON", isContentEditable: false };
fire("selectionchange");
assert.equal(cleared, 1);
fire("mouseup");
await g.sleep(80);
assert.equal(clicked(), true);
doc.activeElement = null;

// 手机：按住拿起后切到后台（visibilitychange 取消）：手指在别处松开，切回来以后点击照常
const target = new EventTarget();
log.length = 0;
new HoldTouchSensor({
  active: "row:t1", event: { target, touches: [{ clientX: 100, clientY: 100 }] }, options: { delay: 10, tolerance: 5 },
  onStart: () => log.push("start"), onMove: () => log.push("move"), onEnd: () => log.push("end"), onCancel: () => log.push("cancel"),
  onAbort: () => log.push("abort"), onPending: () => {},
} as any);
await g.sleep(30);
hide();
assert.deepEqual(log, ["start", "cancel"]);
show();
await g.sleep(80);
assert.equal(clicked(), true);
""")

    def test_keyboard_moves_follow_the_list_order_whatever_the_heights(self):
        # 会话栏：「公司」展开着 300 高，下一张「本月」收着 44 高。拖动库按「底边对齐下一张」挪浮层，再按最近中心找落点：浮层中心
        # 离原位（它自己）比离「本月」还近，算回它自己，放下念「已取消移动」。现在 ↑ ↓ 按这一列的先后换瞄准的那一项，落点就是它
        self.run_page(r"""
import { closestCenter, keyboardAim, keyboardCollision, keyboardSlot } from "@/components/ui/drag";
const order = ["company", "month", "notes", "folders"];
const H: Record<string, number> = { company: 300, month: 44, notes: 44, folders: 44 };
const rects = new Map<string, any>();
let top = 0;
for (const id of order) {
  rects.set(id, { top, left: 0, width: 240, height: H[id], right: 240, bottom: top + H[id] });
  top += H[id]! + 8;
}
const pool = order.map((id) => ({ id, key: id, data: { current: {} }, disabled: false, node: { current: null }, rect: { current: null } })) as any;
const box = (y: number, h: number) => ({ top: y, left: 0, width: 240, height: h, right: 240, bottom: y + h });

// 「公司」空格拿起、↓：瞄「本月」，浮层底边对齐「本月」的底边
const aim = keyboardAim(order, "company", "company", true);
assert.equal(aim, "month");
const y = keyboardSlot(order, "company", aim!, (id) => rects.get(String(id)), 300);
assert.equal(y, 308 + 44 - 300);
// 原来按最近中心算：还是它自己（这就是测试窗口看到的「已取消移动」）
const near = closestCenter({ active: { id: "company" } as any, collisionRect: box(y!, 300), droppableRects: rects as any,
                             droppableContainers: pool, pointerCoordinates: null });
assert.equal(near[0]!.id, "company");
// 现在落点就是瞄着的那一项：放下放在第 2 位
assert.equal(keyboardCollision(pool, "company", aim)[0]!.id, "month");
// 再 ↓ 一格、↑ 回来、回到原位、到头不动
assert.equal(keyboardAim(order, "company", "month", true), "notes");
assert.equal(keyboardSlot(order, "company", "notes", (id) => rects.get(String(id)), 300), 360 + 44 - 300);
assert.equal(keyboardAim(order, "company", "month", false), "company");
assert.equal(keyboardSlot(order, "company", "company", (id) => rects.get(String(id)), 300), 0);
assert.equal(keyboardAim(order, "company", "company", false), null);
assert.equal(keyboardAim(order, "folders", "folders", true), null);
// 往前挪：顶边对齐那一项的顶边（「知识库」↑ 到第 2 位）
assert.equal(keyboardAim(order, "notes", "notes", false), "month");
assert.equal(keyboardSlot(order, "notes", "month", (id) => rects.get(String(id)), 44), 308);
// 瞄着的那一项不在了（拖着时列表变了）：从它自己接着算；落点退回它自己
assert.equal(keyboardAim(order, "notes", "gone", true), "folders");
assert.equal(keyboardCollision(pool, "notes", "gone")[0]!.id, "notes");
assert.deepEqual(keyboardCollision([], "notes", "month"), []);
""")
        drag = read("components/ui/drag.tsx")
        collide = drag.split("  private collide() {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("const hit = keyboard ? keyboardCollision(pool, d.id, d.aim)[0] :", collide)
        step = drag.split("  private step(event: KeyboardEvent) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("const target = keyboardAim(order, d.id, d.aim, down);", step)
        self.assertIn("d.aim = String(target);", step)
        self.assertIn("      aim: id,\n", drag.split("  private lift(", 1)[1])            # 拿起时瞄着它自己（每次拿起是新的一次）
        self.assertNotIn("sortableKeyboardCoordinates", drag)   # 不再按几何找下一项
        self.assertNotIn("@dnd-kit", drag)


# 自写拖动引擎（第 12 轮）的行为用例用的假页面：只有拖动引擎用到的那一点 DOM（节点树、框、行内样式、事件、克隆）。各项的框按
# rect 给，行内 transform 的竖向位移照算进 getBoundingClientRect（同浏览器）；读框次数记在 reads 里
ENGINE_DOM = r"""
import { DragEngine, dragLayer } from "@/components/ui/drag";
const g = globalThis as any;
const page = new EventTarget();
g.addEventListener = page.addEventListener.bind(page);
g.removeEventListener = page.removeEventListener.bind(page);
g.innerHeight = 800;
g.innerWidth = 1000;
g.scrollY = 0;
g.requestAnimationFrame = (run: () => void) => setTimeout(run, 0);
g.cancelAnimationFrame = (id: any) => clearTimeout(id);
g.matchMedia = () => ({ matches: false });
g.getSelection = () => ({ removeAllRanges() {} });
const shiftY = (value: string) => Number(/translate3d\(\s*-?[\d.]+px,\s*(-?[\d.]+)px/.exec(value || "")?.[1] ?? 0);
g.getComputedStyle = (el: any) => {
  const y = shiftY(el.style?.transform);
  return { overflow: "visible", overflowX: "visible", overflowY: "visible", position: "", transformOrigin: "0px 0px", translate: "none",
           transform: el.style?.transform ? `matrix(1, 0, 0, 1, 0, ${y})` : "none" };
};
const reads = { count: 0 };
const all = new Set<any>();
class El extends EventTarget {
  tag: string; parentNode: any = null; children: any[] = []; attrs = new Map<string, string>(); dataset: Record<string, string> = {};
  style: any; rect: { top: number; left: number; width: number; height: number }; nodeType = 1; hidden = false; className = "";
  textContent = ""; id = ""; animations: any[] = [];
  constructor(tag: string, rect = { top: 0, left: 0, width: 200, height: 0 }) {
    super();
    this.tag = tag;
    this.rect = rect;
    const style: any = { transform: "", transition: "" };
    style.setProperty = (name: string, value: string) => (style[name] = value);
    style.removeProperty = (name: string) => delete style[name];
    this.style = style;
    all.add(this);
  }
  get ownerDocument() { return doc; }
  get parentElement() { return this.parentNode instanceof El ? this.parentNode : null; }
  get isConnected(): boolean { for (let n: any = this; n; n = n.parentNode) if (n === doc.body) return true; return false; }
  get firstElementChild() { return this.children[0] ?? null; }
  appendChild(child: any) { child.parentNode?.removeChild?.(child); child.parentNode = this; this.children.push(child); doc.added.push([this, child]); return child; }
  append(...kids: any[]) { for (const kid of kids) this.appendChild(kid); }
  removeChild(child: any) { this.children = this.children.filter((c) => c !== child); child.parentNode = null; doc.removed.push([this, child]); }
  remove() { this.parentNode?.removeChild(this); }
  setAttribute(name: string, value: string) { this.attrs.set(name, String(value)); }
  getAttribute(name: string) { return this.attrs.get(name) ?? null; }
  removeAttribute(name: string) { this.attrs.delete(name); }
  cloneNode(deep: boolean): any {
    const copy = new El(this.tag, this.rect);
    for (const [k, v] of this.attrs) copy.attrs.set(k, v);
    Object.assign(copy.dataset, this.dataset);
    copy.className = this.className;
    copy.textContent = this.textContent;
    if (deep) for (const kid of this.children) copy.appendChild(kid.cloneNode(true));
    return copy;
  }
  querySelectorAll(): any[] { const out: any[] = []; const walk = (e: any) => e.children.forEach((k: any) => (out.push(k), walk(k))); walk(this); return out; }
  contains(node: any) { for (let n = node; n; n = n.parentNode) if (n === this) return true; return false; }
  // 框：自己给了 rect 的按 rect；浮层按行内 top / height（fixed）；没给的（浮层里的各层）同父节点。各层的行内位移一路加上去
  layout(): { top: number; left: number; width: number; height: number } {
    if (this.style.top) return { top: parseFloat(this.style.top), left: parseFloat(this.style.left), width: parseFloat(this.style.width), height: parseFloat(this.style.height) };
    if (!this.rect.height && this.parentElement) return this.parentElement.layout();
    return this.rect;
  }
  getBoundingClientRect() {
    reads.count += 1;
    const box = this.layout();
    let top = box.top - (this.style.top ? 0 : g.scrollY);
    for (let n: any = this; n instanceof El; n = n.parentNode) top += shiftY(n.style.transform);
    return { top, left: box.left, width: box.width, height: box.height, right: box.left + box.width, bottom: top + box.height };
  }
  getClientRects() { return [1]; }
  closest() { return null; }
  focus() { doc.activeElement = this; }
  blur() { if (doc.activeElement === this) doc.activeElement = doc.body; }
  scrollIntoView() {}
  animate(frames: any, opts: any) {
    let done: () => void = () => undefined;
    const a: any = { frames, opts, startTime: null, playState: "running", finished: new Promise<void>((resolve) => (done = resolve)) };
    a.finish = () => { a.playState = "finished"; done(); };
    a.cancel = () => { a.playState = "idle"; done(); };
    this.animations.push(a);
    return a;
  }
}
const doc: any = new EventTarget();
doc.nodeType = 9;
doc.visibilityState = "visible";
doc.added = [] as any[];
doc.removed = [] as any[];
doc.documentElement = new El("html");
doc.documentElement.dataset = {};
doc.body = new El("body");
doc.body.parentNode = doc.documentElement;
doc.documentElement.parentNode = doc;
doc.scrollingElement = doc.documentElement;
doc.createElement = (tag: string) => new El(tag);
doc.getSelection = () => ({ removeAllRanges() {} });
doc.activeElement = doc.body;
doc.querySelectorAll = (selector: string) => {
  const key = selector === "[data-drag-list]" ? "dragList" : selector === "[data-drag-id]" ? "dragId" : null;
  return key ? [...all].filter((e) => e.isConnected && e.dataset[key] !== undefined) : [];
};
doc.querySelector = () => null;
g.document = doc;
const fire = (type: string, extra: any = {}) => {
  const event = Object.assign(new Event(type, { cancelable: true }), extra);
  doc.dispatchEvent(event);
  return event;
};
const texts = { instructions: "Space to lift", pickedUp: (n: string) => `up ${n}`, movedTo: (n: string, a: number, t: number) => `moved ${n} ${a}/${t}`,
  droppedAt: (n: string, a: number, t: number) => `dropped ${n} ${a}/${t}`, cancelled: (n: string) => `cancel ${n}` };
// 一列卡片：a、b、c，高 40，间距 10（顶 0 / 50 / 100）
function column(engine: any, ids: string[], list = "cards", top = 0) {
  const box = doc.body.appendChild(new El("div"));
  const out: Record<string, { node: any; handle: any; item: any }> = {};
  ids.forEach((id, i) => {
    const node = box.appendChild(new El("section", { top: top + i * 50, left: 0, width: 200, height: 40 }));
    node.dataset.dragId = id;
    node.dataset.dragList = list;
    const handle = node.appendChild(new El("button", { top: top + i * 50, left: 180, width: 20, height: 20 }));
    const data = { id, kind: "card", label: id, list };
    const item = { id, data, list: undefined, disabled: false, zone: false, node: null, handle: null, entry: null };
    engine.attach(item, node);
    engine.attachHandle(item, handle);
    engine.register(item, data, list, false);
    out[id] = { node, handle, item };
  });
  engine.setList(list, ids);
  return out;
}
const key = (code: string) => ({ nativeEvent: Object.assign(new Event("keydown", { cancelable: true }), { code, key: code === "Space" ? " " : code }), preventDefault() {} });
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class DragEngineTests(unittest.TestCase):
    """第 12 轮：共用拖动换成自写的指针排序（docs/work/2026-10-17-drag-engine.md）。直接驱动拖动引擎：拿起时一次量好、拖动中不再逐项
    量；让位直接写节点；拿起、落定只通知被拖的那一项；拖动中 body 下不增删节点；放下、取消、读屏播报与原来一致。"""

    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + ENGINE_DOM + body + "\n(globalThis as any).done();\n")

    def test_keyboard_sort_writes_shifts_and_only_wakes_the_lifted_item(self):
        self.run_page(r"""
const drops: string[][] = [];
const actives: Array<string | null> = [];
const engine = new DragEngine(null);
engine.props = { accepts: (a: any, t: any) => a.list === t.list, onDrop: (a: any, t: any) => drops.push([a.id, t.id]), onActive: (a: any) => actives.push(a?.id ?? null) };
engine.setTexts(texts);
const unmount = engine.mount();
const live = doc.body.children.find((e: any) => e.attrs.get("aria-live") === "assertive");
const hidden = doc.body.children.find((e: any) => e.id.startsWith("DndDescribedBy-"));
assert.equal(live.attrs.get("role"), "status");
assert.equal(live.attrs.get("aria-atomic"), "true");
assert.equal(hidden.textContent, "Space to lift");
assert.equal(hidden.style.display, "none");
assert.equal(engine.describedBy, hidden.id);
const cards = column(engine, ["a", "b", "c"]);
const woken: Record<string, number> = { a: 0, b: 0, c: 0 };
for (const id of ["a", "b", "c"]) engine.subscribe(id, () => (woken[id] += 1));
const layer = dragLayer();
doc.added.length = 0;
doc.removed.length = 0;
// 空格拿起：只在把手本身上按才算
engine.press("keyboard", "a", { ...key("Space"), target: cards.b.handle } as any);
assert.equal(engine.stateOf("a"), 0);
reads.count = 0;
cards.a.handle.focus();                                         // 空格按在把手上：焦点本来就在它上面
engine.press("keyboard", "a", { ...key("Space"), target: cards.a.handle } as any);
assert.equal(engine.stateOf("a"), 2);                          // 键盘拿起：焦点跟着它
assert.deepEqual(woken, { a: 1, b: 0, c: 0 });                  // 拿起只叫醒被拖的那一项
assert.deepEqual(actives, ["a"]);
assert.equal(live.textContent, "up a");
const overlay = layer!.children.find((e: any) => e.className === "cc-drag-overlay");
assert.ok(overlay);
assert.equal(overlay.style.zIndex, "55");
assert.equal(overlay.style.top, "0px");
assert.equal(overlay.style.height, "40px");
assert.match(overlay.style.transition, /^$/);                   // 减少动态效果：键盘一格一格挪不带过渡
const float = overlay.children[0];
assert.equal(float.className, "cc-drag-float");
assert.equal(float.dataset.kind, "card");
assert.ok(float.attrs.has("inert"));
assert.equal(float.attrs.get("aria-hidden"), "true");
const liftReads = reads.count;
assert.ok(liftReads >= 3 && liftReads <= 6, String(liftReads));   // 拿起时一次量好（三张卡 + 被拖的那一项本身）
await g.sleep(5);                                               // 下一个任务才开始收键（拿起的那一下空格不算）
reads.count = 0;
fire("keydown", { code: "ArrowDown", key: "ArrowDown" });
assert.equal(reads.count, 0);                                   // 拖动中不再逐项量
assert.equal(live.textContent, "moved a 2/3");
assert.equal(overlay.style.transform, "translate3d(0px, 50px, 0) scaleX(1) scaleY(1)");   // 底边对齐 b 的底边
assert.equal(cards.a.node.style.transform, "translate3d(0px, 50px, 0px)");   // 空档挪到 b 的位置
assert.equal(cards.b.node.style.transform, "translate3d(0px, -50px, 0px)");  // b 让到上面（高 40 + 间距 10）
assert.equal(cards.c.node.style.transform, "");                              // 不让位的一次也不写
assert.equal(cards.c.node.style.transition, "");
assert.deepEqual(woken, { a: 1, b: 0, c: 0 });                  // 让位不经 React
fire("keydown", { code: "ArrowDown", key: "ArrowDown" });
fire("keydown", { code: "ArrowDown", key: "ArrowDown" });       // 到头了：不动、不念
assert.equal(live.textContent, "moved a 3/3");
fire("keydown", { code: "ArrowUp", key: "ArrowUp" });
assert.equal(live.textContent, "moved a 2/3");
assert.deepEqual(doc.added.filter(([parent]: any) => parent === doc.body), []);   // 拖动中 body 下不增删节点
assert.deepEqual(doc.removed.filter(([parent]: any) => parent === doc.body), []);
// 空格放下：没有动效时当场结束，交调用方，各项去掉让位、浮层卸下
fire("keydown", { code: "Space", key: " " });
assert.deepEqual(drops, [["a", "b"]]);
assert.deepEqual(actives, ["a", null]);
assert.equal(live.textContent, "dropped a 2/3");
assert.deepEqual(woken, { a: 2, b: 0, c: 0 });                  // 落定只叫醒被拖的那一项
assert.equal(engine.stateOf("a"), 0);
for (const id of ["a", "b", "c"]) assert.equal(cards[id]!.node.style.transform, "", id);
assert.equal(cards.b.node.style.transition, "");
assert.equal(overlay.parentNode, null);
assert.equal(doc.activeElement, cards.a.handle);                // 拖动中、放下后焦点一直在被拖那一项的把手上
unmount();
assert.equal(live.parentNode, null);
assert.equal(hidden.parentNode, null);
""")

    def test_mouse_drag_follows_the_pointer_and_escape_slides_back_without_a_click(self):
        self.run_page(r"""
const drops: string[][] = [];
const pointers: number[] = [];
const engine = new DragEngine(null);
engine.props = { accepts: (a: any, t: any) => a.list === t.list, onDrop: (a: any, t: any) => drops.push([a.id, t.id]), onPointer: (_a: any, y: number) => pointers.push(y) };
engine.setTexts(texts);
engine.mount();
const live = doc.body.children.find((e: any) => e.attrs.get("aria-live") === "assertive");
const cards = column(engine, ["a", "b", "c"]);
const press = (id: string, y: number) =>
  engine.press("mouse", id, { nativeEvent: { button: 0, clientX: 190, clientY: y, target: cards[id]!.handle, defaultPrevented: false }, target: cards[id]!.handle, preventDefault() {} } as any);
// 右键、已经处理过的按下不拿起
engine.press("mouse", "c", { nativeEvent: { button: 2, clientX: 190, clientY: 110, target: cards.c.handle }, target: cards.c.handle } as any);
engine.press("mouse", "c", { nativeEvent: { button: 0, clientX: 190, clientY: 110, target: cards.c.handle, defaultPrevented: true }, target: cards.c.handle } as any);
fire("mousemove", { clientX: 190, clientY: 130 });
assert.equal(engine.stateOf("c"), 0);
press("c", 110);
fire("mousemove", { clientX: 190, clientY: 112 });             // 没动够 3px
assert.equal(engine.stateOf("c"), 0);
fire("mousemove", { clientX: 190, clientY: 114 });             // 动够了：拿起
assert.equal(engine.stateOf("c"), 1);
assert.equal(live.textContent, "up c");
const overlay = dragLayer()!.children.find((e: any) => e.className === "cc-drag-overlay");
reads.count = 0;
fire("mousemove", { clientX: 230, clientY: 20 });              // 拖到 a 上（只竖着动，浮层不出这一列）
assert.equal(reads.count, 0);
assert.equal(overlay.style.transform, "translate3d(0px, -90px, 0) scaleX(1) scaleY(1)");   // 按下处起算（拿起前那 4px 也算）
assert.equal(live.textContent, "moved c 1/3");
assert.equal(cards.c.node.style.transform, "translate3d(0px, -100px, 0px)");
assert.equal(cards.a.node.style.transform, "translate3d(0px, 50px, 0px)");
assert.equal(cards.b.node.style.transform, "translate3d(0px, 50px, 0px)");
fire("mousemove", { clientX: 190, clientY: -400 });            // 拖过头：浮层停在第一张的顶边
assert.equal(overlay.style.transform, "translate3d(0px, -100px, 0) scaleX(1) scaleY(1)");
assert.deepEqual(pointers.slice(-2), [20, -400]);
// Esc：先滑回（这一列各项去掉让位），再取消；不交调用方
fire("keydown", { code: "Escape", key: "Escape" });
assert.deepEqual(drops, []);
assert.equal(live.textContent, "cancel c");
for (const id of ["a", "b", "c"]) assert.equal(cards[id]!.node.style.transform, "", id);
assert.equal(engine.stateOf("c"), 0);
const reported = pointers.length;
fire("mousemove", { clientX: 190, clientY: 300 });             // 取消以后不再交指针位置
assert.equal(pointers.length, reported);
// 按键还按着就取消了：之后松开那一下不当点击
fire("mouseup");
const click = fire("click", { detail: 1 });
assert.ok(click.defaultPrevented || (click as any).cancelBubble);
await g.sleep(80);
const later = fire("click", { detail: 1 });
assert.ok(!later.defaultPrevented && !(later as any).cancelBubble);
// 再拖一次、照常放下：落在 b 上
press("a", 10);
fire("mousemove", { clientX: 190, clientY: 15 });
fire("mousemove", { clientX: 190, clientY: 65 });
assert.equal(live.textContent, "moved a 2/3");
fire("mouseup");
assert.deepEqual(drops, [["a", "b"]]);
assert.equal(live.textContent, "dropped a 2/3");
""")

    def test_release_lands_first_then_hands_over_in_one_go(self):
        # 有动效时：松手只起播落位，交调用方、去掉让位都等落位播完，并且在同一下里做完。第 13 轮（改法 2）：落位播完那一下画面已经是
        # 最终的样子，先只卸浮层、亮出空档、念放下；交调用方与去掉让位放到这一帧画完以后，仍在同一下
        self.run_page(r"""
import { isDragging } from "@/lib/layers";
const log: string[] = [];
const engine = new DragEngine({ duration: 200, easing: "ease" });
engine.props = { accepts: (a: any, t: any) => a.list === t.list, onDrop: (a: any, t: any) => log.push(`drop ${a.id}>${t.id}`), onActive: (a: any) => log.push(`active ${a?.id ?? "-"}`) };
engine.setTexts(texts);
engine.mount();
const live = doc.body.children.find((e: any) => e.attrs.get("aria-live") === "assertive");
const cards = column(engine, ["a", "b", "c"]);
let woken = 0;
engine.subscribe("a", () => (woken += 1));
engine.press("keyboard", "a", { ...key("Space"), target: cards.a.handle } as any);
cards.a.node.setAttribute("data-drag-source", "gap");           // React 把它重画成空档（假页面里没有 React，照样标上）
const overlay = dragLayer()!.children.find((e: any) => e.className === "cc-drag-overlay");
assert.equal(overlay.style.transition, "transform 200ms ease");   // 键盘一格一格挪带过渡
await g.sleep(5);
fire("keydown", { code: "ArrowDown", key: "ArrowDown" });
assert.equal(cards.b.node.style.transition, "transform 200ms ease");   // 让位带过渡（先写过渡再写位移）
fire("keydown", { code: "Space", key: " " });
assert.deepEqual(log, ["active a"]);                            // 还在落位：没交调用方
const landing = overlay.animations.at(-1);
assert.equal(overlay.dataset.dropping, "");
assert.equal(landing.opts.fill, "forwards");
assert.equal(landing.frames[1].transform, "translate3d(0px, 50px, 0) scaleX(1) scaleY(1)");   // 落到 b 的位置
assert.equal(cards.b.node.style.transform, "translate3d(0px, -50px, 0px)");
assert.equal(cards.a.node.style.transform, "translate3d(0px, 50px, 0px)");   // 空档已由让位摆在落点上（浮层落的就是这里）
woken = 0;
landing.finish();
await Promise.resolve();
// 第一拍（落位播完那一下）：浮层卸下、空档亮出、念放下；调用方还没收到，让位还在，画面就是最终的样子
assert.equal(overlay.parentNode, null);
assert.equal(cards.a.node.getAttribute("data-drag-source"), null);
assert.equal(live.textContent, "dropped a 2/3");
assert.deepEqual(log, ["active a"]);
assert.equal(woken, 0);
assert.equal(isDragging(), false);                              // 「正在拖」同原来在落位播完这一刻撤
assert.equal(engine.stateOf("a"), 2);                           // 被拖那一项随提交才重画
assert.equal(cards.a.node.style.transform, "translate3d(0px, 50px, 0px)");
assert.equal(cards.b.node.style.transform, "translate3d(0px, -50px, 0px)");
// 第二拍（这一帧画完以后）：交调用方、各项去掉让位（不带过渡、当场归位）、onActive(null)、结束拖动同一下做完
await g.sleep(10);
assert.deepEqual(log, ["active a", "active -", "drop a>b"]);
assert.equal(woken, 1);
assert.equal(isDragging(), false);
assert.equal(engine.stateOf("a"), 0);
assert.equal(cards.b.node.style.transform, "");
assert.equal(cards.b.node.style.transition, "");                // 结束时不带过渡、当场归位
assert.equal(cards.a.node.style.transform, "");
assert.equal(live.textContent, "dropped a 2/3");                // 念一次，不重念
""")

    def test_cancel_after_dragging_into_another_list_slides_that_list_back_at_once(self):
        # 审查：对话行拖进已置顶后 Esc（手机 touchcancel、转屏、切后台同一条路），已置顶里让过位的行要当场带过渡滑回原位，不等滑回播完
        # 才无过渡地跳回
        self.run_page(r"""
const returns: string[] = [];
const engine = new DragEngine({ duration: 200, easing: "ease" });
engine.props = { accepts: () => true, onDrop: () => undefined, onReturn: (a: any) => returns.push(a.id) };
engine.setTexts(texts);
engine.mount();
const zone = (id: string, list: string, top: number) => {
  const node = doc.body.appendChild(new El("div", { top, left: 0, width: 200, height: 100 }));
  const data = { id, kind: "zone", label: id, list, zone: true, shut: false };
  const item = { id, data, list: undefined, disabled: false, zone: true, node: null, handle: null, entry: null };
  engine.attach(item, node);
  engine.register(item, data, undefined, false);
};
zone("zp", "pins", 0);
zone("zg", "g", 200);
const pins = column(engine, ["p1"], "pins", 0);
const group = column(engine, ["a", "b"], "g", 200);
const b = group.b!;
engine.press("mouse", "b", { nativeEvent: { button: 0, clientX: 190, clientY: 260, target: b.handle, defaultPrevented: false }, target: b.handle, preventDefault() {} } as any);
fire("mousemove", { clientX: 190, clientY: 264 });
assert.equal(engine.stateOf("b"), 1);
fire("mousemove", { clientX: 190, clientY: 10 });              // 拖到置顶那一行上
// 调用方把它排进已置顶（排在 p1 后面），在那里重挂
b.node.parentNode.removeChild(b.node);
pins.p1!.node.parentNode.appendChild(b.node);
b.node.rect = { top: 50, left: 0, width: 200, height: 40 };
b.node.dataset.dragList = "pins";
engine.register(b.item, b.item.data, "pins", false);
engine.setList("pins", ["p1", "b"]);
engine.setList("g", ["a"]);
fire("mousemove", { clientX: 190, clientY: 12 });
await g.sleep(30);                                              // 下一帧重量，按新位置让位
assert.equal(pins.p1!.node.style.transform, "translate3d(0px, 50px, 0px)");   // p1 让到下面
// Esc：已置顶里让过位的当场带过渡滑回原位
fire("keydown", { code: "Escape", key: "Escape" });
assert.equal(pins.p1!.node.style.transform, "");
assert.equal(pins.p1!.node.style.transition, "transform 200ms ease");
assert.equal(b.node.style.transform, "");
await g.sleep(30);
assert.deepEqual(returns, ["b"]);                              // 画完这一帧再请调用方挪回去
assert.equal(pins.p1!.node.style.transform, "");               // 之后也不再写回让位
// 调用方没挪回去：等满 HOME_WAIT 后照旧滑回、取消，结束时去掉留下的过渡
await g.sleep(650);
const overlay = dragLayer()!.children.find((e: any) => e.className === "cc-drag-overlay");
overlay?.animations.forEach((a: any) => a.finish());
await g.sleep(10);
assert.equal(engine.stateOf("b"), 0);
assert.equal(pins.p1!.node.style.transform, "");
assert.equal(pins.p1!.node.style.transition, "");
""")

    def test_unmounting_mid_drag_leaves_no_listeners_behind(self):
        # 审查：键盘拖动中拖动区卸下（关掉会话栏、换分组方式），页面上收键的那一份没摘，之后第一下空格、回车、Tab 被吞掉
        self.run_page(r"""
const drops: string[][] = [];
let engine = new DragEngine(null);
engine.props = { accepts: (a: any, t: any) => a.list === t.list, onDrop: (a: any, t: any) => drops.push([a.id, t.id]) };
engine.setTexts(texts);
let unmount = engine.mount();
let cards = column(engine, ["a", "b", "c"]);
engine.press("keyboard", "a", { ...key("Space"), target: cards.a.handle } as any);
await g.sleep(5);
fire("keydown", { code: "ArrowDown", key: "ArrowDown" });
unmount();
assert.equal(engine.stateOf("a"), 0);
for (const code of ["Space", "Enter", "Tab", "Escape"]) {
  const event = fire("keydown", { code, key: code === "Space" ? " " : code });
  assert.equal(event.defaultPrevented, false, code);
}
assert.deepEqual(drops, []);
// 拿起前就卸下（按下还没到时间）：下一个任务里也不再挂上收键
engine = new DragEngine(null);
engine.props = { accepts: () => true, onDrop: (a: any, t: any) => drops.push([a.id, t.id]) };
engine.setTexts(texts);
unmount = engine.mount();
cards = column(engine, ["a", "b"], "k", 500);
engine.press("keyboard", "a", { ...key("Space"), target: cards.a.handle } as any);
unmount();
await g.sleep(5);
assert.equal(fire("keydown", { code: "Space", key: " " }).defaultPrevented, false);
// 鼠标拖动中卸下：移动、松开不再回调；按键还按着，松开那一下不当点击，之后照常
engine = new DragEngine(null);
engine.props = { accepts: () => true, onDrop: (a: any, t: any) => drops.push([a.id, t.id]) };
engine.setTexts(texts);
unmount = engine.mount();
cards = column(engine, ["a", "b"], "m", 700);
engine.press("mouse", "a", { nativeEvent: { button: 0, clientX: 190, clientY: 710, target: cards.a.handle, defaultPrevented: false }, target: cards.a.handle, preventDefault() {} } as any);
fire("mousemove", { clientX: 190, clientY: 720 });
assert.equal(engine.stateOf("a"), 1);
unmount();
assert.equal(fire("mousemove", { clientX: 190, clientY: 760 }).defaultPrevented, false);
fire("mouseup");
const click = fire("click", { detail: 1 });
assert.ok(click.defaultPrevented || (click as any).cancelBubble);
await g.sleep(80);
const later = fire("click", { detail: 1 });
assert.ok(!later.defaultPrevented && !(later as any).cancelBubble);
assert.deepEqual(drops, []);
""")

    def test_keyboard_focus_comes_back_after_the_row_is_moved_by_a_redraw(self):
        # 审查：键盘拖动中推送重排把被拖那一行（或它所在的组）挪了位置，焦点掉到 body；列表先后一变当场接回把手，按键时也接回。
        # 焦点被人点到别处的不抢
        self.run_page(r"""
const engine = new DragEngine(null);
engine.props = { accepts: (a: any, t: any) => a.list === t.list, onDrop: () => undefined };
engine.setTexts(texts);
engine.mount();
const cards = column(engine, ["a", "b", "c"]);
engine.press("keyboard", "b", { ...key("Space"), target: cards.b.handle } as any);
await g.sleep(5);
cards.b.handle.focus();
fire("keydown", { code: "ArrowDown", key: "ArrowDown" });
assert.equal(doc.activeElement, cards.b.handle);
// React 挪动节点：挪动的节点失焦
const box = cards.b.node.parentNode;
box.removeChild(cards.b.node);
box.appendChild(cards.b.node);
doc.activeElement = doc.body;
engine.setList("cards", ["c", "a", "b"]);
assert.equal(doc.activeElement, cards.b.handle);                // 同一次提交里接回
doc.activeElement = doc.body;
fire("keydown", { code: "ArrowUp", key: "ArrowUp" });
assert.equal(doc.activeElement, cards.b.handle);                // 按键时也接回
cards.c.handle.focus();
engine.setList("cards", ["a", "c", "b"]);
assert.equal(doc.activeElement, cards.c.handle);                // 不是掉了焦点：不抢
fire("keydown", { code: "Escape", key: "Escape" });
""")

    def test_the_lifted_item_leaving_every_list_cancels_at_once(self):
        # 测试窗口第 12 轮 D15（2/2）：键盘拿起公司组头「1010」、鼠标打开「分组和排序」点「月份」。拖动区没卸（侧栏还是同一个），
        # 是被拖那一组和它那一列的各组卸下了：原来没人结束这次拖动——浮层还浮在月份列表上、收键的监听还挂着，下一下 Tab 被吞、念
        # 「已取消移动「」。」。现在这一轮同步的事做完（React 这次提交）一核对，被拖那一项不在任何一列里就当场取消
        self.run_page(r"""
import { isDragging } from "@/lib/layers";
const drops: string[][] = [];
const actives: Array<string | null> = [];
const engine = new DragEngine({ duration: 200, easing: "ease" });
engine.props = { accepts: (a: any, t: any) => a.list === t.list, onDrop: (a: any, t: any) => drops.push([a.id, t.id]), onActive: (a: any) => actives.push(a?.id ?? null) };
engine.setTexts(texts);
engine.mount();
const live = doc.body.children.find((e: any) => e.attrs.get("aria-live") === "assertive");
const overlays = () => dragLayer()!.children.filter((e: any) => e.className === "cc-drag-overlay");
// React 卸下一项：注销、摘节点（把手、整项的 ref 回 null）
const unmount = (entry: { node: any; item: any }) => {
  engine.attach(entry.item, null);
  engine.attachHandle(entry.item, null);
  engine.unregister(entry.item);
  entry.node.remove();
};
const press = (cards: any, id: string) => {
  cards[id].handle.focus();
  engine.press("keyboard", id, { ...key("Space"), target: cards[id].handle } as any);
};
const down = () => fire("keydown", { code: "ArrowDown", key: "ArrowDown" });

// ① 换分组方式：公司组那一列整个换掉（各组卸下，列还挂着、先后变空），焦点所在的组头卸下、掉到 body
const ids = ["company:1010", "company:2020", "company:3030"];
const groups = column(engine, ids, "companies");
press(groups, "company:1010");
await g.sleep(5);
down();
assert.equal(live.textContent, "moved company:1010 2/3");
for (const id of ids) unmount(groups[id]!);
doc.activeElement = doc.body;
engine.dropList("companies", ids);
engine.setList("companies", []);
await Promise.resolve();                                         // 这次提交做完：当场核对，不等下一帧
assert.equal(engine.stateOf("company:1010"), 0);
assert.equal(isDragging(), false);
assert.equal(overlays().length, 0);                              // 浮层当场卸下（没有原位可滑回）
assert.equal(live.textContent, "cancel company:1010");          // 名字用拿起时的
assert.deepEqual(actives, ["company:1010", null]);
assert.deepEqual(drops, []);
for (const code of ["Tab", "Escape", "Space", "ArrowDown", "Enter"]) {
  assert.equal(fire("keydown", { code, key: code === "Space" ? " " : code }).defaultPrevented, false, code);   // 之后的按键不被吞
}
assert.equal(live.textContent, "cancel company:1010");

// ② 只有被拖那一项没了（别的设备删了这段对话）：别的项留着，让位与过渡当场清掉
const rows = column(engine, ["r1", "r2", "r3"], "rows:A", 300);
press(rows, "r2");
await g.sleep(5);
down();
assert.equal(rows.r3!.node.style.transform, "translate3d(0px, -50px, 0px)");
assert.equal(rows.r3!.node.style.transition, "transform 200ms ease");
unmount(rows.r2!);
doc.activeElement = doc.body;
engine.setList("rows:A", ["r1", "r3"]);
await Promise.resolve();
assert.equal(engine.stateOf("r2"), 0);
assert.equal(live.textContent, "cancel r2");
for (const id of ["r1", "r3"]) {
  assert.equal(rows[id]!.node.style.transform, "", id);
  assert.equal(rows[id]!.node.style.transition, "", id);
}
assert.equal(overlays().length, 0);

// ③ 同一次提交里先卸后挂（拖进另一列重挂、挪回原列、推送重挂）：不算消失，接着拖
const pair = column(engine, ["x", "y"], "m", 600);
press(pair, "x");
await g.sleep(5);
const again = { id: "x", data: pair.x!.item.data, list: undefined, disabled: false, zone: false, node: null, handle: null, entry: null };
engine.attach(pair.x!.item, null);
engine.attachHandle(pair.x!.item, null);
engine.unregister(pair.x!.item);
engine.attach(again, pair.x!.node);
engine.attachHandle(again, pair.x!.handle);
engine.register(again, again.data, "m", false);
await g.sleep(0);
assert.equal(engine.stateOf("x"), 2);
down();
assert.equal(live.textContent, "moved x 2/2");
fire("keydown", { code: "Escape", key: "Escape" });
overlays().forEach((overlay: any) => overlay.animations.forEach((a: any) => a.finish()));
await g.sleep(5);
assert.equal(engine.stateOf("x"), 0);
assert.equal(live.textContent, "cancel x");

// ④ 瞄着的那一项没了：瞄准退回它自己（K-04），让位按此刻重算，放下不交调用方、念取消
const q = column(engine, ["q1", "q2", "q3"], "q", 100);
press(q, "q1");
await g.sleep(5);
down();
assert.equal(q.q1!.node.style.transform, "translate3d(0px, 50px, 0px)");
unmount(q.q2!);
engine.setList("q", ["q1", "q3"]);
await Promise.resolve();
assert.equal(engine.stateOf("q1"), 2);                           // 拿着的那一项还在：接着拖
assert.equal(q.q1!.node.style.transform, "");
assert.equal(overlays()[0]!.style.transform, "translate3d(0px, 0px, 0) scaleX(1) scaleY(1)");   // 浮层回到它自己的位置
assert.equal(live.textContent, "moved q1 1/2");
fire("keydown", { code: "Space", key: " " });
await g.sleep(5);
assert.equal(engine.stateOf("q1"), 0);
assert.deepEqual(drops, []);
assert.equal(live.textContent, "cancel q1");

// ⑤ 鼠标拖动中那一项没了（这次只有它卸下、列的先后还没跟着变）：同样当场取消；之后移动不再交拖动，按键还按着，松开那一下
//    不当点击
const m = column(engine, ["m1", "m2"], "n", 700);
engine.press("mouse", "m1", { nativeEvent: { button: 0, clientX: 190, clientY: 710, target: m.m1!.handle, defaultPrevented: false }, target: m.m1!.handle, preventDefault() {} } as any);
fire("mousemove", { clientX: 190, clientY: 720 });
assert.equal(engine.stateOf("m1"), 1);
unmount(m.m1!);
await Promise.resolve();
assert.equal(engine.stateOf("m1"), 0);
assert.equal(live.textContent, "cancel m1");
assert.equal(overlays().length, 0);
assert.equal(fire("mousemove", { clientX: 190, clientY: 760 }).defaultPrevented, false);
fire("mouseup");
const click = fire("click", { detail: 1 });
assert.ok(click.defaultPrevented || (click as any).cancelBubble);
await g.sleep(80);
const later = fire("click", { detail: 1 });
assert.ok(!later.defaultPrevented && !(later as any).cancelBubble);
assert.deepEqual(drops, []);

// ⑥ 松手后落位途中，落点那一项没了：落位播完不交调用方，念取消（不念「放在第 2 位」）
const w = column(engine, ["w1", "w2"], "w", 200);
engine.press("mouse", "w1", { nativeEvent: { button: 0, clientX: 190, clientY: 210, target: w.w1!.handle, defaultPrevented: false }, target: w.w1!.handle, preventDefault() {} } as any);
fire("mousemove", { clientX: 190, clientY: 215 });
fire("mousemove", { clientX: 190, clientY: 265 });
assert.equal(live.textContent, "moved w1 2/2");
fire("mouseup");
assert.equal(engine.stateOf("w1"), 1);                          // 还在落位
unmount(w.w2!);
engine.setList("w", ["w1"]);
await Promise.resolve();
overlays().forEach((overlay: any) => overlay.animations.forEach((a: any) => a.finish()));
await g.sleep(5);
assert.equal(engine.stateOf("w1"), 0);
assert.deepEqual(drops, []);
assert.equal(live.textContent, "cancel w1");

// ⑦ 松手后落位途中，被拖那一项自己没了：不等落位播完，当场按取消收尾（不交调用方），浮层当场卸下、让位清掉
const v = column(engine, ["v1", "v2"], "v", 200);
engine.press("mouse", "v1", { nativeEvent: { button: 0, clientX: 190, clientY: 210, target: v.v1!.handle, defaultPrevented: false }, target: v.v1!.handle, preventDefault() {} } as any);
fire("mousemove", { clientX: 190, clientY: 215 });
fire("mousemove", { clientX: 190, clientY: 265 });
assert.equal(live.textContent, "moved v1 2/2");
fire("mouseup");
assert.equal(engine.stateOf("v1"), 1);
unmount(v.v1!);
engine.setList("v", ["v2"]);
await Promise.resolve();
assert.equal(engine.stateOf("v1"), 0);
assert.equal(overlays().length, 0);
assert.deepEqual(drops, []);
assert.equal(live.textContent, "cancel v1");
assert.equal(v.v2!.node.style.transform, "");
assert.equal(v.v2!.node.style.transition, "");
""")

    def test_a_keyboard_drag_only_takes_keys_on_its_handle(self):
        # 测试窗口第 12 轮 D15（2/2）：组头键盘拿起后，鼠标打开「分组和排序」、在菜单里按键：↓ 推着被拖的组头走（「移到第 3 位」），
        # 回车选「月份」时拖动也被放下（「已置顶「」。」）。键盘拖动在 document 上收键，按在别处也算。现在只认把手上的按键：焦点被人
        # 移到把手以外能聚焦的地方，当作取消（同 Esc），之后的按键归新焦点；焦点只是掉到 body（React 挪节点）照旧接回把手（K-03）
        self.run_page(r"""
const drops: string[][] = [];
const make = (motion: any) => {
  const engine = new DragEngine(motion);
  engine.props = { accepts: (a: any, t: any) => a.list === t.list, onDrop: (a: any, t: any) => drops.push([a.id, t.id]) };
  engine.setTexts(texts);
  engine.mount();
  return engine;
};
const lives = () => doc.body.children.filter((e: any) => e.attrs.get("aria-live") === "assertive");
const overlays = () => dragLayer()!.children.filter((e: any) => e.className === "cc-drag-overlay");
const keyDown = (code: string) => fire("keydown", { code, key: code === "Space" ? " " : code });
const engine = make(null);
const live = lives().at(-1);
const cards = column(engine, ["a", "b", "c"]);
// 列表外能聚焦的按钮（鼠标点开的菜单、输入框）
const menu = doc.body.appendChild(new El("button", { top: 400, left: 0, width: 100, height: 30 }));

// ① 焦点被人移到列表外（focusin）：当场取消，之后 ↓、回车、空格、Tab、Esc 都归它
cards.a!.handle.focus();
engine.press("keyboard", "a", { ...key("Space"), target: cards.a!.handle } as any);
await g.sleep(5);
keyDown("ArrowDown");
assert.equal(live.textContent, "moved a 2/3");
menu.focus();
fire("focusin");
await Promise.resolve();
assert.equal(engine.stateOf("a"), 0);
assert.equal(live.textContent, "cancel a");
assert.equal(overlays().length, 0);
for (const id of ["a", "b", "c"]) assert.equal(cards[id]!.node.style.transform, "", id);
for (const code of ["ArrowDown", "Enter", "Space", "Tab", "Escape"]) assert.equal(keyDown(code).defaultPrevented, false, code);
assert.deepEqual(drops, []);
assert.equal(live.textContent, "cancel a");
await g.sleep(30);
assert.equal(doc.activeElement, menu);                          // 焦点是人自己挪的：不抢回来

// ② 焦点掉到 body 不算取消，按键时接回把手；焦点在别处时按 ↓、回车（还没等到 focusin）：不挪、不放下，当场取消
cards.b!.handle.focus();
engine.press("keyboard", "b", { ...key("Space"), target: cards.b!.handle } as any);
await g.sleep(5);
doc.activeElement = doc.body;
keyDown("ArrowDown");
assert.equal(doc.activeElement, cards.b!.handle);
assert.equal(engine.stateOf("b"), 2);
assert.equal(live.textContent, "moved b 3/3");
menu.focus();
assert.equal(keyDown("ArrowDown").defaultPrevented, false);
assert.equal(engine.stateOf("b"), 0);
assert.equal(live.textContent, "cancel b");
assert.equal(cards.c!.node.style.transform, "");
assert.equal(keyDown("Enter").defaultPrevented, false);
assert.deepEqual(drops, []);
assert.equal(doc.activeElement, menu);

// ③ 有动效时同 Esc：先滑回（让位带过渡清掉、浮层起播滑回），滑回播完才取消；滑回途中的按键也归新焦点
const smooth = make({ duration: 200, easing: "ease" });
const live2 = lives().at(-1);
const k = column(smooth, ["k1", "k2"], "k", 500);
k.k1!.handle.focus();
smooth.press("keyboard", "k1", { ...key("Space"), target: k.k1!.handle } as any);
await g.sleep(5);
keyDown("ArrowDown");
assert.equal(k.k2!.node.style.transform, "translate3d(0px, -50px, 0px)");
menu.focus();
fire("focusin");
await Promise.resolve();
assert.equal(k.k2!.node.style.transform, "");
assert.equal(k.k2!.node.style.transition, "transform 200ms ease");
const overlay = overlays().at(-1);
assert.equal(overlay.dataset.dropping, "");
assert.equal(keyDown("ArrowUp").defaultPrevented, false);
assert.equal(keyDown("Space").defaultPrevented, false);
overlay.animations.forEach((a: any) => a.finish());
await g.sleep(5);
assert.equal(smooth.stateOf("k1"), 0);
assert.equal(live2.textContent, "cancel k1");
assert.equal(k.k2!.node.style.transition, "");
assert.equal(overlays().length, 0);
assert.deepEqual(drops, []);
""")


# 第 13 轮：在假页面上跑真的 React（react-dom/client），看落定那次提交里 React 自己做了什么。在 ENGINE_DOM 上补 React 渲染、提交、
# 还原焦点要用的那一点：文本节点、insertBefore、节点名、祖先的滚动位置、按类名找（closest / querySelector 的 ".x"）。挪动 / 卸下
# 装着焦点的节点时同 Chrome 同步失焦（记成 lost），focus / blur 也都记进 focusLog；动效变量按 200ms / ease 给
REACT_DOM = r"""
g.HTMLIFrameElement = class {};
doc.defaultView = g;
class Txt { nodeType = 3; parentNode: any = null; children: any[] = []; nodeValue: string; constructor(v: string) { this.nodeValue = v; } get ownerDocument() { return doc; } }
doc.createTextNode = (v: string) => new Txt(v);
doc.createElementNS = (_ns: string, tag: string) => new El(tag);
const P: any = El.prototype;
Object.defineProperty(P, "nodeName", { get() { return this.tag.toUpperCase(); } });
Object.defineProperty(P, "tagName", { get() { return this.tag.toUpperCase(); } });
Object.defineProperty(P, "firstChild", { get() { return this.children[0] ?? null; } });
Object.defineProperty(P, "lastChild", { get() { return this.children.at(-1) ?? null; } });
P.scrollTop = 0;
P.scrollLeft = 0;
const focusLog: any[] = [];
const idOf = (el: any) => el?.getAttribute?.("id") ?? el?.tag;
const dropFocus = (node: any) => {
  const at = doc.activeElement;
  if (at && at !== doc.body && node.contains?.(at)) {
    focusLog.push(["lost", idOf(at)]);
    doc.activeElement = doc.body;
  }
};
P.insertBefore = function (child: any, before: any) {
  if (child.parentNode) {
    dropFocus(child);
    child.parentNode.children = child.parentNode.children.filter((c: any) => c !== child);
  }
  child.parentNode = this;
  const at = before ? this.children.indexOf(before) : -1;
  if (at < 0) this.children.push(child);
  else this.children.splice(at, 0, child);
  return child;
};
const appendRaw = P.appendChild;
P.appendChild = function (child: any) {
  if (child.parentNode) dropFocus(child);
  return appendRaw.call(this, child);
};
const removeRaw = P.removeChild;
P.removeChild = function (child: any) {
  dropFocus(child);
  return removeRaw.call(this, child);
};
P.focus = function (options?: any) {
  focusLog.push(["focus", idOf(this), options ?? null]);
  doc.activeElement = this;
};
P.blur = function () {
  focusLog.push(["blur", idOf(this)]);
  if (doc.activeElement === this) doc.activeElement = doc.body;
};
const hasClass = (el: any, name: string) => String(el.getAttribute?.("class") ?? "").split(/\s+/).includes(name);
P.closest = function (selector: string) {
  if (!selector.startsWith(".")) return null;
  for (let n: any = this; n instanceof El; n = n.parentNode) if (hasClass(n, selector.slice(1))) return n;
  return null;
};
P.querySelector = function (selector: string) {
  if (!selector.startsWith(".")) return null;
  const walk = (e: any): any => {
    for (const kid of e.children) {
      if (!(kid instanceof El)) continue;
      if (hasClass(kid, selector.slice(1))) return kid;
      const found = walk(kid);
      if (found) return found;
    }
    return null;
  };
  return walk(this);
};
const computedRaw = g.getComputedStyle;
g.getComputedStyle = (el: any) => ({
  ...computedRaw(el),
  getPropertyValue: (name: string) => (name === "--cc-dur" ? "200ms" : name === "--cc-ease" ? "ease" : ""),
});
const find = (root: any, id: string): any => {
  if (idOf(root) === id) return root;
  for (const kid of root.children ?? []) {
    const found = find(kid, id);
    if (found) return found;
  }
  return null;
};
"""

# 一列三张卡（a / b / c，各自的把手、「···」、输入框），用共用拖动排序；落点交给调用方后按 React 的写法换顺序。量的框：卡片顶 0 / 50 / 100，
# 高 40
REACT_COLUMN = r"""
import { createElement as h, memo, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { DragArea, DragList, useSortableItem } from "@/components/ui/drag";
import { isDragging } from "@/lib/layers";
const drops: string[][] = [];
const renders: Record<string, number> = {};
const handles: Record<string, any> = {};
let bump: () => void = () => undefined;
let reorder: (ids: string[]) => void = () => undefined;
const Head = memo(function Head({ id, handle }: { id: string; handle: any }) {
  renders[id] = (renders[id] ?? 0) + 1;
  return h("button", { ...handle, id: `head-${id}` }, id);
});
function Card({ id, ownFocus }: { id: string; ownFocus: boolean }) {
  const drag = useSortableItem({ id, kind: "card", label: id, list: "cards" }, { ownFocus });
  handles[id] = drag.handle;
  return h("section", { ...drag.node, id }, h(Head, { id, handle: drag.handle }), h("button", { id: `more-${id}` }, "…"), h("input", { id: `in-${id}` }));
}
function Column({ ownFocus }: { ownFocus: boolean }) {
  const [order, setOrder] = useState(["a", "b", "c"]);
  const [, setTick] = useState(0);
  bump = () => setTick((n) => n + 1);
  reorder = setOrder;
  const onDrop = (active: any, target: any) => {
    drops.push([active.id, target.id]);
    focusLog.push(["drop", isDragging()]);
    const next = order.filter((id) => id !== active.id);
    next.splice(order.indexOf(target.id), 0, active.id);
    setOrder(next);
  };
  return h(DragArea, { accepts: (a: any, t: any) => a.list === t.list, onDrop, texts } as any,
    h(DragList, { id: "cards", items: order }, order.map((id) => h(Card, { key: id, id, ownFocus }))));
}
const host = doc.body.appendChild(new El("div"));
const root = createRoot(host);
const sectionsNow = () => host.children.filter((e: any) => e.tag === "section");
const order = () => sectionsNow().map((e: any) => idOf(e));
// 框按此刻的先后摆（假页面的框不随 React 挪节点走）：卡片顶 0 / 50 / 100…，高 40
function layout() {
  sectionsNow().forEach((section: any, i: number) => {
    section.rect = { top: i * 50, left: 0, width: 200, height: 40 };
    for (const kid of section.children) kid.rect = { top: i * 50, left: 0, width: 200, height: 40 };
  });
}
function mountColumn(ownFocus = true) {
  flushSync(() => root.render(h(Column, { ownFocus })));
  layout();
}
const overlayNow = () => dragLayer()!.children.find((e: any) => e.className === "cc-drag-overlay") ?? null;
const liveNow = () => doc.body.children.find((e: any) => String(e.id).startsWith("DndLiveRegion-"));
// 鼠标在 id 的把手上按下，挪到 y = to 再松手（release = false：不松手）
async function mouseDrag(id: string, to: number, release = true) {
  layout();
  const target = find(host, `head-${id}`);
  const y = order().indexOf(id) * 50 + 10;
  handles[id].onMouseDown({ nativeEvent: { button: 0, clientX: 100, clientY: y, target, defaultPrevented: false }, target, preventDefault() {} });
  fire("mousemove", { clientX: 100, clientY: y + 5 });
  await g.sleep(0);
  fire("mousemove", { clientX: 100, clientY: to });
  await g.sleep(0);
  if (release) fire("mouseup");
}
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class Round13DropTests(unittest.TestCase):
    """第 13 轮（docs/work/2026-10-17-drag-engine.md「第 13 轮」）：落定那次提交前把焦点拿出来、提交后原样放回（改法 1）；落定分两拍，
    画面先落定、下一帧再提交（改法 2）；落定不再多读一次浮层样式（3a）；侧栏把手不随在不在拖换一份（3b）；长按菜单打开那一遍就渲染
    内容、组头偏移在打开那一刻量（4）。行为、播报、焦点终点、滚动终点都不变。"""

    def run_page(self, body, stubs=None, react=True):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + ENGINE_DOM + REACT_DOM
               + (REACT_COLUMN if react else "") + body + "\n(globalThis as any).done();\n", stubs=stubs)

    def test_focus_leaves_before_the_drop_commit_and_comes_back_without_react_restoring_it(self):
        # A14 trace：长按出菜单后再拖，焦点被组件库还到「···」（就在被拖那一项里）；往后挪时 React 提交要把这一项的节点挪个位置，
        # 挪的那一下浏览器同步失焦、现算样式，React 提交后再读祖先滚动、focus()（不带 preventScroll，滚动抽屉）再写回滚动。现在提交前
        # 先失焦、提交后 focus({ preventScroll: true }) 放回：事件仍是先失焦、后获焦（失焦在结束拖动、交调用方以后），焦点终点同一个
        self.run_page(r"""
doc.documentElement.dataset.ccMotion = "reduce";                 // 没有落位：松手当场一拍提交
mountColumn();
find(host, "more-a").focus();
focusLog.length = 0;
await mouseDrag("a", 70);                                    // a 往后挪到 b 的位置
assert.deepEqual(drops, [["a", "b"]]);
assert.deepEqual(order(), ["b", "a", "c"]);                      // React 挪的正是 a 那一节
assert.deepEqual(focusLog, [["drop", false], ["blur", "more-a"], ["focus", "more-a", { preventScroll: true }]]);
assert.equal(doc.activeElement, find(host, "more-a"));
assert.equal(isDragging(), false);
// 往前挪：React 挪的是被越过的那几项，被拖那一项不动，焦点也不动——一个失焦、获焦都没有（同原来）
find(host, "more-c").focus();
focusLog.length = 0;
await mouseDrag("c", 0);                                    // c（此刻排第三）挪到最前
assert.deepEqual(order(), ["c", "b", "a"]);
assert.deepEqual(focusLog, [["drop", false]]);
assert.equal(doc.activeElement, find(host, "more-c"));
// 焦点在输入框里：照旧交给 React（它还要还原选区），React 自己失焦再还原
find(host, "in-c").focus();
focusLog.length = 0;
await mouseDrag("c", 110);                                   // c（此刻排第一）挪到最后
assert.deepEqual(order(), ["b", "a", "c"]);
assert.deepEqual(focusLog, [["drop", false], ["lost", "in-c"], ["focus", "in-c", null]]);
// 焦点不在被拖那一项里：不动
const outside = doc.body.appendChild(new El("button"));
outside.setAttribute("id", "outside");
outside.focus();
focusLog.length = 0;
await mouseDrag("b", 70);
assert.deepEqual(order(), ["a", "b", "c"]);
assert.deepEqual(focusLog, [["drop", false]]);
assert.equal(doc.activeElement, outside);
// 取消（Esc）不交调用方、不挪节点：不动焦点
find(host, "more-a").focus();
focusLog.length = 0;
await mouseDrag("a", 70, false);
fire("keydown", { code: "Escape", key: "Escape" });
fire("mouseup");
await g.sleep(0);
assert.deepEqual(focusLog, []);
assert.deepEqual(order(), ["a", "b", "c"]);
assert.equal(doc.activeElement, find(host, "more-a"));
""")

    def test_the_drop_paints_first_and_commits_on_the_next_frame(self):
        # 落位播完那一下只卸浮层、亮出空档（去掉 data-drag-source）、念放下：它此刻已由让位位移摆在落点上，画面就是最终的样子；调用方
        # 存顺序、整列重排、去掉让位等这一帧画完以后同一次提交，画面不变。期间 React 因别的原因重画，空档不再藏回去；期间又按下就当场提交
        self.run_page(r"""
mountColumn();
const live = liveNow();
const a = find(host, "a");
find(host, "more-a").focus();
focusLog.length = 0;
await mouseDrag("a", 70);
const overlay = overlayNow();
assert.equal(a.getAttribute("data-drag-source"), "gap");
assert.equal(a.style.transform, "translate3d(0px, 50px, 0px)");  // 空档由让位摆到 b 的位置
const landing = overlay.animations.at(-1);
assert.equal(landing.frames[1].transform, "translate3d(0px, 50px, 0) scaleX(1) scaleY(1)");   // 浮层落的就是这里
assert.deepEqual(drops, []);
landing.finish();
await Promise.resolve();
// 第一拍
assert.equal(overlay.parentNode, null);
assert.equal(a.getAttribute("data-drag-source"), null);
assert.equal(a.style.transform, "translate3d(0px, 50px, 0px)");
assert.equal(find(host, "b").style.transform, "translate3d(0px, -50px, 0px)");
assert.equal(live.textContent, "dropped a 2/3");
assert.deepEqual(drops, []);
assert.deepEqual(order(), ["a", "b", "c"]);
assert.equal(isDragging(), false);                               // 「正在拖」同原来在落位播完这一刻撤
flushSync(() => bump());                                         // 这时 React 因别的原因重画：属性没变，不再写回空档
assert.equal(a.getAttribute("data-drag-source"), null);
assert.deepEqual(focusLog, []);
const played = overlay.animations.length;
// 第二拍：这一帧画完以后
await g.sleep(10);
assert.equal(overlay.animations.length, played);                 // 浮层第一拍已经卸了，提交时不再另播一段落位
assert.deepEqual(drops, [["a", "b"]]);
assert.deepEqual(order(), ["b", "a", "c"]);
assert.equal(a.style.transform, "");
assert.equal(a.style.transition, "");
assert.equal(find(host, "b").style.transform, "");
assert.equal(a.getAttribute("data-drag-source"), null);
assert.equal(isDragging(), false);
assert.deepEqual(focusLog, [["drop", false], ["blur", "more-a"], ["focus", "more-a", { preventScroll: true }]]);
assert.equal(live.textContent, "dropped a 2/3");
// 两拍之间又按下（下一次拖动、点按）：当场提交，再交给这一下
await mouseDrag("b", 70);
overlayNow().animations.at(-1).finish();
await Promise.resolve();
assert.deepEqual(order(), ["b", "a", "c"]);
page.dispatchEvent(new Event("pointerdown"));
assert.deepEqual(order(), ["a", "b", "c"]);
assert.equal(isDragging(), false);
// 落位途中就按下（浮层还在路上）：照旧一拍，提交时浮层接着播完再卸
await mouseDrag("a", 70);
const early = overlayNow();
page.dispatchEvent(new Event("pointerdown"));
assert.deepEqual(order(), ["b", "a", "c"]);
assert.equal(early.parentNode !== null, true);
early.animations.at(-1).finish();
await g.sleep(0);
assert.equal(early.parentNode, null);
""")

    def test_drops_that_change_more_than_the_gap_stay_in_one_go(self):
        # 只在第一拍画面等于最终画面时分拍：拖动中版面变过（推送多了一项、临时补出的已置顶、拖进另一列又回来：提交时调用方可能还要改
        # 版面）、整批多选、落进收着的整块、落位没播完，照旧落位播完那一下就提交
        self.run_page(r"""
mountColumn();
await mouseDrag("a", 70, false);
flushSync(() => reorder(["a", "b", "c", "d"]));                  // 拖动中这一列多了一项：版面变过，下一帧重量
layout();
fire("mousemove", { clientX: 100, clientY: 72 });
await g.sleep(5);
fire("mouseup");
const overlay = overlayNow();
overlay.animations.at(-1).finish();
await Promise.resolve();
assert.deepEqual(drops, [["a", "b"]]);                           // 落位播完那一下就交调用方（一拍）
assert.deepEqual(order(), ["b", "a", "c", "d"]);
assert.equal(overlay.parentNode, null);
assert.equal(isDragging(), false);
""")
        drag = read("components/ui/drag.tsx")
        settled = drag.split("  private settled(d: Session): boolean {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("d.relaid || (d.data.count ?? 0) > 1", settled)        # 版面变过、整批多选
        self.assertIn('landing.playState !== "finished" || d.overlay.dataset.dropping === "into"', settled)   # 落位没播完、落进收着的
        self.assertIn("item!.list === d.data.list", settled)                 # 拖进另一列的那一行提交时换一种行重挂
        self.assertIn("!this.holdsOthers(d.id, node)", settled)              # 装着别的拖动项的一整块（侧栏公司组）
        self.assertIn("if (!this.transition || !this.alive || this.committing", settled)   # 减少动态效果、卸下、React 提交里
        again = drag.split("  private remeasureSoon() {", 1)[1].split("\n  }\n", 1)[0]
        self.assertLess(again.index("d.relaid = true;"), again.index("afterPaint(() => {"))

    def test_dragging_a_whole_block_stays_in_one_go(self):
        # 被拖的那一项本身装着别的拖动项（侧栏公司组：它也是组里那一列的整块落点）：先后一变调用方提交时可能连带改版面（前 5 家默认
        # 展开，拖过第 5 家时一家收起、一家展开；复刻页实测跳 138px），落位播完那一下就提交；同样的拖法换成普通的一项，分两拍
        self.run_page(r"""
for (const whole of [true, false]) {
  const drops: string[][] = [];
  const engine = new DragEngine({ duration: 200, easing: "ease" });
  engine.props = { accepts: (a: any, t: any) => a.list === t.list, onDrop: (a: any, t: any) => drops.push([a.id, t.id]) };
  engine.setTexts(texts);
  const unmount = engine.mount();
  const top = whole ? 0 : 300;
  const cards = column(engine, ["a", "b", "c"], whole ? "blocks" : "plain", top);
  if (whole) {
    const data = { id: "zone:a", kind: "group", label: "a", list: "rows:a", zone: true, shut: false };
    const zone: any = { id: data.id, data, list: undefined, disabled: false, zone: true, node: null, handle: null, entry: null };
    engine.attach(zone, cards.a.node);                           // 同一个节点也是这一组的整块落点
    engine.register(zone, data, undefined, false);
  }
  engine.press("mouse", "a", { nativeEvent: { button: 0, clientX: 190, clientY: top + 10, target: cards.a.handle, defaultPrevented: false }, target: cards.a.handle, preventDefault() {} } as any);
  fire("mousemove", { clientX: 190, clientY: top + 15 });
  fire("mousemove", { clientX: 190, clientY: top + 65 });
  fire("mouseup");
  const overlay = dragLayer()!.children.find((e: any) => e.className === "cc-drag-overlay");
  overlay.animations.at(-1).finish();
  await Promise.resolve();
  assert.equal(overlay.parentNode, null);
  assert.deepEqual(drops, whole ? [["a", "b"]] : [], String(whole));
  await g.sleep(10);
  assert.deepEqual(drops, [["a", "b"]], String(whole));
  unmount();
}
""", react=False)

    def test_own_focus_handles_stay_the_same_while_the_drag_starts_and_ends(self):
        # 侧栏的把手（ownFocus：行、组头开关）不随「在不在拖」改任何属性，拿起、落定时被拖那一组的组头（memo）不跟着重画；
        # 卡片把手（不带 ownFocus）被拖时多一个 aria-pressed，照旧换一份
        self.run_page(r"""
doc.documentElement.dataset.ccMotion = "reduce";
mountColumn(true);
assert.deepEqual(renders, { a: 1, b: 1, c: 1 });
const before = handles.a;
await mouseDrag("a", 70, false);
assert.equal(find(host, "a").getAttribute("data-drag-source"), "gap");   // 被拖那一项照常重画成空档
assert.equal(handles.a, before);
assert.equal(renders.a, 1);
fire("mouseup");
await g.sleep(0);
assert.deepEqual(order(), ["b", "a", "c"]);
assert.equal(handles.a, before);
assert.deepEqual(renders, { a: 1, b: 1, c: 1 });
assert.equal(find(host, "head-a").getAttribute("aria-pressed"), null);
flushSync(() => root.render(h("div")));
for (const id of Object.keys(renders)) delete renders[id];
mountColumn(false);
const card = handles.a;
await mouseDrag("a", 70, false);
assert.notEqual(handles.a, card);
assert.equal(find(host, "head-a").getAttribute("aria-pressed"), "true");
assert.equal(renders.a, 2);
fire("mouseup");
await g.sleep(0);
assert.equal(find(host, "head-a").getAttribute("aria-pressed"), null);
assert.equal(renders.a, 3);
""")

    def test_the_landing_does_not_read_the_overlay_again_once_it_has_played(self):
        # 3a：结束拖动时松手那一下已经起播过的落位（正常放下都是），不再读浮层此刻的样子（读计算样式 = 现算一次样式）
        self.run_page(r"""
import { dropAnimation, landOverlay } from "@/components/ui/drag";
const motion = { duration: 200, easing: "ease" };
const node = doc.body.appendChild(new El("section", { top: 100, left: 0, width: 200, height: 40 }));
const overlay = dragLayer()!.appendChild(new El("div"));
Object.assign(overlay.style, { top: "0px", left: "0px", width: "200px", height: "40px", transform: "translate3d(0px, 60px, 0) scaleX(1) scaleY(1)" });
const played = landOverlay(overlay, node, motion, () => null)!;
assert.ok(played);
let transformReads = 0;
const args = (o: any) => ({ active: { id: "a", node, rect: { top: 100, left: 0, width: 200, height: 40, right: 200, bottom: 140 } },
  dragOverlay: { node: o, rect: { top: 60, left: 0, width: 200, height: 40 } },
  get transform() { transformReads += 1; return { x: 0, y: 60, scaleX: 1, scaleY: 1 }; } });
played.finish();
assert.equal(dropAnimation(motion, () => null)(args(overlay) as any), undefined);   // 已经播完：当场卸
assert.equal(transformReads, 0);
const fresh = dragLayer()!.appendChild(new El("div"));
assert.ok(dropAnimation(motion, () => null)(args(fresh) as any));                  // 没起播过：从此刻落到那一项，这才读
assert.equal(transformReads, 1);
""", react=False)

    def test_long_press_menus_render_their_content_in_the_opening_pass(self):
        # 4：菜单的弹层直接挂到 body（同组件库默认，第一遍就渲染内容，不再「先空、布局副作用 setMounted、再同步重画」）；手机公司组头
        # 菜单贴组头开关左边的偏移在打开那一遍渲染里量（版面还没被这次提交改过），不再提交后量完 setOffset 让整份菜单再渲染一遍
        fake = r"""
import { createElement } from "react";
const g = globalThis as any;
g.seen = [] as Array<{ part: string; props: any }>;
const part = (name: string) => (props: any) => {
  g.seen.push({ part: name, props });
  return createElement("div", { "data-part": name }, props.children);
};
export const DropdownMenu: any = Object.fromEntries(
  ["Root", "Trigger", "Portal", "Content", "Item", "Separator", "Sub", "SubTrigger", "SubContent", "RadioGroup", "RadioItem", "CheckboxItem",
   "ItemIndicator"].map((n) => [n, part(n)]),
);
export const Slot: any = { Root: part("Slot") };
"""
        stubs = {
            "radix-ui": fake,
            "@/components/ui/sidebar": "export const useSidebar = () => ({ isMobile: true, setOpenMobile() {} });",
            "@/hooks/use-mobile": "export const useIsMobile = () => false;\nexport const MOBILE_BREAKPOINT = 768;\nexport const MOBILE_QUERY = '(max-width: 767px)';",
            "@/components/ui/marquee": 'import { createElement } from "react";\nexport const Marquee = ({ className, children }: any) => createElement("span", { className }, children);',
        }
        self.run_page(r"""
import { createElement as h, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { GroupSortMenu } from "@/components/assistant-ui/sidebar-group";
import { DropdownMenu } from "@/components/ui/menu";
const host = doc.body.appendChild(new El("div"));
const root = createRoot(host);
let setOpen: (open: boolean) => void = () => undefined;
function Row() {
  const [open, set] = useState(false);
  setOpen = set;
  return h("div", { className: "cc-group-row" }, h("button", { className: "cc-group-toggle", id: "toggle" }), h(GroupSortMenu, { open, onOpenChange: set, alignHead: true }));
}
flushSync(() => root.render(h(Row)));
const trigger = host.querySelector(".cc-group-more");
host.querySelector(".cc-group-toggle").rect = { top: 0, left: 16, width: 200, height: 32 };
trigger.rect = { top: 4, left: 240, width: 24, height: 24 };
g.seen.length = 0;
flushSync(() => setOpen(true));
const contents = g.seen.filter((s: any) => s.part === "Content");
assert.equal(contents.length, 1);                                // 打开那一遍就是最终的样子，菜单内容只渲染一遍
assert.equal(contents[0].props.align, "start");
assert.equal(contents[0].props.alignOffset, -224);
const portals = g.seen.filter((s: any) => s.part === "Portal");
assert.ok(portals.length >= 1);
for (const portal of portals) assert.equal(portal.props.container, doc.body);   // 弹层直接挂到 body
// 收起：偏移留着（收起那一下位置不变），同原来
g.seen.length = 0;
flushSync(() => setOpen(false));
assert.equal(g.seen.filter((s: any) => s.part === "Content").at(-1).props.alignOffset, -224);
// 服务端渲染（测试）照旧不给 container，同组件库
g.seen.length = 0;
renderToStaticMarkup(h(DropdownMenu.Portal, null, "x"));
assert.equal(g.seen.find((s: any) => s.part === "Portal").props.container, undefined);
""", stubs=stubs, react=False)


# 浏览器发触摸事件的先后（Chrome）：touchmove 攒到下一帧开头才发给页面（帧开头先发排着的输入、再调动画帧回调），手指离开它自己的
# 防抖范围之前的几下干脆不发；计时器是另外的任务，到点就跑。真页面上量到的见 docs/work/2026-10-17-drag-engine.md
# 「第 13 轮测试机：按住 0.35 秒拖动偶发出菜单」。这里用真的长按（use-long-press）与真的手机按住拿起（HoldTouchSensor），按这个先后
# 手动发事件、走帧。
LONG_PRESS_PAGE = r"""
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { HoldTouchSensor } from "@/components/ui/drag";
import { useLongPress } from "@/hooks/use-long-press";
import { isDragMoving, markDragMoved, setDragging } from "@/lib/layers";
const g = globalThis as any;
g.addEventListener = () => {};
g.removeEventListener = () => {};
g.document.addEventListener = () => {};
g.document.removeEventListener = () => {};
g.document.querySelector = () => null;
g.getSelection = () => ({ removeAllRanges() {} });
g.MouseEvent = class { type: string; init: any; constructor(type: string, init: any) { this.type = type; this.init = init; } };
let frameId = 0;
const rafs = new Map<number, () => void>();
g.requestAnimationFrame = (run: () => void) => (rafs.set(++frameId, run), frameId);
g.cancelAnimationFrame = (id: number) => void rafs.delete(id);
let opened = 0;
let closed = 0;
let press: any;
renderToStaticMarkup(createElement(() => ((press = useLongPress(() => opened++, () => closed++)), null)));
/** 手指在 (100, 100) 按下一条行。drag：能拖（共用拖动按住 250ms 拿起）；react：React 那一份 onTouchMove / onTouchEnd 收不收得到
 *  （按下的节点被挪走以后收不到，触摸事件照样发给它本身）。 */
function finger({ drag = true, react = true } = {}) {
  const on: Record<string, (e: any) => void> = {};
  const target = { isConnected: true, addEventListener: (t: string, f: any) => (on[t] = f), removeEventListener: (t: string) => delete on[t], dispatchEvent: () => true };
  const start = { target, touches: [{ clientX: 100, clientY: 100 }], timeStamp: 0, preventDefault() {} };
  const log: string[] = [];
  press.onTouchStart(start);                                       // 行上先长按、再交给共用拖动（chain(press.onTouchStart, dragTouch)）
  if (drag)
    new HoldTouchSensor({
      active: "row:t1", event: start, options: { delay: 250, tolerance: 5 },
      onStart: () => { log.push("start"); setDragging(true); }, onMove: () => log.push("move"),
      onEnd: () => { log.push("end"); setDragging(false); }, onCancel: () => { log.push("cancel"); setDragging(false); },
      onAbort: () => log.push("abort"),
    } as any);
  const queued: number[] = [];
  return {
    log,
    /** 手指动到 y：这一下 touchmove 先排在浏览器里。 */
    to: (y: number) => queued.push(y),
    /** 一帧开头：排着的 touchmove 依次发给页面（按下的那个节点上的监听先收到，再冒泡到 React），然后才是动画帧回调。 */
    frame() {
      for (const y of queued.splice(0)) {
        const e = { touches: [{ clientX: 100, clientY: y }], cancelable: true, preventDefault() {} };
        on.touchmove?.(e);
        if (react) press.onTouchMove(e);
      }
      const due = [...rafs.values()];
      rafs.clear();
      for (const run of due) run();
    },
    up() {
      const e = { cancelable: true, prevented: false, preventDefault() { e.prevented = true; } };
      on.touchend?.(e);
      if (react) press.onTouchEnd(e);
      return e;
    },
  };
}
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class Round13LongPressRaceTests(unittest.TestCase):
    """第 13 轮测试机（10-18，三星 A14）：手机按住约 0.35 秒就开始拖，8 次有 3 次长按菜单仍在约 0.57 秒弹出、挂约 0.22 秒才收起。"""

    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + LONG_PRESS_PAGE + body + "\n(globalThis as any).done();\n",
               stubs={"socket.io-client": "export function io() { return { on() { return this; }, off() { return this; }, connected: true }; }"})

    def test_a_move_made_before_the_menu_time_cancels_it_even_when_it_reaches_the_page_after_the_timer(self):
        # 原来：到点（500ms）的计时器先跑，问「拿起后动了没有」，到点前手指已经动出 12px 的那一下 touchmove 还排在浏览器里，问到的
        # 是没动，菜单出来；下一帧这一下发进来，记成在拖，又把菜单收起（[1, 1]：真机上就是弹出、挂 0.2 秒、收起）。现在到点只排到
        # 下一帧开头，这一下先发进来、当场作废长按，菜单不出
        self.run_page(r"""
let f = finger();
await g.sleep(300);                                             // 250ms 拿起
f.to(106); f.frame();                                           // 约 0.39 秒：第一步 6px，还在 10px 里（不算在拖）
assert.deepEqual(f.log, ["start", "move"]);
assert.equal(isDragMoving(), false);
f.to(112);                                                      // 到点前手指已经动出 12px，这一下还排着
await g.sleep(230);                                             // 500ms 的计时器先跑
f.frame();                                                      // 下一帧开头：这一下发进来
assert.equal(isDragMoving(), true);
assert.deepEqual([opened, closed], [0, 0]);                     // 菜单不出、也就不用收
f.to(160); f.frame();
assert.equal(f.up().prevented, false);                          // 照常放下（不是点按、不是长按松手）
assert.deepEqual(f.log, ["start", "move", "move", "move", "end"]);
assert.deepEqual([opened, closed], [0, 0]);
// 不能拖的行（搜索中、没存到服务端的）：到点前已经在滚，同样不出菜单（原来出了就一直开着，滚着列表菜单还在）
f = finger({ drag: false });
await g.sleep(300);
f.to(130);
await g.sleep(230);
f.frame();
assert.deepEqual([opened, closed], [0, 0]);
f.up();
""")

    def test_holding_still_still_opens_the_menu_and_moving_afterwards_still_closes_it(self):
        # 不变的两条：按住不动到 500ms 菜单照旧出来（到点后的下一帧开头）；出菜单后手指再动（真的拖起来了）收起菜单，接着拖。
        # 「动了」由共用拖动在收到那一下时当场告诉长按（lib/layers.ts 的 watchDragMoved），不靠 React 那一份 onTouchMove——
        # 按下的节点被挪走以后 React 收不到，原来菜单就一直开着
        self.run_page(r"""
for (const react of [true, false]) {
  opened = closed = 0;
  const f = finger({ react });
  await g.sleep(530);
  f.to(104); f.frame();                                         // 到点后的下一帧：手指只抖了 4px，照旧出菜单
  assert.deepEqual([opened, closed], [1, 0], String(react));
  f.to(130); f.frame();                                         // 真的拖起来了：收起菜单（只收一次）
  f.to(150); f.frame();
  assert.deepEqual([opened, closed], [1, 1], String(react));
  assert.equal(isDragMoving(), true);
  f.up();
  assert.deepEqual(f.log, ["start", "move", "move", "move", "end"], String(react));
}
// 到点了、下一帧还没来就松手（或系统收回触摸）：同原来到点就出——菜单出来、松手不算点击、拖动原地结束（不是点按）
opened = closed = 0;
let f = finger();
await g.sleep(530);
const up = f.up();
assert.deepEqual([opened, closed], [1, 0]);
assert.equal(up.prevented, true);
assert.deepEqual(f.log, ["start", "end"]);
f.frame();
assert.equal(opened, 1);                                        // 那一帧不再出一次
// 松手以后别处的拖动（例如会话栏卡片）记下的移动不算到这一次头上：留着的菜单不收
setDragging(true);
markDragMoved();
setDragging(false);
assert.equal(closed, 0);
// 没到点就松手：点按，不出菜单
opened = closed = 0;
f = finger();
await g.sleep(300);
f.up();
await g.sleep(260);
f.frame();
assert.deepEqual([opened, closed], [0, 0]);
assert.deepEqual(f.log, ["start", "cancel"]);
""")


if __name__ == "__main__":
    unittest.main()
