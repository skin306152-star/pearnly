"""回滚审计的守门（11-19：点停止写下的停止时刻、任务上的 stop、Cowork 判卡片前落到单据上的分组，都被后面一句不在 except 里的
``frappe.db.rollback()`` 整笔冲掉、不报错）。

1. 共用件 `pearnly_core.transaction.snapshot`：调用方有没提交的写，结束读快照之前记一条点名的错误日志并报错（不回滚、不替它提交）；
   没有就只回滚。
2. 全仓（apps 下、不含测试与补丁）不在 ``except`` 里的裸 ``frappe.db.rollback()`` 只许出现在下面登记过的地方（11-19 逐处审过：
   调用方此前已提交 / 后台任务开头 / 只撤销自己这一步）；新加的要么改走 ``snapshot.fresh`` / ``snapshot.require_clean``
   （同一函数里先调过它的不算裸回滚），要么在这里登记理由。登记只许减少：登记了却已不在的一并报出来删掉。

    python3 tests/test_snapshot_reset.py
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "apps"
SNAPSHOT = APPS / "pearnly_core/pearnly_core/transaction/snapshot.py"
GUARDS = ("snapshot.fresh", "snapshot.require_clean")

# (文件, 函数) → (条数, 理由)。逐处审过的结论（11-19 回滚审计）。
REGISTERED = {
    ("pearnly_control/pearnly_control/account/member_api.py", "execute"): (1, "失败分支撤销这次执行；要留的意图行服务里已先提交"),
    ("pearnly_core/pearnly_core/assistant/titles.py", "_finish"): (1, "后台取标题：领取已提交，失败日志随即提交"),
    ("pearnly_core/pearnly_core/assistant/titles.py", "_gone"): (1, "后台取标题出错分支，之前的都已提交"),
    ("pearnly_core/pearnly_core/assistant/webhook.py", "_gone"): (1, "后台出错分支，只撤销出错那一步自己的写"),
    ("pearnly_core/pearnly_core/cc_desktop/machine.py", "_login"): (1, "此前只有锁定读，回滚只放锁"),
    ("pearnly_core/pearnly_core/execution/work.py", "_finish"): (1, "紧跟在本函数自己的提交之后"),
    ("pearnly_core/pearnly_core/execution/work.py", "run"): (3, "后台任务开头 / 本函数分段提交之后"),
    ("pearnly_core/pearnly_core/execution/work.py", "wake"): (1, "紧跟在本函数自己的提交之后"),
    ("pearnly_core/pearnly_core/transaction/snapshot.py", "fresh"): (1, "共用件本身：先落下调用方的写再回滚"),
    ("pearnly_cowork/pearnly_cowork/assistant/into_document.py", "run"): (1, "紧跟在本函数自己的提交之后"),
    ("pearnly_cowork/pearnly_cowork/assistant/precheck_source.py", "search"): (1, "只读接口开头"),
    ("pearnly_cowork/pearnly_cowork/assistant/tools.py", "_refresh_account"): (1, "紧跟在本函数自己的提交之后"),
    ("pearnly_cowork/pearnly_cowork/assistant/tools.py", "_settle"): (1, "轮询前已提交"),
    ("pearnly_cowork/pearnly_cowork/assistant/tools.py", "mrerp_setup"): (1, "紧跟在本函数自己的提交之后"),
    ("pearnly_cowork/pearnly_cowork/assistant/tools.py", "recognize_attachments"): (1, "此前每笔写都已提交"),
    ("pearnly_cowork/pearnly_cowork/assistant/tools.py", "refresh_account_sets"): (1, "紧跟在本函数自己的提交之后"),
    ("pearnly_cowork/pearnly_cowork/assistant/tools.py", "save_folder_tasks"): (1, "整批保存失败时撤销自己这一批"),
    ("pearnly_cowork/pearnly_cowork/assistant/guided/stocktake.py", "_refused"): (1, "盘点引导确定时的出错分支，只撤销失败那一步自己的写（引擎调确定前已提交「已确定」，guide.run.confirm）"),
    ("pearnly_cowork/pearnly_cowork/companion/transport.py", "_claim_mrerp"): (1, "回到保存点"),
    ("pearnly_cowork/pearnly_cowork/connections/mrerp_menu_reader.py", "run"): (1, "后台任务开头"),
    ("pearnly_cowork/pearnly_cowork/folder_intake/rules.py", "save_many"): (1, "回到保存点"),
    ("pearnly_cowork/pearnly_cowork/push/application/assess.py", "run"): (1, "有意撤销自己的试算；调用方都已先提交"),
    ("pearnly_cowork/pearnly_cowork/push/application/line_items.py", "rows"): (1, "调用方已提交（LINE 会话写入自己提交）"),
    ("pearnly_cowork/pearnly_cowork/push/application/line_items.py", "search"): (1, "只读开头"),
    ("pearnly_cowork/pearnly_cowork/push/application/service.py", "_choices"): (1, "调用方都已先提交"),
    ("pearnly_cowork/pearnly_cowork/push/application/service.py", "submission_failed"): (1, "调用方先提交或已回滚"),
    ("pearnly_cowork/pearnly_cowork/push/mrerp/runner.py", "run"): (1, "后台任务，只撤销自己的领取"),
    ("pearnly_cowork/pearnly_cowork/readback/ledger_check.py", "check"): (1, "紧跟在本函数自己的提交之后"),
    ("pearnly_erp_portal/pearnly_erp_portal/collection/cc_tools.py", "_fresh_invoices"): (1, "每次之前已提交"),
    ("pearnly_erp_portal/pearnly_erp_portal/collection/cc_tools.py", "_refused"): (1, "出错分支，撤销失败那一步自己的写"),
    ("pearnly_erp_portal/pearnly_erp_portal/collection/cc_tools.py", "_settle"): (1, "轮询前已提交"),
    ("pearnly_erp_portal/pearnly_erp_portal/collection/mrerp.py", "_claim"): (1, "后台任务开头，领不到只放锁"),
    ("pearnly_erp_portal/pearnly_erp_portal/express/cc_tools.py", "_settle"): (1, "轮询前已提交"),
    ("pearnly_erp_portal/pearnly_erp_portal/express/ledger_check.py", "check"): (1, "紧跟在本函数自己的提交之后"),
    ("pearnly_erp_portal/pearnly_erp_portal/express/push.py", "_send"): (1, "只在中途已提交之后撤销这之后自己的写"),
    ("pearnly_erp_portal/pearnly_erp_portal/integrations/intake.py", "_read_chat"): (1, "执行器已提交租约，此前只读"),
    ("pearnly_erp_portal/pearnly_erp_portal/mrerp/push.py", "_claim"): (1, "后台任务开头"),
}


def _is_rollback(node) -> bool:
    return isinstance(node, ast.Call) and ast.unparse(node.func) == "frappe.db.rollback"


def bare_rollbacks() -> dict:
    """(文件, 函数) → 不在 except 里、本函数此前也没先经共用件的裸回滚条数。"""
    found: dict = {}
    for path in sorted(APPS.glob("**/*.py")):
        text = str(path)
        if "/tests/" in text or "/patches/" in text or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = str(path.relative_to(APPS))

        def walk(node, function, in_except, guarded):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(child, child.name, False, False)
                    continue
                if isinstance(child, ast.ExceptHandler):
                    walk(child, function, True, guarded)
                    continue
                if isinstance(child, ast.Call) and ast.unparse(child.func).endswith(GUARDS):
                    guarded[0] = True
                if _is_rollback(child) and function and not in_except and not guarded[0]:
                    found[(rel, function)] = found.get((rel, function), 0) + 1
                walk(child, function, in_except, guarded)

        for top in ast.iter_child_nodes(tree):
            if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(top, top.name, False, [False])
            elif isinstance(top, ast.ClassDef):
                for item in top.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        walk(item, item.name, False, [False])
    return found


def _load_snapshot(db):
    frappe = types.ModuleType("frappe")
    frappe.db = db
    logged = []
    frappe.log_error = lambda **kw: logged.append(kw)
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("snapshot_under_test", SNAPSHOT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is not None:
            sys.modules["frappe"] = saved
        else:
            sys.modules.pop("frappe", None)
    return module, logged


class _Db:
    def __init__(self, writes):
        self.transaction_writes = writes
        self.steps = []

    def commit(self):
        self.steps.append("commit")
        self.transaction_writes = 0

    def rollback(self):
        self.steps.append("rollback")
        self.transaction_writes = 0


class Helper(unittest.TestCase):
    def test_pending_caller_writes_are_refused_and_named_without_rollback_or_commit(self):
        db = _Db(writes=2)
        snapshot, logged = _load_snapshot(db)
        for call in (snapshot.fresh, snapshot.require_clean):
            with self.assertRaises(snapshot.PendingWrites) as raised:
                call("toolrun.cancel_waiting")
            self.assertIn("toolrun.cancel_waiting", str(raised.exception))
        self.assertEqual(db.steps, [])  # 不回滚、不替调用方提交
        self.assertEqual(db.transaction_writes, 2)
        self.assertEqual(len(logged), 2)
        self.assertIn("toolrun.cancel_waiting", logged[0]["message"])

    def test_nothing_pending_only_rolls_back_and_logs_nothing(self):
        db = _Db(writes=0)
        snapshot, logged = _load_snapshot(db)
        snapshot.fresh("waits.sweep")
        snapshot.require_clean("precheck_source.document")
        self.assertEqual((db.steps, logged), (["rollback"], []))


class Registered(unittest.TestCase):
    def test_every_bare_rollback_is_registered_with_a_reason(self):
        found = bare_rollbacks()
        unknown = {key: count for key, count in found.items() if key not in REGISTERED}
        self.assertFalse(unknown, "bare frappe.db.rollback() outside except: call snapshot.fresh / snapshot.require_clean "
                                  f"first, or register it here with the reason: {unknown}")
        grown = {key: (count, REGISTERED[key][0]) for key, count in found.items()
                 if key in REGISTERED and count > REGISTERED[key][0]}
        self.assertFalse(grown, f"more bare rollbacks than registered: {grown}")

    def test_the_register_only_shrinks(self):
        found = bare_rollbacks()
        stale = {key: value[0] for key, value in REGISTERED.items() if found.get(key, 0) < value[0]}
        self.assertFalse(stale, f"registered but gone (lower the count or drop the line): {stale}")
        self.assertTrue(all(reason for _count, reason in REGISTERED.values()))


if __name__ == "__main__":
    unittest.main()
