"""CC 前端开着不操作时不耗电（docs/work/2026-10-18-cc-idle-animations.md）。

10-18 测试机：CC 桌面版首页静置约 0.8 核，首页小猫 7 个无限循环的 SVG 动画每帧重画（带铅笔滤镜）。改成出现时播几轮、播完停住后，
这里锁住三件事：

· 装饰动画（产品形象小单的眨眼、笑、尾巴、铃铛、举牌、打勾）不许无限循环：轮数只走 globals.css 的 ``--cc-mascot-*-runs``，
  轮数 × 时长不超过约 10 秒；播完停在静止姿势——keyframes 的结尾（写了 100% 就是它，没写就是元素本来的样子）与元素不加动画时一致，
  开头也一致（打勾除外：开头是还没画出来的勾）；形象图里这些元素不带同名的表现属性；后台暂停、回放、减少动效三条规则都包含全部 7 个；
  组件按页面可见性暂停、按问候语回放。
· globals.css 里 infinite 的动画只允许「正在工作」那几种（白名单），它们的选择器或挂载处只在工作状态成立。
· 组件与 lib 里：不许新写无限动画（内联 style、animate-[…]、Web Animations 的 iterations: Infinity）；Tailwind 自带的无限动画类与
  「在忙」组件（animate-spin / animate-pulse / TypingDots / Skeleton / cc-spinner / cc-loading）每一处都登记了只在工作时挂载的条件；
  setInterval 与自己反复排 requestAnimationFrame 的循环每一处都登记了什么时候在跑、怎么停。
"""
import importlib.util
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend/assistant/src"
CSS_PATH = SRC / "globals.css"
SVG_PATH = SRC / "brand/cc.svg"
GATE = ROOT / "scripts/check-frontend-tokens.py"

_spec = importlib.util.spec_from_file_location("check_frontend_tokens", GATE)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

# 装饰动画：keyframes 名 → 播它的元素的类名（形象图 brand/cc.svg 里的 <g class>）
DECORATIVE = {
    "hm-blink": "hm-eyes",
    "cc-calm": "hm-calm",
    "cc-laugh": "hm-laugh",
    "cc-tail": "cc-tail",
    "cc-bell": "cc-bell",
    "cc-hold": "cc-hold",
    "cc-tick": "cc-tick",
}
# 一次最多播多久（秒）：用户口径「老机器能正常运行」，装饰动画出现时活动一下就停（docs/work/2026-10-18-cc-idle-animations.md）
PLAY_LIMIT_S = 10.0
# 开头与静止姿势不同的（动画本身就是从别的样子变到静止姿势）
STARTS_ELSEWHERE = {"cc-tick": "开头是还没画出来的勾（画勾的起点），停下时是画好的勾"}
# 不加动画时的初始值（CSS 初始值）
INITIAL = {"transform": "none", "opacity": "1", "stroke-dashoffset": "0"}

# globals.css 里允许 infinite 的「正在工作」动画：选择器 → (keyframes, 只在工作时成立的原因)
WORKING_CSS = {
    ".cc-loading .pearnly-mascot-host": ("cc-bob", "打开对话、消息取回前的加载页（ThreadLoading，只在 isLoading 时挂载）"),
    '.cc-activity[data-activity="running"]::before': ("cc-pulse", "侧栏 / 公司页对话行尾：这一轮在跑才有 data-activity=running"),
    ".cc-spinner": ("cc-spin", "按钮里、行内的转圈：只在忙时挂载（B2 起替换 animate-spin）"),
    ".cc-spin i": ("cc-spin-dot", "四点转圈（components/typing-dots.tsx）：只在等待回复、步骤进行中挂载"),
    "@theme --animate-cc-pulse": ("cc-pulse", "骨架的明暗呼吸（animate-cc-pulse）：只在加载占位时挂载"),
}

# 组件里的「在忙」动画：(文件, 标记, 只在工作时挂载的条件——在标记所在行或往上 window 行内出现的原文, window)
BUSY_MARKER = re.compile(r"animate-(?:spin|pulse|ping|bounce)\b|\banimate-cc-pulse\b|\bcc-spinner\b|<TypingDots\b|<Skeleton\b|\"cc-loading\"")
BUSY_SITES = [
    ("assistant.tsx", '"cc-loading"', "function ThreadLoading()", 3),
    ("components/month-ledger.tsx", "animate-spin", "{loading && (", 3),
    ("components/results-panel.tsx", "animate-spin", "function Loading()", 3),
    ("components/add-company.tsx", "animate-spin", "rows === null ?", 3),
    ("components/add-company.tsx", "animate-spin", "busy === row.identity ?", 1),
    ("components/add-company.tsx", "animate-spin", "refreshing ?", 0),
    ("components/companies.tsx", "animate-pulse", "shown === null ?", 2),
    ("components/companies.tsx", "animate-spin", "loading &&", 0),
    ("components/session-panel.tsx", "animate-spin", "{!detail && !error && (", 3),
    ("components/pending.tsx", "animate-spin", "items === null ?", 2),
    ("components/pending.tsx", "animate-spin", "open && !view && !error", 3),
    ("components/pending.tsx", "animate-spin", "busy &&", 0),
    ("components/company-page.tsx", "animate-spin", "if (!detail)", 3),
    ("components/company-page.tsx", "animate-spin", "if (!rows) return", 0),
    ("components/company-cards.tsx", "animate-spin", "busy ?", 0),
    ("components/scheduled-tasks.tsx", "animate-pulse", "rules === null || shown === null ?", 3),
    ("components/ui/skeleton.tsx", "animate-pulse", "function Skeleton(", 5),
    ("components/assistant-ui/thread-list.tsx", "animate-spin", "loadingMore &&", 0),
    ("components/assistant-ui/thread-list.tsx", "<Skeleton", "const ThreadListSkeleton", 15),
    ("components/assistant-ui/thread.tsx", "<Skeleton", "pending.files.map", 3),
    ("components/assistant-ui/thread.tsx", "<TypingDots", "if (!visible) return null;", 12),
    ("components/tool-uis/steps.tsx", "<TypingDots", "running &&", 0),
    ("components/assistant-ui/attachment.tsx", "animate-spin", "{isUploading && (", 5),
    ("components/pending.tsx", "animate-spin", "{open && !sets && !error && (", 3),  # 待选账套那一行展开、可换账套还没取回
]
# 这几个占位组件本身由谁、在什么条件下挂（上面登记的是组件内部）
BUSY_MOUNTS = [
    ("components/assistant-ui/thread.tsx", "{isLoading && Loading && <Loading />}"),
    ("components/assistant-ui/thread-list.tsx", "<AuiIf condition={(s) => s.threads.isLoading && !s.threads.threadIds.length}>\n        <ThreadListSkeleton />"),
    ("components/results-panel.tsx", "if (!preview) return <Loading />;"),
    ("components/assistant-ui/thread.tsx", "if (!pending) return null;"),
]

# setInterval：(文件, 所在函数或条件——在调用往上 window 行内出现的原文, 什么时候在跑, window)
INTERVAL_SITES = [
    ("components/settings.tsx", "function useDesktopState(", "设置「此电脑」开着时每 2 秒读本机状态", 6),
    ("components/settings.tsx", "export function PoolComputers(", "设置里「电脑」一节开着时每 10 秒刷新（多电脑：网页「连接」与桌面版「此电脑」都用它）", 8),
    ("components/scheduled-tasks.tsx", "function HelperConnect(", "「连接 CC 桌面版」弹窗开着时每 3 秒看是否在线", 16),
    ("components/ui/drag.tsx", "if (step && (step < 0 ? d.intent.up : d.intent.down))", "拖动中指针贴着滚动区边缘时自动滚，离开边缘 / 放下即停", 8),
    ("components/assistant-ui/thread.tsx", "if (!running) return;", "工作状态行显示时每秒走一次计时（回合结束即卸下）", 3),
    ("lib/company-panel.ts", "export function useMinuteClock()", "公司页开着时每分钟刷新相对时间", 4),
]
# 自己反复排 requestAnimationFrame 的循环：(文件, 函数名, 停下的条件——函数体里的原文)
RAF_LOOPS = [
    ("components/ui/drag.tsx", "watch", "performance.now() > refocus.until"),
    ("components/delete-chats.tsx", "check", "--frames > 0"),
    ("components/delete-chats.tsx", "wait", "++frames <= 120"),
]


def css_code() -> str:
    return gate.strip_css_comments(CSS_PATH.read_text(encoding="utf-8"))


def parse_css(code: str):
    """→ (规则 [(外层链, 选择器, 属性, 值)], keyframes {名: [(偏移列表, {属性: 值})]})。@media / @layer 嵌套展开。"""
    rules, keyframes = [], {}
    stack = []  # 每层 (文本)
    start = 0
    quote = None
    paren = 0
    for i, c in enumerate(code):
        if quote:
            if c == quote:
                quote = None
            continue
        if c in "\"'":
            quote = c
        elif c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
        elif paren == 0 and c in "{;}":
            text = code[start:i].strip()
            start = i + 1
            if c == "{":
                stack.append(re.sub(r"\s+", " ", text))
                if len(stack) >= 2 and stack[-2].startswith("@keyframes"):
                    name = stack[-2].split()[1]
                    offsets = [s.strip() for s in text.split(",")]
                    offsets = ["0%" if s == "from" else "100%" if s == "to" else s for s in offsets]
                    keyframes.setdefault(name, []).append((offsets, {}))
                continue
            if text and ":" in text and stack:
                prop, value = (part.strip() for part in text.split(":", 1))
                if len(stack) >= 2 and stack[-2].startswith("@keyframes"):
                    keyframes[stack[-2].split()[1]][-1][1][prop] = value
                elif not stack[-1].startswith("@keyframes"):
                    rules.append((tuple(stack[:-1]), stack[-1], prop, value))
            if c == "}" and stack:
                stack.pop()
    return rules, keyframes


def root_vars(rules) -> dict:
    """:root（不带变体属性）与 @theme 里定义的变量 → 值（第一次定义）。"""
    out = {}
    for _, selector, prop, value in rules:
        if prop.startswith("--") and (selector == ":root" or selector.startswith("@theme")):
            out.setdefault(prop, value)
    return out


def resolve(value: str, defs: dict, depth: int = 0) -> str:
    if depth > 10:
        return value
    return re.sub(r"var\(\s*(--[\w-]+)\s*(?:,[^()]*)?\)", lambda m: resolve(defs.get(m.group(1), m.group(0)), defs, depth + 1), value)


def seconds(token: str) -> float:
    m = re.fullmatch(r"(\d*\.?\d+)(ms|s)", token)
    assert m, token
    return float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)


def animations(rules, keyframes):
    """所有 animation 声明 → [(选择器, 原值, keyframes 名, 原写的轮数 token, 解析后的轮数)]；@theme 的 --animate-* 也算。"""
    defs = root_vars(rules)
    out = []
    for outer, selector, prop, value in rules:
        if prop == "animation" or (prop.startswith("--animate-") and selector.startswith("@theme")):
            label = f"@theme {prop}" if prop.startswith("--animate-") else selector
            for raw_one, one in zip(split_top(value), split_top(resolve(value, defs))):
                tokens = one.replace("!important", " ").split()
                names = [t for t in tokens if t in keyframes]
                if not names:
                    continue
                raw_tokens = raw_one.split()
                runs_raw = next((t for t in raw_tokens if t == "infinite" or re.fullmatch(r"var\(--cc-[\w-]*runs\)", t)
                                 or re.fullmatch(r"\d+(\.\d+)?", t)), None)
                runs = next((t for t in tokens if t == "infinite" or re.fullmatch(r"\d+(\.\d+)?", t)), "1")
                out.append((label, raw_one, names[0], runs_raw, runs, outer))
        elif prop == "animation-iteration-count" and "infinite" in value:
            out.append((selector, value, None, "infinite", "infinite", outer))
    return out


def split_top(value: str):
    parts, depth, cur = [], 0, ""
    for c in value:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += c
    parts.append(cur.strip())
    return parts


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


class DecorativeMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules, cls.keyframes = parse_css(css_code())
        cls.defs = root_vars(cls.rules)
        cls.anims = animations(cls.rules, cls.keyframes)
        cls.svg = SVG_PATH.read_text(encoding="utf-8")

    def decl_for(self, name):
        found = [a for a in self.anims if a[2] == name]
        self.assertEqual(len(found), 1, f"{name} 应只有一处 animation 声明：{found}")
        return found[0]

    def test_mascot_parts_are_in_the_brand_svg(self):
        for cls in DECORATIVE.values():
            self.assertRegex(self.svg, rf'class="[^"]*\b{re.escape(cls)}\b', cls)

    def test_decorative_animations_play_a_few_rounds_within_the_limit(self):
        for name, cls in DECORATIVE.items():
            selector, raw, _, runs_raw, runs, _ = self.decl_for(name)
            self.assertIn("." + cls, selector, name)
            self.assertNotIn("infinite", raw, f"{name} 是装饰动画，不许无限循环")
            self.assertRegex(runs_raw or "", r"^var\(--cc-mascot-[\w-]+-runs\)$", f"{name} 的轮数要走 --cc-mascot-*-runs：{raw}")
            self.assertRegex(runs, r"^[1-9]\d*$", f"{name} 的轮数要是正整数：{runs}")
            dur_var = re.search(r"var\((--cc-mascot-[\w-]+-dur)\)", raw)
            self.assertTrue(dur_var, f"{name} 的时长要走 --cc-mascot-*-dur：{raw}")
            total = int(runs) * seconds(resolve(self.defs[dur_var.group(1)], self.defs))
            self.assertLessEqual(total, PLAY_LIMIT_S + 1e-9, f"{name} 一次播 {total:.1f} 秒，超过 {PLAY_LIMIT_S} 秒")

    def test_runs_tokens_are_registered_with_provenance(self):
        lines = CSS_PATH.read_text(encoding="utf-8").splitlines()
        for name in [n for n in self.defs if n.startswith("--cc-mascot-") and n.endswith("-runs")]:
            line = next(l for l in lines if l.strip().startswith(name + ":"))
            self.assertRegex(line, gate.PROVENANCE, name)

    def rest_value(self, cls: str, prop: str) -> str:
        """元素不加动画时这个属性的值：globals.css 里只按这个类（不带悬停 / 状态）写的那条，没有就是初始值。"""
        values = [v for _, sel, p, v in self.rules if p == prop and any(
            re.fullmatch(rf"(?:\.[\w-]+ )?\.{re.escape(cls)}", part.strip()) for part in sel.split(","))]
        return norm(values[-1]) if values else INITIAL[prop]

    def test_rest_pose_is_the_end_of_every_keyframes(self):
        for name, cls in DECORATIVE.items():
            frames = self.keyframes[name]
            props = {p for _, decls in frames for p in decls}
            self.assertTrue(props <= set(INITIAL), f"{name} 动了没登记初始值的属性：{props - set(INITIAL)}")
            for prop in props:
                at = {}
                for offsets, decls in frames:
                    for off in offsets:
                        if prop in decls:
                            at[off] = norm(decls[prop])
                rest = self.rest_value(cls, prop)
                self.assertEqual(at.get("100%", rest), rest, f"{name} 结尾的 {prop} 与静止姿势不同，停下会跳")
                first = at.get("0%", rest)
                if name in STARTS_ELSEWHERE:
                    self.assertNotEqual(first, rest, f"{name} 登记成开头不同，实际相同：从 STARTS_ELSEWHERE 删掉")
                else:
                    self.assertEqual(first, rest, f"{name} 开头的 {prop} 与静止姿势不同")

    def test_brand_svg_parts_carry_no_competing_presentation_attributes(self):
        for cls in DECORATIVE.values():
            for tag in re.findall(rf'<\w+[^>]*class="[^"]*\b{re.escape(cls)}\b[^"]*"[^>]*>', self.svg):
                for attr in ("opacity", "transform", "stroke-dashoffset"):
                    self.assertNotRegex(tag, rf"\s{attr}=", f"{cls} 自带 {attr}，静止姿势就不是 CSS 里的值")

    def selector_classes(self, needle: str, prop: str, value: str):
        found = [sel for _, sel, p, v in self.rules if needle in sel and p == prop and norm(v) == value]
        self.assertEqual(len(found), 1, f"{needle} {prop}: {value}")
        return set(re.findall(r"\.([\w-]+)", found[0].split(":is(", 1)[1]))

    def test_pause_replay_and_reduced_motion_cover_every_part(self):
        want = set(DECORATIVE.values())
        self.assertEqual(self.selector_classes("[data-cc-paused]", "animation-play-state", "paused"), want)
        self.assertEqual(self.selector_classes("[data-cc-replay]", "animation", "none"), want)
        reduced = [sel for outer, sel, p, v in self.rules
                   if any("prefers-reduced-motion: reduce" in o for o in outer) and p == "animation" and norm(v) == "none"
                   and ".cc-tail" in sel]
        self.assertEqual(len(reduced), 1)
        self.assertEqual({re.findall(r"\.([\w-]+)", part)[-1] for part in reduced[0].split(",")}, want)

    def test_mascot_component_pauses_in_background_and_replays_only_when_done(self):
        code = (SRC / "components/mascot.tsx").read_text(encoding="utf-8")
        self.assertIn('document.addEventListener("visibilitychange", sync)', code)
        self.assertIn('root.toggleAttribute("data-cc-paused", document.visibilityState === "hidden")', code)
        self.assertIn('root.setAttribute("data-cc-replay", "")', code)
        self.assertIn("stillPlaying(root)", code)
        welcome = (SRC / "assistant.tsx").read_text(encoding="utf-8")
        self.assertIn("<Mascot src={mascot} play={hello}", welcome)

    def test_reduced_motion_setting_runs_once_via_token(self):
        decl = [v for _, sel, p, v in self.rules if sel.startswith(':root[data-cc-motion="reduce"]') and p == "animation-iteration-count"]
        self.assertEqual(decl, ["var(--cc-reduced-runs) !important"])
        self.assertEqual(resolve("var(--cc-reduced-runs)", self.defs), "1")


class InfiniteOnlyWhileWorkingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules, cls.keyframes = parse_css(css_code())
        cls.anims = animations(cls.rules, cls.keyframes)

    def test_css_infinite_animations_are_the_working_whitelist(self):
        infinite = {(sel, name) for sel, _, name, _, runs, _ in self.anims if runs == "infinite"}
        self.assertEqual(infinite, {(sel, kf) for sel, (kf, _) in WORKING_CSS.items()},
                         "无限循环只留给「正在工作」的动画；装饰动画播几轮就停（见本文件开头）")
        for sel, (kf, _) in WORKING_CSS.items():
            self.assertNotIn(kf, DECORATIVE)

    def test_working_selectors_hold_only_while_working(self):
        # 按状态属性成立的：选择器里带着那个状态
        self.assertTrue(any('[data-activity="running"]' in sel for sel in WORKING_CSS))
        thread = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        self.assertIn("{isLoading && Loading && <Loading />}", thread)
        self.assertIn('<div className="cc-loading" role="status">', (SRC / "assistant.tsx").read_text(encoding="utf-8"))
        files = [p for p in SRC.rglob("*.ts*") if p.suffix in (".ts", ".tsx")]
        uses = [p.relative_to(SRC).as_posix() for p in files if '"cc-loading"' in p.read_text(encoding="utf-8")]
        self.assertEqual(uses, ["assistant.tsx"])
        dots = [p.relative_to(SRC).as_posix() for p in files if re.search(r'className="cc-spin"', p.read_text(encoding="utf-8"))]
        self.assertEqual(dots, ["components/typing-dots.tsx"])


def source_files():
    for path in sorted(SRC.rglob("*")):
        if path.suffix in (".ts", ".tsx") and "node_modules" not in path.parts:
            yield path.relative_to(SRC).as_posix(), gate.strip_comments(path.read_text(encoding="utf-8"))


class ComponentLoopTests(unittest.TestCase):
    def test_no_infinite_animation_written_in_components(self):
        bad = []
        for rel, code in source_files():
            for no, line in enumerate(code.splitlines(), 1):
                if re.search(r"(?i)\binfinite\b", line) or re.search(r"iterations\s*:\s*(?:Infinity|Number\.POSITIVE_INFINITY)", line):
                    bad.append(f"{rel}:{no}")
        self.assertEqual(bad, [], "组件里不写无限动画（装饰动画播几轮就停，正在工作的用 globals.css 的类）")

    def test_every_busy_animation_is_mounted_only_while_working(self):
        used = set()
        for rel, code in source_files():
            lines = code.splitlines()
            for no, line in enumerate(lines):
                for m in BUSY_MARKER.finditer(line):
                    hits = [i for i, (f, marker, guard, window) in enumerate(BUSY_SITES)
                            if f == rel and marker == m.group(0) and any(guard in l for l in lines[max(0, no - window):no + 1])]
                    self.assertTrue(hits, f"{rel}:{no + 1} {m.group(0)}：「在忙」的动画要登记只在工作时挂载的条件（BUSY_SITES）")
                    used.update(hits)
        stale = [BUSY_SITES[i] for i in range(len(BUSY_SITES)) if i not in used]
        self.assertEqual(stale, [], "登记了却找不到：删掉或改成现在的条件")
        for rel, text in BUSY_MOUNTS:
            self.assertIn(text, (SRC / rel).read_text(encoding="utf-8"), rel)

    def test_every_interval_has_a_registered_reason(self):
        used = set()
        for rel, code in source_files():
            lines = code.splitlines()
            for no, line in enumerate(lines):
                if not re.search(r"\bsetInterval\(", line):
                    continue
                hits = [i for i, (f, guard, _, window) in enumerate(INTERVAL_SITES)
                        if f == rel and any(guard in l for l in lines[max(0, no - window):no + 1])]
                self.assertTrue(hits, f"{rel}:{no + 1}：setInterval 要登记什么时候在跑（INTERVAL_SITES）")
                used.update(hits)
                self.assertRegex(code, r"clearInterval\(", f"{rel}：setInterval 要有对应的 clearInterval")
        self.assertEqual(sorted(used), list(range(len(INTERVAL_SITES))), "登记了却找不到")
        company = [rel for rel, code in source_files() if "useMinuteClock()" in code and not rel.startswith("lib/company-panel")]
        self.assertEqual(company, ["components/company-page.tsx"])

    def test_self_rescheduling_frames_have_an_end(self):
        found = set()
        for rel, code in source_files():
            for m in re.finditer(r"(?:const|let)\s+(\w+)\s*=\s*\([^)]*\)\s*=>\s*\{|function\s+(\w+)\s*\([^)]*\)\s*\{", code):
                name = m.group(1) or m.group(2)
                body = code[m.end() - 1:gate.balanced(code, m.end() - 1)]
                if re.search(rf"requestAnimationFrame\(\s*{re.escape(name)}\s*\)", body):
                    entry = next((e for e in RAF_LOOPS if e[0] == rel and e[1] == name), None)
                    self.assertTrue(entry, f"{rel} 的 {name} 自己反复排 requestAnimationFrame：要登记停下的条件（RAF_LOOPS）")
                    self.assertIn(entry[2], body, f"{rel} {name}")
                    found.add((rel, name))
        self.assertEqual(found, {(f, n) for f, n, _ in RAF_LOOPS}, "登记了却找不到")


class GateRunsTests(unittest.TestCase):
    """闸门：动画轮数同时长一样只能写 var()（globals.css 与组件的任意属性、内联 style）。"""

    def test_globals_runs_need_a_token(self):
        self.assertEqual(gate.css_value_problem("animation", "cc-tail var(--cc-mascot-tail-dur) var(--cc-mascot-ease) 3"), "3")
        self.assertEqual(gate.css_value_problem("animation-iteration-count", "2"), "2")
        self.assertEqual(gate.css_value_problem("animation-iteration-count", "1 !important"), "1")
        self.assertEqual(gate.css_value_problem("animation", "cc-tail var(--cc-mascot-tail-dur) var(--cc-mascot-ease) var(--cc-mascot-tail-runs)"), "")
        self.assertEqual(gate.css_value_problem("animation-iteration-count", "var(--cc-reduced-runs) !important"), "")
        self.assertEqual(gate.css_value_problem("animation", "cc-spin var(--cc-spin-dur) var(--cc-spin-ease) infinite"), "")
        self.assertEqual(gate.css_value_problem("animation", "cc-spin-dot var(--cc-dots-dur) var(--cc-ease) infinite"), "")
        self.assertEqual(gate.css_value_problem("animation-iteration-count", "0"), "")

    def test_component_runs_need_a_token(self):
        std = gate.Standard(CSS_PATH.read_text(encoding="utf-8"))
        self.assertTrue(any(r == "R8" for r, _ in gate.check_class("[animation-iteration-count:3]", std)))
        self.assertFalse(gate.check_class("[animation-iteration-count:var(--cc-reduced-runs)]", std))
        self.assertEqual(gate.style_value_problem("animationIterationCount", "3"), "3")
        self.assertEqual(gate.style_value_problem("animationIterationCount", '"2"'), "2")
        self.assertEqual(gate.style_value_problem("animation", '"cc-tail var(--cc-mascot-tail-dur) 3"'), "3")
        self.assertEqual(gate.style_value_problem("animationIterationCount", '"var(--cc-reduced-runs)"'), "")

    def test_current_globals_pass(self):
        css = CSS_PATH.read_text(encoding="utf-8")
        std = gate.Standard(css)
        self.assertEqual([f for f in gate.check_globals_strict(css, std) if f[1] == "R13"], [])


if __name__ == "__main__":
    unittest.main()
