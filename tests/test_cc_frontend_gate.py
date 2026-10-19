"""CC 前端标准闸门的新判据 R4–R15（docs/work/2026-10-17-cc-frontend-standards.md 第 3 节）。

· 每条规则至少一个「拦」、一个「放」的用例：尺寸、间距、字号与行高、层级、动效、其他数值类、任意值与 ``!``、断点与输入设备变体、
  JSX 数值属性与内联 style、globals.css 自身、``--cc-*`` 必须已定义、字重；
· 多行 className、``cn()`` 拼接、模板字符串、``[&_svg]:size-4`` 这类变体前缀、注释不算；
· 收紧范围（scripts/frontend-tokens-strict.txt，已完成清单）：格式、只在范围内失败、范围外只报数；
· 现行 R1–R3 的 scan() 不受影响（tests/test_cc_ccs.py 的 TokenGateTests 原样保留）；对真实 src 跑：范围内 0 违例，CI 跑本文件。
"""
import importlib.util
import pathlib
import posixpath
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts/check-frontend-tokens.py"
STRICT = ROOT / "scripts/frontend-tokens-strict.txt"
SRC = ROOT / "frontend/assistant/src"


def _gate():
    spec = importlib.util.spec_from_file_location("check_frontend_tokens_strict", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _gate()

# 夹具 globals.css：@theme 接过的名字 + 几个 --cc-* 定义（含运行时层登记的默认值）
GLOBALS = """@import "tailwindcss";
@theme inline {
  --color-ink: var(--cc-ink);
  --color-ink-3: var(--cc-ink-3);
  --radius-md: var(--cc-radius-md);
  --shadow-sm: var(--cc-shadow-sm);
  --text-caption: var(--cc-text-caption);
  --text-caption--line-height: var(--cc-text-caption-lh);
  --text-body: var(--cc-row-font);
  --font-sans: var(--cc-font-sans);
  --font-weight-medium: var(--cc-weight-medium);
  --ease-cc: var(--cc-ease);
  --animate-cc-pulse: cc-pulse var(--cc-pulse) infinite;
}
:root {
  --cc-ink: var(--code-ink-1);
  --cc-ink-3: var(--code-ink-3);
  --cc-space-8: var(--code-gap-1);
  --cc-space-12: var(--code-gap-2);
  --cc-icon-sm: var(--code-icon-sm);
  --cc-z-menu: 60; /* 现有值·CC 自定 */
  --cc-mirror: -1; /* 现有值·CC 自定 */
  --cc-dur: var(--code-dur-3);
  --cc-ease: var(--code-ease);
  --cc-thread-w: var(--code-thread-w);
  --cc-thread-gutter: var(--cc-space-16);
  --cc-bubble-min: 20%; /* 现有值·CC 自定 */
  --cc-vvtop: 0px; /* 运行时写入：lib/viewport.ts */
  --cc-status-ok: var(--cc-success);
  --cc-status-bad: var(--cc-danger);
  --cc-weight-medium: 500; /* 实测：页面标题 */
  --cc-radius-md: var(--code-radius-8);
  --cc-shadow-sm: var(--code-shadow-sm);
  --cc-text-caption: var(--code-text-caption);
  --cc-text-caption-lh: var(--code-text-caption-lh);
  --cc-row-font: var(--code-text-body);
  --cc-row-lh: var(--code-text-body-lh);
  --cc-font-sans: var(--ccs-font-sans);
  --cc-pulse: var(--code-dur-pulse);
  --cc-space-16: var(--code-gap-3);
  --cc-success: var(--ccs-success);
  --cc-danger: var(--ccs-danger);
}
"""


class StrictCase(unittest.TestCase):
    def scan(self, source, name="components/x.tsx", css=GLOBALS, strict=None):
        """返回 [(规则, 片段)]；默认把被测文件放进收紧范围。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "globals.css").write_text(css, encoding="utf-8")
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name != "globals.css":
                path.write_text(source, encoding="utf-8")
            scope = [name] if strict is None else strict
            hits = gate.scan_strict(str(root), scope)
            return [(rule, snippet) for rel, _, rule, snippet, inside in hits if rel == name and inside]

    def rules(self, source, **kw):
        return [rule for rule, _ in self.scan(source, **kw)]

    def assertBlocked(self, cases):
        for source, rule in cases.items():
            self.assertIn(rule, self.rules(source), source)

    def assertPasses(self, cases):
        for source in cases:
            self.assertEqual(self.scan(source), [], source)


class RuleTests(StrictCase):
    def test_r4_sizes(self):
        self.assertBlocked({
            '<i className="h-8" />': "R4",
            '<i className="size-4.5" />': "R4",
            '<i className="max-w-md" />': "R4",
            '<i className="w-56 min-w-40" />': "R4",
            '<i className="[&_svg]:size-4" />': "R4",
        })
        self.assertPasses([
            '<i className="size-(--cc-icon-sm) h-full w-auto min-w-0 max-w-none w-1/2 h-fit min-h-0" />',
            '<i className="max-w-[calc(var(--cc-thread-w)+var(--cc-thread-gutter)*2)]" />',
        ])

    def test_r5_spacing(self):
        self.assertBlocked({
            '<i className="p-3" />': "R5",
            '<i className="gap-1.5" />': "R5",
            '<i className="-mt-1" />': "R5",
            '<i className="px-px" />': "R5",
            '<i className="top-9" />': "R5",
        })
        self.assertPasses([
            '<i className="p-(--cc-space-12) -mt-(--cc-space-8) gap-0 mx-auto inset-0 top-full -translate-y-1/2 space-x-reverse" />',
            '<i className="justify-start items-end text-end pointer-events-none" />',
        ])

    def test_r6_type_scale_and_leading(self):
        self.assertBlocked({
            '<i className="text-lg" />': "R6",
            '<i className="text-caption/6" />': "R6",
            '<i className="leading-snug" />': "R6",
            '<i className="leading-6" />': "R6",
        })
        self.assertPasses([
            '<i className="text-caption text-body text-ink text-ink-3/50 text-center text-nowrap text-ellipsis" />',
            '<i className="text-(length:--cc-row-font) leading-none leading-(--cc-row-lh)" />',
        ])

    def test_r7_layers(self):
        self.assertBlocked({
            '<i className="z-50" />': "R7",
            '<i className="z-[80]" />': "R7",
            "<DragOverlay zIndex={55} />": "R7",
            "<i style={{ zIndex: 70 }} />": "R7",
        })
        self.assertPasses(['<i className="z-(--cc-z-menu) z-0 z-auto z-[calc(var(--cc-z-menu)+1)]" />', "<DragOverlay zIndex={0} />"])

    def test_r8_motion(self):
        self.assertBlocked({
            '<i className="transition-all" />': "R8",
            '<i className="duration-200" />': "R8",
            '<i className="ease-in-out" />': "R8",
            '<i className="animate-spin" />': "R8",
            '<i className="animate-in fade-in-0 zoom-in-95" />': "R8",
            '<i className="slide-in-from-top-2" />': "R8",
            '<i className="transition-[opacity_200ms]" />': "R8",
        })
        self.assertPasses([
            '<i className="transition transition-colors transition-[color,box-shadow] duration-(--cc-dur) ease-cc animate-cc-pulse" />',
        ])

    def test_r9_other_numeric_utilities(self):
        self.assertBlocked({
            '<i className="opacity-50" />': "R9",
            '<i className="active:scale-90" />': "R9",
            '<i className="-scale-x-100" />': "R9",
            '<i className="rotate-45" />': "R9",
            '<i className="scale-x-90" />': "R9",
            '<i className="rotate-y-45" />': "R9",
            '<i className="skew-x-6" />': "R9",
            '<i className="ring-2" />': "R9",
            '<i className="outline-2" />': "R9",
            '<i className="border-t-2" />': "R9",
            '<i className="underline-offset-2" />': "R9",
            '<i className="divide-y-2" />': "R9",
            '<i className="line-clamp-2" />': "R9",
            '<i className="stroke-[1.5px]" />': "R10",
            '<i className="backdrop-blur-[1px]" />': "R10",
            '<i className="card blur" />': "R9",
        })
        self.assertPasses([
            '<i className="opacity-0 opacity-100 border border-t ring-ink outline-none stroke-current line-clamp-none" />',
            # 带轴的变换同样只用变量（B3 的「还原」图标；B8 前 R9 把轴当成值、误判）
            '<i className="scale-x-(--cc-mirror) -scale-y-(--cc-mirror) rotate-y-(--cc-mirror) scale-(--cc-mirror)" />',
            'window.addEventListener("blur", stop);',
        ])

    def test_r10_arbitrary_values_and_important(self):
        self.assertBlocked({
            '<i className="max-w-[calc(var(--cc-thread-w)+2rem)]" />': "R10",
            '<i className="grid-cols-[minmax(20%,1fr)_auto]" />': "R10",
            '<i className="text-[0.9em]" />': "R10",
            '<i className="max-h-[calc(var(--cc-vvtop)*0.8)]" />': "R10",
            '<i className="!px-0" />': "R10",
            '<i className="p-0!" />': "R10",
            '<i className="[--cc-modal-share:0.92]" />': "R10",
        })
        self.assertPasses([
            '<i className="grid-cols-[minmax(var(--cc-bubble-min),1fr)_auto] pb-[max(var(--cc-space-8),env(safe-area-inset-bottom))]" />',
            '<i className="[--cc-thread-w:var(--cc-thread-gutter)] h-[100%] w-[calc(var(--cc-thread-w)*2)] grid-cols-[1fr_auto]" />',
            '<i className="!flex items-center!" />',
        ])

    def test_r11_breakpoints_and_input_variants(self):
        self.assertBlocked({
            '<i className="sm:flex-row" />': "R11",
            '<i className="lg:hidden" />': "R11",
            '<i className="max-sm:hidden" />': "R11",
            '<i className="pointer-coarse:hidden" />': "R11",
            '<i className="any-pointer-fine:flex" />': "R11",
            '<i className="min-[640px]:flex" />': "R11",
        })
        self.assertPasses(['<i className="md:flex max-md:hidden in-data-[cc-touch]:flex data-[state=open]:flex [&>svg]:shrink-0 group-hover:flex" />'])

    def test_r12_jsx_numbers_and_inline_style(self):
        self.assertBlocked({
            "<Menu sideOffset={6} />": "R12",
            "<Menu collisionPadding={8} />": "R12",
            "<Tip delayDuration={300} />": "R12",
            "function Sub({ sideOffset = 6 }) {}": "R12",
            "<Icon size={16} />": "R12",
            '<svg width="22" height="22" strokeWidth="1.5" />': "R12",
            "<i style={{ opacity: 0.5 }} />": "R12",
            "<i style={{ paddingInlineStart: `${level * 1.5}em` }} />": "R12",
            '<i style={{ width: "12px" }} />': "R12",
            'el.style.height = "24px";': "R12",
            'const SIDEBAR_WIDTH_ICON = "3rem";': "R12",
        })
        self.assertPasses([
            "<Menu sideOffset={MENU_OFFSET} collisionPadding={0} /> <Tip delayDuration={0} />",
            '<i style={{ width: `${w}px`, height: "var(--cc-space-8)", opacity: 1, display: "none" }} />',
            'el.style.height = `${el.scrollHeight}px`; root.style.setProperty("--cc-vvtop", `${top}px`);',
            '<Button size="sm" /> <svg width={box.width} />',
        ])

    def test_r14_variables_must_be_defined(self):
        self.assertBlocked({
            '<i className="p-(--cc-space-13)" />': "R14",
            '<i style={{ color: "var(--cc-nope)" }} />': "R14",
            '<i className="h-(--code-control-xs)" />': "R14",
            'const v = "var(--ccs-text-sm-size)";': "R14",
            'const c = `var(--cc-missing-${k})`;': "R14",
        })
        self.assertPasses([
            'root.style.setProperty("--cc-vvtop", `${top}px`);',
            'const c = `var(--cc-status-${k})`;',
            '<i className="[--cc-thread-w:var(--cc-thread-gutter)] text-(--cc-ink)" />',
        ])

    def test_r15_font_weights(self):
        self.assertBlocked({
            '<i className="font-semibold" />': "R15",
            '<i className="font-[550]" />': "R15",
            '<i className="font-bold" />': "R15",
        })
        self.assertPasses(['<i className="font-medium font-sans font-(--cc-weight-medium)" />'])

    def test_variable_forms_only_reference_cc_names(self):
        # 1.2：变量写法、方括号里的 var()、内联 style 的 var() 只能引用 --cc-*；别的名字记到对应族的规则
        self.assertBlocked({
            '<i className="w-(--sidebar-width)" />': "R4",
            '<i className="max-w-[var(--thread-max-width)]" />': "R4",
            '<i className="max-w-[calc(var(--thread-max-width)+var(--cc-thread-gutter)*2)]" />': "R4",
            '<i className="p-(--foo)" />': "R5",
            '<i className="-mt-(--foo)" />': "R5",
            '<i className="text-(length:--foo)" />': "R6",
            '<i className="leading-(--foo)" />': "R6",
            '<i className="z-(--foo)" />': "R7",
            '<i className="duration-(--foo)" />': "R8",
            '<i className="opacity-(--foo)" />': "R9",
            '<i className="font-(--foo)" />': "R15",
            '<i style={{ width: "var(--sidebar-width)" }} />': "R12",
            'el.style.width = "calc(var(--sidebar-width) * 2)";': "R12",
        })
        # 名字不对和任意值里写数是两处问题，各记一条
        self.assertEqual(sorted(self.rules('<i className="max-w-[calc(var(--thread-max-width)+2rem)]" />')), ["R10", "R4"])
        self.assertPasses([
            '<i className="w-(--cc-icon-sm) p-(--cc-space-12) text-(length:--cc-row-font) leading-(--cc-row-lh)" />',
            '<i className="max-w-[calc(var(--cc-thread-w)+var(--cc-thread-gutter)*2)] z-(--cc-z-menu)" />',
            '<i style={{ width: "var(--cc-icon-sm)", height: `calc(var(--cc-space-8) * ${n})` }} />',
        ])

    def test_arbitrary_properties_and_compound_variants_do_not_slip_through(self):
        # B0 补修复核建议 ① 与 B1 开工前核对的几种理论漏拦写法（docs/work/2026-10-17-cc-frontend-standards.md「B1 开工前」）
        self.assertBlocked({
            # 任意属性引用外来变量
            '<i className="[max-width:var(--thread-max-width)]" />': "R10",
            '<i className="[width:var(--sidebar-width)]" />': "R10",
            '<i className="[transition-duration:var(--foo)]" />': "R10",
            '<i className="[--cc-thread-w:var(--foo)]" />': "R10",
            # 任意属性写不带单位的层级、行高、字重
            '<i className="[z-index:50]" />': "R7",
            '<i className="[line-height:2]" />': "R6",
            '<i className="[font-weight:600]" />': "R15",
            # 组合变体里的断点、输入设备、容器查询（not-sm: 生成 @media not (width >= 40rem)）
            '<i className="not-sm:flex" />': "R11",
            '<i className="not-pointer-coarse:flex" />': "R11",
            '<i className="not-@md:flex" />': "R11",
            '<i className="group-@md:flex" />': "R11",
            # 模板里拼数字字面量成长度
            'const m = `${4}px`;': "R12",
            'new IntersectionObserver(f, { rootMargin: `${2 * 100}px` });': "R12",
        })
        self.assertEqual(self.rules('<i className="[--cc-thread-w:var(--foo)]" />'), ["R10"])
        self.assertPasses([
            '<i className="[--cc-thread-w:var(--cc-thread-gutter)] [z-index:var(--cc-z-menu)] [z-index:calc(var(--cc-z-menu)+1)]" />',
            '<i className="[transition-duration:var(--cc-dur)] [line-height:var(--cc-row-lh)] [z-index:0]" />',
            '<i className="not-max-md:flex not-md:hidden not-first:mt-0 group-hover:flex in-data-[cc-touch]:flex not-data-[x]:flex" />',
            'const a = `${w}px`; const b = `${0}px`; const c = `translate(${x}px, 0)`; const d = `p-${4}`;',
        ])
        # 同一处只记一次：style 块里的 `${4}px` 归 STYLE_PAIR
        self.assertEqual(self.rules("<i style={{ width: `${4}px` }} />"), ["R12"])

    def test_container_query_variants(self):
        self.assertBlocked({
            '<i className="@lg:flex" />': "R11",
            '<i className="@md:flex" />': "R11",
            '<i className="@max-md:hidden" />': "R11",
            '<i className="@[480px]:grid-cols-2" />': "R11",
            '<i className="@min-[480px]:grid-cols-2" />': "R11",
            '<i className="@lg/main:flex" />': "R11",
        })
        self.assertPasses(['<i className="@container md:flex max-md:hidden" />'])

    def test_easing_keywords_inside_arbitrary_values(self):
        self.assertBlocked({
            '<i className="animate-[x_var(--cc-dur)_ease-out]" />': "R8",
            '<i className="animate-[cc-pop_var(--cc-dur)_ease-in-out_both]" />': "R8",
            '<i className="[transition:opacity_var(--cc-dur)_linear]" />': "R8",
            '<i className="[transition:opacity_200ms_var(--cc-ease)]" />': "R8",
            '<i className="[animation-timing-function:ease-in]" />': "R8",
            '<i className="[animation:x_var(--cc-dur)_steps(4)]" />': "R8",
        })
        self.assertPasses([
            '<i className="animate-[x_var(--cc-dur)_var(--cc-ease)] [transition:opacity_var(--cc-dur)_var(--cc-ease)]" />',
            # 关键帧名里带 linear / ease 不算缓动词
            '<i className="animate-[cc-linear-x_var(--cc-dur)] animate-[cc-ease-pop_var(--cc-dur)_var(--cc-ease)]" />',
        ])

    def test_transform_in_inline_style(self):
        self.assertBlocked({
            '<i style={{ transform: "translateY(4px)" }} />': "R12",
            'el.style.transform = "translateX(-0.5rem)";': "R12",
            '<i style={{ marginTop: "-4px" }} />': "R12",
        })
        self.assertPasses([
            '<i style={{ transform: `translateX(${x}px)` }} />',
            '<i style={{ transform: "translateY(-50%) scale(0.9) rotate(90deg)" }} />',
            'el.style.transform = "translate3d(0, 0, 0)";',
        ])

    def test_zero_length_strings_pass(self):
        self.assertPasses(['<i style={{ marginTop: "0px" }} />', 'const D = "0ms"; const E = "0.0s";'])
        self.assertBlocked({'const W = "3rem";': "R12", 'new IntersectionObserver(f, { rootMargin: "200px" });': "R12"})

    def test_one_violation_is_counted_once(self):
        # style 块里的值归 STYLE_PAIR，.style.x = / setProperty / svg 宽高归各自的模式，字面字符串不再另记「整串是长度」
        once = {
            "<i style={{ zIndex: 50 }} />": "R7",
            "const s = { zIndex: 50 };": "R7",
            '<i style={{ width: "24px" }} />': "R12",
            'el.style.width = "24px";': "R12",
            'el.style.setProperty("--cc-icon-sm", "12px");': "R12",
            '<svg width="22px" />': "R12",
        }
        for source, rule in once.items():
            self.assertEqual(self.rules(source), [rule], source)

    def test_class_strings_in_every_form(self):
        multi = 'const x = cn(\n  "flex items-center",\n  open && "p-3",\n  `gap-2 ${big ? "h-8" : "h-(--cc-icon-sm)"}`,\n);'
        self.assertEqual(sorted(self.scan(multi)), [("R4", "h-8"), ("R5", "gap-2"), ("R5", "p-3")])
        # 注释、运行时拼出来的片段（p-${n}）、普通文字不算
        quiet = '// p-3 h-8 z-50\n/* text-lg */\nconst a = `p-${n}`; const t = "会话 p 3"; const re = /p-3/;'
        self.assertEqual(self.scan(quiet), [])
        # JSX 文字里的撇号不吞掉后面的代码
        self.assertEqual(self.rules("<p>It's fine</p>\n<i className=\"p-3\" />"), ["R5"])


class GlobalsTests(StrictCase):
    def css(self, body):
        return self.scan("", name="globals.css", css=GLOBALS + body)

    def test_r13_blocks_hard_coded_declarations(self):
        cases = {
            ".a { padding: 12px; }": "padding",
            ".a { animation: cc-bob 1.4s ease-in-out infinite; }": "animation",
            ".a { transition: opacity var(--cc-dur) linear; }": "transition",
            ".a { transition-duration: 200ms; }": "transition-duration",
            ".a { font-weight: 600; }": "font-weight",
            ".a { z-index: 50; }": "z-index",
            ".a { line-height: 1.5; }": "line-height",
            ".a { width: calc(var(--cc-thread-w) + 2rem); }": "width",
            "@media (min-width: 768px) { .a { display: none; } }": "@media",
            ":root { --cc-new: 14px; }": "--cc-new",
            ".a { transform: translateX(1rem); }": "transform",
            ".a { transform: rotate(45deg) translate(-50%, 4px); }": "transform",
            ".a { flex: 0 0 240px; }": "flex",
            ".a { font: 500 14px/20px x; }": "font",
            ".a { font: var(--cc-weight-medium) 13px/1.4 x; }": "font",
        }
        for body, snippet in cases.items():
            hits = self.css(body)
            self.assertTrue(any(rule == "R13" and snippet in text for rule, text in hits), (body, hits))
        self.assertIn(("R5", "@apply p-3"), self.css(".a { @apply p-3; }"))

    def test_r13_allows_variables_keywords_and_skipped_blocks(self):
        body = "\n".join([
            ".a { padding: var(--cc-space-8) 0; width: 100%; height: auto; margin: 0 auto; inset: 50%; }",
            ".a { width: calc(var(--cc-thread-w) * 2); transition: opacity var(--cc-dur) var(--cc-ease); animation: none; }",
            ".a { z-index: var(--cc-z-menu); font-weight: var(--cc-weight-medium); line-height: inherit; }",
            "@media (width < 768px) { .a { display: none; } }",
            "@media (width >= 768px) and (pointer: coarse) { .a { display: none; } }",
            "@media (prefers-reduced-motion: reduce) { .a { animation: none; } }",
            "@keyframes cc-bob { from { transform: translateY(0); } to { transform: translateY(-6px); padding: 3px; } }",
            "@font-face { font-family: x; font-weight: 400; }",
            ":root { --cc-tap: 44px; /* 外壳实测：G2 */ --cc-row-h: var(--cc-space-8); --tw-x: 3px; }",
            ".a { @apply border-ink text-ink; }",
            ".a { transform: translateX(var(--cc-space-8)); } .b { transform: translateY(-50%); } .c { transform: rotate(90deg) scale(0.94); }",
            ".a { transform: translate(calc(var(--cc-space-8) * -1), 0); flex: 1 1 0%; } .b { flex: 1; flex: none; flex: 0 0 var(--cc-icon-sm); }",
            ".a { font: inherit; } .b { font: var(--cc-weight-medium) var(--cc-row-font) / var(--cc-row-lh) var(--cc-font-sans); }",
        ])
        self.assertEqual(self.css(body), [])

    def test_globals_references_must_be_defined(self):
        self.assertIn(("R14", "--cc-undefined"), self.css(".a { color: var(--cc-undefined); }"))


class ScopeTests(StrictCase):
    def test_new_rules_fail_only_inside_the_strict_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "globals.css").write_text(GLOBALS, encoding="utf-8")
            (root / "components").mkdir()
            (root / "components/done.tsx").write_text('<i className="p-(--cc-space-8)" />', encoding="utf-8")
            (root / "components/todo.tsx").write_text('<i className="p-3 z-50" />', encoding="utf-8")
            listing = root / "strict.txt"
            listing.write_text("# 说明\ncomponents/done.tsx\n", encoding="utf-8")
            run = lambda *extra: subprocess.run(  # noqa: E731
                [sys.executable, str(GATE), "--root", str(root), "--strict-list", str(listing), *extra],
                capture_output=True, text=True)
            ok = run()
            self.assertEqual(ok.returncode, 0, ok.stdout)
            self.assertIn("范围外 R4–R15：2 处 / 1 个文件", ok.stdout)
            listing.write_text("components/done.tsx\ncomponents/todo.tsx\n", encoding="utf-8")
            bad = run()
            self.assertEqual(bad.returncode, 1, bad.stdout)
            self.assertIn("components/todo.tsx:1: [R5] p-3", bad.stdout)
            self.assertIn("components/todo.tsx:1: [R7] z-50", bad.stdout)
            report = run("--report")
            self.assertIn("R5         1      0", report.stdout)
            # 写法不规范、扩展名不受管、文件不存在的条目对不上任何受检文件：直接失败，不能悄悄跳过
            for entry in ("./components/todo.tsx", "components//todo.tsx", "components/../components/todo.tsx",
                          "components/Todo.tsx", "components/todo.md", "components/gone.tsx"):
                listing.write_text(f"components/done.tsx\n{entry}\n", encoding="utf-8")
                odd = run()
                self.assertEqual(odd.returncode, 1, (entry, odd.stdout))
                self.assertIn(f"收紧清单条目对不上任何受检文件：{entry}", odd.stdout)
            # 清单文件删掉（B8）= 全仓收紧
            listing.unlink()
            self.assertIsNone(gate.strict_list(str(listing)))
            self.assertEqual(run().returncode, 1)

    def test_strict_list_is_a_sorted_completed_list(self):
        self.assertTrue(STRICT.exists(), "B8 之前清单文件必须存在（B8 删掉后改成断言它不存在）")
        entries = gate.strict_list(str(STRICT))
        self.assertEqual(entries, sorted(entries))
        self.assertEqual(len(entries), len(set(entries)))
        checked = {rel for rel, _ in gate.checked_files(str(SRC))}
        for entry in entries:
            self.assertTrue((SRC / entry).is_file(), entry)
            self.assertEqual(entry, posixpath.normpath(entry), entry)
            self.assertTrue(entry.endswith(gate.EXTENSIONS), entry)
            self.assertIn(entry, checked)
        self.assertEqual(gate.unmatched_entries(str(SRC), entries), [])
        text = STRICT.read_text(encoding="utf-8")
        self.assertIn("已完成清单，不是豁免清单", text)
        self.assertIn("B8 删掉本文件", text)
        # 不设忽略清单、不支持行内禁用
        self.assertNotIn("IGNORE", dir(gate))
        self.assertNotIn("noqa", GATE.read_text(encoding="utf-8").split("def scan_strict", 1)[1])

    def test_the_old_scan_still_only_judges_colors_radii_and_shadows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "globals.css").write_text(GLOBALS, encoding="utf-8")
            (root / "x.tsx").write_text('<i className="p-3 z-50 text-[0.9em] rounded-md" />', encoding="utf-8")
            self.assertEqual(gate.scan(str(root)), [])

    def test_the_real_frontend_has_no_violations_inside_the_strict_list(self):
        entries = gate.strict_list(str(STRICT))
        inside = [h for h in gate.scan_strict(str(SRC), entries) if h[4]]
        self.assertEqual(inside, [])
        result = subprocess.run([sys.executable, str(GATE)], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ci_runs_this_file(self):
        workflow = (ROOT / ".github/workflows/development.yml").read_text(encoding="utf-8")
        self.assertIn("run: python3 tests/test_cc_frontend_gate.py", workflow)
        self.assertIn("run: python3 scripts/check-frontend-tokens.py", workflow)


if __name__ == "__main__":
    unittest.main()
