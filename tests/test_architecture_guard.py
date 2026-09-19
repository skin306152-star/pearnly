"""Prove that representative architecture regressions fail the offline gate."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-architecture.py"
spec = importlib.util.spec_from_file_location("architecture_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ArchitectureGuard(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.package = self.root / "apps/pearnly_dms/pearnly_dms"
        self.package.mkdir(parents=True)
        (self.package.parent / "pyproject.toml").write_text("")
        for name in ("__init__.py", "hooks.py", "modules.txt", "patches.txt"):
            (self.package / name).write_text("")
        (self.package / "contracts").mkdir()
        (self.package / "contracts/models.py").write_text("from dataclasses import dataclass\n")
        docs = self.root / "docs/development"
        docs.mkdir(parents=True)
        (docs / "architecture-policy.json").write_text(json.dumps({"root_modules": {"pearnly_dms": ["__init__.py", "hooks.py", "dms_models.py"]}}))
        (docs / "dms-module-map.json").write_text(json.dumps({"modules": {"pearnly_dms.dms_models": "pearnly_dms.contracts.models"}, "compatibility": {}}))

    def test_valid_domain_package(self):
        self.assertEqual(guard.check(self.root), [])

    def test_new_root_business_module_rejected(self):
        (self.package / "new_business.py").write_text("def create(): pass\n")
        self.assertTrue(any("domain package" in x for x in guard.check(self.root)))

    def test_pure_contract_cannot_import_framework(self):
        (self.package / "contracts/models.py").write_text("import frappe\n")
        self.assertTrue(any("pure and independent" in x for x in guard.check(self.root)))

    def test_internal_import_cannot_use_legacy_path(self):
        (self.package / "contracts/models.py").write_text("from pearnly_dms import dms_models\n")
        self.assertTrue(any("internal code" in x for x in guard.check(self.root)))

    def test_new_cross_domain_dependency_requires_review(self):
        policy = self.root / "docs/development/architecture-policy.json"
        data = json.loads(policy.read_text())
        data["dms_domain_dependencies"] = {"contracts": [], "editor": []}
        policy.write_text(json.dumps(data))
        (self.package / "contracts/models.py").write_text("from pearnly_dms.editor.api import create\n")
        self.assertTrue(any("domain dependency" in x for x in guard.check(self.root)))

    def test_compatibility_module_cannot_accumulate_business_logic(self):
        mapping = self.root / "docs/development/dms-module-map.json"
        data = json.loads(mapping.read_text())
        data["compatibility"] = {"pearnly_dms.dms_models": {"module": "pearnly_dms.contracts.models", "exports": ["Thing"]}}
        mapping.write_text(json.dumps(data))
        (self.package / "dms_models.py").write_text("from pearnly_dms.contracts.models import Thing\ndef new_business(): pass\n")
        self.assertTrue(any("compatibility entry" in x for x in guard.check(self.root)))


if __name__ == "__main__":
    unittest.main()
