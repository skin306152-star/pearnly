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
· 期初优先读余额列（模板与 Mr.erp 把期初放在余额列），没有余额列才退回"借 − 贷"（旧线上是"贷 − 借"，
  与逐行余额方向相反，2026-09-29 有意改正，见下）；
· 空日期沿用上一行的日期（Mr.erp 一天只印一次日期）；
· 期末优先用**表内余额列最后一笔**，没有余额列才按 `期初+借−贷` 算（银行科目借增贷减，与逐行余额同一方向；
  旧线上老公式 `期初+贷−借` 对资产科目方向相反，2026-09-29 有意改正）；`closing_printed` 如实说明期末是读来的还是算出来的；
· 借或贷写成负数的是冲销，记到另一边（旧线上取绝对值，2026-09-29 有意改正，与收入对账同一口径）；
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


# 科目代码 + 名称（「11-01-03-06 SAVING」「1111-01  เงินสด」「1112.01 ธนาคาร」「111201 ธนาคาร」）。
ACCOUNT_HEAD = re.compile(r"^\s*(\d{2,}(?:[-.]\d+)+|\d{4,})\s+\S")


PERIOD_FROM = ("วันที่จาก", "ตั้งแต่วันที่", "from", "period")
PRINTED_DATE_RE = re.compile(r"\d{1,2}\s*[/.\-]\s*\d{1,2}\s*[/.\-]\s*\d{2,4}|\d{1,2}\s+[\u0e00-\u0e7f.]+\s*\d{2,4}")


def _printed_start(rows) -> str:
	"""表头里「วันที่จาก / from」后面的第一个日期（ISO），没有返回空。"""
	for row in rows:
		text = " ".join(str(cell).strip() for cell in row if cell not in (None, ""))
		lowered = text.lower()
		label = next((word for word in PERIOD_FROM if word in lowered), None)
		if not label:
			continue
		match = PRINTED_DATE_RE.search(text[lowered.index(label) + len(label):])
		# 去掉空格再读：部署版 parse_date 遇到空格只留第一段（为了去掉「日期 时间」的时间），「1 ก.พ. 2569」读成「1」。
		value = parse_date(re.sub(r"\s+", "", match.group(0))) if match else None
		if value:
			return value.isoformat()
	return ""


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
	# 科目标题行（Express「11-01-03-06 SAVING-K Bank」、Mr.erp「1111-01  เงินสด」）：第一格是科目代码 + 名称，
	# 没有日期、没有借贷，余额列是这个科目的期初；下面的交易都归这个科目，直到下一个标题行。
	section = ""
	section_openings: Dict[str, Any] = {}
	section_last: Dict[str, float] = {}
	printed_balances = False  # 逐笔余额是否真的印了（Mr.erp 导出每行余额都是 0）

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
				# 期初填在借贷列的格式：净额 = 借 − 贷（银行科目借增贷减，与下面逐行余额同一方向；
				# 旧线上写成贷 − 借，逐行余额却是借 − 贷，没有余额列时期初期末整个反号 —— 2026-09-29 排查）
				credit_idx = col_map.get("credit", -1)
				debit_idx = col_map.get("debit", -1)
				if 0 <= credit_idx < len(row_list):
					credit = to_float(row_list[credit_idx])
					debit = to_float(
						row_list[debit_idx]
						if 0 <= debit_idx < len(row_list) else 0
					)
					opening = debit - credit
					gl_opening_found = True
			continue
		if is_gl_skip_row(row_list):
			continue  # 其余汇总/结转行（ยอดยกไป/subtotal/总计）不是交易
		cell = lambda key: row_list[col_map[key]] if key in col_map and col_map[key] < len(row_list) else ""  # noqa: E731
		head = ACCOUNT_HEAD.match(row_list[0]) if row_list else None
		if (head and parse_date(cell("date")) is None and not to_float(cell("debit") or 0)
				and not to_float(cell("credit") or 0)):
			section = head.group(1)
			section_openings.setdefault(section, to_float(cell("balance")) if cell("balance") else None)
			continue

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
			# 分段总账的行归所在科目段；从单号里抠科目（旧线上）只在没有分段时用 —— Mr.erp 全科目总账的单号
			# 「690531-001」被当成科目，读出 1,468 个“科目”（2026-09-29 压测 B01）。
			account = section or extract_account_code(doc_no) or extract_account_code(description)

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
				# 负数是冲销：记到另一边（旧线上取绝对值，冲销被当成同方向再记一笔，2026-09-29 排查）。
				debit=max(debit, 0.0) + max(-credit, 0.0),
				credit=max(credit, 0.0) + max(-debit, 0.0),
			)
		)
		if (
			"balance" in col_map
			and col_map["balance"] < len(row_list)
			and row_list[col_map["balance"]]
		):
			last_balance_seen = to_float(row_list[col_map["balance"]])
			printed_balances = printed_balances or last_balance_seen != 0
			if section:
				section_last[section] = last_balance_seen

	if rows and not printed_balances:
		# 每行余额都是 0 = 没印逐笔余额，不是余额真为 0：期末按期初 + 借 − 贷算，科目期初不拿它核对。
		last_balance_seen = None
		section_last = {}

	relevant = [code for code in section_openings if not account_code or code.startswith(account_code)]
	if not gl_opening_found and len(relevant) == 1 and section_openings[relevant[0]] is not None:
		# 没有「ยอดยกมา / 期初」字样时，（选中的）科目标题行余额就是期初：有发生额时要与这个科目印出的期末对得上
		# 才采信（期初 + 借 − 贷 = 期末），当月 0 笔就是它本身。2026-09-29 压测：Express 版式总账期初全读成 0，
		# 旧线上同样（它的期初反推用的是按 0 推出来的余额，不起作用）。
		code, printed = relevant[0], section_openings[relevant[0]]
		mine = [row for row in rows if row.account_code == code]
		movement = sum(row.debit for row in mine) - sum(row.credit for row in mine)
		if (not mine or not printed_balances
				or (code in section_last and abs(printed + movement - section_last[code]) <= 0.01)):
			opening, gl_opening_found = printed, True
			if code in section_last:
				last_balance_seen = section_last[code]
	if not gl_opening_found:
		opening = 0.0
	attach_running_balance(rows, opening)
	if last_balance_seen is not None:
		closing = round(last_balance_seen, 2)
	else:
		total_credit = sum(row.credit for row in rows)
		total_debit = sum(row.debit for row in rows)
		closing = round(opening + total_debit - total_credit, 2)

	return {
		"ok": True,
		"rows": rows,
		"accounts": sorted(accounts_seen - {"?"}),
		"opening": opening,
		"closing": closing,
		"closing_printed": last_balance_seen is not None,
		"row_count": len(rows),
		# 表里印了期初行：0 行时据此区分“本月没有发生额”和“读错了表”（bank_intake.read_ledger_file）。
		"opening_found": gl_opening_found,
		# 表头印的起始日期（「วันที่จาก 1 ก.พ. 2569」）：当月无发生额时用它定总账期间（对账按期间筛账单行）。
		"period_start": _printed_start(all_rows_raw[:header_idx]),
	}
