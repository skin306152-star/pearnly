# -*- coding: utf-8 -*-
"""对账单 / 总账的**文件入口**：字节 → 行（把前面几层接到原生银行任务上的那一步）。

来源：ec2e5c957e6b ``services/recon/bank_recon_v2.py`` 的分发（``_parse_bank_statement_impl``
/ ``_parse_gl_impl``）与 ``services/recon/bank_recon_pipeline.py`` 里"选哪条路"的那一段。

部署版有两条模型路：默认的 **OCR 管线**（提示词 + 文档模型 + 列来源校验）与 PDF 回退用的
**旧 gemini 路**。本模块接的是后者 —— 也就是本地回退链，因为它的每一段都已经在原生：

对账单：文件名里的银行码优先 → xlsx/xls/xlsm/csv/tsv **先直读**（第 ③ 层，零成本，认不出表头
才轮到模型）→ PDF 走文字层 + 文本行解析（KBank 列压行 / 一行一笔，行数不足或余额链太坏才轮到
模型）→ 定案序列（跳汇总行/方向纠正/逐行核对/金额反推）→ 换眼重读 → 期初期末兜底 → 完整性
交叉校验。

总账：xlsx/xls/xlsm/csv/tsv 先直读，读不出才轮到模型；PDF 与其它格式只能走模型。

模型这一层是**注入**的：``model_rows(document_type, file_bytes, filename)`` 只要交出
``{ok, rows, opening, closing, ...}``。谁来填由编排层决定 —— 现在的实现是 OCR 域的
**文档档位**（`ocr.engine.document_tier`：提示词 → 文档模型 → 列来源校验 → 换眼重读 →
legacy 形状 → 本包的 `statement_from_pipeline`/`gl_from_pipeline` 出行），见 DocType 控制器
的 `document_model_rows`；本包已迁的 ``bank_model_path``（旧 gemini 路的 JSON → 行）是另一
个可选实现。本模块只要求"给我行"，因此可以完全离线验证（用一个假模型）。

**没有器件的部分如实留空**：pdfplumber 表格与坐标解析、图片/Word 的直读、图片渲染。
``read_page`` / ``page_count`` 没给就不做换眼重读（结果与"没有断链"同形，不带 escalations 键）。

只吃传进来的字节与行：不读文件、不打数据库、不调模型。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from pearnly_cowork.reconciliation import (
	bank_arbitration, bank_parse, bank_reread, bank_sheets, bank_statement, bank_text,
	gl_sheets,
)
from pearnly_cowork.reconciliation.bank_matching import GlRow
from pearnly_cowork.reconciliation.bank_statement import StatementRow
from pearnly_cowork.tabular import readers as table_io

WORKBOOK_EXTENSIONS = ("xlsx", "xls", "xlsm", "csv", "tsv", "txt")

# 部署版 MIN_PLUMBER_ROWS：免费解析出的行少于这个数就轮到模型。
MIN_TABLE_ROWS = 3
# 部署版 `_need_gemini` 的余额链可信度阈值：坏行占比超过它也要轮到模型。
BAD_RATIO_LIMIT = 0.30
# 期初兜底关键词（部署版 `parse_bank_statement` 的 `_OPEN_KW`）。
OPENING_BACKFILL_KEYWORDS = ("b/f", "brought forward", "ยอดยกมา", "ยกมา", "opening", "期初", "上期")

ModelRows = Callable[[str, bytes, str], Optional[Dict[str, Any]]]


def extension(filename: str) -> str:
	"""小写扩展名（不含点）；没有扩展名就是空串。"""
	return (filename or "").lower().rsplit(".", 1)[-1]


def _adopted(bank_code: str, filename: str) -> str:
	"""文件名里的银行码优先 —— 一条满是跨行转账的流水会把对手行名印上百次。"""
	return bank_parse.bank_code_from_filename(filename) or bank_code or "generic"


def _failure(error_code: str, error: str, **extra) -> Dict[str, Any]:
	return {"ok": False, "error_code": error_code, "error": error, "rows": [], **extra}


def _statement_text_rows(file_bytes: bytes, bank_code: str
		) -> Tuple[List[StatementRow], float, float, str]:
	"""PDF 文字层 → 文本行解析；返回 ``(rows, opening, closing, source)``。

	与部署版第 4a/4b 步同序同判：先 KBank 列压行（仅 kbank），**行数仍不足**才再试一行一笔；
	每一步都只在"行数严格更多"时替换。
	"""
	pages = table_io.extract_pdf_text_pages(file_bytes)
	text = "\n".join(pages)
	rows: List[StatementRow] = []
	opening = 0.0
	closing = 0.0
	source = ""
	if not text.strip():
		return rows, opening, closing, source
	if bank_code == "kbank":
		column_rows, column_opening, column_closing = bank_text.parse_kbank_text_columns(text)
		if len(column_rows) > len(rows):
			rows, opening, closing = column_rows, column_opening, column_closing
			source = "kbank_columns"
	if len(rows) < MIN_TABLE_ROWS:
		text_rows, text_opening, text_closing = bank_text.parse_statement_text_lines(text, bank_code)
		if len(text_rows) > len(rows):
			rows, opening, closing = text_rows, text_opening, text_closing
			source = "text_lines"
	return rows, opening, closing, source


def repair_balance_from_neighbours(rows, opening):
	"""某一行余额与前后都对不上，而「前一行余额 + 本行发生额 + 下一行发生额 = 下一行余额」成立：是这一行余额
	读错了（B01 印的 451,134.86 读成 451,134.99）。前后两边同时印证才改，标成已自动更正，再重新核对一遍。"""
	previous = opening
	for index, row in enumerate(rows):
		if index + 1 < len(rows) and previous is not None:
			following = rows[index + 1]
			expected = round(previous + (row.deposit or 0) - (row.withdrawal or 0), 2)
			if (following.balance and abs(expected - (row.balance or 0)) >= 0.01
					and abs(expected + (following.deposit or 0) - (following.withdrawal or 0) - following.balance) <= 0.01):
				row.balance = expected
				row.amount_autocorrected = True
		previous = row.balance
	bank_statement.verify_row_balances(rows, opening)


def chronological(rows, opening, closing):
	"""新日期在前的账单（BBL 网银：第一行就是月底）翻成从旧到新，期初按翻过来的第一行重算、期末取最后一行。

	只在日期整体倒着排、且倒过来余额链断得更少时才翻（2026-09-29 压测 B04 BBL6764：48 行里 47 行断链，期初推成
	月底余额）。旧线上没有这一步。余额为负（透支户）照常算。
	"""
	dated = [row.date for row in rows if row.date]
	if len(rows) < 2 or len(dated) < 2 or dated[0] <= dated[-1]:
		return rows, opening, closing

	def breaks(sequence):
		count, previous = 0, None
		for row in sequence:
			if previous is not None and abs(previous + (row.deposit or 0) - (row.withdrawal or 0) - (row.balance or 0)) > 0.01:
				count += 1
			previous = row.balance
		return count

	if breaks(list(reversed(rows))) >= breaks(rows):
		return rows, opening, closing
	rows = list(reversed(rows))
	first = rows[0]
	opening = round((first.balance or 0) - (first.deposit or 0) + (first.withdrawal or 0), 2)
	return rows, opening, rows[-1].balance or closing


def read_statement_file(file_bytes: bytes, filename: str, *, model_rows: Optional[ModelRows] = None,
		read_page: Optional[Callable[[int], List[StatementRow]]] = None,
		page_count: Optional[int] = None,
		max_reread_pages: int = bank_reread.MAX_REREAD_PAGES) -> Dict[str, Any]:
	"""对账单文件 → 结果字典（形状归一，见模块说明）。

	成功：``{ok, rows, row_count, opening, closing, bank_code, source, parser_version,
	completeness, balance_warn_count, low_conf_count, amount_fixed_count, printed_totals,
	needs_review}``；走到换眼时另带 ``escalations``，两法分歧时另带 ``method_divergence``，
	直读路另带 ``sheets_parsed/account_codes/multi_account``。
	失败：``{ok: False, error_code, error, rows: []}``。
	"""
	ext = extension(filename)
	bank_code = _adopted("", filename)
	rows: List[StatementRow] = []
	opening = 0.0
	closing = 0.0
	source = ""
	completeness: Optional[Dict[str, Any]] = None
	parser_version = bank_parse.PARSER_VERSION
	accounts: Optional[Dict[str, Any]] = None
	free_error: Optional[Tuple[str, str]] = None

	if ext in WORKBOOK_EXTENSIONS:
		direct = bank_sheets.read_statement_workbook(file_bytes, filename)
		if direct["ok"]:
			rows = direct["rows"]
			opening = direct["opening"]
			closing = direct["closing"]
			source = "workbook"
			completeness = direct["completeness"]
			parser_version = direct["parser_version"]
			accounts = {"sheets_parsed": direct["sheets_parsed"],
				"account_codes": direct["account_codes"], "multi_account": direct["multi_account"]}
		else:
			free_error = (direct["error_code"], direct["error"])
	elif ext == "pdf":
		text = "\n".join(table_io.extract_pdf_text_pages(file_bytes))
		bank_code = _adopted(bank_parse.detect_bank(text) if text.strip() else "", filename)
		rows, opening, closing, source = _statement_text_rows(file_bytes, bank_code)
	elif model_rows is None:
		return _failure("unsupported_format",
			f"unsupported format .{ext}" if ext else "unsupported format")

	# 要不要轮到模型：行数不够，或免费解析的余额链太坏（部署版 `_need_gemini`）。
	printed_totals = None
	method_divergence: List[str] = []
	if model_rows is not None and (not rows or len(rows) < MIN_TABLE_ROWS
			or bank_statement.statement_row_bad_ratio(rows, opening) > BAD_RATIO_LIMIT):
		free_bad = bank_statement.statement_row_bad_ratio(rows, opening)
		model = model_rows("bank_statement", file_bytes, filename) or {}
		model_row_list = model.get("rows") or []
		if model.get("ok") and model_row_list:
			model_opening = float(model.get("opening", opening) or 0)
			model_bad = bank_statement.statement_row_bad_ratio(model_row_list, model_opening)
			# 两法分歧先留痕：无论后面选谁都不能静默（BBL2645 的教训）。
			method_divergence = bank_arbitration.divergence_reasons(
				opening, closing, len(rows), model_opening,
				model.get("closing", closing), len(model_row_list))
			# 免费行数不足 → 直接用模型；否则谁的余额链更可信用谁。
			if len(rows) < MIN_TABLE_ROWS or model_bad < free_bad:
				rows = model_row_list
				opening = model_opening
				closing = model.get("closing", closing)
				bank_code = model.get("bank_code") or bank_code
				printed_totals = model.get("printed_totals")
				source = "enterprise" if model.get("enterprise") else "model"
				completeness = None  # 模型路的完整性用下面的交叉校验重算

	if not rows:
		if free_error is not None:
			return _failure(free_error[0], free_error[1])
		if ext == "pdf":
			text = "\n".join(table_io.extract_pdf_text_pages(file_bytes))
			hint = "" if text.strip() else " (PDF has no extractable text)"
			return _failure("no_rows", f"No statement rows found in PDF{hint}")
		return _failure("no_rows", "No statement rows found")

	for row in rows:
		row.source_file = filename
	rows, opening, closing = chronological(rows, opening, closing)

	# 定案序列（跳汇总行 → 方向纠正 → 逐行核对 → 金额反推修复），顺序是正确性不变式。
	# 2026-09-29 有意变化：企业档的行也走（旧线上直接返回）。本地拼行的行余额链已平，走一遍不变；3.8 抽取的行
	# 没修过 —— B08 BBL0555 一笔存入多读 300（下一行 +300 / -300 抵消）没被纠正。另补一步余额反推（本地拼行的
	# 部署修正有，文字路径没有）：前后两行都印证时，改读错的那一行余额。
	rows = bank_statement.finalize_rows(rows, opening)
	if source == "enterprise":
		repair_balance_from_neighbours(rows, opening)

	# 换眼重读：只有调用方给了"读一页"和页数（也就是有渲染/直读器件）才做。
	escalations: List[Dict[str, Any]] = []
	if read_page is not None and page_count is not None:
		rows, escalations = bank_reread.maybe_reread_chain_breaks(
			rows, opening, page_count=int(page_count), read_page=read_page,
			extension=ext, max_pages=max_reread_pages)

	balance_warn_count = sum(1 for row in rows if row.balance_ok is False)
	low_conf_count = sum(1 for row in rows if row.confidence == "low")
	# 金额被余额链反推改写过的行：它们 balance_ok 已翻 True，单独计数供人工看。
	amount_fixed_count = sum(1 for row in rows if getattr(row, "amount_autocorrected", False))

	# 期初/期末兜底回填：模型有时不回传它俩（行里已有 B/F 与末行余额时用算术补回）。
	if not opening:
		first = rows[0]
		description = (first.description or "").lower()
		if ((first.deposit or 0) == 0 and (first.withdrawal or 0) == 0 and first.balance
				and any(keyword in description for keyword in OPENING_BACKFILL_KEYWORDS)):
			opening = first.balance
		else:
			movement = next((row for row in rows
				if (row.deposit or 0) or (row.withdrawal or 0)), None)
			if movement and movement.balance:
				opening = round(movement.balance
					- ((movement.deposit or 0) - (movement.withdrawal or 0)), 2)
	if not closing:
		closing = next((row.balance for row in reversed(rows) if row.balance), closing)

	# 打印合计/笔数 + 期末平衡的交叉校验；直读路自带它自己那份，别覆盖。
	if completeness is None:
		completeness = bank_statement.audit_completeness(rows, opening, closing, printed_totals)
	if method_divergence:
		completeness = dict(completeness)
		completeness["ok"] = False
		completeness["issues"] = list(completeness.get("issues") or []) + [
			"两法分歧(详见 method_divergence)"]

	result: Dict[str, Any] = {
		"ok": True,
		"rows": rows,
		"row_count": len(rows),
		"opening": opening,
		"closing": closing,
		"bank_code": _adopted(bank_code, filename),
		"source": source,
		"parser_version": parser_version,
		"balance_warn_count": balance_warn_count,
		"low_conf_count": low_conf_count,
		"amount_fixed_count": amount_fixed_count,
		"printed_totals": printed_totals,
		"completeness": completeness,
		"needs_review": bool(balance_warn_count or low_conf_count or amount_fixed_count
			or not completeness["ok"]),
	}
	if method_divergence:
		result["method_divergence"] = method_divergence
	if escalations:
		result["escalations"] = escalations
	if accounts is not None:
		result.update(accounts)
	return result


def read_ledger_file(file_bytes: bytes, filename: str, *, account_code: str = "",
		model_rows: Optional[ModelRows] = None) -> Dict[str, Any]:
	"""总账文件 → 结果字典；直读读不出（或格式不是表格）时轮到注入的模型路。"""
	ext = extension(filename)
	rows: List[GlRow] = []
	accounts: List[str] = []
	opening = 0.0
	closing = 0.0
	closing_printed = False
	source = ""
	free_error: Optional[Tuple[Optional[str], str]] = None
	no_movement = False

	if ext in WORKBOOK_EXTENSIONS:
		direct = gl_sheets.parse_gl_workbook(file_bytes, filename, account_code)
		if direct["ok"]:
			rows, accounts = direct["rows"], direct["accounts"]
			opening, closing = direct["opening"], direct["closing"]
			closing_printed = direct["closing_printed"]
			source = "workbook"
			# 印了期初、当月一笔也没有：这个户本月无发生额（Express 的储蓄 / 定期户多数月份如此），照样对账，
			# 银行那边的每一笔都显示为总账未记。旧线上整单判失败（no_rows）；2026-09-29 用户：对产品有利的直接做。
			no_movement = not rows and bool(direct.get("opening_found"))
		else:
			free_error = (direct.get("error_code"), direct["error"])
	elif ext == "pdf":
		# 总账的 PDF 表格/坐标器件还没进原生：这条格式只能走模型。
		if model_rows is None:
			return _failure("model_unavailable", "the ledger PDF path needs the model tier",
				accounts=[])
	elif model_rows is None:
		return _failure("unsupported_format",
			f"unsupported format .{ext}" if ext else "unsupported format", accounts=[])

	if not rows and model_rows is not None and not no_movement:
		model = model_rows("general_ledger", file_bytes, filename) or {}
		if model.get("ok") and model.get("rows"):
			rows = model["rows"]
			accounts = list(model.get("accounts") or [])
			opening = float(model.get("opening", 0) or 0)
			closing = float(model.get("closing", 0) or 0)
			closing_printed = bool(model.get("closing_printed", False))
			source = "model"
		elif model.get("error"):
			return _failure(model.get("error_code") or "model_failed", model["error"], accounts=[])

	if not rows and not no_movement:
		if free_error is not None and free_error[0]:
			return _failure(free_error[0], free_error[1], accounts=[])
		if model_rows is None:
			return _failure("model_unavailable",
				"no local reader for this format and no model path was supplied", accounts=[])
		return _failure("no_rows", "gemini returned no usable rows", accounts=[])

	for row in rows:
		row.source_file = filename
	return {
		"ok": True,
		"rows": rows,
		"row_count": len(rows),
		"accounts": accounts,
		"opening": opening,
		"closing": closing,
		"closing_printed": closing_printed,
		"source": source,
		"parser_version": bank_parse.PARSER_VERSION,
		"needs_review": False,
	}
