#!/usr/bin/env python3
"""CC 对话卡片源码闸门：卡片只有一套外框、卡里不写「正在加载」、标准组件不在调用处改外观（2026-11-16 独立前端验收）。

起因：11-16 用户看截图发现代录卡与收款卡「按钮和组件不是一套」，并批评「我之前说过前端一些相关要求…我看不到你们就装眼瞎」。
独立验收逐条对照用户要求（~/pearnly-devtools/frontend-audit-1116/checklist.md）后，把能从源码判定的三条做成机器检查；
按钮只有主 / 次两档、按钮行只经 CardFoot 由 tests/test_cc_card_buttons.py 管，渲染后的样子（四语溢出、截断、触屏快捷键、
标题栏、推荐项排第一）由 scripts/check-cc-card-render.mjs 在预览页上量。

判据（frontend/assistant/src 下 .tsx；注释不算；components/ui/ 与 kit/ 不查）：

  C1 卡片外框：对话卡片的外框类 cc-steps-card 只在登记的外框件里写（FRAME_OWNERS：骨架 card-frame、提问卡 ask-card、
     允许卡 steps、代录卡 operator-card、待处理卡 pending）。新卡用 card-frame.tsx 的 CardFrame，不再自己拼一层外框
     （B73，用户 11-13「两张卡必须同一种版式」、11-16「同一套，不许两套」）。
  C2 卡里不写「正在加载」：components/tool-uis/ 里不用 ui.loading。卡里等待用 Skeleton，等待的原因由服务端那一句说
     （B76，ccs.md 第 2 节「加载用骨架，不用转圈」；11-16 第 1 轮：转圈 +「正在加载」与服务端事实行重复）。
  C3 调用处不改外观：标准组件（Button、Item、Input、Badge、Card、Alert、Skeleton …）的 className 里不写外观类——
     圆角 rounded-*、底色 bg-*、阴影 shadow-*、边框色 border-<色>、字色 text-<色>、ring-*、opacity-*。
     外观改 globals.css 或换 variant（B80，docs/development/cc-ui-kit.md 规则 4）。cc-* 外观类直挂由 check-frontend-kit.py 的 K4 管，这里不重复。
     TooltipIconButton 不算（ccs.md：它只是带提示框的普通 button，外观本来就由调用方的 cc-* 类给）。

没有基线、没有豁免：现有违规先修再合入（主会话 11-16）。

    python3 scripts/check-frontend-cards.py              # 检查；有违规就失败并逐处列出
    python3 scripts/check-frontend-cards.py --root DIR   # 检查别的 src 目录（测试用）

只读；不连网络。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "assistant" / "src"
SKIP_PREFIXES = ("components/ui/", "kit/")
SKIP_DIRS = {"node_modules", "dist", "build", ".vite"}

FRAME_OWNERS = {
    "components/tool-uis/card-frame.tsx",
    "components/tool-uis/ask-card.tsx",
    "components/tool-uis/steps.tsx",
    "components/tool-uis/operator-card.tsx",
    "components/pending.tsx",
}
CARD_DIR = "components/tool-uis/"

STANDARD = re.compile(
    r"(Button|Item\w*|Input|InputGroup\w*|Badge|Card\w*|Alert\w*|Textarea|NativeSelect\w*|Select\w*|Checkbox|Switch|Toggle\w*|"
    r"Tabs\w*|Table\w*|Dialog\w*|AlertDialog\w*|Sheet\w*|Popover\w*|DropdownMenu\w*|Tooltip|TooltipContent|TooltipTrigger|"
    r"Questionnaire\w*|Progress|Skeleton|Spinner|Separator|Kbd|Label|Field\w*|RadioGroup\w*|Empty\w*|Avatar\w*|Collapsible\w*|"
    r"Attachment\w*|Message\w*|Bubble\w*|Trace\w*|Marker\w*|ButtonGroup)"
)
TAG = re.compile(r"<([A-Z][\w.]*)\b([^>]*?)\bclassName=(\"[^\"]*\"|\{`[^`]*`\}|\{cn\((?:[^()]|\([^()]*\))*\)\})", re.S)
APPEARANCE = re.compile(
    r"(?<![\w:-])(?:[\w-]+:)*("
    r"rounded(?:-[\w()/.\[\]-]+)?|bg-[\w()/.\[\]-]+|shadow(?:-[\w()/.\[\]-]+)?|ring(?:-[\w()/.\[\]-]+)?|opacity-[\w()/.\[\]-]+|"
    r"border-(?:ink|line|danger|success|warning|brand|link|white|black|transparent|surface|\(--)[\w()/.\[\]-]*|"
    r"text-(?:ink|link|danger|success|warning|brand|white|black|muted|foreground)[\w()/.\[\]-]*"
    r")(?![\w-])"
)
FRAME_CLASS = re.compile(r"(?<![\w-])cc-steps-card(?![\w-])")
LOADING = re.compile(r"\bui\.loading\b")
COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)

RULES = {
    "C1": "卡片外框只在登记的外框件里写；新卡用 card-frame.tsx 的 CardFrame",
    "C2": "卡里不写「正在加载」（ui.loading）：等待用 Skeleton，原因由服务端那一句说",
    "C3": "标准组件不在调用处改外观（圆角 / 底色 / 阴影 / 边框色 / 字色 / ring / 透明度）：改 globals.css 或换 variant",
}


def strip_comments(text: str) -> str:
    """去掉注释（换成同样多的换行，行号不变）。字符串里的 // 很少见于 tsx 的 className，按注释处理可接受。"""
    return COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def scan(src: Path) -> list[tuple[str, int, str, str]]:
    found: list[tuple[str, int, str, str]] = []
    for path in sorted(src.rglob("*.tsx")):
        if SKIP_DIRS & set(path.parts):
            continue
        rel = path.relative_to(src).as_posix()
        if rel.startswith(SKIP_PREFIXES):
            continue
        text = strip_comments(path.read_text(encoding="utf-8"))
        if rel not in FRAME_OWNERS:
            for m in FRAME_CLASS.finditer(text):
                found.append((rel, line_of(text, m.start()), "C1", m.group(0)))
        if rel.startswith(CARD_DIR):
            for m in LOADING.finditer(text):
                found.append((rel, line_of(text, m.start()), "C2", m.group(0)))
        for m in TAG.finditer(text):
            name = m.group(1)
            if "." in name or not STANDARD.fullmatch(name):
                continue
            for bad in APPEARANCE.finditer(m.group(3)):
                found.append((rel, line_of(text, m.start()), "C3", f"<{name} className 「{bad.group(0)}」"))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=SRC, help="要检查的 src 目录")
    args = parser.parse_args(argv)
    found = scan(args.root)
    if not found:
        print("PASS: 卡片外框一套、卡里不写「正在加载」、标准组件不在调用处改外观")
        return 0
    print(f"FAIL: {len(found)} 处")
    for rule in RULES:
        rows = [f for f in found if f[2] == rule]
        if not rows:
            continue
        print(f"\n{rule} {RULES[rule]}（{len(rows)} 处）")
        for rel, line, _, text in rows:
            print(f"  {rel}:{line}  {text}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
