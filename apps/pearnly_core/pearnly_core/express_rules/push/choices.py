"""Express 账套里「系统猜不准、要人选一次」的决定（2026-09-28 Express 压测，用户：「不确定的多选项，录入前 /
复核时让人先选定一次，系统记住后同类自动套用」）。纯计算，不连库；记在哪、谁能选见 `push/choices.py`。

每个决定按「账套 + 这一类」记一次：
- `party`：票面对方（进项供应商 / 销项客户）落 Express 哪个档。同税号有几个档（连锁分店、会计按名称区分）、
  或同税号只有一个档但分店号对不上（识别常把 00113 读成 00013）时要选；选「新建」就照票面新建，不再拦。
- `item`：票面品名（对方的叫法）落哪个 Express 存货。库存行：名字能对上 Express 存货名的不问。没声明过账类型的行（11-06 按行选
  物料与库存，评估 docs/work/2026-11-06-erp-line-item-stock-choice-评估.md 第 15 节）：这一问同时定这一行动不动库存 —— 选存货 /
  新建存货（``__new__``）= 库存行，选「记费用」（``__expense__``）或 Express 的非库存品 = 费用 / 服务行；名字对上库存品或非库存品的照它，
  不问。选定的过账类型放进 ``applied["kinds"]``，映射器按它定行（`with_line_kinds`）。
- `account`：服务类进项记哪个费用科目（按供应商）。选了就按会计的记法写直接科目行，不建服务存货。
- `payment`：进项现购还是赊购（按供应商）。复核页改过付款方式就记下。
- `vat`：小票 / 简易税票上的税额抵不抵进项（按供应商）。
- `department`：部门码（整份账套一个）。
- `receipt`：POS 导出按天录的销售里，非现金的付款方式（转账、QR、刷卡、外卖平台）收进哪个银行 / 清算科目（按付款方式，
  2026-10-13）。每天按付款方式另记一张日记账凭证「借 这个科目 / 贷 现金」（`contracts/pos.py`）。
- `service_vat`：赊购单服务品的进项税口径（เกณฑ์สิทธิ / เกณฑ์เงินสด，按供应商；10-29）。这本账套的历史定得了的不问
  （`push/service_vat.py`，CC 桌面版随供应商主档报上来每家的口径串 ``srv``），定不了才问一次。
- `location`：库存行进 Express 哪个仓（11-08，评估 docs/work/2026-11-07-cowork-express-warehouse-评估.md）。只在这份账套「在用的仓」
  （CC 桌面版 1.12.18 起随目录报 ``reported_warehouses``：代码表全部仓，本账 STCRD 动库存的进货 / 销货行用过的仓与主仓标 ``used``）有两个以上时才定；
  逐行：行上人选的（复核页 ``location``）> 这一行记过的（归类键同存货对照 `item_subject`）> 这份账套的默认仓（``subject = "*"``）>
  主仓。都没有时 ``ask_location`` 才问一次默认仓（预选主仓）；不问的入口（自动推 / 定时任务）照写主仓、不拦。主仓不写仓码（载荷同以前）。
- `refund`：收银退货单 SR 的退货科目（借方，税前；ERP B3b）。Express 实录 SR0000001 记在 41-02 一类「รับคืน / ลดหนี้」科目，
  账套里查不到原生的取法（ISRUN / ISINFO 都没有这一栏），所以按公司问一次、记住（整家公司一个，归类键 ``pos_refund``）。

选过的值在 Express 主档里已经没有（被删 / 改了）→ 当作没选过、重新问，不静默换别的。
主档没报上来（旧版小助手）的那一类不拦，照旧由小助手按名匹配。
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional

from pearnly_core.express_rules.contracts import pos as pos_contract
from pearnly_core.express_rules.push import service_vat
from pearnly_core.express_rules.push.payment import payment_verdict, payment_written

NEW = "__new__"
# 存货对照的一个值：这一行不建存货、写成费用 / 服务行（不动库存；11-06 按行选）
EXPENSE = "__expense__"
KINDS = ("party", "item", "account", "payment", "vat", "department", "receipt", "service_vat",  # 第 8 项 = service_vat.KIND
         "location")
LOCATION = "location"
# 整份账套的默认仓（同部门）
LOCATION_DEFAULT = "*"
# 只在 ERP 的收银退货单上出现的一类（不进 KINDS：Cowork 按 KINDS 存取、列出账套记忆，加进去会改 Cowork 现有输出）
REFUND = pos_contract.REFUND_KIND
PAYMENT_VALUES = ("cash", "credit")
# 引导式录入（覆盖表 A7）：明细对不上时怎么录——只记金额不入库 / 这次不录（「改明细」等第 2 期复核卡）；去留类，不预选
DETAIL_VALUES = ("amount_only", "skip")
# 引导式录入（覆盖表 A14）：销项收入科目按账套记一个（归类键固定）
REVENUE_SUBJECT = "revenue"
VAT_VALUES = ("claim", "expense")
# 票面名称里的法人前后缀：比名称时去掉（「บมจ.」上市公司缩写压测里就没去掉，匹配不上 Big C 的档）。
_LEGAL = ("บริษัทมหาชนจำกัด", "บริษัทจำกัด", "บริษัท", "บมจ.", "บมจ", "ห้างหุ้นส่วนจำกัด", "ห้างหุ้นส่วนสามัญ",
          "หจก.", "หจก", "หสน.", "จำกัด(มหาชน)", "จำกัดมหาชน", "(มหาชน)", "จำกัด", "co.,ltd.", "co.,ltd",
          "company limited", "co., ltd.", "co.ltd.", "limited", "ltd.", "public company limited", "pcl.")
_EXPENSE_DOC_TYPES = ("receipt", "simplified_tax_invoice")


def digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def norm_name(value) -> str:
    """名称归一（只用于比对 / 做键）：小写、去法人前后缀、去空白与标点；泰文声调符保留。"""
    text = unicodedata.normalize("NFC", str(value or "")).lower().replace("\xa0", " ")
    for word in sorted(_LEGAL, key=len, reverse=True):
        text = text.replace(word.lower(), " ")
    return re.sub(r"[\s.,()\-_/\\'\"&]+", "", text)


def branch5(value) -> str:
    """分店号归一成 5 位（空 / 0 = 总店 00000）。"""
    number = digits(value)
    return (number[-5:] if number else "0").zfill(5)


def party_subject(direction, tax, branch, name) -> str:
    tax = digits(tax)
    who = tax if len(tax) == 13 else f"name:{norm_name(name)}"
    return f"{direction}|{who}|{branch5(branch)}|{norm_name(name)}"


def supplier_subject(direction, tax, name) -> str:
    """按对方（不分店）记的决定：费用科目、付款方式、抵不抵进项。"""
    tax = digits(tax)
    return f"{direction}|{tax if len(tax) == 13 else 'name:' + norm_name(name)}"


def item_subject(direction, party_tax, party_name, item_name) -> str:
    return f"{supplier_subject(direction, party_tax, party_name)}|{norm_name(item_name)}"


def _money(value):
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def _rows(value) -> List[Dict[str, Any]]:
    return [row for row in value or [] if isinstance(row, Mapping) and str(row.get("code") or "").strip()]


def _codes(rows) -> set:
    return {str(row["code"]).strip() for row in rows}


def _party(fields, direction):
    side = "buyer" if direction == "sales" else "seller"
    return (fields.get(f"{side}_name") or "", fields.get(f"{side}_tax") or "", fields.get(f"{side}_branch") or "")


def _stock_lines(fields, posting_kind, kinds: Optional[Mapping[str, str]] = None) -> List[Dict[str, Any]]:
    items = [item for item in fields.get("items") or [] if isinstance(item, Mapping)]
    declared = line_kinds(items, posting_kind, kinds)
    return [item for item, kind in zip(items, declared) if kind == "stock"]


def _row_kind(item) -> str:
    kind = str(item.get("posting_kind") or "").strip()
    return kind if kind in ("stock", "service") else ""


def line_kinds(items, posting_kind, kinds: Optional[Mapping[str, str]] = None) -> List[str]:
    """每一行的过账类型（``stock`` / ``service`` / 空 = 没声明）：行上声明的（复核页逐行选的）> 存货对照定的（``kinds``，按规范品名）
    > 整批声明的。有一行在行上声明过时，没声明的行不套整批（同映射器 `item_posting_kinds`：部分行声明 = 要人补）。"""
    rows = [item for item in items or [] if isinstance(item, Mapping)]
    own = [_row_kind(item) for item in rows]
    batch = posting_kind if posting_kind in ("stock", "service") and not any(own) else ""
    picked = kinds or {}
    return [kind or str(picked.get(norm_name(str(item.get("name") or "")[:50])) or "") or batch
            for item, kind in zip(rows, own)]


def with_line_kinds(fields: Mapping[str, Any], kinds: Optional[Mapping[str, str]], posting_kind: Optional[str] = None):
    """把存货对照定下的过账类型写到行上（映射器定行之前）：行上已声明的不改；有定下的就把每一行都写上（没定下的行照整批），
    让映射器按行出 ``item_mode``。没有 ``kinds`` 原样返回。"""
    if not kinds or not isinstance(fields, Mapping):
        return fields
    items = fields.get("items")
    if not isinstance(items, list):
        return fields
    rows = [item for item in items if isinstance(item, Mapping)]
    declared = line_kinds(rows, posting_kind, kinds)
    if not any(declared) or not all(declared):
        return fields  # 有的行定不下（没整批声明）：不硬写，照旧交映射器判（部分声明 = 要人补）
    return {**fields, "items": [{**item, "posting_kind": kind} for item, kind in zip(rows, declared)]}


def _payment_question(subject, label, fields, guided) -> Dict[str, Any]:
    """付款方式那一题。引导模式下付款判断有把握（票种：收据 = 已付、完整税票 = 赊；漏斗 ``payment_verdict``）就作预选
    （来源 ``rule``，「怎么录」类，要点了才算，主会话 11-12）；没把握不预选。"""
    item = {"kind": "payment", "subject": subject, "label": label,
            "options": [{"code": code} for code in PAYMENT_VALUES], "current": ""}
    paid = payment_verdict(fields)[0] if guided else None
    if paid is not None:
        item.update(current="cash" if paid else "credit", source="rule")
    return item


def _detail_status(fields) -> str:
    """调用方没给明细对账状态时自己算（同映射器的对账闸 ``common.extract_line_items``）：税前额读不出、没有明细的回空（不出题）。"""
    from pearnly_core.express_rules.push import common

    base = _money(fields.get("subtotal"))
    if base is None or not isinstance(fields.get("items"), list) or not fields.get("items"):
        return ""
    total = _money(fields.get("total_amount") or fields.get("total"))
    return str(common.extract_line_items(dict(fields), base, total=total).get("status") or "")


def _expense_receipt(fields) -> bool:
    return (str(fields.get("document_type") or "") in _EXPENSE_DOC_TYPES
            and (_money(fields.get("vat")) or Decimal(0)) > 0 and len(digits(fields.get("seller_tax"))) == 13)


def resolve(fields: Mapping[str, Any], *, direction: str, posting_kind: Optional[str],
            config: Mapping[str, Any], remembered: Mapping[str, Mapping[str, str]],
            account_resolved: bool = False, fixed: Optional[Mapping[str, str]] = None,
            receipts: Iterable = (), trace: Optional[Dict[str, List[Optional[str]]]] = None,
            refund: bool = False, book: str = "", ask_service_vat: bool = False,
            undeclared_lines: bool = False, ask_location: bool = False, guided: bool = False,
            items_status: str = "") -> Dict[str, Any]:
    """这张票在这份账套里的决定 → `{"applied": {...}, "needed": [...]}`。

    `config`：推送准备用的账套配置（含小助手上报的 `reported_customers / reported_suppliers / reported_products /
    reported_accounts / reported_departments`）；`remembered`：`{kind: {subject: value}}`，这份账套里记过的；
    `account_resolved`：费用科目已由科目映射（分类 → 科目）定下，不用再问。
    `needed` 每条：`{kind, subject, label, options?, current?}`，页面据此弹一次框。
    `fixed`：`{kind: value}`，自动录入规则给这条规则定的值（付款 / 抵进项 / 费用科目或销项收入科目 / 存货 / 部门），只对这张票生效、
    优先于账套里记过的，不写进账套记忆（用户 2026-10-08：规则的默认值只管自己的票）。对方档不在此列。
    `receipts`：POS 日汇总票里非现金的付款方式 `[(subject, label)]`（`contracts.pos`）；每一种要一个收款科目，
    记过的（且在科目表里）放进 `applied["receipts"]` `{subject: 科目}`，没有的要人选。
    `refund`：这是收银退货单 SR（销项，`contracts.pos` 的 ``refund`` 块）：要一个退货科目，记过的（且在科目表里）放进
    `applied["refund"]`，没有的要人选（整家公司一个）。不给（默认）与以前完全一样。
    `book`：服务品进项税口径按「这本账套 × 这家供应商」记；账套记忆本来就按账套分的（Cowork）不给，按公司记的（ERP）给账套键。
    `ask_service_vat`：账套历史定不了时要不要问服务品进项税口径（ERP 单据上问；Cowork 的两处选择卡还没有这一项，先不问，
    记过的照用，定不了由 CC 桌面版拒写转人工）。
    `undeclared_lines`：没声明过账类型的行要不要按行问（11-06）：调用方在这张单推往这份账套「没声明就会交人」时给（永续 / 混合库存账套的
    商品单，Cowork `goods_posting.blocked`）；不给（默认）没声明的行照旧不问，交映射器按记账画像定。记过的、Express 同名的照样定下。
    `ask_location`：多仓账套（在用的仓 ≥ 2）的库存行定不了仓时要不要问一次默认仓（人在场的入口给：复核页、CC 卡片、人点的推送）；
    不给（默认）照写主仓，不问（自动推 / 定时任务，评估第 12 节 #4）。选定的仓放进 ``applied["locations"]`` ``{规范品名: 仓码}``
    （只放非主仓）；行上人选的、与这一行记过的不同的放进 ``applied["remember_locations"]`` ``{归类键: {value, label}}``，由调用方记下。
    `guided`：人在 CC 对话里过引导卡（引导式录入第 1 期，覆盖表 docs/work/2026-11-12-cc-guided-entry-express-coverage.md 第 5 节 A 类）：
    判不出的不再静默默认，一律出题——A1 进项付款状态是别的值、A3 服务品进项税口径、A6 多仓的仓、A7 明细对不上（``items_status``
    是 mismatch / incomplete）、A9 没有同税号档但有同名档、A12 销项没有付款信号、A14 销项收入科目没记过（连接默认只作预选）。
    不给（默认）与以前完全一样。
    `trace`：给了就就地记下「每一类用了哪几个归类键」`{kind: [subject | None]}`（None = 任务默认定的），
    `company_choices.decided_by` 据此说清按什么定的（第 2 期公司规则）；不给不记，结果不变。
    """
    fields = fields if isinstance(fields, Mapping) else {}
    direction = "sales" if direction == "sales" else "purchase"
    name, tax, branch = _party(fields, direction)
    applied: Dict[str, Any] = {}
    needed: List[Dict[str, Any]] = []

    def chosen(kind, subject):
        if kind != "party" and str((fixed or {}).get(kind) or "").strip():
            return str(fixed[kind]).strip()
        return str((remembered.get(kind) or {}).get(subject) or "").strip()

    def used(kind, subject):
        if trace is not None:
            by_task = kind != "party" and str((fixed or {}).get(kind) or "").strip()
            trace.setdefault(kind, []).append(None if by_task else subject)

    # ── 对方档 ──
    rows = _rows(config.get("reported_customers" if direction == "sales" else "reported_suppliers"))
    subject = party_subject(direction, tax, branch, name)
    value = chosen("party", subject)
    if not value and len(digits(tax)) == 13:
        # 同税号同分店记过、名称写法不同（识别把同一个名字读出一两个字不同，11-08 生产：「ซูเปอร์」/「ซุเปอร์」又问了一遍）：
        # 那里记的值只有一个就照用；记过几个不同的照常问。键本身不改，已记下的照样认
        prefix = f"{direction}|{digits(tax)}|{branch5(branch)}|"
        found = {key: str(v).strip() for key, v in (remembered.get("party") or {}).items()
                 if str(key).startswith(prefix) and str(v or "").strip()}
        if len(set(found.values())) == 1:
            subject, value = next(iter(found.items()))
    pinned = str(fields.get("party_code") or "").strip()
    if pinned and (not rows or pinned in _codes(rows)):
        # 来源已给出 Express 的对方档编码（ERP 前台从 Express 回读的主档里选的，ADR-025）：照码，不问、不记
        applied["party"] = pinned
    elif value and (value == NEW or not rows or value in _codes(rows)):
        applied["party"] = value
        used("party", subject)
    elif rows and len(digits(tax)) == 13:
        same_tax = [row for row in rows if digits(row.get("tax_id")) == digits(tax)]
        exact = [row for row in same_tax if branch5(row.get("branch")) == branch5(branch)]
        if len(same_tax) >= 2 or (same_tax and not exact) or value:
            needed.append({"kind": "party", "subject": subject, "label": name,
                           "options": [{"code": row["code"], "name": row.get("name") or "",
                                        "branch": branch5(row.get("branch"))} for row in same_tax[:50]],
                           "current": value or ""})
    if guided and "party" not in applied and not any(item["kind"] == "party" for item in needed) and norm_name(name):
        # A9：没有同税号档、但 Express 里有同名档（疑似重复）：问一次选已有档还是新建，不交电脑端转人工
        same_name = [row for row in rows if norm_name(row.get("name")) == norm_name(name)
                     and not (len(digits(tax)) == 13 and digits(row.get("tax_id")) == digits(tax))]
        if same_name:
            needed.append({"kind": "party", "subject": subject, "label": name,
                           "options": [{"code": row["code"], "name": row.get("name") or "",
                                        "branch": branch5(row.get("branch"))} for row in same_name[:50]]
                           + [{"code": NEW}], "current": value or ""})

    # ── 部门（整份账套一个）──
    # 不再问（用户 2026-10-08：62 套真实账套全都有两个以上部门，等于每套账都要问一次）：选过的照用；没选过由小助手
    # 写入时取这本单据簿自己设的默认部门（ISRUN.DEPCOD），簿上也没设就不记部门 —— 与会计在 Express 里手录不选部门一样。
    departments = _rows(config.get("reported_departments"))
    value = chosen("department", "*")
    if departments and value and value in _codes(departments):
        applied["department"] = value
        used("department", "*")

    party_key = supplier_subject(direction, tax, name)
    if direction == "purchase":
        # ── 付款方式：票面人工裁决 / 票面写明的优先；没有就按任务的默认值 / 这家记过的；都没有要问 ──
        # 票面写明了（付款状态 / 方式，例如收据上的「เงินสด」）就照票面，任务默认值与记过的只管票面没写的
        # （10-09 实测：任务默认赊购把三张票面写明现金的票记成了 RR 赊购）。
        explicit = str(fields.get("posting_payment_manual") or "").strip()
        value = chosen("payment", party_key)
        written = payment_written(fields)
        if explicit in PAYMENT_VALUES:
            applied["remember_payment"] = explicit
        elif written:
            pass
        elif value in PAYMENT_VALUES:
            applied["payment"] = value
            used("payment", party_key)
            # 哪来的（写进载荷 doctype_src，CC 推送结果据此说清「按这家记过的现购」，验收 F19）
            applied["payment_from"] = "task_default" if str((fixed or {}).get("payment") or "").strip() else "remembered"
        elif guided or not str(fields.get("payment_status") or "").strip():
            # A1：引导模式下付款状态是别的值（判不出）也问，不落默认 RR
            needed.append(_payment_question(party_key, name, fields, guided))
        # ── 小票 / 简易税票的税额抵不抵进项 ──
        if _expense_receipt(fields):
            value = chosen("vat", party_key)
            if value in VAT_VALUES:
                applied["vat"] = value
                used("vat", party_key)
            else:
                needed.append({"kind": "vat", "subject": party_key, "label": name,
                               "options": [{"code": code} for code in VAT_VALUES], "current": ""})

        # ── 服务品进项税口径：赊购单有服务品、这本账套的历史定不了才问 ──
        _service_vat(fields, posting_kind, config, applied, needed if (ask_service_vat or guided) else [], chosen, used,
                     explicit, book,
                     name, tax, branch)

    elif guided:
        # A12：销项没有付款信号（人定的 / 票面写明的）时问现销还是赊销，不静默默认赊销；这家记过的照用
        explicit = str(fields.get("posting_payment_manual") or "").strip()
        value = chosen("payment", party_key)
        if explicit in PAYMENT_VALUES or payment_written(fields):
            pass
        elif value in PAYMENT_VALUES:
            applied["payment"] = value
            used("payment", party_key)
            applied["payment_from"] = "task_default" if str((fixed or {}).get("payment") or "").strip() else "remembered"
        else:
            needed.append(_payment_question(party_key, name, fields, guided))
        _sales_service_vat(fields, posting_kind, config, applied, needed, chosen, used, explicit, book)

    # ── 存货对照：库存行落哪个存货；没声明过账类型的行顺带定它动不动库存（11-06 按行选）──
    catalog = _rows(config.get("reported_products"))
    products = [row for row in catalog if str(row.get("kind") or "stock") == "stock"]
    others = [row for row in catalog if str(row.get("kind") or "stock") != "stock"]
    rows_all = [item for item in fields.get("items") or [] if isinstance(item, Mapping)]
    own_kinds = [_row_kind(item) for item in rows_all]
    batch = posting_kind if posting_kind in ("stock", "service") and not any(own_kinds) else ""
    items: Dict[str, str] = {}
    kinds: Dict[str, str] = {}
    if rows_all and catalog:
        names = {norm_name(row.get("name")): row["code"] for row in products if norm_name(row.get("name"))}
        other_names = {norm_name(row.get("name")) for row in others if norm_name(row.get("name"))}
        codes, other_codes = _codes(products), _codes(others)
        for line, own in zip(rows_all, own_kinds):
            if str(line.get("code") or "").strip():
                continue  # 行上已给出 Express 存货码（ERP 前台的单）：照码写，不按品名对、不问
            label = str(line.get("name") or "").strip()[:50]  # 载荷里的品名截到 50 字（`extract_line_items`）
            norm = norm_name(label)
            key = item_subject(direction, tax, name, label)
            if own == "service":
                continue  # 行上声明了服务（复核页逐行选的）：不问存货
            if not own:
                # 行上没声明：这一行记过的 > 整批声明 > Express 同名（库存品 = 库存行，非库存品 = 费用行）> 问
                value = str((remembered.get("item") or {}).get(key) or "").strip()
                if value == EXPENSE or value in other_codes:
                    kinds[norm] = "service"
                    used("item", key)
                    continue
                if value and (value == NEW or value in codes):
                    kinds[norm] = "stock"
                    items[norm] = value
                    used("item", key)
                    continue
                if batch == "service":
                    continue
                if not batch:
                    if not undeclared_lines:
                        continue  # 调用方没要按行问：照旧交映射器按记账画像定（周期制账套同名存货也不改成库存行）
                    if norm in names:
                        kinds[norm] = "stock"
                    elif norm in other_names:
                        kinds[norm] = "service"
                    else:
                        needed.append({"kind": "item", "subject": key, "label": label, "current": value or "",
                                       "expense": True})
                    continue
            # 库存行（行上或整批声明了存货）：照旧——选过的（含任务默认的「新建」）> Express 同名不问 > 问
            value = chosen("item", key)
            if not own and not value and norm in other_names and norm not in names:
                # 整批走库存（卡片预选 / 公司选的），这一行却与 Express 的非库存品同名：Express 有这件、写入照名取它（不动库存），
                # 不问「没有商品资料」、不建重复物料，也不为它问出库仓（测试机 11-09 shots/13）。行上逐行声明了存货的照旧问
                kinds[norm] = "service"
                continue
            if value and (value == NEW or value in codes):
                items[norm] = value
                used("item", key)
            elif norm in names and value in ("", EXPENSE):
                continue  # Express 里有同名存货：小助手照名找得到，不用问
            else:
                # 整批声明的存货行也可以改成记费用（这一行的选择优先于整批）；行上声明的存货行不给「记费用」
                needed.append({"kind": "item", "subject": key, "label": label,
                               "current": "" if value == EXPENSE else value or "", "expense": not own})
    if items:
        applied["items"] = items
    if kinds:
        applied["kinds"] = kinds
    lines = _stock_lines(fields, posting_kind, kinds)

    # ── 仓库：多仓账套的库存行进哪个仓 ──
    _locations(config, lines, direction, tax, name, remembered, chosen, used, applied, needed, ask_location or guided)

    # ── 服务类进项的费用科目：没有库存行的单；有库存行时只要有一行定为费用 / 服务（混行单，11-06）也问 ──
    effective = line_kinds(rows_all, posting_kind, kinds)
    expense_lines = any(kind == "service" for kind in effective)
    if (direction == "purchase" and not account_resolved
            and ((posting_kind != "stock" and not lines) or (lines and expense_lines))):
        accounts = _rows(config.get("reported_accounts"))
        value = chosen("account", party_key)
        if value and (not accounts or value in _codes(accounts)):
            applied["account"] = value
            # 哪来的（写进载荷 account_source，CC 推送结果据此说清科目怎么定的，验收 F19）；人这次选的 / 历史定的由
            # 推送服务再细分（`push.application.service._decisions`）
            applied["account_from"] = "task_default" if str((fixed or {}).get("account") or "").strip() else "remembered"
            used("account", party_key)
        else:
            needed.append({"kind": "account", "subject": party_key, "label": name, "current": value or ""})

    # ── 销项的收入科目：只有定时任务设了默认收入科目才定（用户 2026-10-20），不用账套记忆、不问；没设照连接的默认收入科目。
    # 与进项同一口径：科目映射（分类 → 科目）已定的照映射，科目须在这份账套的科目表里
    value = str((fixed or {}).get("account") or "").strip()
    if direction == "sales" and value and not account_resolved:
        accounts = _rows(config.get("reported_accounts"))
        if not accounts or value in _codes(accounts):
            applied["account"] = value
            applied["account_from"] = "task_default"
            used("account", party_key)
    elif direction == "sales" and guided and not account_resolved:
        # A14：引导模式下收入科目按账套记一个；没记过就问，连接的默认收入科目只作预选（以前静默落默认只打「待核」）
        accounts = _rows(config.get("reported_accounts"))
        value = chosen("account", REVENUE_SUBJECT)
        if value and (not accounts or value in _codes(accounts)):
            applied["account"] = value
            applied["account_from"] = "remembered"
            used("account", REVENUE_SUBJECT)
        else:
            default = str(config.get("revenue_acc") or "").strip()
            needed.append({"kind": "account", "subject": REVENUE_SUBJECT, "label": "",
                           "current": default if default and (not accounts or default in _codes(accounts)) else ""})

    # ── 明细对不上（A7，引导模式）：怎么录由人定一次，只管这一张，不预选 ──
    if guided and not items_status:
        items_status = _detail_status(fields)
    if guided and str(items_status or "") in ("mismatch", "incomplete"):
        ref = str(fields.get("invoice_number") or fields.get("invoice_no") or "").strip()
        needed.append({"kind": "detail", "subject": f"detail|{direction}|{digits(tax)}|{norm_name(name)}|{ref}"[:1000],
                       "label": name, "current": "", "options": [{"code": code} for code in DETAIL_VALUES]})

    # ── POS 日汇总：非现金付款方式的收款科目 ──
    accounts = _rows(config.get("reported_accounts"))
    for subject, label in receipts or ():
        value = chosen("receipt", subject)
        if value and (not accounts or value in _codes(accounts)):
            applied.setdefault("receipts", {})[subject] = value
            used("receipt", subject)
        else:
            needed.append({"kind": "receipt", "subject": subject, "label": label, "current": value or ""})

    # ── 收银退货单 SR：退货科目（整家公司一个）──
    if refund and direction == "sales":
        kind, subject = pos_contract.REFUND_KIND, pos_contract.REFUND_SUBJECT
        value = chosen(kind, subject)
        if value and (not accounts or value in _codes(accounts)):
            applied[kind] = value
            used(kind, subject)
        else:
            needed.append({"kind": kind, "subject": subject, "label": "", "current": value or ""})
    return {"applied": applied, "needed": needed}


def warehouses_in_use(rows) -> List[Dict[str, Any]]:
    """这份账套报来的仓里在用的（``used``，含主仓）；不到两个回空 = 不让人选仓、照写主仓。旧版 CC 桌面版不报 → 空。"""
    found = [row for row in _rows(rows) if str(row.get("used") or "") == "1"]
    return found if len(found) >= 2 else []


def main_warehouse(rows) -> str:
    """主仓码（报来的仓里标 ``main`` 的）；没有回空。"""
    return next((str(row["code"]).strip() for row in _rows(rows) if str(row.get("main") or "") == "1"), "")


def _locations(config, lines, direction, tax, name, remembered, chosen, used, applied, needed, ask) -> None:
    """库存行各进哪个仓（见文件头 `location`）。只放非主仓：主仓不带仓码，CC 桌面版照旧写 ISINFO.MAINLOC。"""
    rows = warehouses_in_use(config.get("reported_warehouses"))
    # 这一张还在问存货对照的行（答了可能成库存行）：默认仓并进同一问，不等答完再另问一轮（评估第 12 节 #7）
    pending = any(item.get("kind") == "item" for item in needed)
    if not rows or not (lines or pending):
        return
    codes = _codes(rows)
    main = main_warehouse(config.get("reported_warehouses"))
    default = chosen(LOCATION, LOCATION_DEFAULT)
    default = default if default in codes else ""
    picked: Dict[str, str] = {}
    remember: Dict[str, Dict[str, str]] = {}
    unsettled = pending and not default
    for line in lines:
        label = str(line.get("name") or "").strip()[:50]
        norm = norm_name(label)
        own = str(line.get("location") or "").strip()
        key = item_subject(direction, tax, name, label)
        line_value = str((remembered.get(LOCATION) or {}).get(key) or "").strip()
        if own in codes:
            value = own
            if own != line_value:
                remember[key] = {"value": own, "label": label}  # 行上人选的仓按这一行记下（同票上人改的付款方式），下回同一样东西照它
        elif line_value in codes:
            value = line_value
            used(LOCATION, key)
        elif default:
            value = default
            used(LOCATION, LOCATION_DEFAULT)
        else:
            unsettled = True
            continue
        if value != main:
            picked[norm] = value
    if picked:
        applied["locations"] = picked
    if remember:
        applied["remember_locations"] = remember
    if unsettled and ask:
        needed.append({"kind": LOCATION, "subject": LOCATION_DEFAULT, "label": "", "current": main if main in codes else "",
                       "options": [{"code": row["code"], "name": row.get("name") or ""} for row in rows]})


def service_vat_subject(direction, tax, name, book: str = "") -> str:
    """服务品进项税口径的归类键：这家供应商（同 :func:`supplier_subject`），按公司记的前面加账套键。"""
    subject = supplier_subject(direction, tax, name)
    return f"{book}|{subject}" if book else subject


def sales_service_vat_subject(book: str = "") -> str:
    """销项服务品税口径按账套记一个（主会话 11-12：约一成账套历史定不了，问一次按公司记住）。"""
    return f"sales|*|{book}"


def _sales_service_vat(fields, posting_kind, config, applied, needed, chosen, used, explicit, book) -> None:
    """赊销带服务品（或没有明细）、有税时的销项税口径（Express ARTRN.SRV_VATTYP；引导模式）：记过的照用；这本账套的销项历史
    （客户档读回的口径串并集）一贯一种就不问；两种都有才问一次。客户档没带口径串（CC 桌面版还没报）不问。现销、没税、只有库存品的不问。"""
    items = [item for item in fields.get("items") or [] if isinstance(item, Mapping)]
    has_vat = (_money(fields.get("vat")) or Decimal(0)) > 0 or any(item.get("vat_exempt") for item in items)
    paid = explicit == "cash" or applied.get("payment") == "cash" or payment_verdict(fields)[0] is True
    service = not items or len(_stock_lines(fields, posting_kind)) < len(items)
    if not has_vat or paid or not service:
        return
    subject = sales_service_vat_subject(book)
    value = chosen(service_vat.KIND, subject)
    if service_vat.chosen_value(value):
        applied[service_vat.KIND] = value
        used(service_vat.KIND, subject)
        return
    rows = [row for row in _rows(config.get("reported_customers")) if "srv" in row]
    if not rows:
        return
    if service_vat.basis(has_vat=True, cash=False, service=True,
                         book=service_vat.book_verdict(row.get("srv") for row in rows)) is None:
        needed.append({"kind": service_vat.KIND, "subject": subject, "label": "",
                       "options": [{"code": code} for code in service_vat.OPTIONS], "current": ""})


def _supplier_verdict(rows, fields, applied, tax, branch) -> Optional[str]:
    """这家供应商在账套里的口径串（主档行的 ``srv``）；没有这家（新供应商）回 ""；同税号几个档还没选定回 None（先问对方档）。"""
    code = str(fields.get("party_code") or applied.get("party") or "").strip()
    if code and code != NEW:
        row = next((row for row in rows if str(row["code"]).strip() == code), None)
        return service_vat.verdict(str((row or {}).get("srv") or ""))
    if code == NEW or len(digits(tax)) != 13:
        return ""
    same = [row for row in rows if digits(row.get("tax_id")) == digits(tax)]
    exact = [row for row in same if branch5(row.get("branch")) == branch5(branch)]
    if len(exact) == 1 or (len(same) == 1 and not exact):
        return service_vat.verdict(str((exact or same)[0].get("srv") or ""))
    return None if same else ""


def _service_vat(fields, posting_kind, config, applied, needed, chosen, used, explicit, book, name, tax, branch):
    """赊购单服务品的进项税口径（`push/service_vat.py`）：记过的照用（写进载荷 ``srv_vattyp``）；没记过、这本账套历史定不了的问一次。
    主档没带口径串（旧版 CC 桌面版）的不问，交 CC 写时按账套历史定。现购单、只有库存品、没有税的不问。"""
    items = [item for item in fields.get("items") or [] if isinstance(item, Mapping)]
    has_vat = (_money(fields.get("vat")) or Decimal(0)) > 0 or any(item.get("vat_exempt") for item in items)
    paid = explicit == "cash" or applied.get("payment") == "cash" or payment_verdict(fields)[0] is True
    service = not items or len(_stock_lines(fields, posting_kind)) < len(items)
    if not has_vat or paid or not service:
        return
    subject = service_vat_subject("purchase", tax, name, book)
    value = chosen(service_vat.KIND, subject)
    if service_vat.chosen_value(value):
        applied[service_vat.KIND] = value
        used(service_vat.KIND, subject)
        return
    rows = _rows(config.get("reported_suppliers"))
    if not rows or not any("srv" in row for row in rows):
        return
    supplier = _supplier_verdict(rows, fields, applied, tax, branch)
    if supplier is None:
        return
    book_verdict = service_vat.book_verdict(row.get("srv") for row in rows)
    if service_vat.basis(has_vat=True, cash=False, service=True, supplier=supplier, book=book_verdict) is None:
        needed.append({"kind": service_vat.KIND, "subject": subject, "label": name,
                       "options": [{"code": code} for code in service_vat.OPTIONS], "current": ""})


def apply_to_payload(payload: Dict[str, Any], applied: Mapping[str, Any]) -> Dict[str, Any]:
    """把选定的对方档 / 存货编码 / 部门码写进载荷（映射器出载荷之后、盖版本号之前）。

    带了存货编码或部门码 → 载荷版本 2：旧版小助手会静默忽略这两个键（按名新建存货、部门留空），
    所以用版本号让它诚实拒收（见 `preflight` 版本协商）。"""
    party = str(applied.get("party") or "")
    side = "customer" if payload.get("direction") == "sales" else "supplier"
    block = payload.get(side) if isinstance(payload.get(side), dict) else None
    if block is not None and party:
        if party == NEW:
            block.update(code="", confirmed_new=True, **{f"{side}_new": True})
        else:
            block.update(code=party, **{f"{side}_new": False})
    items = applied.get("items") or {}
    version = 1
    if items:
        for item in payload.get("items") or []:
            code = items.get(norm_name(item.get("name")))
            if item.get("code"):
                continue  # 行上自带的存货码（`common.extract_line_items` 照来源带上）不被同名的选择盖掉
            if code and code != NEW and str(item.get("item_mode") or "") in ("stock_item", "stock_sale"):
                item["code"] = code
                version = 2
    if applied.get("department"):
        payload["depcod"] = str(applied["department"])[:4]
        version = 2
    locations = applied.get("locations") or {}
    if locations:
        # 多仓账套选定的仓（只放了非主仓）：写在库存行上，`finalize_payload` 见行上 ``loccod`` 盖载荷 4（CC 校验在仓库代码表里）
        for item in payload.get("items") or []:
            code = locations.get(norm_name(item.get("name")))
            if code and not item.get("loccod") and str(item.get("item_mode") or "") in ("stock_item", "stock_sale"):
                item["loccod"] = str(code)[:4]
    value = service_vat.chosen_value(applied.get(service_vat.KIND))
    if value and payload.get("direction") in (None, "", "purchase"):
        # 人选的服务品进项税口径（这本账套的历史定不了时用）：旧版 CC 桌面版不认这个键、恒写 1，盖版本号让它诚实拒收
        payload[service_vat.PAYLOAD_KEY] = value
        version = max(version, service_vat.PAYLOAD_VERSION)
    elif value and payload.get("direction") == "sales":
        # 销项服务品税口径（引导卡答的 / 记过的，主会话 11-12）：同名键；``2`` 由 `common.finalize_payload` 盖 21（旧版写死 1）
        payload[service_vat.PAYLOAD_KEY] = value
    if version > int(payload.get("payload_version") or 1):
        payload["payload_version"] = version
    return payload


def borrow_receipts(remembered: Dict[str, Dict[str, str]], receipts: Iterable, borrowed: Mapping[str, str],
                    accounts: Any) -> None:
    """POS 收款科目按公司问一次：这份账套还没记过的付款方式，借同一家公司别的账套记过的值（``borrowed``
    `{subject: 科目}`），科目须在本账套科目表里（``accounts``，小助手报上来的；没报上来不借）。就地改 ``remembered``，
    只用于这一次判断，不写进本账套记忆。"""
    codes = _codes(_rows(accounts))
    mine = remembered.setdefault("receipt", {})
    for subject, _label in receipts or ():
        value = str((borrowed or {}).get(subject) or "").strip()
        if subject not in mine and value and value in codes:
            mine[subject] = value


__all__ = ["EXPENSE", "KINDS", "LOCATION", "LOCATION_DEFAULT", "NEW", "main_warehouse", "warehouses_in_use", "PAYMENT_VALUES", "REFUND", "VAT_VALUES", "apply_to_payload", "borrow_receipts", "item_subject", "norm_name",
           "line_kinds", "party_subject", "resolve", "service_vat_subject", "supplier_subject", "with_line_kinds"]
