"""对话页（frontend/assistant，ADR-019）CC 闭环第一批的源码检查（2026-10-12）：
提醒卡片（通道 1）、侧栏圆点与浮顶、补拉锚点、推送通知（通道 2）、深链接、桌面端「打开对话」、定时任务页。

只读源码，不跑浏览器、不连站点；行为由前端构建（tsc）与测试环境实测负责，这里守住各路之间的约定不被改丢。
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend/assistant/src"


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
        self.assertIn("new WeakMap<NoticeItem, ThreadMessage>()", reducer)  # 同一条提醒同一个对象
        provider = read("lib/managed-agents/runtime-provider.tsx")
        self.assertIn("withNotices(snapshot.messages, snapshot.notices)", provider)
        self.assertIn("messages: messages as readonly ThreadMessageLike[]", provider)
        self.assertIn("noticesShown(sessionId, lastNotice)", provider)
        # 回复完成通知、文件面板仍只看引擎消息
        self.assertNotIn("withNotices", provider.split("export function useActiveSessionSnapshot", 1)[1])

    def test_unattached_notices_still_reach_the_page(self):
        realtime = read("lib/managed-agents/realtime.ts")
        self.assertIn('if (push.session_id || isNotice(push.event)) noteEvent(push.session_id || "", push.event);', realtime)
        self.assertIn("if (push.session_id) handlers.get(push.session_id)", realtime)
        self.assertIn("export function onSiteEvent(event: string, handler: (data: unknown) => void)", realtime)
        self.assertIn("export function onConnectionChange(listener: (connected: boolean) => void)", realtime)
        # 共用层不认识产品事件
        self.assertNotIn("cowork", realtime)

    def test_sidebar_dot_and_float_to_top(self):
        api = read("lib/managed-agents/api.ts")
        self.assertIn('post("notices_seen", { session_id: id })', api)
        activity = read("lib/managed-agents/activity.ts")
        self.assertIn('export type Activity = "running" | "unread" | "notice";', activity)
        for name in ("seedUnread", "noticesShown", "useBumps", "onNotice"):
            self.assertIn(f"export function {name}(", activity)
        self.assertIn("markNoticesSeen(sessionId)", activity)
        adapter = read("lib/managed-agents/session-list-adapter.ts")
        self.assertIn("if (s.unread) seedUnread(s.id);", adapter)
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
        self.assertIn('if (isDesktopApp()) return setState("unsupported");', push)
        self.assertIn('if (isIOS() && !standalone()) return setState("install");', push)
        # 打开要在点击里直接订阅：enablePush 里第一个异步动作就是 subscribe
        enable = push.split("export function enablePush()", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("await", enable)
        self.assertLess(enable.index("pushManager"), enable.index("push_subscribe"))

    def test_settings_row_and_sign_out_forget_the_device(self):
        settings = read("components/settings.tsx")
        self.assertIn("const push = usePush();", settings)
        self.assertIn("export function Switch(", settings)
        self.assertIn("<Notifications />", settings)
        self.assertIn("st.notificationsBlocked", settings)
        self.assertIn('t("addToHome")', settings)
        page = read("assistant.tsx")
        sign_out = page.split("async function signOut()", 1)[1].split("\n}", 1)[0]
        self.assertLess(sign_out.index("await forgetDevice()"), sign_out.index("pearnly_core.identity.sign_out"))
        self.assertIn("useEffect(() => void syncPush(), []);", page)
        # 「通知」一行要能打开「添加到主屏幕」的步骤：设置放在 InstallProvider 里
        workspace = page.split("function Workspace()", 1)[1]
        self.assertLess(workspace.index("<InstallProvider>"), workspace.index("<Settings />"))

    def test_page_notifications_for_notices(self):
        desktop = read("lib/desktop.ts")
        alert = desktop.split("onNotice((notice) => {", 1)[1].split("}),", 1)[0]
        self.assertIn("tag: `notice:${notice.id}`", alert)
        self.assertIn("notice.title || APP_NAME", alert)
        self.assertIn('openTarget(notice.session || "")', alert)
        # 浏览器里开了推送由后台脚本弹，不再另弹
        self.assertIn("if (!desktopApp && (pushOnRef.current || document.hasFocus())) return;", alert)


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
        self.assertIn('tr("cowork.folder.waiting", plus(rule.waiting, rule.waiting_partial))', page)
        self.assertIn('rule.include_existing ? "cowork.folder.enableConfirmAll" : "cowork.folder.enableConfirm"', page)
        self.assertIn('import { Switch } from "@/components/settings";', page)
        self.assertIn('tr("cowork.folder.field.remind")', page)
        self.assertIn("remind: remind ? 1 : 0", page)
        self.assertIn('hint={folderCount ? tr("cowork.folder.waiting", folderCount) : tr(folderHint)}', page)
        # 词条走 Cowork 术语表（folder_intake.api.terms 的 cowork.folder. 前缀），不进前端词条
        i18n = read("i18n.ts")
        self.assertNotIn("cowork.folder", i18n)


class TextTests(unittest.TestCase):
    def test_new_words_exist_in_every_page_language(self):
        i18n = read("i18n.ts")
        messages = block(i18n, "const MESSAGES = {")
        settings = block(i18n, "const SETTINGS = {")
        for lang in ("en", "zh", "th"):
            words = keys(block(messages, f"  {lang}: {{"))
            self.assertTrue({"notice", "newNotice"} <= words, lang)
            words = keys(block(settings, f"  {lang}: {{"))
            self.assertTrue({"notifications", "turnOnNotifications", "notificationsBlocked"} <= words, lang)
        self.assertNotIn("Express", messages.split("conversationMenu", 1)[1].split("}", 1)[0])


if __name__ == "__main__":
    unittest.main()
