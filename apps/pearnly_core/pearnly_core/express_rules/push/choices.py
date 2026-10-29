"""Express 账套里「系统猜不准、要人选一次」的决定（2026-09-28 Express 压测，用户：「不确定的多选项，录入前 /
复核时让人先选定一次，系统记住后同类自动套用」）。纯计算，不连库；记在哪、谁能选见 `push/choices.py`。

每个决定按「账套 + 这一类」记一次：
- `party`：票面对方（进项供应商 / 销项客户）落 Express 哪个档。同税号有几个档（连锁分店、会计按名称区分）、
  或同税号只有一个档但分店号对不上（识别常把 00113 读成 00013）时要选；选「新建」就照票面新建，不再拦。
- `item`：票面品名（对方的叫法）落哪个 Express 存货。只管库存行；名字能对上 Express 存货名的不问。
- `account`：服务类进项记哪个费用科目（按供应商）。选了就按会计的记法写直接科目行，不建服务存货。
- `payment`：进项现购还是赊购（按供应商）。复核页改过付款方式就记下。
- `vat`：小票 / 简易税票上的税额抵不抵进项（按供应商）。
- `department`：部门码（整份账套一个）。
- `receipt`：POS 导出按天录的销售里，非现金的付款方式（转账、QR、刷卡、外卖平台）收进哪个银行 / 清算科目（按付款方式，
  2026-10-13）。每天按付款方式另记一张日记账凭证「借 这个科目 / 贷 现金」（`contracts/pos.py`）。
- `service_vat`：赊购单服务品的进项税口径（เกณฑ์สิทธิ / เกณฑ์เงินสด，按供应商；10-29）。这本账套的历史定得了的不问
  （`push/service_vat.py`，CC 桌面版随供应商主档报上来每家的口径串 ``srv``），定不了才问一次。
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
KINDS = ("party", "item", "account", "payment", "vat", "department", "receipt", "service_vat")  # 末项 = service_vat.KIND
# 只在 ERP 的收银退货单上出现的一类（不进 KINDS：Cowork 按 KINDS 存取、列出账套记忆，加进去会改 Cowork 现有输出）
REFUND = pos_contract.REFUND_KIND
PAYMENT_VALUES = ("cash", "credit")
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


def _stock_lines(fields, posting_kind) -> List[Dict[str, Any]]:
    items = [item for item in fields.get("items") or [] if isinstance(item, Mapping)]
    kinds = [str(item.get("posting_kind") or "") for item in items]
    if any(kinds):
        return [item for item, kind in zip(items, kinds) if kind == "stock"]
    return items if posting_kind == "stock" else []


def _expense_receipt(fields) -> bool:
    return (str(fields.get("document_type") or "") in _EXPENSE_DOC_TYPES
            and (_money(fields.get("vat")) or Decimal(0)) > 0 and len(digits(fields.get("seller_tax"))) == 13)


def resolve(fields: Mapping[str, Any], *, direction: str, posting_kind: Optional[str],
            config: Mapping[str, Any], remembered: Mapping[str, Mapping[str, str]],
            account_resolved: bool = False, fixed: Optional[Mapping[str, str]] = None,
            receipts: Iterable = (), trace: Optional[Dict[str, List[Optional[str]]]] = None,
            refund: bool = False, book: str = "", ask_service_vat: bool = False) -> Dict[str, Any]:
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
        elif not str(fields.get("payment_status") or "").strip():
            needed.append({"kind": "payment", "subject": party_key, "label": name,
                           "options": [{"code": code} for code in PAYMENT_VALUES], "current": ""})
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
        _service_vat(fields, posting_kind, config, applied, needed if ask_service_vat else [], chosen, used, explicit, book,
                     name, tax, branch)

    # ── 存货对照（库存行）──
    products = [row for row in _rows(config.get("reported_products")) if str(row.get("kind") or "stock") == "stock"]
    lines = _stock_lines(fields, posting_kind)
    if lines and products:
        names = {norm_name(row.get("name")): row["code"] for row in products if norm_name(row.get("name"))}
        codes = _codes(products)
        items: Dict[str, str] = {}
        for line in lines:
            if str(line.get("code") or "").strip():
                continue  # 行上已给出 Express 存货码（ERP 前台的单）：照码写，不按品名对、不问
            label = str(line.get("name") or "").strip()[:50]  # 载荷里的品名截到 50 字（`extract_line_items`）
            key = item_subject(direction, tax, name, label)
            value = chosen("item", key)
            if value and (value == NEW or value in codes):
                items[norm_name(label)] = value
                used("item", key)
            elif norm_name(label) in names and not value:
                continue  # Express 里有同名存货：小助手照名找得到，不用问
            else:
                needed.append({"kind": "item", "subject": key, "label": label, "current": value or ""})
        if items:
            applied["items"] = items

    # ── 服务类进项的费用科目 ──
    if direction == "purchase" and posting_kind != "stock" and not lines and not account_resolved:
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


def service_vat_subject(direction, tax, name, book: str = "") -> str:
    """服务品进项税口径的归类键：这家供应商（同 :func:`supplier_subject`），按公司记的前面加账套键。"""
    subject = supplier_subject(direction, tax, name)
    return f"{book}|{subject}" if book else subject


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
    value = service_vat.chosen_value(applied.get(service_vat.KIND))
    if value and payload.get("direction") in (None, "", "purchase"):
        # 人选的服务品进项税口径（这本账套的历史定不了时用）：旧版 CC 桌面版不认这个键、恒写 1，盖版本号让它诚实拒收
        payload[service_vat.PAYLOAD_KEY] = value
        version = max(version, service_vat.PAYLOAD_VERSION)
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


__all__ = ["KINDS", "NEW", "PAYMENT_VALUES", "REFUND", "VAT_VALUES", "apply_to_payload", "borrow_receipts", "item_subject", "norm_name",
           "party_subject", "resolve", "service_vat_subject", "supplier_subject"]
