"""「录吧」快捷回复（总表二 11-10「去掉开始按钮」：卡片不放开始，开始由用户一句话下令；输入框上方给一个「录吧」，等同打字）。

- 什么时候出现（lib/record-reply.ts，主会话 11-11 按 A 线实际交付定）：对话事件 pearnly.write_plan 里有一份在等用户点头
  （awaiting）、CC 没在跑；started / replaced / 没有计划都不出现；归并层按计划记状态，只进不退（reducer 的 writePlans）。
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
    def test_it_shows_only_while_a_write_plan_awaits_the_user(self):
        # 主会话 11-11 按 A 线实际交付定：看对话事件 pearnly.write_plan（A 线 assistant/write_plan.py 的 event：
        # {id: pplan_<计划>_<状态>, type, plan, status: awaiting|started|replaced, count, processed_at}，推送与补拉都带）。
        # 有一份 awaiting、CC 没在跑就出；started / replaced / 没有都不出；改票后 CC 重新算出新的 awaiting 又出。
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { project } from "@/lib/managed-agents/reducer";
import { offersRecord } from "@/lib/record-reply";

let t = 0;
const at = () => new Date(Date.UTC(2026, 10, 11, 4, 0, t++)).toISOString();
const plan = (name: string, status: string, count = 3) => ({ id: `pplan_${name}_${status}`, type: "pearnly.write_plan", plan: name, status, count, processed_at: at() }) as any;
const shown = (events: any[], running = false) => offersRecord(project(events).writePlans, running);

// 没有计划
assert.equal(shown([]), false);
// 复述完在等用户点头：出现；CC 还在说（在跑）：不出现
const a1 = plan("WP-1", "awaiting");
assert.equal(shown([a1]), true);
assert.equal(shown([a1], true), false);
// 用户点了头、CC 开始了：不出现
const s1 = plan("WP-1", "started");
assert.equal(shown([a1, s1]), false);
// 改了票：旧的被替掉、新算的一份在等 → 又出现；新的这份开始了 → 不出现
const r1 = plan("WP-1", "replaced"), a2 = plan("WP-2", "awaiting"), s2 = plan("WP-2", "started");
assert.equal(shown([a1, r1, a2]), true);
assert.equal(shown([a1, r1, a2, s2]), false);
// 开始之后用户又改（下一份）：新的 awaiting 又出现
assert.equal(shown([a1, s1, plan("WP-3", "awaiting")]), true);
// 只有替掉的：不出现
assert.equal(shown([a1, r1]), false);
// 推送与补拉乱序：开始先到、等点头的旧事件晚到，不回到出现；同一条重复到只算一次
assert.equal(shown([s1, a1]), false);
assert.equal(shown([r1, a1, a1]), false);
assert.equal(shown([a1, a1]), true);
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
        self.assertIn("const shown = useRecordOffered();", reply)
        # 只订阅这一个值（会话控制器的快照：写入计划状态 + 在不在忙），回复每来一段不重画
        provider = read("lib/managed-agents/runtime-provider.tsx")
        hook = block(provider, "export function useRecordOffered(): boolean {")
        self.assertIn("offersRecord(read().writePlans, isBusy(read()))", hook)
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
