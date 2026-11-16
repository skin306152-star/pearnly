"""ERP 写账链路验收场景的共用件（公司接入 → 主档同步 → 赊销单 → 收款三处）：配置、基线、调站点脚本、账本读回的几样断言。

场景模块用 `load()` 取（场景文件按路径单独加载，不是包），骨架同示范 1（`scenarios/erp_purchase_express.py`）：
基线核对 → 走入口 → 写账状态与单号 → 账本按单号读回 → 预期以外的表逐字节不变 → 恢复基线。

环境变量同示范 1：PEARNLY_ACC_ERP_USER / _TAX / _BRANCH、PEARNLY_ACC_LEDGER、PEARNLY_ACC_LEDGER_HOST、PEARNLY_ACC_BASELINE、
PEARNLY_ACC_SITE_TARGET（lab / maclab）、PEARNLY_ACC_ERP_SITE。
"""
import ast
import json
import os
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE_DIR = ROOT / "scripts" / "acceptance" / "site"
Unmet = sys.modules["acc_errors"].Unmet
Infra = sys.modules["acc_errors"].Infra

LEDGER = os.environ.get("PEARNLY_ACC_LEDGER", r"\\accserver\d$\ACCOUNT\69EXP\TEST2")
HOST = os.environ.get("PEARNLY_ACC_LEDGER_HOST", "testpc2")
SITE_TARGET = os.environ.get("PEARNLY_ACC_SITE_TARGET", "lab")
SITE = os.environ.get("PEARNLY_ACC_ERP_SITE", "erp.maclab.localhost" if SITE_TARGET == "maclab" else "testerp.pearnly.com")
NBSP = "\xa0"


def settings() -> dict:
    need = {"user": "PEARNLY_ACC_ERP_USER", "company_tax": "PEARNLY_ACC_ERP_TAX", "baseline": "PEARNLY_ACC_BASELINE"}
    values = {key: os.environ.get(name, "") for key, name in need.items()}
    missing = [need[key] for key, value in values.items() if not value]
    if missing:
        raise Unmet("没配：" + "、".join(missing) + "（见 scripts/acceptance/erp_chain.py 文件头）")
    return {**values, "branch": os.environ.get("PEARNLY_ACC_ERP_BRANCH", ""), "ledger": LEDGER}


def run_id(ctx) -> str:
    return ctx.folder.parent.parent.name if ctx.attempt > 1 else ctx.folder.parent.name


def tag(ctx) -> str:
    """写进客户名 / 请求号的短标记：同一轮几条场景、同一条重跑都不撞名。一条命令跑一组（first_run）时各场景共用一个运行编号，
    所以再带场景编号的三位校验码（11-16 首跑：采购 .twice 与上一格开出同一个单号 ACC-<运行编号>，被产品按重复单号拒掉）。"""
    digits = "".join(ch for ch in run_id(ctx) if ch.isdigit())
    return f"{digits[-6:] or '0'}{scenario_code(ctx)}{ctx.attempt}"


def scenario_code(ctx) -> str:
    return f"{zlib.crc32(ctx.scenario['id'].encode('utf-8')) % 1000:03d}"


def after_dir(ctx, part: str = "") -> str:
    return f"{ctx.targets.windows(HOST).work}/runs/{run_id(ctx)}-{ctx.scenario['id']}-{ctx.attempt}{part}"


def tuple_of(path: str, name: str) -> list[str]:
    """读 CC 桌面版源码里的一份表清单（不另抄一份）：只认字面量元组。"""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return list(next(ast.literal_eval(node.value) for node in tree.body
                     if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name))


def sales_tables() -> list[str]:
    """销售直写允许变化的表（`dbf_schema.SALES_BACKUP_TABLES` 的算法：总清单去掉应付三张），另加分仓、附加说明、科目余额标记。"""
    base = tuple_of("companion/src/companion/dbf_schema.py", "BACKUP_TABLES")
    return [name for name in base if name not in ("APTRN", "APBAL", "APMAS")] + ["STLOC", "ARTRNRM", "GLBAL"]


def receipt_tables() -> list[str]:
    """收款直写允许变化的表（`dbf_receipt.BACKUP_TABLES` = 写入表 + 客户主档 + 科目余额标记 + 银行流水）。"""
    return tuple_of("companion/src/companion/dbf_receipt.py", "WRITE_TABLES") + ["ARMAS", "GLBAL", "BKTRN"]


def stock_tables() -> list[str]:
    """库存单（入库 / 出库 / 调整 / 调拨 / 作废）允许变化的表：`dbf_stock.TABLES` 加写前一并备份的科目余额标记、分仓、附加说明
    （`dbf_stock.write_stock_entry` 的备份范围；调拨与作废动的表都在这个范围里）。"""
    return tuple_of("companion/src/companion/dbf_stock.py", "TABLES") + ["GLBAL", "STLOC", "ARTRNRM"]


def chain_tables() -> list[str]:
    """先开赊销单再收款的场景：两种写入的表合起来。"""
    return sorted(set(sales_tables()) | set(receipt_tables()))


def baseline(ctx, book, conf, writes: bool = True) -> None:
    """``writes``：这条场景会写账本——从这一刻起收尾一定恢复基线（哪一步不通过都恢复，不等走到账本读回）。"""
    if writes:
        ctx.after = after_dir(ctx)
    with ctx.step("基线", "账套没人开着，内容与基线快照逐文件一致"):
        probe = book.probe()
        ctx.require("没有别的程序开着账套", [], probe.get("busy"))
        state = book.verify(conf["baseline"])
        if state.get("refused"):
            raise Unmet(f"基线快照不可用：{state['refused']}")
        if not state["ok"]:
            restored = book.restore(conf["baseline"])
            ctx.save("baseline-restore.json", restored)
            ctx.require("恢复到基线后逐文件一致", True, restored["ok"])
        ctx.check("账套 = 基线", True, True)


def site(ctx, script: str, params: dict, timeout=600, save: str = "", product: str = "erp", name: str = "", target: str = "") -> dict:
    """跑一段站点脚本，取末行 ``ACC {json}``；脚本自己拒绝（不是测试租户、账套不对）算前置条件不满足。
    ``product`` / ``name`` / ``target``：不是 ERP 站点时给（Cowork 的场景在测试机实验栈跑）。"""
    out = ctx.targets.site(target or SITE_TARGET).run(product, SITE_DIR / script, name or SITE, json.dumps(params, ensure_ascii=False),
                                                      timeout=timeout)
    line = next((text for text in reversed(out.splitlines()) if text.startswith("ACC ")), None)
    if line is None:
        raise Infra(f"站点脚本 {script} 没有回结果：{out[-300:]}")
    result = json.loads(line[4:])
    ctx.save(save or f"{Path(script).stem}.json", result)
    if not result.get("ok"):
        raise Unmet(f"站点侧没走通（{script}）：{result.get('why')}（报上来的账套：{result.get('reported')}）")
    return result


def only_ledger(ctx, paths) -> None:
    ctx.check("落点只有验收账套", [LEDGER.casefold()],
              sorted({str(path).replace("/", "\\").rstrip("\\").casefold() for path in paths or []}))


def money(value) -> float:
    return round(float(value or 0), 2)


def grew(summary: dict, name: str):
    tables = summary.get("tables", {})
    return (tables[name][1] - tables[name][0]) if name in tables else None


def copy_and_diff(ctx, book, conf, allow: list[str], part: str = "") -> tuple[str, dict]:
    """拷出写后的现场并与基线对比：预期以外的文件一个都不许变。回（现场目录，对比摘要）。"""
    after = after_dir(ctx, part)
    ctx.after = getattr(ctx, "after", None) or after
    ctx.require("拷出写后的现场", True, book.copy(after).get("ok"))
    summary = book.diff(conf["baseline"], after, allow=allow, out=f"{after}.diff.json")
    ctx.save(f"ledger-diff-summary{part}.json", summary)
    ctx.check("预期以外的文件没有变化", [], summary.get("stray"))
    return after, summary


def rows(book, folder: str, table: str, where: str) -> list[dict]:
    return [row for row in book.call("rows", folder, table, "--where", where).get("rows", []) if not row.get("_deleted")]


def invoice_readback(ctx, book, folder: str, sale: dict, collected: bool = False) -> dict:
    """赊销单（IV）在账本里的样子：表头一行、客户编码原样、金额、未收余额 = 合计、已收 0；客户主档称谓与核心名分栏。
    ``collected``：这一轮后面还收了它的款（现场是收完之后拷的），已收与余额改由 `invoice_balance` 核。"""
    docnum, expected = sale["state"]["ledger_docnum"], sale["expected"]
    head = rows(book, folder, "ARTRN", f"DOCNUM={docnum}")
    ctx.save(f"artrn-{docnum}.json", head)
    ctx.require(f"ARTRN 里 {docnum} 恰好一行", 1, len(head))
    row = head[0]
    ctx.check("发票的客户编码（与 ERP 客户挂的 Express 编码逐字符一致）", expected["customer_code"], row.get("CUSCOD"))
    ctx.check("发票含税合计", money(expected["totals"]["grand_total"]), money(row.get("NETAMT")))
    if not collected:
        ctx.check("发票已收 0", 0.0, money(row.get("RCVAMT")))
        ctx.check("发票未收余额 = 合计", money(expected["totals"]["grand_total"]), money(row.get("REMAMT")))
    vat = rows(book, folder, "ISVAT", f"DOCNUM={docnum}")
    ctx.check("ISVAT 里这张发票恰好一行", 1, len(vat))
    if expected.get("new_customer"):
        master = rows(book, folder, "ARMAS", f"CUSCOD={expected['customer_code']}")
        ctx.save(f"armas-{docnum}.json", master)
        ctx.require("ARMAS 里新客户恰好一行", 1, len(master))
        # 11-15 线上缺陷（prename-shop）：「ร้าน…」的称谓被拆走后没存进称谓栏，等于丢字
        ctx.check("新客户的称谓栏（PRENAM）", expected["prename"], str(master[0].get("PRENAM") or "").strip())
        ctx.check("新客户的核心名（CUSNAM）", expected["core_name"], str(master[0].get("CUSNAM") or "").strip())
    return row


def receipt_readback(ctx, book, folder: str, record: dict, remaining_after: dict | None = None) -> dict:
    """收款单（RE）在账本里的样子：表头（客户编码原样、对方单号 = 收款记录号、现金 / 其他方式金额）、挂的发票与本次金额、
    方式行、银行流水、凭证一张。``record`` 是站点脚本交回的一笔收款（`erp_collection_post.py`）；``remaining_after`` 是这一笔
    写完时各张发票应有的余额（回执带回来的余额照它核；账本里发票最后的余额由场景在全部写完后用 `invoice_balance` 核）。"""
    docnum, name = record["docnum"], record["name"]
    head = rows(book, folder, "ARTRN", f"DOCNUM={docnum}")
    ctx.save(f"artrn-{docnum}.json", head)
    ctx.require(f"ARTRN 里 {docnum} 恰好一行", 1, len(head))
    row = head[0]
    # 11-15 挡上线缺陷（cc-code-fidelity）：编码里的不换行空格被换成普通空格，写 Express 时被拒
    ctx.check(f"{docnum} 的客户编码逐字符原样", record["customer"], row.get("CUSCOD"))
    ctx.check(f"{docnum} 的对方单号 = 收款记录号", name, str(row.get("YOUREF") or "").strip())
    ctx.check(f"{docnum} 合计", money(record["total"]), money(row.get("NETAMT")))
    ctx.check(f"{docnum} 现金栏（CSHRCV）", money(record["cash"]), money(row.get("CSHRCV")))
    ctx.check(f"{docnum} 其他方式栏（CHQRCV）", money(sum(float(item["amount"]) for item in record["methods"])), money(row.get("CHQRCV")))
    items = rows(book, folder, "ARRCPIT", f"RCPNUM={docnum}")
    ctx.save(f"arrcpit-{docnum}.json", items)
    ctx.check(f"{docnum} 挂的发票与本次金额", sorted([item["docnum"], money(item["amount"])] for item in record["invoices"]),
              sorted([str(item.get("DOCNUM") or "").strip(), money(item.get("RCVAMT"))] for item in items))
    lines = rows(book, folder, "ARRCPCQ", f"RCPNUM={docnum}")
    ctx.save(f"arrcpcq-{docnum}.json", lines)
    ctx.check(f"{docnum} 的方式行（每种方式一行，编号 = 前缀 + 收款单号）",
              sorted([item["prefix"] + docnum, money(item["amount"])] for item in record["methods"]),
              sorted([str(item.get("CHQNUM") or "").strip(), money(item.get("RCVAMT"))] for item in lines))
    bank = rows(book, folder, "BKTRN", f"VOUCHER={docnum}")
    ctx.save(f"bktrn-{docnum}.json", bank)
    # 记不记银行流水按账本自己的设置判（同原生、同 CC `dbf_receipt.plan_bank`）：收款方式（ISRUN ZR）的 SHORTNAM 指到 BKMAS 里有的户头
    # 才记一行；只按科目对得上户头的不记（11-16 首跑：TEST2 的转账方式 SHORTNAM 都是空的，期望「每种转账都记」写错了）
    transfers = [item for item in record["methods"] if item.get("role") == "transfer"]
    to_bank = [item for item in transfers if bank_account_of(book, folder, item["prefix"])]
    if to_bank:
        ctx.check(f"{docnum} 指到银行户头的转账进了银行流水（每种一行，金额一致）", sorted(money(item["amount"]) for item in to_bank),
                  sorted(money(item.get("AMOUNT")) for item in bank))
    else:
        label = "只收现金" if not transfers else "转账方式没有经 SHORTNAM 指到银行户头"
        ctx.check(f"{docnum} {label}，不记银行流水", 0, len(bank))
    ctx.check(f"{docnum} 的凭证恰好一张", 1, len(rows(book, folder, "GLJNL", f"VOUCHER={docnum}")))
    for item in record["invoices"]:
        if remaining_after and item["docnum"] in remaining_after:
            ctx.check(f"{docnum} 回执带回的 {item['docnum']} 余额", money(remaining_after[item["docnum"]]),
                      money((record.get("balances") or {}).get(item["docnum"])))
    return row


def bank_account_of(book, folder: str, prefix: str) -> str:
    """账本里这种收款方式（ISRUN DOCTYP=ZR、这个前缀）经 SHORTNAM 指到的、BKMAS 里有的户头；同前缀几行取第一行指到户头的；没有回空。"""
    for row in rows(book, folder, "ISRUN", "DOCTYP=ZR"):
        short = str(row.get("SHORTNAM") or "").strip()
        if str(row.get("PREFIX") or "").strip() == prefix and short and rows(book, folder, "BKMAS", f"BNKACC={short}"):
            return short
    return ""


def invoice_balance(ctx, book, folder: str, docnum: str, total, received) -> None:
    """全部收款写完后，被收的发票在账本里的已收与余额。"""
    found = rows(book, folder, "ARTRN", f"DOCNUM={docnum}")
    ctx.require(f"被收的发票 {docnum} 还在", 1, len(found))
    ctx.check(f"{docnum} 已收合计", money(received), money(found[0].get("RCVAMT")))
    ctx.check(f"{docnum} 未收余额", money(float(total) - float(received)), money(found[0].get("REMAMT")))
    if money(total) == money(received):
        ctx.check(f"{docnum} 收清后标完成", "Y", str(found[0].get("CMPLAPP") or "").strip())


def cleanup(ctx) -> None:
    """跑完恢复基线并核对；失败的现场已经拷在 ctx.after 里（不清理，留给人看）。"""
    if not getattr(ctx, "after", None) or not os.environ.get("PEARNLY_ACC_BASELINE"):
        return
    restored = ctx.targets.ledger(HOST, LEDGER).restore(os.environ["PEARNLY_ACC_BASELINE"])
    ctx.save("cleanup-restore.json", restored)
    if not restored.get("ok"):
        raise RuntimeError(f"验收账套没能恢复到基线：{restored}")
