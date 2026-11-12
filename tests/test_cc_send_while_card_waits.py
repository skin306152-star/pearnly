"""卡片挂着等用户时，输入框里打的话当场发，不排队（11-11 压测 S8a、主会话 11-12 审定 A / B）。

- 提问卡 ask_user、「允许 / 拒绝」卡挂着时这一轮在等用户：以前算「在忙」，输入框的发送进排队、等这一轮结束才发，而这一轮
  正等用户答卡，永远发不出。现在只有模型在工作（在回复、本站在执行、结果在交回）或新对话在建会话时才排队。
- 发出后服务端先把卡交回（提问卡写明回答在这条消息里，要允许的按拒绝、不执行，tests/test_assistant.py 的 AskUserTests）。
- 停止键、状态行「需要您的输入」不变（仍按 reducer.isBusy）。

做法同 tests/test_assistant_ui.py：源码断言 + 用 esbuild 打包交给 node 跑的行为检查（要先 npm ci --prefix frontend/assistant）。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, NODE, SRC, run_ts  # noqa: E402

needs_node = unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")


@needs_node
class SendWhileCardWaitsTests(unittest.TestCase):
    def test_only_a_working_model_holds_the_next_message(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { holdsSends, isBusy } from "@/lib/managed-agents/reducer";

const state = (isRunning: boolean, approvals: string[] = [], custom: string[] = []) =>
  ({ isRunning, pendingApprovals: approvals, pendingCustomTools: custom });
// 空闲：不排
assert.equal(holdsSends(state(false)), false);
// 模型在回复：排着，本轮完了自动发
assert.equal(holdsSends(state(true)), true);
// 卡片挂着等用户（提问卡、允许卡同一个判定）：仍算这一轮没结束（停止键、状态行），但发送不排队
assert.equal(isBusy(state(false, ["sevt_ask"])), true);
assert.equal(holdsSends(state(false, ["sevt_ask"])), false);
assert.equal(holdsSends(state(false, ["sevt_a", "sevt_b"])), false);
// 一张卡答了正在交回、另一张还挂着；本站还欠着工具结果：模型这边有活在走，照旧排
assert.equal(holdsSends(state(true, ["sevt_b"])), true);
assert.equal(holdsSends(state(false, ["sevt_ask"], ["sevt_tool"])), true);
assert.equal(holdsSends(state(false, [], ["sevt_tool"])), true);
console.log("HARNESS OK");
""")

    def test_the_queue_sends_at_once_while_a_card_waits(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createSendQueue } from "@/lib/managed-agents/send-queue";
import { holdsSends } from "@/lib/managed-agents/reducer";

const sent: string[] = [];
const queue = createSendQueue(async (message: any) => { sent.push(message.content[0].text); });
const say = (text: string) => queue.adapter.enqueue({ role: "user", content: [{ type: "text", text }] } as any, { steer: false });
// 提问卡挂着
queue.setBusy(holdsSends({ isRunning: false, pendingApprovals: ["sevt_ask"], pendingCustomTools: [] }));
say("改写进别家");
assert.deepEqual(sent, ["改写进别家"]);
assert.equal(queue.adapter.items.length, 0);
// 模型在回复：排着
queue.setBusy(holdsSends({ isRunning: true, pendingApprovals: [], pendingCustomTools: [] }));
await new Promise((r) => setTimeout(r, 0));
say("下一句");
assert.deepEqual(sent, ["改写进别家"]);
assert.equal(queue.adapter.items.length, 1);
console.log("HARNESS OK");
""")

    def test_the_composer_queue_follows_it(self):
        provider = (SRC / "lib/managed-agents/runtime-provider.tsx").read_text(encoding="utf-8")
        self.assertIn("const holding = holdsSends(snapshot) || creating;", provider)
        self.assertIn("sendQueue.setBusy(holding);", provider)
        # 停止键、Esc 仍按整轮是否结束
        self.assertIn("isRunning: running,", provider)


if __name__ == "__main__":
    unittest.main()
