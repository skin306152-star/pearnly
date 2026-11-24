"""验收场景：代录列出的单被直写抢先（主会话 11-24 口径；L3 首跑问题 1）——列出阶段不锁；列出过的单被每分钟那一轮直写写进账本后，
代录这边如实显示「已入账 · 单号」、开始时跳过，不凭空消失，账本里只有一张（不重复入账）。

  acc.erp.operator.listed.direct   一张已提交、还没交进写账队列的赊销单（新建「ร้าน…」客户、只记账的一行）

编排 B（Mac 实验栈 + 测试机 CC 桌面版 + 验收账套），服务端那一段在站点里直接调代录的服务函数（via = "api"），不开对话、不调模型，
费用 0。写账的是产品自己每分钟那一轮（`express.push.poll`）交的直写件，CC 桌面版照常写；本场景只看、不替产品写。

断言：列出时这张在资料清单里；等到它被直写写成（写账件是直写的，不是代录的）；之后资料清单第一页里它作为「已入账」一行出现、
单号 = 账本单号；给模型的准备中清单里它在 entered、不在 waiting；写入计划取单把它跳过（already_entered + 单号）；交单不交它；
这张单名下只有一件写账；账本读回 ARTRN 只多一张发票、预期以外的文件不变。
"""
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
Unmet = sys.modules["acc_errors"].Unmet
OPERATOR_KEY = "erp:operator:"
POLL_WAIT = 240   # 每分钟一轮，留足两轮 + 电脑领单写账


def _chain():
    if "acc_erp_chain" not in sys.modules:
        spec = importlib.util.spec_from_file_location("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["acc_erp_chain"] = module
        spec.loader.exec_module(module)
    return sys.modules["acc_erp_chain"]


def run(ctx):
    chain = _chain()
    conf = chain.settings()
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)

    with ctx.step("开一张还没交进写账队列的单", "单据已提交、账本栏是「排队中」、没有写账件"):
        held = chain.site(ctx, "erp_push_held.py", {**conf, "kind": "sales", "run": chain.tag(ctx), "leave": "queued"}, timeout=300)
        ctx.require("单据的账本栏与写账件数", ["queued", 0], [held["state"]["ledger_state"], held["jobs"]])
        ctx.facts.update(erp_doc=held["doc"], ledger=chain.LEDGER)

    with ctx.step("代录列出 → 被直写写掉 → 代录这边怎么说",
                  "列出时在清单里；直写写成后清单里是「已入账 · 单号」一行、模型看到 entered、写入计划跳过、交单不交；只有一件写账"):
        out = chain.site(ctx, "erp_operator_listed.py", {"user": conf["user"], "company": held["company"], "doc_id": held["doc_id"],
                                                          "doctype": held["doctype"], "doc": held["doc"], "wait": POLL_WAIT},
                         timeout=POLL_WAIT + 300)
        doc_id = held["doc_id"]
        state, job = out.get("state") or {}, out.get("job") or {}
        if doc_id not in (out.get("listed") or []):
            raise Unmet(f"列出时这张已经不在资料清单里了（直写在列出前就交了）：{out.get('state')}")
        ctx.require("等到它被直写写成", "written", state.get("ledger_state"))
        ctx.check("写成它的是直写件，不是代录件", False, str(job.get("request_key") or "").startswith(OPERATOR_KEY))
        docnum = state.get("ledger_docnum") or ""
        ctx.check("资料清单里不再当成待录", False, doc_id in (out.get("after_listed") or []))
        ctx.check("资料清单第一页里是「已入账 · 单号」一行", [[doc_id, "saved", docnum, []]],
                  [[row.get("doc_id"), row.get("state"), row.get("docnum"), row.get("moves")] for row in out.get("after_gone") or []])
        ctx.check("给模型的准备中清单：不在 waiting、在 entered 带单号", [False, [[doc_id, docnum]]],
                  [doc_id in (out.get("model_waiting") or []), [[row.get("doc_id"), row.get("docnum")] for row in out.get("model_entered") or []]])
        ctx.check("写入计划取单跳过它（already_entered + 单号）", [{"doc_id": doc_id, "reason": "already_entered", "docnum": docnum}],
                  [row for row in out.get("skipped") or [] if row.get("doc_id") == doc_id])
        ctx.check("写入计划里没有它", False, doc_id in (out.get("planned") or []))
        ctx.check("交单不交它（没有别的单就整批被拒）", True,
                  bool(out.get("hand_in_refused")) or doc_id not in [row.get("doc_id") for row in (out.get("hand_in") or {}).get("results") or []])
        ctx.check("这张单名下只有一件写账", 1, out.get("jobs"))
        ctx.facts.update(docnums=[docnum])

    with ctx.step("账本读回", "ARTRN 只多一张发票、预期以外的文件不变（没有重复入账）"):
        _after, summary = chain.copy_and_diff(ctx, book, conf, chain.sales_tables())
        ctx.check("ARTRN 只多一行", 1, chain.grew(summary, "ARTRN"))
