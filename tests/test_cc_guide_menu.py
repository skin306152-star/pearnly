"""CC 输入框上方的业务菜单（引导式录入第 1 期，评估 6.1；``thread.tsx`` 的 BusinessMenu 与 ``lib/business.ts``）。

断言的是应有状态：
- 只列这个产品 × 这个模式真能做的业务（服务端 ``businesses``，产品声明；Cowork 与代录没有收款）；与「写入」同一行同一种按钮，
  同一刻只出一种（有写入计划在等时出「写入」，在跑时都不出）；选中的一项实底带「×」，点了取消；不是必经之路；
- 选了业务：占位字换成按业务的一句（采购「上传采购票据」、销售「上传销售票据」、收款「输入客户名称或编号」），四语；
- 选的记在会话上：有会话直接交服务端（没收下撤回），新建页先记本页、会话建好补交；侧栏列表读到的会话业务记下。

    python3 tests/test_cc_guide_menu.py
"""
import sys
import unittest
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT + "/tests")

from test_assistant_ui import run_ts  # noqa: E402
from test_cc_ask import STUBS  # noqa: E402

SRC = Path(ROOT) / "frontend/assistant/src"

STORE = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
const g = globalThis as any;
const posted: any[] = [];
g.fetch = async (url: string, init: any = {}) => {
  if (String(url).includes("businesses")) return new Response(JSON.stringify({ message: { chat: ["purchase", "sales", "collection", "payment"], operator: ["purchase"] } }));
  posted.push({ url: String(url), body: JSON.parse(init.body || "{}") });
  return new Response(JSON.stringify({ message: {} }));
};
const { useBusiness, loadOffered, chooseBusiness, noteBusiness, useCurrentBusiness, promptOf } = await import("@/lib/business");
await loadOffered();
assert.deepEqual(useBusiness.getState().offered?.operator, ["purchase"]);
// 新建页（没有会话）：记在本页，不发请求
chooseBusiness(null, "purchase");
assert.equal(useBusiness.getState().draft, "purchase");
assert.equal(posted.length, 0);
// 有会话：按本页先显示、交给服务端
chooseBusiness("S1", "sales");
assert.equal(useBusiness.getState().bySession["S1"], "sales");
await new Promise((r) => setTimeout(r, 0));
assert.equal(posted.length, 1);
assert.ok(posted[0].url.endsWith("pearnly_core.assistant.api.business"));
assert.deepEqual(posted[0].body, { session_id: "S1", business: "sales" });
// 侧栏读到的会话业务记下；"" = 没选
noteBusiness("S2", "collection");
noteBusiness("S3", undefined);
assert.equal(useBusiness.getState().bySession["S2"], "collection");
assert.equal("S3" in useBusiness.getState().bySession, false);
// 侧栏带来的选业务时刻（RFC3339）记成毫秒；会话随第一句建好后补交的按会话开头算（from_start，0）
noteBusiness("S4", "purchase", "2026-11-14T08:00:00Z");
assert.equal(useBusiness.getState().atBySession["S4"], Date.parse("2026-11-14T08:00:00Z"));
chooseBusiness("S5", "sales", true);
await new Promise((r) => setTimeout(r, 0));
assert.deepEqual(posted.at(-1).body, { session_id: "S5", business: "sales", from_start: 1 });
assert.equal(useBusiness.getState().atBySession["S5"], 0);
// 那一句该不该出（promptOf）：没选不出；选了、之后没交文件出；之后交过带文件的一句不出；选之前交的不算
const at = 1000;
const user = (t: number, files = 0) => ({ role: "user", createdAt: new Date(t), attachments: Array(files).fill({}) });
assert.equal(promptOf("", at, [], 0, null), "");
assert.equal(promptOf("purchase", at, [], 0, null), "purchase");
assert.equal(promptOf("purchase", at, [user(500, 2)], 0, null), "purchase");
assert.equal(promptOf("purchase", at, [user(1500)], 0, null), "purchase");
assert.equal(promptOf("purchase", at, [user(1500, 1)], 0, null), "");
assert.equal(promptOf("sales", 0, [user(10, 1)], 0, null), "");
// 输入框挂着附件、正在发带文件的一句：不重复要
assert.equal(promptOf("sales", at, [], 1, null), "");
assert.equal(promptOf("sales", at, [], 0, { text: "", files: ["a.pdf"] }), "");
assert.equal(promptOf("sales", at, [], 0, { text: "hi", files: [] }), "sales");
// 收款不要文件：选了之后用户说过话（或正在发）就不出
assert.equal(promptOf("collection", at, [user(500)], 0, null), "collection");
assert.equal(promptOf("collection", at, [user(1500)], 0, null), "");
assert.equal(promptOf("collection", at, [], 0, { text: "x", files: [] }), "");
assert.equal(promptOf("collection", at, [{ role: "assistant", createdAt: new Date(2000) }], 0, null), "collection");
g.done();
"""


def read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


class GuideMenuTests(unittest.TestCase):
    def test_menu_lists_what_the_product_offers_and_sits_with_record_reply(self):
        thread = read("components/assistant-ui/thread.tsx")
        start = thread.index("const BusinessMenu: FC")
        body = thread[start:thread.index("\n};", start)]
        # 同一行、同一种按钮；在跑时、有写入计划在等时不出；只列产品声明的
        self.assertIn("<RecordQuickReply />\n            <BusinessMenu />", thread)
        self.assertIn('data-slot="cc-business-menu"', body)
        self.assertNotIn('cc-quick-replies', body)  # 业务菜单不是快捷回复：流程按 cc-quick-replies 找「写入」那颗
        self.assertIn("if (record || running || !list.length) return null;", body)
        self.assertIn("const list = menuOf(offered, mode);", body)
        # 菜单按这段对话公司的账本种类过滤（A1 11-19 businesses(session_id)）：带对话号取，换公司 / 账套（key）重取；
        # 取不到（服务端还不认这个参数）就用全产品那份；新建页没有对话号不过滤
        self.assertIn("const offered = useOffered(sessionId);", body)
        biz = read("lib/business.ts")
        self.assertIn('get<Offered>("businesses", { session_id: sessionId })', biz)
        self.assertIn(".catch(() => undefined);", biz)
        self.assertIn("if (sessionId) loadOfferedFor(sessionId, key);", biz)
        self.assertIn("const key = `${project}|${ledger}`;", biz)
        self.assertIn("}, [sessionId, key]);", biz)
        self.assertIn("return own?.offered ?? all;", biz)  # 服务端清单按声明的 order 排（A2 线 F1，lib/business menuOf）
        # 用户 11-17 定稿：没选的是描边小按钮（图标 + 业务名）；选中只留这一项、实底（主按钮那档）带 ×，点 × 取消
        # （按表单走的业务 = 本对话开着的引导，× 取消这次引导）
        self.assertIn('variant="outline" data-business={business}', body)  # 用户 11-19：32 高 14 号字（默认档 .cc-btn），不用 24 高的列表小按钮
        self.assertIn('<Button aria-pressed onClick={clear}>', body)
        self.assertNotIn('size="sm"', body)
        # 「更多」的下拉向上开、与按钮右对齐，不遮输入框（用户 11-19）
        self.assertIn('<DropdownMenuContent side="top" align="end" data-slot="cc-business-more-menu">', body)
        self.assertNotIn('variant="secondary"', body)
        self.assertIn("<XIcon />", body)
        self.assertIn('chooseBusiness(sessionId, "")', body)
        self.assertIn("const chosen = open?.business ?? (current && list.includes(current) ? current : \"\");", body)
        # 前 4 项摆出、其余进「更多」（标准下拉菜单）；一行放不下少摆（量宽度）、不换行
        self.assertIn("const MENU_FIRST = 4;", thread)
        self.assertIn("const fit = useMenuFit(row, measure, list, MENU_FIRST);", body)
        self.assertIn('<DropdownMenuTrigger asChild>{more}</DropdownMenuTrigger>', body)
        self.assertIn('{t("more")}', body)
        self.assertIn('aria-hidden inert className="cc-business-measure"', body)
        # 量宽度那一排自身零宽并裁掉溢出：绝对定位的看不见元素也算进对话区的可滚动范围，整排摆开会让手机上整页能左滑走
        # （11-28 iPhone）；按钮保持自然宽度，量出的数不变
        css = read("globals.css")
        ruler = css[css.index(".cc-business-measure {"):css.index("}", css.index(".cc-business-measure {"))]
        for rule in ("position: absolute", "width: 0", "height: 0", "overflow: hidden", "visibility: hidden"):
            self.assertIn(rule, ruler)
        self.assertIn(".cc-business-measure > * { flex: none; }", css)
        self.assertIn("for (const el of ruler.children) watch.observe(el);", thread)  # 0×0 的那一排本身观察不到宽度变化
        # 描边小按钮有边框（以前 outline 没落到样式上，看着像纯文字）
        self.assertIn('"data-outline": variant === "outline"', read("components/ui/button.tsx"))
        self.assertIn(".cc-btn-sm[data-outline] { border: var(--cc-hairline) solid var(--cc-line); background: var(--cc-bg-surface); }",
                      read("globals.css"))
        # 占位字按业务换
        self.assertIn("(guide.placeholder as Record<string, string>)[business]", thread)

    def test_prompt_is_drawn_by_the_page_in_the_conversation(self):
        """选了业务 CC 在对话里说一句要什么（用户 11-14：不是只换输入框的提示字）：在消息组里、待发气泡之后；同回复的正文样式；
        不带 data-role（不算模型回复）；词条四语；去重交给 promptOf（上面的纯函数断言）。"""
        thread = read("components/assistant-ui/thread.tsx")
        self.assertIn("<PendingSendBubble />\n            <BusinessPrompt />", thread)
        start = thread.index("const BusinessPrompt: FC")
        body = thread[start:thread.index("\n};", start)]
        self.assertIn('data-slot="cc-business-prompt"', body)
        self.assertIn("cc-reply", body)
        self.assertIn("const shown = useBusinessPromptShown();", body)
        self.assertIn("return promptOf(business, at, messages, composerFiles, pending) as Business | \"\";", thread)
        # 出了这一句就不是空对话（验收 M06 11-19：欢迎页不收、那句挤在形象旁边）：对话页用同一个判断
        self.assertIn('const prompted = useBusinessPromptShown() !== "";', thread)
        self.assertIn("const isEmpty = useAuiState(isNewChatView) && !pending && !guides && !prompted;", thread)
        self.assertIn("(guide.prompt as Record<string, string>)[shown]", body)
        self.assertNotIn("data-role", body)

    def test_words_in_four_languages(self):
        i18n = read("i18n.ts")
        for words in ("请上传采购票据。", "请上传销售票据。", "请提供客户和收款金额。", "Please upload the purchase documents.",
                      "Please provide the customer and the amount received.", "กรุณาอัปโหลดเอกสารซื้อ", "กรุณาระบุลูกค้าและยอดรับชำระ",
                      "売上書類をアップロードしてください。", "得意先と入金額をお知らせください。","上传采购票据", "上传销售票据", "输入客户名称或编号", "Upload purchase documents", "อัปโหลดเอกสารซื้อ",
                      "仕入書類をアップロード", "รับชำระ", "入金"):
            self.assertIn(words, i18n)

    def test_choice_is_kept_before_the_session_and_carried_after(self):
        run_ts(STORE, stubs=STUBS, lang="zh")
        business = read("lib/business.ts")
        self.assertIn("export function useBusinessCarry", business)
        self.assertIn("noteBusiness(s.id, (s as ListedSession).business, (s as ListedSession).business_at)",
                      read("lib/managed-agents/session-list-adapter.ts"))


if __name__ == "__main__":
    unittest.main()
