"""CC 标准组件库闸门（scripts/check-frontend-kit.py）：认得出手写的通用控件，组件库自己不算，基线只许减少。"""
import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_frontend_kit", ROOT / "scripts/check-frontend-kit.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = check.main(list(argv))
    return code, out.getvalue() + err.getvalue()


class FrontendKitGate(unittest.TestCase):
    def src(self, files):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = pathlib.Path(folder.name)
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return root

    def test_hand_written_controls_are_found(self):
        text = (
            'import { Dialog } from "radix-ui";\n'
            'import { Questionnaire } from "@shadcn/react/questionnaire";\n'
            '<button type="button" className="cc-btn">a</button>\n'
            '<input value={v} />\n'
            '<input\n  hidden\n  type="file"\n/>\n'
            '<select><option /></select>\n'
            '<textarea />\n'
            '<table className="cc-table" />\n'
            '<div role="switch" aria-checked />\n'
            '<span className="cc-card-btn" data-primary />\n'
        )
        hits = check.scan_text(text)
        rules = [r for _, r, _ in hits]
        self.assertEqual(rules.count("K1"), 5)  # button、input（选文件框不算）、select、textarea、table
        self.assertEqual(rules.count("K2"), 2)
        self.assertEqual(rules.count("K3"), 1)
        self.assertEqual(rules.count("K4"), 3)  # cc-btn、cc-table、cc-card-btn

    def test_comments_selectors_and_similar_names_are_not_controls(self):
        text = (
            "// <button className=\"cc-btn\"> 旧写法\n"
            "/* <table> */\n"
            'document.querySelector(\'[role="menu"]\');\n'
            "el.closest('.cc-btn');\n"
            '<div className="cc-empty-line cc-btn-group-x cc-menu-item cc-alert-box" />\n'
            'const url = "https://example.com//button";\n'
        )
        self.assertEqual(check.scan_text(text), [])

    def test_library_itself_is_not_scanned_and_baseline_only_shrinks(self):
        root = self.src({
            "components/ui/button.tsx": '<button className="cc-btn" />\n',
            "components/page.tsx": '<button className="cc-btn" />\n<button />\n',
        })
        self.assertEqual(run("--root", str(root), "--update")[0], 0)
        base = json.loads((root / "frontend-kit-baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(base, {"components/page.tsx": {"K1": 2, "K4": 1}})
        self.assertEqual(run("--root", str(root))[0], 0)

        # 新增一处：检查失败，--update 也拒绝
        (root / "components/page.tsx").write_text('<button className="cc-btn" />\n<button />\n<input />\n', encoding="utf-8")
        code, out = run("--root", str(root))
        self.assertEqual(code, 1)
        self.assertIn("components/ui", out)
        self.assertEqual(run("--root", str(root), "--update")[0], 1)
        # 新文件同样不许手写
        (root / "components/page.tsx").write_text('<button className="cc-btn" />\n<button />\n', encoding="utf-8")
        (root / "components/new.tsx").write_text("<textarea />\n", encoding="utf-8")
        self.assertEqual(run("--root", str(root))[0], 1)
        (root / "components/new.tsx").unlink()

        # 换成组件库：减少照过，可以把基线降下来
        (root / "components/page.tsx").write_text('import { Button } from "@/components/ui/button";\n<Button />\n<button />\n',
                                                  encoding="utf-8")
        code, out = run("--root", str(root))
        self.assertEqual(code, 0)
        self.assertIn("--update", out)
        self.assertEqual(run("--root", str(root), "--update")[0], 0)
        base = json.loads((root / "frontend-kit-baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(base, {"components/page.tsx": {"K1": 1}})

    def test_repository_is_within_its_baseline(self):
        code, out = run()
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
