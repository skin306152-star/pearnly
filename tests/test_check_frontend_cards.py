"""scripts/check-frontend-cards.py 的自测：三条规则各拦得住、该放的放（11-16 独立前端验收）。

    python3 tests/test_check_frontend_cards.py
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_frontend_cards", ROOT / "scripts" / "check-frontend-cards.py")
cards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cards)


def run(files: dict[str, str]) -> list[tuple[str, int, str, str]]:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp)
        for rel, text in files.items():
            path = src / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return cards.scan(src)


class FrameRule(unittest.TestCase):
    def test_new_card_with_its_own_frame_fails(self):
        found = run({"components/tool-uis/new-card.tsx": '<div className="cc-steps-card cc-ui-questionnaire-card">x</div>'})
        self.assertEqual([(f[0], f[2]) for f in found], [("components/tool-uis/new-card.tsx", "C1")])

    def test_registered_frame_owners_pass(self):
        files = {rel: '<div className="cc-steps-card" />' for rel in cards.FRAME_OWNERS}
        self.assertEqual(run(files), [])

    def test_comment_mentioning_the_class_passes(self):
        self.assertEqual(run({"components/tool-uis/a.tsx": "// 外框同 cc-steps-card\n<CardFrame title={t} />"}), [])


class LoadingRule(unittest.TestCase):
    def test_loading_word_in_a_card_fails(self):
        found = run({"components/tool-uis/a.tsx": "<span><Spinner />{ui.loading}</span>"})
        self.assertEqual([f[2] for f in found], ["C2"])

    def test_loading_word_outside_cards_passes(self):
        self.assertEqual(run({"components/settings.tsx": "<span>{ui.loading}</span>"}), [])


class AppearanceRule(unittest.TestCase):
    def test_radius_and_color_on_a_standard_component_fail(self):
        found = run({"components/a.tsx": '<Button variant="ghost" className="justify-start rounded-none">x</Button>\n'
                                          '<Skeleton className="bg-surface h-(--cc-x)" />\n'
                                          '<ItemTitle className={cn("min-w-0", "text-danger")}>y</ItemTitle>'})
        self.assertEqual(sorted(f[3] for f in found),
                         sorted(["<Button className 「rounded-none」", "<Skeleton className 「bg-surface」", "<ItemTitle className 「text-danger」"]))

    def test_layout_classes_pass(self):
        self.assertEqual(run({"components/a.tsx": '<Button className="w-full justify-start gap-(--cc-space-8)" />'
                                                  '<Skeleton className="w-1/2" /><ItemTitle className="wrap-break-word" />'}), [])

    def test_tooltip_icon_button_and_plain_elements_pass(self):
        self.assertEqual(run({"components/a.tsx": '<TooltipIconButton className="cc-row-btn rounded-full bg-surface" />'
                                                  '<div className="rounded-md bg-surface" />'}), [])

    def test_library_and_kit_are_not_checked(self):
        self.assertEqual(run({"components/ui/button.tsx": '<Button className="rounded-none" />',
                              "kit/gallery.tsx": '<Badge className="bg-surface" />'}), [])

    def test_variant_prefixed_appearance_fails(self):
        found = run({"components/a.tsx": '<Badge className="max-md:rounded-full" />'})
        self.assertEqual([f[2] for f in found], ["C3"])


class RepoState(unittest.TestCase):
    def test_line_numbers_point_at_the_tag(self):
        found = run({"components/a.tsx": "\n\n<Badge className=\"rounded-full\" />"})
        self.assertEqual(found[0][1], 3)


if __name__ == "__main__":
    unittest.main()
