"""验收场景：ERP 采购单提交 → CC 桌面版写进 Express 验收账套 → 单号回显 → 账本读回、账本不变式 → 恢复基线。

编排 B（测试机1 的实验栈挂候选源码 + 测试机2 的 CC 桌面版 + 账套服务器上的验收账套）。模型不参与，费用 0。
这是第一期的示范「正常主线」：把基线恢复 / 占用探测 / 读回对比 / 不变式 / 证据 / 收尾还原整条骨架跑通。

**状态：11-14 真机首跑通过**（测试机1 的 erp 实验栈 + 测试机2 的 CC + 69EXP\\TEST2；HP691114-001，124 秒，没有重跑）。
CC 桌面版不能只勾一本账套，所以测试机2 上有一个验收专用数据根 `C:\\PearnlyAcceptance\\root`（登记表只有一行，指向验收账套），
CC 的 ERP 档案跑的时候只读它；做法见验收测试标准「验收专用数据根」。仍待校准的：
- ALLOWED：采购直写会动的表，取自 `companion/src/companion/dbf_schema.py::PURCHASE_BACKUP_TABLES`，另加库存分仓表与附加说明表
  （`dbf_writer.write_purchase` 写前备份的范围）；实际多动了哪张表，本场景会直接判不通过并列出来——那就是要查的东西。
- 字段比对现在只核 APTRN 表头（单号、供应商、金额、税、合计）；「与原生样张逐字段比」（标准 §4.2 第 3 条）要先在验收账套里
  用 Express 界面录好样张、取进基线，届时把 SAMPLE 填上。
- 这一版从服务端提交（via = "api"），界面那一段首次实跑时补。

参数全部从环境变量取（不进仓库的是机器上的路径与账号，不是密钥）：
  PEARNLY_ACC_ERP_USER       测试机1 的 CC 桌面版上登录的 ERP 员工
  PEARNLY_ACC_ERP_TAX / _BRANCH   接了验收账套的那家公司
  PEARNLY_ACC_LEDGER         验收账套路径（默认 \\\\accserver\\ACCOUNT\\69EXP\\TEST2）
  PEARNLY_ACC_BASELINE       基线快照目录（PEARNLY_ACC_LEDGER_HOST 那台机器的本机盘；默认测试机2）
  PEARNLY_ACC_SITE_TARGET / PEARNLY_ACC_ERP_SITE   服务端在哪（lab 测试机实验栈，默认；maclab 本机 Mac 实验栈）与站点名
"""
import json
import os
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
Unmet = sys.modules["acc_errors"].Unmet
Infra = sys.modules["acc_errors"].Infra

LEDGER = os.environ.get("PEARNLY_ACC_LEDGER", r"\\accserver\ACCOUNT\69EXP\TEST2")
# 做账套快照 / 核对 / 还原的那台机器（基线快照在它的本机盘）：主会话 11-13 定基线在测试机2 取
HOST = os.environ.get("PEARNLY_ACC_LEDGER_HOST", "testpc2")
# 服务端在哪：lab = 测试机1 的实验栈（testerp 域名切过去）；maclab = 本机 Mac 实验栈（不切域名，CC 经局域网连）
SITE_TARGET = os.environ.get("PEARNLY_ACC_SITE_TARGET", "lab")
SITE = os.environ.get("PEARNLY_ACC_ERP_SITE", "erp.maclab.localhost" if SITE_TARGET == "maclab" else "testerp.pearnly.com")
SAMPLE = None  # 原生样张的单号；录好并进基线后填，见文件头


def allowed_tables() -> list[str]:
    """采购直写允许变化的表：读 CC 桌面版自己的写入清单，不另抄一份。"""
    import ast

    tree = ast.parse((ROOT / "companion" / "src" / "companion" / "dbf_schema.py").read_text(encoding="utf-8"))
    base = next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "BACKUP_TABLES")
    return [name for name in base if name not in ("ARTRN", "ARBAL", "ARMAS")] + ["STLOC", "ARTRNRM", "GLBAL"]


def settings() -> dict:
    need = {"user": "PEARNLY_ACC_ERP_USER", "company_tax": "PEARNLY_ACC_ERP_TAX", "baseline": "PEARNLY_ACC_BASELINE"}
    values = {key: os.environ.get(name, "") for key, name in need.items()}
    missing = [need[key] for key, value in values.items() if not value]
    if missing:
        raise Unmet("没配：" + "、".join(missing) + "（见场景文件头）")
    return {**values, "branch": os.environ.get("PEARNLY_ACC_ERP_BRANCH", "")}


def run(ctx):
    conf = settings()
    twice = ctx.scenario["id"].endswith(".twice")  # 不正常使用 M02：同一张单连推两次，账本里仍只许多一张
    book = ctx.targets.ledger(HOST, LEDGER)
    run_id = ctx.folder.parent.parent.name if ctx.attempt > 1 else ctx.folder.parent.name
    ctx.after = f"{ctx.targets.windows(HOST).work}/runs/{run_id}-{ctx.scenario['id']}-{ctx.attempt}"

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

    with ctx.step("提交采购单并等写入", "单据提交后进写账队列，CC 桌面版写进验收账套并回执"):
        out = ctx.targets.site(SITE_TARGET).run("erp", ROOT / "scripts" / "acceptance" / "site" / "erp_purchase_post.py", SITE,
                                          json.dumps({**conf, "ledger": LEDGER, "run": f"{run_id}-{zlib.crc32(ctx.scenario['id'].encode('utf-8')) % 1000:03d}{ctx.attempt}",
                                                      "resend": twice}), timeout=480)
        line = next((text for text in reversed(out.splitlines()) if text.startswith("ACC ")), None)
        if line is None:
            raise Infra(f"站点脚本没有回结果：{out[-300:]}")
        posted = json.loads(line[4:])
        ctx.save("site.json", posted)
        if not posted["ok"]:
            raise Unmet(f"站点侧没开成单：{posted.get('why')}（报上来的账套：{posted.get('reported')}）")
        state = posted["state"]
        ctx.require("写账状态", "written", state.get("ledger_state"))
        ctx.check("回显了 Express 单号", True, bool(state.get("ledger_docnum")))
        if twice:
            ctx.check("重复推送了两次（推送后立刻、写完之后）", 2, len(posted.get("resent") or []))
            ctx.check("重复推送后单号没变", (posted.get("first") or {}).get("ledger_docnum"), state.get("ledger_docnum"))
            ctx.check("重复推送后没有换成另一件写账任务", (posted.get("first") or {}).get("ledger_job"), state.get("ledger_job"))
        ctx.check("CC 桌面版写后读回", "ok", (posted.get("job") or {}).get("verify_state"))
        ctx.check("落点只有验收账套", [LEDGER.casefold()], sorted({str(path).replace("/", "\\").rstrip("\\").casefold() for path in posted["book_paths"]}))
        ctx.facts.update(docnums=[state.get("ledger_docnum") or ""], ledger=LEDGER, erp_doc=posted["doc"])

    with ctx.step("账本读回", "账套里按单号读得到这张单，表头与单据一致；预期以外的表逐字节不变"):
        ctx.require("拷出写后的现场", True, book.copy(ctx.after).get("ok"))
        diff_path = f"{ctx.after}.diff.json"
        summary = book.diff(conf["baseline"], ctx.after, allow=allowed_tables(), out=diff_path)
        ctx.save("ledger-diff-summary.json", summary)
        ctx.check("预期以外的文件没有变化", [], summary.get("stray"))
        rows = book.call("rows", ctx.after, "APTRN", "--where", f"DOCNUM={state['ledger_docnum']}").get("rows", [])
        ctx.save("aptrn-row.json", rows)
        ctx.require("APTRN 里这张单恰好一行", 1, len(rows))
        row, expected = rows[0], posted["expected"]
        ctx.check("供应商编码", expected["supplier_code"], row.get("SUPCOD"))
        totals = expected.get("totals", {})
        if "grand_total" in totals:
            ctx.check("含税合计", round(totals["grand_total"], 2), round(float(row.get("NETAMT") or 0), 2))
        if "vat_amount" in totals:
            ctx.check("税额", round(totals["vat_amount"], 2), round(float(row.get("VATAMT") or 0), 2))
        tables = summary.get("tables", {})
        grew = lambda name: (tables[name][1] - tables[name][0]) if name in tables else None  # noqa: E731
        ctx.check("APTRN 只多一行", 1, grew("APTRN"))
        # 应有的变化（11-14 首跑校准）：明细一行、进项税一行、凭证一张且分录不少于两行；少了任何一样都是没写全
        lines = book.call("rows", ctx.after, "STCRD", "--where", f"DOCNUM={state['ledger_docnum']}").get("rows", [])
        ctx.save("stcrd-rows.json", lines)
        ctx.require("STCRD 里这张单的明细行数", 1, len(lines))
        ctx.check("明细的物料编码", expected["item_code"], lines[0].get("STKCOD"))
        ctx.check("明细的数量", float(expected["qty"]), float(lines[0].get("TRNQTY") or 0))
        ctx.check("明细的不含税单价", round(float(expected["price"]), 2), round(float(lines[0].get("XUNITPR") or 0), 2))
        vat = book.call("rows", ctx.after, "ISVAT", "--where", f"DOCNUM={state['ledger_docnum']}").get("rows", [])
        ctx.save("isvat-rows.json", vat)
        ctx.require("ISVAT 里这张单恰好一行", 1, len(vat))
        if "subtotal" in totals:
            ctx.check("进项税登记的税前金额", round(totals["subtotal"], 2), round(float(vat[0].get("AMT01") or 0), 2))
        if "vat_amount" in totals:
            ctx.check("进项税登记的税额", round(totals["vat_amount"], 2), round(float(vat[0].get("VAT01") or 0), 2))
        ctx.check("凭证多一张", 1, grew("GLJNL"))
        ctx.check("凭证分录不少于两行", True, (grew("GLJNLIT") or 0) >= 2)
        if SAMPLE:
            sample = book.call("rows", conf["baseline"], "APTRN", "--where", f"DOCNUM={SAMPLE}").get("rows", [])
            ctx.require("基线里有原生样张", 1, len(sample))
            differ = sorted(key for key in row if key not in NATIVE_IGNORE and row.get(key) != sample[0].get(key))
            ctx.check("与原生样张逐字段一致（白名单以外）", [], differ)


NATIVE_IGNORE = {"DOCNUM", "REFNUM", "DOCDAT", "DUEDAT", "CREDAT", "CHGDAT", "USERID", "RECNO"}  # 首次实跑时按样张校准


def cleanup(ctx):
    """跑完恢复基线并核对；失败的现场已经拷在 ctx.after 里（不清理，留给人看）。"""
    if not getattr(ctx, "after", None) or not os.environ.get("PEARNLY_ACC_BASELINE"):
        return
    restored = ctx.targets.ledger(HOST, LEDGER).restore(os.environ["PEARNLY_ACC_BASELINE"])
    ctx.save("cleanup-restore.json", restored)
    if not restored.get("ok"):
        raise RuntimeError(f"验收账套没能恢复到基线：{restored}")
