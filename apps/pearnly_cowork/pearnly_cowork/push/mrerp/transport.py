"""MR.ERP 发票导入：登录 → 选账套 → 导入 5 步 → 逐行回读 report 判成败。

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
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from pearnly_core.session_lock import account_lock
from pearnly_cowork.connections.mrerp_session import MrErpSession, SessionError, lock_key
from pearnly_cowork.push.mrerp import routing
from pearnly_cowork.push.mrerp.account_valve import ACCOUNT_REVIEW_CODE, account_gate, tag_account_review
from pearnly_cowork.push.mrerp.models import (
    FailedRow,
    ImportResult,
    MRERPAuthError,
    MRERPBusinessError,
    MRERPTechnicalError,
    SuccessRow,
    bill_no_for,
)
from pearnly_cowork.push.mrerp.modules import MrErpModule, get_module
from pearnly_cowork.push.mrerp.preview_parse import TAGSTRIP_RE, parse_preview_form, preview_hints
from pearnly_cowork.push.mrerp.report_parse import parse_master_report
from pearnly_cowork.push.mrerp.report_parser import parse_import_report
from pearnly_cowork.push.mrerp.xlsx import generator as _gen

logger = logging.getLogger(__name__)


def _selmenu_param(m) -> str:
    """selmenu 表单参数 · 主数据导入(imparmas/impstkmas…)无 selmenu → 发空串,别发 'None'。"""
    return "" if m.selmenu is None else str(m.selmenu)


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
        """按每张票的方向 / 付款 / 货费分流到对应导入模块后推送（见 routing.route_and_upload）。"""
        if self._sess is None:
            raise RuntimeError("MrErpImport used outside `with` block")
        return routing.route_and_upload(self, histories, mappings, today=today)

    def upload_invoice_batch(self, histories: List[Dict[str, Any]], mappings: Dict[str, Any], *,
                             today: date) -> ImportResult:
        if not histories:
            return ImportResult(total=0)
        if self._sess is None:
            raise RuntimeError("MrErpImport used outside `with` block")
        m = self.module
        if not m.verified or m.selmenu is None:
            raise MRERPBusinessError(f"doc_type={m.doc_type} direct-write not ready")
        t0 = time.time()
        original = histories

        # ★匹配闸:票面买卖方须符合套账主体(own_tax_id 由调用方从套账解析后放 mappings)。
        # MR.ERP 导入不带卖方身份(默认=登录公司)→ 防"推错套账"只能靠这里的税号锚点。
        histories, mismatch_failed = self._filter_by_account_set(histories, mappings)
        if not histories:
            return self._result(original, [], mismatch_failed, t0, 0)

        # 科目安全阀(主动层):提供了该套账科目表且配置科目码不在表内 → 全挡下退回用户配置(不硬建)。
        review = account_gate(mappings)
        if review:
            reasons = [ACCOUNT_REVIEW_CODE, ", ".join(review)]
            fr = [self._fail_row(h, reasons) for h in histories]
            return self._result(original, [], mismatch_failed + fr, t0, 0)

        # 缺对手方主数据则自建并注入码(须在 preflight 前)· 销项建买方客户 / 采购建卖方供应商。
        if m.doc_type.startswith("sales"):
            from pearnly_cowork.push.mrerp.autocreate import provision_customers

            provision_customers(self, histories, mappings)
        elif m.sheet_kind == "purchase":  # purchase + purchase_expense 共用 impaptran 供应商自建
            from pearnly_cowork.push.mrerp.autocreate import provision_suppliers

            provision_suppliers(self, histories, mappings)

        valid, preflight_failed = self._preflight(histories, mappings)
        if not valid:
            return self._result(original, [], mismatch_failed + preflight_failed, t0, 0)

        # 销/采:明细行对不上已有商品则自建并注入码(在生成 xlsx 前)
        if m.doc_type.startswith("sales") or m.sheet_kind == "purchase":
            from pearnly_cowork.push.mrerp.autocreate import provision_products

            provision_products(self, valid, mappings)

        xlsx_bytes = _gen.generate_xlsx(valid, mappings, sheet_kind=m.sheet_kind, expense=m.expense)
        expected = [_gen.derive_mrerp_invoice_no(h) for h in valid]

        self._prepare(m)
        self._upload_xlsx(xlsx_bytes)
        form_fields, row_ids, idus_form = self._fetch_preview()
        if not row_ids:
            hints = preview_hints(self._last_preview_html or "")
            msg = "preview has no importable rows (xlsx rejected server-side)"
            if hints:
                msg += ": " + " / ".join(hints[:3])
            raise MRERPBusinessError(msg)
        self.import_sent = True
        raw = self._confirm_import(form_fields, row_ids)

        success_rows, failed_rows = self._classify(valid, expected, raw, idus_form)
        return self._result(original, success_rows, mismatch_failed + preflight_failed + failed_rows,
                            t0, len(xlsx_bytes))

    def _fail_row(self, h: Dict[str, Any], reasons: List[str]) -> FailedRow:
        return FailedRow(invoice_no=_gen.derive_mrerp_invoice_no(h), reasons=reasons, original=h)

    def _filter_by_account_set(self, histories: List[Dict[str, Any]], mappings: Dict[str, Any]
                               ) -> Tuple[List[Dict[str, Any]], List[FailedRow]]:
        """匹配闸:只挡能确认是别家的票(读不到税号→放行不误挡)· _own_tax_id 提供才启用(否则全放行)。"""
        own = mappings.get("_own_tax_id") if isinstance(mappings, dict) else None
        if not own:
            return histories, []
        expected = "sales" if self.module.doc_type.startswith("sales") else "purchase"
        kept: List[Dict[str, Any]] = []
        failed: List[FailedRow] = []
        for h in histories:
            if routing.confirmed_account_set_mismatch(h, h, own_tax_id=own, expected_direction=expected):
                failed.append(self._fail_row(h, [routing.ACCOUNT_SET_MISMATCH_CODE]))
            else:
                kept.append(h)
        return kept, failed

    # ---- 自建主数据 --------------------------------------------------

    def create_customers(self, customers: List[Dict[str, Any]], mappings: Dict[str, Any]) -> Dict[str, bool]:
        """自建客户(idempotent)· 导入 imparmas · 返回 {客户码: 成功}。已存在视为成功。"""
        return self._import_masters(get_module("master_customer"), customers, mappings, "A")

    def create_products(self, products: List[Dict[str, Any]], mappings: Dict[str, Any]) -> Dict[str, bool]:
        """自建商品(idempotent)· 导入 impstkmas · 返回 {商品码: 成功}。已存在视为成功。"""
        return self._import_masters(get_module("master_product"), products, mappings, "C")

    def create_suppliers(self, suppliers: List[Dict[str, Any]], mappings: Dict[str, Any]) -> Dict[str, bool]:
        """自建供应商(idempotent)· 导入 impapmas · 返回 {供应商码: 成功}。已存在视为成功。"""
        return self._import_masters(get_module("master_supplier"), suppliers, mappings, "A")

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
        # 空备注=新建成功;"มีอยู่ในระบบแล้ว"(该码已存在)=幂等重复,视为成功
        return {c: (not notes.get(c) or "มีอยู่ในระบบแล้ว" in notes.get(c, "")) for c in codes}

    # ---- 5 步 --------------------------------------------------------

    def _upload_xlsx(self, xlsx_bytes: bytes, module=None) -> None:
        m = module or self.module
        # ★必须先显式 GET 目标模块的 formupload:老 PHP 按「最后打开的 form」在服务器 session 存导入上下文;
        # 主档自建先跑会吃掉首次导航 → 后续导入带错上下文 → importpc 空消息假成功(2026-07-09 真机根因)。
        self._request("GET", f"{m.path}/formupload.php?idmenu={m.idmenu}",
                      headers={"Referer": f"{self.login_url}/login/mainmenu.php"})
        r = self._request(
            "POST", f"{m.path}/component/uploadexcel.php",
            files={"uploadfile": ("import.xlsx", xlsx_bytes,
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"idus": str(self._sess.idus), "selmenu": _selmenu_param(m)},
            headers={"Referer": f"{self.login_url}/{m.path}/formupload.php?idmenu={m.idmenu}",
                     "X-Requested-With": "XMLHttpRequest", "Origin": self.login_url},
        )
        body = (r.text or "").strip()
        if r.status_code >= 400:
            raise MRERPBusinessError(f"upload rejected {r.status_code}: {body[:200]}")
        # 成功 = 空 body;非空 = 错误描述(JS 会 alert)
        if body:
            raise MRERPBusinessError(f"upload rejected: {body[:300]}")

    def _fetch_preview(self, module=None) -> Tuple[List[Tuple[str, str]], List[str], Optional[str]]:
        m = module or self.module
        r = self._request(
            "POST", f"{m.path}/formrdpc.php",
            data={"idus": str(self._sess.idus), "selmenu": _selmenu_param(m)},
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

    # ---- 分类:回读 report 判真成败 ----------------------------------

    def _classify(self, valid: List[Dict[str, Any]], expected: List[str], raw: str, idus_form: Optional[str]
                  ) -> Tuple[List[SuccessRow], List[FailedRow]]:
        by_inv = {inv: h for inv, h in zip(expected, valid)}

        def _ok_row(i: str) -> SuccessRow:
            return SuccessRow(invoice_no=i, mrerp_bill_no=bill_no_for(self.module.doc_type, i),
                              original=by_inv.get(i, {}))

        def _fail(inv: str, reasons: List[str]) -> FailedRow:
            return FailedRow(invoice_no=inv, reasons=reasons, original=by_inv.get(inv, {}))

        if raw == "1":
            # importpc "1" = 全部提交 · 不出报告
            return [_ok_row(i) for i in expected], []
        if not raw.strip().isdigit():
            # 非数字体(alert/script/Error…)= 服务器异常:此时 report 备注列全空,逐行判会把整批误判成功
            # 且实际不落库(2026-07-09 真机 453 假绿三连)→ 全部诚实置失败。
            summary = TAGSTRIP_RE.sub("", raw).strip()[:120]
            reasons = ["ERR_MRERP_IMPORT_ERROR"] + ([summary] if summary else [])
            return [], [_fail(inv, reasons) for inv in expected]
        # 纯数字(如 "2")= 正常受理但有逐行结果 → 必拉 report 逐行判定,不臆断
        report = parse_import_report(self._fetch_report(idus_form))
        success = [_ok_row(i) for i in report.success]
        # 科目安全阀:report 报科目未匹配 → 归为 ERR_ACCOUNT_NEEDS_REVIEW(退回用户配置,不硬建)
        failed = [_fail(row.invoice_no, tag_account_review(list(row.reasons))) for row in report.failed]
        seen = {s.invoice_no for s in success} | {f.invoice_no for f in failed}
        failed.extend(_fail(inv, ["report did not mention this invoice"]) for inv in expected if inv not in seen)
        return success, failed

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
