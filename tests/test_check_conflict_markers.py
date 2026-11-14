"""冲突标记检查（scripts/check-conflict-markers.py）的离线单测：真冲突拦、文档分隔线不拦、二进制跳过、暂存快照与工作树各看各的。
标记都用拼出来的字符串，这个文件自己不带成组的标记。"""
import importlib.util
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("conflict_markers", ROOT / "scripts" / "check-conflict-markers.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
OURS, MIDDLE, THEIRS = "<" * 7, "=" * 7, ">" * 7
REAL = f"a = 1\n{OURS} HEAD\nb = 2\n{MIDDLE}\nb = 3\n{THEIRS} pearnly/topic\nc = 4\n"


class ConflictsTest(unittest.TestCase):
    def test_a_real_conflict_is_found_at_its_first_line(self):
        self.assertEqual(gate.conflicts(REAL.encode()), [2])
        self.assertEqual(gate.conflicts((REAL + REAL).encode()), [2, 9])
        self.assertEqual(gate.conflicts(REAL.replace("\n", "\r\n").encode()), [2])                      # Windows 换行
        self.assertEqual(gate.conflicts(f"{OURS} HEAD\nx\n||||||| base\ny\n{MIDDLE}\nz\n{THEIRS} 4ffe8ae (topic)\n".encode()), [1])  # diff3 写法

    def test_document_rules_and_lone_markers_pass(self):
        for text in (f"标题\n{MIDDLE}\n正文\n",                                    # markdown / rst 的等号分隔线
                     f"{'=' * 20}\n段落\n{'=' * 20}\n",                            # 更长的分隔线
                     f"{OURS}\n{MIDDLE}\n{THEIRS}\n",                              # 尖括号行没带分支 / 提交名
                     f"{OURS} HEAD\n只有开头\n",                                   # 不成组
                     f"{MIDDLE}\n{THEIRS} topic\n{OURS} HEAD\n",                   # 顺序不对
                     f"说明：冲突标记是 `{OURS} 分支名`、`{MIDDLE}`、`{THEIRS} 分支名`\n",   # 行内提到，不在行首
                     f"{OURS} HEAD\nx\n{MIDDLE} \ny\n{THEIRS} topic\n"):           # 等号行后面还有字
            self.assertEqual(gate.conflicts(text.encode()), [], text)

    def test_binary_files_are_skipped(self):
        self.assertEqual(gate.conflicts(b"\x89PNG\0\0" + REAL.encode()), [])


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.git("init", "-q")
        (self.root / "ok.md").write_text(f"标题\n{MIDDLE}\n", encoding="utf-8")
        self.git("add", "ok.md")
        self.git("commit", "-q", "-m", "base")

    def git(self, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.root, check=True, capture_output=True)

    def test_staged_mode_reads_the_index_and_tracked_mode_reads_the_worktree(self):
        self.assertEqual(gate.main(["--root", str(self.root)]), 0)
        (self.root / "bad.py").write_text(REAL, encoding="utf-8")
        self.assertEqual(gate.main(["--root", str(self.root)]), 0)             # 没跟踪的文件不看
        self.git("add", "bad.py")
        self.assertEqual(gate.check(gate.staged(self.root)), ["bad.py:2"])
        self.assertEqual(gate.main(["--root", str(self.root), "--staged"]), 1)
        (self.root / "bad.py").write_text("a = 1\n", encoding="utf-8")         # 工作树里改好了但没重新暂存：钩子看的是暂存快照
        self.assertEqual(gate.main(["--root", str(self.root), "--staged"]), 1)
        self.assertEqual(gate.main(["--root", str(self.root)]), 0)
        self.git("add", "bad.py")
        self.assertEqual(gate.main(["--root", str(self.root), "--staged"]), 0)

    def test_this_repository_has_none(self):
        self.assertEqual(gate.check(gate.tracked(ROOT)), [])


if __name__ == "__main__":
    unittest.main()
