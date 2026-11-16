"""验收场景（一组，修复前红）：11-16 真机线查出的四个代录问题，按「修好后的正确行为」断言。修复在别的窗口、还没进 main，现在跑应当
不通过；对应分支合进 main（并部署到被测站点）后应转绿。

  acc.erp.operator.intake.purchase   H3「ERP 代录放票」：放进来的票识别完是收单记录 ``I:<记录>``，开始前卡片（写账前置判定 check）
                                      认得它（修复前静默丢掉，写入计划判「未找到」）、作答记下不报没有权限（修复前 403）、交单后提交成
                                      ERP 采购单并由 CC 桌面版在 Express 界面录成 → 账本读回。修复：`pearnly/erp-operator-intake`
                                      （32ec86ccf 起，H3 窗口）。
  acc.erp.operator.stock.group       H5「代录被拦后无路可走」：一件账套里没有的新库存品，账套合格的存货科目组两个以上时开始前就问
                                      「记哪个物料组」、答了按账套记住、照选的组建档写成（修复前开始前不问，CC 桌面版按用得最多的组建档、
                                      不合格就写到一半停下 STOCK_ACC_GROUP_UNFIT、卡上只有「重试」）。停下时另核单据上带原因与可选项。
                                      修复：`pearnly/cc-stop-exits-h5`（d818a1791，S1 + S2，CC 桌面版窗口）。
  acc.erp.operator.masters.link      验收首跑查出的产品缺陷 2「代录回执挂码」：新供应商 + 新物料的现购单由代录写成后，ERP 的供应商档
                                      与物料挂上账本里实际用的码，主档同步后不另建一条（修复前服务端 `cc_desktop/jobs.py` 只读回执顶层
                                      party_code，代录的码在 ``meta.operator.masters``）。修复：`pearnly/operator-masters-link`（e7d615d3b）。
  acc.erp.operator.no.computer       「发单人没有电脑时显示未连接」：自己一台 CC 桌面版都没有、公司里也没有办公室电脑兜底的员工交代录，
                                      单据页、代录进度、给模型的任务状态如实说「CC 桌面版未连接」（修复前代录只写「等待电脑」、模型说已交给
                                      电脑）。不写账套。修复：`pearnly/erp-operator-intake` 的 341e08624（computer_state 钩子）。

编排 B（实验栈 + 测试机2 的 CC 桌面版 + 验收账套）；前三条占测试机桌面（代录在 Express 界面里录），一次一条，20:40 前收尾。
模型不参与，费用 0；不跑识别（H3 照识别结果的样子造收单记录）。都从服务端函数走（via = "api"）：对话里的卡片、页面点选在流程用例
`erp.cc.operator.intake` / `cc.operator.stop-choice` / `erp.cc.operator.no-computer` 里核。
**状态：写好未跑**（11-16）。首跑要校准的：H3 收单记录的票面内容（供应商按名称 + 税号认回账套里那位）；H5 要验收账套合格的存货科目组
两个以上（不到两个记前置条件不满足）；挂码那条等主档同步做完最多 7 分钟；未连接那条要另配一家「CC 报了账套、但够得到账套的电脑上
没有这家公司员工」的公司与一个没有电脑的员工（`PEARNLY_ACC_ERP_NOPC_USER` / `_TAX` / `_BRANCH`，没配记前置条件不满足）。
"""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
Unmet = sys.modules["acc_errors"].Unmet
OPERATOR_KEY = "erp:operator:"          # 同 `express.push.OPERATOR_KEY`
NOT_CONNECTED = "express.cc_desktop_not_connected"   # 同 `express.contracts.NOT_CONNECTED`
UNKNOWN_DOCUMENT = "unknown_document"   # H3 修复后认不出的编号种类单独报的码（不该出在 I: 上）


def _chain():
    if "acc_erp_chain" not in sys.modules:
        spec = importlib.util.spec_from_file_location("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["acc_erp_chain"] = module
        spec.loader.exec_module(module)
    return sys.modules["acc_erp_chain"]


def handed_and_written(ctx, chain, out) -> str:
    """交单之后：写成了、是代录交的、只这一件、任务里已录入带同一个单号；落点只有验收账套。回 Express 单号。"""
    state, job = out.get("state") or {}, out.get("job") or {}
    ctx.save("handed.json", out.get("handed"))
    if state.get("ledger_state") == "held":
        stopped = out.get("stopped_card") or {}
        # H5 S2：停下的件在单据上带原因与可选项（不是空白、只有「重试」）
        ctx.check("停下的件带原因", True, bool(state.get("ledger_reason")))
        ctx.check("停下的件带可选项", True, any(item.get("options") for item in stopped.get("items") or []))
    ctx.require("写账状态", "written", state.get("ledger_state"))
    ctx.check("回显了 Express 单号", True, bool(state.get("ledger_docnum")))
    ctx.check("这件写账是代录交的", True, str(job.get("request_key") or "").startswith(OPERATOR_KEY))
    ctx.check("这张单名下只有这一件写账", 1, out.get("jobs"))
    rows = [row for row in (out.get("task_view") or {}).get("documents") or [] if row.get("state") == "saved"]
    ctx.check("代录任务里这张是已录入、带同一个单号", [state["ledger_docnum"]], [row.get("docnum") for row in rows])
    chain.only_ledger(ctx, out.get("book_paths"))
    ctx.facts.update(docnums=[state["ledger_docnum"]], ledger=chain.LEDGER, erp_doc=out.get("doc"), task=out.get("task"))
    return state["ledger_docnum"]


def card_answered(ctx, out) -> None:
    """开始前卡片认得这张、题都答得了、记下没被拒。"""
    card = out.get("card") or {}
    ctx.save("start-card.json", out.get("rounds"))
    ctx.require("开始前卡片认得这张单（不是未找到）", True, card.get("listed"))
    ctx.check("这张单没被判成「本产品不认的编号」", [], [row for row in card.get("not_writable") or [] if row.get("reason") == UNKNOWN_DOCUMENT])
    ctx.check("卡片上的题都答得了", [], out.get("unanswered"))
    ctx.check("答案都在这一题的可选范围里", [], out.get("expand_errors"))
    ctx.require("记下没被拒（不是没有权限）", True, (out.get("remembered") or {}).get("ok"))


def intake(ctx, chain, conf, book):
    with ctx.step("放票 → 开始前卡片 → 记下 → 交单", "收单记录 I: 在开始前卡片里、作答记下成功、交单后提交成 ERP 采购单并由代录录成"):
        out = chain.site(ctx, "erp_operator_card.py", {**conf, "kind": "intake", "run": chain.tag(ctx), "price": 10}, timeout=1500)
        ctx.facts.update(intake=out.get("intake"))
        card_answered(ctx, out)
        ctx.require("交单后提交成了 ERP 采购单", True, bool(out.get("doc")))
        docnum = handed_and_written(ctx, chain, out)
    with ctx.step("账本读回", "账套里按单号读得到这张现购单，与网页开单交代录的一样；预期以外的表逐字节不变"):
        chain.purchase_readback(ctx, book, conf, docnum, out["expected"])


def stock_group(ctx, chain, conf, book):
    with ctx.step("新库存品：开始前问物料组 → 记住 → 交单", "开始前卡片问一次「新物料记哪个物料组」并列出全部合格的组；答了记住、照写"):
        out = chain.site(ctx, "erp_operator_card.py", {**conf, "kind": "stock_group", "run": chain.tag(ctx), "price": 10}, timeout=1500)
        card_answered(ctx, out)
        asked = [item for rnd in out.get("rounds") or [] for item in rnd.get("items") or [] if item.get("kind") == "stock_group"
                 and item.get("state") != "decided"]
        ctx.save("stock-group-asked.json", {"asked": asked, "fit": out.get("fit_groups"), "chosen": out.get("group_chosen")})
        ctx.check("开始前问了物料组", True, bool(asked))
        ctx.check("合格的组都在可选项里", [], sorted(set(out.get("fit_groups") or []) - {code for item in asked for code in item.get("options") or []}))
        ctx.check("答了以后记住（再判不再问）", [], [item for item in (out.get("card_after") or {}).get("items") or []
                                              if item.get("kind") == "stock_group" and item.get("state") != "decided"])
        docnum = handed_and_written(ctx, chain, out)
    with ctx.step("账本读回", "现购单读得到；新建的物料挂的是选的那个物料组；预期以外的表逐字节不变"):
        got = chain.purchase_readback(ctx, book, conf, docnum, out["expected"], new_masters=True)
        master = chain.rows(book, got["after"], "STMAS", f"STKCOD={got['item_code']}")
        ctx.save("stmas-new-item.json", master)
        ctx.require("STMAS 里新物料恰好一行", 1, len(master))
        ctx.check("新物料的物料组 = 开始前选的", out.get("group_chosen"), str(master[0].get("ACCCOD") or "").strip())
        ctx.check("STMAS 只多一行", 1, chain.grew(got["summary"], "STMAS"))


def masters_link(ctx, chain, conf, book):
    with ctx.step("新供应商 + 新物料交代录", "代录在 Express 里建档并录成；写完主档同步做完"):
        out = chain.site(ctx, "erp_operator_card.py", {**conf, "kind": "new_masters", "run": chain.tag(ctx), "price": 10}, timeout=1800)
        card_answered(ctx, out)
        docnum = handed_and_written(ctx, chain, out)
        ctx.check("写完后的主档同步做完了", "ready", out.get("sync_state"))
    with ctx.step("账本读回与挂码", "ERP 的供应商 / 物料挂上账本里实际用的码；主档同步后同一个码只有这一条档"):
        got = chain.purchase_readback(ctx, book, conf, docnum, out["expected"], new_masters=True)
        codes = out.get("made_codes") or {}
        ctx.check("供应商档挂上了账本里实际用的码", got["supplier_code"], codes.get("supplier"))
        ctx.check("物料挂上了账本里实际用的码", got["item_code"], codes.get("item"))
        ctx.check("主档同步后供应商没多出一条（这个码只有单上那一位）", [out.get("made_supplier")],
                  [row.get("name") for row in out.get("suppliers_new") or [] if row.get("express_code") == got["supplier_code"]])
        ctx.check("主档同步后物料没多出一条（这个码只有单上那一件）", [out.get("made_item")],
                  [row.get("name") for row in out.get("products_new") or [] if row.get("express_code") == got["item_code"]])
        ctx.check("APMAS 只多一行", 1, chain.grew(got["summary"], "APMAS"))
        ctx.check("STMAS 只多一行", 1, chain.grew(got["summary"], "STMAS"))


def no_computer(ctx, chain):
    need = {"user": "PEARNLY_ACC_ERP_NOPC_USER", "company_tax": "PEARNLY_ACC_ERP_NOPC_TAX"}
    conf = {key: os.environ.get(name, "") for key, name in need.items()}
    missing = [need[key] for key, value in conf.items() if not value]
    if missing:
        raise Unmet("没配：" + "、".join(missing) + "（一个没有电脑的员工，和他所在的、没有办公室电脑兜底的公司；见场景文件头）")
    conf["branch"] = os.environ.get("PEARNLY_ACC_ERP_NOPC_BRANCH", "")
    with ctx.step("没有电脑的员工交代录", "交单后排着没人领；单据页、代录进度、给模型的任务状态都说「CC 桌面版未连接」；收尾撤件作废"):
        out = chain.site(ctx, "erp_operator_no_computer.py", {**conf, "run": chain.tag(ctx), "price": 10}, timeout=300)
        job = out.get("job") or {}
        ctx.facts.update(erp_doc=out.get("doc"))
        ctx.require("交单后排进了写账队列", ["queued", "queued"], [(out.get("state") or {}).get("ledger_state"), job.get("status")])
        ctx.check("这一件归发单人（公司里没有电脑兜底）", conf["user"], job.get("actor"))
        ctx.check("单据页的原因：CC 桌面版未连接", NOT_CONNECTED, out.get("doc_note"))
        ctx.check("代录判出发单人没有电脑：CC 桌面版未连接", NOT_CONNECTED, out.get("computer_state"))
        ctx.check("给模型的任务状态带「未连接」那一句", out.get("words"), (out.get("status") or {}).get("no_computer"))
        ctx.check("对话里的代录进度写明未连接（不只「等待电脑」）", True, (out.get("feed") or {}).get("says_not_connected"))
        ctx.check("收尾撤掉了这一件、作废了这张单", [True, ""], [bool((out.get("cleaned") or {}).get("doc")), out.get("cleanup_error") or ""])


def run(ctx):
    chain = _chain()
    case = ctx.scenario["id"].removeprefix("acc.erp.operator.")
    if case == "no.computer":
        no_computer(ctx, chain)
        return
    flows = {"intake.purchase": intake, "stock.group": stock_group, "masters.link": masters_link}
    if case not in flows:
        raise Unmet(f"场景 {ctx.scenario['id']} 在这个模块里没有对应的走法")
    conf = chain.settings()
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)
    flows[case](ctx, chain, conf, book)


def cleanup(ctx):
    _chain().cleanup(ctx)
