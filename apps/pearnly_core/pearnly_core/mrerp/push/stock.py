"""一次 MR.ERP 库存入库 / 出库导入直写（纯网络，不碰数据库；多产品共用，现在只有 ERP 库存单用，2026-11-19）。

同收款导入的会话与 5 步（`transport.MrErpImport`：共用入口拿会话——同账号锁、被踢标记、接上缓存会话；formupload → uploadexcel →
formrdpc → importpc → report），模块 ``stock_in``（impstktranrec 356，回读列表 stktranrec）/ ``stock_out``（impstktraniss 357，
回读 stktraniss）。

- 表头 / 明细取值：分店、部门、งาน 照这份账套的主档与选过的（`push.defaults.AccountDefaults`，同发票、收款）；物料码、仓库码由调用方
  给（ERP 物料与仓库从 MR.ERP 主档整读来，记着 MR.ERP 编码）。数量按物料的标准单位（导入没有单位列）。
- 成本：入库照单价、金额写；出库不带价，MR.ERP 按物料的成本法（平均 / 先进先出）自己算，写入后从这一天起重算这件物料的库存卡
  （与在 MR.ERP 原生录入一样）。日记账由 MR.ERP 按账套「成本会计制度」决定（Perpetual 记、Periodic 不记）。
- 菜单：入库 / 出库导入页里这份账套选过的、或唯一一个已关联日记账的（`menus.resolve`，没关联的不用）；定不下来不导。
- 结论同收款（`receipt.execute` 的形状）：受理后在所选菜单的列表里按单号查到才算成功，记 MR.ERP 单号（菜单前缀 + 我方单号）；
  「单号已存在」也要查到；导入发出之后出错、受理了却查不到 = 结果不明（``unknown``，不当失败重推）。

载荷（产品冻结、纯 JSON）::

    {"kind": "in" | "out", "number", "date", "notes": [备注1, 备注2, 备注3],
     "lines": [{"code": MR.ERP 物料码, "store": MR.ERP 仓库码, "qty", "price"（只入库）}],
     "menu": 选过的菜单号, "defaults": {主档字段: 选过的代码}}
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict

from pearnly_core.session_lock import SessionLockUnavailable
from pearnly_core.mrerp import menus as mrerp_menus
from pearnly_core.mrerp.master_reader import MasterReader
from pearnly_core.mrerp.push import readback
from pearnly_core.mrerp.push.defaults import AccountDefaults, Missing, tag_default_changed
from pearnly_core.mrerp.push.duplicates import is_already_pushed_error
from pearnly_core.mrerp.push.models import MRERPAccountInUseError, MRERPAuthError, MRERPBusinessError, MRERPTechnicalError
from pearnly_core.mrerp.push.preview_parse import TAGSTRIP_RE, preview_hints
from pearnly_core.mrerp.push.report_parser import parse_import_report
from pearnly_core.mrerp.push.transport import READBACK_NOT_FOUND, READBACK_NOT_LINKED, MrErpImport
from pearnly_core.mrerp.push.xlsx import stock as sheet

logger = logging.getLogger(__name__)

DOC_TYPES = {sheet.IN: "stock_in", sheet.OUT: "stock_out"}
ZERO = Decimal("0.00")


def execute(entry: Dict[str, Any], target: Dict[str, Any], secrets: Dict[str, str], system_url: str) -> Dict[str, Any]:
    """→ ``{status: success | failed | unknown, reason, docnum, meta, menus, masters}``（同 `receipt.execute`）。永不抛。"""
    comidyear, _, seldb = str(target.get("account_set") or "").partition(":")
    importer = None
    try:
        doc_type = DOC_TYPES[entry.get("kind")]
        importer = MrErpImport(system_url=system_url, username=secrets.get("username") or "",
                               password=secrets.get("password") or "", comidyear=comidyear, seldb=seldb,
                               doc_type=doc_type)
        with importer:
            return _run(importer, entry)
    except (MRERPAuthError, MRERPBusinessError, MRERPTechnicalError, SessionLockUnavailable) as error:
        code = {MRERPAccountInUseError: "ERR_MRERP_ACCOUNT_IN_USE", MRERPAuthError: "ERR_AUTH",
                MRERPBusinessError: "ERR_BUSINESS", MRERPTechnicalError: "ERR_TECHNICAL"}.get(type(error), "ERR_SESSION_LOCK")
        text = str(error)
        return _exception(importer, text if text.startswith(code) else f"{code}: {text}")
    except Exception as error:  # noqa: BLE001 — 契约：任何异常都要落到单据上，不冒到队列
        logger.exception("MR.ERP stock push raised")
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


def filled(defaults: AccountDefaults, entry: Dict[str, Any]) -> Dict[str, Any]:
    """导入表要的整张单（分店 / 部门 / งาน 按主档与选过的定，定不下来抛 `Missing`）。"""
    head = {field: defaults.value(field)["code"] for field in ("branch", "dept", "job")}
    lines = [{"code": str(line.get("code") or "").strip(), "store": str(line.get("store") or "").strip(),
              "qty": line.get("qty"), "dept": head["dept"], "job": head["job"],
              **({"price": line.get("price")} if entry.get("kind") == sheet.IN else {})}
             for line in entry.get("lines") or []]
    return {"kind": entry.get("kind"), "number": str(entry.get("number") or "").strip(), "date": entry.get("date"),
            "branch": head["branch"], "notes": entry.get("notes") or [], "lines": lines}


COSTSTD_PAGE = "component/php/checkcoststd.php"


def standard_costs(importer: MrErpImport, reader: MasterReader, lines) -> None:
    """入库行没有单价的：照 MR.ERP 原生入库网页，选物料时带物料主档的标准成本（``coststd``，`checkcoststd.php` 按物料 id 回
    纯数字）；读不到按 0（原生单价初值 0.00、不拦）。只改没带价的行。"""
    missing = [line for line in lines if line.get("price") in (None, "")]
    if not missing:
        return
    ids = {str(item[1]).strip(): item[0] for item in (reader.listbox("stktranrec") or {}).get("txtcode[]") or []
           if isinstance(item, list) and len(item) >= 2}
    for line in missing:
        value = "0"
        idstk = ids.get(line["code"])
        if idstk is not None:
            response = importer._request("POST", COSTSTD_PAGE, data={"idstkmas": str(idstk)},
                                         headers={"X-Requested-With": "XMLHttpRequest", "Origin": importer.login_url,
                                                  "Referer": f"{importer.login_url}/stktranrec/allform.php"})
            text = str(getattr(response, "text", "") or "").strip().replace(",", "")
            try:
                value = str(Decimal(text)) if text and Decimal(text) >= 0 else "0"
            except Exception:  # noqa: BLE001 — 不是数字（被踢回登录页等）按 0
                value = "0"
        line["price"] = value


def _run(importer: MrErpImport, entry: Dict[str, Any]) -> Dict[str, Any]:
    module = importer.module
    importer._prepare(module)
    reader = MasterReader(importer._request, importer.login_url)
    defaults = AccountDefaults(reader, entry.get("defaults") or {})
    importer.account_defaults = defaults
    reader.head_branch()  # 只有网页，会改写会话里的当前菜单：在打开导入页之前读（同发票导入）
    try:
        values = filled(defaults, entry)
    except Missing as missing:
        return _outcome(importer, "failed", missing.reason)
    if values["kind"] == sheet.IN:
        standard_costs(importer, reader, values["lines"])
    problem = sheet.check(values)
    if problem:
        return _outcome(importer, "failed", problem)

    menu, reason = mrerp_menus.resolve(importer._read_menus(module), entry.get("menu"))
    if reason:
        return _outcome(importer, "failed", reason)
    selmenu = menu["idmenu"]
    importer._menu_label = str(menu.get("label") or "")
    number = values["number"]
    blob = sheet.generate(values)

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
            reasons = list(row.reasons) if row else ["report did not mention this stock entry"]
            if not row or not is_already_pushed_error("; ".join(reasons)):
                return _outcome(importer, "failed", "; ".join(tag_default_changed(reasons)))
            already = True
    found, listing = importer._read_back(selmenu, [number])
    listed = found.get(number)
    meta: Dict[str, Any] = {"doc_type": module.doc_type, "mrerp_menu": importer._menu_label}
    if listed is None:
        return _outcome(importer, "unknown", READBACK_NOT_LINKED if listing == "not_linked" else READBACK_NOT_FOUND,
                        meta=meta)
    if already:
        meta["already_pushed"] = True
    if entry.get("kind") == sheet.IN:
        total = sum((sheet.amount(line["qty"], line["price"]) for line in values["lines"]), ZERO)
        meta["verify"] = readback.amount_check(listed.amount, str(total))  # 列表金额 = 各行金额之和（只记不拦）
    return _outcome(importer, "success", docnum=listed.docno, meta=meta)


__all__ = ["COSTSTD_PAGE", "DOC_TYPES", "execute", "filled", "standard_costs"]
