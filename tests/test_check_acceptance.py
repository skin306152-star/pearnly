"""验收登记检查（scripts/check-acceptance.py）：在临时仓库里核——引用对不上失败；按属性推出欠账，没场景又不在基线失败；
基线已还上的要删、只许减少（第一次标属性的存量行例外）；证据之后 covers 改了自动过期；上线闸口径；真仓库的登记过检查。"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_acceptance", ROOT / "scripts" / "check-acceptance.py")
acceptance = importlib.util.module_from_spec(spec)
sys.modules["check_acceptance"] = acceptance
spec.loader.exec_module(acceptance)

MAP = {
    "erp.purchase.submit": {"ends": ["desktop", "mobile"], "tests": ["flow:erp.purchase.create"], "product": "ERP", "kind": "page-action"},
    "erp.purchase.list": {"ends": ["desktop"], "tests": [], "product": "ERP", "kind": "nav-page"},
}
FLOWS = '[[flow]]\nid = "erp.purchase.create"\nmodule = "scripts/flows/x.py"\n'
SCENARIO = textwrap.dedent('''
    [[scenario]]
    id = "acc.purchase"
    title = "采购写进账本"
    class = "normal"
    layer = "L3"
    env = "B"
    module = "scripts/acceptance/scenarios/purchase.py"
    rows = ["erp.purchase.submit"]
    covers = ["apps/erp/push.py"]
    resources = ["testpc1", "ledger:69EXP/TEST2"]
    budget_usd = 0
    tier = "gate"
''')


class Repo:
    def __init__(self, case):
        self.tmp = tempfile.TemporaryDirectory()
        case.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.write("apps/erp/push.py", "x = 1\n")
        self.write("scripts/acceptance/scenarios/purchase.py", "def run(ctx): pass\n")
        self.write("tests/flows/registry.toml", FLOWS)
        self.write("tests/acceptance/owed_baseline.txt", "")
        self.commit("base")
        self.git("branch", "origin/main")  # 检查脚本拿它当分叉点

    def git(self, *args):
        return subprocess.run(["git", "-c", "core.quotepath=off", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.root, capture_output=True,
                              text=True, check=True).stdout.strip()

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(text), encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def registry(self):
        return acceptance.Registry(self.root, map_rows=MAP)

    def check(self):
        return acceptance.check(self.root, "origin/main", self.registry())[0]


class CheckAcceptanceTest(unittest.TestCase):
    def test_bad_references_are_reported(self):
        repo = Repo(self)
        repo.write("tests/acceptance/registry.toml", SCENARIO.replace('"erp.purchase.submit"', '"erp.gone"')
                   .replace("purchase.py", "missing.py").replace('"testpc1"', '"testpc9"').replace('"normal"', '"misuse"'))
        repo.write("tests/acceptance/rows.toml", '[row."erp.nope"]\neffect = ["write"]\n')
        problems = "\n".join(repo.registry().problems)
        for expected in ("erp.gone 不在产品地图", "模块不存在", "不认识的资源名 testpc9", "misuse 类场景要写 misuse",
                         "产品地图里没有这一行", "effect 必填"):
            self.assertIn(expected, problems)

    def test_owed_scenarios_fail_until_covered_or_baselined(self):
        repo = Repo(self)
        repo.write("tests/acceptance/rows.toml", '[row."erp.purchase.submit"]\neffect = ["site", "ledger"]\n')
        owed = sorted(need.partition("|")[2] for need in repo.registry().all_owed())
        # 流程已经给了「正常主线」和两套数据；真账本读回、串联和各种乱用都还欠着
        self.assertEqual(owed, ["L3:normal", "chain", "misuse:M02", "misuse:M03", "misuse:M04", "misuse:M07", "misuse:M08",
                                "misuse:M09", "misuse:M11"])
        self.assertEqual(len(repo.check()), 9)
        repo.write("tests/acceptance/registry.toml", SCENARIO)
        self.assertNotIn("erp.purchase.submit|L3:normal", repo.registry().all_owed())
        repo.write("tests/acceptance/owed_baseline.txt", "\n".join(sorted(repo.registry().all_owed())) + "\n")
        self.assertEqual(repo.check(), [])  # 第一次标属性的存量行可以进基线

    def test_api_only_scenario_proves_the_ledger_but_not_the_screen(self):
        repo = Repo(self)
        rows = dict(MAP, **{"erp.purchase.submit": dict(MAP["erp.purchase.submit"], tests=[])})
        repo.write("tests/acceptance/rows.toml", '[row."erp.purchase.submit"]\neffect = ["ledger"]\n')
        repo.write("tests/acceptance/registry.toml", SCENARIO.replace('env = "B"', 'env = "B"\nvia = "api"'))
        owed = acceptance.Registry(repo.root, map_rows=rows).all_owed()
        self.assertIn("erp.purchase.submit|normal", owed)
        self.assertNotIn("erp.purchase.submit|L3:normal", owed)

    def test_baseline_only_shrinks_and_paid_off_entries_must_go(self):
        repo = Repo(self)
        repo.write("tests/acceptance/rows.toml", '[row."erp.purchase.submit"]\neffect = ["site"]\nna = { "misuse:M11" = "只在电脑上用" }\n')
        repo.write("tests/acceptance/owed_baseline.txt", "erp.purchase.submit|misuse:M02\nerp.purchase.submit|misuse:M09\n")
        self.assertEqual(repo.check(), [])
        repo.commit("attrs")
        repo.git("branch", "-f", "origin/main")
        # 之后再给同一行加欠账（多标了 shared_doc）：不能靠往基线里加条目过去
        repo.write("tests/acceptance/rows.toml", '[row."erp.purchase.submit"]\neffect = ["site"]\nshared_doc = true\n'
                                                 'na = { "misuse:M11" = "只在电脑上用" }\n')
        repo.write("tests/acceptance/owed_baseline.txt",
                   "erp.purchase.submit|misuse:M02\nerp.purchase.submit|misuse:M09\nerp.purchase.submit|misuse:M10\n")
        self.assertTrue(any("基线只许减少" in line for line in repo.check()))
        # 还上了（na 或场景）却没从基线删
        repo.write("tests/acceptance/rows.toml", '[row."erp.purchase.submit"]\neffect = ["site"]\n'
                                                 'na = { "misuse:M11" = "只在电脑上用", "misuse:M09" = "这一页所有岗位都能提交" }\n')
        repo.write("tests/acceptance/owed_baseline.txt", "erp.purchase.submit|misuse:M02\nerp.purchase.submit|misuse:M09\n")
        self.assertTrue(any("已经不欠了" in line for line in repo.check()))

    def test_evidence_goes_stale_when_covered_files_change(self):
        repo = Repo(self)
        repo.write("tests/acceptance/registry.toml", SCENARIO)
        sha = repo.commit("scenario")
        self.assertEqual(repo.registry().freshness("acc.purchase")[0], "没有证据")
        repo.write("tests/acceptance/evidence.toml", f'[[pass]]\nscenario = "acc.purchase"\nsha = "{sha}"\ndate = "2026-11-12"\nrun = "r1"\n')
        repo.write("docs/readme.md", "不相干的改动\n")
        repo.commit("evidence")
        self.assertEqual(repo.registry().freshness("acc.purchase")[0], "已验")
        repo.write("apps/erp/push.py", "x = 2\n")
        repo.commit("touch covered file")
        state, why = repo.registry().freshness("acc.purchase")
        self.assertEqual(state, "过期")
        self.assertIn("apps/erp/push.py", why)
        repo.write("tests/acceptance/evidence.toml", '[[pass]]\nscenario = "acc.purchase"\nsha = "deadbeef1"\ndate = "2026-11-12"\nrun = "r1"\n')
        self.assertEqual(repo.registry().freshness("acc.purchase")[0], "过期")  # 提交不在仓库

    def test_evidence_with_a_content_digest_survives_a_rewritten_commit_and_goes_stale_on_content(self):
        repo = Repo(self)
        repo.write("tests/acceptance/registry.toml", SCENARIO)
        repo.write("apps/erp/push.py", "x = 1\n")
        sha = repo.commit("scenario")
        covers = repo.registry().scenarios["acc.purchase"]["covers"]
        digest = acceptance.covers_digest(repo.root, sha, covers)
        self.assertEqual(len(digest), 16)
        # 证据那次的提交号已经不在仓库（分支变基 / 合并后换了号）：内容没变就仍然算已验
        repo.write("tests/acceptance/evidence.toml",
                   f'[[pass]]\nscenario = "acc.purchase"\nsha = "deadbeef1"\ndate = "2026-11-14"\nrun = "r1"\ncovers = "{digest}"\n')
        repo.write("docs/readme.md", "不相干的改动\n")
        repo.commit("evidence")
        self.assertEqual(repo.registry().freshness("acc.purchase")[0], "已验")
        repo.write("apps/erp/push.py", "x = 2\n")
        repo.commit("touch covered file")
        state, why = repo.registry().freshness("acc.purchase")
        self.assertEqual(state, "过期")
        self.assertIn("内容指纹", why)
        self.assertIsNone(acceptance.covers_digest(repo.root, "deadbeef1", covers))

    def test_release_gate_lists_gate_scenarios_hit_without_fresh_evidence(self):
        repo = Repo(self)
        repo.write("tests/acceptance/registry.toml", SCENARIO)
        last_release = repo.commit("scenario")
        repo.write("docs/readme.md", "x\n")
        repo.commit("docs only")
        self.assertEqual(acceptance.gate(repo.registry(), last_release), ([], False))
        repo.write("apps/erp/push.py", "x = 3\n")
        repo.commit("product change")
        lines, blocks = acceptance.gate(repo.registry(), last_release)
        self.assertEqual(len(lines), 1)
        self.assertIn("没有证据", lines[0])
        self.assertFalse(blocks)
        repo.write("tests/acceptance/rows.toml", "[policy]\ngate_blocks = true\n")
        self.assertTrue(acceptance.gate(repo.registry(), last_release)[1])

    def test_ledger_rows_point_at_the_required_fields_table_once_it_exists(self):
        repo = Repo(self)
        repo.write("tests/acceptance/rows.toml", '[row."erp.purchase.submit"]\neffect = ["ledger"]\nfields = "express:RR"\n')
        self.assertIsNone(repo.registry().field_keys)   # 字段表还没建：不校验
        repo.write(acceptance.FIELDS_SOURCE, 'REQUIRED_FIELDS = {"express:HP": [{"field": "DOCNUM", "auto": "isrun"}]}\n')
        self.assertTrue(any("express:RR 不在必填字段登记表" in line for line in repo.registry().problems))
        repo.write(acceptance.FIELDS_SOURCE, 'REQUIRED_FIELDS = {"express:RR": [], "express:HP": []}\n')
        self.assertEqual(repo.registry().problems, [])
        repo.write("tests/acceptance/rows.toml", '[row."erp.purchase.submit"]\neffect = ["ledger"]\n')
        problems, notes = acceptance.check(repo.root, "origin/main", repo.registry())
        self.assertTrue(any("fields" in line for line in notes))      # 开关没开：只提示
        repo.write("tests/acceptance/rows.toml", '[policy]\nenforce_fields = true\n[row."erp.purchase.submit"]\neffect = ["ledger"]\n')
        problems, notes = acceptance.check(repo.root, "origin/main", repo.registry())
        self.assertTrue(any("fields" in line for line in problems))   # 开关开了：不通过

    def test_unmapped_high_risk_entries_owe_scenarios_like_rows(self):
        repo = Repo(self)
        repo.write("tests/acceptance/unmapped.toml", """
            [[entry]]
            id = "control.topup.approve"
            block = "管理后台"
            what = "充值审核到账"
            where = "apps/pearnly_control/x.py:1"
            effect = ["money", "site"]
            source = "control §A"
        """)
        registry = repo.registry()
        self.assertEqual(registry.problems, [])
        owed = sorted(item.partition("|")[2] for item in registry.all_owed() if item.startswith("control.topup.approve|"))
        self.assertEqual(owed, ["misuse:M02", "misuse:M09", "misuse:M15", "normal", "variant:new+old"])
        repo.write("tests/acceptance/unmapped.toml", '[[entry]]\nid = "erp.purchase.submit"\nblock = "x"\n')
        self.assertTrue(any("已经有这一行" in line for line in repo.registry().problems))

    def test_real_registry_passes_and_report_prints(self):
        registry = acceptance.Registry(ROOT)
        self.assertEqual(registry.problems, [])
        self.assertEqual(sorted(registry.all_owed() - registry.baseline), [])
        self.assertIn("验收总览", acceptance.report(registry))


if __name__ == "__main__":
    unittest.main()
