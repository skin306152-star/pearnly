"""验收场景：Cowork 录入页「提交」一张采购票 → 推送 → CC 桌面版写进 Express 验收账套 → 单号回显 → 账本读回 → 恢复基线。

编排 B。服务端跟 `PEARNLY_ACC_SITE_TARGET`：maclab → 本机 Mac 实验栈 cowork.maclab.localhost（A7 两格 11-19 就在这里通过；
这条场景不跑识别，不受「Mac 栈站点名不在识别回放名单里」的限制），lab → 测试机实验栈 testcowork（域名切过去）。
默认不再指向线上测试租户（11-23：办公室出口连不上生产时不排连线上的场景）。模型不参与、识别不跑（票的内容写死，取自验收样票库 purchase-a2），费用 0。

  acc.cowork.intake.express         提交 → 写成一张采购单，读回表头（供应商、金额、税、合计）
  acc.cowork.intake.express.twice   写成后同一张票再提交一次 → 账本里仍只多一张
  acc.cowork.intake.detail.amount_only  A7：票面明细合计 ≠ 税前（税前 100、只有一行 40）→ 引导卡问「明细」→ 只记金额 → 写成只有表头与分录（STCRD 无行）
  acc.cowork.intake.detail.skip         A7：同一类票答「不入账」→ 不写，停在 items_mismatch，账本与基线逐文件一致
（A7 两格 11-19 首跑在本机 Mac 实验栈 cowork + 测试机2 通过：PEARNLY_ACC_SITE_TARGET=maclab、PEARNLY_ACC_COWORK_SITE=cowork.maclab.localhost；
答卡要站点配好智能体引擎——只建会话、不发消息，模型费 0）

**状态：写好未跑**（11-15 晚）。不在这条场景里的：上传、识别（回放）、读票——识别回放要先给一份字节固定的样票录一次真识别结果，
并且站点的识别引擎不能是 Claude（那条路不经回放、照样付费），见任务记录「Cowork 两条」一节，等主会话定。

参数（环境变量）：
  PEARNLY_ACC_COWORK_USER         测试机 CC 桌面版上登录的 Cowork 员工
  PEARNLY_ACC_COWORK_CONNECTION   接着验收账套的那条 Express 连接
  PEARNLY_ACC_BASELINE、PEARNLY_ACC_LEDGER、PEARNLY_ACC_LEDGER_HOST 同别的场景；PEARNLY_ACC_COWORK_SITE 默认按站点目标
  （maclab → cowork.maclab.localhost，lab → testcowork.pearnly.com）
"""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
Unmet = sys.modules["acc_errors"].Unmet
SITE_TARGET = os.environ.get("PEARNLY_ACC_SITE_TARGET", "lab")
SITE = os.environ.get("PEARNLY_ACC_COWORK_SITE") or ("cowork.maclab.localhost" if SITE_TARGET == "maclab" else "testcowork.pearnly.com")
SAMPLE = "purchase-a2"   # 服务类的票（只记账），供应商在验收账套基线里有


def _chain():
    if "acc_erp_chain" not in sys.modules:
        spec = importlib.util.spec_from_file_location("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["acc_erp_chain"] = module
        spec.loader.exec_module(module)
    return sys.modules["acc_erp_chain"]


def sample() -> dict:
    import tomllib

    data = tomllib.loads((ROOT / "tests" / "acceptance" / "samples.toml").read_text(encoding="utf-8"))
    return next(item for item in data["sample"] if item["id"] == SAMPLE)


def settings(chain) -> dict:
    need = {"user": "PEARNLY_ACC_COWORK_USER", "connection": "PEARNLY_ACC_COWORK_CONNECTION", "baseline": "PEARNLY_ACC_BASELINE"}
    values = {key: os.environ.get(name, "") for key, name in need.items()}
    missing = [need[key] for key, value in values.items() if not value]
    if missing:
        raise Unmet("没配：" + "、".join(missing) + "（见场景文件头）")
    return {**values, "ledger": chain.LEDGER}


def purchase_tables(chain) -> list[str]:
    base = chain.tuple_of("companion/src/companion/dbf_schema.py", "BACKUP_TABLES")
    return [name for name in base if name not in ("ARTRN", "ARBAL", "ARMAS")] + ["STLOC", "ARTRNRM", "GLBAL"]


def run(ctx):
    chain = _chain()
    conf, ticket = settings(chain), sample()
    twice = ctx.scenario["id"].endswith(".twice")
    detail = ctx.scenario["id"].rsplit(".", 1)[-1] if ".intake.detail." in ctx.scenario["id"] else ""
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)
    target = SITE_TARGET
    if detail:
        a7(ctx, chain, conf, book, ticket, detail, target)
        return

    with ctx.step("提交并等写入", "票提交后建推送记录、确认后进写账队列，CC 桌面版写进验收账套并回执"):
        out = chain.site(ctx, "cowork_intake_post.py",
                         {**conf, "run": chain.tag(ctx), "seller_tax": ticket["supplier"]["tax_id"], "seller_name": ticket["supplier"]["name"],
                          "amount": ticket["expect"]["amount"], "vat": ticket["expect"]["vat"], "resubmit": twice},
                         timeout=600, product="cowork", name=SITE, target=target)
        record = out["record"]
        if record.get("status") == "manual":
            raise Unmet(f"推送停在等人处理：{record.get('reason')}")
        ctx.require("推送状态", "success", record.get("status"))
        ctx.check("回显了 Express 单号", True, bool(record.get("docnum")))
        ctx.check("CC 桌面版写后读回", "ok", record.get("verify"))
        ctx.check("真的写了（不是认作重复跳过）", [True, False], [record.get("written"), record.get("duplicate_skipped")])
        chain.only_ledger(ctx, [record.get("written_to")])
        ctx.check("票上记着这条推送成功", ["success", record["name"]], [out["last"]["last_push_status"], out["last"]["last_push_record"]])
        if twice:
            # 再提交是被挡、回同一条、还是另建一条被 CC 认作重复跳过，都可以；对错由下面账本读回的「只多一行」判
            second = out.get("resubmit") or {}
            ctx.require("确实又提交了一次", True, bool(second))
            ctx.check("再提交没有写出另一个单号", True, ((second.get("record") or {}).get("docnum") or record["docnum"]) == record["docnum"])
        ctx.facts.update(docnums=[record["docnum"]], ledger=chain.LEDGER, erp_doc=out["history"])

    with ctx.step("账本读回", "账套里按单号读得到这张单，表头与票面一致；预期以外的表逐字节不变"):
        after, summary = chain.copy_and_diff(ctx, book, conf, purchase_tables(chain))
        rows = chain.rows(book, after, "APTRN", f"DOCNUM={record['docnum']}")
        ctx.save("aptrn-row.json", rows)
        ctx.require("APTRN 里这张单恰好一行", 1, len(rows))
        ctx.check("供应商编码", ticket["expect"]["supplier_code"], str(rows[0].get("SUPCOD") or "").strip())
        ctx.check("含税合计", chain.money(out["expected"]["total"]), chain.money(rows[0].get("NETAMT")))
        ctx.check("税额", chain.money(out["expected"]["vat"]), chain.money(rows[0].get("VATAMT")))
        ctx.check("APTRN 只多一行", 1, chain.grew(summary, "APTRN"))
        ctx.check("进项税登记恰好一行", 1, len(chain.rows(book, after, "ISVAT", f"DOCNUM={record['docnum']}")))
        ctx.check("凭证多一张", 1, chain.grew(summary, "GLJNL"))


def a7(ctx, chain, conf, book, ticket, detail, target):
    """A7「明细对不上」（guided-erp / 引擎窗口 11-17 定，被测 cc-remembered-every-basis）：只在 Cowork 出这一题。"""
    with ctx.step("造票、答卡、提交", "税前 100、只有一行 40 的票；引导卡问「明细」，按本格作答（卡上其余题照预选或第一个现成选项）"):
        out = chain.site(ctx, "cowork_intake_post.py",
                         {**conf, "run": chain.tag(ctx), "seller_tax": ticket["supplier"]["tax_id"], "seller_name": ticket["supplier"]["name"],
                          "amount": "100", "vat": "7", "detail": detail},
                         timeout=900, product="cowork", name=SITE, target=target)
        ctx.save("card-answers.json", {"kinds": out.get("card_kinds"), "answers": out.get("answers"), "detail": out.get("detail")})
        ctx.require("引导卡问了「明细」", True, "detail" in (out.get("card_kinds") or []))
        ctx.check("「明细」按本格作答", detail, (out.get("detail") or {}).get("value"))
    if detail == "skip":
        with ctx.step("不入账：不写", "准备阶段就停在「明细对不上」，不进写账队列；账本与基线逐文件一致"):
            prepared = out.get("prepared") or {}
            ctx.check("停在等人处理、原因是明细对不上", ["manual", "items_mismatch"], [prepared.get("status"), prepared.get("reason")])
            ctx.check("账套与基线逐文件一致", True, bool(book.verify(conf["baseline"]).get("ok")))
        return
    record = out.get("record") or {}
    with ctx.step("只记金额：写成", "写成一张采购单并回执"):
        ctx.require("推送状态", "success", record.get("status"))
        ctx.check("CC 桌面版写后读回", "ok", record.get("verify"))
        ctx.check("真的写了（不是认作重复跳过）", [True, False], [record.get("written"), record.get("duplicate_skipped")])
        chain.only_ledger(ctx, [record.get("written_to")])
        ctx.facts.update(docnums=[record.get("docnum") or ""], ledger=chain.LEDGER, erp_doc=out.get("history"))
    with ctx.step("账本读回", "只有表头与分录：APTRN 一行（税前 100、税 7、合计 107）、STCRD 没有这张单的行、凭证一张、进项税一行"):
        after, summary = chain.copy_and_diff(ctx, book, conf, purchase_tables(chain))
        rows = chain.rows(book, after, "APTRN", f"DOCNUM={record['docnum']}")
        ctx.save("aptrn-row.json", rows)
        ctx.require("APTRN 里这张单恰好一行", 1, len(rows))
        ctx.check("表头金额（税前 / 税 / 合计）", [100.0, 7.0, 107.0],
                  [chain.money(rows[0].get("AMOUNT")), chain.money(rows[0].get("VATAMT")), chain.money(rows[0].get("NETAMT"))])
        ctx.check("没有存货明细行（STCRD）", 0, len(chain.rows(book, after, "STCRD", f"DOCNUM={record['docnum']}")))
        ctx.check("凭证多一张", 1, chain.grew(summary, "GLJNL"))
        ctx.check("进项税登记恰好一行", 1, len(chain.rows(book, after, "ISVAT", f"DOCNUM={record['docnum']}")))


def cleanup(ctx):
    _chain().cleanup(ctx)
