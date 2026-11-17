"""一次 MR.ERP 收款导入直写（纯网络，不碰数据库；多产品共用，现在只有 ERP 收款用，2026-11-17）。

同发票导入的会话与 5 步（`transport.MrErpImport`：共用入口 `mrerp.account.use` 拿会话——同账号锁、被踢标记、接上缓存会话；
formupload → uploadexcel → formrdpc → importpc → report），模块 ``receipt``（impfarev 375，回读列表 farev）。

- 表头取值：分店 / 部门 / งาน 照这份账套的主档与选过的（`push.defaults.AccountDefaults`，同发票）；销售员先用收款单上的
  （按员工代码或去空格的名字对上，`xlsx.receipt.employee_name`），对不上用账套默认员工，不拦、不新造；部门收款单上给了、
  主档里有就用它。
- 科目：收到的钱进「เงินสด」那一格——现金是参考科目 2「เงินสด」，转账是收款人选的银行科目（11-16 实测：银行科目填进现金列，
  日记账借银行）；零用金、其他 1、其他 3 填 MR.ERP 收款新单的参考科目（1 / 20 / 5，`farev/allform.php`）+ 0，没配的用收钱那格的
  科目 + 0；折扣进其他 2、记负数（现金 + 零用金 = 核销合计 + 其他三格；收款单给的折扣科目，没给按这份账套的 ``discount_account``）。
- 菜单：收款导入页里这份账套选过的、或唯一一个已关联日记账的（`menus.resolve`，没关联的不用）；定不下来不导。
- 结论同发票导入（`execute.execute` 的形状）：受理后在所选菜单的 farev 列表里按单号查到才算成功，记 MR.ERP 单号（菜单前缀 +
  我方单号）；「单号已存在」也要查到；导入发出之后出错、受理了却查不到 = 结果不明（``unknown``，不当失败重推）。

载荷（产品冻结、纯 JSON）::

    {"number", "date", "customer", "salesman", "department",
     "money": {"kind": "cash" | "transfer", "account": 转账的银行科目, "amount"},
     "discount": {"amount", "account"}, "bills": [{"docnum", "amount"}], "notes": [备注1, 备注2, 备注3],
     "menu": 选过的收款菜单号, "defaults": {主档字段: 选过的代码}}
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict

from pearnly_core.session_lock import SessionLockUnavailable
from pearnly_core.mrerp import masters as master_rules
from pearnly_core.mrerp import menus as mrerp_menus
from pearnly_core.mrerp.master_reader import MasterReader
from pearnly_core.mrerp.push import readback
from pearnly_core.mrerp.push.account_valve import ACCOUNT_REVIEW_CODE
from pearnly_core.mrerp.push.defaults import AccountDefaults, Missing, tag_default_changed
from pearnly_core.mrerp.push.duplicates import is_already_pushed_error
from pearnly_core.mrerp.push.models import MRERPAccountInUseError, MRERPAuthError, MRERPBusinessError, MRERPTechnicalError
from pearnly_core.mrerp.push.preview_parse import TAGSTRIP_RE, preview_hints
from pearnly_core.mrerp.push.report_parser import parse_import_report
from pearnly_core.mrerp.push.transport import READBACK_NOT_FOUND, READBACK_NOT_LINKED, MrErpImport
from pearnly_core.mrerp.push.xlsx import receipt as sheet

logger = logging.getLogger(__name__)

DOC_TYPE = "receipt"
# 参考科目（tbl_acreffile 的 id）：MR.ERP 收款新单的默认科目（`farev/allform.php`）
REFERENCE = {"cash": 2, "petty": 1, "other1": 20, "other2": 18, "other3": 5}
ZERO = Decimal("0.00")


def execute(receipt: Dict[str, Any], target: Dict[str, Any], secrets: Dict[str, str], system_url: str) -> Dict[str, Any]:
    """→ ``{status: success | failed | unknown, reason, docnum, meta, menus, masters}``（同 `execute.execute`）。永不抛。"""
    comidyear, _, seldb = str(target.get("account_set") or "").partition(":")
    importer = None
    try:
        importer = MrErpImport(system_url=system_url, username=secrets.get("username") or "",
                               password=secrets.get("password") or "", comidyear=comidyear, seldb=seldb,
                               doc_type=DOC_TYPE)
        with importer:
            return _run(importer, receipt)
    except (MRERPAuthError, MRERPBusinessError, MRERPTechnicalError, SessionLockUnavailable) as error:
        code = {MRERPAccountInUseError: "ERR_MRERP_ACCOUNT_IN_USE", MRERPAuthError: "ERR_AUTH",
                MRERPBusinessError: "ERR_BUSINESS", MRERPTechnicalError: "ERR_TECHNICAL"}.get(type(error), "ERR_SESSION_LOCK")
        text = str(error)
        return _exception(importer, text if text.startswith(code) else f"{code}: {text}")
    except Exception as error:  # noqa: BLE001 — 契约：任何异常都要落到收款记录，不冒到队列
        logger.exception("MR.ERP receipt push raised")
        return _exception(importer, f"ERR_UNEXPECTED: {type(error).__name__}: {error}")


def _outcome(importer, status, reason="", docnum="", meta=None) -> Dict[str, Any]:
    defaults = importer.account_defaults
    return {"status": status, "reason": str(reason or "")[:500], "docnum": docnum, "meta": meta or {},
            "menus": importer.observed_menus, "masters": defaults.observed if defaults else {}}


def _exception(importer, reason) -> Dict[str, Any]:
    sent = bool(importer and importer.import_sent)
    return {"status": "unknown" if sent else "failed", "reason": reason[:500],
            "menus": importer.observed_menus if importer else {},
            "masters": importer.account_defaults.observed if importer and importer.account_defaults else {}}


def header(defaults: AccountDefaults, receipt: Dict[str, Any]) -> Dict[str, str]:
    """分店 / 部门 / งาน / 销售员（见文件头）；定不下来抛 `Missing`。"""
    values = {field: defaults.value(field)["code"] for field in ("branch", "dept", "job")}
    wanted_dept = str(receipt.get("department") or "").strip()
    depts = master_rules.masters([defaults.reader.listbox("fabillrev") or {}]).get("dept") or []
    if wanted_dept and any(option["code"] == wanted_dept for option in depts):
        values["dept"] = wanted_dept
    staff = master_rules.masters([defaults.reader.listbox("fabillrev") or {}]).get("employee") or []
    values["employee"] = sheet.employee_name(staff, receipt.get("salesman")) or defaults.value("employee")["name"]
    return values


def accounts(defaults: AccountDefaults, receipt: Dict[str, Any], reference: Dict[int, str]) -> Dict[str, str]:
    """五对「科目 + 金额」（见文件头）；收钱的科目定不下来抛 `Missing`。"""
    money = receipt.get("money") or {}
    if money.get("kind") == "transfer":
        main = str(money.get("account") or "").strip()
    else:
        main = str(reference.get(REFERENCE["cash"]) or "").strip()
    if not main:
        raise Missing(master_rules.UNREADABLE)  # 现金：这家公司在 MR.ERP 的参考科目没配「เงินสด」
    discount = sheet.money((receipt.get("discount") or {}).get("amount"))
    out = {"cash_account": main, "cash": str(sheet.money(money.get("amount"))),
           "petty": "0", "other1": "0", "other2": str(-discount) if discount > ZERO else "0", "other3": "0"}
    for slot in ("petty", "other1", "other2", "other3"):
        out[f"{slot}_account"] = str(reference.get(REFERENCE[slot]) or "").strip() or main
    if discount > ZERO:
        out["other2_account"] = str((receipt.get("discount") or {}).get("account") or "").strip() or \
            defaults.value("discount_account")["code"]
    return out


def _run(importer: MrErpImport, receipt: Dict[str, Any]) -> Dict[str, Any]:
    module = importer.module
    importer._prepare(module)
    reader = MasterReader(importer._request, importer.login_url)
    defaults = AccountDefaults(reader, receipt.get("defaults") or {})
    importer.account_defaults = defaults
    # 参考科目与总店只有网页，会改写会话里的当前菜单：在打开导入页之前读（同发票导入）
    reference = reader.reference() or {}
    reader.head_branch()
    try:
        head = header(defaults, receipt)
        slots = accounts(defaults, receipt, reference)
    except Missing as missing:
        return _outcome(importer, "failed", missing.reason)
    chart = defaults.chart()
    unknown = sorted({slots["cash_account"], slots["other2_account"]} - set(chart)) if chart else []
    if unknown:
        return _outcome(importer, "failed", f"{ACCOUNT_REVIEW_CODE}; {', '.join(unknown)}")

    menu, reason = mrerp_menus.resolve(importer._read_menus(module), receipt.get("menu"))
    if reason:
        return _outcome(importer, "failed", reason)
    selmenu = menu["idmenu"]
    importer._menu_label = str(menu.get("label") or "")
    bills = [{"docnum": str(b.get("docnum") or "").strip(), "amount": str(b.get("amount") or "")}
             for b in receipt.get("bills") or []]
    number = str(receipt.get("number") or "").strip()
    filled = {"number": number, "date": receipt.get("date"), "customer": receipt.get("customer"),
              **head, **slots, "notes": receipt.get("notes") or [], "bills": bills}
    problem = sheet.check(filled)
    if problem:
        return _outcome(importer, "failed", problem)
    blob = sheet.generate(filled)

    importer._upload_xlsx(blob, selmenu=selmenu, opened=True)  # 上面核菜单时刚打开过导入页
    form_fields, row_ids, idus_form = importer._fetch_preview(selmenu=selmenu)
    if not row_ids:
        hints = preview_hints(importer._last_preview_html or "")
        raise MRERPBusinessError("preview has no importable rows (xlsx rejected server-side)"
                                 + (": " + " / ".join(hints[:3]) if hints else ""))
    importer.import_sent = True
    raw = importer._confirm_import(form_fields, row_ids)
    already = False
    if raw != "1":
        if not raw.strip().isdigit():
            summary = TAGSTRIP_RE.sub("", raw).strip()[:120]
            return _outcome(importer, "failed", "; ".join(["ERR_MRERP_IMPORT_ERROR"] + ([summary] if summary else [])))
        report = parse_import_report(importer._fetch_report(idus_form))
        if number not in report.success:
            row = next((r for r in report.failed if r.invoice_no == number), None)
            reasons = list(row.reasons) if row else ["report did not mention this receipt"]
            if not row or not is_already_pushed_error("; ".join(reasons)):
                return _outcome(importer, "failed", "; ".join(tag_default_changed(reasons)))
            already = True
    found, listing = importer._read_back(selmenu, [number])
    listed = found.get(number)
    meta: Dict[str, Any] = {"doc_type": DOC_TYPE, "mrerp_menu": importer._menu_label}
    if listed is None:
        return _outcome(importer, "unknown", READBACK_NOT_LINKED if listing == "not_linked" else READBACK_NOT_FOUND,
                        meta=meta)
    if already:
        meta["already_pushed"] = True
    billed = sum((sheet.money(b["amount"]) for b in bills), ZERO)
    meta["verify"] = readback.amount_check(listed.amount, str(billed))  # farev 列表金额 = 核销合计（只记不拦）
    return _outcome(importer, "success", docnum=listed.docno, meta=meta)


__all__ = ["DOC_TYPE", "REFERENCE", "accounts", "execute", "header"]

