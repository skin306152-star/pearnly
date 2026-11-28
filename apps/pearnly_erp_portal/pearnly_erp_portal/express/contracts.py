"""ERP 当前台、Express 当账本的纯规则（2026-10-22；10-23 起 CC 桌面版直连 ERP，ADR-025）：单据怎么变成 Express 写账规则读的
票面（`pages`）、Express 主档怎么落进 ERP 主档。不连库、不依赖 Frappe、不做 IO；金额一律 Decimal。

单据（:func:`document` 出、:func:`checked` 核）::

    {"version": 1, "ref": "PUR-2026-00012@<建单时刻>", "direction": "purchase" | "sales", "date": "YYYY-MM-DD",
     "number": "票面 / 本方单号", "company": {"tax_id": 13 位, "branch": "00000"},
     "party": {"code": Express 对方档码或空, "name", "tax_id", "branch"},
     "paid": true | false, "has_vat": true | false, "currency": "THB",
     "subtotal": 税前（扣折扣后）, "vat": 税额, "total": 含税总额（字符串化的十进制数）,
     "items": [{"name", "code": 存货码或空, "unit", "qty", "price", "amount": 行净额, "loccod": 仓库码或空,
                "stock": true | false}]}
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
import re

VERSION = 1
CENT = Decimal("0.01")

# ERP 单据上的 Express 状态（只按电脑的回执定：HTTP 成功、交进队列都不算写入）
QUEUED, WRITTEN, MISMATCH, HELD, FAILED = "queued", "written", "mismatch", "held", "failed"
HANDLED = "handled"  # 人标记「已在 Express 手工处理」（收银日汇总 / 退货日汇总停住时的出口，主会话 10-26）
# 断开 Express 时还没写进去的单（排队中、停下的）：撤掉写账件、不再写，单据上照实标「未写入 Express（已断开）」（主会话 11-01）
DETACHED = "detached"
STATES = (QUEUED, WRITTEN, MISMATCH, HELD, FAILED, DETACHED)
# 单据上的账本栏是通用列（``ledger_state`` / ``ledger_docnum`` / …，评估 2026-11-02 §12 第 6 条）：一张单只会有一个账本，
# ``ledger_target`` 记是哪一个，排上时记。现在只有 Express；以后 ERP 接 MR.ERP 记 ``mrerp``
LEDGER_EXPRESS = "express"
# 还没写进 Express 的状态（每分钟再看一次停下的；断开时撤掉）
OPEN_STATES = (QUEUED, HELD)
# 接上之后开始、还没写成的单（库存对齐把这些单的变动加回去）：排队中、停下的、失败的
PENDING_STATES = (QUEUED, HELD, FAILED)
# 停下的原因码（`ledger_reason`，页面按码出文案）
NO_TAX = "express.company_tax_missing"
NO_BOOK = "express.account_set_missing"
NO_STOCK_BOOK = "express.stock_book_missing"  # 账套 ISRUN 没有这个库存单别（OU / JU / TK）
SEVERAL_BOOKS = "express.account_set_several"
# 单据日期不在账套整个会计期间（本年起 ~ 次年止）里：Express 原生录单会拒（Message#70 วันที่ไม่อยู่ในงวด），不写、如实报。只在 CC
# 对话 / 代录里按本对话账套写时出现（按日期选的天然在期间内）；日期不会自己变，不每分钟重试（`target.pick`）
PERIOD_OUTSIDE = "express.account_period_mismatch"
NO_WRITER = "express.no_writer"
READING = "express.masters_reading"
# 一本账套整套主档那件按需请求的请求号前缀（`masters._request_key`；LINE 等收款方式时按它叫醒，`collection.service.reading_topics`）
MASTERS_REQUEST = "erp_masters:"
NOT_CONNECTED = "express.cc_desktop_not_connected"
OFFLINE = "express.cc_desktop_offline"
# 排队中的单钉在一台此刻不在线的电脑上（`cc_desktop.jobs.waiting_computer`：领走后那台失联、收回时钉回它；定时任务钉在读文件夹
# 的那台）：别的电脑不写，等那台。原因栏存「这个码:电脑名」，页面写「等待电脑 · 名称」（11-25 两台电脑实测）
WAITING_COMPUTER = "express.waiting_computer"


def waiting_computer(label) -> str:
    """原因栏的写法：``express.waiting_computer:<电脑名>``（名称去掉换行等空白、截到 140 字；名称里有冒号、竖线等照存，解析只认
    第一个冒号之前的前缀）；没有名称时只存码 ``express.waiting_computer``（页面只写「等待电脑」）。"""
    name = " ".join(str(label or "").split())[:140]
    return f"{WAITING_COMPUTER}:{name}" if name else WAITING_COMPUTER


def waiting_computer_of(reason):
    """原因栏是「等那台电脑」时回电脑名（没有名称回空串），不是回 ``None``。"""
    text = str(reason or "")
    if text == WAITING_COMPUTER:
        return ""
    return text[len(WAITING_COMPUTER) + 1:] if text.startswith(WAITING_COMPUTER + ":") else None
# 写账判定 / 代录预检里编号种类本产品不认（契约缺口，11-16 H3）：回这个原因、记错误日志（标题 UNKNOWN_DOCUMENT_LOG），
# 与「不是本人的单」（不回、不报存在与否）分开；只给模型与日志，不是界面文案
UNKNOWN_DOCUMENT = "unknown_document"
UNKNOWN_DOCUMENT_LOG = "Write precheck: unknown document kind"
REFUND_WAITING = "express.pos_refund_waiting"  # 收银退货单的原 HS 还没写进 Express（`pos_day`）
REFUND_CROSS_YEAR = "express.pos_refund_cross_year"  # 原 HS 与退货在不同年度账套：Express 原生跨年退货写法实录前停人工
# ERP 仓库没有对应的 Express 仓库（没有仓库码）：以前不带码、CC 桌面版静默写进 Express 主仓；改为停下交人（多仓闭环 P0，G15）
WAREHOUSE_UNMAPPED = "express.warehouse_unmapped"
# 盘点调整里有还没有 Express 编码的商品（盘点当场新建的）：本批写 TK 前停下交人，CC 在 TK 里建档另排（用户 10-29 批复 #7）
ITEM_UNMAPPED = "express.item_unmapped"
# 识别来的单（单上有识别标记 ``ocr_review``）：票面印的合计（``printed_total``，人可以改）没有、或与单据合计对不上 → 不写账，
# 停下进待处理（2026-11-03「CC 通用疑难处理」，管钱的底线：缺页的单明细不全，单据合计自洽却不完整）。人把票面合计填对，
# 或「修改」补齐明细两边对上，新单照常写。手工录的单、上线前的老单没有识别标记，照旧
PRINTED_TOTAL_MISSING = "express.printed_total_missing"
PRINTED_TOTAL_MISMATCH = "express.printed_total_mismatch"
# 交给代录、还没录成的单（最近一件写账是代录交的）：直写、「再写一次」都不接，由代录去录（主会话 10-27）
OPERATOR_OWNED = "express.operator_owned"
REFUND_ORIGINAL_MANUAL = "express.pos_refund_original_manual"  # 原 HS 是人手工处理的：Express 单号不知道，退货单也要手工处理
DISCONNECTED = "express.disconnected"  # 断开 Express 时还没写进去（`push.detach`）
# 采购单 / 销售单在 ERP 作废时电脑已在写，写完才回来：Express 里有这张、ERP 已作废——转人工，人去 Express 里处理（11-13）
VOIDED_AFTER_WRITE = "express.voided_after_write"
# 写成了、单据却已不在等这件的结果（被换掉的旧件迟到写成、现在那件写成时单据已被改成别的状态）：转人工、单号留着，同一张单不再
# 自动写（`push.job_changed`，11-28 ④；同 MR.ERP 记结论遇到意外状态的 `mrerp.contracts.MOVED_DURING_IMPORT`）。以共用通道的
# 「结果不明」码（:data:`LOST`）开头、后缀只有小写码（不原样露码），但不算结果不明（:func:`result_unknown` 排除它）。单号在单据上、确实写进去了：原因说「已写进
# Express，需人工处理」（`pending.REASONS`），CC 说已写进 + 单号 + 需人工处理，不引向「核对后记下」那个出口（主会话 11-28）
MOVED_DURING_WRITE = "cc_desktop.result_unknown:moved_during_write"
# 共用通道收回后转人工的原因码（``cc_desktop.jobs.LOST``，可能写进去了）：状态词「结果不明」（:func:`result_unknown`）
LOST = "cc_desktop.result_unknown"
# 转人工时单据时间线里记的一条（原生评论，同「已在 Express 手工处理」）：只有一张单号时按它是旧件 / 现在那件写的；两张都在
COMMENT_EARLIER = "express.comment.writtenEarlier"
COMMENT_CHANGED = "express.comment.writtenAfterChange"
COMMENT_TWICE = "express.comment.writtenTwice"


CHOICE = "choice_needed"
# 过一会儿自己会好的（电脑开机、主档读完、有人在电脑上登录 CC、退货单的原 HS 写成了）：每分钟再试，判定 / 计划报「等电脑」；
# 要人选的、单据本身不对的不自动重试
RETRY_REASONS = (NO_WRITER, READING, REFUND_WAITING)
# 每分钟那一轮（`push.poll`）再准备的：上面几种，加「没有账套」——网页 / LINE 按单据日期选账套的单，电脑报了新年度账套就接着写。
# 「没有账套」不算等电脑（A 线 11-12 A9：判定、计划、待处理、代录如实报「没有账套」，不再永远等电脑）
POLL_REASONS = (NO_BOOK, *RETRY_REASONS)
# 要人处理的单（ERP 版 CC 的「待处理」，2026-11-02）：停下等人（要人选一次、单据本身不对、电脑转了人工…；不含每分钟会自己
# 再试的 `RETRY_REASONS`）、失败、写进去了但核对不上。只看单据上的状态与原因（`needs_person`）
ATTENTION_STATES = (HELD, FAILED, MISMATCH)


def printed_hold(recognized, printed_total, grand_total, net_payable=None):
    """识别来的单能不能写账（纯判断）：回停下的原因码，``None`` = 照常写。``recognized``：单上有识别标记；
    ``net_payable``：采购单扣了预扣税后的实付额（票面合计印的可能是它，同识别时的核对 `amounts.computed_issues`，容差同）。"""
    if not recognized:
        return None
    from pearnly_core.tax_documents import amounts

    printed = amounts.money(printed_total)
    if printed is None or printed <= 0:  # 金额栏空着存成 0
        return PRINTED_TOTAL_MISSING
    if amounts.computed_issues({"total": printed}, grand_total, None, net_payable):
        return PRINTED_TOTAL_MISMATCH
    return None


# 给人看的状态词（用户 11-22 统一成 录入中 / 成功 / 失败）：网页列表、详情、Express 弹窗与 LINE 回执同一张表（单据页
# `public/desk/transactions.js` 的 ``LEDGER_WORDS`` / ``AUTO_RETRY`` 契约测试比对）。录入中 = 交出去还在路上：排队中，以及停在
# 每分钟会自己再试的原因上（:data:`AUTO_RETRY_REASONS`，主会话 11-22 审定）；失败只给要人处理的（停下待选、核对不一致、被拒、写入失败、
# 已断开）。写进去了但回读核对不一致（mismatch）原因 :data:`MISMATCH_REASON`；人标记「已在 Express 手工处理」（handled）算成功。
# 原因照旧在原因栏 / 悬浮里
ENTERING, SUCCEEDED, NOT_DONE = "Entering", "Succeeded", "Failed"
LEDGER_WORDS = {QUEUED: ENTERING, WRITTEN: SUCCEEDED, HANDLED: SUCCEEDED, HELD: NOT_DONE, FAILED: NOT_DONE,
                MISMATCH: NOT_DONE, DETACHED: NOT_DONE}
# 结果不明（写没写进去不知道）单列「结果不明」，不并进失败（主会话 11-25：写成失败会让人重录、重复入账）：停下的原因是
# CC 桌面版写到一半断了（`cc_desktop.jobs.LOST`）或 MR.ERP 导入发出后查不到 / 出错（`mrerp.contracts.UNKNOWN` 前缀）
RESULT_UNKNOWN = "Result unknown"
UNKNOWN_REASONS = ("cc_desktop.result_unknown", "mrerp.result_unknown")
MISMATCH_REASON = "express.reason.mismatch"
# 每分钟自己再试的停下原因（= :data:`POLL_REASONS`：没有账套、没有能写的电脑、主档在读、退货单的原 HS 还没写成）：算录入中
AUTO_RETRY_REASONS = POLL_REASONS


def result_unknown(state, reason="") -> bool:
    """停下的原因是结果不明（:data:`UNKNOWN_REASONS`，MR.ERP 的带 ``: <码>`` 后缀；纯判断）。网页列表 / 详情 / Express 弹窗、
    筛选、LINE 回执、待处理、导出、CC 列单与推送结果都从这里判（用户 11-28：「结果不明」单列成状态）。迟到写成
    （:data:`MOVED_DURING_WRITE`：单号在单据上、确实写进去了）不算：它是「已写进 Express，需人工处理」，状态词照停下的「失败」。"""
    reason = str(reason or "")
    return state == HELD and reason.startswith(UNKNOWN_REASONS) and reason != MOVED_DURING_WRITE


def ledger_word(state, reason="") -> str:
    """单据账本状态（与停下的原因）→ 状态词词条（没在表里的原样）。"""
    if state == HELD and str(reason or "") in AUTO_RETRY_REASONS:
        return ENTERING
    if result_unknown(state, reason):
        return RESULT_UNKNOWN
    return LEDGER_WORDS.get(state or "", state or "")


def ledger_settled(state, reason) -> bool:
    """LINE 上等结果的这一单有没有结论：状态词是「录入中」的（排队中、停在自己会再试的原因上）还没有，等到超时回兜底。"""
    return bool(state) and ledger_word(state, reason) != ENTERING


def needs_person(state, reason) -> bool:
    """这张单此刻要不要人处理（纯判断，`pending` 与页面同一口径）。"""
    if state in (FAILED, MISMATCH):
        return True
    return state == HELD and str(reason or "") not in RETRY_REASONS


# 这几种采购单是真进货 / 费用，推进 Express（采购订单 10-28 已删，见 `purchasing.contracts.RETIRED_KINDS`）
PURCHASE_KINDS = ("purchase_invoice", "expense", "goods", "service")
SALES_KINDS = ("receipt",)


def in_express(state) -> bool:
    """单据按自己的 Express 状态算不算「写进 Express 的单」（排上了、停下、写成了…）：没排上过的、断开时撤下的不算。"""
    return bool(state) and state != DETACHED


def d(value) -> Decimal:
    try:
        amount = Decimal(str(value if value not in (None, "") else 0).replace(",", ""))
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return amount if amount.is_finite() else Decimal(0)


def unmapped(codes) -> list:
    """``{ERP 仓库: Express 仓库码}`` 里没有码的那些仓（排好序；空仓名不算）。"""
    return sorted(name for name, code in (codes or {}).items() if name and not str(code or "").strip())


def money(value: Decimal) -> str:
    return format(d(value).quantize(CENT, rounding=ROUND_HALF_UP), "f")


def branch5(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return (digits[-5:] if digits else "0").zfill(5)


def tax13(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 13 else ""


def spread(amounts: list, base: Decimal) -> list:
    """逐行金额按比例摊成合计正好等于 ``base``（含税单价、整单折扣、四舍五入都在这里收平），余数落末行。"""
    total = sum(amounts, Decimal(0))
    if not amounts:
        return []
    if total <= 0:
        share = (base / len(amounts)).quantize(CENT, rounding=ROUND_HALF_UP)
        out = [share] * len(amounts)
    else:
        out = [(amount * base / total).quantize(CENT, rounding=ROUND_HALF_UP) for amount in amounts]
    out[-1] = (out[-1] + base.quantize(CENT, rounding=ROUND_HALF_UP) - sum(out, Decimal(0))).quantize(CENT)
    return out


# 按台号码写进 Express 单据行的附加说明（ARTRNRM.REMARK C(50)，cp874 一字一字节；一行最多 10 条；评估
# docs/work/2026-11-02-vehicle-artrnrm-评估.md）。标签同 ERP 打印行下印号（`printing/_labels.html` 宏 ``vehicle``），手机电器照 S 盘原生习惯「S/N」
REMARK_WIDTH = 50
VEHICLE = "vehicle"
CHASSIS_LABEL, ENGINE_LABEL, COLOR_LABEL, SERIAL_LABEL = "เลขตัวถัง", "เลขเครื่อง", "สี", "S/N"


def _wrapped(text: str) -> list:
    """一条超过 REMARK_WIDTH 的说明折成几条（号码再长也不截断）。"""
    text = str(text or "").strip()
    return [text[start:start + REMARK_WIDTH] for start in range(0, len(text), REMARK_WIDTH)] if text else []


def unit_remarks(units: list, kind: str) -> list:
    """一行上的台（``[{serial_no, secondary_no, color}]``）→ 这一行的附加说明。

    汽车一台两条：「เลขตัวถัง 车架号」「เลขเครื่อง 发动机号 สี 颜色」（标签照的三项连同标签放一条超过 50 字节）；手机电器一台一个号段
    「S/N[/第二个号][ 颜色]」，两个空格隔开、一条装满再换下一条（S 盘原生「S/N.NPB201850664S  NPB201850665S …」）。空的项不写。"""
    clean = [{key: " ".join(str(unit.get(key) or "").split()) for key in ("serial_no", "secondary_no", "color")}
             for unit in units or []]
    clean = [unit for unit in clean if any(unit.values())]
    out = []
    if kind == VEHICLE:
        for unit in clean:
            out += _wrapped(f"{CHASSIS_LABEL} {unit['serial_no']}" if unit["serial_no"] else "")
            rest = " ".join(part for part in (f"{ENGINE_LABEL} {unit['secondary_no']}" if unit["secondary_no"] else "",
                                              f"{COLOR_LABEL} {unit['color']}" if unit["color"] else "") if part)
            out += _wrapped(rest)
        return out
    note = ""
    for unit in clean:
        token = "/".join(part for part in (unit["serial_no"], unit["secondary_no"]) if part)
        token = " ".join(part for part in (token, unit["color"]) if part)
        joined = f"{note}  {token}" if note else f"{SERIAL_LABEL} {token}"
        if note and len(joined) > REMARK_WIDTH:
            out += _wrapped(note)
            joined = f"{SERIAL_LABEL} {token}"
        note = joined
    return out + _wrapped(note)


def document(*, ref: str, direction: str, doc_date: str, number: str, company: dict, party: dict, paid: bool,
             has_vat: bool, total, vat, lines: list, currency: str = "THB") -> dict:
    """一张已提交的 ERP 单 → 交给网关的单据。

    ``lines``：``[{name, code, unit, qty, amount（行金额，ERP 原样）, loccod, stock}]``。税前额 = 含税总额 − 税额，
    逐行金额摊成合计等于税前额（ERP 的行金额可能含税、可能没扣整单折扣），单价 = 行金额 / 数量。"""
    total, vat = d(total), d(vat) if has_vat else Decimal(0)
    base = total - vat
    rows = [line for line in lines if d(line.get("qty")) > 0]
    amounts = spread([d(line.get("amount")) for line in rows], base)
    items = []
    for line, amount in zip(rows, amounts):
        qty = d(line["qty"])
        code = str(line.get("code") or "").strip()
        items.append({"name": str(line.get("name") or code or "-").strip()[:200], "code": code[:20],
                      "unit": str(line.get("unit") or "").strip()[:20], "qty": format(qty.normalize(), "f"),
                      "price": format((amount / qty).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f"),
                      "amount": money(amount),
                      # 库存行一律带仓库码（出入库第一期，10-25）：随单新建的物料也按这一行的仓写、建 STLOC 行，不再落主仓
                      "loccod": str(line.get("loccod") or "").strip()[:4] if line.get("stock") else "",
                      # 包装单位：ERP 商品单位表的换算率（CC 桌面版认不出 Express 主档的包装单位时按它换成库存单位）
                      **({"factor": str(line["factor"])} if line.get("stock") and d(line.get("factor")) not in (0, 1) else {}),
                      # 免税行（收银日汇总 / 退货单在增值税登记户的单里标）：CC 桌面版不摊税给它、明细记 VATCOD '0'
                      **({"vat_exempt": True} if line.get("vat_exempt") else {}),
                      # 按台号码（:func:`unit_remarks` 拼好）：CC 桌面版写进 Express 这一行的附加说明（载荷 17）
                      **({"remarks": list(line["remarks"])} if line.get("remarks") else {}),
                      "stock": bool(line.get("stock"))})
    return {"version": VERSION, "ref": ref, "direction": direction, "date": str(doc_date)[:10],
            "number": str(number or "").strip()[:40],
            "company": {"tax_id": tax13(company.get("tax_id")), "branch": branch5(company.get("branch"))},
            "party": {"code": str(party.get("code") or "").strip()[:20], "name": str(party.get("name") or "").strip()[:200],
                      "tax_id": tax13(party.get("tax_id")), "branch": branch5(party.get("branch"))},
            "paid": bool(paid), "has_vat": bool(has_vat) and vat > 0,
            "currency": (str(currency or "THB").strip().upper() or "THB")[:3],
            "subtotal": money(base), "vat": money(vat), "total": money(total), "items": items}


# ── Express 主档 → ERP 主档 ──────────────────────────────────────────────────────


def rows(value) -> list:
    """有编码的那几行（Express 主档每类的行）。"""
    return [row for row in value or [] if isinstance(row, dict) and str(row.get("code") or "").strip()]


def unit_names(data: dict) -> dict:
    """单位码 → 名称（Express ISTAB 20）。"""
    return {str(row["code"]).strip(): str(row.get("name") or row["code"]).strip() for row in rows(data.get("units"))}


PRENAME_JOIN = " "  # 前缀与核心名之间（Express 打印口径，真机冒烟核；改它不会引起整批改名，见 masters.same_name）


def display_name(prename, core) -> str:
    """Express 的 PRENAM（称谓 / 法人前缀）+ CUSNAM / SUPNAM（核心名）→ 显示名。没有前缀、或核心名本来就带着它时只用核心名。"""
    prename, core = str(prename or "").strip(), str(core or "").strip()
    if not prename or core.startswith(prename):
        return core
    return f"{prename}{PRENAME_JOIN}{core}"


def party(row: dict) -> dict:
    """客户 / 供应商一行 → ERP 字段的共同部分。``name`` 是显示名（CC 桌面版 1.12.22 起另报 ``prename``，旧版不带时就是核心名），
    ``core`` 是 Express 里的核心名（按名称认领本地档时也认它）。"""
    branch = branch5(row.get("branch"))
    core = str(row.get("name") or row["code"]).strip()
    return {"code": str(row["code"]).strip()[:20], "name": display_name(row.get("prename"), core)[:140], "core": core[:140],
            "tax_id": tax13(row.get("tax_id")), "branch": branch, "head": branch == "00000",
            "address": str(row.get("address") or "").strip()[:500], "phone": str(row.get("phone") or "").strip()[:60]}


def product(row: dict, units: dict) -> dict:
    """商品一行 → ERP 商品字段：库存单位是 Express 的基本单位，售卖单位与进货单位不同于它时进单位表（换算到基本单位）。"""
    base = units.get(str(row.get("unit") or "").strip()) or str(row.get("unit") or "").strip()
    extra = {}
    for unit_key, factor_key in (("sale_unit", "sale_factor"), ("purchase_unit", "purchase_factor")):
        name = units.get(str(row.get(unit_key) or "").strip()) or str(row.get(unit_key) or "").strip()
        factor = d(row.get(factor_key))
        if name and name != base and factor > 0:
            extra.setdefault(name, factor)
    sale = units.get(str(row.get("sale_unit") or "").strip()) or base
    prices = [d(price) for price in row.get("prices") or []]
    return {"code": str(row["code"]).strip()[:20], "name": str(row.get("name") or row["code"]).strip()[:140],
            "base_unit": base[:20], "unit": (sale or base)[:20], "units": [{"unit_name": name[:20], "factor": str(factor)}
                                                                           for name, factor in extra.items()],
            "price": money(prices[0]) if prices and prices[0] > 0 else "", "stock": row.get("kind") == "stock",
            "barcode": str(row.get("barcode") or "").strip()[:60]}


def stock_targets(stock_rows, pending) -> dict:
    """各 (存货码, 仓库码) 在 ERP 里应有的结存（基本单位）：Express 的结存 + 还没写进 Express 的 ERP 单的变动。

    ``pending``：``{(code, loccod): 带符号数量}``（进货为正、卖出为负），ERP 自己已经按这些单过了库存，Express 还没有。"""
    targets = {}
    for row in stock_rows or []:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("code") or "").strip(), str(row.get("loccod") or "").strip())
        if key[0] and key[1]:
            targets[key] = targets.get(key, Decimal(0)) + d(row.get("qty"))
    for key, qty in (pending or {}).items():
        targets[key] = targets.get(key, Decimal(0)) + d(qty)
    return targets


def state(value) -> str:
    return value if value in STATES else ""


# ── 单据 → 写账规则读的票面 ─────────────────────────────────────────────────────

ITEMS_MAX = 200
_TOL = Decimal("0.05")


class DocumentError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _amount(value, *, positive=False, code="express.amount_invalid") -> Decimal:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise DocumentError(code) from None
    if not number.is_finite() or number < 0 or (positive and number <= 0):
        raise DocumentError(code)
    return number.quantize(Decimal("0.0001")).normalize()


def checked(doc: dict) -> dict:
    """:func:`document` 出的单再核一遍（金额对得上、行合计等于税前、日期合法），不合规抛 ``DocumentError``（码给页面显示）。"""
    if not tax13((doc.get("company") or {}).get("tax_id")):
        raise DocumentError(NO_TAX)
    try:
        date.fromisoformat(str(doc.get("date") or ""))
    except ValueError:
        raise DocumentError("express.date_invalid") from None
    items = doc.get("items") or []
    if not items or len(items) > ITEMS_MAX:
        raise DocumentError("express.items_invalid")
    party = doc.get("party") or {}
    if not party.get("code") and not party.get("name"):
        raise DocumentError("express.party_invalid")
    subtotal, vat = _amount(doc.get("subtotal"), positive=True), _amount(doc.get("vat"))
    total = _amount(doc.get("total"), positive=True)
    if abs(subtotal + vat - total) > _TOL or (not doc.get("has_vat") and vat != 0):
        raise DocumentError("express.amount_invalid")
    for item in items:
        _amount(item.get("qty"), positive=True, code="express.items_invalid")
    if abs(sum((_amount(item.get("amount"), code="express.items_invalid") for item in items), Decimal(0))
           - subtotal) > _TOL:
        raise DocumentError("express.items_mismatch")
    return doc


def pages(doc: dict) -> list:
    """核过的单 → 写账规则读的票面（主页面的 ``fields``）。本方是买方（进项）或卖方（销项）。票面写明的值（现 / 赊、对方编码、
    行编码与仓库、货 / 服务）只管这一张，由写账规则照写（`express_rules.push.choices` / `common` 的按码直落）。"""
    party, own = doc["party"], doc["company"]
    them, us = ("seller", "buyer") if doc["direction"] == "purchase" else ("buyer", "seller")
    fields = {
        "direction": doc["direction"],
        "date": doc["date"],
        "invoice_number": doc["number"] or doc["ref"].split("@")[0][:40],
        "document_type": "tax_invoice" if doc["has_vat"] else "receipt",
        # 币种照 ERP 单据写明（不让写账规则按品名 / 单号里的外币记号去猜）
        "currency": doc["currency"],
        "subtotal": doc["subtotal"], "vat": doc["vat"], "total_amount": doc["total"],
        "payment_status": "paid" if doc["paid"] else "unpaid",
        # 进项按可抵进项的货票车道（有明细、按行声明库存 / 服务）
        "posting_item_type_manual": "goods",
        f"{them}_name": party["name"], f"{them}_tax": party["tax_id"], f"{them}_branch": party["branch"],
        f"{us}_tax": own["tax_id"], f"{us}_branch": own["branch"],
        "party_code": party["code"],
        "items": [{"name": item["name"], "qty": item["qty"], "price": item["price"], "subtotal": item["amount"],
                   "unit": item["unit"], "code": item["code"], "loccod": item["loccod"],
                   **({"factor": item["factor"]} if item.get("factor") else {}),
                   **({"vat_exempt": True} if item.get("vat_exempt") else {}),
                   **({"remarks": list(item["remarks"])} if item.get("remarks") else {}),
                   "posting_kind": "stock" if item["stock"] else "service"}
                  for item in doc["items"]],
    }
    page = {"page_index": 1, "fields": fields}
    pos = doc.get("pos")
    if isinstance(pos, dict):
        # 收银日汇总（B3a）：当天票号起止、张数、各付款方式金额照共用约定写进主页面（`express_rules.contracts.pos`）
        from decimal import Decimal as _Decimal

        from pearnly_core.express_rules.contracts import pos as pos_contract

        page["_extraction_audit"] = pos_contract.audit(pos["first"], pos["last"], pos["count"],
                                                       [(label, _Decimal(str(amount))) for label, amount in pos["payments"]])
        refund = doc.get("refund")
        if isinstance(refund, dict):
            # 收银退货单（B3b）：挂原 HS 的 Express 单号与原单含税合计，写账规则据此出退货单 SR
            page["_extraction_audit"].update(pos_contract.refund_audit(refund["against"], refund["against_total"],
                                                                       refund.get("cross_year") is True,
                                                                       refund.get("against_book") or ""))
    return [page]


__all__ = ["ATTENTION_STATES", "AUTO_RETRY_REASONS", "LOST", "MOVED_DURING_WRITE", "CHOICE", "ENTERING", "LEDGER_WORDS", "MISMATCH_REASON", "NOT_DONE", "RESULT_UNKNOWN", "SUCCEEDED", "UNKNOWN_REASONS", "ledger_settled", "ledger_word", "result_unknown", "DocumentError", "FAILED", "HELD", "MISMATCH", "NOT_CONNECTED", "NO_BOOK", "NO_TAX", "NO_WRITER", "PERIOD_OUTSIDE",
           "OFFLINE", "WAITING_COMPUTER", "waiting_computer", "waiting_computer_of", "OPEN_STATES", "VOIDED_AFTER_WRITE", "PRINTED_TOTAL_MISMATCH", "PRINTED_TOTAL_MISSING", "printed_hold", "WAREHOUSE_UNMAPPED", "ITEM_UNMAPPED", "unmapped", "POLL_REASONS", "PURCHASE_KINDS", "QUEUED", "READING", "RETRY_REASONS", "SALES_KINDS", "needs_person",
           "SEVERAL_BOOKS", "STATES", "VERSION", "WRITTEN", "branch5", "checked", "d", "document", "money", "pages",
           "party", "product", "spread", "state", "stock_targets", "rows", "tax13", "unit_names", "unit_remarks"]
