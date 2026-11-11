"""「录吧」快捷回复（总表二 11-10「去掉开始按钮」：卡片不放开始，开始由用户一句话下令；输入框上方给一个「录吧」，等同打字）。

- 什么时候出现（lib/record-reply.ts）：对话最后一轮是 CC 说完的、里面调了写入计划（A 线 pearnly_write_plan）且计划有要写的
  （status = ready）；在跑、用户已回话、没有要写的、工具报错都不出现。提醒卡片（system）排在后面不算一轮。
- 点了等同打字（thread.tsx 的 RecordQuickReply）：组件库的 append 在本站运行时里进同一个发送排队（send-queue），
  空闲时当场交给 onNew 发出，字随界面语言；用标准组件库的 Button。

做法同 tests/test_assistant_ui.py：源码断言 + 用 esbuild 打包交给 node 跑的行为检查（要先 npm ci --prefix frontend/assistant）。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, NODE, block, read, run_ts  # noqa: E402

needs_node = unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")


@needs_node
class RecordReplyBehaviourTests(unittest.TestCase):
    def test_it_shows_only_while_a_ready_write_plan_waits_for_the_user(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { offersRecord, WRITE_PLAN_TOOL } from "@/lib/record-reply";

assert.equal(WRITE_PLAN_TOOL, "pearnly_write_plan");
const user = (text: string) => ({ role: "user", content: [{ type: "text", text }] });
const plan = (result: unknown, extra: Record<string, unknown> = {}) => ({ type: "tool-call", toolName: "pearnly_write_plan", toolCallId: "t1", args: {}, result, ...extra });
const said = (...parts: unknown[]) => ({ role: "assistant", content: [...parts, { type: "text", text: "บริษัท A · 9.A · 3 ใบ ได้ไหม" }] });
const ready = JSON.stringify({ status: "ready", company: "A", count: 3 });
const notice = { role: "system", content: [{ type: "text", text: "Run finished" }] };

// 复述完在等用户：出现（结果是一段 JSON 文字，或带 text 的对象，两种都认）
assert.equal(offersRecord([user("帮我录"), said(plan(ready))], false), true);
assert.equal(offersRecord([user("帮我录"), said(plan({ type: "text", text: ready }))], false), true);
assert.equal(offersRecord([user("帮我录"), said(plan({ status: "ready" }))], false), true);
// 回复后面又来了一张提醒卡片（system）：不算一轮，照样出现
assert.equal(offersRecord([user("帮我录"), said(plan(ready)), notice], false), true);
// 还在跑（复述还没说完）：不出现
assert.equal(offersRecord([user("帮我录"), said(plan(ready))], true), false);
// 用户已经回话（点了录吧、打了别的）：最后一条是用户的，不出现
assert.equal(offersRecord([user("帮我录"), said(plan(ready)), user("录吧")], false), false);
// 没有要写的、做不了：不出现（CC 用话说明，前端不出可点的开始）
assert.equal(offersRecord([user("帮我录"), said(plan(JSON.stringify({ status: "nothing_to_write", reason: "no ledger" })))], false), false);
// 工具报错、结果还没回来、纯文字失败码：不出现
assert.equal(offersRecord([user("帮我录"), said(plan(ready, { isError: true }))], false), false);
assert.equal(offersRecord([user("帮我录"), said(plan(undefined))], false), false);
assert.equal(offersRecord([user("帮我录"), said(plan("not_allowed"))], false), false);
// 同一轮里改过再算（用户说了修改）：按最后一次
assert.equal(offersRecord([user("第 3 张不要"), said(plan(ready), plan(JSON.stringify({ status: "nothing_to_write" })))], false), false);
assert.equal(offersRecord([user("第 3 张不要"), said(plan(JSON.stringify({ status: "nothing_to_write" })), plan(ready))], false), true);
// 别的工具、上一轮的计划：都不算
assert.equal(offersRecord([user("看看"), said({ type: "tool-call", toolName: "operator_start", result: ready })], false), false);
assert.equal(offersRecord([user("帮我录"), said(plan(ready)), user("先别录"), said()], false), false);
// 用户改了票（下一轮）、CC 重新调计划：又是 ready 就重新出现；这回没有要写的、或报错就不出现
const changed = [user("帮我录"), said(plan(ready)), user("第 3 张不要")];
assert.equal(offersRecord(changed, false), false);
assert.equal(offersRecord([...changed, said(plan(ready))], false), true);
assert.equal(offersRecord([...changed, said(plan(JSON.stringify({ status: "nothing_to_write" })))], false), false);
assert.equal(offersRecord([...changed, said(plan(ready, { isError: true }))], false), false);
// 空对话
assert.equal(offersRecord([], false), false);
console.log("HARNESS OK");
""", lang="zh")

    def test_tapping_it_sends_the_words_like_typing(self):
        # 组件库的 append（RecordQuickReply 调的就是它）在有排队的运行时里进发送排队，同输入框发送：空闲时当场交给 onNew；
        # 在忙时排着（录吧不是停止词，不当停止）
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { ExternalStoreThreadRuntimeCore } from "@assistant-ui/core/internal";
import { createSendQueue } from "@/lib/managed-agents/send-queue";
import { t } from "@/i18n";

const g = globalThis as any;
const sent: string[] = [];
let stops = 0;
const q = createSendQueue(async (m: any) => { sent.push(m.content[0].text); }, () => { stops++; });
const core = new ExternalStoreThreadRuntimeCore(
  { getModelContext: () => ({}), subscribe: () => () => {} } as any,
  { messages: [], isRunning: false, convertMessage: (m: any) => m, onNew: async () => { throw new Error("goes through the queue"); }, queue: q.adapter } as any,
);
const text = t("recordNow");
assert.equal(text, "录吧");
await core.append({ role: "user", content: [{ type: "text", text }], parentId: null, sourceId: null, runConfig: {}, attachments: [] } as any);
await g.sleep(5);
assert.deepEqual(sent, ["录吧"]);
q.setBusy(true);
await core.append({ role: "user", content: [{ type: "text", text }], parentId: null, sourceId: null, runConfig: {}, attachments: [] } as any);
assert.equal(stops, 0);
assert.deepEqual(q.adapter.items.map((i: any) => i.prompt), ["录吧"]);
process.stdout.write("HARNESS OK\n", () => process.exit(0));
""", lang="zh")


class RecordReplySourceTests(unittest.TestCase):
    def test_it_sits_above_the_composer_as_a_kit_button(self):
        thread = read("components/assistant-ui/thread.tsx")
        self.assertIn("<ApprovalSheet />\n            <RecordQuickReply />\n            <Composer />", thread)
        reply = block(thread, "const RecordQuickReply: FC = () => {")
        self.assertIn('import { Button } from "@/components/ui/button";', thread)
        self.assertIn("offersRecord(s.thread.messages, s.thread.isRunning)", reply)
        self.assertIn('const text = t("recordNow");', reply)
        self.assertIn('thread.append({ role: "user", content: [{ type: "text", text }] })', reply)
        self.assertIn("<Button", reply)
        self.assertNotIn("<button", reply)

    def test_the_words_exist_in_four_languages(self):
        i18n = read("i18n.ts")
        for words in ('recordNow: "Record it"', 'recordNow: "录吧"', 'recordNow: "บันทึกเลย"', 'recordNow: "記帳して"'):
            self.assertIn(words, i18n)


if __name__ == "__main__":
    unittest.main()
