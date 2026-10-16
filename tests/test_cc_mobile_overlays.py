"""CC 对话页（frontend/assistant）手机上的弹层与返回键（2026-10-15 三星 A14、安卓 13、装成应用、Chrome 106 实测 F11）。

· F11a 弹窗不超出屏幕：dvh / svh 一律走 globals.css 的 --cc-vh / --cc-svh / --cc-viewport-h（旧浏览器退回 vh）；
  闸门 scripts/check-frontend-viewport-units.py 拦裸用；弹窗按可见区域（键盘弹出时变矮）居中限高，头尾固定、主体滚动；
· F11b 返回键先关最上面一层（lib/back-stack.ts）：打开、返回键、别的方式关掉后不多退一步、嵌套、关不掉时补回、刷新后、
  电脑与桌面版不碰浏览器历史；
· F11c 长按侧栏行不选字、不弹系统长按菜单；输入框照常选字；
· F11d 菜单离屏幕边 8px，窄屏子菜单在原菜单里换内容（带返回）；
· F11f 抽屉里打字时底部「新建会话」那一条收起。
行为检查要 node 与前端依赖（npm ci --prefix frontend/assistant），没有就跳过。
"""
import importlib.util
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, FRONTEND, NODE, run_ts  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend/assistant/src"
GATE = ROOT / "scripts/check-frontend-viewport-units.py"
NEEDS_NODE = unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")


def read(path):
    return (SRC / path).read_text(encoding="utf-8")


def _gate():
    spec = importlib.util.spec_from_file_location("check_frontend_viewport_units", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _gate()


# ── F11a：视口单位与弹窗 ──────────────────────────────────────────────────────────────────────────

class ViewportUnitGateTests(unittest.TestCase):
    def scan(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for name, text in files.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text, encoding="utf-8")
            return [(rel, snippet) for rel, _, snippet in gate.scan(str(root))]

    def test_bare_new_viewport_units_are_blocked(self):
        bad = {
            '<i className="fixed max-h-[88dvh]" />': "max-h-[88dvh]",
            '<i className="flex h-dvh w-full" />': "h-dvh",
            '<i className="min-h-svh" />': "min-h-svh",
            '<i className="md:h-lvh" />': "md:h-lvh",
            '<i className="h-[min(680px,92dvh)]" />': "h-[min(680px,92dvh)]",
            '<i style={{ maxHeight: "80dvh" }} />': "80dvh",
            "const w = `calc(100dvw - 2rem)`;": "calc(100dvw",
        }
        for source, snippet in bad.items():
            self.assertIn(("x.tsx", snippet), self.scan({"x.tsx": source}), source)
        css = {
            ".a { max-height: 88dvh; }": "max-height: 88dvh",
            ".a { height: var(--x, 100dvh); }": "height: var(--x, 100dvh)",
            # 前一条带 dvh 不算兜底；值里有 var( 时前一条接不住
            ".a { height: 90svh; height: 100dvh; }": "height: 100dvh",
            ".a { height: 100vh; height: var(--x, 100dvh); }": "height: var(--x, 100dvh)",
            "@media (max-width: 767px) { .a { min-height: 100svh; } }": "min-height: 100svh",
        }
        for source, snippet in css.items():
            self.assertIn(("g.css", snippet), self.scan({"g.css": source}), source)

    def test_fallbacks_and_variables_pass(self):
        good = {
            "x.tsx": "\n".join([
                '<i className="h-screen supports-[height:100dvh]:h-dvh max-h-[calc(var(--cc-viewport-h)*0.8)]" />',
                '<i className="h-(--cc-vh) min-h-(--cc-svh) [--cc-modal-share:0.8] h-[88vh]" />',
                "// 注释里的 max-h-[88dvh]、h-dvh 不算",
                "/* 100svh */",
            ]),
            "g.css": "\n".join([
                ":root { --cc-vh: 100vh; --cc-svh: 100vh; --cc-viewport-h: var(--cc-vvh, var(--cc-vh)); }",
                "@supports (height: 100dvh) { :root { --cc-vh: 100dvh; } }",
                ".a { height: 100vh; height: 100dvh; }",
                ".b { height: var(--cc-viewport-h); }",
                "/* height: 100dvh */",
            ]),
        }
        self.assertEqual(self.scan(good), [])

    def test_the_assistant_frontend_passes_and_ci_runs_the_gate(self):
        self.assertEqual(gate.SKIP_DIRS, {"node_modules", "dist", "build", ".vite"})
        self.assertNotIn("IGNORE", dir(gate))
        result = subprocess.run([sys.executable, str(GATE)], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        workflow = (ROOT / ".github/workflows/development.yml").read_text(encoding="utf-8")
        self.assertIn("run: python3 scripts/check-frontend-viewport-units.py", workflow)
        self.assertIn("run: python3 tests/test_cc_mobile_overlays.py", workflow)

    def test_globals_define_the_viewport_tokens_with_a_vh_fallback(self):
        css = read("globals.css")
        self.assertIn("  --cc-vh: 100vh;\n", css)
        self.assertIn("  --cc-svh: 100vh;\n", css)
        self.assertIn("  --cc-viewport-h: var(--cc-vvh, var(--cc-vh));\n", css)
        self.assertIn("@supports (height: 100dvh) { :root { --cc-vh: 100dvh; } }", css)
        self.assertIn("@supports (height: 100svh) { :root { --cc-svh: 100svh; } }", css)
        # 手机外壳、整页外框、侧栏都只引用变量
        self.assertIn("height: var(--cc-viewport-h);", css.split(".cc-mshell {", 1)[1].split("}", 1)[0])
        self.assertIn('<div className="flex h-(--cc-vh) w-full bg-app">', read("assistant.tsx"))
        self.assertIn("h-(--cc-vh)", read("components/error-boundary.tsx"))
        sidebar = read("components/ui/sidebar.tsx")
        self.assertIn("min-h-(--cc-svh)", sidebar)
        self.assertIn("h-(--cc-svh)", sidebar)


class DialogFitTests(unittest.TestCase):
    def test_modals_are_centred_in_the_visible_area_and_capped_to_it(self):
        css = read("globals.css")
        modal = css.split("  .cc-modal {", 1)[1].split("}", 1)[0]
        self.assertIn("top: calc(var(--cc-vvtop, 0px) + var(--cc-viewport-h) / 2);", modal)
        self.assertIn("max-height: calc(var(--cc-viewport-h) * var(--cc-modal-share, 0.88));", modal)
        guide = css.split("  .cc-install-guide {", 1)[1].split("}", 1)[0]
        self.assertIn("max-height: calc(var(--cc-viewport-h) - 2rem); overflow-y: auto;", guide)
        self.assertNotIn("top: 50%", guide)
        # 每个居中弹窗都用 cc-modal，不再自己写 top-1/2（工具类会盖过 cc-modal 的位置）
        for path, needle in [
            ("components/scheduled-tasks.tsx", "cc-card cc-enter cc-modal fixed left-1/2 z-50 flex"),
            ("components/add-company.tsx", "cc-card cc-enter cc-dialog cc-modal fixed left-1/2 z-50 flex"),
            ("components/settings.tsx", "cc-modal fixed left-1/2 z-[71] flex h-[min(680px,calc(var(--cc-viewport-h)*0.92))]"),
            ("components/ui/dialog.tsx", "cc-modal fixed start-[50%] z-50 grid overflow-y-auto"),
        ]:
            source = read(path)
            self.assertIn(needle, source, path)
            self.assertNotRegex(source, r"\btop-(?:1/2|\[50%\])", path)
        self.assertIn("[--cc-modal-share:0.8]", read("components/add-company.tsx"))
        self.assertIn("[--cc-modal-share:0.92]", read("components/settings.tsx"))
        self.assertIn("max-h-[calc(var(--cc-viewport-h)*0.8)]", read("components/assistant-ui/attachment.tsx"))

    def test_dialog_header_and_footer_stay_while_the_body_scrolls(self):
        tasks = read("components/scheduled-tasks.tsx")
        modal = tasks.split("function Modal(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('<div className="flex shrink-0 items-center justify-between gap-3 px-5 pt-4 pb-2">', modal)
        self.assertIn('<div className="min-h-0 flex-1 overflow-y-auto px-5 py-2">{children}</div>', modal)
        self.assertIn('<div className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>', modal)
        self.assertLess(modal.index("DialogPrimitive.Close"), modal.index("overflow-y-auto"))  # 关闭在头上，不随主体滚走
        add = read("components/add-company.tsx")
        self.assertIn('<div className="flex shrink-0 items-center justify-between gap-3 px-5 pt-4 pb-2">', add)
        self.assertIn('<div className="shrink-0 px-5 pb-2">', add)  # 搜索框不随列表滚走
        self.assertIn('<div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3" data-slot="cc-candidates">', add)
        settings = read("components/settings.tsx")
        self.assertIn('<main className="min-h-0 min-w-0 flex-1 overflow-y-auto', settings)

    def test_viewport_tracking_moves_to_the_root_for_portaled_dialogs(self):
        viewport = read("lib/viewport.ts")
        self.assertIn('root.style.setProperty("--cc-vvh", `${viewport.height}px`);', viewport)
        self.assertIn('root.style.setProperty("--cc-vvtop", `${viewport.offsetTop}px`);', viewport)
        self.assertNotIn("scrollTo(", viewport)
        main = read("main.tsx")
        self.assertIn("trackViewport();", main)
        shell = read("components/mobile-shell.tsx")
        self.assertNotIn("visualViewport", shell)
        self.assertNotIn("--cc-vvh", shell)
        # 页面 meta：安卓键盘弹出时缩布局视口（站点页面与构建入口一致）
        meta = "interactive-widget=resizes-content"
        self.assertIn(meta, (FRONTEND / "index.html").read_text(encoding="utf-8"))
        self.assertIn(meta, (ROOT / "apps/pearnly_core/pearnly_core/www/assistant.html").read_text(encoding="utf-8"))

    @NEEDS_NODE
    def test_viewport_variables_follow_the_keyboard(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { trackViewport } from "@/lib/viewport";

const g = globalThis as any;
const handlers: Record<string, () => void> = {};
g.visualViewport = { height: 786, offsetTop: 0, addEventListener: (t: string, f: () => void) => (handlers[t] = f), removeEventListener: (t: string) => delete handlers[t] };
const props: Record<string, string> = {};
const root: any = { style: { setProperty: (k: string, v: string) => (props[k] = v) } };
const stop = trackViewport(root);
assert.deepEqual(props, { "--cc-vvh": "786px", "--cc-vvtop": "0px" });
// 键盘弹出：可见区域只剩 475
g.visualViewport.height = 475;
handlers.resize!();
assert.equal(props["--cc-vvh"], "475px");
g.visualViewport.offsetTop = 120;
handlers.scroll!();
assert.equal(props["--cc-vvtop"], "120px");
stop();
assert.deepEqual(Object.keys(handlers), []);
// 没有 visualViewport：不写，CSS 退回 --cc-vh
delete g.visualViewport;
const none: Record<string, string> = {};
trackViewport({ style: { setProperty: (k: string, v: string) => (none[k] = v) } } as any)();
assert.deepEqual(none, {});
g.done();
""")


# ── F11b：返回键 ───────────────────────────────────────────────────────────────────────────────

BACK_HEAD = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { BackStack, type BackEnv } from "@/lib/back-stack";

const g = globalThis as any;
// 假的浏览器历史：entries 是每条记的层数，go 要等 flush() 才到（真浏览器里 history.go 是异步的）
function world(entries: number[] = [0], index = entries.length - 1) {
  const w: any = { entries: [...entries], index, pending: [] as number[], open: 0, closes: 0, refuse: false, timers: [] as any[], now: 0 };
  const env: BackEnv = {
    depth: () => w.entries[w.index],
    push: (d) => { w.entries = w.entries.slice(0, w.index + 1); w.entries.push(d); w.index++; },
    go: (delta) => void w.pending.push(delta),
    domLayers: () => w.open,
    closeDomTop: () => void w.closes++,
    later: (run, ms) => void w.timers.push({ at: w.now + ms, run }),
  };
  w.stack = new BackStack(env);
  w.tick = (ms = 0) => {
    w.now += ms;
    for (;;) {
      const due = w.timers.filter((t: any) => t.at <= w.now).sort((a: any, b: any) => a.at - b.at)[0];
      if (!due) break;
      w.timers.splice(w.timers.indexOf(due), 1);
      due.run();
    }
  };
  w.flush = () => { while (w.pending.length) { w.index += w.pending.shift(); w.stack.onPopState(); w.tick(); } };
  w.back = () => { w.index--; w.stack.onPopState(); w.tick(); };   // 用户按返回键
  w.setOpen = (n: number) => { w.open = n; w.stack.sync(); };
  return w;
}
"""


@NEEDS_NODE
class BackKeyBehaviourTests(unittest.TestCase):
    def run_back(self, body):
        run_ts(BACK_HEAD + body + "\ng.done();\n", stubs={"@/lib/desktop": "export const isDesktopApp = () => false;"})

    def test_open_then_back_closes_the_dialog_and_leaves_nothing_behind(self):
        self.run_back(r"""
const w = world();
w.setOpen(1); w.tick();
assert.deepEqual([w.entries, w.index], [[0, 1], 1]);
w.back();
assert.equal(w.closes, 1);            // 关的是弹层，页面还在
w.setOpen(0); w.tick();               // 弹层关上了
assert.deepEqual([w.pending, w.index], [[], 0]);
assert.equal(w.closes, 1);
""")

    def test_closing_another_way_takes_its_entry_back(self):
        self.run_back(r"""
const w = world();
w.setOpen(1); w.tick();
w.setOpen(0); w.tick();               // 点 ×、点外面、Esc、选了菜单项
assert.deepEqual(w.pending, [-1]);
w.flush();
assert.equal(w.index, 0);
assert.equal(w.closes, 0);            // 自己退的那一步不当成返回键
// 下次按返回键就离开页面（index 0 再往前是别的页面），不会先空按一次
""")

    def test_nested_layers_close_top_first(self):
        self.run_back(r"""
const w = world();
let drawer = 0;
const off = w.stack.register(() => drawer++);   // 手机抽屉
w.tick();
w.setOpen(1); w.tick();                        // 抽屉里长按出的菜单
assert.deepEqual([w.entries, w.index], [[0, 1, 2], 2]);
w.back();
assert.deepEqual([w.closes, drawer], [1, 0]);  // 先关菜单
w.setOpen(0); w.tick();
assert.deepEqual([w.pending, w.index], [[], 1]);
w.back();
assert.deepEqual([w.closes, drawer], [1, 1]);  // 再关抽屉
off(); w.tick();
assert.deepEqual([w.pending, w.index], [[], 0]);
""")

    def test_menu_item_opening_a_dialog_keeps_one_entry(self):
        self.run_back(r"""
const w = world();
w.setOpen(1); w.tick();               // 菜单
w.setOpen(0); w.setOpen(1); w.tick(); // 选「设置」：菜单关、弹窗开在同一拍
assert.deepEqual([w.entries, w.index, w.pending], [[0, 1], 1, []]);
w.back();
assert.equal(w.closes, 1);            // 返回键关的是弹窗
""")

    def test_a_layer_that_refuses_to_close_gets_its_entry_back(self):
        self.run_back(r"""
const w = world();
w.setOpen(1); w.tick();
w.back();
assert.equal(w.closes, 1);
w.tick();
assert.deepEqual([w.entries.slice(0, w.index + 1), w.pending], [[0], []]);   // 还在关：不急着补
w.tick(400);                          // 没关上
assert.deepEqual([w.entries, w.index], [[0, 1], 1]);
w.back();
assert.equal(w.closes, 2);            // 再按还能关
""")

    def test_escape_in_a_submenu_closing_the_whole_menu_steps_back_once_more(self):
        self.run_back(r"""
const w = world();
w.setOpen(2); w.tick();               // 菜单 + 子菜单
assert.deepEqual([w.entries, w.index], [[0, 1, 2], 2]);
w.back();                             // 组件库在子菜单里按 Esc 关整个菜单
w.setOpen(0); w.tick();
assert.deepEqual(w.pending, [-1]);
w.flush();
assert.deepEqual([w.index, w.closes], [0, 1]);
""")

    def test_a_layer_opened_while_our_own_step_back_is_on_its_way(self):
        self.run_back(r"""
const w = world();
w.setOpen(1); w.tick();
w.setOpen(0); w.tick();               // 退一步在路上
w.setOpen(1); w.tick();               // 又开了一个：先不加，等退回来
assert.deepEqual([w.entries, w.index], [[0, 1], 1]);
w.flush();
assert.deepEqual([w.entries, w.index, w.closes], [[0, 1], 1, 0]);   // 退回来后照实补上，没把新开的关掉
""")

    def test_a_step_back_the_browser_never_answers_does_not_block_or_repeat(self):
        self.run_back(r"""
const w = world();
w.setOpen(1); w.tick();
w.setOpen(0); w.tick();
assert.deepEqual(w.pending, [-1]);
w.pending = [];                       // 浏览器没退、也没回 popstate
w.tick(1500);
assert.deepEqual(w.pending, []);      // 不反复退
w.setOpen(1); w.tick();               // 之后照常：开一层记一条
assert.deepEqual([w.entries, w.index], [[0, 1, 2], 2]);
w.back();
assert.equal(w.closes, 1);
""")

    def test_reload_with_a_layer_open_counts_from_the_current_entry(self):
        self.run_back(r"""
const w = world([0, 1, 2]);           // 开着两层时刷新了页面
w.tick();
assert.deepEqual(w.pending, []);      // 不往回退（那几条属于刷新前的页面，退过去会重新载入）
w.setOpen(1); w.tick();
assert.deepEqual([w.entries, w.index], [[0, 1, 2, 3], 3]);
w.setOpen(0); w.tick(); w.flush();
assert.equal(w.index, 2);
""")

    def test_only_touch_browsers_touch_the_history(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
const g = globalThis as any;
const listeners: string[] = [];
g.addEventListener = (type: string) => listeners.push(type);
g.removeEventListener = () => {};
g.MutationObserver = class { observe() {} disconnect() {} };
const { installBackKey } = await import("@/lib/back-stack");
const env: any = { depth: () => 0, push: () => assert.fail("push"), go: () => assert.fail("go"), domLayers: () => 1, closeDomTop() {}, later() {} };
// 电脑浏览器、CC 桌面版窗口：不装，不碰历史
installBackKey(env, false)();
assert.deepEqual(listeners, []);
// 手机：装上，听 popstate；只装一次
const stop = installBackKey({ ...env, push() {}, later() {} }, true);
assert.deepEqual(listeners, ["popstate"]);
installBackKey(env, true);
assert.deepEqual(listeners, ["popstate"]);
stop();
g.done();
""", stubs={"@/lib/desktop": "export const isDesktopApp = () => false;"})

    def test_the_browser_side_and_the_page_wiring(self):
        back = read("lib/back-stack.ts")
        # 组件库弹层：弹窗、警告框、下拉菜单（含子菜单）；退场动画里的 closed 不算。
        # 选择器在不引任何模块的 lib/layers.ts（侧栏键盘也用），返回键栈引它、并照旧从这里导出
        self.assertIn('\'[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"], '
                      '[data-radix-menu-content][data-state="open"]\'', read("lib/layers.ts"))
        self.assertIn('import { OPEN_LAYERS } from "@/lib/layers";', back)
        self.assertIn('export { OPEN_LAYERS } from "@/lib/layers";', back)
        self.assertIn("domLayers: () => document.querySelectorAll(OPEN_LAYERS).length,", back)
        # 关最上层 = 一次 Esc（组件库只关最上层）；记层数时保留 history.state 里别的键；同一地址
        self.assertIn('document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true, cancelable: true }));', back)
        self.assertIn('history.pushState({ ...(state() ?? {}), [KEY]: depth }, "")', back)
        self.assertIn("export function installBackKey(env: BackEnv = browserEnv(), enabled = touchOnly() && !isDesktopApp())", back)
        # 只看 body 的直接子节点（Portal）与 data-state，不看对话里的逐字更新
        self.assertIn("mounts.observe(document.body, { childList: true });", back)
        self.assertIn('states.observe(document.body, { subtree: true, attributes: true, attributeFilter: ["data-state"] });', back)
        self.assertIn("installBackKey();", read("main.tsx"))
        page = read("assistant.tsx")
        self.assertIn("useBackLayer(isMobile && openMobile, () => setOpenMobile(false));", page)
        self.assertIn('useBackLayer(isMobile && view === "chat" && results.open, () => results.setOpen(false));', page)
        # 本页视图导航不进浏览器历史；深链接的 replaceState 保留 history.state（不抹掉层数）
        nav = read("lib/nav.ts")
        self.assertNotIn("pushState", nav)
        self.assertIn("history.replaceState(history.state,", nav)
        for path in SRC.rglob("*.ts*"):
            if path.name != "back-stack.ts":
                self.assertNotIn("pushState", path.read_text(encoding="utf-8"), path)
        # 依赖的组件库行为：Esc 在 document 上（捕获阶段）听
        escape = FRONTEND / "node_modules/@radix-ui/react-use-escape-keydown/dist/index.mjs"
        self.assertIn('ownerDocument.addEventListener("keydown", handleKeyDown, { capture: true })', escape.read_text(encoding="utf-8"))


# ── F11c：长按 ────────────────────────────────────────────────────────────────────────────────

class LongPressTests(unittest.TestCase):
    def test_long_press_rows_do_not_select_text_but_inputs_still_do(self):
        css = read("globals.css")
        block = css.split('    [data-slot="aui_thread-list-item"], .cc-project-row, .cc-mdrawer, [data-slot="sidebar"] {', 1)[1].split("\n  }", 1)[0]
        self.assertIn("-webkit-user-select: none; user-select: none; -webkit-touch-callout: none;", block)
        # 整个手机抽屉与平板侧栏都不可选：只关行本身时长按会改选抽屉顶上的「CC」并弹 Google 触摸搜索（部署后重验 F11c）
        self.assertIn('.cc-mdrawer :is(input, textarea)', block)
        self.assertIn('[data-slot="sidebar"] :is(input, textarea)', block)
        self.assertIn('[data-slot="aui_thread-list-item"] input, .cc-project-row input,', block)
        # 对话正文、输入框不在这个规则里
        self.assertNotIn("aui-thread", block)
        hook = read("hooks/use-long-press.ts")
        self.assertIn("window.getSelection?.()?.removeAllRanges();", hook)
        self.assertIn("if (e.timeStamp - touchedAt.current < CONTEXT_MENU_WINDOW_MS) e.preventDefault();", hook)
        # 侧栏对话行把整套处理挂上（含 onContextMenu）；公司组头只在手机上
        self.assertIn("{...press}", read("components/assistant-ui/thread-list.tsx"))
        self.assertIn("{...(isMobile ? press : {})}", read("components/assistant-ui/projects.tsx"))

    @NEEDS_NODE
    def test_long_press_blocks_the_system_menu_only_for_fingers(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { useLongPress } from "@/hooks/use-long-press";

const g = globalThis as any;
let cleared = 0;
g.getSelection = () => ({ removeAllRanges: () => cleared++ });
let opened = 0;
let press: any;
renderToStaticMarkup(createElement(() => ((press = useLongPress(() => opened++)), null)));
const event = (timeStamp: number, extra: any = {}) => {
  const e: any = { timeStamp, prevented: false, preventDefault() { e.prevented = true; }, ...extra };
  return e;
};
press.onTouchStart(event(1000, { touches: [{ clientX: 10, clientY: 10 }] }));
// 安卓长按：系统的 contextmenu 在手指按着时到 —— 拦掉
const menu = event(1500);
press.onContextMenu(menu);
assert.equal(menu.prevented, true);
await g.sleep(560);
assert.deepEqual([opened, cleared], [1, 1]);   // 出菜单，清掉已经出现的选区
const up = event(1600);
press.onTouchEnd(up);
assert.equal(up.prevented, true);              // 松手不算一次点击
// 过了很久的鼠标右键（电脑）：不拦
const right = event(9000);
press.onContextMenu(right);
assert.equal(right.prevented, false);
// 手指一动是在滚：不出菜单
press.onTouchStart(event(10000, { touches: [{ clientX: 10, clientY: 10 }] }));
press.onTouchMove(event(10100, { touches: [{ clientX: 10, clientY: 40 }] }));
await g.sleep(560);
assert.equal(opened, 1);
g.done();
""")


# ── F11d：菜单不出屏 ──────────────────────────────────────────────────────────────────────────

# 另一个工作包正在改的文件：合并时把它的 import 换成 @/components/ui/menu，再从这里删掉（删掉前下面的断言会提醒）
PENDING_MENU_IMPORT: set = set()


class MenuEdgeTests(unittest.TestCase):
    def test_every_menu_comes_from_the_wrapper(self):
        for path in sorted(SRC.rglob("*.tsx")):
            rel = path.relative_to(SRC).as_posix()
            text = path.read_text(encoding="utf-8")
            if rel == "components/ui/menu.tsx":
                continue
            if rel in PENDING_MENU_IMPORT:
                self.assertIn('import { DropdownMenu } from "radix-ui";', text, f"{rel} 已改用包装，从 PENDING_MENU_IMPORT 删掉")
                continue
            self.assertNotRegex(text, r"import \{[^}]*\bDropdownMenu\b[^}]*\} from \"radix-ui\"", rel)
            # 子菜单一律 SubMenu（窄屏换内容），不直接用组件库的 Sub
            self.assertNotIn("DropdownMenu.Sub", text, rel)
        menu = read("components/ui/menu.tsx")
        self.assertIn("export const MENU_EDGE = 8;", menu)
        self.assertIn("export const DropdownMenu = { ...Radix, Content, SubContent };", menu)
        css = read("globals.css").split("  .cc-menu {", 1)[1].split("}", 1)[0]
        # 限高：页面写的 --cc-menu-max（没写不封顶）与组件库量的可用高度取小；方向由包装按完整高度选（F11d）
        self.assertIn("max-width: var(--radix-dropdown-menu-content-available-width); max-height: min(var(--cc-menu-max, 100vh), "
                      "var(--radix-dropdown-menu-content-available-height, 100vh)); overflow-y: auto;", css)
        for rel in ("components/period-chip.tsx", "components/assistant-ui/projects.tsx"):
            self.assertNotIn("max-h-80", read(rel), rel)
        self.assertEqual(read("components/period-chip.tsx").count('className="cc-menu !min-w-40 [--cc-menu-max:var(--cc-menu-max-list)]"'), 2)
        # 方向：按触发按钮（aria-labelledby）上下的空间与内容完整高度选，换子菜单那一页时重选
        self.assertIn('const id = el.getAttribute("aria-labelledby");', menu)
        self.assertIn("el.scrollHeight + borders", menu)
        self.assertIn('lengthPx(style.getPropertyValue("--cc-menu-max"))', menu)
        # 离边距离按传进来的 collisionPadding（数字或分边写）；打开期间内容变高、键盘弹出收起时重选
        self.assertIn('const edgeTop = typeof collisionPadding === "number" ? collisionPadding : (collisionPadding?.top ?? 0);', menu)
        self.assertIn('const edgeBottom = typeof collisionPadding === "number" ? collisionPadding : (collisionPadding?.bottom ?? 0);', menu)
        self.assertIn("const below = bottom - box.bottom - sideOffset - edgeBottom;", menu)
        self.assertIn("const above = box.top - top - sideOffset - edgeTop;", menu)
        self.assertIn("resized?.observe(el);", menu)
        self.assertIn("changed.observe(el, { childList: true, subtree: true });", menu)
        self.assertIn('window.visualViewport?.addEventListener("resize", measure);', menu)
        self.assertIn('window.visualViewport?.removeEventListener("resize", measure);', menu)
        self.assertIn("}, [el, side, sideways, sideOffset, edgeTop, edgeBottom, open]);", menu)
        # 旁边展开（left / right）照传进来的方向，之前选过的上 / 下不盖掉它
        self.assertIn('const sideways = side === "left" || side === "right";', menu)
        self.assertIn("side={sideways ? side : (chosen ?? side)}", menu)

    def test_submenus_use_the_wrapper_and_the_drawer_row_menu_opens_downwards(self):
        page = read("assistant.tsx")
        self.assertIn('<SubMenu icon={<GlobeIcon />} label={t("language")} sideOffset={8}>', page)
        self.assertIn('<SubMenu icon={<InfoIcon />} label={t("learnMore")} sideOffset={8}>', page)
        projects = read("components/assistant-ui/projects.tsx")
        self.assertIn('<SubMenu icon={<Building2Icon />} label={co.moveTo} className="cc-menu !min-w-52 max-w-72">', projects)
        # 手机胶囊菜单里的「处理月份」移到了输入框下巴（设计文档 4.3），菜单里只留「移到…」
        self.assertNotIn("<SubMenu label={co.period}", projects)
        rows = read("components/assistant-ui/thread-list.tsx")
        # 行菜单、组标题菜单一律向下开、右对齐（设计文档 3.6，电脑同手机抽屉）；放不下时包装组件翻到上面（F11d）
        more = rows.split("const ThreadListItemMore: FC", 1)[1]
        self.assertEqual(more.count('side="bottom"\n            align="end"'), 1)
        self.assertEqual(more.count('side="bottom"\n      align="end"'), 1)  # 多选时的批量项
        self.assertNotIn('side={isMobile ? "bottom" : "right"}', rows)
        # 组标题「···」（分组和排序，公司组与已置顶 / 最近 / 月份共用）
        group = read("components/assistant-ui/sidebar-group.tsx")
        # 右对齐；只有手机上的公司组头（alignHead）左边贴着组头开关的左边（第 7 轮：各行首字从菜单左边露出）
        self.assertIn('<DropdownMenu.Content\n          side="bottom"\n          align={head ? "start" : "end"}\n          alignOffset={head ? offset : 0}\n          sideOffset={4}', group)
        self.assertIn("const head = alignHead && offset !== null;", group)

    @NEEDS_NODE
    def test_menus_keep_8px_from_the_edge_and_phones_open_submenus_in_place(self):
        # 组件库换成记下 props 的空壳：看包装交给组件库的是什么；窄屏 / 宽屏由 use-mobile 的桩决定
        fake = r"""
import { createElement } from "react";
const g = globalThis as any;
g.seen = [] as Array<{ part: string; props: any }>;
const part = (name: string) => (props: any) => {
  g.seen.push({ part: name, props });
  return createElement("div", { "data-part": name, "data-slot": props["data-slot"], style: props.style }, props.children);
};
export const DropdownMenu: any = Object.fromEntries(
  ["Root", "Trigger", "Portal", "Content", "Item", "Separator", "Sub", "SubTrigger", "SubContent"].map((n) => [n, part(n)]),
);
"""
        harness = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { DropdownMenu, MENU_EDGE, SubMenu } from "@/components/ui/menu";

const g = globalThis as any;
const markup = renderToStaticMarkup(
  h(DropdownMenu.Content, { align: "end", className: "cc-menu" },
    h(DropdownMenu.Item, null, "Rename"),
    h(SubMenu, { label: "Move to", className: "cc-menu !min-w-52" }, h(DropdownMenu.Item, null, "Acme"))),
);
const content = g.seen.find((s: any) => s.part === "Content");
assert.equal(MENU_EDGE, 8);
assert.equal(content.props.collisionPadding, 8);
assert.equal(content.props.align, "end");
const parts = g.seen.map((s: any) => s.part);
"""
        wide = harness + r"""
// 宽屏：组件库的子菜单原样，旁边那块同样离边 8px
assert.ok(parts.includes("Sub") && parts.includes("SubTrigger"));
const sub = g.seen.find((s: any) => s.part === "SubContent");
assert.deepEqual([sub.props.collisionPadding, sub.props.sideOffset, sub.props.className], [8, 6, "cc-menu !min-w-52"]);
assert.doesNotMatch(markup, /cc-menu-page/);
// 写明的值照样用
g.seen.length = 0;
renderToStaticMarkup(h(DropdownMenu.Content, { collisionPadding: 16 }, "x"));
assert.equal(g.seen.find((s: any) => s.part === "Content").props.collisionPadding, 16);
g.done();
"""
        narrow = harness + r"""
// 窄屏：没有旁边展开的子菜单；那一行是普通菜单项，点了不关菜单、换成子菜单的内容（还没点：子菜单内容不出现）
assert.ok(!parts.includes("Sub") && !parts.includes("SubTrigger") && !parts.includes("SubContent"));
assert.match(markup, /data-slot="cc-menu-page" style="display:contents"/);
assert.match(markup, /data-slot="cc-menu-subpage" style="display:none"/);
assert.doesNotMatch(markup, /Acme/);
const trigger = g.seen.find((s: any) => s.part === "Item" && s.props["data-slot"] === "cc-menu-sub");
let prevented = false;
try { trigger.props.onSelect({ preventDefault: () => (prevented = true) }); } catch {}
assert.equal(prevented, true);
g.done();
"""
        flip = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { pickSide } from "@/components/ui/menu";
// 原方向放得下：不动
assert.equal(pickSide("bottom", 120, 170, 500), "bottom");
assert.equal(pickSide("top", 120, 500, 170), "top");
// 靠下的行菜单：下边 170、上边 500，要 300 → 翻到上面；反过来同理
assert.equal(pickSide("bottom", 300, 170, 500), "top");
assert.equal(pickSide("top", 300, 500, 170), "bottom");
// 刚好放得下也不翻
assert.equal(pickSide("bottom", 170, 170, 500), "bottom");
// 两边都放不下：取大的一边（菜单里滚动）；一样大不翻
assert.equal(pickSide("bottom", 800, 170, 500), "top");
assert.equal(pickSide("bottom", 800, 500, 170), "bottom");
assert.equal(pickSide("top", 800, 500, 170), "bottom");
assert.equal(pickSide("bottom", 800, 300, 300), "bottom");
(globalThis as any).done();
"""
        run_ts(flip, stubs={"radix-ui": fake, "@/hooks/use-mobile": "export const useIsMobile = () => false;"})
        run_ts(wide, stubs={"radix-ui": fake, "@/hooks/use-mobile": "export const useIsMobile = () => false;"})
        run_ts(narrow, stubs={"radix-ui": fake, "@/hooks/use-mobile": "export const useIsMobile = () => true;"})
        menu = read("components/ui/menu.tsx")
        # 子菜单那一页：顶上「‹ 名称」回上一层（不加新词条，用的是子菜单自己的名字）
        back = menu.split('data-slot="cc-menu-back"', 1)[1].split("</Radix.Item>", 1)[0]
        self.assertIn("pages.setOpen(null);", back)
        self.assertIn("<ChevronLeftIcon />", back)
        self.assertIn('<span className="flex-1">{label}</span>', back)


# ── F11f：抽屉里打字 ─────────────────────────────────────────────────────────────────────────

class DrawerTypingTests(unittest.TestCase):
    def test_drawer_bottom_bar_steps_aside_while_typing(self):
        page = read("assistant.tsx")
        drawer = page.split("function MobileDrawer()", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("onFocusCapture={(e) => setTyping(isTextField(e.target))}", drawer)
        self.assertIn("onBlurCapture={() => setTyping(false)}", drawer)
        self.assertIn('data-slot="cc-drawer-foot"\n        hidden={typing}', drawer)
        # hidden 要压得过 flex：Tailwind 的 preflight 给 [hidden] 加了 !important
        preflight = (FRONTEND / "node_modules/tailwindcss/preflight.css")
        if preflight.is_file():
            self.assertRegex(preflight.read_text(encoding="utf-8"), r"\[hidden\]:where\(:not\(\[hidden='until-found'\]\)\) \{\s*display: none !important;")


if __name__ == "__main__":
    unittest.main()
