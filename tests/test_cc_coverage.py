"""CC 覆盖总表闸（引导侧）：一份数据、总表由它生成、与引导声明对得上（``scripts/check-cc-coverage.py``；总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 第 4 节，P0 第 1 个提交）。

断言的是应有状态：
- 一格只有六种写法，范围外必须写依据；
- 标「做完 / 做了一半」的格，这个产品这个模式必须声明了这项业务；「做完」不能是只有开场一句的引导，也要面向那种账本；
  声明了的格不许标范围外；
- 仓库里的数据过闸，总表文件里的那一段与数据生成的一致；
- 11-22 起总表跟着证据走、双向拦：声明了的格标低了（todo / no、线开着还 wait、完整引导有页面流程还 half）拦，标高了没证据拦
  （done 没页面流程、账本线关着还标 half）；done / half 要写依据；引导相关的流程、真机记录、验收场景都要登记到某一格；
  依据里的文档摘句、真机记录、验收证据要对得上，过期的验收证据不算档；相对分叉点改了引导声明却没改总表数据 → 拦；
- 现状如实：只有 ERP 直写收款（本地 Express）做完；收款只在 ERP 直写；代录只有采购、销售等五项业务在范围内。

    python3 tests/test_cc_coverage.py
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_cc_coverage", ROOT / "scripts/check-cc-coverage.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

GUIDES = {"pearnly_cowork": [{"id": "purchase", "modes": ["chat", "operator"], "source": "documents", "ledgers": ["express_local"],
                               "writes": True, "review": "x.review", "confirm": "x.confirm", "result": "x.result"}],
          "pearnly_erp_portal": [{"id": "collection", "modes": ["chat"], "source": "chat", "ledgers": ["express_local", "mrerp"]}]}
OUT = ["out:not here"]


def row(**cells):
    base = {"id": "purchase", "name": "Purchase", "guide": "purchase", "cowork_direct": OUT, "cowork_operator": OUT,
            "erp_direct": OUT, "erp_operator": OUT}
    return {**base, **cells}


class CellTests(unittest.TestCase):
    def test_the_six_ways_to_write_a_cell(self):
        self.assertEqual(gate.parse("done"), ("done", "", ""))
        self.assertEqual(gate.parse("half:p2"), ("half", "p2", ""))
        self.assertEqual(gate.parse("todo:p4"), ("todo", "p4", ""))
        self.assertEqual(gate.parse("no:p2:desktop"), ("no", "p2", "desktop"))
        self.assertEqual(gate.parse("wait:cloud"), ("wait", "", "cloud"))
        self.assertEqual(gate.parse("out:只在 ERP"), ("out", "", "只在 ERP"))

    def test_anything_else_is_refused(self):
        for bad in ("done:p1", "half", "todo:p9", "no:p2", "wait:someone", "wait:erp_docs", "out:", "out", "maybe"):
            self.assertRaises(ValueError, gate.parse, bad)


class RuleTests(unittest.TestCase):
    def test_done_or_half_needs_a_declared_guide_for_that_product_and_mode(self):
        self.assertEqual(gate.check([row(cowork_direct=["half:p2", "wait:mrerp", "wait:cloud"], cowork_operator=["todo:p2"])],
                                    GUIDES), [])
        errors = gate.check([row(erp_operator=["half:p2", "wait:mrerp", "wait:cloud"], cowork_direct=["todo:p2"],
                                 cowork_operator=["todo:p2"])], GUIDES)
        self.assertEqual(len(errors), 1)
        self.assertIn("purchase.erp_operator[express_local]: marked half but pearnly_erp_portal declares no guide", errors[0])

    def test_done_needs_a_complete_guide_that_covers_the_ledger(self):
        chat = {"id": "collection", "name": "Collection", "guide": "collection", "cowork_direct": OUT, "cowork_operator": OUT,
                "erp_direct": ["done", "wait:mrerp", "wait:cloud"], "erp_operator": OUT}
        self.assertTrue(any("only has an opening line" in e for e in gate.check([chat], GUIDES)))
        ledger = row(cowork_direct=["done", "done", "wait:cloud"], cowork_operator=["todo:p2"])
        self.assertEqual(gate.check([ledger], GUIDES),
                         ["purchase.cowork_direct[mrerp]: marked done but the guide does not cover mrerp"])

    def test_a_declared_guide_cannot_be_marked_out_of_scope(self):
        errors = gate.check([row(cowork_operator=["todo:p2"])], GUIDES)
        self.assertEqual(errors, ["purchase.cowork_direct: marked out of scope but pearnly_cowork declares this guide for chat"])

    def test_a_group_has_one_cell_or_three_and_a_row_a_unique_id(self):
        self.assertTrue(any("one cell" in e for e in gate.check([row(erp_direct=["todo:p2", "wait:mrerp"])], {})))
        self.assertTrue(any("unique id" in e for e in gate.check([row(guide=None), row(guide=None)], {})))


LINES = {"mrerp": {"cowork_direct": "open", "cowork_operator": "closed:off", "erp_direct": "closed:off", "erp_operator": "closed:off"},
         "cloud": {g: "closed:placeholder" for g in gate.GROUPS}}
EVIDENCE = {"flows": {"erp.cc.guide.collection": {"id": "erp.cc.guide.collection", "module": "scripts/flows/erp_cc_guide_collection.py"},
                      "cowork.cc.guide": {"id": "cowork.cc.guide", "product": "cowork", "module": "scripts/flows/cc_guide.py"},
                      "erp.web.collection": {"id": "erp.web.collection", "module": "scripts/flows/erp_web_collection.py"}},
            "scenarios": {"acc.cc": {"id": "acc.cc", "env": "B", "driver": "real", "rows": ["erp.cc.tool.erp_collection_guide"]},
                          "acc.local": {"id": "acc.local", "env": "A", "driver": "real", "rows": ["erp.cc.tool.x"]}},
            "passes": {"acc.local": [{"scenario": "acc.local"}]}, "stale": {}, "records": {}}
COLLECTION = {"id": "collection", "modes": ["chat"], "source": "form", "ledgers": ["express_local", "mrerp"], "writes": True,
              "review": "x.review", "confirm": "x.confirm", "result": "x.result"}
ERP = {"pearnly_erp_portal": [COLLECTION]}


def erp_row(cells, *links):
    return {"id": "collection", "name": "Collection", "guide": "collection", "cowork_direct": OUT, "cowork_operator": OUT,
            "erp_direct": cells, "erp_operator": OUT, "link": list(links)}


def full_check(rows, guides=ERP, evidence=None, unlinked=None):
    unlinked = unlinked if unlinked is not None else {"flows": {"cowork.cc.guide": "other product"}, "scenarios": {"acc.cc": "later"}}
    return gate.check(rows, guides, LINES, evidence or EVIDENCE, unlinked)


DONE_LINK = {"cell": "erp_direct.express_local", "basis": "declared; page flow", "page_flows": ["erp.cc.guide.collection"]}


class EvidenceRuleTests(unittest.TestCase):
    def test_a_done_cell_with_its_page_flow_passes(self):
        self.assertEqual(full_check([erp_row(["done", "wait:mrerp", "wait:cloud"], DONE_LINK)]), [])

    def test_a_declared_cell_marked_lower_is_stopped(self):
        errors = full_check([erp_row(["todo:p1", "wait:mrerp", "wait:cloud"])])
        self.assertTrue(any("mark it half (or done) instead of todo" in e for e in errors), errors)
        self.assertTrue(any("has no cell marked half / done / wait" in e for e in errors), errors)

    def test_a_complete_guide_with_a_page_flow_still_marked_half_is_stopped(self):
        errors = full_check([erp_row(["half:p1", "wait:mrerp", "wait:cloud"], DONE_LINK)])
        self.assertEqual(errors, ["collection.erp_direct[express_local]: a complete guide with a registered page flow "
                                  "(erp.cc.guide.collection): mark it done"])

    def test_done_without_a_page_flow_or_half_without_a_basis_is_stopped(self):
        errors = full_check([erp_row(["done", "wait:mrerp", "wait:cloud"])], unlinked={"flows": {
            "erp.cc.guide.collection": "x", "cowork.cc.guide": "x"}, "scenarios": {"acc.cc": "x"}})
        self.assertTrue(any("no page flow is registered" in e for e in errors), errors)
        self.assertTrue(any("without a [[row.link]]" in e for e in errors), errors)

    def test_a_closed_line_takes_only_wait_and_an_open_line_no_wait(self):
        errors = full_check([erp_row(["done", "half:p1", "wait:cloud"], DONE_LINK,
                                     {"cell": "erp_direct.mrerp", "basis": "x"})])
        self.assertTrue(any("the mrerp line is closed for erp_direct (off): mark it wait:mrerp" in e for e in errors), errors)
        cowork = {"pearnly_cowork": [{**COLLECTION, "id": "purchase", "source": "documents"}]}
        row_ = row(cowork_direct=["half:p2", "wait:mrerp", "wait:cloud"], cowork_operator=["out:no"],
                   link=[{"cell": "cowork_direct.express_local", "basis": "x"}])
        errors = gate.check([row_], cowork, LINES, EVIDENCE, {"flows": {f: "x" for f in EVIDENCE["flows"]}, "scenarios": {"acc.cc": "x"}})
        self.assertTrue(any("the mrerp line is open for cowork_direct and the guide covers mrerp: mark it half or done" in e
                            for e in errors), errors)

    def test_every_guide_flow_and_scenario_is_registered_to_a_cell(self):
        errors = full_check([erp_row(["done", "wait:mrerp", "wait:cloud"], DONE_LINK)], unlinked={})
        self.assertIn("guide flow cowork.cc.guide is not registered to any cell ([[row.link]] flows / page_flows, "
                      "or [unlinked.flows] with why)", errors)
        self.assertIn("acceptance scenario acc.cc asserts a guide row but is not registered to any cell", errors)
        self.assertFalse(any("erp.web.collection" in e for e in errors))
        errors = full_check([erp_row(["done", "wait:mrerp", "wait:cloud"], DONE_LINK)],
                            unlinked={"flows": {"cowork.cc.guide": "x", "gone.flow": "x"}, "scenarios": {"acc.cc": ""}})
        self.assertIn("unlinked.flows: gone.flow no longer exists: remove it", errors)
        self.assertIn("unlinked.scenarios: acc.cc needs a reason", errors)

    def test_a_declared_guide_needs_a_row(self):
        guides = {"pearnly_erp_portal": [COLLECTION, {**COLLECTION, "id": "stocktake"}]}
        errors = full_check([erp_row(["done", "wait:mrerp", "wait:cloud"], DONE_LINK)], guides)
        self.assertIn(f"pearnly_erp_portal: guide 'stocktake' is declared but no row of {gate.DATA_REL} has guide = 'stocktake'", errors)

    def test_references_must_resolve_and_tiers_follow_the_evidence(self):
        link = {**DONE_LINK, "flows": ["no.such.flow"], "model": ["docs/work/2026-11-14-cc-guided-master-design.md::没有这句话的片段"],
                "device": ["真机记录:no.row", "acc:acc.unknown"]}
        errors = full_check([erp_row(["done", "wait:mrerp", "wait:cloud"], link)])
        self.assertTrue(any("flow no.such.flow is not in tests/flows/registry.toml" in e for e in errors), errors)
        self.assertTrue(any("does not contain" in e for e in errors), errors)
        self.assertTrue(any("no passing 真机记录 for no.row" in e for e in errors), errors)
        self.assertTrue(any("acc.unknown is not in tests/acceptance/registry.toml" in e for e in errors), errors)
        self.assertEqual(gate.tier_of("acc:acc.local", "model", EVIDENCE), ("model", None))
        self.assertEqual(gate.tier_of("acc:acc.cc", "device", EVIDENCE), (None, None))      # 没跑过 / 过期：不算档
        passed = {**EVIDENCE, "passes": {"acc.cc": [{"scenario": "acc.cc"}]}}
        self.assertEqual(gate.tier_of("acc:acc.cc", "device", passed), ("device", None))
        quote = "docs/work/2026-11-14-cc-guided-master-design.md::覆盖总表（一张）"
        self.assertEqual(gate.tier_of(quote, "model", EVIDENCE), ("model", None))
        rows = [erp_row(["done", "wait:mrerp", "wait:cloud"], {**DONE_LINK, "model": [quote], "device": ["acc:acc.cc"]})]
        self.assertEqual(gate.tiers(rows, EVIDENCE)[("collection", "erp_direct.express_local")], "model")
        self.assertEqual(gate.tiers(rows, passed)[("collection", "erp_direct.express_local")], "device")

    def test_evidence_on_a_cell_marked_todo_is_stopped(self):
        rows = [erp_row(["done", "wait:mrerp", "wait:cloud"], DONE_LINK),
                {**row(cowork_direct=["todo:p2"], cowork_operator=["todo:p2"], guide="other", id="other"),
                 "link": [{"cell": "cowork_direct", "page_flows": ["cowork.cc.guide"]}]}]
        errors = gate.check(rows, ERP, LINES, EVIDENCE, {"scenarios": {"acc.cc": "x"}})
        self.assertIn("other.cowork_direct: evidence is registered but the cell is todo", errors)


class SameCommitTests(unittest.TestCase):
    """相对分叉点改了引导声明（或引导流程）而总表数据没改 → 拦；一起改了就过。"""

    def _repo(self, tmp):
        import subprocess
        def run(*args):
            subprocess.run(args, cwd=tmp, check=True, capture_output=True)
        app = Path(tmp) / "apps/pearnly_x/pearnly_x"
        app.mkdir(parents=True)
        (app / "hooks.py").write_text('pearnly_guides = ["pearnly_x.guides.GUIDES"]\n', encoding="utf-8")
        (app / "guides.py").write_text('GUIDES = [{"id": "a", "modes": ["chat"], "ledgers": ["express_local"], "source": "form"}]\n',
                                       encoding="utf-8")
        (Path(tmp) / "tests/flows").mkdir(parents=True)
        (Path(tmp) / "tests/flows/registry.toml").write_text('[[flow]]\nid = "x.guide.a"\nmodule = "m.py"\n', encoding="utf-8")
        (Path(tmp) / "scripts/impact").mkdir(parents=True)
        (Path(tmp) / gate.DATA_REL).write_text("# v1\n", encoding="utf-8")
        run("git", "init", "-q", "-b", "main")
        run("git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
        run("git", "checkout", "-qb", "work")
        return app, run

    def test_a_changed_declaration_needs_the_table_changed_in_the_same_branch(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            app, run = self._repo(tmp)
            root = Path(tmp)
            self.assertEqual(gate.same_commit(root, "main")[0], [])
            (app / "guides.py").write_text('GUIDES = [{"id": "a", "modes": ["chat", "operator"], "ledgers": ["express_local"], '
                                           '"source": "form"}]\n', encoding="utf-8")
            errors, _ = gate.same_commit(root, "main")
            self.assertEqual(len(errors), 1)
            self.assertIn("guide declarations", errors[0])
            (root / gate.DATA_REL).write_text("# v2\n", encoding="utf-8")
            run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "both")
            self.assertEqual(gate.same_commit(root, "main")[0], [])

    def test_a_new_guide_flow_needs_the_table_changed_too(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _app, run = self._repo(tmp)
            root = Path(tmp)
            with open(root / "tests/flows/registry.toml", "a", encoding="utf-8") as fh:
                fh.write('\n[[flow]]\nid = "x.guide.b"\nmodule = "m.py"\n')
            errors, _ = gate.same_commit(root, "main")
            self.assertTrue(errors and "guide flows" in errors[0], errors)

    def test_without_a_merge_base_the_rule_is_skipped(self):
        self.assertEqual(gate.same_commit(ROOT, "no-such-ref-xyz")[0], [])


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.data = gate.load_all()
        self.rows = self.data["row"]
        self.guides, self.errors = gate.guides_gate.load()
        self.evidence = gate.load_evidence()

    def test_the_data_passes_and_the_table_in_the_matrix_is_generated_from_it(self):
        self.assertEqual(self.errors, [])
        self.assertEqual(gate.check(self.rows, self.guides, self.data["line"], self.evidence, self.data.get("unlinked")), [])
        self.assertEqual(gate.doc_section(gate.DOC.read_text(encoding="utf-8")),
                         gate.render(self.rows, self.guides, self.data["line"], self.evidence))

    def test_the_state_is_reported_as_it_is(self):
        found = gate.counts(self.rows, self.evidence)
        self.assertEqual(found["done"], 25)
        by_id = {r["id"]: r for r in self.rows}
        # 11-25 C1：Cowork 盘点、公司资料接共用结果步骤并有真页面流程
        self.assertEqual([by_id["stocktake"]["cowork_direct"][0], by_id["company_info"]["cowork_direct"][0]], ["done", "done"])
        self.assertEqual(by_id["collection"]["erp_direct"][0], "done")
        # 11-23 真页面流程：Cowork 直写采购、ERP 代录采购 / 销售（本地 Express）
        self.assertEqual([by_id["purchase"]["cowork_direct"][0], by_id["purchase"]["erp_operator"][0],
                          by_id["sales"]["erp_operator"][0]], ["done", "done", "done"])
        # 11-24 Cowork 代录采购 / 销售（本地 Express）有真页面流程
        self.assertEqual([by_id["purchase"]["cowork_operator"][0], by_id["sales"]["cowork_operator"][0]], ["done", "done"])
        # 11-24 Cowork 直写、ERP 直写采购 / 销售 · MR.ERP：页面流程 + MR.ERP 专属测试账套卡上作答真写（cc-guide-mrerp-direct）
        self.assertEqual([by_id[row][group][1] for row in ("purchase", "sales") for group in ("cowork_direct", "erp_direct")],
                         ["done"] * 4)
        # 11-24 对账在接 MR.ERP 的公司对话里有真页面流程
        self.assertEqual(by_id["recon"]["cowork_direct"][1], "done")
        # 11-24 单据维护（Cowork 直写 / 代录）、待处理（Cowork 直写 · 本地补结果步骤、代录 · 本地新开）有真页面流程
        self.assertEqual([by_id["documents"]["cowork_direct"][0], by_id["documents"]["cowork_operator"][0],
                          by_id["resolve"]["cowork_direct"][0], by_id["resolve"]["cowork_operator"][0]], ["done"] * 4)
        # 11-23 没有复核的表单答完出确定卡后的真页面流程：Cowork 回读、对账（本地 Express）
        self.assertEqual([by_id["readback"]["cowork_direct"][0], by_id["recon"]["cowork_direct"][0]], ["done", "done"])
        # 11-23 ERP 直写采购 / 销售（本地 Express）放开并有真页面流程
        self.assertEqual([by_id["purchase"]["erp_direct"][0], by_id["sales"]["erp_direct"][0]], ["done", "done"])
        # 11-23 documents 源出题进对话口径之后：Cowork 直写销售（本地 Express）
        self.assertEqual(by_id["sales"]["cowork_direct"][0], "done")
        # 11-23 补了结果步骤之后的真页面流程：Cowork 增值税、POS 日汇总（本地 Express）、预扣税
        self.assertEqual([by_id["vat"]["cowork_direct"][0], by_id["pos_day"]["cowork_direct"][0], by_id["wht"]["cowork_direct"][0]],
                         ["done", "done", "done"])
        self.assertEqual(found["device"] + found["model"] + found["server"], found["done"] + found["half"])
        self.assertEqual(found["none"], found["todo"] + found["no"])
        states = ("done", "half", "todo", "no", *(f"wait:{line}" for line in gate.WAITS))
        self.assertEqual(found["in_scope"], sum(found[k] for k in states))
        by_id = {r["id"]: r for r in self.rows}
        self.assertTrue(all(gate.parse(c)[0] == "out" for g in ("cowork_direct", "cowork_operator", "erp_operator")
                            for c in by_id["collection"][g]))
        in_operator = [r["id"] for r in self.rows for g in ("cowork_operator", "erp_operator")
                       if any(gate.parse(c)[0] != "out" for c in r[g])]
        self.assertEqual(sorted(set(in_operator)), ["documents", "new_master", "purchase", "resolve", "sales"])


if __name__ == "__main__":
    unittest.main()
