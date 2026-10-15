"""Express 账套以网页选择为准（用户 2026-09-26）：电脑上报的可写账套都可作推送目标。

多台电脑（ADR-021 第 2 批）起，「这台电脑报过这份账套、可写、同一程序目录」读它的可达行（`pool.reaches`），不再读连接
上报记录里的单份名单。不连库、不依赖 Frappe（`cowork_pool_fake`）。
"""
import pathlib
import sys
import unittest
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cowork_pool_fake as fake  # noqa: E402

fake.install()

from pearnly_cowork.connections import model, pool  # noqa: E402
from pearnly_cowork.targets.identity import normalize_express_account_key as key  # noqa: E402

ROOT_67 = r"\\accserver\ExpressI\2567"
ROOT_68 = r"\\accserver\ExpressI\2568"
SETS = [{"code": "A68", "path": ROOT_68 + r"\ACME", "root": ROOT_68, "writable": True},
        {"code": "A67", "path": ROOT_67 + r"\ACME", "root": ROOT_67},
        {"code": "L68", "path": ROOT_68 + r"\LOCKED", "root": ROOT_68, "writable": False}]


class DynamicAccount(unittest.TestCase):
    def setUp(self):
        fake.DB.clear()
        doc = fake.Doc(doctype="Cowork ERP Connection", name="CONN", workspace="W", client="", adapter="express",
                       enabled=1, binding_generation=0, record_revision=1, projection_heads="{}", report_json="{}",
                       config_json=model.dumps({}))
        fake.DB.docs["Cowork ERP Connection"] = {"CONN": doc}
        fake.Computer(doc, "PC").report({"account_sets": SETS}, datetime(2026, 10, 16))

    def test_any_reported_writable_account_under_its_root_is_authorized(self):
        self.assertTrue(pool.reaches("CONN", "PC", key(ROOT_68 + r"\ACME"), key(ROOT_68)))
        self.assertTrue(pool.reaches("CONN", "PC", key(ROOT_67 + r"\ACME"), key(ROOT_67)))

    def test_unreported_locked_or_other_root_is_not(self):
        self.assertFalse(pool.reaches("CONN", "PC", key(ROOT_68 + r"\LOCKED"), key(ROOT_68)))
        self.assertFalse(pool.reaches("CONN", "PC", key(ROOT_68 + r"\OTHER"), key(ROOT_68)))
        self.assertFalse(pool.reaches("CONN", "PC", key(ROOT_68 + r"\ACME"), key(ROOT_67)))
        self.assertFalse(pool.reaches("CONN", "OTHER-PC", key(ROOT_68 + r"\ACME"), key(ROOT_68)))


if __name__ == "__main__":
    unittest.main()
