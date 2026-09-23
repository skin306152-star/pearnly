# -*- coding: utf-8 -*-
"""总账 Excel/CSV 直读（零成本，不调模型）。

来源：ec2e5c957e6b ``services/recon/bank_gl_excel.py`` 的**确定性那一段**，加上它共用的
``services/recon/bank_gl_common.py``（列名词典、科目号正则、表头映射、运行余额）。
与银行流水那支（`bank_sheets`）并列，是第 ③ 层"不花钱的那条路"的第二块。

不搬的是旧文件里的 **ADR-006 模板学习层**（已存映射 / 本地推断 / `allow_ai` 时调模型问列
对应 / 否则 `needs_mapping` 交前端弹"确认列对应"）：它要一张映射存储表和一个确认界面，
属另一件事；本平台还没落，所以这里**不假装有**。后果写清楚：表头认不出时返回旧代码在
"没有学习层"时的结果 ``gl_headers_not_found``，而不是 needs_mapping。

保留的几处旧口径（都有真实事故，别按直觉改）：

· 表头必须**同时**有日期列和钱列（借或贷）才算真表头 —— 只有日期+科目+余额不算
  （否则单列净额 GL 会解析出 0 行还一路跑到"完成"）；
· 期初单独识别，且**扫该行前四格**而不是只看描述列：固定模板把"期初"标签放在日期列；
· 期初优先读余额列（模板与 Mr.erp 把期初放在余额列），没有余额列才退回"贷 − 借"；
· 空日期沿用上一行的日期（Mr.erp 一天只印一次日期）；
· 期末优先用**表内余额列最后一笔**（老公式 `期初+贷−借` 对收入科目正确、对资产科目方向
  相反），没有余额列才退回老公式；`closing_printed` 如实说明期末是读来的还是算出来的；
· 借贷两列都是 0 的行不是交易，跳过；`account_code` 给了就按前缀过滤。

只读传入的 bytes：没有凭据、数据库、网络或模型调用。公开入口是 `parse_gl_workbook`；
把它接进原生对账任务（存进任务并参与匹配）是后面那一层的事。
"""

from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Tuple

from pearnly_cowork.tabular import readers as table_io
from pearnly_cowork.reconciliation.bank_matching import GlRow
from pearnly_cowork.reconciliation.bank_parse import (
	is_gl_skip_row, parse_date, to_float,
)

# 总账表头词典（四语）：日期 / 凭证 / 摘要 / 借方 / 贷方 / 科目 / 余额
GL_DATE_H = {"วันที่", "date", "วัน", "日期", "日付"}
GL_DOC_H = {"ใบสำคัญ", "เลขที่เอกสาร", "doc", "voucher", "reference", "เอกสาร", "凭证",
	"伝票番号", "伝票", "ref"}
GL_DESC_H = {"คำอธิบาย", "รายการ", "description", "detail", "รายละเอียด", "摘要"}
GL_DEBIT_H = {"เดบิต", "เดบิท", "debit", "dr", "借方", "ถอน", "จ่าย"}
GL_CREDIT_H = {"เครดิต", "credit", "cr", "贷方", "貸方", "ฝาก", "รับ"}
GL_ACCOUNT_H = {"รหัสบัญชี", "account", "gl account", "เลขที่บัญชี", "รหัส", "账号", "科目"}
# 余额列识别：老逻辑只看借贷，Row 2 的期初 `ยอดยกมา` 余额列填了数却没读到 → 期初 0 →
# 期末全错（BUG-FIX-T2 v118.35.0.43）。
GL_BALANCE_H = {"คงเหลือ", "ยอดคงเหลือ", "balance", "running balance", "余额", "残高"}

# 科目号：4–7 位数字，或 4–7 位加「-两位/三位」的分级写法；前后不能贴着别的数字或小数点。
ACCOUNT_CODE_RE = re.compile(r"(?<![\d.])([1-9]\d{3,6}(?:[-–]\d{2,3})?)(?![\d.])")

# 期初余额行关键词（四语，只认"期初/承前"，不含"期末/结转"）。固定模板把标签放在日期列
# 而不是描述列，所以识别期初要扫整行前几格。
GL_OPENING_KEYWORDS = (
	"ยอดยกมา",
	"ยกมา",
	"brought forward",
	"balance forward",
	"opening",
	"b/f",
	"期初",
	"繰越残高",
)


def map_gl_columns(header_row: List) -> Dict[str, int]:
	"""识别总账表头各列的位置；同一列只认第一次出现。**没有单列净额（amount）键** ——
	固定词典里没有它，单列净额只有旧侧的学习层映射拿得到（见模块说明）。"""
	col_map: Dict[str, int] = {}
	for index, cell in enumerate(header_row):
		header = str(cell or "").strip().lower()
		if not header:
			continue
		if "date" not in col_map and table_io.header_hit(header, GL_DATE_H):
			col_map["date"] = index
		elif "doc_no" not in col_map and table_io.header_hit(header, GL_DOC_H):
			col_map["doc_no"] = index
		elif "description" not in col_map and table_io.header_hit(header, GL_DESC_H):
			col_map["description"] = index
		elif "debit" not in col_map and table_io.header_hit(header, GL_DEBIT_H):
			col_map["debit"] = index
		elif "credit" not in col_map and table_io.header_hit(header, GL_CREDIT_H):
			col_map["credit"] = index
		elif "account" not in col_map and table_io.header_hit(header, GL_ACCOUNT_H):
			col_map["account"] = index
		elif "balance" not in col_map and table_io.header_hit(header, GL_BALANCE_H):
			col_map["balance"] = index
	return col_map


def extract_account_code(text: str) -> str:
	"""从凭证号或摘要里抽出科目号；抽不到返回空串。"""
	match = ACCOUNT_CODE_RE.search(str(text or ""))
	return match.group(1) if match else ""


def attach_running_balance(rows: List[GlRow], opening: float) -> None:
	"""给每行附运行余额（期初 + 累计 借 − 贷，借方抬升），就地写 ``row.balance``。

	只用于展示/导出，不参与匹配 —— 旧侧把这条约定写在这里，两个 GL 解析器共用。
	"""
	balance = opening
	for row in rows:
		balance = round(balance + row.debit - row.credit, 2)
		row.balance = balance


def _load_gl_rows(file_bytes: bytes, filename: str):
	"""读出工作簿的**第一张表**（旧 GL 直读只看 active/第一张，与流水那支读全部 sheet
	不同）；返回 ``(rows, None)`` 或 ``(None, 错误文案)``。"""
	extension = (filename or "").lower().rsplit(".", 1)[-1]
	all_rows_raw = None
	if extension in ("csv", "tsv", "txt"):
		csv_sheets = table_io.load_csv_sheets(file_bytes)
		if csv_sheets:
			all_rows_raw = csv_sheets[0][1]
	if all_rows_raw is None:
		try:
			import openpyxl

			workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
			all_rows_raw = list(workbook.active.values)
		except Exception:
			try:
				import xlrd

				workbook = xlrd.open_workbook(file_contents=file_bytes)
				sheet = workbook.sheet_by_index(0)
				all_rows_raw = [sheet.row_values(number) for number in range(sheet.nrows)]
			except Exception as error:
				# The deployed message quotes the **last** (xlrd) failure, not the
				# openpyxl one; keep the same text.
				csv_sheets = table_io.load_csv_sheets(file_bytes)
				if csv_sheets:
					all_rows_raw = csv_sheets[0][1]
				else:
					return None, f"Cannot read Excel/CSV: {error}"
	return all_rows_raw, None


def find_gl_header(all_rows_raw) -> Tuple[int, Dict[str, int]]:
	"""前 10 行里找总账表头：**必须有日期列，且借或贷至少有一列**；找不到返回 ``(-1, {})``。"""
	for index, row in enumerate(all_rows_raw[:10]):
		col_map = map_gl_columns([str(cell or "").strip() for cell in row])
		if "date" in col_map and ("debit" in col_map or "credit" in col_map):
			return index, col_map
	return -1, {}


def parse_gl_workbook(file_bytes: bytes, filename: str = "",
		account_code: str = "") -> Dict[str, Any]:
	"""总账工作簿/CSV → 与部署版同形的结果字典。

	成功：``{ok, rows, accounts, opening, closing, closing_printed, row_count}``；
	失败：``{ok: False, error}`` 或 ``{ok: False, error_code: "gl_headers_not_found", error}``。
	"""
	all_rows_raw, load_error = _load_gl_rows(file_bytes, filename)
	if load_error is not None:
		return {"ok": False, "error": load_error}

	header_idx, col_map = find_gl_header(all_rows_raw)
	if not col_map:
		# 旧代码在这里走学习层（本平台没有），没有映射可用时的结果就是这个。
		return {
			"ok": False,
			"error_code": "gl_headers_not_found",
			"error": "Cannot detect GL column headers",
		}

	rows: List[GlRow] = []
	accounts_seen = set()
	opening = 0.0
	closing = 0.0
	gl_opening_found = False
	last_row_date = None  # 空日期沿用上一行（Mr.erp 一天只印一次日期）
	last_balance_seen = None  # 给期末兜底用最后一笔交易的余额

	for row in all_rows_raw[header_idx + 1:]:
		if not any(row):
			continue
		row_list = [str(cell or "").strip() for cell in row]
		# 期初余额行：标签在日期列，所以扫该行前四格（四语：期初/Opening/ยอดยกมา/繰越）。
		if any(keyword in " ".join(row_list[:4]).lower() for keyword in GL_OPENING_KEYWORDS):
			balance_idx = col_map.get("balance", -1)
			if 0 <= balance_idx < len(row_list) and row_list[balance_idx]:
				opening = to_float(row_list[balance_idx])
				gl_opening_found = True
			else:
				# 期初填在借贷列的格式：净额 = 贷 − 借
				credit_idx = col_map.get("credit", -1)
				debit_idx = col_map.get("debit", -1)
				if 0 <= credit_idx < len(row_list):
					credit = to_float(row_list[credit_idx])
					debit = to_float(
						row_list[debit_idx]
						if 0 <= debit_idx < len(row_list) else 0
					)
					opening = credit - debit
					gl_opening_found = True
			continue
		if is_gl_skip_row(row_list):
			continue  # 其余汇总/结转行（ยอดยกไป/subtotal/总计）不是交易

		date_text = (
			row_list[col_map["date"]]
			if "date" in col_map and col_map["date"] < len(row_list)
			else ""
		)
		tx_date = parse_date(date_text) if date_text else None
		if tx_date is not None:
			last_row_date = tx_date
		elif last_row_date is not None:
			tx_date = last_row_date
		else:
			continue

		doc_no = (
			row_list[col_map["doc_no"]]
			if "doc_no" in col_map and col_map["doc_no"] < len(row_list)
			else ""
		)
		description = (
			row_list[col_map["description"]]
			if "description" in col_map and col_map["description"] < len(row_list)
			else ""
		)
		debit = to_float(
			row_list[col_map["debit"]]
			if "debit" in col_map and col_map["debit"] < len(row_list)
			else 0
		)
		credit = to_float(
			row_list[col_map["credit"]]
			if "credit" in col_map and col_map["credit"] < len(row_list)
			else 0
		)
		# 单列净额（Net Movement）：没有独立借/贷列时按符号拆，正=借方、负=贷方。
		# 固定词典里没有 amount 键，所以这条路只有调用方给出映射时才走到（旧侧是学习层）。
		if (
			debit == 0.0
			and credit == 0.0
			and "amount" in col_map
			and col_map["amount"] < len(row_list)
		):
			amount = to_float(row_list[col_map["amount"]])
			if amount > 0:
				debit = amount
			elif amount < 0:
				credit = abs(amount)

		account = ""
		if "account" in col_map and col_map["account"] < len(row_list):
			account = str(row_list[col_map["account"]]).strip()
		if not account:
			account = extract_account_code(doc_no) or extract_account_code(description)

		if debit == 0.0 and credit == 0.0:
			continue

		if account_code and account and not account.startswith(account_code):
			continue

		accounts_seen.add(account or "?")
		rows.append(
			GlRow(
				date=tx_date,
				doc_no=doc_no,
				account_code=account,
				description=description,
				debit=abs(debit),
				credit=abs(credit),
			)
		)
		if (
			"balance" in col_map
			and col_map["balance"] < len(row_list)
			and row_list[col_map["balance"]]
		):
			last_balance_seen = to_float(row_list[col_map["balance"]])

	if not gl_opening_found:
		opening = 0.0
	attach_running_balance(rows, opening)
	if last_balance_seen is not None:
		closing = round(last_balance_seen, 2)
	else:
		total_credit = sum(row.credit for row in rows)
		total_debit = sum(row.debit for row in rows)
		closing = round(opening + total_credit - total_debit, 2)

	return {
		"ok": True,
		"rows": rows,
		"accounts": sorted(accounts_seen - {"?"}),
		"opening": opening,
		"closing": closing,
		"closing_printed": last_balance_seen is not None,
		"row_count": len(rows),
	}
