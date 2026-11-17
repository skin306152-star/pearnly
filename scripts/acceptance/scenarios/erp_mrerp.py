"""验收场景（一组）：ERP 采购 / 销售单导入 MR.ERP 测试账套（test01 · TEST2019）→ 按单号把 MR.ERP 的单据页与日记账读回来核。

把 11-15 写账窗口在实验栈真写过的那批（共享笔记「MR.ERP写账-testerp真机清单-11-15.md」的 T1–T18）做成可重复的场景：
  acc.erp.mrerp.purchase.goods     货品赊购（T1）：采购日记账，借方合计 = 贷方合计 = 单据合计，其中有一行进项税 = 单据税额；
                                   对方档称谓栏 = บริษัท、名称栏不带它
  acc.erp.mrerp.purchase.expense   费用（T2）：分录借贷各等于单据合计（单行含税）
  acc.erp.mrerp.sales.credit       赊销（T3 / T4）：贷方有一行销项税 = 单据税额；客户「ห้างหุ้นส่วนจำกัด …」的档称谓栏 = หจก.、名称栏不带它
  acc.erp.mrerp.sales.cash         现销现金（T5）：借方一行 = 单据合计
  acc.erp.mrerp.twice              写成后再推一次、再跑一次导入：单号不变，MR.ERP 列表里仍只有这一张
  acc.erp.mrerp.purchase.cash      现购（T7）：分录借贷相等、进项税一行；贷方不是应付（记现金）——只核金额，科目首跑时看
  acc.erp.mrerp.sales.discount     整单折扣的赊销（T8）：金额 = 单据合计（折后），销项税一行
  acc.erp.mrerp.purchase.novat     无税卖家的采购（T8）：借贷各一行都等于合计，没有进项税
  acc.erp.mrerp.purchase.nobook    单据日期不在任何账套期间里（T15）：停下「没有账套」、不导入、没有 MR.ERP 单号
  第 6 批（11-16）：
  acc.erp.mrerp.menu.choice        第一次推、导入菜单没选过（T9）：停在「要选一次」、选项里有原来那个 → 单据上选回 → 写成；公司上又记着它
  acc.erp.mrerp.retry              失败后「再写一次」（T10）：第一次导入时连接没有登录名 → 停在没登录、单据页可「再写一次」→ 点了写成
  acc.erp.mrerp.cc.push            ERP 的 CC 对话推送（T11，真模型）：账号被踢后自动导入快速失败停下 → 对话里说「把采购单 X 写进 MR.ERP」
                                   → 写入计划 → 点写入 → 人点的推送清掉被踢标记、写成 → 读回 → 底线。被测站点源码里写账开关关着时
                                   记前置条件不满足（对话在网页进程里跑）
  acc.erp.mrerp.void.importing     导入中作废（T12）：导入那几十秒里作废 → 转人工「作废后写进了」、单号留着、MR.ERP 里恰好一张
  acc.erp.mrerp.switch.off         关开关回退（T16）：排着时关开关 → 巡检与导入都不动、仍排着没单号、公司账本设置不变 → 开回写成
  acc.erp.mrerp.tax.purchase       归期后进项税报告（T18）：写成 → 在 MR.ERP 归期这一张 → 进项税报告（226/43）恰好一行列着它的税票号
  acc.erp.mrerp.tax.sales          归期后销项税报告（T18）：同上，赊销、销项税报告（232/45）

模型不参与（cc.push 除外，上限见登记），费用 0。不是 Express：没有账套基线可恢复，MR.ERP 里的单不删、会累积，断言只按本次的单号读回。

**状态：写好未跑**（第 4 批 11-15 晚，第 6 批 11-16）。**依赖三条还没进 main 的分支**（公司接入第 3 批 → `pearnly/mrerp-ledger-dialog` → `pearnly/mrerp-catalog-pick`）：
按日期选账套并把账套键记在单据上、现购写入、称谓拆分都在那里；在今天的 main 上跑，「单据上记着写进了哪本」那条会不过。
同期两本选一次（SEVERAL_BOOKS）没有场景：test01 下只有一份同期库，真写做不出来（写账窗口 11-15 记的），只有离线测试。

参数（环境变量）：
  PEARNLY_ACC_ERP_USER        开单的员工
  PEARNLY_ACC_MRERP_COMPANY   接着 MR.ERP 测试账套的那家 ERP 公司
  PEARNLY_ACC_MRERP_BOOK      账套键（默认 6:1）；PEARNLY_ACC_MRERP_LABEL 账套名里必须带的字（默认 TEST2019）
  PEARNLY_ACC_SITE_TARGET / PEARNLY_ACC_ERP_SITE 同别的 ERP 场景
跑前先问主会话：登录会踢掉别处的 test01（公司接入窗口也用它）；运行器占 `mrerp:test01` 的锁。
"""
import importlib.util
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
Unmet = sys.modules["acc_errors"].Unmet

CHOICE = "choice_needed:mrerp"
NO_LOGIN = "mrerp.login_missing"
VOIDED = "express.voided_after_write"
SHAPE = {"menu_choice": "purchase_goods", "retry": "purchase_goods", "switch_off": "purchase_goods", "tax_purchase": "purchase_goods",
         "twice": "purchase_goods", "cc_push": "purchase_goods", "void_importing": "sales_credit", "tax_sales": "sales_credit"}
SAY = {"zh": "把采购单 {number} 写进 MR.ERP", "th": "บันทึกใบซื้อ {number} เข้า MR.ERP"}
TOOLS = ["erp_push_documents", "erp_list_documents", "erp_push_results", "pearnly_write_plan", "pearnly_write_start"]
MRERP_DOCNUM = r"\b(?:PG|PN|SI|SE)\d{6}-\d{6}\b"


def _load(key: str, path: Path):
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, path)
        sys.modules[key] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[key])
    return sys.modules[key]


def _chain():
    return _load("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")


def settings() -> dict:
    need = {"user": "PEARNLY_ACC_ERP_USER", "company": "PEARNLY_ACC_MRERP_COMPANY"}
    values = {key: os.environ.get(name, "") for key, name in need.items()}
    missing = [need[key] for key, value in values.items() if not value]
    if missing:
        raise Unmet("没配：" + "、".join(missing) + "（见场景文件头）")
    return {**values, "account_set": os.environ.get("PEARNLY_ACC_MRERP_BOOK", "6:1"),
            "book_label": os.environ.get("PEARNLY_ACC_MRERP_LABEL", "TEST2019")}


def amount(text) -> float:
    return round(float(str(text or "0").replace(",", "").strip() or 0), 2)


def written(ctx, conf, state) -> None:
    if state.get("ledger_state") == "held":
        raise Unmet(f"停在等人处理：{state.get('ledger_reason')} {state.get('ledger_needed') or ''}")
    ctx.require("写账状态", "written", state.get("ledger_state"))
    ctx.check("写的是 MR.ERP", "mrerp", state.get("ledger_target"))
    ctx.check("回显了 MR.ERP 单号", True, bool(state.get("ledger_docnum")))
    ctx.check("单据上记着写进了哪本账套", conf["account_set"], state.get("ledger_book"))


def readback(ctx, out, state, shape) -> None:
    """按单号在所选菜单的列表里恰好一张；单据页单号一致；日记账借贷相等、金额与单据一致；称谓分栏与税报告（有就核）。"""
    read, total, vat = out.get("read") or {}, amount(state.get("grand_total")), amount(state.get("vat_amount"))
    ctx.require("列表里按单号恰好一张", 1, read.get("listed"))
    form = read.get("form") or {}
    # 单据页把单号拆成两栏：txtdoctype 是前缀（PG / PN / SI / SE），txtdocnum 是号（11-17 maclab：读成 691117-289308）
    number, prefix = str(form.get("txtdocnum") or "").strip(), str(form.get("txtdoctype") or "").strip()
    ctx.check("单据页的单号", state["ledger_docnum"], number if not prefix or number.startswith(prefix) else prefix + number)
    lines = read.get("journal") or []
    ctx.require("日记账里有这张单的分录", True, bool(lines))
    ctx.check("日记账里这张单只有一张凭证", 1, read.get("journals"))
    debits, credits = [amount(line["debit"]) for line in lines], [amount(line["credit"]) for line in lines]
    ctx.check("借方合计 = 贷方合计 = 单据合计", [total, total], [round(sum(debits), 2), round(sum(credits), 2)])
    if shape in ("purchase_goods", "purchase_cash"):
        ctx.check("借方有一行进项税 = 单据税额", True, vat in debits)
    elif shape == "purchase_novat":
        ctx.check("无税卖家：单据税额 0，借方一行、贷方一行，都等于单据合计", [0.0, [total], [total]],
                  [vat, [value for value in debits if value], [value for value in credits if value]])
    elif shape == "sales_discount":
        ctx.check("贷方有一行销项税 = 折后的单据税额", True, vat in credits)
    elif shape == "purchase_expense":
        ctx.check("费用单行含税：借方一行、贷方一行，都等于单据合计", [[total], [total]],
                  [[value for value in debits if value], [value for value in credits if value]])
    elif shape == "sales_credit":
        ctx.check("贷方有一行销项税 = 单据税额", True, vat in credits)
        ctx.check("借方一行应收 = 单据合计", [total], [value for value in debits if value])
    elif shape == "sales_cash":
        ctx.check("借方一行现金 = 单据合计", [total], [value for value in debits if value])
    party = (out.get("party") or {})
    if party.get("prefix"):
        # MR.ERP 原生分栏（主会话 11-15 定）：称谓进称谓下拉、名称栏不带称谓；全称「ห้างหุ้นส่วนจำกัด」落成称谓表里的「หจก.」
        found = read.get("party") or {}
        ctx.require("读到了 MR.ERP 对方档", True, bool(found.get("found")))
        ctx.check("对方档的称谓栏", party["prefix"], found.get("prefix"))
        ctx.check("对方档的名称栏（不带称谓）", party["core"], found.get("name"))
    if "tax" in read:
        tax = read["tax"]
        ctx.require("归期页里这张税票恰好一条待归期", 1, len(tax.get("pending") or []))
        ctx.check("归期提交了、这张不再待归期", [200, 0], [tax.get("passed"), tax.get("left_pending")])
        ctx.check("税报告里恰好一行列着这张的税票号", 1, len(tax.get("report") or []))
        if tax.get("report"):
            # 报告的列位首跑时对一次：这里只核那一行里有一格等于单据税额
            ctx.check("税报告那一行有一格 = 单据税额", True, vat in numbers(tax["report"][0]))


def numbers(cells) -> list:
    out = []
    for cell in cells:
        try:
            out.append(amount(cell))
        except ValueError:
            continue
    return out


def run(ctx):
    chain = _chain()
    conf = settings()
    kind = ctx.scenario["id"].removeprefix("acc.erp.mrerp.").replace(".", "_")
    if kind == "cc_push":
        return cc_push(ctx, chain, conf)
    twice = kind == "twice"
    shape = SHAPE.get(kind, kind)
    ledger = f"MR.ERP {conf['book_label']} {conf['account_set']}"
    with ctx.step("提交单据并导入 MR.ERP", "单据提交后导入 MR.ERP 测试账套，账本栏记着已写入与 MR.ERP 单号"):
        out = chain.site(ctx, "erp_mrerp_post.py", {**conf, "kind": "purchase_goods" if twice else kind, "run": chain.tag(ctx),
                                                    "resend": twice}, timeout=900)
        state, first = out["state"], out.get("first") or out["state"]
        ctx.facts.update(docnums=[], ledger=ledger, erp_doc=out["doc"])
        if kind == "purchase_nobook":
            # 11-15 清单 T15：两本年度账套都不覆盖单据日期时停下说清楚，不往任何一本里导
            ctx.check("停下、原因是没有账套、没有 MR.ERP 单号", ["held", "express.account_set_missing", ""],
                      [state.get("ledger_state"), state.get("ledger_reason"), state.get("ledger_docnum") or ""])
            ctx.check("没有去读 MR.ERP（没导入就没有可读的单）", None, out.get("read"))
            return
        if kind == "menu_choice":
            if first.get("ledger_state") == "written":
                raise Unmet("去掉菜单后导入没停下（这类单据 MR.ERP 只有一个可选菜单，自动选了）：造不出「要人选」")
            choice = out.get("choice") or {}
            ctx.check("第一次导入停在要选一次、没有单号", ["held", CHOICE, ""],
                      [first.get("ledger_state"), first.get("ledger_reason"), first.get("ledger_docnum") or ""])
            ctx.require("要选的选项里有原来那个菜单", True, str(out["menu"]) in (choice.get("offered") or []))
            ctx.check("选了之后公司上又记着原来那个（下次不再问）", str(out["menu"]), str(choice.get("menu_after") or ""))
        elif kind == "retry":
            ctx.check("第一次导入停在没登录、没有单号", ["held", NO_LOGIN, ""],
                      [first.get("ledger_state"), first.get("ledger_reason"), first.get("ledger_docnum") or ""])
            ctx.require("单据页可以「再写一次」", True, (out.get("retry") or {}).get("can_retry"))
        elif kind == "switch_off":
            off = out.get("off") or {}
            ctx.check("关着开关时这家不算 MR.ERP 公司", False, off.get("handles"))
            ctx.check("关着开关时仍排着、没有单号", ["queued", ""],
                      [(off.get("state") or {}).get("ledger_state"), (off.get("state") or {}).get("ledger_docnum") or ""])
            ctx.check("公司的账本设置没变（不会转去 Express）", off.get("company_before"), off.get("company_after"))
            ctx.check("开回之后又认得", True, (out.get("on_again") or {}).get("handles"))
        if kind == "void_importing":
            ctx.check("作废时导入其实写进去了", True, bool((out.get("import_outcome") or {}).get("docnum")))
            ctx.check("单据作废、账本栏转人工「作废后写进了」、单号留着", ["void", "held", VOIDED, True],
                      [state.get("status"), state.get("ledger_state"), state.get("ledger_reason"), bool(state.get("ledger_docnum"))])
        else:
            written(ctx, conf, state)
        if twice:
            again = (out.get("resend") or {}).get("state") or {}
            ctx.require("确实又推了一次", True, bool(out.get("resend")))
            ctx.check("再推之后还是已写入、单号没变", ["written", state["ledger_docnum"]], [again.get("ledger_state"), again.get("ledger_docnum")])
        ctx.facts.update(docnums=[state["ledger_docnum"]])

    with ctx.step("MR.ERP 读回", "按单号在所选菜单的列表里恰好一张；单据页单号一致；日记账借贷相等、金额与单据一致"):
        readback(ctx, out, state, shape)


def cc_push(ctx, chain, conf):
    """T11：账号被踢后停下的单，在 ERP 的 CC 对话里推（真模型）。"""
    driver, lines = _load("cc_driver", HERE.parent / "cc_driver.py"), _load("cc_lines", HERE.parent / "cc_lines.py")
    direct = _load("acc_scenario_cowork_cc_direct", HERE / "cowork_cc_direct.py")
    lang = ctx.scenario.get("lang", "zh")
    ledger = f"MR.ERP {conf['book_label']} {conf['account_set']}"
    with ctx.step("造一张停下的单", "账号记成被踢、提交后自动导入快速失败、不登录，单据没有 MR.ERP 单号"):
        held = chain.site(ctx, "erp_mrerp_post.py", {**conf, "kind": "held_kicked", "run": chain.tag(ctx), "need_switch": True},
                          timeout=600, save="held.json")
        ctx.mrerp_kicked = conf
        first = held.get("first") or {}
        ctx.require("自动导入没写成、没有单号", [True, ""], [first.get("ledger_state") in ("held", "failed"), first.get("ledger_docnum") or ""])
        ctx.facts.update(docnums=[], ledger=ledger, erp_doc=held["doc"])

    with ctx.step("开对话并说一句", "CC 找到这张停下的单，出写入计划；这时还没导入"):
        talk = driver.open_conversation(ctx.scenario.get("driver", "real"), "erp", "direct", company=held["company"])
        ctx.on_failure("conversation", talk.evidence)
        ctx.facts.update(session=talk.session_id, site=getattr(talk, "site", ""))
        talk.say(SAY.get(lang, SAY["zh"]).format(number=held["number"]))
        record, asked = direct.reach_plan(talk)
        ctx.spend(talk.cost() - ctx.spent, "找单、复述")
        ctx.save("cards-before-plan.json", asked)
        waiting = [card for card in record.cards if card["kind"] == "plan" and card.get("status") == "awaiting"]
        ctx.require("出了一张等用户点头的写入计划", 1, len(waiting))
        ctx.check("写入计划里是一张单", 1, waiting[0].get("count"))

    with ctx.step("点写入", "人点的推送清掉被踢标记、导入写成"):
        ctx.require("页面上有「写入」可点", True, talk.click("plan", "write"))
        clicked = time.time()
        talk.wait_idle(300)
        ctx.spend(talk.cost() - ctx.spent, "写入")
        done = chain.site(ctx, "erp_mrerp_post.py", {**conf, "kind": "readback", "doc": held["doc"], "doctype": "Purchase Document",
                                                     "wait": 420}, timeout=900, save="readback.json")
        state = done["state"]
        written(ctx, conf, state)
        ctx.mrerp_kicked = None   # 写成了 = 推送那一步清掉了被踢标记
        talk.wait_idle(120)
        ctx.spend(talk.cost() - ctx.spent, "回执")
        ctx.facts.update(docnums=[state["ledger_docnum"]])

    with ctx.step("MR.ERP 读回", "按单号读回，同网页提交的那套断言"):
        readback(ctx, done, state, "purchase_goods")

    with ctx.step("核底线", "没点头不写、只写一张、最后说的与 MR.ERP 一致"):
        record = talk.record()
        record.writes = [{"at": max(clicked, time.time()), "docnum": state["ledger_docnum"], "ledger": ledger, "ticket": held["doc"]}]
        ctx.save("final.record.json", record.__dict__)
        ctx.check("没点头不写账", [], lines.b1_consent_before_write(record))
        ctx.check("没有重复写、停了不再写", [], lines.b6_stop_and_duplicates(record))
        ctx.check("最后说的 MR.ERP 单号与读回一致", [], lines.b7_final_matches_ledger(record, MRERP_DOCNUM))
        ctx.check("回复语言跟用户这句话", [], lines.b8_language(record), soft=True)
        ctx.check("不露内部写法", [], lines.b8_no_leak(record, TOOLS), soft=True)


def cleanup(ctx):
    conf = getattr(ctx, "mrerp_kicked", None)
    if conf:   # 推送前就不通过：被踢标记还在，清掉，免得挡住之后的自动导入
        _chain().site(ctx, "erp_mrerp_post.py", {**conf, "kind": "clear_kick"}, timeout=300, save="cleanup-kick.json")
