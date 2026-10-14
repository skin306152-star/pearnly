"""CC 的样式规范「ccs」（docs/development/ccs.md，教程问题清单第 12 条）的检查。

· globals.css 的 --ccs-* 与 Anthropic 官方公开的 Claude 样式变量表逐项一致（浅色 / 深色），--cc-* 引用它们，旧的另配状态色不再出现；
· scripts/check-frontend-tokens.py 的判据：写死的颜色 / 圆角 / 阴影都拦、引用变量的写法都放行，注释不算；当前源码通过；
· 对话里卡片的规矩：步骤详情不在卡内套滚动（长内容收起、点一下展开）、发送中的附件用骨架不用转圈、手机点按区不小于 44pt。
要跑 clipText 的那一条需要 node 与前端依赖（npm ci --prefix frontend/assistant），没有就跳过。
"""
import importlib.util
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, NODE, run_ts  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend/assistant/src"
GATE = ROOT / "scripts/check-frontend-tokens.py"


def _gate():
    spec = importlib.util.spec_from_file_location("check_frontend_tokens", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _gate()


def css_block(css, selector):
    """globals.css 里第一个以 selector 开头、含 --ccs- 的块，解析成 {变量: 值}。"""
    for m in re.finditer(r"^" + re.escape(selector) + r" \{\n(.*?)\n\}", css, re.M | re.S):
        if "--ccs-" in m.group(1):
            body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
            return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))
    raise AssertionError(f"no ccs block for {selector}")


def cc_block(css, selector):
    """--cc-* 那一块（第二个同名块）。"""
    blocks = [m.group(1) for m in re.finditer(r"^" + re.escape(selector) + r" \{\n(.*?)\n\}", css, re.M | re.S)]
    body = next(b for b in blocks if "--cc-brand:" in b)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))


def hex_alpha(value, alpha=None):
    r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgb({r} {g} {b} / {alpha})" if alpha is not None else value.lower()


# 官方表照录（https://claude.com/docs/connectors/building/mcp-apps/design-guidelines「Style variables」，2026-10-15）
OFFICIAL = {
    "bg-primary": ("#FFFFFF", "#30302E"), "bg-secondary": ("#F5F4ED", "#262624"), "bg-tertiary": ("#FAF9F5", "#141413"),
    "bg-inverse": ("#141413", "#FAF9F5"), "bg-info": ("#D6E4F6", "#253E5F"), "bg-danger": ("#F7ECEC", "#602A28"),
    "bg-success": ("#E9F1DC", "#1B4614"), "bg-warning": ("#F6EEDF", "#483A0F"),
    "text-primary": ("#141413", "#FAF9F5"), "text-secondary": ("#3D3D3A", "#C2C0B6"), "text-tertiary": ("#73726C", "#9C9A92"),
    "text-inverse": ("#FFFFFF", "#141413"), "text-info": ("#3266AD", "#80AADD"), "text-danger": ("#7F2C28", "#EE8884"),
    "text-success": ("#265B19", "#7AB948"), "text-warning": ("#5A4815", "#D1A041"),
    "text-disabled": (("#141413", 0.5), ("#FAF9F5", 0.5)),
    "border-primary": (("#1F1E1D", 0.4), ("#DEDCD1", 0.4)), "border-secondary": (("#1F1E1D", 0.3), ("#DEDCD1", 0.3)),
    "border-tertiary": (("#1F1E1D", 0.15), ("#DEDCD1", 0.15)), "border-info": ("#4682D5", "#4682D5"),
    "border-danger": ("#A73D39", "#CD5C58"), "border-success": ("#437426", "#599130"), "border-warning": ("#805C1F", "#A87829"),
    "ring-primary": (("#141413", 0.7), ("#FAF9F5", 0.7)), "ring-info": (("#3266AD", 0.5), ("#80AADD", 0.5)),
    "ring-danger": (("#A73D39", 0.5), ("#CD5C58", 0.5)),
}
SHARED = {
    "radius-xs": "4px", "radius-sm": "6px", "radius-md": "8px", "radius-lg": "10px", "radius-xl": "12px", "radius-full": "9999px",
    "border-width": "0.5px",
    "shadow-hairline": "0 1px 2px 0 rgba(0, 0, 0, 0.05)",
    "shadow-sm": "0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px -1px rgba(0, 0, 0, 0.1)",
    "shadow-md": "0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1)",
    "shadow-lg": "0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -4px rgba(0, 0, 0, 0.1)",
    "text-xs-size": "12px", "text-xs-line-height": "1.4", "text-sm-size": "14px", "text-sm-line-height": "1.4",
    "text-md-size": "16px", "text-md-line-height": "1.4", "text-lg-size": "20px", "text-lg-line-height": "1.25",
    "heading-xl-size": "24px", "heading-2xl-size": "28px", "heading-2xl-line-height": "1.1",
    "heading-3xl-size": "36px", "heading-3xl-line-height": "1",
}


class OfficialValuesTests(unittest.TestCase):
    def setUp(self):
        self.css = (SRC / "globals.css").read_text(encoding="utf-8")
        self.light = css_block(self.css, ":root")
        self.dark = css_block(self.css, ".dark")

    def test_ccs_matches_the_official_table_in_both_themes(self):
        for name, (light, dark) in OFFICIAL.items():
            for theme, want in ((self.light, light), (self.dark, dark)):
                expected = hex_alpha(*want) if isinstance(want, tuple) else want.lower()
                self.assertEqual(theme.get("--ccs-" + name), expected, name)
        for name, value in SHARED.items():
            self.assertEqual(self.light.get("--ccs-" + name), value, name)

    def test_cc_names_point_at_the_official_values(self):
        cc = cc_block(self.css, ":root")
        for name, target in {
            "--cc-ink": "--ccs-text-primary", "--cc-ink-2": "--ccs-text-secondary", "--cc-ink-3": "--ccs-text-tertiary",
            "--cc-bg-surface": "--ccs-bg-primary", "--cc-line": "--ccs-border-tertiary", "--cc-hairline": "--ccs-border-width",
            "--cc-danger": "--ccs-text-danger", "--cc-danger-soft": "--ccs-bg-danger", "--cc-success": "--ccs-text-success",
            "--cc-warning": "--ccs-text-warning", "--cc-info": "--ccs-text-info", "--cc-focus-ring": "--ccs-ring-primary",
            "--cc-status-online": "--ccs-border-success", "--cc-status-processing": "--ccs-border-info",
            "--cc-status-attention": "--ccs-border-warning", "--cc-status-offline": "--ccs-border-danger",
            "--cc-radius-md": "--ccs-radius-md", "--cc-radius-xl": "--ccs-radius-xl", "--cc-shadow-sm": "--ccs-shadow-sm",
            "--cc-text-sm": "--ccs-text-sm-size",
        }.items():
            self.assertEqual(cc.get(name), f"var({target})", name)
        # 强调色仍是 CC 橘黄；外壳实测值保留
        self.assertEqual(cc["--cc-brand"], "#f28c28")
        for name, value in {"--cc-bg-app": "#fbfbfb", "--cc-bg-sidebar": "#fbfaf8", "--cc-sidebar-w": "18rem",
                            "--cc-radius-composer": "20px", "--cc-radius-bubble": "18px"}.items():
            self.assertEqual(cc[name], value, name)
        self.assertIn("外壳实测", self.css)
        # 另配的旧状态色不再出现
        for old in ("#27ae60", "#b7950b", "#d35400", "#c0392b", "#1a1a1a", "#2f7d4f", "#c4373a"):
            self.assertNotIn(old, self.css.lower(), old)

    def test_tailwind_utilities_resolve_to_ccs(self):
        theme = self.css.split("@theme inline {", 1)[1].split("\n}", 1)[0]
        for line in ("--default-border-width: var(--cc-hairline);", "--shadow-sm: var(--cc-shadow-sm);",
                     "--radius-md: var(--cc-radius-md);", "--text-sm: var(--ccs-text-sm-size);",
                     "--text-sm--line-height: var(--ccs-text-sm-line-height);", "--color-warning: var(--cc-warning);"):
            self.assertIn(line, theme)


class TokenGateTests(unittest.TestCase):
    THEME = """@theme inline {
  --color-ink: var(--cc-ink);
  --color-danger: var(--cc-danger);
  --radius-md: var(--cc-radius-md);
  --radius-dialog: var(--cc-radius-dialog);
  --shadow-sm: var(--cc-shadow-sm);
}
"""

    def scan(self, source, name="components/x.tsx"):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "globals.css").write_text(self.THEME + ":root { --cc-ink: #141413; }\n", encoding="utf-8")
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
            (root / "node_modules/lib").mkdir(parents=True)
            (root / "node_modules/lib/x.tsx").write_text('const c = "#ff0000";', encoding="utf-8")
            return [(rule, snippet) for _, _, rule, snippet in gate.scan(str(root))]

    def test_hard_coded_values_are_blocked(self):
        bad = {
            'const a = "#c0392b";': "color-hex",
            'const b = { color: "rgba(0,0,0,.5)" };': "color-function",
            '<i className="text-red-500 hover:bg-emerald-50" />': "color-palette",
            '<i className="bg-black/50" />': "color-palette",
            '<i className="[&_svg]:text-white" />': "color-palette",
            '<i className="rounded-[14px]" />': "radius",
            '<i className="rounded-2xl" />': "radius",
            '<i className="p-2 rounded" />': "radius",
            '<i className="shadow-[0_2px_8px_var(--x)]" />': "shadow",
            '<i className="shadow-2xl" />': "shadow",
            '<i className="card shadow" />': "shadow",
            '<i className="drop-shadow-md" />': "shadow",
            '<i style={{ borderRadius: 12 }} />': "style-borderRadius",
            '<i style={{ boxShadow: "0 1px 2px black" }} />': "style-boxShadow",
            '<i style={{ color: "white" }} />': "style-color",
        }
        for source, rule in bad.items():
            self.assertIn(rule, [r for r, _ in self.scan(source)], source)

    def test_variables_and_mapped_utilities_pass(self):
        good = "\n".join([
            '<i className="text-ink bg-danger/10 rounded-md rounded-dialog rounded-full rounded-t-md rounded-[var(--cc-radius-bubble)]" />',
            '<i className="shadow-sm shadow-none shadow-[var(--cc-shadow-pop)] shadow-(--cc-shadow-pop) shadow-ink/10" />',
            '<i className="transition-[color,box-shadow] text-(length:--cc-text-sm) from-transparent" />',
            '<i style={{ color: "var(--cc-success)", boxShadow: "var(--cc-shadow-sm)", fill: "currentColor" }} />',
            'const url = "https://example.com/#add"; // 注释里的 #c0392b、text-red-500、rounded-2xl 不算',
            "/* rgba(0,0,0,.1) shadow-2xl */",
        ])
        self.assertEqual(self.scan(good), [])

    def test_only_node_modules_and_build_output_are_excluded(self):
        self.assertEqual(gate.SKIP_DIRS, {"node_modules", "dist", "build", ".vite"})
        self.assertNotIn("IGNORE", dir(gate))
        self.assertEqual(self.scan('const c = "#abcdef";', name="lib/x.ts"), [("color-hex", "#abcdef")])

    def test_the_assistant_frontend_passes_and_ci_runs_the_gate(self):
        result = subprocess.run([sys.executable, str(GATE)], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        workflow = (ROOT / ".github/workflows/development.yml").read_text(encoding="utf-8")
        self.assertIn("run: python3 scripts/check-frontend-tokens.py", workflow)
        self.assertIn("run: python3 tests/test_cc_ccs.py", workflow)


class CardRuleTests(unittest.TestCase):
    def test_cards_in_the_conversation_follow_the_card_rules(self):
        steps = (SRC / "components/tool-uis/steps.tsx").read_text(encoding="utf-8")
        # 卡内不套滚动：步骤详情自动撑高，长了收起
        self.assertNotRegex(steps, r"max-h-|overflow-(?:y-)?auto|overflow-scroll")
        self.assertIn("<Clipped text={step.input} />", steps)
        self.assertEqual(steps.count("cc-tap"), 3)
        thread = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        pending = thread.split("const PendingSendBubble: FC = () => {", 1)[1].split("\n};", 1)[0]
        self.assertNotIn("animate-spin", pending)
        self.assertIn("<Skeleton", pending)
        files = thread.split("const MessageFiles: FC = () => {", 1)[1].split("\n};", 1)[0]
        self.assertIn('className="cc-icon-btn cc-tap"', files)
        notice = thread.split("const NoticeMessage: FC = () => {", 1)[1].split("\n};", 1)[0]
        self.assertIn('className="cc-btn cc-tap"', notice)
        css = (SRC / "globals.css").read_text(encoding="utf-8")
        self.assertIn("@media (pointer: coarse) { .cc-tap { min-height: var(--cc-tap); min-width: var(--cc-tap); } }", css)
        self.assertIn("--cc-tap: 2.75rem;", css)

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_long_step_details_fold_instead_of_scrolling(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { CLIP_CHARS, CLIP_LINES, clipText } from "@/components/tool-uis/steps";
const g = globalThis as any;
const lines = (n: number) => Array.from({ length: n }, (_, i) => `line ${i + 1}`).join("\n");
// 短的整段显示
assert.deepEqual(clipText(lines(CLIP_LINES)), { text: lines(CLIP_LINES), hidden: 0 });
// 多了先显示前 CLIP_LINES 行，其余算成「… N 行」；展开后整段
assert.deepEqual(clipText(lines(CLIP_LINES + 5)), { text: lines(CLIP_LINES), hidden: 5 });
assert.deepEqual(clipText(lines(40), true), { text: lines(40), hidden: 0 });
// 一整行很长（压成一行的 JSON）：截到 CLIP_CHARS，被截的那一行算 1 行
const long = "x".repeat(CLIP_CHARS * 3);
assert.deepEqual(clipText(long), { text: "x".repeat(CLIP_CHARS), hidden: 1 });
assert.deepEqual(clipText(`a\n${long}\nb`), { text: `a\n${"x".repeat(CLIP_CHARS - 2)}`, hidden: 2 });
g.done();
""")


if __name__ == "__main__":
    unittest.main()
