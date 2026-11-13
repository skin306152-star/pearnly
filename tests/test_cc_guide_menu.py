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
const { useBusiness, loadOffered, chooseBusiness, noteBusiness, useCurrentBusiness } = await import("@/lib/business");
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
        self.assertIn('data-slot="cc-quick-replies"', body)
        self.assertIn("if (record || running || !list.length) return null;", body)
        self.assertIn("offered?.[mode]", body)
        # 选中的实底带 ×，点了取消；没选的是描边按钮
        self.assertIn('variant="secondary"', body)
        self.assertIn("<XIcon />", body)
        self.assertIn('chooseBusiness(sessionId, "")', body)
        self.assertIn('variant="outline"', body)
        # 占位字按业务换
        self.assertIn("guide.placeholder[business]", thread)

    def test_words_in_four_languages(self):
        i18n = read("i18n.ts")
        for words in ("上传采购票据", "上传销售票据", "输入客户名称或编号", "Upload purchase documents", "อัปโหลดเอกสารซื้อ",
                      "仕入書類をアップロード", "รับชำระ", "入金"):
            self.assertIn(words, i18n)

    def test_choice_is_kept_before_the_session_and_carried_after(self):
        run_ts(STORE, stubs=STUBS, lang="zh")
        business = read("lib/business.ts")
        self.assertIn("export function useBusinessCarry", business)
        self.assertIn("noteBusiness(s.id, (s as ListedSession).business)", read("lib/managed-agents/session-list-adapter.ts"))


if __name__ == "__main__":
    unittest.main()
