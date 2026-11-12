"""网页进程入口（ADR-028）：所有跑 Frappe 网页的运行定义都经 `pearnly_core.web.wsgi`，回退只改一行运行配置。

入口在运行定义（compose）与镜像里，实时源码盖不到，所以这里静态核对每一处；替换本身与 Frappe 原实现逐项一致的对比在
`tests/test_web_multipart.py`（要 Frappe，候选离线车道跑）。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = "${PEARNLY_WSGI_APP:-pearnly_core.web.wsgi:application}"


class Entry(unittest.TestCase):
    def test_every_production_web_service_runs_through_the_entry(self):
        compose = (ROOT / "infrastructure/production/compose.yml").read_text(encoding="utf-8")
        gunicorns = [line for line in compose.splitlines() if "bin/gunicorn" in line]
        self.assertEqual(len(gunicorns), 5, "x-web 与 billing / dms / erp / cowork 四个网页服务")
        for line in gunicorns:
            self.assertIn(f"'{ENTRY}'", line)
        self.assertNotIn("frappe.app:application", compose)

    def test_rollback_is_one_runtime_line(self):
        record = (ROOT / "docs/adr/0028-web-entry-multipart.md").read_text(encoding="utf-8")
        self.assertIn("PEARNLY_WSGI_APP=frappe.app:application", record)

    def test_local_stacks_serve_through_the_entry_too(self):
        for path in ("infrastructure/compose/regression-lab.yml", "scripts/scenarios/real_run_preflight.py"):
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn("bench serve --port", text, path)
            self.assertIn("-m pearnly_core.web.wsgi", text, path)
        for path in sorted((ROOT / "infrastructure/compose").glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"frappe\.app:application\b(?!_with)", text), path.name)

    def test_the_frappe_side_comparison_runs_in_the_candidate_lane(self):
        lane = (ROOT / "scripts/ci-candidate-checks.sh").read_text(encoding="utf-8")
        self.assertIn("tests/test_web_multipart.py", lane)


if __name__ == "__main__":
    unittest.main()
