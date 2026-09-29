"""Bank reconciliation task: the controller, its file entry and its model seam.

The task store itself lives in the reconciliation domain. Two things cannot live
there and therefore live here, in the app's module (orchestration) layer:

1. reading the uploaded original — the file store belongs to the files domain and
   the reconciliation domain may not depend on it;
2. the model path — the OCR document tier belongs to the ocr domain, and the two
   domains do not depend on each other either.

Both are compositions, which is exactly what this layer is for. The model reading
itself runs in a durable OCR Work (``recon_center.document_ocr``), injected by the
reconciliation job: scans and photos are rendered and read page by page there, and
every provider call sits inside the Work the provider layer requires. A file no
reader can decode is refused with a clear error instead of being stored as an
empty task.
"""
from datetime import date

import frappe

from pearnly_cowork.files import service as files_service
from pearnly_cowork.ocr.engine import document_tier
from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.providers.client import ProviderFailure
from pearnly_cowork.reconciliation import bank_parse, bank_recon_access, bank_recon_service
from pearnly_cowork.reconciliation.bank_recon_documents import BankReconciliationDocument


class CoworkBankReconciliation(BankReconciliationDocument):
    pass


def document_model_rows(gl_account_code="", *, model_call=None, model="", read=None):
	"""Build the intake's model seam on the OCR document tier.

	The returned callable takes ``(document_type, file_bytes, filename)`` and gives
	back the same rows shape the deterministic readers give, so the intake can
	compare the two readings and keep the more trustworthy one.

	``read`` is the reconciliation job's durable reader (``recon_center.document_ocr``):
	each file becomes one OCR Work, so scans and photos are rendered, read page by page
	and billed there, and every provider call runs inside the Work the provider layer
	requires (2026-09-29: called directly from the job, a file that needed the model
	failed the whole reconciliation with ``ocr.durable_work_required``). ``model_call``
	is the injected text-only seam the offline tests use. Without either (a plain web
	request), there is no model path and the deterministic readers' answer stands.
	"""
	def rows(document_type, file_bytes, filename):
		if not document_tier.admitted(document_type):
			return None
		if read is not None:
			legacy = read(document_type, file_bytes, filename)
		elif model_call is not None:
			pages = document_tier.text_pages(file_bytes)
			if not pages or not any(page.strip() for page in pages):
				return None
			try:
				_document, legacy, _warnings = document_tier.read_document(document_type,
					"\n".join(pages), model_call, model=model)
			except (ContentError, ProviderFailure) as error:
				frappe.log_error(title="cowork document tier", message=f"{type(error).__name__}: {error}")
				return None
		else:
			return None
		if not legacy:
			return None
		if str(legacy.get("engine") or "").startswith("enterprise-"):
			return enterprise_rows(legacy, document_type, filename, gl_account_code)
		if document_type == "bank_statement":
			return bank_parse.statement_from_pipeline(legacy, filename)
		return bank_parse.gl_from_pipeline(legacy, gl_account_code)

	return rows


def _date(value):
	try:
		return date.fromisoformat(value)
	except (TypeError, ValueError):
		return None


def _entry_date(entry):
	"""读出的一行的日期：以印出的原文为准。2026-09-29 压测：存折 / BAY 账单印两位公历年「26」，3.8 读成佛历
	2525 / 2526（→ 1982 / 1983），490 行里 132 行干脆没给日期，原文「23/03/26」却每行都在。原文按「两位年不晚于
	明年是公历，否则佛历」读；没有原文才用模型的日期，早于 1990 的按两位公历年纠正（+43 年）。"""
	from pearnly_cowork.ocr.enterprise.header_bank import parse_date

	printed = parse_date(entry.get("transaction_date_raw") or "", date.today().year)
	if printed:
		return date.fromisoformat(printed)
	value = _date(entry.get("transaction_date"))
	if value and value.year < 1990 and value.year + 43 <= date.today().year + 1:
		try:
			return value.replace(year=value.year + 43)
		except ValueError:
			return value.replace(year=value.year + 43, day=28)
	return value


def _same_month_years(rows):
	"""同一份文件里，月份与多数行相同、年份却不同（相差 6 年内）的行，年份读错了：改成多数行的年份。

	2026-09-29 压测 B01（KBank 2026-05 账单照片）：印的「26」被读成「20」「25」，29 行落到 2020 / 2025 年 5 月。
	跨月的存折不受影响（只改与多数行同月的行）。
	"""
	from collections import Counter

	months = Counter((row.date.year, row.date.month) for row in rows if row.date)
	if not months:
		return
	(year, month), _count = months.most_common(1)[0]
	for row in rows:
		if row.date and row.date.month == month and row.date.year != year and abs(row.date.year - year) <= 6:
			try:
				row.date = row.date.replace(year=year)
			except ValueError:
				row.date = row.date.replace(year=year, day=28)


def _undo_offsetting_imputations(rows):
	"""相邻两行的金额都是按余额差补出来的、方向相反、数额几乎相等：多半是中间那个余额读错了（B01 印的 534,708.53
	读成 708.53，两行金额都没读到，补成「支出 533,940 / 存入 534,390」互相抵消，余额链显示全通过 —— 2026-09-29
	压测）。只凭账单反推不出真实金额，撤掉这一对补出来的金额（记 0、低可信），让余额链在这里断开交人复核。"""
	for index in range(len(rows) - 1):
		first, second = rows[index], rows[index + 1]
		if not (getattr(first, "amount_autocorrected", False) and getattr(second, "amount_autocorrected", False)):
			continue
		a = (first.deposit or 0) - (first.withdrawal or 0)
		b = (second.deposit or 0) - (second.withdrawal or 0)
		largest = max(abs(a), abs(b))
		if a * b < 0 and largest and abs(a + b) <= max(1.0, largest * 0.02):
			for row in (first, second):
				row.deposit, row.withdrawal, row.amount_autocorrected = 0.0, 0.0, False
				row.confidence = "low"


def _account(value):
	"""科目必须带数字：3.8 读 Express 打印的总账时把「สมุด」栏（จ่าย / รับ）当成科目（B04 BBL6764）。"""
	text = str(value or "").strip()
	return text if any(char.isdigit() for char in text) else ""


def enterprise_rows(legacy, document_type, filename, account_code=""):
	"""Enterprise pipeline pages → the intake's rows; consumes every page and never repairs twice.

	Source: ec2e5c957e6b ``services/recon/enterprise_adapter.try_parse`` (the row mapping after the
	pipeline). ``enterprise`` tells the intake these rows are final: the deployed bank adapter returned
	them as they were, without the statement finalisation the text parsers get. ``bank_code`` stays
	empty so the intake keeps the code it took from the file name (deployed: file name, else "generic").
	"""
	from pearnly_cowork.reconciliation.bank_matching import GlRow
	from pearnly_cowork.reconciliation.bank_statement import StatementRow

	pages = legacy.get("pages") or []
	rows = []
	for page in pages:
		review = bool(page.get("needs_manual_review"))
		for entry in (page.get("document") or {}).get("entries") or []:
			if document_type == "bank_statement":
				if not any(bank_parse.to_float(entry.get(field)) for field in ("deposit", "withdrawal", "balance")):
					continue  # 存、取、余额全是 0 的噪声行（B05 KBank8229-02 第一行「TRN」）；文字路径同样跳过
				row = StatementRow(date=_entry_date(entry),
					description=entry.get("description") or "", deposit=bank_parse.to_float(entry.get("deposit")),
					withdrawal=bank_parse.to_float(entry.get("withdrawal")),
					balance=bank_parse.to_float(entry.get("balance")), source_file=filename,
					confidence="low" if review else "medium",
					amount_autocorrected=bool(entry.get("chain_amount_imputed")),
					direction_autocorrected=bool(entry.get("chain_repaired")))
				if entry.get("balance") is None or not str(entry.get("balance")).strip():
					# 没印余额的行（网银查询截图）余额留空，不当成 0 去核链（2026-09-30 压测 GIZE BBL-SA-01/03）。
					# 行指纹按余额算，所以建好之后再置空（与存储读回的做法一致）。
					row.balance = None
				rows.append(row)
			elif not account_code or _account(entry.get("account_code")) == account_code:
				rows.append(GlRow(date=_entry_date(entry), doc_no=entry.get("voucher_no") or "",
					account_code=_account(entry.get("account_code")), description=entry.get("description") or "",
					debit=bank_parse.to_float(entry.get("debit")), credit=bank_parse.to_float(entry.get("credit")),
					balance=bank_parse.to_float(entry.get("balance")), source_file=filename))
	_same_month_years(rows)
	if document_type == "bank_statement":
		_undo_offsetting_imputations(rows)
	first = (pages[0].get("document") or {}) if pages else {}
	last = (pages[-1].get("document") or {}) if pages else {}
	documents = [page.get("document") or {} for page in pages]
	bank_name = next((str(item.get("bank_name") or "").strip() for item in documents if item.get("bank_name")), "")
	number = next((str(item.get("account_last4") or item.get("account_number") or "") for item in documents
		if item.get("account_last4") or item.get("account_number")), "")
	digits = "".join(char for char in number if char.isdigit())
	return {"ok": bool(rows), "rows": rows, "row_count": len(rows), "enterprise": True,
		# 读出的是哪家银行、账号尾号：同一侧几份账单不一致时提示混入了别的账户（2026-09-29 压测 B05-02）。
		"identity": {"bank": bank_name, "last4": digits[-4:]},
		"opening": bank_parse.to_float(first.get("opening_balance")),
		"closing": bank_parse.to_float(last.get("closing_balance")), "bank_code": "",
		# 读出了余额（哪怕是 0）：没有流水的账单按 0 行对账要靠它（2026-09-30 压测 GIZE BBL-SA-04）。
		"balances_read": any(str(value).strip() for value in (first.get("opening_balance"),
			last.get("closing_balance")) if value is not None),
		# 原文印着“无流水”、且认出了是哪个账户 / 哪个期间：没有余额也按 0 行对账（2026-09-30 压测 SCB 网银）。
		"no_data_printed": any((page.get("extraction_audit") or {}).get("no_data_printed") for page in pages),
		"recognised": bool(bank_name or digits or any(item.get("period_start") for item in documents)),
		"accounts": sorted({row.account_code for row in rows}) if document_type == "general_ledger" else [],
		"parser_version": legacy.get("engine"), "needs_review": bool(legacy.get("_needs_review")),
		"validation_warnings": [warning for page in pages for warning in page.get("validation_warnings") or []]}


@frappe.whitelist(methods=["POST"])
def create_from_sources(workspace, title, request_id, statement_source, gl_source,
        header=None, gl_account_code="", reconciliation_type="bank", anchors=None, lang="th"):
    """Create one reconciliation from two uploaded originals.

    Both sources are read through the files domain, which checks the actor's
    source access and the stored checksum before any byte is returned.
    """
    bank_recon_access.require(workspace)
    statement_filename, statement_bytes = files_service.original(statement_source)
    gl_filename, gl_bytes = files_service.original(gl_source)
    return bank_recon_service.create_from_documents(
        workspace, title, request_id, statement_bytes, statement_filename,
        gl_bytes, gl_filename, header=header, gl_account_code=gl_account_code,
        reconciliation_type=reconciliation_type, anchors=anchors, lang=lang,
        model_rows=document_model_rows(gl_account_code))
