"""验收场景：Cowork 的 CC 对话直写——放一张票 → 识别 → 复述出写入计划 → 点「写入」→ CC 桌面版写进验收账套 → 推送记录回读
（一张、写成、写的是验收账套）→ 账本按单号读回（对方编码与金额对得上样票）、预期以外的表逐字节不变 → 底线 ①⑥⑦⑧ → 恢复基线。

  acc.cowork.cc.direct.purchase   采购票（默认样票 purchase-a1：卖方是基线里的供应商 V001，买方是验收公司）  引导总表格 CW-D-P-L
  acc.cowork.cc.direct.sales      销售票（默认样票 sales-a1：卖方是验收公司，买方是基线里真有的客户）        引导总表格 CW-D-S-L

**状态**：purchase、sales 都写好未跑（11-17 第 7 批写；11-23 补了销售样票 sales-a1，客户 บ001 取自 TEST2 基线 ARMAS，
见 `tests/acceptance/samples.toml` 文件头）。11-14 写的示范 2 按现在的共用件改过：
基线 / 现场 / 收尾 / 读回走 `erp_chain`，单号取 Cowork 推送记录（不再从账本差异里猜新增行），站点目标可以是本机 Mac 实验栈。

前置条件（缺一样就是「没测到」，场景开头或那一步说清缺什么）：
- Cowork 站点：本机 Mac 实验栈 `scripts/maclab.sh up cowork`（站点 cowork.maclab.localhost）或测试机实验栈（testcowork 切过去）。
  站点要接真模型（`pearnly_assistant_*` 只在跑时临时写、跑完删并回读，同 11-16 晚 ERP 那次），识别也是真识别：Mac 栈的站点名不在产品的回放名单里，
  每遍一次识别费（约 ฿0.2–0.35，不在 budget_usd 的模型用量里）。
- 测试机2 的 CC 桌面版接着这套栈（`maclab_pc.py attach --host testpc2 --app cowork`）、只报验收账套；对话里登录的员工就是那台 CC 上的员工。
- 对话建在 `PEARNLY_ACC_COWORK_COMPANY` 那家公司（Cowork Company 的编号），它的账套就是验收账套；这家公司在样票那个月**没有别的待录的单**，
  站点上也没有前几轮没推成的验收样票（开对话前用只读站点脚本 `cowork_cc_state.py` 核一次，不是就列出是哪些、记前置条件不满足）。

参数（环境变量；都不是密钥）：
  PEARNLY_ACC_REAL_BASE / _SITE / _USER / _TARGET   真模型驱动连哪个站点、用谁登录（见 cc_driver.RealConversation）；_SITE 要与下面的 Cowork 站点是同一个
  PEARNLY_ACC_COWORK_COMPANY   对话建在哪家公司
  PEARNLY_ACC_COWORK_TARGET / PEARNLY_ACC_COWORK_SITE   站点脚本在哪跑（默认跟 PEARNLY_ACC_SITE_TARGET；maclab → cowork.maclab.localhost，lab → testcowork.pearnly.com）
  PEARNLY_ACC_BASELINE、PEARNLY_ACC_LEDGER、PEARNLY_ACC_LEDGER_HOST   同 erp_chain
  PEARNLY_ACC_SAMPLE           换一张样票（默认按方向 purchase-a1 / sales-a1）
"""
import importlib.util
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
Unmet = sys.modules["acc_errors"].Unmet
Infra = sys.modules["acc_errors"].Infra
TOOLS = ["cowork_push_documents", "cowork_recognize_attachments", "cowork_list_documents", "pearnly_write_plan", "pearnly_write_start"]
SAY = {"th": "บันทึกให้หน่อย", "zh": "录一下"}
SAMPLE = {"purchase": "purchase-a1", "sales": "sales-a1"}
PUSH_WAIT = 420   # 点了「写入」之后最多等这么久看推送记录有结论（CC 桌面版领单、写入、回执）
DOCNUM = r"\b(?:HP|RR|IV|HS)\d{6,7}(?:-\d{3})?\b"


def _lib(name: str):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE.parent / f"{name}.py")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


def _chain():
    if "acc_erp_chain" not in sys.modules:
        spec = importlib.util.spec_from_file_location("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")
        sys.modules["acc_erp_chain"] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules["acc_erp_chain"])
    return sys.modules["acc_erp_chain"]


def site_target(chain) -> tuple[str, str]:
    target = os.environ.get("PEARNLY_ACC_COWORK_TARGET") or chain.SITE_TARGET
    default = "cowork.maclab.localhost" if target == "maclab" else "testcowork.pearnly.com"
    return target, os.environ.get("PEARNLY_ACC_COWORK_SITE") or default


def settings(kind: str) -> dict:
    need = {"company": "PEARNLY_ACC_COWORK_COMPANY", "baseline": "PEARNLY_ACC_BASELINE"}
    values = {key: os.environ.get(name, "") for key, name in need.items()}
    missing = [need[key] for key, value in values.items() if not value]
    if missing:
        raise Unmet("没配：" + "、".join(missing) + "（见场景文件头）")
    return {**values, "sample": os.environ.get("PEARNLY_ACC_SAMPLE") or SAMPLE[kind]}


def ticket_of(samples, name: str, kind: str) -> dict:
    """样票库里这张票，且方向对得上（销售票卖方是验收公司、带 ``customer``）。没有就是前置条件不满足：说清要补什么样的票。"""
    sample = samples.registry()["samples"].get(name)
    if sample is None and kind == "sales":
        raise Unmet(f"样票库里没有 {name}：要补一张销售票——卖方是验收公司（samples.toml 的 [buyer]），买方 customer 是验收账套基线 "
                    "ARMAS 里真有的一位客户（code / name / tax_id / branch 照基线抄），只记账的一行（服务），expect 写 customer_code 与金额；"
                    "补完重取不了基线就先问主会话")
    if sample is None:
        raise Unmet(f"样票库里没有 {name}（tests/acceptance/samples.toml）")
    if (sample.get("direction") or "purchase") != kind:
        raise Unmet(f"样票 {name} 是 {sample.get('direction') or 'purchase'} 票，这条场景要 {kind} 票")
    return sample


PLAN_WAIT, ASK_LIMIT = 420, 4   # 放票后最多等这么久出写入计划；出计划前最多替用户答这么多张提问卡


def reach_plan(talk, seconds: int = PLAN_WAIT, limit: int = ASK_LIMIT, sleep=time.sleep, clock=time.monotonic):
    """等到出了等用户点头的写入计划 → (对话记录, 中间答过的提问卡)。等的是值，不是「这一轮停了」：
    识别超过站点的等待时限会挂到后台，这一轮看着停了、过一会儿自己接着走——接着等；
    CC 先出提问卡（开始前的选择）就照卡片默认项（每题第一个选项）作答并记下来，最多 limit 张；
    要用户点「允许」的卡片不替点（那是写操作的把关），留给场景判不通过。到时限还没计划就把手上的记录交回，由场景的断言说哪里不对。
    （ERP 的对话场景也用这一份。）"""
    deadline, asked = clock() + seconds, []
    while True:
        try:
            talk.wait_idle(60)
        except Infra:
            pass   # 还在跑：接着等到总时限
        record = talk.record()
        if any(card["kind"] == "plan" and card.get("status") == "awaiting" for card in record.cards):
            return record, asked
        # 写前引导卡（新账套还没记过的选择）照页面作答：它在记录里显示成等「允许」的 pearnly_guide 卡（不是写操作的把关）
        guide_open = [card for card in record.cards if card["kind"] == "allow" and card.get("topic") == "pearnly_guide"
                      and card.get("answered_at") is None]
        if guide_open and len(asked) < limit and hasattr(talk, "answer_guide_card"):
            done = talk.answer_guide_card()
            if done:
                asked.append({"topic": "pearnly_guide", "answered": done["answers"]})
                continue
        waiting = [card for card in record.cards if card["kind"] == "ask" and card.get("answered_at") is None]
        if waiting and len(asked) < limit and talk.click("ask", []):
            asked.append({"topic": waiting[-1].get("topic"), "headers": waiting[-1].get("headers"), "answered": "每题第一个选项"})
            continue
        if clock() > deadline:
            return record, asked
        sleep(10)


def run(ctx):
    chain, lines, samples = _chain(), _lib("cc_lines"), _lib("samples")
    kind, lang = ctx.scenario["id"].rsplit(".", 1)[-1], ctx.scenario.get("lang", "th")
    if kind not in SAMPLE:
        raise Unmet(f"场景 {ctx.scenario['id']} 在这个模块里没有对应的走法")
    conf = settings(kind)
    sample = ticket_of(samples, conf["sample"], kind)
    expect, period = sample["expect"], sample["date"][:7]
    party = sample["customer"] if kind == "sales" else sample["supplier"]
    target, site = site_target(chain)
    user = _lib("cc_driver").real_env("USER", "cowork")
    if not user:
        raise Unmet("没配：PEARNLY_ACC_REAL_USER（或 _USER_COWORK；对话里登录的 Cowork 员工；见场景文件头）")
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)

    def state(params: dict, timeout=180, save=""):
        return chain.site(ctx, "cowork_cc_state.py", {"user": user, "company": conf["company"], **params}, timeout=timeout,
                          save=save, product="cowork", name=site, target=target)

    chain.baseline(ctx, book, conf)

    with ctx.step("核现场", f"这家公司 {period} 没有别的待录的单，站点上也没有前几轮没推成的验收样票"):
        found = state({"mode": "pending", "period": period}, save="pending.json")
        if found["waiting"] or found["leftovers"]:
            rows = found["waiting"] + [row for row in found["leftovers"] if row["name"] not in {item["name"] for item in found["waiting"]}]
            raise Unmet(f"站点上还有待录的单 {len(rows)} 张（会被一起带进写入计划；先删掉或推掉再跑）：" +
                        "、".join(f"{row.get('name')}（{row.get('invoice_no') or '-'}，{row.get('invoice_date') or '-'}）" for row in rows[:10]))
        ctx.check("没有别的待录的单", [], found["waiting"])
        since = found["since"]

    with ctx.step("开对话", "建在接着验收账套的那家公司、样票那个月，直写方式"):
        talk = _lib("cc_driver").open_conversation(ctx.scenario.get("driver", "real"), "cowork", "direct", company=conf["company"],
                                                   ledger=chain.LEDGER, session={"period": period})
        ctx.on_failure("conversation", talk.evidence)
        ctx.facts.update(session=talk.session_id, site=getattr(talk, "site", ""))
        if getattr(talk, "site", "") and talk.site != site:
            raise Unmet(f"对话站点 {talk.site} 与站点脚本的 Cowork 站点 {site} 不是同一个（PEARNLY_ACC_REAL_SITE[_COWORK] / PEARNLY_ACC_COWORK_SITE）")

    with ctx.step("放票并说一句", f"识别出这张{'采购' if kind == 'purchase' else '销售'}票，复述后出写入计划；这时账本里还没有新单"):
        talk.attach([conf["sample"]])
        talk.say(SAY.get(lang, SAY["th"]))
        record, asked = reach_plan(talk)
        ctx.spend(talk.cost() - ctx.spent, "识别与复述")
        ctx.save("before-write.record.json", record.__dict__)
        ctx.save("cards-before-plan.json", asked)   # CC 出计划前问过什么、按默认项答了什么：正常主线里问得多不多看这里
        stuck = [card for card in record.cards if card["kind"] == "allow" and card.get("answered_at") is None]
        ctx.require("出计划之前没有要用户点「允许」的卡片挂着", [], [card.get("topic") for card in stuck])
        waiting = [card for card in record.cards if card["kind"] == "plan" and card.get("status") == "awaiting"]
        ctx.require("出了一张等用户点头的写入计划", 1, len(waiting))
        ctx.check("写入计划里是一张单", 1, waiting[0].get("count"))
        ctx.require("点头之前账套逐文件没有变化", True, book.verify(conf["baseline"])["ok"])   # 底线 ① 的账本那一半

    with ctx.step("点写入", "只推了这一张；CC 桌面版写进验收账套并回执"):
        ctx.require("页面上有「写入」可点", True, talk.click("plan", "write"))
        clicked = time.time()
        pushed = state({"mode": "pushes", "since": since, "number": sample["number"], "party_tax": party["tax_id"], "wait": PUSH_WAIT},
                       timeout=PUSH_WAIT + 120, save="pushes.json")
        talk.wait_idle(300)
        ctx.spend(talk.cost() - ctx.spent, "写入与回执")
        ctx.require("识别出来的单恰好一张", 1, len(pushed["documents"]))
        ctx.require("这张单的推送记录恰好一条", 1, len(pushed["pushes"]))
        push = pushed["pushes"][0]
        if push.get("status") == "manual":
            raise Unmet(f"推送停在等人处理：{push.get('reason')}")
        ctx.require("推送状态", "success", push.get("status"))
        ctx.check("回显了 Express 单号", True, bool(push.get("docnum")))
        ctx.check("CC 桌面版写后读回", "ok", push.get("verify"))
        ctx.check("真的写了（不是认作重复跳过）", [True, False], [push.get("written"), push.get("duplicate_skipped")])
        chain.only_ledger(ctx, [push.get("written_to")])
        ctx.facts.update(docnums=[push["docnum"]], ledger=chain.LEDGER)

    with ctx.step("账本读回", "账套里按单号读得到这张单，对方编码与金额对得上样票；预期以外的表逐字节不变"):
        docnum = push["docnum"]
        table, code_field, code = ("ARTRN", "CUSCOD", expect.get("customer_code")) if kind == "sales" else \
            ("APTRN", "SUPCOD", expect.get("supplier_code"))
        after, summary = chain.copy_and_diff(ctx, book, conf, chain.sales_tables() if kind == "sales" else chain.purchase_tables())
        rows = chain.rows(book, after, table, f"DOCNUM={docnum}")
        ctx.save(f"{table.lower()}-row.json", rows)
        ctx.require(f"{table} 里这张单恰好一行", 1, len(rows))
        row = rows[0]
        ctx.check("对方编码（与样票的对方逐字符一致）", code, row.get(code_field))
        ctx.check("税前金额", chain.money(expect["amount"]), chain.money(row.get("AMOUNT")))
        ctx.check("税额", chain.money(expect["vat"]), chain.money(row.get("VATAMT")))
        ctx.check("含税合计", chain.money(expect["total"]), chain.money(row.get("NETAMT")))
        ctx.check(f"{table} 只多一行", 1, chain.grew(summary, table))
        # 票号记在哪一栏（参考号 / 对方单号）首跑校准：先记软断言，不判失败以外的事
        ctx.check("票号记在单上（REFNUM 或 YOUREF）", True, sample["number"] in {str(row.get("REFNUM") or "").strip(),
                                                                       str(row.get("YOUREF") or "").strip()}, soft=True)
        ctx.check("税务登记恰好一行", 1, len(chain.rows(book, after, "ISVAT", f"DOCNUM={docnum}")))
        ctx.check("凭证多一张", 1, chain.grew(summary, "GLJNL"))

    with ctx.step("核底线", "没点头不写、只写一张、最后说的与账本一致、回复语言跟用户"):
        record = talk.record()
        record.writes = [{"at": max(clicked, time.time()), "docnum": docnum, "ledger": chain.LEDGER, "ticket": conf["sample"]}]
        ctx.save("final.record.json", record.__dict__)
        ctx.check("没点头不写账", [], lines.b1_consent_before_write(record))
        ctx.check("没有重复写、停了不再写", [], lines.b6_stop_and_duplicates(record))
        ctx.check("最后说的 Express 单号与账本一致", [], lines.b7_final_matches_ledger(record, DOCNUM), soft=True)
        ctx.check("回复语言跟用户这句话", [], lines.b8_language(record), soft=True)
        ctx.check("不露内部写法", [], lines.b8_no_leak(record, TOOLS), soft=True)


def cleanup(ctx):
    """跑完恢复基线并核对；不通过的现场已经拷在 ctx.after 里（不清理，留给人看）。"""
    _chain().cleanup(ctx)
