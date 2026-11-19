"""检查脚本读 git 的文件清单时不转义中文文件名（11-19：产品地图闸门认不到写在中文文件名里的真机记录，ERP 界面窗口只好另存英文名）。

git 默认 `core.quotepath=true`，`diff --name-only` / `ls-files` 等把非 ASCII 路径写成 `"docs/work/\\344\\270\\255..."`，
按行读进来的名字和磁盘上的对不上。本仓规则：脚本里启动 git 一律带 `-c core.quotepath=off`（或用 `-z`）。

1. 全仓扫：Python 里 `["git", ...]` 都带 `-c core.quotepath=off`；shell / Node 里列文件名的 git 调用（`diff --name-only`、
   `ls-files`、`--name-status`）带它或 `-z`。
2. 真跑一遍：临时仓库里改一个中文文件名的文件，产品地图闸门的 `changed()` 取到的就是这个中文名。
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLAG = "core.quotepath=off"
LISTING = re.compile(r"\bgit\b[^\n]*?\b(ls-files|--name-only|--name-status)\b")


def tracked(*paths):
    out = subprocess.run(["git", "-c", FLAG, "ls-files", "-z", "--", *paths], cwd=ROOT, capture_output=True, check=True).stdout
    return [name for name in out.decode("utf-8").split("\0") if name]


class QuotePathEverywhere(unittest.TestCase):
    def test_python_git_calls_turn_quoting_off(self):
        bad = []
        for name in tracked("scripts", "tests", ".githooks"):
            if not name.endswith(".py"):
                continue
            text = (ROOT / name).read_text(encoding="utf-8")
            for match in re.finditer(r'\["git",([^\]\n]{0,60})', text):
                if f'"{FLAG}"' not in match.group(1):
                    line = text.count("\n", 0, match.start()) + 1
                    bad.append(f"{name}:{line}")
        self.assertEqual(bad, [], "启动 git 要带 -c core.quotepath=off（中文文件名否则被转义）：\n" + "\n".join(bad))

    def test_shell_and_node_file_listings_turn_quoting_off(self):
        bad = []
        for name in tracked("scripts", ".githooks", ".github/workflows"):
            path = ROOT / name
            if not (name.startswith(".githooks/") or path.suffix in (".sh", ".mjs", ".js", ".cjs", ".yml", ".yaml")):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if LISTING.search(line) and FLAG not in line and " -z" not in line and '"-z"' not in line:
                    bad.append(f"{name}:{number}: {line.strip()[:120]}")
        self.assertEqual(bad, [], "列文件名的 git 调用要带 -c core.quotepath=off 或 -z：\n" + "\n".join(bad))


class ChineseFileNameReadBack(unittest.TestCase):
    def test_product_map_gate_sees_the_chinese_name(self):
        spec = importlib.util.spec_from_file_location("check_product_map_quotepath", ROOT / "scripts" / "check-product-map.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *args: subprocess.run(["git", "-c", FLAG, "-c", "core.hooksPath=/dev/null", "-c", "user.name=t",
                                                "-c", "user.email=t@example.com", *args], cwd=repo, check=True, capture_output=True)
            git("init", "-q", "-b", "main")
            (repo / "a.txt").write_text("a\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-q", "-m", "base")
            record = repo / "docs" / "work" / "2026-11-19-界面三项-真机记录.md"
            record.parent.mkdir(parents=True)
            record.write_text("记录\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-q", "-m", "record")
            (repo / "新建-未跟踪.md").write_text("x\n", encoding="utf-8")
            names = module.changed("main~1", root=repo)
        self.assertIn("docs/work/2026-11-19-界面三项-真机记录.md", names)
        self.assertIn("新建-未跟踪.md", names)
        self.assertFalse(any("\\" in name or name.startswith('"') for name in names), names)


if __name__ == "__main__":
    unittest.main()
