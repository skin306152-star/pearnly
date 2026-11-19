"""库存只到「物料 × 仓库」的存量迁移（11-19，补丁 `v1_33_warehouse_only_stock`）：内存替身，不连库。

- 带过仓位的「物料 × 仓库」按流水重放成一行 ``BAL-物料-仓库``，删掉旧的 ``BAL-<sha>``；合并前后每个「物料 × 仓库」合计数量相等
  （同仓不同仓位之间的挪动正负抵消）；
- 非盘点表的仓位清空、盘点的表不动；
- 可重跑：第二次什么都不动。
"""
import hashlib
import importlib.util
import pathlib
import random
import sys
import types
import unittest
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Site:
    """一个站点的四样东西：结存行、流水、各表上的仓位列、盘点记录（不该动）。"""

    def __init__(self, contracts):
        self.contracts = contracts
        self.balances = {}   # name -> {name, product, warehouse, location, qty_on_hand, ...}
        self.movements = []  # {product, warehouse, location, qty_base, unit_cost, cost_known, posting_date, posting_time, name}
        self.columns = {}    # (doctype, column) -> [values]
        self.count_entries = [{"location": "A-01"}, {"location": "B-02"}]
        self.replayed = []

    def move(self, product, warehouse, location, qty, cost=None, day="2026-10-01", seq=None):
        seq = len(self.movements) if seq is None else seq
        self.movements.append({"product": product, "warehouse": warehouse, "location": location, "qty_base": Decimal(qty),
                               "unit_cost": Decimal(cost or 0), "cost_known": int(cost is not None),
                               "movement_type": "adjust", "posting_date": day, "posting_time": f"{seq:06d}",
                               "name": f"STM-{seq:05d}"})

    def book_old_balances(self):
        """11-19 以前的引擎：每个「物料 × 仓库 × 仓位」各滚一行结存（名字照旧口径，带仓位的是 ``BAL-<sha>``）。"""
        groups = {}
        for row in self.movements:
            groups.setdefault((row["product"], row["warehouse"], row["location"]), []).append(row)
        for (product, warehouse, location), rows in groups.items():
            balance, _rows = self.contracts.roll(self.contracts.ZERO_BALANCE, sorted(rows, key=self.order))
            name = self.contracts.legacy_position_name("BAL", product, warehouse, location)
            self.balances[name] = {"name": name, "product": product, "warehouse": warehouse, "location": location,
                                   "qty_on_hand": balance.qty}

    @staticmethod
    def order(row):
        return (row["posting_date"], row["posting_time"], row["name"])

    def totals(self):
        out = {}
        for row in self.balances.values():
            key = (row["product"], row["warehouse"])
            out[key] = out.get(key, Decimal(0)) + Decimal(row["qty_on_hand"])
        return out

    # ── 补丁用到的 frappe / stock 接口 ──
    def has_column(self, doctype, column):
        return doctype != "ERP Product" or column == "default_location"

    def get_all(self, doctype, fields=None, **kwargs):
        assert doctype == "ERP Stock Balance", doctype
        return [{key: row.get(key) for key in fields} for row in self.balances.values()]  # frappe._dict：按键取

    def sql(self, query, *args):
        table, column = query.split("`tab")[1].split("`")[0], query.split("set `")[1].split("`")[0]
        assert "Count" not in table, "盘点的表不动"
        if table == "ERP Stock Movement" and column == "location":
            for row in self.movements:
                row["location"] = ""
        if table == "ERP Stock Balance" and column == "location":
            for row in self.balances.values():
                row["location"] = ""
        self.columns.setdefault((table, column), []).append(query)

    def delete(self, doctype, filters):
        assert doctype == "ERP Stock Balance"
        self.balances.pop(filters["name"])

    def replay(self, product, warehouse):
        rows = sorted((row for row in self.movements if row["product"] == product and row["warehouse"] == warehouse),
                      key=self.order)
        balance, _rows = self.contracts.roll(self.contracts.ZERO_BALANCE, rows)
        name = self.contracts.position_name("BAL", product, warehouse)
        self.balances[name] = {"name": name, "product": product, "warehouse": warehouse, "location": "",
                               "qty_on_hand": balance.qty}
        self.replayed.append((product, warehouse))


class Migration(unittest.TestCase):
    def setUp(self):
        self.modules = dict(sys.modules)
        self.contracts = load("pearnly_erp_portal.inventory.contracts", APP / "inventory/contracts.py")
        self.site = Site(self.contracts)
        frappe = types.ModuleType("frappe")
        frappe.get_all = self.site.get_all
        frappe.db = types.SimpleNamespace(has_column=self.site.has_column, sql=self.site.sql, delete=self.site.delete,
                                          commit=lambda: None)
        inventory = types.ModuleType("pearnly_erp_portal.inventory")
        inventory.contracts = self.contracts
        inventory.stock = types.SimpleNamespace(lock_products=lambda products: None, replay_balance=self.site.replay)
        package = types.ModuleType("pearnly_erp_portal")
        package.inventory = inventory
        sys.modules.update({"frappe": frappe, "pearnly_erp_portal": package, "pearnly_erp_portal.inventory": inventory,
                            "pearnly_erp_portal.inventory.stock": inventory.stock})
        self.patch = load("v1_33_warehouse_only_stock", APP / "patches/v1_33_warehouse_only_stock.py")

    def tearDown(self):
        sys.modules.clear()
        sys.modules.update(self.modules)

    def test_located_balances_merge_into_one_row_per_product_and_warehouse_with_the_same_total(self):
        site = self.site
        site.move("P1", "W1", "", 10, 5)
        site.move("P1", "W1", "A-01", 4, 6)
        site.move("P1", "W1", "A-01", -1)
        site.move("P1", "W1", "B-02", 3, 7)
        site.move("P1", "W1", "A-01", -2)       # 同仓 A-01 → B-02 挪 2：一减一加
        site.move("P1", "W1", "B-02", 2)
        site.move("P1", "W2", "A-01", 8, 9)     # 别的仓同名仓位：各算各的仓
        site.move("P2", "W1", "", 5, 1)         # 从来没有仓位的：名字本来就对，不动
        site.book_old_balances()
        before = site.totals()
        self.assertEqual(len(site.balances), 5)
        self.patch.execute()
        after = site.totals()
        self.assertEqual(after, before)
        self.assertEqual(sorted(site.balances), ["BAL-P1-W1", "BAL-P1-W2", "BAL-P2-W1"])
        self.assertEqual(site.balances["BAL-P1-W1"]["qty_on_hand"], Decimal(16))
        self.assertEqual(sorted(site.replayed), [("P1", "W1"), ("P1", "W2")])
        self.assertTrue(all(not row["location"] for row in site.movements))
        self.assertEqual(site.count_entries, [{"location": "A-01"}, {"location": "B-02"}])  # 盘点记录的实际仓位不动
        cleared = {table for table, _column in site.columns}
        self.assertTrue({"ERP Stock Movement", "ERP Stock Opening", "ERP Serial No", "ERP Line Item Memory",
                         "Purchase Line", "ERP Sales Document Line", "ERP Stock Entry Line", "ERP Product"} <= cleared)

    def test_a_second_run_changes_nothing(self):
        site = self.site
        site.move("P1", "W1", "A-01", 4, 6)
        site.move("P1", "W1", "B-02", 3, 7)
        site.book_old_balances()
        self.patch.execute()
        state = {name: dict(row) for name, row in site.balances.items()}
        site.replayed.clear()
        self.patch.execute()
        self.assertEqual(site.replayed, [])
        self.assertEqual(site.balances, state)

    def test_random_ledgers_keep_every_product_and_warehouse_total(self):
        rng = random.Random(1119)
        for _round in range(30):
            self.site.__init__(self.contracts)
            for seq in range(rng.randint(1, 40)):
                self.site.move(rng.choice(["P1", "P2", "P3"]), rng.choice(["W1", "W2"]), rng.choice(["", "A", "B", "C-ã"]),
                               rng.randint(-5, 9), rng.choice([None, 3, 4]), day=f"2026-10-{rng.randint(1, 28):02d}", seq=seq)
            self.site.book_old_balances()
            before = self.site.totals()
            self.patch.execute()
            self.assertEqual(self.site.totals(), before)
            self.assertTrue(all(name == f"BAL-{row['product']}-{row['warehouse']}" for name, row in self.site.balances.items()))

    def test_the_pure_plan(self):
        sha = "BAL-" + hashlib.sha256("P1\0W1\0A".encode()).hexdigest()
        rows = [{"name": sha, "product": "P1", "warehouse": "W1", "location": "A"},
                {"name": "BAL-P1-W1", "product": "P1", "warehouse": "W1", "location": ""},
                {"name": "BAL-P2-W1", "product": "P2", "warehouse": "W1", "location": ""}]
        self.assertEqual(self.patch.merge_targets(rows), [("P1", "W1")])
        self.assertEqual(self.patch.stale_balances(rows), [sha])
        self.assertEqual(self.contracts.legacy_position_name("BAL", "P1", "W1", "A"), sha)
        self.assertEqual(self.contracts.position_name("BAL", "P1", "W1"), "BAL-P1-W1")

    def test_the_patch_is_registered_after_the_model_sync(self):
        lines = (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn("pearnly_erp_portal.patches.v1_33_warehouse_only_stock", lines)
        self.assertGreater(lines.index("pearnly_erp_portal.patches.v1_33_warehouse_only_stock"), lines.index("[post_model_sync]"))


if __name__ == "__main__":
    unittest.main()
