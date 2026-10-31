"""Offline tests for doctype_guard.py compare: no Frappe, no site."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

GUARD = Path(__file__).with_name('doctype_guard.py')


class Compare(unittest.TestCase):
    def compare(self, before, after, removals=None, noise=True):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'doctype_guard.py'
            script.write_text(GUARD.read_text())
            if removals is not None:
                (root / 'doctype-removals.txt').write_text('# deliberate\n' + '\n'.join(removals) + '\n')
            files = []
            for name, names in (('before', before), ('after', after)):
                path = root / name
                # 容器启动时可能先打印别的行，只认带标记的那一行
                path.write_text(('Linking fresh assets to volume...\n' if noise else '') + 'DOCTYPES ' + json.dumps(names) + '\n')
                files.append(str(path))
            return subprocess.run(['python3', str(script), 'compare', *files], text=True, capture_output=True, timeout=10)

    def test_same_or_more_doctypes_pass(self):
        self.assertEqual(self.compare(['A', 'B'], ['A', 'B']).returncode, 0)
        result = self.compare(['A', 'B'], ['A', 'B', 'C'], noise=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('2 before, 3 after', result.stdout)

    def test_missing_doctypes_stop_with_their_names(self):
        result = self.compare(['A', 'B', 'C'], ['A'])
        self.assertEqual(result.returncode, 86)
        self.assertIn('2 DocTypes disappeared during migration', result.stderr)
        self.assertIn('B, C', result.stderr)

    def test_registered_removals_do_not_count(self):
        self.assertEqual(self.compare(['A', 'B', 'C'], ['A', 'C'], removals=['B']).returncode, 0)
        self.assertEqual(self.compare(['A', 'B', 'C'], ['A'], removals=['B']).returncode, 86)

    def test_output_without_the_marker_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            empty = Path(folder) / 'x'
            empty.write_text('Linking fresh assets to volume...\n')
            result = subprocess.run(['python3', str(GUARD), 'compare', str(empty), str(empty)], text=True, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)

    def test_shipped_removal_register_has_no_entries_yet(self):
        lines = [line for line in GUARD.with_name('doctype-removals.txt').read_text().splitlines()
                 if line.strip() and not line.startswith('#')]
        self.assertEqual(lines, [])


if __name__ == '__main__':
    unittest.main()
