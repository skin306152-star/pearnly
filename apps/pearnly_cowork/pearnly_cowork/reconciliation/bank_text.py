# -*- coding: utf-8 -*-
"""银行单的**文本行**解析（没有表格结构时的那条路）。

来源：ec2e5c957e6b ``services/recon/bank_stmt_text.py``（从 ``bank_recon_v2.py`` 拆出的
原文）。PDF 抽出的文字层既没有单元格边界也没有坐标，只能按行猜；旧侧在"pdfplumber 表格
拿到的行数不够"时才走这里，本平台把两个解析器原样交出来，由调用方（第 ④ 层的接线）
决定取舍。

两个解析器各治一种排版：

· `parse_statement_text_lines` —— 一行一笔（日期开头，金额与余额在行尾连续两个数字）；
  方向靠**余额差**判断（差 = +金额 → 存款，差 = −金额 → 取款），差额对不上才退回描述词，
  再不行按"当存款"。还处理泰文用句点当千分位的写法（``115.586.50`` → ``115586.50``）。
· `parse_kbank_text_columns` —— KBank 的 pdfminer 文字层是**列被压成行**的：日期、时间、
  摘要各占一行，金额与余额在另一个块里交替出现。所以先按"日期行"抽交易头，再按
  num / num+渠道 的交替模式配对，按位置把两者接起来。

另有 `labelled_account`（2026-10-13 新加，旧线上没有）：账单表头上标着「เลขที่บัญชี / Account No.」的账号，
对账预设按账户记时用（与表格账单的 ``bank_sheets.labelled_sheet_account`` 同一口径）。
还有 `printed_bank_name`（2026-10-20 新加，旧线上没有）：同一块表头上印着的银行名，结果、导出和 CC 回答里写的银行名取它。

只读传入的文本：没有凭据、数据库、网络或模型调用。
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from pearnly_cowork.reconciliation.bank_parse import parse_date, to_float
from pearnly_cowork.reconciliation.bank_statement import AMOUNT_TOL, StatementRow

_DATE_PREFIX = re.compile(r"^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$")


def parse_statement_text_lines(text: str, bank_code: str = "generic") -> Tuple[List[StatementRow], float, float]:
	"""一行一笔的文本行解析 → ``(rows, opening, closing)``。

	``bank_code`` 保留在签名里：部署版收下这个参数后**从未读过它**，而调用方一直传，
	所以形状照旧，免得接线时改两处。
	"""
	rows: List[StatementRow] = []
	opening = 0.0
	closing = 0.0
	prev_balance: Optional[float] = None

	def token_to_float(token: str) -> Optional[float]:
		"""文本里的数字：去掉千分位逗号与空格，支持句点当千分位（泰文写法），支持前导负号。"""
		raw = token.replace(",", "").replace(" ", "")
		if not raw:
			return None
		negative = raw.startswith("-")
		if negative:
			raw = raw[1:]
		if raw.count(".") > 1:
			last_dot = raw.rfind(".")
			raw = raw[:last_dot].replace(".", "") + raw[last_dot:]
		try:
			value = float(raw)
			return -value if negative else value
		except ValueError:
			return None

	for line in text.splitlines():
		line = line.strip()
		if not line:
			continue

		# 期初标记：取该行最后一个正数
		if any(keyword in line for keyword in ["ยอดยกมา", "brought forward", "ยอดคงเหลือยกมา"]):
			tokens = line.split()
			for token in reversed(tokens):
				value = token_to_float(token)
				if value is not None and value > 0:
					opening = value
					prev_balance = opening
					break
			continue

		# 期末标记：同样取该行最后一个正数（本行不入 rows）
		if any(keyword in line for keyword in ["ยอดยกไป", "closing balance"]):
			tokens = line.split()
			for token in reversed(tokens):
				value = token_to_float(token)
				if value is not None and value > 0:
					closing = value
					break
			continue

		tokens = line.split()
		if len(tokens) < 3:
			continue

		# 第一个 token 必须是日期（dd-mm-yy / dd/mm/yy）
		if not _DATE_PREFIX.match(tokens[0]):
			continue
		tx_date = parse_date(tokens[0])
		if tx_date is None:
			continue

		# 把连续的数字 token 分组，取**最右边**那个"至少两个数"的组：摘要里的数字
		# （公司名、支票号）因此不会把真正的"金额+余额"对挤掉 —— 它们总是相邻的。
		groups: List[List[Tuple[int, float]]] = []
		current: List[Tuple[int, float]] = []
		for index in range(1, len(tokens)):
			value = token_to_float(tokens[index])
			if value is not None:
				current.append((index, value))
			else:
				if current:
					groups.append(current)
					current = []
		if current:
			groups.append(current)
		number_values: List[Tuple[int, float]] = []
		for group in reversed(groups):
			if len(group) >= 2:
				number_values = group
				break

		if len(number_values) < 2:
			continue

		balance = number_values[-1][1]
		amount = abs(number_values[-2][1])
		if amount == 0.0:
			continue

		description_end = number_values[-2][0]
		description = " ".join(tokens[1:description_end]).strip()

		# 方向：先看余额差，差额对不上再看描述里的动词，最后按存款
		withdrawal = 0.0
		deposit = 0.0
		if prev_balance is not None:
			diff = round(balance - prev_balance, 2)
			if abs(diff - amount) <= AMOUNT_TOL:
				deposit = amount
			elif abs(diff + amount) <= AMOUNT_TOL:
				withdrawal = amount
			else:
				lowered = description.lower()
				if any(keyword in lowered
						for keyword in ["จ่าย", "ถอน", "debit", "withdraw", "โอนออก", "ชำระ"]):
					withdrawal = amount
				else:
					deposit = amount
		else:
			deposit = amount

		prev_balance = balance
		closing = balance

		rows.append(
			StatementRow(
				date=tx_date,
				description=description,
				withdrawal=withdrawal,
				deposit=deposit,
				balance=balance,
			)
		)

	# 没有期初标记：用首笔反推（余额 − 存 / 余额 + 取）
	if not opening and rows:
		first = rows[0]
		if first.deposit > 0:
			opening = round(first.balance - first.deposit, 2)
		elif first.withdrawal > 0:
			opening = round(first.balance + first.withdrawal, 2)

	if not closing and rows:
		closing = rows[-1].balance

	return rows, opening, closing


def parse_kbank_text_columns(text: str) -> Tuple[List[StatementRow], float, float]:
	"""KBank 列被压成行的文字层 → ``(rows, opening, closing)``。

	每笔交易的形状是：日期行 / 时间行 / 摘要行；金额与余额在后面的值块里交替出现
	（值单独一行，余额那行右面带渠道文字）。所以按位置把交易头与 (金额, 余额) 配对。
	"""
	lines = [line.strip() for line in text.splitlines() if line.strip()]

	date_re = re.compile(r"^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$")
	time_re = re.compile(r"^\d{1,2}:\d{2}$")

	# 表头从**第一行日期**开始；值块从最后一个"รายละเอียด"列头之后开始（好跳过开头那
	# 三个汇总数：期末余额、取款合计、存款合计）
	header_start = 0
	for index, line in enumerate(lines):
		if date_re.match(line):
			header_start = index
			break
	value_start = header_start
	for index, line in enumerate(lines):
		if "รายละเอียด" in line and index > 0:
			value_start = index + 1
			break

	data = lines[header_start:]
	value_lines = lines[value_start:]

	def is_number(line: str) -> bool:
		raw = line.replace(",", "").replace(" ", "").lstrip("-")
		if not raw:
			return False
		if raw.count(".") > 1:
			last = raw.rfind(".")
			raw = raw[:last].replace(".", "") + raw[last:]
		try:
			float(raw)
			return True
		except ValueError:
			return False

	# 第一阶段：按顺序抽交易头（日期 / 时间 / 摘要）
	headers = []
	index = 0
	while index < len(data):
		if date_re.match(data[index]):
			header_date = parse_date(data[index])
			if header_date is None:
				index += 1
				continue
			time_text = ""
			description = ""
			following = index + 1
			if following < len(data) and time_re.match(data[following]):
				time_text = data[following]
				following += 1
			if following < len(data) and not date_re.match(data[following]):
				first_token = data[following].split()[0] if data[following].split() else ""
				if not is_number(first_token):
					description = data[following]
					following += 1
			headers.append({"date": header_date, "time": time_text, "desc": description})
			index = following
		else:
			index += 1

	if not headers:
		return [], 0.0, 0.0

	# 第二阶段：把值块读成 "纯数字行" 与 "数字+渠道" 两种
	values = []
	for line in value_lines:
		tokens = line.split()
		if not tokens:
			continue
		value = to_float(tokens[0])
		if value is None:
			continue
		if len(tokens) == 1:
			values.append(("num", value, ""))
		else:
			values.append(("bal", value, " ".join(tokens[1:])))

	# 第三阶段：先期初，再交替取"金额 / 余额"
	opening = 0.0
	pairs = []
	state = "opening"
	current_amount = None
	for kind, value, rest in values:
		if state == "opening":
			if kind == "num":
				opening = value
				state = "amt"
		elif state == "amt":
			if kind == "num":
				current_amount = value
				state = "bal"
		elif state == "bal":
			if kind == "bal":
				pairs.append((current_amount, value, rest))
				current_amount = None
				state = "amt"
			elif kind == "num":
				current_amount = value

	# 第四阶段：第一个交易头没有时间 = 那是期初那行的日期，去掉
	if headers and not headers[0]["time"]:
		headers = headers[1:]

	# 第五阶段：按位置把交易头与 (金额, 余额) 逐对合起来
	rows: List[StatementRow] = []
	prev_balance = opening
	count = min(len(headers), len(pairs))
	for index in range(count):
		header = headers[index]
		amount, balance, channel = pairs[index]
		if amount is None or balance is None:
			continue
		diff = round(balance - prev_balance, 2)
		withdrawal = 0.0
		deposit = 0.0
		if abs(diff - amount) <= AMOUNT_TOL:
			deposit = amount
		elif abs(diff + amount) <= AMOUNT_TOL:
			withdrawal = amount
		else:
			# 差额对不上：先用描述与渠道的动词，再退回"余额涨就是存款"
			text_blob = (header["desc"] + " " + channel).lower()
			if ("รับโอน" in text_blob) or ("ฝาก" in text_blob and "ฝากด้วยเช็ค" not in text_blob):
				deposit = amount
			elif ("โอนเงิน" in text_blob) or ("ถอน" in text_blob) or ("จ่าย" in text_blob):
				withdrawal = amount
			else:
				if diff > 0:
					deposit = amount
				else:
					withdrawal = amount
		prev_balance = balance
		description = header["desc"]
		if channel:
			description = (description + " " + channel).strip()
		rows.append(
			StatementRow(
				date=header["date"],
				description=description,
				withdrawal=withdrawal,
				deposit=deposit,
				balance=balance,
			)
		)

	closing = rows[-1].balance if rows else 0.0
	return rows, opening, closing


# ── 表头上标着的账号（对账预设按账户记）──────────────────────────────────────────

# 抽字时泰文声调符号常丢掉或断开（pdfminer 把「เลขที่บัญชี」抽成「เลขที\n\n\x00บัญชี」，pypdf 抽成「เลขที บัญชี」）：
# 认标签前先去掉 \x00 与声调符号、泰文数字换成阿拉伯数字；泰文标签的字与字之间允许空白（含断行）。
_HEADER_CLEAN = str.maketrans({"\x00": None, "\u0e48": None, "\u0e49": None, "\u0e4a": None, "\u0e4b": None,
	**{chr(0x0E50 + digit): str(digit) for digit in range(10)}})
_THAI_LABELS = ("เลขทีบัญชี", "หมายเลขบัญชี", "บัญชีเลขที", "เลขบัญชี")  # 声调符号已去掉：ที่ → ที
_ACCOUNT_LABEL = "|".join([r"\s*".join(word) for word in _THAI_LABELS]
	+ [r"(?:account|acct|acc|a\s*/\s*c)\.?\s*(?:no\b\.?|number\b|#)", r"\ba\s*/\s*c\b"])
_MASK = "Xx*•"
# 号码紧跟在标签后面，中间只有空白与冒号、点、括号这类分隔符；号码整段取完（后面不能还连着数字、遮挡符或字）。
_LABELLED_NUMBER = re.compile(r"(?:" + _ACCOUNT_LABEL + r")[\s:：.#()（）\[\]\-–]*"
	r"(?P<number>[0-9" + _MASK + r"]+(?:[-–][0-9" + _MASK + r"]+)*)(?![\w*•/\-–])", re.I)
# 流水表从第一行以日期开头、或带金额（两位小数）的行开始；账号只在它前面的表头里找。
_TABLE_LINE = re.compile(r"^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}(?!\d)"
	r"|(?<![\d.,])\d{1,3}(?:,\d{3})+\.\d{2}(?![\d.])|(?<![\d.,])\d+\.\d{2}(?![\d.])")
# 账号的位数（泰国各行 10–12 位）：短的多半是页码、日期、尾号，长的是卡号或参考号。
ACCOUNT_DIGITS = (8, 16)


def labelled_account(page_text: str) -> str:
	"""PDF 文字层第一页表头上标着「เลขที่บัญชี / หมายเลขบัญชี / บัญชีเลขที่ / เลขบัญชี / Account No. / A/C No.」的账号
	（照印出的写法，如 ``305-1-77420-9``）；认不出回空。

	只认：第一页（``\\f`` 之前）、流水表之前（``_TABLE_LINE``）、紧跟在标签后面的完整号码。遮挡的号码（xxx-x-x4209-x）、
	位数不像账号的、隔着别的字的（电话、参考号、流水行里遮挡的 PromptPay 号）都不算；表头上标着两个不同的号也不算（分不清）。
	"""
	header = []
	for line in str(page_text or "").split("\f")[0].translate(_HEADER_CLEAN).splitlines():
		if _TABLE_LINE.search(line.strip()):
			break
		header.append(line)
	found = {}
	for match in _LABELLED_NUMBER.finditer("\n".join(header)):
		number = match.group("number")
		digits = re.sub(r"\D", "", number)
		if any(mark in number for mark in _MASK) or not ACCOUNT_DIGITS[0] <= len(digits) <= ACCOUNT_DIGITS[1]:
			continue
		found.setdefault(digits, number)
	return next(iter(found.values())) if len(found) == 1 else ""


# ── 表头上印着的银行名（结果、导出、CC 回答里写的银行名）──────────────────────────

# 表头一行里几栏之间隔着两个以上空格、制表符或「 / 」（「ธนาคารสาธิต / SATHIT DEMO BANK」）；逐栏看。
_HEADER_COLUMNS = re.compile(r"\s{2,}|\t|\s+/\s+|\s*\|\s*")
# 「ธนาคาร…」开头的一栏：后面紧跟名字（不是冒号）。表头里也会印「ธนาคารผู้รับ / ธนาคารปลายทาง」这类栏名，不算。
_THAI_BANK = re.compile(r"^ธนาคาร(?![\s:：])(?!ผู้|ผู|ปลายทาง|ต้นทาง|คู่|คู|และ)\S")
# 「ธนาคาร: X」「ชื่อธนาคาร: X」「Bank: X」「Bank Name: X」：取冒号后面印的。
_BANK_LABEL = re.compile(r"^(?:ชื่อ\s*ธนาคาร|ชื\s*อ\s*ธนาคาร|ธนาคาร|bank(?:\s+name)?)\s*[:：]\s*(?P<name>\S.*)$", re.I)
# 英文名：只有字母与常见标点、带「bank」一词（KASIKORNBANK、Bangkok Bank、Bank of Ayudhya）；账单标题、栏名不算。
_ENGLISH_BANK = re.compile(r"^[A-Za-z][A-Za-z.,&'()\- ]{1,78}$")
_ENGLISH_NOT_A_NAME = re.compile(r"\b(?:statement|account|branch|transfer|deposits?|withdrawals?|balance|to|from|"
	r"date|page|currency)\b", re.I)
BANK_NAME_LIMIT = 80


def _bank_name_in(column: str) -> str:
	label = _BANK_LABEL.match(column)
	if label:
		return label.group("name").strip()
	if _THAI_BANK.match(column):
		# 「ธนาคารกสิกรไทย สาขาสีลม」只要银行名
		return re.split(r"\s*\(?สาขา", column, maxsplit=1)[0].strip()
	if (_ENGLISH_BANK.match(column) and re.search(r"bank\b", column, re.I)
			and column.lower() not in ("bank", "bank name") and not _ENGLISH_NOT_A_NAME.search(column)):
		return column
	return ""


def printed_bank_name(page_text: str) -> str:
	"""PDF 文字层第一页表头上印着的银行名（照印出的写法，如 ``ธนาคารสาธิต``）；认不出回空，不按账号格式或流水里的对方行猜。

	与 `labelled_account` 同一块表头：第一页（``\\f`` 之前）、流水表之前（``_TABLE_LINE``），从上往下第一个像银行名的栏：
	「ธนาคาร…」开头的、「ธนาคาร / Bank (Name):」标签后面的、带「bank」一词的英文名。流水行里的对方银行、页脚都不看。
	"""
	for raw in str(page_text or "").split("\f")[0].replace("\x00", "").splitlines():
		if _TABLE_LINE.search(raw.translate(_HEADER_CLEAN).strip()):
			break
		for column in _HEADER_COLUMNS.split(raw.strip()):
			name = _bank_name_in(" ".join(column.split()))
			if name:
				return name[:BANK_NAME_LIMIT]
	return ""
