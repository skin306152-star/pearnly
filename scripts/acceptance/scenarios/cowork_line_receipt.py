"""验收场景：Cowork LINE 录单回执（核心集第 14 条）——LINE 卡片上点「保存」→ 推送 → CC 桌面版写进验收账套 → 用同一张回复令牌回结果
（`pearnly_cowork.line.save` + `pearnly_core.line_ui.receipt`：loading 开满 60 秒、55 秒还没结果才回「录入中 +【查看结果】」、全程只回复
不推送）→ 账本按单号读回 → 恢复基线。

  acc.cowork.line.receipt   服务类采购票（样票 purchase-a2，内容写死、不跑识别）卡片「保存」

编排 B，服务端**只在接着 LINE 测试频道的测试环境 testcowork 跑**（测试机1 实验栈，PEARNLY_ACC_SITE_TARGET=lab）：Mac 栈没有 LINE 频道，
镜像模式下 loading 与兜底都不走。卡片是进程内点的（via = "api"），回复令牌与 LINE 号是假值，本进程出站全截住；结果那一回发生在别的
进程（CC 回执请求 / 后台任务），LINE 拒收假令牌、站点记一笔 reply_rejected——「回一次、不推送」由站点按天出站计数的前后差证明。
回执文字（单号写在消息里）LINE 拒收后看不到：真机记录（三星，`cowork.line.postback.cowork_save`）核。模型不参与，费用 0。

断言：推送写成、单号 = 账本读回（与 `acc.cowork.intake.express` 同一套）；本进程开了 loading 且秒数 = LOADING_SECONDS（60）；
等结果的键记下了；**全程没有推送**（站点计数 push_* 前后不变、本进程没有 push 请求）；55 秒内有结果时：回复恰好一次（站点计数）、
本进程没有回兜底；55 秒还没结果时：本进程回的就是兜底（「录入中」+【查看结果】），记一笔「走了兜底」。

**状态：写好未跑**（11-23）。参数同 `cowork_intake_express`（PEARNLY_ACC_COWORK_USER / _CONNECTION / _BASELINE / _LEDGER / _LEDGER_HOST）。
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
Unmet = sys.modules["acc_errors"].Unmet
REPLY_KINDS = ("reply_ok", "reply_rejected", "reply_failed", "reply_unknown")


def _intake():
    key = "acc_cowork_intake_shared"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "cowork_intake_express.py")
        sys.modules[key] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[key])
    return sys.modules[key]


def run(ctx):
    intake = _intake()
    chain = intake._chain()
    if intake.SITE_TARGET != "lab":
        raise Unmet("这条只在接着 LINE 测试频道的测试环境 testcowork 跑（PEARNLY_ACC_SITE_TARGET=lab）；Mac 栈没有 LINE 频道")
    conf, ticket = intake.settings(chain), intake.sample()
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)

    with ctx.step("卡片上点「保存」并等结果", "开了 60 秒 loading、记下等结果的键；推送写成；全程没有推送消息；55 秒内有结果就只回一次结果"):
        out = chain.site(ctx, "cowork_line_save.py",
                         {**conf, "run": chain.tag(ctx), "seller_tax": ticket["supplier"]["tax_id"], "seller_name": ticket["supplier"]["name"],
                          "amount": ticket["expect"]["amount"], "vat": ticket["expect"]["vat"]},
                         timeout=600, product="cowork", name=intake.SITE, target=intake.SITE_TARGET)
        record, here, delta = out.get("record") or {}, out.get("here") or [], out.get("usage_delta") or {}
        if record.get("status") == "manual":
            raise Unmet(f"推送停在等人处理：{record.get('reason')}")
        ctx.require("推送状态", "success", record.get("status"))
        ctx.check("回显了 Express 单号", True, bool(record.get("docnum")))
        loading = [call for call in here if call["kind"] == "start"]
        ctx.check("开了 LINE loading，秒数 = LOADING_SECONDS", [out.get("loading_seconds")], [call.get("seconds") for call in loading])
        ctx.check("记下了等结果的键", True, out.get("noted"))
        ctx.check("全程没有推送（站点计数 push_* 不变）", {}, {key: value for key, value in delta.items() if key.startswith("push")})
        ctx.check("全程没有推送（本进程没有 push 请求）", [], [call for call in here if call["kind"] == "push"])
        ctx.check("这张票名下只有这一条推送记录", 1, out.get("pushes"))
        fallback = [call for call in here if call["kind"] == "reply"]
        settled = out.get("settled_after")
        if settled is not None and settled < float(out.get("wait_seconds") or 55):
            ctx.check("55 秒内有结果：回复恰好一次（站点计数）", 1, sum(delta.get(key, 0) for key in REPLY_KINDS))
            ctx.check("55 秒内有结果：本进程没有回兜底", [], fallback)
        else:
            texts = [text for call in fallback for text in call.get("texts") or []]
            ctx.check("55 秒还没结果：本进程回了一次兜底", 1, len(fallback))
            ctx.facts["fallback"] = " / ".join(str(text) for text in texts)[:200]
        ctx.facts.update(docnums=[record["docnum"]], ledger=chain.LEDGER, erp_doc=out.get("history"), settled_after=str(settled))

    with ctx.step("账本读回", "账套里按单号读得到这张单，表头与票面一致；预期以外的表逐字节不变"):
        after, summary = chain.copy_and_diff(ctx, book, conf, intake.purchase_tables(chain))
        rows = chain.rows(book, after, "APTRN", f"DOCNUM={record['docnum']}")
        ctx.save("aptrn-row.json", rows)
        ctx.require("APTRN 里这张单恰好一行", 1, len(rows))
        ctx.check("供应商编码", ticket["expect"]["supplier_code"], str(rows[0].get("SUPCOD") or "").strip())
        ctx.check("含税合计", chain.money(out["expected"]["total"]), chain.money(rows[0].get("NETAMT")))
        ctx.check("APTRN 只多一行", 1, chain.grew(summary, "APTRN"))
        ctx.check("凭证多一张", 1, chain.grew(summary, "GLJNL"))


def cleanup(ctx):
    _intake()._chain().cleanup(ctx)
