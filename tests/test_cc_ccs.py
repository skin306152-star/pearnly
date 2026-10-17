"""CC 的样式规范「ccs」（docs/development/ccs.md，教程问题清单第 12 条）的检查。

· globals.css 的 --ccs-* 与 Anthropic 官方公开的 Claude 样式变量表逐项一致（浅色 / 深色），旧的另配状态色不再出现；
· --code-* 与 Claude 设计系统实测（docs/work/2026-10-16-cc-shell-design.md 第 2 节）逐项一致（浅色 / 深色），--cc-* 的结构色、
  尺寸、阴影、动效引用它，状态色等仍引用官方表；三个变体（网页 / 桌面窗口 / 手机）的取值；抄在别处的页面底色与之一致；
· scripts/check-frontend-tokens.py 的判据：写死的颜色 / 圆角 / 阴影都拦、引用变量的写法都放行，注释不算；当前源码通过；
· 对话里卡片的规矩：步骤详情不在卡内套滚动（长内容收起、点一下展开）、发送中的附件用骨架不用转圈、手机点按区不小于 44pt。
要跑 clipText 的那一条需要 node 与前端依赖（npm ci --prefix frontend/assistant），没有就跳过。
"""
import importlib.util
import json
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


def declared_asks():
    """各产品工具声明的「允许」卡片问话（``ask`` / ``ask_when``，英文原文）连同服务端词条表里的中、泰、日译文。"""
    found = set()
    for path in (ROOT / "apps").glob("*/*/**/*.py"):
        text = path.read_text(encoding="utf-8")
        for block in re.findall(r'"ask(?:_when)?": (\{[^{}]*\}|"[^"]*")', text):
            found.update(re.findall(r'"([^"]+\?)"', block))
    asks = set(found)
    for table in (ROOT / "apps").glob("*/*/i18n/messages.tsv"):
        for line in table.read_text(encoding="utf-8").splitlines():
            cells = line.split("\t")
            if cells[0] in found:
                asks.update(cell for cell in cells[1:] if cell)
    return sorted(asks)


def css_block(css, selector):
    """globals.css 里第一个以 selector 开头、含 --ccs- 的块，解析成 {变量: 值}。"""
    for m in re.finditer(r"^" + re.escape(selector) + r" \{\n(.*?)\n\}", css, re.M | re.S):
        if "--ccs-" in m.group(1):
            body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
            return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))
    raise AssertionError(f"no ccs block for {selector}")


def cc_block(css, selector):
    """--cc-* 的定义：主块（含 --cc-brand）连同刻度层、用途层、运行时层几块同名块合在一起（标准化记录 1.1 分层）。"""
    blocks = [m.group(1) for m in re.finditer(r"^" + re.escape(selector) + r" \{\n(.*?)\n\}", css, re.M | re.S)]
    assert any("--cc-brand:" in b for b in blocks), selector
    out = {}
    for body in blocks:
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        defs = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))
        if any(name.startswith("--cc-") for name in defs):
            for name in defs:
                assert name not in out, f"{name} 在 {selector} 里定义了两次"
            out.update(defs)
    return out


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
    "focus": ("#2a78d6", "#2a78d6"), "clay": ("#d97757", "#d97757"),
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
    "sidebar-w": "288px", "sidebar-head": "44px", "search-h": "32px", "search-h-desktop": "28px", "row": "32px", "row-desktop": "30px", "row-phone": "44px",
    "row-gap": "2px", "row-radius": "8px", "row-pad": "10px", "row-ctl": "24px", "row-lead": "28px",
    "group-band": "32px", "group-band-phone": "44px", "group-top": "16px", "group-top-desktop": "14px", "group-chevron": "12px", "row-pulse": "6px",
    "tray": "48px", "tray-btn": "32px", "sidebar-float": "8px",
    "menu-pad": "4px", "menu-min-w": "128px", "menu-item": "32px", "menu-item-py": "6px", "menu-item-px": "10px",
    "menu-sep-inset": "10px", "menu-sep-gap": "4px", "menu-sep": "1px",
    # 标题栏（3.9）、右侧会话栏（3.13）、列表页（3.14）
    "titlebar": "32px", "titlebar-btn": "26px",
    "panel-w": "280px", "panel-gap": "10px", "panel-pad": "8px", "panel-head": "26px", "panel-row": "26px", "panel-btn": "20px",
    "list-w": "960px", "list-search-h": "36px", "list-btn": "24px", "list-card-w": "314px", "list-card-h": "108px",
    "ease": "cubic-bezier(0.32, 0.72, 0, 1)", "ease-fast": "cubic-bezier(0.2, 0, 0, 1)",
    "ease-out": "cubic-bezier(0.165, 0.84, 0.44, 1)",
    "dur-1": "60ms", "dur-2": "120ms", "dur-3": "200ms", "dur-4": "300ms", "dur-5": "450ms", "pulse": "1.2s",
    # 对话区（设计文档 3.10–3.12）
    "prose-block": "0.75em", "prose-heading": "1.25em",
    "step-h": "24px", "step-py": "2px", "step-px": "4px", "step-chevron": "12px", "step-row-py": "6px", "step-row-px": "8px",
    "ring": "1px", "composer-h": "44px", "composer-pad": "8px", "composer-btn": "28px",
    "chin-h": "46px", "chin-h-phone": "48px", "chin-out": "8px", "chin-pad": "8px", "chin-btn": "24px", "radius-5": "5px",
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
        # 强调色仍是 CC 橘黄；手机外壳实测值、侧栏宽保留（输入框圆角改指设计系统 14，见 test_cc_structure_points_at_the_code_layer）
        self.assertEqual(cc["--cc-brand"], "#f28c28")
        for name, value in {"--cc-mbtn-bg": "#f6f6f4", "--cc-menu-max-list": "20rem"}.items():
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
        self.assertEqual(cc_block(self.css, ":root")["--cc-sidebar-w"], "var(--code-sidebar-w)")  # 只有一处 288

    def test_only_variables_read_the_code_layer(self):
        # 分层：--code-* 只给 --cc-* 变量取值，样式规则与组件一律用 --cc-*（设计文档 4.1）
        for line in self.css.splitlines():
            if "var(--code-" in line:
                self.assertTrue(line.strip().startswith("--"), line)
        for path in SRC.rglob("*.ts*"):
            self.assertNotIn("--code-", path.read_text(encoding="utf-8"), path)

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
            "--cc-row-hover": "--code-row-hover", "--cc-row-selected": "--code-row-selected",
            "--cc-line": "--code-line", "--cc-line-strong": "--code-line-strong", "--cc-line-stronger": "--code-line-stronger",
            "--cc-link": "--code-link", "--cc-code-ink": "--code-code-ink", "--cc-code-bg": "--code-code-bg",
            "--cc-tooltip-bg": "--code-tooltip-bg", "--cc-tooltip-ink": "--code-tooltip-ink", "--cc-search-bg": "--code-search-bg",
            "--cc-focus-ring": "--code-focus", "--cc-bubble-bg": "--cc-alpha-1",
            "--cc-shadow-card": "--code-shadow-card", "--cc-shadow-float": "--code-shadow-float", "--cc-shadow-pop": "--code-shadow-pop",
            "--cc-shadow-composer": "--code-shadow-composer", "--cc-shadow-search": "--code-shadow-search",
            "--cc-radius-bubble": "--code-radius-10", "--cc-radius-code": "--code-radius-code", "--cc-radius-menu": "--code-radius-menu",
            "--cc-radius-row": "--code-row-radius", "--cc-radius-row-ctl": "--code-radius-6",
            "--cc-body-size": "--code-text-body", "--cc-body-lh": "--code-text-body-lh",
            "--cc-row-h": "--code-row", "--cc-group-h": "--code-group-band", "--cc-group-top": "--code-group-top",
            "--cc-group-font": "--code-text-group", "--cc-group-lh": "--code-text-group-lh", "--cc-row-ctl": "--code-row-ctl",
            "--cc-row-lead": "--code-row-lead", "--cc-icon": "--code-icon", "--cc-control": "--code-control",
            "--cc-search-h": "--code-search-h", "--cc-menu-pad": "--code-menu-pad", "--cc-menu-min-w": "--code-menu-min-w",
            "--cc-menu-item-h": "--code-menu-item", "--cc-menu-item-py": "--code-menu-item-py", "--cc-menu-item-px": "--code-menu-item-px",
            "--cc-menu-sep": "--code-menu-sep", "--cc-menu-sep-gap": "--code-menu-sep-gap", "--cc-menu-sep-inset": "--code-menu-sep-inset",
            "--cc-ease": "--code-ease", "--cc-ease-fast": "--code-ease-fast", "--cc-ease-out": "--code-ease-out",
            "--cc-dur-instant": "--code-dur-1", "--cc-dur-fast": "--code-dur-2", "--cc-dur": "--code-dur-3",
            "--cc-dur-slow": "--code-dur-4", "--cc-dur-pop": "--code-dur-5", "--cc-reveal": "--cc-dur-fast",
            "--cc-radius-composer": "--code-radius-input",
            "--cc-md-block": "--code-prose-block", "--cc-md-heading": "--code-prose-heading",
            "--cc-step-h": "--code-step-h", "--cc-step-py": "--code-step-py", "--cc-step-px": "--code-step-px",
            "--cc-step-chevron": "--code-step-chevron", "--cc-step-row-py": "--code-step-row-py",
            "--cc-step-row-px": "--code-step-row-px", "--cc-ring-w": "--code-ring", "--cc-radius-step": "--code-radius-6",
            "--cc-radius-step-card": "--code-radius-10", "--cc-composer-h": "--code-composer-h",
            "--cc-composer-pad": "--code-composer-pad", "--cc-composer-btn": "--code-composer-btn", "--cc-chin-h": "--code-chin-h",
            "--cc-chin-h-phone": "--code-chin-h-phone", "--cc-chin-out": "--code-chin-out", "--cc-chin-pad": "--code-chin-pad",
            "--cc-chin-btn": "--code-chin-btn", "--cc-chin-font": "--code-text-footnote", "--cc-radius-chin": "--code-radius-5",
        }.items():
            self.assertEqual(cc.get(name), f"var({target})", name)
        # 聚焦：内描 1px 页面色 + 外 1px 蓝描边，没有光晕（用户 2026-10-16「全局这种光晕取消掉，可以用边框，但是不能有光晕」）；
        # 光晕变量浅色、深色都删掉，不留不用的变量
        self.assertEqual(cc["--cc-shadow-focus"], "inset 0 0 0 1px var(--cc-bg-app), 0 0 0 1px var(--cc-focus-ring)")
        self.assertEqual(cc["--cc-shadow-focus-inset"], "inset 0 0 0 var(--cc-focus-w) var(--cc-focus-ring)")
        self.assertNotIn("halo", self.css)
        self.assertEqual(cc["--cc-reveal-rest"], "0")
        self.assertEqual(cc["--cc-sidebar-float"], "0px")
        # 结构色只在 --code-* 里分深浅：--cc-* 深色块不再另给底色、叠色与弹层阴影；页面底色所有变体（含手机）同一个
        dark = [b for b in re.findall(r"^\.dark \{\n(.*?)\n\}", self.css, re.M | re.S) if "--cc-brand:" in b][0]
        for name in ("--cc-bg-app", "--cc-bg-sidebar", "--cc-bg-subtle", "--cc-bg-hover", "--cc-bg-active", "--cc-shadow-pop",
                     "--cc-shadow-composer:", "--cc-shadow-focus"):
            self.assertNotIn(name, dark, name)
        self.assertEqual(self.css.count("--cc-bg-app:"), 1)
        self.assertNotIn("@media (max-width: 767px) { :root", self.css)

    def test_three_variants(self):
        self.assertEqual(variant_block(self.css, "desktop"), {
            "--cc-bg-sidebar": "var(--code-sidebar-desktop)", "--cc-row-h": "var(--code-row-desktop)",
            "--cc-group-top": "var(--code-group-top-desktop)", "--cc-sidebar-float": "var(--code-sidebar-float)",
            # 桌面窗口的侧栏搜索框 28（Claude 桌面版截图约 27.4；第 11 轮按钮排查 G9）
            "--cc-search-h": "var(--code-search-h-desktop)",
        })
        # 手机：菜单项、侧栏 / 抽屉搜索框到点按区下限 44（第 11 轮按钮排查 G1 / G9）；触屏平板（粗指针，非桌面窗口）同样
        self.assertEqual(variant_block(self.css, "phone"), {
            # 手机问候、形象（外壳实测；用途层登记，标准化记录 2.2）
            "--cc-greeting-font": "20px", "--cc-mascot-h": "var(--cc-mascot-h-sm)",
            "--cc-row-h": "var(--code-row-phone)", "--cc-group-h": "var(--code-group-band-phone)", "--cc-reveal-rest": "1",
            "--cc-body-size": "var(--code-text-prose)", "--cc-body-lh": "var(--code-text-prose-lh)",
            "--cc-menu-item-h": "var(--cc-tap)", "--cc-search-h": "var(--cc-tap)",
        })
        # 触屏平板（网页布局 + 粗指针，第 11 轮 D26）的对话顶栏高也在这个不分层的变量块里（写在 components 层敌不过 :root，合并审查）
        self.assertIn('@media (pointer: coarse) {\n  :root:not([data-cc-variant="desktop"]) { --cc-menu-item-h: var(--cc-tap); --cc-search-h: var(--cc-tap); }\n'
                      '  :root[data-cc-variant="web"] { --cc-topbar-h: var(--cc-tap); }\n}', self.css)
        # 桌面窗口：侧栏浮成卡片（离窗口边 8px、卡片阴影）
        self.assertIn(':root[data-cc-variant="desktop"] [data-slot="sidebar-container"] { padding: var(--cc-sidebar-float); '
                      "border-inline-width: 0; }", self.css)
        self.assertIn(':root[data-cc-variant="desktop"] [data-slot="sidebar-inner"] { border-radius: var(--cc-radius-menu); '
                      "box-shadow: var(--cc-shadow-card); }", self.css)
        # 启动时写一次（窄屏分界与手机外壳同一条），先于首帧
        variant = (SRC / "lib/variant.ts").read_text(encoding="utf-8")
        self.assertIn('import { MOBILE_QUERY } from "@/hooks/use-mobile";', variant)
        self.assertIn('import { isDesktopApp } from "@/lib/desktop";', variant)
        self.assertIn("const variant = pickVariant(desktop, narrow.matches);\n    root.dataset.ccVariant = variant;", variant)
        self.assertIn('  apply();\n  narrow.addEventListener("change", apply);', variant)  # 桌面窗口拉窄也跟着换
        # 触屏标记（标准化记录 3.4）：同一次写入、先于首帧；外接鼠标、平板主指针变了跟着换；与原来 @media (pointer: coarse) 同一条查询
        self.assertIn('export const COARSE_QUERY = "(pointer: coarse)";', variant)
        self.assertIn('if (pickTouch(variant, coarse.matches)) root.dataset.ccTouch = "";\n    else delete root.dataset.ccTouch;', variant)
        self.assertIn('  coarse.addEventListener("change", apply);', variant)
        mobile = (SRC / "hooks/use-mobile.ts").read_text(encoding="utf-8")
        self.assertIn("window.matchMedia(MOBILE_QUERY)", mobile)
        self.assertEqual(mobile.count("setIsMobile(mql.matches);"), 2)  # 与变体同一个判断，不用 innerWidth
        self.assertNotIn("window.innerWidth", mobile)
        main = (SRC / "main.tsx").read_text(encoding="utf-8")
        self.assertLess(main.index("installVariant();"), main.index("createRoot("))

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_pick_variant(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { pickTouch, pickVariant } from "@/lib/variant";
assert.equal(pickVariant(true, false), "desktop");
assert.equal(pickVariant(true, true), "phone");  // 桌面窗口拉到分界以下换手机外壳，密度跟着换
assert.equal(pickVariant(false, true), "phone");
assert.equal(pickVariant(false, false), "web");
// 触屏标记：手机变体一定是；粗指针且不是桌面窗口（平板、触屏笔记本上的网页）是；桌面窗口在触屏上也不是；鼠标的网页不是
assert.equal(pickTouch("phone", false), true);
assert.equal(pickTouch("phone", true), true);
assert.equal(pickTouch("web", true), true);
assert.equal(pickTouch("web", false), false);
assert.equal(pickTouch("desktop", true), false);
assert.equal(pickTouch("desktop", false), false);
(globalThis as any).done();
""", stubs={"@/lib/desktop": "export const isDesktopApp = () => false;"})

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_install_variant_writes_the_touch_marker_and_follows_the_pointer(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { MOBILE_QUERY } from "@/hooks/use-mobile";
import { installVariant } from "@/lib/variant";
const g = globalThis as any;
const listeners: Record<string, Array<() => void>> = {};
const state: Record<string, boolean> = { [MOBILE_QUERY]: false, "(pointer: coarse)": true };
g.window.matchMedia = (query: string) => ({
  get matches() { return state[query] ?? false; },
  addEventListener: (_: string, f: () => void) => { (listeners[query] ??= []).push(f); },
});
const root = { dataset: {} as Record<string, string> } as unknown as HTMLElement;
installVariant(root);
assert.equal(root.dataset.ccVariant, "web");
assert.equal(root.dataset.ccTouch, "");             // 平板上的网页：粗指针、不是桌面窗口
state["(pointer: coarse)"] = false;                 // 接上鼠标
for (const f of listeners["(pointer: coarse)"]) f();
assert.equal("ccTouch" in root.dataset, false);
state[MOBILE_QUERY] = true;                         // 窗口拉窄成手机外壳：鼠标也按触屏尺寸
for (const f of listeners[MOBILE_QUERY]) f();
assert.equal(root.dataset.ccVariant, "phone");
assert.equal(root.dataset.ccTouch, "");
g.done();
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
        for part in ("color: var(--cc-ink-3);", "font-size: var(--cc-group-font);", "line-height: var(--cc-group-lh);"):
            self.assertIn(part, label)
        # 可折叠的组标题都是 .cc-group-*（ERP / DMS 的日期组第 11 轮起也用 GroupHeader）：.cc-section-label 只是标题，不再有开关样式；
        # 鼠标在组标题上才变次要色（设计文档 3.4），鼠标进侧栏只淡入箭头
        self.assertNotIn(".cc-section-toggle", css)
        self.assertNotIn('[data-slot="sidebar-inner"]:hover .cc-section-label', css)
        self.assertIn(".cc-group-row:hover .cc-group-toggle, .cc-group-toggle:focus-visible { color: var(--cc-ink-2); }", css)
        self.assertIn('[data-slot="sidebar-inner"]:hover .cc-group-caret', css)
        search = rule(".cc-search")
        for part in ("height: var(--cc-search-h);", "border-radius: var(--cc-radius-row);", "background: var(--cc-search-bg);",
                     "box-shadow: inset 0 0 0 var(--cc-hairline) var(--cc-line), var(--cc-shadow-search);"):
            self.assertIn(part, search)
        # 侧栏搜索框的聚焦是紧凑的 1.5px 里描边（2026-10-16 验收：6px 光晕贴着侧栏边与下面的列表）
        self.assertIn(".cc-search:focus-within { box-shadow: var(--cc-shadow-focus-inset); }", css)
        self.assertIn(".cc-search input::placeholder { color: var(--cc-ink-3); }", css)
        self.assertIn("font-size: var(--cc-text-caption);", rule(".cc-search-hint"))
        row_btn = rule(".cc-row-btn")
        self.assertIn("width: var(--cc-row-ctl); height: var(--cc-row-ctl); border-radius: var(--cc-radius-row-ctl);", row_btn)
        menu = rule(".cc-menu")
        for part in ("min-width: var(--cc-menu-min-w);", "padding: var(--cc-menu-pad);", "background: var(--cc-bg-popover);",
                     "border-radius: var(--cc-radius-menu);", "box-shadow: inset 0 0 0 1px var(--cc-line), var(--cc-shadow-pop);"):
            self.assertIn(part, menu)
        item = rule(".cc-menu-item")
        for part in ("min-height: var(--cc-menu-item-h);", "padding: var(--cc-menu-item-py) var(--cc-menu-item-px);",
                     "border-radius: var(--cc-radius-row);", "font-size: var(--cc-row-font); line-height: var(--cc-row-lh);"):
            self.assertIn(part, item)
        self.assertIn(".cc-menu-item[data-disabled] { opacity: 0.4; pointer-events: none; }", css)
        self.assertIn("font-size: var(--cc-text-footnote);", rule(".cc-menu-value"))
        self.assertIn(".cc-menu-sep { height: 0; margin: var(--cc-menu-sep-gap) var(--cc-menu-sep-inset); "
                      "border-top: var(--cc-menu-sep) solid var(--cc-line); }", css)
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
        for rel in ("components/ui/button.tsx", "components/ui/input.tsx", "components/ui/sheet.tsx",
                    "components/ui/sidebar.tsx"):
            self.assertIn("focus-visible:shadow-focus", read(rel), rel)
        # 弹窗默认的 × 第 11 轮起是图标按钮（.cc-icon-btn 的聚焦就是 --cc-shadow-focus）
        self.assertIn('className="cc-icon-btn cc-tap absolute end-3 top-3"', read("components/ui/dialog.tsx"))
        self.assertIn(".cc-icon-btn:focus-visible { box-shadow: var(--cc-shadow-focus); }", self.css)
        # 聚焦不带光晕（用户 10-16）：整个 CC 前端不再有 ring 光晕写法（ring-[3px]、半透明 ring、ring-offset、focus 时的 ring-2）
        halo = re.compile(r"ring-\[\d|ring-ring|ring-offset|(?:focus|focus-visible|aria-invalid):ring-|ring-[a-z-]+/\d|outline-ring/\d")
        for path in sorted(SRC.rglob("*.ts*")) + [SRC / "globals.css"]:
            self.assertIsNone(halo.search(path.read_text(encoding="utf-8")), path)
        self.assertEqual(read("components/ui/sidebar.tsx").count("focus-visible:shadow-focus"), 5)
        self.assertIn("shadow-pop", read("components/ui/dialog.tsx"))
        self.assertIn("bg-tooltip text-tooltip-ink", read("components/ui/tooltip.tsx"))
        thread = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        self.assertIn('className="cc-reply px-2 wrap-break-word"', thread)
        md = read("components/assistant-ui/markdown-text.tsx")
        # 块之间只用上外边距（设计文档 3.10）：段落、列表、表格、代码、引用、分隔线 0.75em，标题前 1.25em，第一块 0
        self.assertIn('"aui-md-p mt-(--cc-md-block) first:mt-0"', md)
        self.assertNotRegex(md, r"\bm[yb]-\d|last:mb-0")
        self.assertNotIn("aui-md-p leading-relaxed", md)
        for tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.assertRegex(md, r'"aui-md-' + tag + r' mt-\(--cc-md-heading\)[^"]* first:mt-0"', tag)
        for cls in ("aui-md-blockquote", "aui-md-hr", "aui-md-table-wrap", "aui-code-header-root"):
            self.assertRegex(md, cls + r'[^"]* mt-\(--cc-md-block\)[^"]* first:mt-0', cls)
        self.assertEqual(md.count("mt-(--cc-md-block) ps-(--cc-md-list-indent)"), 2)
        self.assertIn('<li className={cn("aui-md-li", className)} {...props} />', md)
        self.assertIn("[&>li]:ps-(--cc-md-li-indent)", md)
        # 表格：外圈 1px 环加圆角（10），里面细线
        self.assertIn("overflow-x-auto rounded-lg border-(length:--cc-ring-w) border-line first:mt-0", md)
        self.assertIn('"aui-md-th min-w-24 border-line border-b px-3 py-1.5 text-start font-medium not-first:border-s', md)
        self.assertIn('"aui-md-td min-w-24 border-line border-b px-3 py-1.5 text-start not-first:border-s', md)
        self.assertNotIn("bg-muted px-3", md)
        self.assertIn('"aui-md-a text-link hover:text-link/80 underline underline-offset-2"', md)
        self.assertIn('"aui-md-inline-code bg-code-bg text-code-ink rounded-code px-1 py-0.5 font-mono text-[0.9em]"', md)
        self.assertIn("--cc-md-list-indent: 28px;", self.css)
        self.assertIn("--cc-md-li-indent: 7px;", self.css)
        self.assertNotIn("--cc-md-list-top", self.css)
        # 回复里的块（正文、步骤、文件卡）之间同样只用上外边距；回复下的操作行悬停淡入、没有悬停的设备常显
        self.assertIn("  .cc-reply > :where(:not(:first-child)) { margin-top: var(--cc-md-block); }", self.css)
        self.assertIn("  .cc-reply-actions { opacity: var(--cc-reveal-rest); transition: opacity var(--cc-reveal) var(--cc-ease); }",
                      self.css)
        self.assertIn('  [data-role="assistant"]:hover .cc-reply-actions, .cc-reply-actions:focus-within { opacity: 1; }', self.css)
        self.assertIn("  @media (hover: none) { .cc-reply-actions { opacity: 1; } }", self.css)
        bar = thread.split("const AssistantActionBar: FC = () => {", 1)[1].split("\n};", 1)[0]
        self.assertIn('autohide="never"', bar)
        self.assertIn("hideWhenRunning", bar)
        self.assertIn("cc-reply-actions", bar)
        # 栏宽 768：内容区左右各 1rem，正文与输入框正好 --thread-max-width（= --cc-thread-w 48rem）
        self.assertIn("max-w-[calc(var(--thread-max-width)+2rem)] flex-1 flex-col px-4 pt-4", thread)
        self.assertIn("--cc-thread-w: 48rem;", self.css)

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
        self.assertIn('APP_INK = {"light": "#0b0b0b", "dark": "#f0efec"}', tomo)  # 同 --code-ink-1 浅 / 深
        vite = (ROOT / "frontend/assistant/vite.config.ts").read_text(encoding="utf-8")
        self.assertIn('background_color: "#fcfcfb",', vite)
        # 深色（测试窗口第 2 轮：手机深色下安卓系统导航条仍是白的）：页面报两种深浅；离线页同样；manifest 给深色的一份；
        # 设置里固定了浅 / 深色时运行时改 color-scheme，theme-color 跟着当前底色
        self.assertIn('<meta name="color-scheme" content="light dark" />', html)
        self.assertLess(html.index('name="color-scheme"'), html.index("<style>"))
        for meta in ('<meta name="color-scheme" content="light dark">',
                     '<meta name="theme-color" content="#fcfcfb" media="(prefers-color-scheme: light)">',
                     '<meta name="theme-color" content="#151515" media="(prefers-color-scheme: dark)">'):
            self.assertIn(meta, api)
        self.assertIn('user_preferences: { color_scheme_dark: { background_color: "#151515", theme_color: "#151515" } },', vite)
        main = (SRC / "main.tsx").read_text(encoding="utf-8")
        apply = main.split("const applyScheme = () => {", 1)[1].split("\n};", 1)[0]
        self.assertIn("colorScheme(theme);\n  themeColor(true);", apply)
        self.assertIn("const value = colorSchemeOf(theme);", main)
        self.assertIn('window.addEventListener("cc-theme", applyScheme);', main)
        appearance = (SRC / "lib/appearance.ts").read_text(encoding="utf-8")
        self.assertIn('return theme === "system" ? "light dark" : theme;', appearance)
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


def theme_block(css):
    return css.split("@theme inline {", 1)[1].split("\n}", 1)[0]


class StandardLayerTests(unittest.TestCase):
    """标准层（标准化记录第 2 节，B1）：刻度层、@theme 接的字阶与字重、cn() 认识这些名字。"""

    def setUp(self):
        self.css = (SRC / "globals.css").read_text(encoding="utf-8")

    def test_type_scale_and_weights_are_wired_into_the_theme(self):
        theme = theme_block(self.css)
        for name, (size, lh) in {
            "caption": ("--cc-text-caption", "--cc-text-caption-lh"), "footnote": ("--cc-text-footnote", "--cc-text-footnote-lh"),
            "group": ("--cc-group-font", "--cc-group-lh"), "panel": ("--cc-panel-font", "--cc-panel-lh"),
            "body": ("--cc-row-font", "--cc-row-lh"), "subhead": ("--cc-text-subhead", "--cc-text-subhead-lh"),
            "prose": ("--cc-text-prose", "--cc-text-prose-lh"), "title": ("--cc-text-title", "--cc-text-title-lh"),
            "page": ("--cc-text-page", "--cc-text-page-lh"),
        }.items():
            self.assertIn(f"  --text-{name}: var({size});\n  --text-{name}--line-height: var({lh});", theme, name)
        self.assertIn("--text-page--font-weight: var(--cc-text-page-weight);", theme)
        cc = cc_block(self.css, ":root")
        # 全部引用 Claude 设计系统实测的字阶（设计文档 2.2）
        for name, target in {"--cc-text-subhead": "--code-text-heading", "--cc-text-prose": "--code-text-prose",
                             "--cc-text-title": "--code-text-title", "--cc-text-caption": "--code-text-caption",
                             "--cc-text-footnote": "--code-text-footnote"}.items():
            self.assertEqual(cc[name], f"var({target})", name)
        for name, weight in {"normal": "regular", "medium": "medium", "semibold": "semibold", "bold": "bold"}.items():
            self.assertIn(f"  --font-weight-{name}: var(--cc-weight-{weight});", theme)
            self.assertEqual(cc[f"--cc-weight-{weight}"], f"var(--ccs-font-weight-{name})")
        self.assertIn("--breakpoint-md: 768px;", theme)

    def test_cn_knows_the_theme_names(self):
        # 2.5：tailwind-merge 要登记 @theme 接过的字阶与字重名，与 globals.css 两边一致
        theme = theme_block(self.css)
        sizes = sorted(set(re.findall(r"^  --text-([a-z]+):", theme, re.M)) - {"xs", "sm", "base", "lg", "xl"} - {"2xl", "3xl", "4xl"})
        weights = sorted(re.findall(r"^  --font-weight-([a-z]+):", theme, re.M))
        utils = (SRC / "lib/utils.ts").read_text(encoding="utf-8")
        listed = lambda name: sorted(re.findall(r'"([a-z]+)"', utils.split(f"export const {name} = [", 1)[1].split("]", 1)[0]))
        self.assertEqual(listed("TEXT_SIZES"), sizes)
        self.assertEqual(listed("FONT_WEIGHTS"), weights)

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_cn_merges_type_scale_names_as_sizes_not_colors(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { cn } from "@/lib/utils";
// 字号与字色各留一个（原来 text-caption 被当成颜色，text-ink-3 就丢了）
assert.equal(cn("text-ink-3", "text-caption"), "text-ink-3 text-caption");
assert.equal(cn("text-caption", "text-ink-3"), "text-caption text-ink-3");
// 同是字号：后一个赢
assert.equal(cn("text-sm", "text-caption"), "text-caption");
assert.equal(cn("text-body", "text-page"), "text-page");
assert.equal(cn("text-prose text-ink", "text-(length:--cc-body-size)"), "text-ink text-(length:--cc-body-size)");
// 字重
assert.equal(cn("font-medium", "font-semibold"), "font-semibold");
assert.equal(cn("font-medium text-caption", "text-ink-2"), "font-medium text-caption text-ink-2");
(globalThis as any).done();
""")

    def test_the_scale_layer_has_one_definition_per_value(self):
        # 2.4 第 1 / 6 条：刻度只有一份，刻度层这一块里不出现两个定义完全相同的变量
        block = self.css.split("/* ── 刻度层", 1)[1].split("\n}", 1)[0]
        defs = re.findall(r"^  (--cc-[\w-]+):\s*([^;]+);", block, re.M)
        self.assertGreater(len(defs), 60)
        seen = {}
        for name, value in defs:
            self.assertNotIn(value.strip(), seen, f"{name} 与 {seen.get(value.strip())} 定义相同")
            seen[value.strip()] = name
        names = [n for n, _ in defs]
        for prefix in ("--cc-space-", "--cc-control", "--cc-icon", "--cc-z-", "--cc-dur", "--cc-weight-", "--cc-radius-"):
            self.assertTrue(any(n.startswith(prefix) for n in names), prefix)
        # 间距刻度按像素命名，值就是名字（设计文档 2.3 的 --code-gap / pad）
        spacing = {n: v for n, v in defs if n.startswith("--cc-space-")}
        self.assertEqual(list(spacing), [f"--cc-space-{n}" for n in (2, 4, 6, 8, 12, 16, 24, 28, 40)])
        code = code_block(self.css, ":root")
        for name, value in spacing.items():
            target = re.fullmatch(r"var\((--code-[\w-]+)\)", value).group(1)
            self.assertEqual(code[target], name.rsplit("-", 1)[1] + "px", name)


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
        self.assertIn("{input && <Clipped text={input} />}", steps)
        # 能点的：内容展开「… N 行」、步骤行、步骤组摘要（允许卡的问话照 Claude 不是按钮，第 10 轮起不再算点按区）
        self.assertEqual(steps.count("cc-tap"), 3)
        thread = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        pending = thread.split("const PendingSendBubble: FC = () => {", 1)[1].split("\n};", 1)[0]
        self.assertNotIn("animate-spin", pending)
        self.assertIn("<Skeleton", pending)
        files = thread.split("const MessageFiles: FC = () => {", 1)[1].split("\n};", 1)[0]
        self.assertIn('className="cc-icon-btn cc-tap"', files)
        # 提醒卡的「打开通知」在卡片里、底部靠右，用卡片按钮（同允许卡，用户 10-17）；没打开的原因在按钮上方一行
        notice = thread.split("const NoticeMessage: FC = () => {", 1)[1].split("\n};", 1)[0]
        self.assertNotIn("cc-btn ", notice)
        card = notice[notice.index('role="note"'):]
        foot = card[card.index('<div className="cc-ask-foot">'):]
        self.assertLess(foot.index("push.failure"), foot.index('<div className="cc-card-actions">'))
        self.assertIn('<button type="button" className="cc-card-btn" onClick={() => void push.enable()}>', foot)
        self.assertLess(card.index("{noticeTime(createdAt)}"), card.index('<div className="cc-ask-foot">'))
        css = (SRC / "globals.css").read_text(encoding="utf-8")
        self.assertIn("@media (pointer: coarse) { .cc-tap { min-height: var(--cc-tap); min-width: var(--cc-tap); } }", css)
        self.assertIn("--cc-tap: 2.75rem;", css)

    def test_steps_follow_claude_code(self):
        # 设计文档 3.11：连续工具调用收成一行摘要（进行中仍是「正在运行」，做完按类计数、带失败数）；点开是细框卡片，
        # 一步一行、只写一句说明（没有图标），失败的行尾红字（沿用「失败」词条）；再点开才看命令与输出
        steps = (SRC / "components/tool-uis/steps.tsx").read_text(encoding="utf-8")
        group = steps[steps.index("export const StepsGroup"):]
        self.assertIn("const title = groupLive(group.status)\n    ? steps.running\n"
                      "    : summaryText(summarize(tools.map(toStepPart), labels.done), steps.summary, labels.done, { asks: labels.ask, failed: steps.failed });", group)
        # 第 8 轮：点了「拒绝」的一步没做，摘要与行都写卡片上的问话（去掉问号）加「已拒绝」，不写做完的说法
        self.assertIn("return { toolName: tool.toolName, failed: stepFailed(tool), denied: stepDenied(tool), undone: stepUndone(tool) };", steps)
        self.assertIn('<button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="cc-steps-summary cc-tap">',
                      group)
        # 一张卡片、一步一行：组给卡片，组里的每一步不再各包一层（测试窗口 S2：外卡套内卡）
        self.assertIn('<div className="cc-steps-card">\n          <InStepsGroup.Provider value={true}>{children}</InStepsGroup.Provider>', group)
        self.assertIn('if (inGroup) return <>{children}</>;', steps)
        self.assertIn('return <div className="cc-steps-card">{children}</div>;', steps)
        # 允许 / 拒绝卡不进组（等回答的要看得见，点完的照 Claude 留在原处），也放进同样带环的卡片
        thread = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        self.assertIn('part.type === "tool-call" && approvalCard(part as unknown as CardPart, labels) ? [] : groupByBase(part, context)', thread)
        self.assertIn('<LoneStep part={part as unknown as CardPart}>\n                        <ToolFallbackComponent {...part} />', thread)
        self.assertNotIn('<div className="cc-steps-card">', thread)
        self.assertNotIn("steps.steps(", steps)
        row = steps[steps.index("const PlainStep"):steps.index("/** 卡片上的问话")]
        self.assertIn("const errored = stepFailed({ result, isError, status });", row)
        self.assertIn('{errored && <span className="cc-step-failed">{steps.failed}</span>}', row)
        self.assertIn('className="cc-step-row cc-tap"', row)
        # 同 Claude Code 实测：行字淡色、一行省略；能展开的行尾 12px 箭头（展开转 90°），失败的「失败」在箭头前；没详情的行没有箭头
        self.assertIn('<Marquee className="min-w-0 flex-1 truncate">', row)   # 放不下时悬停滚动（components/ui/marquee.tsx）
        self.assertIn('{errored && <span className="cc-step-failed">{steps.failed}</span>}\n'
                      '        {expandable && <ChevronRightIcon aria-hidden />}\n      </button>', row)
        # 允许 / 拒绝卡（拒绝了、允许了却没存上的也是）不在这里：卡片写问话、点完一行所选那一项（用户 10-17），步骤行不再写「已拒绝」
        self.assertIn("return card ? <AskStep {...props} card={card} labels={labels} /> : <PlainStep {...props} labels={labels} />;", steps)
        self.assertNotIn("cc-step-denied", steps)
        # 出卡片前就核出要拒绝的那一步（没做成）写问话，其余写步骤名（第 10 轮审查，undoneText）
        self.assertIn("{undone ?? step.verb}\n          {step.target", row)
        # 本站工具的行后面带「 · 对象」（单号、账套文件夹名…，lib/step-subject.ts），同一个工具连调几次也分得清；
        # 只在展开的行里用，摘要行（按类计数）与工作状态行不用
        self.assertIn("const subject = step.own ? stepSubject(args as Args, output?.text, known) : \"\";", row)
        self.assertIn('{subject && <span className="cc-step-target">{` · ${subject}`}</span>}', row)
        self.assertEqual(steps.count("stepSubject("), 1)
        # 记录名字从整段对话的结果里认（推送那一步的结果里没有单号，测试窗口第 2 轮），不只这条回复
        self.assertIn("useAuiState((s) => knownRecords(s.thread.messages as readonly MessageLike[]))", steps)
        # 各步共用一份：同一个消息数组只算一次，结果都没变（流式只来文字）交回同一个 Map，不随每个字重画
        self.assertIn("knownByMessages.set(messages, lastKnown.known);", steps)
        self.assertIn("results.every((result, i) => result === lastKnown.results[i])", steps)
        self.assertNotIn("s.message.parts);\n  return useMemo", steps)
        # 点开的本站工具参数与结果只显示给人看的（lib/step-detail.ts）；内置工具照原样
        self.assertIn("input: detailArgs(args), showOutput: true, own: true", steps)
        self.assertIn("(outputText ? (step.own ? detailOutput(outputText) : outputText) : \"\")", steps)
        self.assertNotIn("JSON.stringify(args", steps)
        self.assertNotIn("subject", group)
        self.assertNotRegex(steps, r"\bicon\b|Icon className")
        self.assertIn('import { ChevronRightIcon } from "lucide-react";', steps)
        css = (SRC / "globals.css").read_text(encoding="utf-8")
        rule = lambda selector: css.split("\n  " + selector + " {", 1)[1].split("}", 1)[0]
        summary = rule(".cc-steps-summary")
        for part in ("min-height: var(--cc-step-h);", "padding: var(--cc-step-py) var(--cc-step-px);",
                     "border-radius: var(--cc-radius-step); color: var(--cc-ink-3);", "font-size: var(--cc-row-font);"):
            self.assertIn(part, summary)
        self.assertIn('  .cc-steps-summary:hover, .cc-steps-summary[aria-expanded="true"] { color: var(--cc-ink-2); }', css)
        self.assertIn("border: var(--cc-ring-w) solid var(--cc-line); border-radius: var(--cc-radius-step-card);", rule(".cc-steps-card"))
        self.assertIn("  .cc-steps-card > :where(:not(:first-child)) { border-top: var(--cc-ring-w) solid var(--cc-line); }", css)
        self.assertIn("padding: var(--cc-step-row-py) var(--cc-step-row-px);", rule(".cc-step-row"))
        self.assertIn("color: var(--cc-ink-3); font-size: var(--cc-row-font); line-height: var(--cc-row-lh);", rule(".cc-step-row"))
        self.assertIn("width: var(--cc-step-chevron); height: var(--cc-step-chevron); flex-shrink: 0;", rule(".cc-step-row > svg"))
        self.assertIn('  .cc-step-row[aria-expanded="true"] > svg { transform: rotate(90deg); }', css)
        self.assertIn("  .cc-step-failed { flex-shrink: 0; color: var(--cc-danger); }", css)
        self.assertNotIn(".cc-step-denied", css)
        self.assertIn("font-family: var(--cc-font-mono);", rule(".cc-step-detail"))

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_step_summary_counts_by_kind_with_failures(self):
        script = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { stepFailed, stepKind, summarize, summaryText } from "@/lib/step-summary";
import { steps } from "@/i18n";
const done = { pearnly_save_draft: "__SAVED__" };
// 分类：内置工具按类，本站工具（有做完的说法）各一类，其余「工具」
assert.equal(stepKind("bash", done), "command");
assert.equal(stepKind("write", done), "edit");
assert.equal(stepKind("edit", done), "edit");
for (const name of ["glob", "grep", "web_search"]) assert.equal(stepKind(name, done), "search");
assert.equal(stepKind("web_fetch", done), "fetch");
assert.equal(stepKind("pearnly_save_draft", done), "own:pearnly_save_draft");
assert.equal(stepKind("mcp__x__y", done), "tool");
// 工具名撞上原型上的键（constructor、toString）也只算「工具」，摘要照常出字
for (const name of ["constructor", "toString", "__proto__"]) assert.equal(stepKind(name, done), "tool");
assert.equal(summaryText(summarize([{ toolName: "constructor", failed: false }], done), steps.summary, done), steps.summary.tool(1));
// 失败：结果标了出错、步骤标了出错、没做完就停了
assert.equal(stepFailed({ result: { text: "boom", isError: true } }), true);
assert.equal(stepFailed({ result: { text: "ok", isError: false } }), false);
assert.equal(stepFailed({ result: "plain" }), false);
assert.equal(stepFailed({ isError: true }), true);
assert.equal(stepFailed({ status: { type: "incomplete" } }), true);
assert.equal(stepFailed({ status: { type: "complete" } }), false);
// 合并、按首次出现的顺序，失败数跟在那一类后面
const parts = [
  { toolName: "bash", failed: false }, { toolName: "read", failed: false }, { toolName: "bash", failed: true },
  { toolName: "pearnly_save_draft", failed: false }, { toolName: "pearnly_save_draft", failed: false },
  { toolName: "mcp__x__y", failed: true }, { toolName: "grep", failed: false }, { toolName: "web_search", failed: false },
  { toolName: "web_fetch", failed: true }, { toolName: "edit", failed: false }, { toolName: "write", failed: false },
];
const entries = summarize(parts, done);
assert.deepEqual(entries.map((e) => [e.kind, e.count, e.failed]), [
  ["command", 2, 1], ["read", 1, 0], ["own:pearnly_save_draft", 2, 0], ["tool", 1, 1], ["search", 2, 0], ["fetch", 1, 1], ["edit", 2, 0],
]);
assert.equal(summaryText(entries, steps.summary, done), __WANT__);
assert.equal(summaryText(summarize([{ toolName: "pearnly_save_draft", failed: true }], done), steps.summary, done), __ONE__);
(globalThis as any).done();
"""
        cases = {
            "en": ("Ran 2 commands (1 failed), Read 1 file, __SAVED__ ×2, Used 1 tool (1 failed), Searched 2 times, "
                   "Fetched 1 page (1 failed), Edited 2 files", "__SAVED__ (1 failed)"),
            # 中文连接符照 Claude Code 实测（「已获取 6 个页面（6 个失败）, 已浏览 网页」）
            "zh": ("已运行 2 个命令（1 个失败）, 已读取 1 个文件, __SAVED__ ×2, 已使用 1 个工具（1 个失败）, 已搜索 2 次, "
                   "已获取 1 个页面（1 个失败）, 已编辑 2 个文件", "__SAVED__（1 个失败）"),
            "th": ("รันคำสั่ง 2 รายการ (ไม่สำเร็จ 1), อ่าน 1 ไฟล์, __SAVED__ ×2, ใช้เครื่องมือ 1 รายการ (ไม่สำเร็จ 1), ค้นหา 2 ครั้ง, "
                   "เปิด 1 หน้า (ไม่สำเร็จ 1), แก้ไข 2 ไฟล์", "__SAVED__ (ไม่สำเร็จ 1)"),
        }
        for lang, (want, one) in cases.items():
            run_ts(script.replace("__WANT__", repr(want)).replace("__ONE__", repr(one)), lang=lang)

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_a_denied_step_reads_as_not_done(self):
        # 第 8 轮：规则卡点了「拒绝」，步骤仍写「记下了公司选择」（3 次）。拒绝的一步写卡片上的问话（去掉问号）加「已拒绝」；
        # 重开对话时没有本页的点击记录，按结果里的 status: denied 认（pearnly_core toolrun.decide）。结果说没存上的算失败
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { askStem, stepDenied, stepFailed, stepUndone, summarize, summaryText } from "@/lib/step-summary";
import { steps, t } from "@/i18n";
const done = { cowork_company_choice_save: "记下了公司选择" };
const asks = { cowork_company_choice_save: "保存为公司选择？" };
const denial = { word: "已拒绝", asks, failed: steps.failed };
assert.equal(askStem("保存为公司选择？"), "保存为公司选择");
assert.equal(askStem("Save this choice for the company? "), "Save this choice for the company");
// 本页点了拒绝；重开后按结果认；允许了、还在等的不算
assert.equal(stepDenied({ approval: { approved: false } }), true);
assert.equal(stepDenied({ result: { text: JSON.stringify({ status: "denied", message: "The user did not allow this change." }), isError: false } }), true);
assert.equal(stepDenied({ approval: { approved: true }, result: { text: "{\"status\": \"saved\"}", isError: false } }), false);
assert.equal(stepDenied({ approval: {} }), false);
assert.equal(stepDenied({ result: { text: "plain", isError: false } }), false);
// 第 8 轮以前的旧对话：拒绝交回的是那句原文（服务端不翻译），重开时同样认出
assert.equal(stepDenied({ result: { text: "The user did not allow this change. Do not try it again unless the user asks.", isError: false } }), true);
// 摘要：拒绝的写问话 + 已拒绝，存上的照旧写做完的说法；几次时 ×n
const parts = [{ toolName: "cowork_company_choice_save", failed: false, denied: true }];
assert.equal(summaryText(summarize(parts, done), steps.summary, done, denial), "保存为公司选择 · 已拒绝");
assert.equal(summaryText(summarize([...parts, ...parts], done), steps.summary, done, denial), "保存为公司选择 ×2 · 已拒绝");
assert.equal(summaryText(summarize([{ toolName: "cowork_company_choice_save", failed: false }], done), steps.summary, done, denial), "记下了公司选择");
// 没给问话（不是要允许的工具）：用做完的说法
assert.equal(summaryText(summarize([{ toolName: "x_tool", failed: false, denied: true }], { x_tool: "做了" }), steps.summary, { x_tool: "做了" }, denial), "做了 · 已拒绝");
// 结果写明没存上（对象对不上回 not_saved、公司认不出回 failed）：算失败；存上了不算
assert.equal(stepFailed({ result: { text: JSON.stringify({ status: "not_saved", reason: "x" }), isError: false } }), true);
assert.equal(stepFailed({ result: { text: JSON.stringify({ status: "failed", reason: "x" }), isError: false } }), true);
assert.equal(stepFailed({ result: { text: JSON.stringify({ status: "saved" }), isError: false } }), false);
assert.equal(stepFailed({ result: { text: "{not json", isError: false } }), false);
// 第 8 轮审查：结果写明没存上的（点了允许、二次核对没过）不写做完的说法：问话 + 失败；几次 ×n；没有问话的工具照做完的说法加失败数
const notSaved = { text: JSON.stringify({ status: "not_saved", reason: "x" }), isError: false };
assert.equal(stepUndone({ result: notSaved }), true);
assert.equal(stepUndone({ result: { text: JSON.stringify({ status: "saved" }), isError: false } }), false);
const undone = { toolName: "cowork_company_choice_save", failed: true, undone: true };
assert.equal(summaryText(summarize([undone], done), steps.summary, done, denial), "保存为公司选择 · 失败");
assert.equal(summaryText(summarize([undone, undone], done), steps.summary, done, denial), "保存为公司选择 ×2 · 失败");
assert.equal(summaryText(summarize([{ toolName: "x_tool", failed: true, undone: true }], { x_tool: "做了" }), steps.summary, { x_tool: "做了" }, denial), "做了（1 个失败）");
assert.equal(summaryText(summarize([undone, ...parts], done), steps.summary, done, denial), "保存为公司选择 · 失败, 保存为公司选择 · 已拒绝");
// 问话去掉疑问的写法：中文「要…吗」，泰文句末 ไหม / หรือไม่，日文句末「か」（第 8 轮审查：只去问号时读着仍在问）
assert.equal(askStem("要删除这些定时任务吗？"), "删除这些定时任务");
assert.equal(askStem("บันทึกตัวเลือกนี้ให้บริษัทไหม"), "บันทึกตัวเลือกนี้ให้บริษัท");
assert.equal(askStem("ลบงานตามกำหนดเวลาเหล่านี้หรือไม่"), "ลบงานตามกำหนดเวลาเหล่านี้");
assert.equal(askStem("この選択を会社に保存しますか？"), "この選択を会社に保存します");
// 各工具声明的问话（四种语言，服务端词条表）去掉后都不再是问句
const declared = __ASKS__ as string[];
assert.ok(declared.length >= 40, String(declared.length));
for (const ask of declared) {
  const stem = askStem(ask);
  assert.ok(stem.length > 0, ask);
  assert.doesNotMatch(stem, /[?？吗]$|ไหม$|หรือไม่$|か$/u, ask);
}
(globalThis as any).done();
""".replace("__ASKS__", json.dumps(declared_asks(), ensure_ascii=False)), lang="zh")

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_step_subject_names_the_record_not_its_internal_name(self):
        # 结果样子照 apps/pearnly_cowork/pearnly_cowork/assistant/tools.py（list_documents、push_choices、document_detail、
        # recognize_attachments、folder_tasks、vat_draft、companion_status）
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { SUBJECT_MAX, looksInternal, recordLabels, stepSubject } from "@/lib/step-subject";
const j = (value: unknown) => JSON.stringify(value);
const listed = j({ documents: [
  { name: "8f2a1c9d3e", invoice_no: "IV6810-00012", seller_name: "Makro", push_account_set: "C:\\ExpressI\\T9NIEXCP",
    items: [{ name: "Rice 5kg", qty: 1 }] },
  { name: "1b2c3d4e5f", invoice_no: "", seller_name: "Lotus", filename: "scan.pdf" },
], count: 2, total: 2 });
const known = new Map(recordLabels(JSON.parse(listed)));
assert.deepEqual([...known], [["8f2a1c9d3e", "IV6810-00012"], ["1b2c3d4e5f", "Lotus"]]);  // 商品行的 name 不算记录
// 推送前要选的项 ×3：各写各的单号；同一回复里没认出的落到账套文件夹名
const choices = (doc: string) => j({ document: doc, connection: "cnx-office", account_label: "Siam Trading Co., Ltd.",
  account_set: "C:\\ExpressI\\T9NIEXCP\\", needed: ["account"], options: {} });
assert.equal(stepSubject({ document: "8f2a1c9d3e" }, choices("8f2a1c9d3e"), known), "IV6810-00012");
assert.equal(stepSubject({ document: "1b2c3d4e5f" }, choices("1b2c3d4e5f"), known), "Lotus");
assert.equal(stepSubject({ document: "9a8b7c6d5e" }, choices("9a8b7c6d5e"), known), "T9NIEXCP");
// 详情：自己的结果里就有单号（识别字段叫 invoice_number）
assert.equal(stepSubject({ document: "4d5e6f7a8b" }, j({ document: "4d5e6f7a8b", invoice_number: "ABB6909-00007",
  seller_name: "Big C" })), "ABB6909-00007");
// 识别附件：附件 → 识别出的票，第一张的单号 +其余张数；还在跑（没结果）时用参数里的账套
const recognized = j({ results: [
  { file: "a1b2c3d4e5", batch: "b1c2d3e4f5", state: "saved", documents: [{ name: "c1d2e3f4a5", invoice_no: "INV-001" }] },
  { file: "a9b8c7d6e5", batch: "b9c8d7e6f5", state: "saved", documents: [{ name: "c9d8e7f6a5", invoice_no: "INV-002" }] },
  { file: "aa11bb22cc", batch: "bb11cc22dd", state: "failed", reason: "failed" },
] });
const recognize = { files: ["a1b2c3d4e5", "a9b8c7d6e5", "aa11bb22cc"], account_set_key: "S:\\Express\\T9NIEXCP" };
assert.equal(stepSubject(recognize, recognized), "INV-001 +2");
assert.equal(stepSubject(recognize, undefined), "T9NIEXCP");
// 定时任务：前面列任务的结果认出标题
const tasks = new Map(recordLabels({ tasks: [{ name: "3b9c1d2e4f", title: "Monthly purchases T9", enabled: 1 }] }));
assert.equal(stepSubject({ task: "3b9c1d2e4f" }, j({ status: "queued" }), tasks), "Monthly purchases T9");
// 人给的字：月份、搜索词；没点账套时用结果里的公司范围
assert.equal(stepSubject({ period: "2026-09", account_set_key: "C:\\ExpressI\\T9NIEXCP" }, "{}"), "2026-09");
assert.equal(stepSubject({ search: "  Makro  " }, listed), "Makro");
assert.equal(stepSubject({}, j({ documents: [], count: 0, scope: { company: "Siam Trading", company_id: "a1b2c3d4e5" } })),
  "Siam Trading");
// 认不出就不写：连接状态、只有内部名字、结果不是 JSON
assert.equal(stepSubject({}, j({ connections: [{ connection: "cnx-office", label: "Office PC", online: true }] })), "");
assert.equal(stepSubject({ document: "8f2a1c9d3e" }, j({ document: "8f2a1c9d3e", status: "not_found" })), "");
assert.equal(stepSubject({ document: "CWH-2026-00001" }, j({ name: "CWH-2026-00001", title: "CWEC-0003" })), "");
assert.equal(stepSubject({ documents: ["8f2a1c9d3e"] }, "not json"), "");
assert.equal(stepSubject(undefined, undefined), "");
assert.equal(looksInternal("IV6810-00012"), false);
assert.equal(looksInternal("2026-09"), false);
assert.equal(looksInternal("0012345678"), false);
assert.equal(looksInternal("8f2a1c9d3e"), true);
// 长的截到 SUBJECT_MAX 个字，「+其余」留着
const long = "Siam Makro Public Company Limited (Head Office) Branch 00012";
const longKnown = new Map([["8f2a1c9d3e", long], ["1b2c3d4e5f", "x"]]);
const one = stepSubject({ document: "8f2a1c9d3e" }, undefined, longKnown);
assert.equal([...one].length, SUBJECT_MAX);
assert.ok(one.endsWith("…") && long.startsWith(one.slice(0, -1)));
const many = stepSubject({ documents: ["8f2a1c9d3e", "1b2c3d4e5f"] }, undefined, longKnown);
assert.equal([...many].length, SUBJECT_MAX);
assert.ok(many.endsWith("… +1"));
(globalThis as any).done();
""")

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_push_step_names_the_invoice_and_folders_keep_their_case(self):
        # 测试窗口第 2 轮：推送那一行写成「推送了单据 · t9niexcp」——推送的结果里没有单号（单号在前几条回复列过 / 识别过的
        # 结果里），参数里的账套键是归一过的小写。要写单号；只能写账套时照原写法（T9NIEXCP），不显示小写键。
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { folderSpellings, knownEntries, stepSubject } from "@/lib/step-subject";
const j = (value: unknown) => JSON.stringify(value);
// 前一条回复的列表（整段对话合起来的 known）
const listed = { documents: [{ name: "CWH-2026-00031", invoice_no: "IV6810-00012", seller_name: "Makro",
  push_account_set: "C:\\ExpressI\\T9NIEXCP" }, { name: "CWH-2026-00032", invoice_no: "IV6810-00013" }] };
const known = new Map(knownEntries(listed));
assert.deepEqual(folderSpellings(listed), [["folder:t9niexcp", "T9NIEXCP"]]);
const pushed = j({ results: [{ document: "CWH-2026-00031", status: "entered", docnum: "HP691015-001",
  written: { payment: "cash", payment_from: "remembered", debit_account: "53-05-02-00" } }] });
const args = { documents: ["CWH-2026-00031"], account_set_key: "c:\\expressi\\t9niexcp" };
assert.equal(stepSubject(args, pushed, known), "IV6810-00012");
assert.equal(stepSubject({ ...args, documents: ["CWH-2026-00031", "CWH-2026-00032"] }, pushed, known), "IV6810-00012 +1");
// 单号认不出时落到账套：小写键按见过的原写法还原；没见过的全小写按 Express 的写法大写；本来带大写的照原样
assert.equal(stepSubject({ ...args, documents: ["CWH-2026-00099"] }, pushed, known), "T9NIEXCP");
assert.equal(stepSubject({ ...args, documents: ["CWH-2026-00099"] }, pushed), "T9NIEXCP");
assert.equal(stepSubject({ account_set_key: "s:\\2569\\exp69\\69sincer" }, "{}"), "69SINCER");
assert.equal(stepSubject({ account_set_key: "S:\\Data\\Demo02" }, "{}"), "Demo02");
const seen = new Map(knownEntries({ account_sets: [{ path: "S:\\Data\\Demo02" }] }));
assert.equal(stepSubject({ account_set_key: "s:\\data\\demo02" }, "{}", seen), "Demo02");
// 结果里有原写法的账套（push_choices 的 account_set）先用它
assert.equal(stepSubject({ account_set_key: "c:\\expressi\\t9niexcp" },
  j({ account_set: "C:\\ExpressI\\T9NIEXCP", needed: [] })), "T9NIEXCP");
(globalThis as any).done();
""")

    @unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
    def test_step_details_show_only_what_people_read(self):
        # 测试窗口第 2 轮：点开的步骤里是原样 JSON，满是连接 CWEC-…、file_…、记录名 CWH-…、c:\pearnlyexpress\… 本地路径。
        # 只留给人看的（单号、名称、金额、日期、状态、说明）；人给的字（搜索词、月份）照留；不是 JSON 的原样
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { detailArgs, detailOutput, internalValue, sanitizeDetail } from "@/lib/step-detail";
for (const value of ["CWEC-0003", "CWH-2026-00031", "file_011CNha8iCJcU1wXNR6q4V8w", "sesn_01ABCDEFGH",
  "c:\\pearnlyexpress\\70exp\\t9niexcp", "C:/ExpressI/T9NIEXCP", "\\\\accserver\\s\\2569", "/Users/skin/x.pdf",
  "8f2a1c9d3e", "3f2a1c9d-4b5e-4c6d-8e7f-0a1b2c3d4e5f", "3F2A1C9D-4B5E-4C6D-8E7F-0A1B2C3D4E5F",
  "9b74c9897bac770ffc029102a200c5de2f1f6d5c7e3a1b0c9d8e7f6a5b4c3d2e"]) assert.equal(internalValue(value), true, value);
for (const value of ["IV6810-00012", "2026-09", "Makro", "1070.00", "entered", "HP691015-001", "สาขา 00012", "0105562012340",
  "2026-09/inv-01.pdf", "Rice 5kg", "", "agent_failed", "file_unreadable", "session_expiry", "user_permissions"])
  assert.equal(internalValue(value), false, value);
// 原因码跟在结果里照留
assert.deepEqual(sanitizeDetail({ status: "failed", reason: "agent_failed", count: "3f2a1c9d-4b5e-4c6d-8e7f-0a1b2c3d4e5f" }),
  { status: "failed", reason: "agent_failed" });
// 推送的参数：连接、账套键、记录名都不显示 → 没有给人看的就不显示参数
assert.equal(detailArgs({ documents: ["CWH-2026-00031"], connection: "cnx-office",
  account_set_key: "c:\\expressi\\t9niexcp" }), undefined);
assert.equal(detailArgs(undefined), undefined);
assert.equal(detailArgs({}), undefined);
// 人给的字照留
assert.deepEqual(JSON.parse(detailArgs({ search: "Makro", period: "2026-09", connection: "CWEC-0003",
  posting_kind: "service" })!), { search: "Makro", period: "2026-09", posting_kind: "service" });
// 结果：内部的键与值拿掉，单号、金额、状态、说明留着；拿空了的列表 / 对象不显示，原来就空的照留
const out = detailOutput(JSON.stringify({ results: [{ document: "CWH-2026-00031", status: "entered", docnum: "HP691015-001",
  invoice_no: "IV6810-00012", total: 1070, written: { payment: "cash", debit_account: "53-05-02-00" },
  file: "file_011CNha8iCJcU1wXNR6q4V8w", push_connection: "CWEC-0003", record: "PR-1", batches: ["b1c2d3e4f5"] }],
  account_set: "C:\\ExpressI\\T9NIEXCP", account_label: "T.9 นิภา", note: "queued: sent", files: [],
  folder: "c:\\pearnlyexpress\\70exp", session_id: "sesn_01ABCDEFGH", ok: true, count: 0 }));
assert.deepEqual(JSON.parse(out), { results: [{ status: "entered", docnum: "HP691015-001", invoice_no: "IV6810-00012",
  total: 1070, written: { payment: "cash", debit_account: "53-05-02-00" } }], account_label: "T.9 นิภา",
  note: "queued: sent", files: [], ok: true, count: 0 });
assert.ok(out.includes("\n  "));  // 缩进显示
// 文件夹里的文件：文件名、相对路径留着，本地绝对路径拿掉
assert.deepEqual(sanitizeDetail({ files: [{ path: "2026-09/inv-01.pdf", name: "inv-01.pdf" },
  { path: "C:\\Scans\\a.pdf", name: "a.pdf" }] }),
  { files: [{ path: "2026-09/inv-01.pdf", name: "inv-01.pdf" }, { name: "a.pdf" }] });
// 只剩内部名字的：显示空；不是 JSON / 不是对象的原样
assert.equal(detailOutput(JSON.stringify({ document: "CWH-1", connection: "x" })), "");
assert.equal(detailOutput("Express is offline on CWEC-0003"), "Express is offline on CWEC-0003");
assert.equal(detailOutput("42"), "42");
// 点了「拒绝」：给用户看 message（术语表，按用户语言）；只给模型读的英文 instruction 不显示
assert.deepEqual(JSON.parse(detailOutput(JSON.stringify({ status: "denied", message: "已拒绝此更改",
  instruction: "Do not try it again unless the user asks." }))), { status: "denied", message: "已拒绝此更改" });
(globalThis as any).done();
""")

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
