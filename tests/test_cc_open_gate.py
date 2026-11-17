"""链接 / 通知打开对话时的「正在打开」闸（主会话 11-17，流程窗口查出的竞态，流程 cc.deep-link-attach）。

以前：打开 /assistant?c=<会话>（链接、LINE、待处理、通知点开都走 lib/nav.ts 的 useDeepLinks）时，页面先给组件库的「新对话」，要等
几次请求回来才切过去；这段时间加附件走 ensureSession 当场建出一段新会话，附件和这句进了那段，切过去后从眼前消失。
现在：打开在途时输入框、「+」不可用，附件适配器不收（拖放、粘贴也不建会话）；一次只认最新的一次打开；超时放开并落到「找不到」，
同时作废这一次，晚到的响应不再切走页面。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, NODE, read, run_ts  # noqa: E402


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class OpenGateTests(unittest.TestCase):
    def test_a_link_in_the_address_closes_the_gate_before_the_first_render(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { linkedAt } from "@/lib/opening";
for (const href of ["https://x.test/assistant?c=sesn_01Ab", "https://x.test/assistant?view=pending&company=CO-1",
                    "https://x.test/assistant?task=OPT-0001", "https://x.test/assistant?company=CO-1"]) {
  assert.equal(linkedAt(href), true, href);
}
for (const href of ["https://x.test/assistant", "https://x.test/assistant?c=", "https://x.test/app/home?c=sesn_01Ab", "bad"]) {
  assert.equal(linkedAt(href), false, href);
}
(globalThis as any).done();
""")

    def test_only_the_latest_open_counts_and_a_late_reply_after_the_timeout_is_dropped(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { gatedOpen, opening, useOpening } from "@/lib/opening";
import { createAttachmentAdapter } from "@/lib/managed-agents/attachments";

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const switched: string[] = [];
let timedOut = 0;

// 1) 打开在途：闸关着，附件适配器不收（不建会话、不出卡片）；打开完放开
let created = 0;
const adapter = createAttachmentAdapter(async () => { created += 1; return "s-new"; }, undefined, opening);
let finish!: () => void;
const first = gatedOpen(async (live) => { await new Promise<void>((r) => { finish = r; }); if (live()) switched.push("A"); },
                        () => { timedOut += 1; }, () => true, 1000);
assert.equal(opening(), true);
const yielded: any[] = [];
for await (const step of adapter.add!({ file: new File(["x"], "slip.jpg", { type: "image/jpeg" }) } as any)) yielded.push(step);
assert.deepEqual(yielded, []);
assert.equal(created, 0);
finish();
await first;
assert.deepEqual(switched, ["A"]);
assert.equal(useOpening.getState().token, 0);

// 2) 超时：放开、落到「找不到」，晚到的响应不再切走页面
let late!: () => void;
const slow = gatedOpen(async (live) => { await new Promise<void>((r) => { late = r; }); if (live()) switched.push("B"); },
                       () => { timedOut += 1; }, () => true, 20);
await wait(40);
assert.equal(timedOut, 1);
assert.equal(opening(), false);   // 输入框可用了
late();
await slow;
assert.deepEqual(switched, ["A"]); // B 没切过去

// 3) 新的一次打开作废还在途的旧一次：旧的回来不切，新的照切
let oldDone!: () => void;
const older = gatedOpen(async (live) => { await new Promise<void>((r) => { oldDone = r; }); if (live()) switched.push("old"); },
                        () => { timedOut += 1; }, () => true, 1000);
const newer = gatedOpen(async (live) => { await wait(5); if (live()) switched.push("new"); }, () => { timedOut += 1; }, () => true, 1000);
await newer;
oldDone();
await older;
assert.deepEqual(switched, ["A", "new"]);
assert.equal(opening(), false);
assert.equal(timedOut, 1);
(globalThis as any).done();
""")

    def test_the_gate_reaches_every_link_entry_the_composer_and_the_attach_button(self):
        nav = read("lib/nav.ts")
        for entry in ("      run(initial);\n", "      if (target) run(target);\n",
                      '      run(link ?? (id && !id.startsWith("/") ? { c: id, view: "" } : { c: "", view: "tasks" }));'):
            self.assertIn(entry, nav)   # 地址参数、后台脚本点通知（pearnly-open）、桌面端 / 页面通知（tomo-open）
        self.assertNotIn("void open(", nav)
        self.assertNotIn("if (!alive) return;\n        const now", nav)
        self.assertIn("    if (!live()) return false;\n    await aui.threads().switchToThread(id);", nav)
        provider = read("lib/managed-agents/runtime-provider.tsx")
        self.assertIn("isDisabled: snapshot.isDead || held,", provider)
        self.assertIn("const disabled = useAuiState((s) => !s.composer.isEditing || s.thread.isDisabled);",
                      read("components/assistant-ui/attachment.tsx"))


if __name__ == "__main__":
    unittest.main()
