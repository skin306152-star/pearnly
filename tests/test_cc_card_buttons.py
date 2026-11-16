"""对话里的卡只有一套骨架与按钮（用户 11-16：「同一套，不许两套」，代录卡与收款卡按钮不一样被点名）。

照线上已在用的允许卡 / 代录卡定的规则（frontend/assistant/src/components/tool-uis/card-frame.tsx 文件头）：
- 卡片按钮只用标准 Button 的 size="xs"（外观 .cc-card-btn：电脑 24、触屏 44 胶囊），两档：主按钮默认样式、次按钮 variant="secondary"；
  卡片里不许用 ghost / outline / destructive 的 xs 按钮自造第三种样子；
- 卡片脚（按钮那一行）只经 CardFoot / CardFrame（card-frame.tsx）、允许卡 ApprovalBar、提问卡 QuestionnaireActions 画，
  别的文件不许自己写 .cc-card-actions 一行；手写 <button className="cc-card-btn"> 只许存量减少；
- 一张卡最多一个主按钮，次按钮在左、主按钮在右（CardFoot 按这个顺序画）。

    python3 tests/test_cc_card_buttons.py
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend/assistant/src"
CARDS = sorted([*(SRC / "components/tool-uis").glob("*.tsx"), SRC / "components/pending.tsx"])

# 手写卡片按钮的存量（11-16）：只许减少
RAW_BASELINE = {"components/tool-uis/approval-sheet.tsx": 2, "components/tool-uis/operator-start.tsx": 3, "components/pending.tsx": 4}
# 自己画按钮行的文件（骨架本身、允许卡、允许卡的手机浮层、代录开始卡旧卡——A4 上线时随旧卡删掉）
FOOT_OWNERS = {"components/tool-uis/card-frame.tsx", "components/tool-uis/approval-bar.tsx", "components/tool-uis/approval-sheet.tsx",
               "components/tool-uis/operator-start.tsx"}

BUTTON = re.compile(r"<Button\b([^>]*)>", re.S)


def rel(path: Path) -> str:
    return str(path.relative_to(SRC))


class CardButtonTests(unittest.TestCase):
    def test_card_buttons_are_the_two_kinds_of_xs(self):
        bad = []
        for path in CARDS:
            for m in BUTTON.finditer(path.read_text(encoding="utf-8")):
                attrs = m.group(1)
                if 'size="xs"' not in attrs:
                    continue
                variant = re.search(r'variant=(?:"([^"]+)"|\{)', attrs)
                kind = variant.group(1) if variant and variant.group(1) else ("expr" if variant else "default")
                if kind not in ("default", "secondary", "expr"):
                    bad.append(f"{rel(path)}: variant={kind}")
        self.assertEqual(bad, [], "卡片按钮只有主 / 次两档（Button size=xs，默认或 secondary），不自造 ghost / outline 的样子")

    def test_only_the_shared_foot_draws_the_button_row(self):
        owners = sorted(rel(p) for p in CARDS if "cc-card-actions" in p.read_text(encoding="utf-8") and rel(p) not in FOOT_OWNERS)
        self.assertEqual(owners, [], "按钮那一行用 card-frame.tsx 的 CardFoot / CardFrame（或允许卡、提问卡），不在卡里自己写 .cc-card-actions")

    def test_hand_written_card_buttons_only_shrink(self):
        for path in CARDS:
            count = len(re.findall(r'className="cc-card-btn', path.read_text(encoding="utf-8")))
            self.assertLessEqual(count, RAW_BASELINE.get(rel(path), 0), f"{rel(path)}：手写 cc-card-btn 只许减少，新卡用 Button size=xs")

    def test_the_foot_puts_the_one_primary_on_the_right(self):
        frame = (SRC / "components/tool-uis/card-frame.tsx").read_text(encoding="utf-8")
        self.assertIn("const ordered = [...buttons.filter((b) => !b.primary), ...buttons.filter((b) => b.primary)];", frame)
        self.assertIn('size="xs" variant={button.primary ? "default" : "secondary"}', frame)
        # 新做的引导卡（复核、结果、识别中、代录卡头）都用这一套骨架
        for name in ("guide-review.tsx", "guide-run-card.tsx"):
            self.assertIn('from "./card-frame"', (SRC / "components/tool-uis" / name).read_text(encoding="utf-8"), name)
        self.assertIn("<CardFoot", (SRC / "components/tool-uis/operator-card.tsx").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
