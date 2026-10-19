"""MR.ERP 发票导入：登录 → 选账套 → 核对菜单 → 导入 5 步 → 逐行回读 report → 在所选菜单列表里回读才算成功。

来源：旧线上 ec2e5c957e6b · `services/erp/mrerp_http/adapter.py`（`MrErpHttpAdapter`）。
导入 5 步：formupload（先 GET 建服务器侧导入上下文）→ component/uploadexcel → formrdpc（预览）→
component/importpc（确认）→ component/report（importpc 回纯数字时逐行判；回 "1" 全部成功；
非数字体 = 服务器异常，整批诚实置失败）。同一 MR.ERP 账号串行（旧 `mrerp_session_lock`）。

与旧版的差异：
  · 会话用 `connections.mrerp_session`（与只读目录采集共用；公网地址校验、不许跳出原站点）；
  · 会话锁用 `pearnly_core.session_lock.account_lock`（拿不到锁就报错，不放行）；
  · `import_sent`：importpc 一旦发出就置真——之后任何异常都是**结果不明**（可能已落库），
    调用方不得当失败重推（旧 `confirmed_push_reservation.mark_reserved_push_unknown` 同一判据）；
  · 「今天」由调用方注入（分流判定用）；失败只留原因码，不带四语展示文案。

有意变化（2026-09-28，MR.ERP 推送假成功，旧线上同样有此缺陷）：
  · selmenu 不写死：每次推送读这次打开的导入页选项，只用已关联日记账的；这份账套选过的以选过的为准
    （`mappings['_mrerp_menus']`，准备推送时冻结），失效就这张票停下请人重选，不自动换
    （规则见 `targets/mrerp_menus.py`）。上传前紧挨着那次必需的 formupload GET 再核一次；
  · importpc 回 "1" 或 report 行空备注都**不**直接算成功：先 GET `{列表模块}/allview.php?idmenu=所选菜单`
    （写会话、看是否提示未关联日记账），再 POST `component/showdata.php` 按我方单号查，列表里以我方单号结尾的
    那一行才算成功，记下 MR.ERP 实际单号（菜单前缀 + 我方单号）；查不到 = 结果不明（`unknown`，要人工核对）；
  · 「单号已存在」同样要在所选菜单里查到才算已推过（MR.ERP 查重只在同一菜单 + 带前缀单号内）。
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from pearnly_core.session_lock import account_lock
from pearnly_cowork.contracts.push import explicit_direction
from pearnly_cowork.connections.mrerp_session import MrErpSession, SessionError, lock_key
from pearnly_cowork.connections.mrerp_master_reader import MasterReader
from pearnly_cowork.push.mrerp import readback, routing
from pearnly_cowork.push.mrerp.defaults import AccountDefaults, Missing, tag_default_changed
from pearnly_cowork.push.mrerp.account_valve import ACCOUNT_REVIEW_CODE, account_gate, tag_account_review
from pearnly_cowork.push.mrerp.models import (
    FailedRow,
    ImportResult,
    MRERPAuthError,
    MRERPBusinessError,
    MRERPTechnicalError,
    SuccessRow,
)
from pearnly_cowork.push.mrerp.modules import MrErpModule, get_module
from pearnly_cowork.push.mrerp.preview_parse import TAGSTRIP_RE, parse_preview_form, preview_hints
from pearnly_cowork.push.mrerp.report_parse import parse_master_report
from pearnly_cowork.push.mrerp.report_parser import parse_import_report
from pearnly_cowork.push.mrerp.xlsx import generator as _gen
from pearnly_cowork.push.retry import is_already_pushed_error
from pearnly_cowork.targets import mrerp_menus

logger = logging.getLogger(__name__)


# 主档导入报「这个码已存在」（imparmas / impapmas / impstkmas importpc.php 的 รหัสลูกค้า / รหัสผู้จำหน่าย /
# รหัสสินค้า[เพื่อซื้อ|เพื่อขาย]ดังกล่าวมีอยู่ในระบบแล้ว）
_CODE_EXISTS = re.compile(r"รหัส(?:ลูกค้า|ผู้จำหน่าย|สินค้า(?:เพื่อซื้อ|เพื่อขาย)?)ดังกล่าวมีอยู่ในระบบแล้ว")

READBACK_NOT_FOUND = "ERR_MRERP_READBACK_NOT_FOUND"
READBACK_NOT_LINKED = "ERR_MRERP_READBACK_NOT_LINKED"


class MrErpImport:
    def __init__(self, *, system_url: str, username: str, password: str, comidyear: str, seldb: str,
                 doc_type: str = "sales_credit", lock_timeout: float = 180.0):
        if not system_url or not username or not password:
            raise MRERPAuthError("system url, username and password are required")
        self.login_url = system_url.rstrip("/")
        self._username, self._password = username, password
        self.comidyear, self.seldb = str(comidyear), str(seldb)
        self.module: MrErpModule = get_module(doc_type)
        self._lock_timeout = lock_timeout
        self._sess: Optional[MrErpSession] = None
        self._lock_cm = None
        self._last_preview_html = ""
        self.import_sent = False
        # 这次会话读到的导入页选项（导入页 → 选项 | None），调用方存下来给选择入口用。
        self.observed_menus: Dict[str, Optional[List[Dict[str, Any]]]] = {}
        # 这家公司的导入值（推送时按主档算，见 push/mrerp/defaults.py）；自建主档要的值定不下来时记下原因码。
        self.account_defaults: Optional[AccountDefaults] = None
        self.default_reason: Optional[str] = None

    # ---- lifecycle ---------------------------------------------------

    def __enter__(self) -> "MrErpImport":
        self._lock_cm = account_lock(lock_key(self.login_url, self._username), timeout_sec=self._lock_timeout)
        self._lock_cm.__enter__()
        try:
            self._sess = MrErpSession(self.login_url, self._username, self._password)
        except SessionError as error:
            self.__exit__(type(error), error, None)
            raise self._mapped(error) from None
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._sess is not None:
            try:
                self._sess.close()
            except Exception:  # noqa: BLE001 — 关连接失败不影响结果
                pass
        self._sess = None
        if self._lock_cm is not None:
            try:
                self._lock_cm.__exit__(exc_type, exc, tb)
            finally:
                self._lock_cm = None

    @staticmethod
    def _mapped(error: SessionError):
        return MRERPAuthError(str(error)) if error.code == "ERR_AUTH" else MRERPTechnicalError(str(error))

    def _prepare(self, module) -> None:
        """登录 + 选账套（幂等）；idus 从这次要导入的模块页抓（旧版 prepare(formupload) 同法）。"""
        try:
            self._sess.prepare(self.comidyear, self.seldb, f"{module.path}/formupload.php?idmenu={module.idmenu}")
        except SessionError as error:
            raise self._mapped(error) from None

    def _request(self, method: str, path: str, **kwargs):
        try:
            return self._sess.request(method, path, **kwargs)
        except SessionError as error:
            raise self._mapped(error) from None

    # ---- 主入口 -------------------------------------------------------

    def upload_routed_batch(self, histories: List[Dict[str, Any]], mappings: Dict[str, Any], *,
                            today: date) -> ImportResult:
        """按每张票的方向 / 付款 / 货费分流到对应导入模块后推送（见 routing.route_and_upload）。

        有票没有明确方向时，先读这家公司在 MR.ERP 登记的税号放进 ``mappings['_account_tax_id']``：工作空间税号判不出
        方向时按它判，匹配闸也认它（事务所替客户记账，2026-10-19，同 Express 按账套税号判）。读不到 / 不是有效税号就不放，
        照旧只按工作空间税号。都有明确方向时不读（方向与匹配闸都用不到）。"""
        if self._sess is None:
            raise RuntimeError("MrErpImport used outside `with` block")
        if isinstance(mappings, dict) and any(not explicit_direction(h, h) for h in histories):
            tax = routing.account_tax(self._reader(mappings).company_tax())
            if tax:
                mappings["_account_tax_id"] = tax
        return routing.route_and_upload(self, histories, mappings, today=today)

    def _reader(self, mappings: Dict[str, Any]) -> MasterReader:
        """登录选好账套后的主档读取（同一次会话共用一次，`upload_invoice_batch` 接着用）。"""
        self._prepare(self.module)
        if self.account_defaults is None:
            self.account_defaults = AccountDefaults(MasterReader(self._request, self.login_url),
                                                    (mappings or {}).get("_mrerp_defaults"))
        return self.account_defaults.reader

    def upload_invoice_batch(self, histories: List[Dict[str, Any]], mappings: Dict[str, Any], *,
                             today: date) -> ImportResult:
        if not histories:
            return ImportResult(total=0)
        if self._sess is None:
            raise RuntimeError("MrErpImport used outside `with` block")
        m = self.module
        if not m.verified or m.doc_type not in mrerp_menus.PAGE_OF:
            raise MRERPBusinessError(f"doc_type={m.doc_type} direct-write not ready")
        t0 = time.time()
        original = histories

        # ★匹配闸:票面买卖方须符合套账主体(own_tax_id 由调用方从套账解析后放 mappings)。
        # MR.ERP 导入不带卖方身份(默认=登录公司)→ 防"推错套账"只能靠这里的税号锚点。
        histories, mismatch_failed = self._filter_by_account_set(histories, mappings)
        if not histories:
            return self._result(original, [], mismatch_failed, t0, 0)

        # 这家公司的导入值（分店 / 部门 / 员工 / 收款科目等）：按这次读到的主档、这份账套选过的值与规则算，定不下来就
        # 不推、不建主档（规则见 targets/mrerp_masters.py）。参考科目、称谓、总店只有网页，会改写会话里的当前菜单，
        # 所以在打开导入页之前读。
        self._prepare(m)
        self.default_reason = None
        self._reader(mappings)  # 同一次会话里几组单据（赊销 / 现金）共用一次读取
        try:
            self.account_defaults.reader.reference()
            self.account_defaults.reader.prefixes()
            self.account_defaults.reader.head_branch()
            self.account_defaults.header(mappings, histories, sales=m.doc_type.startswith("sales"),
                                         cash_sale=m.doc_type == "sales_cash")
        except Missing as missing:
            return self._result(original, [], mismatch_failed + [self._fail_row(h, [missing.reason])
                                                                 for h in histories], t0, 0)

        # 科目安全阀(主动层):这家公司的科目表(上面读到的)里没有要用的科目码 → 全挡下退回用户配置(不硬建)。
        review = account_gate(mappings)
        if review:
            reasons = [ACCOUNT_REVIEW_CODE, ", ".join(review)]
            fr = [self._fail_row(h, reasons) for h in histories]
            return self._result(original, [], mismatch_failed + fr, t0, 0)

        # 菜单：这次打开的导入页里，这份账套选过的（或唯一一个）已关联日记账的菜单；定不下来就不推、不建主档。
        chosen = ((mappings or {}).get("_mrerp_menus") or {}).get(m.doc_type)
        menu, reason = mrerp_menus.resolve(self._read_menus(m), chosen)
        if reason:
            return self._result(original, [], mismatch_failed + [self._fail_row(h, [reason]) for h in histories],
                                t0, 0)
        selmenu = menu["idmenu"]
        self._menu_label = str(menu.get("label") or "")

        # 缺对手方主数据则自建并注入码(须在 preflight 前)· 销项建买方客户 / 采购建卖方供应商。
        if m.doc_type.startswith("sales"):
            from pearnly_cowork.push.mrerp.autocreate import provision_customers

            provision_customers(self, histories, mappings)
        elif m.sheet_kind == "purchase":  # purchase + purchase_expense 共用 impaptran 供应商自建
            from pearnly_cowork.push.mrerp.autocreate import provision_suppliers

            provision_suppliers(self, histories, mappings)

        if self.default_reason:  # 自建主档要的值定不下来（要人选 / 选过的没了）：这批不推
            return self._result(original, [], mismatch_failed + [self._fail_row(h, [self.default_reason])
                                                                 for h in histories], t0, 0)

        valid, preflight_failed = self._preflight(histories, mappings)
        if not valid:
            return self._result(original, [], mismatch_failed + preflight_failed, t0, 0)

        # 销/采:明细行对不上已有商品则自建并注入码(在生成 xlsx 前)
        if m.doc_type.startswith("sales") or m.sheet_kind == "purchase":
            from pearnly_cowork.push.mrerp.autocreate import provision_products

            provision_products(self, valid, mappings)
            if self.default_reason:
                return self._result(original, [], mismatch_failed + preflight_failed
                                    + [self._fail_row(h, [self.default_reason]) for h in valid], t0, 0)

        xlsx_bytes = _gen.generate_xlsx(valid, mappings, sheet_kind=m.sheet_kind, expense=m.expense)
        expected = [_gen.derive_mrerp_invoice_no(h) for h in valid]

        # 主档自建换掉了会话里的导入上下文：上传前必需的那次 formupload GET 顺便再核一次菜单（期间被改就停）。
        _, reason = mrerp_menus.resolve(self._read_menus(m), selmenu)
        if reason:
            return self._result(original, [], mismatch_failed + preflight_failed
                                + [self._fail_row(h, [reason]) for h in valid], t0, 0)
        self._upload_xlsx(xlsx_bytes, selmenu=selmenu, opened=True)
        form_fields, row_ids, idus_form = self._fetch_preview(selmenu=selmenu)
        if not row_ids:
            hints = preview_hints(self._last_preview_html or "")
            msg = "preview has no importable rows (xlsx rejected server-side)"
            if hints:
                msg += ": " + " / ".join(hints[:3])
            raise MRERPBusinessError(msg)
        self.import_sent = True
        raw = self._confirm_import(form_fields, row_ids)

        success_rows, failed_rows, unknown_rows = self._classify(valid, expected, raw, idus_form, selmenu)
        res = self._result(original, success_rows, mismatch_failed + preflight_failed + failed_rows,
                           t0, len(xlsx_bytes))
        res.unknown = unknown_rows
        return res

    def _fail_row(self, h: Dict[str, Any], reasons: List[str]) -> FailedRow:
        return FailedRow(invoice_no=_gen.derive_mrerp_invoice_no(h), reasons=reasons, original=h)

    def _filter_by_account_set(self, histories: List[Dict[str, Any]], mappings: Dict[str, Any]
                               ) -> Tuple[List[Dict[str, Any]], List[FailedRow]]:
        """匹配闸:只挡能确认是别家的票(读不到税号→放行不误挡)· _own_tax_id(工作空间税号)或
        _account_tax_id(这家公司在 MR.ERP 登记的税号)有一个才启用(否则全放行)。"""
        own = mappings.get("_own_tax_id") if isinstance(mappings, dict) else None
        account = mappings.get("_account_tax_id") if isinstance(mappings, dict) else None
        if not own and not account:
            return histories, []
        expected = "sales" if self.module.doc_type.startswith("sales") else "purchase"
        kept: List[Dict[str, Any]] = []
        failed: List[FailedRow] = []
        for h in histories:
            if routing.confirmed_account_set_mismatch(h, h, own_tax_id=own, expected_direction=expected,
                                                      account_tax_id=account):
                failed.append(self._fail_row(h, [routing.ACCOUNT_SET_MISMATCH_CODE]))
            else:
                kept.append(h)
        return kept, failed

    # ---- 自建主数据 --------------------------------------------------

    def create_customers(self, customers: List[Dict[str, Any]], mappings: Dict[str, Any]) -> Dict[str, bool]:
        """自建客户(idempotent)· 导入 imparmas · 返回 {客户码: 成功}。已存在视为成功。
        类型 / 应收科目 / 销售员等取这家公司的主档（push/mrerp/defaults.py），定不下来就不建、记下原因码。"""
        try:
            records, shared = self._defaults().customers(customers)
        except Missing as missing:
            return self._not_created(customers, missing)
        return self._import_masters(get_module("master_customer"), records, {**mappings, **shared}, "A")

    def create_products(self, products: List[Dict[str, Any]], mappings: Dict[str, Any]) -> Dict[str, bool]:
        """自建商品(idempotent)· 导入 impstkmas · 返回 {商品码: 成功}。已存在视为成功。
        分类 / 单位 / 科目取这家公司的主档与参考科目（费用物料取这份账套选的费用科目）。"""
        try:
            values = self._defaults().products(expense=bool(getattr(self.module, "expense", False)))
        except Missing as missing:
            return self._not_created(products, missing)
        return self._import_masters(get_module("master_product"), products, {**mappings, **values}, "C")

    def create_suppliers(self, suppliers: List[Dict[str, Any]], mappings: Dict[str, Any]) -> Dict[str, bool]:
        """自建供应商(idempotent)· 导入 impapmas · 返回 {供应商码: 成功}。已存在视为成功。"""
        try:
            records, shared = self._defaults().suppliers(suppliers)
        except Missing as missing:
            return self._not_created(suppliers, missing)
        return self._import_masters(get_module("master_supplier"), records, {**mappings, **shared}, "A")

    def _defaults(self) -> AccountDefaults:
        if self.account_defaults is None:  # 只建主档、没走 upload_invoice_batch 的调用（测试 / 工具）
            self._prepare(self.module)
            self.account_defaults = AccountDefaults(MasterReader(self._request, self.login_url), {})
        return self.account_defaults

    def _not_created(self, records: List[Dict[str, Any]], missing: "Missing") -> Dict[str, bool]:
        self.default_reason = self.default_reason or missing.reason
        return {str(r.get("code") or ""): False for r in records if r.get("code")}

    def _import_masters(self, module, records: List[Dict[str, Any]], mappings: Dict[str, Any],
                        code_col: str) -> Dict[str, bool]:
        """自建主数据通用流:导入 → raw=1 全成功;否则读 report 判逐行(空备注/已存在=成功)。"""
        codes = [str(r.get("code") or "") for r in records if r.get("code")]
        if not codes:
            return {}
        if self._sess is None:
            raise RuntimeError("MrErpImport used outside `with` block")
        xlsx = _gen.generate_xlsx(records, mappings, sheet_kind=module.sheet_kind)
        self._prepare(module)
        self._upload_xlsx(xlsx, module=module)
        form_fields, row_ids, idus_form = self._fetch_preview(module=module)
        if not row_ids:
            return {c: False for c in codes}
        raw = self._confirm_import(form_fields, row_ids, module=module)
        if raw == "1":
            return {c: True for c in codes}
        notes = parse_master_report(self._fetch_report(idus_form, module=module), code_col=code_col)
        # 空备注 = 新建成功；「รหัส…ดังกล่าวมีอยู่ในระบบแล้ว」（这个**码**已存在）= 幂等重复，视为成功。
        # 只有「ชื่อสินค้า…มีอยู่ในระบบแล้ว」（同名的另一个码已存在）不算：码并没有建出来（2026-10-08 按 MR.ERP 源码修，
        # 旧线上只看「มีอยู่ในระบบแล้ว」，把同名不同码当成已建好，后面的单据才因码不存在失败）。
        return {c: (not notes.get(c) or bool(_CODE_EXISTS.search(notes.get(c, "")))) for c in codes}

    # ---- 5 步 --------------------------------------------------------

    def _open_form(self, module=None) -> str:
        """GET 目标模块的 formupload（导入页），返回页面。"""
        m = module or self.module
        r = self._request("GET", f"{m.path}/formupload.php?idmenu={m.idmenu}",
                          headers={"Referer": f"{self.login_url}/login/mainmenu.php"})
        return r.text or ""

    def _read_menus(self, module) -> Optional[List[Dict[str, Any]]]:
        """打开导入页并读出菜单选项；页上没有菜单下拉返回 None。读到的记进 observed_menus（读不到的不记，
        免得一次掉线把选择入口里已知的选项冲掉）。"""
        options = mrerp_menus.parse_options(self._open_form(module))
        if options is not None:
            self.observed_menus[module.path] = options
        return options

    def _upload_xlsx(self, xlsx_bytes: bytes, module=None, *, selmenu: str = "", opened: bool = False) -> None:
        m = module or self.module
        # ★必须先显式 GET 目标模块的 formupload:老 PHP 按「最后打开的 form」在服务器 session 存导入上下文;
        # 主档自建先跑会吃掉首次导航 → 后续导入带错上下文 → importpc 空消息假成功(2026-07-09 真机根因)。
        # opened=True:调用方刚为核对菜单 GET 过,中间没有别的导入请求。
        if not opened:
            self._open_form(m)
        r = self._request(
            "POST", f"{m.path}/component/uploadexcel.php",
            files={"uploadfile": ("import.xlsx", xlsx_bytes,
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"idus": str(self._sess.idus), "selmenu": selmenu},
            headers={"Referer": f"{self.login_url}/{m.path}/formupload.php?idmenu={m.idmenu}",
                     "X-Requested-With": "XMLHttpRequest", "Origin": self.login_url},
        )
        body = (r.text or "").strip()
        if r.status_code >= 400:
            raise MRERPBusinessError(f"upload rejected {r.status_code}: {body[:200]}")
        # 成功 = 空 body;非空 = 错误描述(JS 会 alert)
        if body:
            raise MRERPBusinessError(f"upload rejected: {body[:300]}")

    def _fetch_preview(self, module=None, *, selmenu: str = "") -> Tuple[List[Tuple[str, str]], List[str], Optional[str]]:
        # 主数据导入(imparmas/impstkmas…)没有菜单:selmenu 发空串。
        m = module or self.module
        r = self._request(
            "POST", f"{m.path}/formrdpc.php",
            data={"idus": str(self._sess.idus), "selmenu": selmenu},
            headers={"Referer": f"{self.login_url}/{m.path}/formupload.php?idmenu={m.idmenu}",
                     "Content-Type": "application/x-www-form-urlencoded", "Origin": self.login_url},
        )
        self._last_preview_html = r.text or ""
        if r.status_code >= 400:
            raise MRERPBusinessError(f"preview failed {r.status_code}")
        return parse_preview_form(self._last_preview_html)

    def _confirm_import(self, form_fields: List[Tuple[str, str]], row_ids: List[str], module=None) -> str:
        """按浏览器 FormData(#frmimport1) 提交整表(全 hidden + 勾选 cbimport)。"""
        m = module or self.module
        files: List[Tuple[str, Tuple[Optional[str], str]]] = [(name, (None, value)) for name, value in form_fields]
        have = {n for n, _ in form_fields}
        if "cballfrmimport1" not in have:
            files.append(("cballfrmimport1", (None, "on")))
        for rid in row_ids:
            key = f"cbimport[{rid}]"
            if key not in have:
                files.append((key, (None, str(rid))))
        r = self._request(
            "POST", f"{m.path}/component/importpc.php", files=files,
            headers={"Referer": f"{self.login_url}/{m.path}/formrdpc.php",
                     "X-Requested-With": "XMLHttpRequest", "Origin": self.login_url},
        )
        raw = (r.text or "").strip()
        if r.status_code >= 400:
            raise MRERPBusinessError(f"importpc rejected {r.status_code}: {raw[:200]}")
        if not raw:
            raise MRERPBusinessError("importpc returned empty body (unknown state)")
        return raw

    def _fetch_report(self, idus_form: Optional[str], module=None) -> bytes:
        m = module or self.module
        r = self._request("POST", f"{m.path}/component/report.php",
                          data={"numform": "1", "idus": str(idus_form or self._sess.idus)},
                          headers={"Referer": f"{self.login_url}/{m.path}/formrdpc.php"})
        if r.status_code >= 400 or not r.content:
            raise MRERPBusinessError(f"report.php failed {r.status_code} ({len(r.content)}B)")
        return r.content

    # ---- 分类:report 判受理 → 所选菜单列表回读判落库 ------------------------

    def _classify(self, valid: List[Dict[str, Any]], expected: List[str], raw: str, idus_form: Optional[str],
                  selmenu: str) -> Tuple[List[SuccessRow], List[FailedRow], List[FailedRow]]:
        """→ (成功, 失败, 结果不明)。成功只来自回读查到；受理了却查不到的是结果不明（可能已落库）。"""
        by_inv = {inv: h for inv, h in zip(expected, valid)}

        def _fail(inv: str, reasons: List[str]) -> FailedRow:
            return FailedRow(invoice_no=inv, reasons=reasons, original=by_inv.get(inv, {}))

        if raw == "1":
            # importpc "1" = 全部受理 · 不出报告;落没落进所选菜单要回读
            accepted, failed, already = list(expected), [], set()
        elif not raw.strip().isdigit():
            # 非数字体(alert/script/Error…)= 服务器异常:此时 report 备注列全空,逐行判会把整批误判成功
            # 且实际不落库(2026-07-09 真机 453 假绿三连)→ 全部诚实置失败。
            summary = TAGSTRIP_RE.sub("", raw).strip()[:120]
            reasons = ["ERR_MRERP_IMPORT_ERROR"] + ([summary] if summary else [])
            return [], [_fail(inv, reasons) for inv in expected], []
        else:
            # 纯数字(如 "2")= 正常受理但有逐行结果 → 必拉 report 逐行判定,不臆断
            report = parse_import_report(self._fetch_report(idus_form))
            accepted = list(report.success)
            already = {row.invoice_no for row in report.failed if is_already_pushed_error("; ".join(row.reasons))}
            accepted += [inv for inv in expected if inv in already and inv not in accepted]
            # 科目安全阀:report 报科目未匹配 → 归为 ERR_ACCOUNT_NEEDS_REVIEW(退回用户配置,不硬建)
            failed = [_fail(row.invoice_no, tag_default_changed(tag_account_review(list(row.reasons))))
                      for row in report.failed if row.invoice_no not in already]
            seen = set(accepted) | {f.invoice_no for f in failed}
            failed.extend(_fail(inv, ["report did not mention this invoice"]) for inv in expected if inv not in seen)

        found, problem = self._read_back(selmenu, accepted)
        success: List[SuccessRow] = []
        unknown: List[FailedRow] = []
        for inv in accepted:
            row = found.get(inv)
            if row is None:
                unknown.append(_fail(inv, [READBACK_NOT_LINKED if problem == "not_linked" else READBACK_NOT_FOUND]))
            else:
                success.append(SuccessRow(invoice_no=inv, mrerp_bill_no=row.docno, original=by_inv.get(inv, {}),
                                          already_pushed=inv in already, doc_type=self.module.doc_type,
                                          menu=getattr(self, "_menu_label", "")))
        return success, failed, unknown

    def _read_back(self, selmenu: str, invoice_nos: List[str]) -> Tuple[Dict[str, readback.ListedRow], Optional[str]]:
        """在所选菜单的列表里逐张按单号查 → ({我方单号: 列表行}, 列表页问题)。列表页提示未关联日记账就不查。"""
        if not invoice_nos:
            return {}, None
        listing = self.module.listing_module
        page = self._request("GET", f"{listing}/allview.php?idmenu={selmenu}",
                             headers={"Referer": f"{self.login_url}/login/mainmenu.php"})
        problem = readback.listing_problem(page.text or "")
        if problem:
            logger.warning("[mrerp-readback] menu=%s listing unusable: %s", selmenu, problem)
            return {}, problem
        found: Dict[str, readback.ListedRow] = {}
        for inv in invoice_nos:
            r = self._request(
                "POST", f"{listing}/component/showdata.php",
                data={"showdata_numrows": str(readback.PAGE_ROWS), "showdata_pages": "1", "searchdataval": inv},
                headers={"Referer": f"{self.login_url}/{listing}/allview.php", "X-Requested-With": "XMLHttpRequest",
                         "Origin": self.login_url})
            row = readback.find(readback.parse_rows(r.text or ""), inv) if r.status_code < 400 else None
            if row is not None:
                found[inv] = row
        logger.info("[mrerp-readback] menu=%s found=%d/%d", selmenu, len(found), len(invoice_nos))
        return found, None

    # ---- helpers -----------------------------------------------------

    def _preflight(self, histories: List[Dict[str, Any]], mappings: Dict[str, Any]
                   ) -> Tuple[List[Dict[str, Any]], List[FailedRow]]:
        valid: List[Dict[str, Any]] = []
        failed: List[FailedRow] = []
        # 校验器按模块细分(sheet_kind 只管模板族):费用档(453)不跑 vat_rate_anomaly
        # ——นอกระบบ 不抵扣,票面税率异常无会计后果(2026-07-09 拍板)。
        if self.module.expense:
            from pearnly_cowork.push.mrerp.xlsx.purchase import validate_expense_history as _validate
        elif self.module.sheet_kind == "purchase":
            from pearnly_cowork.push.mrerp.xlsx.purchase import validate_purchase_history as _validate
        else:
            _validate = _gen.validate_history_for_sales_credit
        for h in histories:
            ok, err_code, _warnings = _validate(h, mappings)
            if ok:
                valid.append(h)
            else:
                failed.append(self._fail_row(h, [err_code or "ERR_UNKNOWN_PREFLIGHT"]))
        return valid, failed

    def _result(self, histories: List[Dict[str, Any]], success: List[SuccessRow], failed: List[FailedRow],
                t0: float, xlsx_size: int) -> ImportResult:
        res = ImportResult(total=len(histories))
        res.success = success
        res.failed = failed
        res.elapsed_ms = int((time.time() - t0) * 1000)
        res.xlsx_size_bytes = xlsx_size
        return res
