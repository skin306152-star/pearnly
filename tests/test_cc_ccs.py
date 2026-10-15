"""CC 的样式规范「ccs」（docs/development/ccs.md，教程问题清单第 12 条）的检查。

· globals.css 的 --ccs-* 与 Anthropic 官方公开的 Claude 样式变量表逐项一致（浅色 / 深色），旧的另配状态色不再出现；
· --code-* 与 Claude 设计系统实测（docs/work/2026-10-16-cc-shell-design.md 第 2 节）逐项一致（浅色 / 深色），--cc-* 的结构色、
  尺寸、阴影、动效引用它，状态色等仍引用官方表；三个变体（网页 / 桌面窗口 / 手机）的取值；抄在别处的页面底色与之一致；
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


def code_block(css, selector):
    """--code-* 那一块（含 --code-ink-1 的同名块）。"""
    for m in re.finditer(r"^" + re.escape(selector) + r" \{\n(.*?)\n\}", css, re.M | re.S):
        if "--code-ink-1:" in m.group(1):
            body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
            return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))
    raise AssertionError(f"no code block for {selector}")


def variant_block(css, name):
    m = re.search(r'^:root\[data-cc-variant="' + name + r'"\] \{\n(.*?)\n\}', css, re.M | re.S)
    assert m, name
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)))


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


# Claude 设计系统实测（设计文档 2.1 颜色、2.4 阴影）：(浅色, 深色)；「黑 / 白 N%」= rgb(0 0 0 / N) / rgb(255 255 255 / N)
BLACK, WHITE = "rgb(0 0 0 / {})", "rgb(255 255 255 / {})"
CODE_COLORS = {
    "ink-1": ("#0b0b0b", "#f0efec"), "ink-2": ("#52514e", "#c3c2b7"), "ink-3": ("#898781", "#898781"),
    "ink-disabled": (BLACK.format(0.35), WHITE.format(0.35)),
    "surface-0": ("#f9f9f7", "#0b0b0b"), "surface-1": ("#fcfcfb", "#151515"), "surface-2": ("#ffffff", "#1a1a19"),
    "surface-3": ("#ffffff", "#20201f"), "surface-popover": ("#ffffff", "#20201f"),
    "sidebar-web": ("#fafaf9", "#111111"), "sidebar-desktop": ("#ffffff", "#262626"),
    "alpha-1": (BLACK.format(0.05), WHITE.format(0.05)), "alpha-2": (BLACK.format(0.1), WHITE.format(0.1)),
    "alpha-3": (BLACK.format(0.2), WHITE.format(0.2)),
    "fill-hover": (BLACK.format(0.05), WHITE.format(0.075)), "fill-selected": (BLACK.format(0.1), WHITE.format(0.15)),
    "row-hover": ("#f0efec", WHITE.format(0.075)), "row-selected": ("#edece8", WHITE.format(0.15)),
    "line": (BLACK.format(0.1), WHITE.format(0.1)), "line-strong": (BLACK.format(0.2), WHITE.format(0.2)),
    "line-stronger": (BLACK.format(0.4), WHITE.format(0.4)),
    "link": ("#184f95", "#6da7ec"), "code-ink": ("#8e2626", "#ec7e7e"), "code-bg": (BLACK.format(0.05), WHITE.format(0.05)),
    "danger": ("#8e2626", "#ec7e7e"), "success": ("#006300", "#0ca30c"), "warning": ("#734500", "#db9300"),
    "focus": ("#2a78d6", "#2a78d6"), "focus-halo": ("#cde2fb", "rgb(24 79 149 / 0.6)"), "clay": ("#d97757", "#d97757"),
    "backdrop": (BLACK.format(0.4), BLACK.format(0.5)),
    "tooltip-bg": ("#0b0b0b", "#20201f"), "tooltip-ink": ("#ffffff", "#f0efec"),
    "search-bg": (WHITE.format(0.5), WHITE.format(0.05)),
    "shadow-card": ("0 0 0 1px rgb(0 0 0 / 0.06), 0 4px 24px rgb(0 0 0 / 0.04)",
                    "inset 0 0 0 1px rgb(255 255 255 / 0.06), 0 4px 24px rgb(0 0 0 / 0.08)"),
    "shadow-float": ("0 0 0 0.5px #0000000d, 0 2px 8px #0000000a, 0 12px 32px #00000008",
                     "0 0 0 0.5px #ffffff0f, 0 2px 8px #0000004d, 0 12px 32px #00000040"),
    "shadow-pop": ("0 0 0 0.5px #0000000f, 0 4px 12px #0000000f, 0 16px 40px #00000014",
                   "0 0 0 0.5px #ffffff14, 0 4px 12px #00000066, 0 16px 40px #00000080"),
    "shadow-composer": ("0 0 0 1px var(--code-line), 0 4px 20px rgb(0 0 0 / 0.035)",
                        "0 0 0 1px var(--code-line), 0 4px 20px rgb(0 0 0 / 0.25)"),
    "shadow-search": ("0 1px 2px rgb(0 0 0 / 0.05)", "0 1px 2px rgb(0 0 0 / 0.05)"),
}
# 设计文档 2.2 字号、2.3 尺寸、3.6 菜单、2.4 动效（深浅色同一套）
CODE_SHARED = {
    "text-caption": "12px", "text-caption-lh": "17px", "text-footnote": "13px", "text-footnote-lh": "17px", "text-panel-lh": "19px",
    "text-body": "14px", "text-body-lh": "20px", "text-heading": "15px", "text-heading-lh": "20px",
    "text-prose": "16px", "text-prose-lh": "24px", "text-title": "22px", "text-title-lh": "28px",
    "text-page": "24px", "text-page-lh": "31px", "text-page-weight": "500", "text-group": "13px", "text-group-lh": "16px",
    "control": "32px", "control-nested": "22px", "control-xs": "24px", "control-sm": "28px", "control-lg": "40px",
    "radius-code": "5px", "radius-6": "6px", "radius-7": "7px", "radius-8": "8px", "radius-10": "10px", "radius-menu": "12px",
    "radius-input": "14px",
    "gap-1": "8px", "gap-2": "12px", "gap-3": "16px", "gap-4": "28px", "gap-5": "40px",
    "pad-1": "6px", "pad-2": "8px", "pad-3": "12px", "pad-4": "16px", "pad-5": "24px", "icon": "20px", "icon-sm": "16px",
    "sidebar-w": "288px", "sidebar-head": "44px", "search-h": "32px", "row": "32px", "row-desktop": "30px", "row-phone": "44px",
    "row-gap": "2px", "row-radius": "8px", "row-pad": "10px", "row-ctl": "24px", "row-lead": "28px",
    "group-band": "32px", "group-band-phone": "44px", "group-top": "16px", "group-top-desktop": "14px", "group-chevron": "12px",
    "tray": "48px", "tray-btn": "32px", "sidebar-float": "8px",
    "menu-pad": "4px", "menu-min-w": "128px", "menu-item": "32px", "menu-item-py": "6px", "menu-item-px": "10px",
    "menu-sep-inset": "10px", "menu-sep-gap": "4px", "menu-sep": "1px",
    "ease": "cubic-bezier(0.32, 0.72, 0, 1)", "ease-fast": "cubic-bezier(0.2, 0, 0, 1)",
    "ease-out": "cubic-bezier(0.165, 0.84, 0.44, 1)",
    "dur-1": "60ms", "dur-2": "120ms", "dur-3": "200ms", "dur-4": "300ms", "dur-5": "450ms",
}
PAGE_BG = {"light": "#fcfcfb", "dark": "#151515"}


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
        # 状态色、细线宽、圆角档位、官方阴影、字号表仍引用官方表
        for name, target in {
            "--cc-hairline": "--ccs-border-width",
            "--cc-danger": "--ccs-text-danger", "--cc-danger-soft": "--ccs-bg-danger", "--cc-success": "--ccs-text-success",
            "--cc-warning": "--ccs-text-warning", "--cc-info": "--ccs-text-info",
            "--cc-status-online": "--ccs-border-success", "--cc-status-processing": "--ccs-border-info",
            "--cc-status-attention": "--ccs-border-warning", "--cc-status-offline": "--ccs-border-danger",
            "--cc-radius-md": "--ccs-radius-md", "--cc-radius-xl": "--ccs-radius-xl", "--cc-shadow-sm": "--ccs-shadow-sm",
            "--cc-text-sm": "--ccs-text-sm-size",
        }.items():
            self.assertEqual(cc.get(name), f"var({target})", name)
        # 强调色仍是 CC 橘黄；手机外壳实测值、侧栏宽、输入框圆角保留
        self.assertEqual(cc["--cc-brand"], "#f28c28")
        for name, value in {"--cc-sidebar-w": "18rem", "--cc-radius-composer": "20px", "--cc-mbtn-bg": "#f6f6f4"}.items():
            self.assertEqual(cc[name], value, name)
        self.assertIn("外壳实测", self.css)
        # 另配的旧状态色、旧页面底色不再出现
        for old in ("#27ae60", "#b7950b", "#d35400", "#c0392b", "#1a1a1a", "#2f7d4f", "#c4373a", "#fbfbfb", "#fbfaf8"):
            self.assertNotIn(old, self.css.lower(), old)

    def test_code_layer_matches_the_measured_design_system(self):
        light, dark = code_block(self.css, ":root"), code_block(self.css, ".dark")
        for name, (want_light, want_dark) in CODE_COLORS.items():
            self.assertEqual(light.get("--code-" + name), want_light, name)
            self.assertEqual(dark.get("--code-" + name, light.get("--code-" + name)), want_dark, name)
        for name, value in CODE_SHARED.items():
            self.assertEqual(light.get("--code-" + name), value, name)
            self.assertNotIn("--code-" + name, dark, name)
        self.assertEqual(light["--code-sidebar-w"], "288px")
        self.assertEqual(cc_block(self.css, ":root")["--cc-sidebar-w"], "18rem")  # 同一个 288

    def test_cc_structure_points_at_the_code_layer(self):
        cc = cc_block(self.css, ":root")
        for name, target in {
            "--cc-ink": "--code-ink-1", "--cc-ink-2": "--code-ink-2", "--cc-ink-3": "--code-ink-3", "--cc-ink-4": "--code-ink-disabled",
            "--cc-bg-app": "--code-surface-1", "--cc-bg-sidebar": "--code-sidebar-web", "--cc-bg-surface": "--code-surface-2",
            "--cc-bg-popover": "--code-surface-popover", "--cc-bg-subtle": "--code-alpha-1",
            "--cc-bg-hover": "--code-fill-hover", "--cc-bg-active": "--code-fill-selected", "--cc-bg-selected": "--code-fill-selected",
            "--cc-bg-overlay": "--code-backdrop",
            "--cc-alpha-1": "--code-alpha-1", "--cc-alpha-2": "--code-alpha-2", "--cc-alpha-3": "--code-alpha-3",
            "--cc-fill-hover": "--code-fill-hover", "--cc-fill-selected": "--code-fill-selected",
            "--cc-row-hover": "--code-row-hover", "--cc-row-selected": "--code-row-selected", "--cc-list-selected": "--cc-row-hover",
            "--cc-line": "--code-line", "--cc-line-strong": "--code-line-strong", "--cc-line-stronger": "--code-line-stronger",
            "--cc-link": "--code-link", "--cc-code-ink": "--code-code-ink", "--cc-code-bg": "--code-code-bg",
            "--cc-tooltip-bg": "--code-tooltip-bg", "--cc-tooltip-ink": "--code-tooltip-ink", "--cc-search-bg": "--code-search-bg",
            "--cc-focus-ring": "--code-focus", "--cc-focus-halo": "--code-focus-halo", "--cc-bubble-bg": "--cc-alpha-1",
            "--cc-shadow-card": "--code-shadow-card", "--cc-shadow-float": "--code-shadow-float", "--cc-shadow-pop": "--code-shadow-pop",
            "--cc-shadow-composer": "--code-shadow-composer", "--cc-shadow-search": "--code-shadow-search",
            "--cc-radius-bubble": "--code-radius-10", "--cc-radius-code": "--code-radius-code", "--cc-radius-menu": "--code-radius-menu",
            "--cc-radius-row": "--code-row-radius", "--cc-radius-row-ctl": "--code-radius-6",
            "--cc-body-size": "--code-text-body", "--cc-body-lh": "--code-text-body-lh",
            "--cc-row-h": "--code-row", "--cc-group-h": "--code-group-band", "--cc-group-top": "--code-group-top",
            "--cc-group-font": "--code-text-group", "--cc-group-lh": "--code-text-group-lh", "--cc-row-ctl": "--code-row-ctl",
            "--cc-row-lead": "--code-row-lead", "--cc-icon": "--code-icon", "--cc-control": "--code-control",
            "--cc-search-h": "--code-search-h", "--cc-menu-pad": "--code-menu-pad", "--cc-menu-min-w": "--code-menu-min-w",
            "--cc-menu-item-h": "--code-menu-item",
            "--cc-ease": "--code-ease", "--cc-ease-fast": "--code-ease-fast", "--cc-ease-out": "--code-ease-out",
            "--cc-dur-instant": "--code-dur-1", "--cc-dur-fast": "--code-dur-2", "--cc-dur": "--code-dur-3",
            "--cc-dur-slow": "--code-dur-4", "--cc-dur-pop": "--code-dur-5", "--cc-reveal": "--cc-dur-fast",
        }.items():
            self.assertEqual(cc.get(name), f"var({target})", name)
        # 聚焦：内描 1px 页面色 + 外 1px 蓝 + 6px 光晕
        self.assertEqual(cc["--cc-shadow-focus"],
                         "inset 0 0 0 1px var(--cc-bg-app), 0 0 0 1px var(--code-focus), 0 0 0 6px var(--code-focus-halo)")
        self.assertEqual(cc["--cc-reveal-rest"], "0")
        self.assertEqual(cc["--cc-sidebar-float"], "0px")
        # 结构色只在 --code-* 里分深浅：--cc-* 深色块不再另给底色、叠色与弹层阴影；页面底色所有变体（含手机）同一个
        dark = [b for b in re.findall(r"^\.dark \{\n(.*?)\n\}", self.css, re.M | re.S) if "--cc-brand:" in b][0]
        for name in ("--cc-bg-app", "--cc-bg-sidebar", "--cc-bg-subtle", "--cc-bg-hover", "--cc-bg-active", "--cc-shadow-pop",
                     "--cc-shadow-composer:", "--cc-focus-halo"):
            self.assertNotIn(name, dark, name)
        self.assertEqual(self.css.count("--cc-bg-app:"), 1)
        self.assertNotIn("@media (max-width: 767px) { :root", self.css)

    def test_three_variants(self):
        self.assertEqual(variant_block(self.css, "desktop"), {
            "--cc-bg-sidebar": "var(--code-sidebar-desktop)", "--cc-row-h": "var(--code-row-desktop)",
            "--cc-group-top": "var(--code-group-top-desktop)", "--cc-sidebar-float": "var(--code-sidebar-float)",
        })
        self.assertEqual(variant_block(self.css, "phone"), {
            "--cc-row-h": "var(--code-row-phone)", "--cc-group-h": "var(--code-group-band-phone)", "--cc-reveal-rest": "1",
            "--cc-body-size": "var(--code-text-prose)", "--cc-body-lh": "var(--code-text-prose-lh)",
        })
        # 桌面窗口：侧栏浮成卡片（离窗口边 8px、卡片阴影）
        self.assertIn(':root[data-cc-variant="desktop"] [data-slot="sidebar-container"] { padding: var(--cc-sidebar-float); '
                      "border-inline-width: 0; }", self.css)
        self.assertIn(':root[data-cc-variant="desktop"] [data-slot="sidebar-inner"] { border-radius: var(--cc-radius-menu); '
                      "box-shadow: var(--cc-shadow-card); }", self.css)
        # 启动时写一次（窄屏分界与手机外壳同一条），先于首帧
        variant = (SRC / "lib/variant.ts").read_text(encoding="utf-8")
        self.assertIn('import { MOBILE_QUERY } from "@/hooks/use-mobile";', variant)
        self.assertIn('import { isDesktopApp } from "@/lib/desktop";', variant)
        self.assertIn("root.dataset.ccVariant = pickVariant(desktop, narrow.matches);", variant)
        mobile = (SRC / "hooks/use-mobile.ts").read_text(encoding="utf-8")
        self.assertIn("window.matchMedia(MOBILE_QUERY)", mobile)
        main = (SRC / "main.tsx").read_text(encoding="utf-8")
        self.assertLess(main.index("installVariant();"), main.index("createRoot("))

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_pick_variant(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { pickVariant } from "@/lib/variant";
assert.equal(pickVariant(true, false), "desktop");
assert.equal(pickVariant(true, true), "desktop");
assert.equal(pickVariant(false, true), "phone");
assert.equal(pickVariant(false, false), "web");
(globalThis as any).done();
""", stubs={"@/lib/desktop": "export const isDesktopApp = () => false;"})

    def test_shared_classes_use_the_design_values(self):
        css = self.css
        def rule(selector):
            return css.split("\n  " + selector + " {", 1)[1].split("}", 1)[0]
        nav = rule(".cc-nav-item")
        for part in ("height: var(--cc-row-h);", "border-radius: var(--cc-radius-row);", "color: var(--cc-ink-2);",
                     "font-size: var(--cc-row-font);"):
            self.assertIn(part, nav)
        # 悬停只换底不换字色；当前页选中底 + 主色字；键盘焦点蓝环
        self.assertIn(".cc-nav-item:hover { background: var(--cc-row-hover); }", css)
        self.assertIn('.cc-nav-item[data-active], .cc-nav-item[data-active="true"] { background: var(--cc-row-selected); color: var(--cc-ink); }', css)
        self.assertIn(".cc-nav-item:focus-visible { box-shadow: var(--cc-shadow-focus); color: var(--cc-ink); }", css)
        self.assertIn(".cc-nav-item svg { width: var(--cc-icon); height: var(--cc-icon); flex-shrink: 0; }", css)
        self.assertIn("width: var(--cc-row-lead); height: var(--cc-row-lead);", rule(".cc-nav-icon"))
        label = css.split(".cc-section-label {", 1)[1].split("}", 1)[0]
        for part in ("color: var(--cc-ink-3);", "font-size: var(--cc-group-font);", "line-height: var(--cc-group-lh);",
                     "transition: color var(--cc-reveal) var(--cc-ease);"):
            self.assertIn(part, label)
        self.assertIn('[data-slot="sidebar-inner"]:hover .cc-section-label { color: var(--cc-ink-2); }', css)
        search = rule(".cc-search")
        for part in ("height: var(--cc-search-h);", "border-radius: var(--cc-radius-row);", "background: var(--cc-search-bg);",
                     "box-shadow: inset 0 0 0 var(--cc-hairline) var(--cc-line), var(--cc-shadow-search);"):
            self.assertIn(part, search)
        self.assertIn(".cc-search:focus-within { box-shadow: var(--cc-shadow-focus); }", css)
        self.assertIn(".cc-search input::placeholder { color: var(--cc-ink-3); }", css)
        self.assertIn("font-size: var(--cc-text-caption);", rule(".cc-search-hint"))
        row_btn = rule(".cc-row-btn")
        self.assertIn("width: var(--cc-row-ctl); height: var(--cc-row-ctl); border-radius: var(--cc-radius-row-ctl);", row_btn)
        menu = rule(".cc-menu")
        for part in ("min-width: var(--cc-menu-min-w);", "padding: var(--cc-menu-pad);", "background: var(--cc-bg-popover);",
                     "border-radius: var(--cc-radius-menu);", "box-shadow: inset 0 0 0 1px var(--cc-line), var(--cc-shadow-pop);"):
            self.assertIn(part, menu)
        item = rule(".cc-menu-item")
        for part in ("min-height: var(--cc-menu-item-h);", "padding: var(--code-menu-item-py) var(--code-menu-item-px);",
                     "border-radius: var(--cc-radius-row);", "font-size: var(--cc-row-font); line-height: var(--cc-row-lh);"):
            self.assertIn(part, item)
        self.assertIn(".cc-menu-item[data-disabled] { opacity: 0.4; pointer-events: none; }", css)
        self.assertIn("font-size: var(--cc-text-footnote);", rule(".cc-menu-value"))
        self.assertIn(".cc-menu-sep { height: 0; margin: var(--code-menu-sep-gap) var(--code-menu-sep-inset); "
                      "border-top: var(--code-menu-sep) solid var(--cc-line); }", css)
        self.assertIn(".cc-input:focus { box-shadow: var(--cc-shadow-focus); }", css)
        self.assertIn(".cc-btn:focus-visible { box-shadow: var(--cc-shadow-focus); }", css)
        # 对话正文 14/20（手机 16/24，变体切），字体仍按设置；用户气泡叠色 1、圆角 10、内边距 8/12
        self.assertIn("font-size: var(--cc-body-size); line-height: var(--cc-body-lh);", rule(".cc-reply"))
        self.assertIn("font-family: var(--cc-font-reply);", rule(".cc-reply"))
        bubble = rule(".cc-bubble-user")
        self.assertIn("padding: 0.5rem 0.75rem;", bubble)
        self.assertIn("font-size: var(--cc-body-size); line-height: var(--cc-body-lh);", bubble)
        self.assertIn("--cc-radius-code: var(--code-radius-code);", css)
        theme = css.split("@theme inline {", 1)[1].split("\n}", 1)[0]
        for line in ("--shadow-focus: var(--cc-shadow-focus);", "--shadow-float: var(--cc-shadow-float);",
                     "--radius-code: var(--cc-radius-code);", "--color-link: var(--cc-link);",
                     "--color-row-hover: var(--cc-row-hover);", "--color-tooltip: var(--cc-tooltip-bg);"):
            self.assertIn(line, theme)

    def test_components_use_the_focus_shadow_and_body_tokens(self):
        read = lambda rel: (SRC / rel).read_text(encoding="utf-8")
        for rel in ("components/ui/button.tsx", "components/ui/input.tsx"):
            self.assertIn("focus-visible:shadow-focus", read(rel), rel)
            self.assertNotIn("ring-[3px]", read(rel), rel)
        self.assertIn("shadow-pop", read("components/ui/dialog.tsx"))
        self.assertIn("bg-tooltip text-tooltip-ink", read("components/ui/tooltip.tsx"))
        thread = read("components/assistant-ui/thread.tsx")
        self.assertIn('className="cc-reply px-2 wrap-break-word"', thread)
        md = read("components/assistant-ui/markdown-text.tsx")
        self.assertIn('"aui-md-p my-3 first:mt-0 last:mb-0"', md)
        self.assertIn('<li className={cn("aui-md-li", className)} {...props} />', md)
        self.assertIn("mt-(--cc-md-list-top) mb-3 ps-(--cc-md-list-indent)", md)
        self.assertIn("[&>li]:ps-(--cc-md-li-indent)", md)
        self.assertIn('"aui-md-a text-link hover:text-link/80 underline underline-offset-2"', md)
        self.assertIn('"aui-md-inline-code bg-code-bg text-code-ink rounded-code px-1 py-0.5 font-mono text-[0.9em]"', md)
        self.assertIn("--cc-md-list-top: 1.5em;", self.css)
        self.assertIn("--cc-md-list-indent: 28px;", self.css)
        self.assertIn("--cc-md-li-indent: 7px;", self.css)

    def test_background_copies_follow_the_page_color(self):
        html = (ROOT / "apps/pearnly_core/pearnly_core/www/assistant.html").read_text(encoding="utf-8")
        self.assertIn('<meta name="theme-color" content="#fcfcfb" media="(prefers-color-scheme: light)" />', html)
        self.assertIn('<meta name="theme-color" content="#151515" media="(prefers-color-scheme: dark)" />', html)
        self.assertIn("html, body { margin: 0; height: 100%; background: #fcfcfb; }", html)
        self.assertIn("@media (prefers-color-scheme: dark) { html, body { background: #151515; } }", html)
        api = (ROOT / "apps/pearnly_core/pearnly_core/assistant/api.py").read_text(encoding="utf-8")
        self.assertIn("background:#fcfcfb;", api)
        self.assertIn("body{background:#151515;", api)
        splash = (ROOT / "scripts/build-ios-splash.py").read_text(encoding="utf-8")
        self.assertIn('THEMES = {"light": (252, 252, 251), "dark": (21, 21, 21)}', splash)
        tomo = (ROOT / "companion/src/companion/gui_tomo.py").read_text(encoding="utf-8")
        self.assertIn('APP_BG = {"light": "#fcfcfb", "dark": "#151515"}', tomo)
        vite = (ROOT / "frontend/assistant/vite.config.ts").read_text(encoding="utf-8")
        self.assertIn('background_color: "#fcfcfb",', vite)
        for text in (html, api, splash, tomo, vite):
            for old in ("#fbfbfb", "#f9f9f7", "#262624", "(249, 249, 247)", "(38, 38, 36)"):
                self.assertNotIn(old, text)
        self.assertEqual(PAGE_BG, {"light": CODE_COLORS["surface-1"][0], "dark": CODE_COLORS["surface-1"][1]})

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
