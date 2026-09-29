# -*- coding: utf-8 -*-
"""银行流水 Excel/CSV 直读（零成本，不调模型）。

来源：ec2e5c957e6b ``services/recon/bank_stmt_xlsx.py``。搬的是**确定性那一段**：
多语言表头词典 → 每 sheet 独立找表头 → 取账户号与期初 → 逐行转成 `StatementRow` →
每账户各跑一次余额链（顺序与 `bank_statement.finalize_rows` 一致，那边已经迁好）。

不搬的是旧文件里的 **ADR-006 模板学习层**（拿不准时查已存映射 / 本地推断 /
`allow_ai` 时调一次模型问列对应 / 否则返回 `needs_mapping` 交前端弹"确认列对应"）：
它要一张映射存储表和一个确认界面，属于另一件事；本平台还没落，所以这里**不假装有**。
后果写清楚：表头认不出来时返回旧代码在"没有学习层"时的那个结果
（``stmt_headers_not_found``），而不是 needs_mapping。

保留的几处旧口径（都是有真实事故的，别按直觉改）：

· 表头可在前 16 行任意位置（KTB 在第 11 行）；
· 单一带符号 amount 列（正=存 负=取），没有独立存/取列时用；
· 多账户（每 sheet 一个）**各自**用**自己的**期初做方向纠正与余额校验，绝不跨账户 ——
  KTB 8258（期初 -39）与 8606（期初 -740 万）混成一条链会让整链作废；
· 期初汇总区被银行清空时，用首笔交易"余额 − 净额"反推期初（唯一可证的算术）；
· 期末取**最后一个带余额的行**（末行常是无余额的 Sweep 行），而"余额真的是 0"与
  "余额单元格空着"必须分开看（旧代码曾把归零户的 0 当空，期末报成上一行的值）；
· 汇总/合计行整行跳过，且必须在记期末之前跳（否则大额合计会污染期末）。

只读传入的 bytes：没有凭据、数据库、网络或模型调用。落点属于对账域，公开入口是
`read_statement_workbook`；把它接进原生银行任务（存进任务并跑配对）是后面那一层的事。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pearnly_cowork.tabular import readers as table_io
from pearnly_cowork.reconciliation.bank_parse import parse_date, to_float
from pearnly_cowork.reconciliation.bank_statement import (
	StatementRow, finalize_rows, is_summary_row,
)

# 流水表头词典（四语 + 越南语）：日期 / 摘要 / 取款 / 存款 / 余额 / 单一金额列
_STMT_DATE_H = {"วันที่", "วัน", "date", "trans date", "transaction date", "日期", "交易日",
	"日付", "ngày"}
_STMT_DESC_H = {"รายการ", "รายละเอียด", "description", "detail", "particulars", "narration",
	"memo", "摘要", "描述", "交易摘要", "diễn giải"}
_STMT_WITHDRAW_H = {"ถอนเงิน", "ถอน", "withdrawal", "withdrawals", "debit", "dr", "out",
	"paid out", "取款", "提款", "取出", "出账", "支出", "借方", "出金", "rút"}
_STMT_DEPOSIT_H = {"ฝากเงิน", "ฝาก", "deposit", "deposits", "credit", "cr", "in", "paid in",
	"存款", "存入", "入账", "收入", "贷方", "入金", "gửi"}
_STMT_BALANCE_H = {"ยอดคงเหลือ", "คงเหลือ", "balance", "running balance", "余额", "结余",
	"残高", "số dư"}
_STMT_AMOUNT_H = {"amount", "จำนวนเงิน", "จำนวน", "金额", "金額", "số เงิน", "số tiền"}

_STMT_ACCT_LABEL = ("account no", "account number", "เลขที่บัญชี", "บัญชีเงินฝาก", "账号",
	"账户", "口座")

_STMT_OPEN_KW = ("ยอดยกมา", "ยกยอด", "ยกมา", "brought forward", "balance b/f", "b/f",
	"opening", "期初", "上期")

# 期初行识别里额外认的措辞（ยกยอด 是"承前结转"的另一种写法，曾漏掉→被当存款计入）
_STMT_OPEN_ROW_KW = ("ยอดยกมา", "ยกยอด", "ยกมา", "brought forward", "opening", "期初")

PARSER_VERSION = "bank_recon_v2+xlsx_direct_v3"

# 表格有日期列的表头、却没有余额列：不是银行导出的账单（待查询清单、收款明细之类）。
NOT_A_STATEMENT = "not_a_statement"


def map_statement_columns(header_row: List) -> Dict[str, int]:
	"""识别流水表头各列的位置（zh/th/en/ja/vi）；同一列只认第一次出现。"""
	col_map: Dict[str, int] = {}
	for index, cell in enumerate(header_row):
		header = str(cell or "").strip().lower()
		if not header:
			continue
		if "date" not in col_map and table_io.header_hit(header, _STMT_DATE_H):
			col_map["date"] = index
		elif "description" not in col_map and table_io.header_hit(header, _STMT_DESC_H):
			col_map["description"] = index
		elif "withdrawal" not in col_map and table_io.header_hit(header, _STMT_WITHDRAW_H):
			col_map["withdrawal"] = index
		elif "deposit" not in col_map and table_io.header_hit(header, _STMT_DEPOSIT_H):
			col_map["deposit"] = index
		elif "balance" not in col_map and table_io.header_hit(header, _STMT_BALANCE_H):
			col_map["balance"] = index
		elif "amount" not in col_map and table_io.header_hit(header, _STMT_AMOUNT_H):
			col_map["amount"] = index
	return col_map


def find_statement_header(raw_rows) -> Tuple[int, Dict[str, int]]:
	"""前 16 行里找流水表头（日期 + 余额 + 至少一个金额列）；找不到返回 ``(-1, {})``。"""
	for index, row in enumerate(raw_rows[:16]):
		col_map = map_statement_columns([str(cell or "").strip() for cell in row])
		if "date" in col_map and "balance" in col_map \
				and ("withdrawal" in col_map or "deposit" in col_map or "amount" in col_map):
			return index, col_map
	return -1, {}


def _table_without_balance(raw_rows) -> bool:
	"""前 16 行里有一行像表头（至少 3 个文字格、其中一个是日期列），但整张表找不到余额列。"""
	for row in raw_rows[:16]:
		cells = [str(cell or "").strip() for cell in row]
		texts = [cell for cell in cells if cell and not to_float(cell)]
		col_map = map_statement_columns(cells)
		if len(texts) >= 3 and "date" in col_map and "balance" not in col_map:
			return True
	return False


def extract_sheet_account(raw_rows, header_idx: int, sheet_name: str = "") -> str:
	"""账户号：表头上方那张 `Account No. : xxx` 标签行里右侧第一个非空值，找不到退回
	sheet 名（KTB 把账户号当 sheet 名，如 984-2-99825-8）。"""
	for raw in raw_rows[: max(header_idx, 0)]:
		cells = [str(cell or "").strip() for cell in raw]
		for index, cell in enumerate(cells):
			if any(label in cell.lower() for label in _STMT_ACCT_LABEL):
				for value in cells[index + 1:]:
					if value:
						return value
	name = str(sheet_name or "").strip()
	return name if any(ch.isdigit() for ch in name) else ""


def scan_preheader_opening(raw_rows, header_idx: int) -> Optional[float]:
	"""表头上方带标签的期初余额（ยกมา / opening / b/f…），返回 float 或 None。"""
	for raw in raw_rows[: max(header_idx, 0)]:
		cells = [str(cell or "").strip() for cell in raw]
		if any(keyword in " ".join(cells).lower() for keyword in _STMT_OPEN_KW):
			for cell in cells:
				value = to_float(cell)
				if value != 0.0 or cell.strip() in ("0", "0.00", "0.0"):
					return value
	return None


def parse_statement_sheet(raw_rows, header_idx: int, col_map: Dict[str, int], filename: str,
		account_no: str = "") -> Tuple[List[StatementRow], Optional[float], Optional[float]]:
	"""一个 sheet 的流水行 → ``(rows, opening or None, closing or None)``。

	每行都打上账户号；日期缺的行沿用上一行的日期（银行单常见）；无发生的行跳过。
	"""
	rows: List[StatementRow] = []
	opening_balance = scan_preheader_opening(raw_rows, header_idx)
	opening_found = opening_balance is not None
	last_valid_closing = None
	last_date = None
	date_idx = col_map["date"]
	balance_idx = col_map.get("balance", -1)  # 学习层映射可能没有余额列，缺了按 0（不崩）
	withdrawal_idx = col_map.get("withdrawal", -1)
	deposit_idx = col_map.get("deposit", -1)
	amount_idx = col_map.get("amount", -1)
	description_idx = col_map.get("description", -1)

	def cell(row_list, index):
		return row_list[index] if 0 <= index < len(row_list) else ""

	for raw in raw_rows[header_idx + 1:]:
		if not any(raw):
			continue
		row_list = [str(cell_value or "").strip() for cell_value in raw]
		date_text = cell(row_list, date_idx)
		parsed_date = parse_date(date_text) if date_text else None
		if parsed_date is not None:
			last_date = parsed_date
		balance = to_float(cell(row_list, balance_idx))
		withdrawal = to_float(cell(row_list, withdrawal_idx)) if withdrawal_idx >= 0 else 0.0
		deposit = to_float(cell(row_list, deposit_idx)) if deposit_idx >= 0 else 0.0
		# 单一带符号金额列（没有独立存/取列）：正=存款、负=取款
		if amount_idx >= 0 and withdrawal_idx < 0 and deposit_idx < 0:
			amount = to_float(cell(row_list, amount_idx))
			if amount > 0:
				deposit = amount
			elif amount < 0:
				withdrawal = abs(amount)
		description = cell(row_list, description_idx)

		# 汇总/合计行整行跳过：它的余额常是大额合计，会被当成期末污染余额链
		# （真实小现金件 เงินสดย่อย 实测）。必须在这里跳，不能等到外层事后过滤。
		if description and is_summary_row(description):
			continue

		is_opening = (
			not opening_found
			and parsed_date is None
			and withdrawal == 0.0
			and deposit == 0.0
			and balance != 0.0
		) or (
			not opening_found
			and description
			and any(keyword in description.lower() for keyword in _STMT_OPEN_ROW_KW)
		)
		if is_opening:
			opening_balance = balance
			opening_found = True
			continue
		if parsed_date is None and last_date is None:
			continue
		if withdrawal == 0.0 and deposit == 0.0:
			continue
		rows.append(StatementRow(
			date=parsed_date if parsed_date is not None else last_date,
			description=description,
			withdrawal=withdrawal,
			deposit=deposit,
			balance=balance,
			source_file=filename,
			account_no=account_no,
		))
		# "余额真的是 0"（Sweep 归零户的合法期末）与"余额单元格空着"必须分开：
		# row_list 已经把数值 0.0 变成 ""（0.0 是假值），所以这里看**原始单元格**判空。
		raw_balance_cell = raw[balance_idx] if 0 <= balance_idx < len(raw) else None
		if raw_balance_cell is not None and str(raw_balance_cell).strip() != "":
			last_valid_closing = balance
	return rows, opening_balance, last_valid_closing


def read_statement_workbook(file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
	"""银行流水工作簿/CSV → 与部署版同形的结果字典。

	每个 sheet 独立成账户；每账户用**自己的**期初跑一次余额链（`finalize_rows`：
	跳汇总行 → 按余额纠正方向 → 逐行核对 → 按余额修复）。多账户时期初/期末取各账户
	合计（聚合口径，配合警告），并逐账户做"期初 + Σ存 − Σ取 = 期末"的交叉校验。
	"""
	sheets = table_io.load_workbook_sheets(file_bytes)
	if not sheets:
		extension = (filename or "").lower().rsplit(".", 1)[-1]
		if extension in ("csv", "tsv", "txt"):
			sheets = table_io.load_csv_sheets(file_bytes)
	if not sheets:
		return {
			"ok": False,
			"error_code": "file_unreadable",
			"error": "Cannot read Excel/CSV (legacy .xls / corrupt / unsupported format)",
		}

	accounts: List[Dict[str, Any]] = []
	sheets_with_data = 0
	for sheet_name, raw_rows in sheets:
		header_idx, col_map = find_statement_header(raw_rows)
		if not col_map:
			continue  # 该 sheet 没有流水表头（汇总页/空页），跳过
		account_no = extract_sheet_account(raw_rows, header_idx, sheet_name)
		sheet_rows, sheet_opening, sheet_closing = parse_statement_sheet(
			raw_rows, header_idx, col_map, filename, account_no)
		sheet_rows = [row for row in sheet_rows if not is_summary_row(row.description)]
		if not sheet_rows:
			continue
		opening_known = sheet_opening is not None
		if not opening_known and sheet_rows[0].balance is not None:
			# 期初汇总区被银行整块留白时，用首笔交易"余额 − 净额"反推期初：
			# 这是唯一可证的算术，给余额链一个正确锚点；否则默认 0 会让首行无从核对。
			first = sheet_rows[0]
			opening = round(first.balance - ((first.deposit or 0) - (first.withdrawal or 0)), 2)
			opening_known = True
		else:
			opening = sheet_opening if sheet_opening is not None else 0.0
		closing_known = sheet_closing is not None
		# 末期优先用最后一个"有余额"的行，退回末行余额（末行常是无余额的 Sweep 行）
		closing = sheet_closing if sheet_closing is not None else sheet_rows[-1].balance
		# 每账户用自己的期初：方向纠正 + 逐行核对 + 按余额修复，绝不跨账户
		sheet_rows = finalize_rows(sheet_rows, opening)
		accounts.append({
			"account_no": account_no,
			"rows": sheet_rows,
			"opening": opening,
			"closing": closing,
			"opening_known": opening_known,
			"closing_known": closing_known,
		})
		sheets_with_data += 1

	if not accounts:
		if any(_table_without_balance(raw_rows) for _name, raw_rows in sheets):
			# 有表头、有日期列，就是没有余额列：不是网银导出的账单（2026-09-30 压测 B08-06：事务所自己做的
			# 「สอบถามรายการ」待查询清单，只有序号 / 日期 / 收款 / 备注）。说清楚，不笼统说读不出。
			return {
				"ok": False,
				"error_code": NOT_A_STATEMENT,
				"error": "The sheet has a table with dates but no balance column; it is not a bank statement export",
			}
		return {
			"ok": False,
			"error_code": "stmt_headers_not_found",
			"error": "No bank-statement table found in any sheet",
		}

	all_rows: List[StatementRow] = [row for account in accounts for row in account["rows"]]
	multi_account = len([a for a in accounts if a["account_no"]]) > 1 or len(accounts) > 1
	opening_balance = sum(account["opening"] for account in accounts)
	closing_balance = sum(account["closing"] for account in accounts)

	# 期末交叉校验（期初 + Σ存 − Σ取 ?= 期末）：对不上 = 可能漏行或读错金额。
	# 一头没有真值就不算（无从校验，不误报）。
	issues: List[Dict[str, Any]] = []
	for account in accounts:
		if not (account.get("opening_known") and account.get("closing_known")):
			continue
		sum_deposit = round(sum(row.deposit or 0 for row in account["rows"]), 2)
		sum_withdrawal = round(sum(row.withdrawal or 0 for row in account["rows"]), 2)
		calculated = round(account["opening"] + sum_deposit - sum_withdrawal, 2)
		tolerance = max(1.0, abs(account["closing"]) * 0.001)
		if abs(calculated - account["closing"]) > tolerance:
			issues.append({
				"type": "closing_mismatch",
				"calc": calculated,
				"printed": account["closing"],
				"diff": round(calculated - account["closing"], 2),
				"account": account["account_no"],
			})

	# 行级余额断链也要冒到摘要里：否则用户得逐行翻才看得见（部署版为此加过）。
	broken = sum(1 for row in all_rows if getattr(row, "balance_ok", None) is False)
	if broken > 0:
		issues.append({"type": "balance_break", "count": broken})

	return {
		"ok": True,
		"rows": all_rows,
		"row_count": len(all_rows),
		"opening": opening_balance,
		"closing": closing_balance,
		"bank_code": "generic",
		"parser_version": PARSER_VERSION,
		"sheets_parsed": sheets_with_data,
		"accounts": accounts,
		"account_codes": [account["account_no"] for account in accounts if account["account_no"]],
		"multi_account": multi_account,
		"completeness": {"ok": len(issues) == 0, "issues": issues},
		"needs_review": False,
	}
