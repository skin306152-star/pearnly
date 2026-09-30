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

import re
from datetime import date, datetime, timedelta
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
# 代扣税 / 手续费另列一栏、余额按「金额 − 税」走（KTB 网银 .xls：Amount | Tax | Balance；2026-09-30 压测 05-银行导出样本）。
_STMT_TAX_H = {"tax", "ภาษี", "หัก ณ ที่จ่าย", "withholding", "税"}
# 旧词典之外补认的栏名（旧词典与部署版逐字对照，不能改）：KBank .xls「จำนวนเงินหักบัญชี / นำฝากเข้าบัญชี」、
# 手工表「Withdraw」、Krungsri「ยอดเงินในบัญชี」（2026-09-30 压测 05-银行导出样本）。
_STMT_WITHDRAW_MORE = {"หักบัญชี", "withdraw"}
_STMT_DEPOSIT_MORE = {"เข้าบัญชี", "นำฝาก"}
_STMT_BALANCE_MORE = {"ยอดเงินในบัญชี"}  # Krungsri 网银 .xls（实为 xlsx）

_STMT_ACCT_LABEL = ("account no", "account number", "เลขที่บัญชี", "บัญชีเงินฝาก", "账号",
	"账户", "口座")

_STMT_OPEN_KW = ("ยอดยกมา", "ยกยอด", "ยกมา", "brought forward", "balance b/f", "b/f",
	"opening", "期初", "上期")

# 期初行识别里额外认的措辞（ยกยอด 是"承前结转"的另一种写法，曾漏掉→被当存款计入）
_STMT_OPEN_ROW_KW = ("ยอดยกมา", "ยกยอด", "ยกมา", "brought forward", "opening", "期初")

PARSER_VERSION = "bank_recon_v2+xlsx_direct_v3"

# 表格有日期列的表头、却没有余额列：不是银行导出的账单（待查询清单、收款明细之类）。
NOT_A_STATEMENT = "not_a_statement"
# 会计软件导出的总账（分类账）放进了账单口（2026-09-30 压测 05-银行导出样本：MANACHAI / ASIA SPORT 等二十几份）。
LEDGER_NOT_STATEMENT = "ledger_not_statement"

# 网银导出的金额带币种：「-1,234.00 THB」（BBL 网银新版，扣款印负号）。
_CURRENCY_RE = re.compile(r"\s*(THB|USD|CNY|RMB|EUR|JPY|บาท)\s*$", re.IGNORECASE)
# 账单上印着「没有记录」：这个期间没有流水（ICBC、SCB 网银实测），不是读不出。
_NO_DATA_RE = re.compile(r"没有满足条件的记录|no\s+data\s+available|no\s+(?:transactions?|records?)\s+found|ไม่พบรายการ"
	r"|ไม่มีรายการเคลื่อนไหว", re.IGNORECASE)
# 总账的表头：有「簿 / 凭证号」一类的列，又有借贷两列；或标题写着分类账。
_LEDGER_BOOK_H = ("สมุด", "ใบสำคัญ", "เลขที่เอกสาร", "voucher", "journal", "凭证")
_LEDGER_DRCR_H = (("เดบิต", "เดบิท", "debit", "借方"), ("เครดิต", "credit", "贷方"))
_LEDGER_TITLE = ("แยกประเภท", "general ledger", "总账", "分类账")


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
		elif "withdrawal" not in col_map and (table_io.header_hit(header, _STMT_WITHDRAW_H)
				or table_io.header_hit(header, _STMT_WITHDRAW_MORE)):
			col_map["withdrawal"] = index
		elif "deposit" not in col_map and (table_io.header_hit(header, _STMT_DEPOSIT_H)
				or table_io.header_hit(header, _STMT_DEPOSIT_MORE)):
			col_map["deposit"] = index
		elif "balance" not in col_map and (table_io.header_hit(header, _STMT_BALANCE_H)
				or table_io.header_hit(header, _STMT_BALANCE_MORE)):
			col_map["balance"] = index
		elif "amount" not in col_map and table_io.header_hit(header, _STMT_AMOUNT_H):
			col_map["amount"] = index
		elif "tax" not in col_map and table_io.header_hit(header, _STMT_TAX_H):
			col_map["tax"] = index
	return col_map


def _amount(cell_value) -> Tuple[float, bool]:
	"""格子 → ``(金额, 是否带币种)``；带币种的（网银导出）扣款印负号，调用方按绝对值取。"""
	text = str(cell_value or "").strip()
	stripped = _CURRENCY_RE.sub("", text)
	return to_float(stripped), stripped != text


def _serial_date(cell_value) -> Optional[date]:
	"""Excel 的日期序号（旧 .xls 读出来是 46113.0 这样的数）→ 日期；不是数字、不在 1970–2119 之间回 None。"""
	if isinstance(cell_value, bool) or not isinstance(cell_value, (int, float)):
		return None
	if not 25569 <= cell_value < 80000:
		return None
	return date(1899, 12, 30) + timedelta(days=int(cell_value))


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


_MONEY_CELL = re.compile(r"^-?[\d,]*\d\.\d{2}$")
_FORWARD_KW = ("b/f", "balance forward", "brought forward", "ยอดยกมา", "ยกมา")


_CLOCK_RE = re.compile(r"\d\s+(\d{1,2}):(\d{2})(?::(\d{2}))?")


def _clock_of(cell_value) -> Optional[Tuple[int, int, int]]:
	"""日期格里印的时刻（「25/06/2026 16:40:12」或带时间的日期值）；没印时刻回 None。"""
	if isinstance(cell_value, datetime):
		clock = (cell_value.hour, cell_value.minute, cell_value.second)
		return clock if any(clock) else None
	found = _CLOCK_RE.search(str(cell_value or ""))
	return (int(found.group(1)), int(found.group(2)), int(found.group(3) or 0)) if found else None


def _date_of(cell_value) -> Optional[date]:
	text = " ".join(str(cell_value or "").split())  # 「25/04/2026\n00:00:00」：Krungsri 日期与时间之间是换行
	return (parse_date(text) if text else None) or _serial_date(cell_value)


def headerless_rows(raw_rows, filename: str = "") -> Optional[Tuple[List[StatementRow], float]]:
	"""没有表头行的网银导出（Krungsri「StatementInquiry」CSV：日期时间、空、金额、余额、交易码、摘要、渠道、网点；
	2026-09-30 压测 05-银行导出样本 SISTER）按列的位置认：日期在第一列、恰好两列每行都是两位小数的金额——左边是发生额、
	右边是余额（金额那栏空着时取余额左边其他有数的栏）；存还是取按余额的升降定。第一行没有日期、印着承前余额（B/F）的是期初。

	只在认得非常确定时用：带日期的行至少 3 行，且九成五以上的行「上一行余额 ± 金额 = 这一行余额」，否则回 None。
	返回 ``(行, 期初)``。"""
	dated = [row for row in raw_rows if row and _date_of(row[0])]
	if len(dated) < 3:
		return None
	width = max(len(row) for row in dated)
	money_cols = [index for index in range(1, width)
		if sum(1 for row in dated if index < len(row) and _MONEY_CELL.match(str(row[index] or "").strip())) >= 0.9 * len(dated)]
	if len(money_cols) != 2:
		return None
	amount_idx, balance_idx = money_cols
	# 余额左边偶尔有金额的其他列：同一份 Krungsri CSV 的整数额转出记在前一栏（2026-09-30 实测 8 笔）
	spare_cols = [index for index in range(1, balance_idx) if index != amount_idx
		and any(index < len(row) and _MONEY_CELL.match(str(row[index] or "").strip()) for row in dated)]
	opening = None
	for row in raw_rows:
		if row and not _date_of(row[0]) and any(keyword in " ".join(str(cell_value or "") for cell_value in row).lower()
				for keyword in _FORWARD_KW):
			value = next((to_float(cell_value) for cell_value in row if _MONEY_CELL.match(str(cell_value or "").strip())), None)
			if value is not None:
				opening = value
				break
	texts = [index for index in range(1, width) if index not in money_cols]
	description_idx = max(texts, key=lambda index: sum(len(str(row[index] or "")) for row in dated if index < len(row)),
		default=-1)
	rows, previous, consistent = [], opening, 0
	for row in dated:
		amount, balance = to_float(row[amount_idx]), to_float(row[balance_idx])
		if not amount:
			amount = next((to_float(row[index]) for index in spare_cols
				if index < len(row) and to_float(row[index])), 0.0)
		if previous is None:
			previous = round(balance - amount, 2)  # 没有承前余额：假定第一笔是存入，靠下面的一致性把关
			opening = previous
		deposit = amount if balance >= previous else 0.0
		withdrawal = amount if balance < previous else 0.0
		if abs(abs(balance - previous) - abs(amount)) <= 0.01:
			consistent += 1
		rows.append(StatementRow(date=_date_of(row[0]), description=str(row[description_idx] or "").strip()
			if 0 <= description_idx < len(row) else "", withdrawal=abs(withdrawal), deposit=abs(deposit),
			balance=balance, source_file=filename))
		previous = balance
	if consistent < 0.95 * len(rows):
		return None
	return rows, opening


def _short_buddhist_dates(rows) -> None:
	"""客户手打「6/1/69」（佛历 2569 年 1 月 6 日）被 Excel 按美式月/日存成 1969-06-01 的日期格：
	按佛历短年份换回公历，再在「日/月对调」与「不对调」两种里取夹在前后正常日期之间的那个；
	都夹不住就原样留着（2026-09-30 压测 Mrs.Flowers BBLMonthly）。同一个错日期（含沿用它的空日期行）一起改。"""
	good = [(index, row.date) for index, row in enumerate(rows) if row.date and row.date.year >= 2000]
	if not good:
		return
	ascending = good[0][1] <= good[-1][1]
	fixed: Dict[date, Optional[date]] = {}
	for index, row in enumerate(rows):
		value = row.date
		if value is None or not 1940 <= value.year < 1990:
			continue
		if value not in fixed:
			year = 2500 + value.year % 100 - 543
			earlier = next((day for position, day in reversed(good) if position < index), None)
			later = next((day for position, day in good if position > index), None)
			if not ascending:
				earlier, later = later, earlier
			fixed[value] = None
			for month, day in ((value.day, value.month), (value.month, value.day)):
				try:
					candidate = date(year, month, day)
				except ValueError:
					continue
				if (earlier is None or candidate >= earlier) and (later is None or candidate <= later):
					fixed[value] = candidate
					break
		if fixed[value] is not None:
			row.date = fixed[value]


def _chain_breaks(rows) -> int:
	return sum(1 for previous, row in zip(rows, rows[1:])
		if previous.balance is not None and row.balance is not None
		and abs(previous.balance + (row.deposit or 0) - (row.withdrawal or 0) - row.balance) > 0.01)


def _oldest_first(rows) -> Tuple[List[StatementRow], bool]:
	"""新的在前（日期从新到旧）、且倒过来余额链明显更顺的表，翻成从旧到新（2026-09-30 压测 05-银行导出样本：BBL 网银新版、
	Mrs.Flowers、Sangjit 等按首行反推期初，推出来的是期末附近的数，整份对不上）。"""
	dated = [row.date for row in rows if row.date]
	if len(rows) < 2 or len(dated) < 2 or dated[0] < dated[-1]:
		return rows, False
	backwards = rows[::-1]
	if dated[0] == dated[-1]:
		# 首末同一天（BBL 网银当天「利息 + 扣税」两行，同一秒入账）：格子里印了时刻、时刻没往后走、
		# 且倒过来整条接上才翻；没印时刻的表不猜
		clocks = [getattr(row, "clock", None) for row in rows]
		if None not in clocks and clocks[0] >= clocks[-1] and _chain_breaks(rows) and not _chain_breaks(backwards):
			return backwards, True
		return rows, False
	if _chain_breaks(backwards) < _chain_breaks(rows):
		return backwards, True
	return rows, False


def _chain_order(rows) -> Tuple[List[StatementRow], bool]:
	"""行序被打乱、但余额链本身完整的表，按「上一行余额 ± 本行金额 = 本行余额」重新接成一条。

	KTB 网银 .xls 把支票类（CBCA）按支票日期插在别处，余额却是入账时的：按表上顺序断十几处，
	按余额接起来一处不断（2026-09-30 压测 05-银行导出样本 Asia Bangna 8606）。
	只在 ≥10 行、全部有余额、重排后**每一行都接上**时采用；同一余额有几行可接时按原表顺序取先出现的。"""
	if len(rows) < 10 or any(row.balance is None for row in rows) or not _chain_breaks(rows):
		return rows, False

	def before(row):
		return round(row.balance - (row.deposit or 0) + (row.withdrawal or 0), 2)

	starts: Dict[float, List[int]] = {}
	for index, row in enumerate(rows):
		starts.setdefault(before(row), []).append(index)
	balances = {round(row.balance, 2) for row in rows}
	heads = [index for index, row in enumerate(rows) if before(row) not in balances]
	head = heads[0] if len(heads) == 1 else 0
	used = [False] * len(rows)
	used[head] = True
	order = [head]
	while len(order) < len(rows):
		following = next((index for index in starts.get(round(rows[order[-1]].balance, 2), ()) if not used[index]), None)
		if following is None:
			return rows, False
		used[following] = True
		order.append(following)
	chained = [rows[index] for index in order]
	return (chained, True) if not _chain_breaks(chained) else (rows, False)


def _header_row_opening(header_row) -> Optional[float]:
	"""表头那一行里印着承前余额（KBank 网银 .xls：「… ยอดยกมา | 441,243.73」）。"""
	cells = [str(cell_value or "").strip() for cell_value in header_row]
	for index, cell_text in enumerate(cells):
		if any(keyword in cell_text.lower() for keyword in _STMT_OPEN_KW):
			for value in cells[index + 1:]:
				if value and (to_float(value) or value in ("0", "0.00", "0.0")):
					return to_float(value)
	return None


def looks_like_ledger(raw_rows) -> bool:
	"""前 16 行像总账：有「簿 / 凭证号」列又有借贷两列，或标题写着分类账。"""
	for row in raw_rows[:16]:
		cells = [str(cell_value or "").strip().lower() for cell_value in row]
		joined = " ".join(cells)
		if any(title in joined for title in _LEDGER_TITLE):
			return True
		if (any(any(word in cell_text for word in _LEDGER_BOOK_H) for cell_text in cells)
				and all(any(any(word in cell_text for word in words) for cell_text in cells) for words in _LEDGER_DRCR_H)):
			return True
	return False


def printed_no_data(raw_rows) -> bool:
	return any(_NO_DATA_RE.search(str(cell_value or "")) for row in raw_rows[:40] for cell_value in row)


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


def _text_money_columns(data_rows, col_map: Dict[str, int]) -> Dict[str, int]:
	"""存 / 取栏里一个数都没有、却有 ≥5 格文字的，那栏其实是摘要（客户自制月表把说明写在「Withdraw」栏、
	金额写在「Amount」栏；2026-09-30 压测 Mrs.Flowers BBLMonthly）：改认成摘要，金额栏接手。"""
	fixed = dict(col_map)
	for key in ("withdrawal", "deposit"):
		index = fixed.get(key, -1)
		if index < 0 or "amount" not in fixed:
			continue
		cells = [str(raw[index] or "").strip() for raw in data_rows if index < len(raw)]
		numbers = sum(1 for text in cells if text and to_float(text))
		texts = sum(1 for text in cells if text and not to_float(text) and text not in ("-", "0"))
		if not numbers and texts >= 5:
			del fixed[key]
			fixed.setdefault("description", index)
	return fixed


# 表尾合计的写法（旧合计词典之外）：Krungsri「ถอนเงินรวม / ฝากเงินรวม」
_FOOTER_MORE = ("ถอนเงินรวม", "ฝากเงินรวม")


def _footer_text(text: str) -> bool:
	return is_summary_row(text) or any(keyword in text for keyword in _FOOTER_MORE)


def _sheet_conventions(data_rows, col_map: Dict[str, int]) -> Tuple[bool, int, bool]:
	"""整张表的印法：``(扣款栏印负号, 表尾从第几行开始 or -1, 没日期没余额的金额行是小计)``。

	都按整张表判，门槛取到部署版对照样本碰不到的量，不按单行猜：
	- 取款栏的数只有负数、没有正数，且余额链证明按绝对值才接得上：银行把扣款印成负号
	  （BBL 网银 .xlsx 实测，一张表可能只有 1–3 笔扣款）；
	- ≥10 行带金额的流水、首末两笔之间每笔都印了日期：最后一笔之后没日期的行是表尾
	  （合计、总存取、「ยอดคงเหลือ」小结、客户加的 VAT 小计），不能沿用上一行日期记成流水，
	  也不能把表尾单独一格的数当期初（KBank / BBL 网银、客户整理的月表实测）。
	  按日只印一次日期的银行（同日后几笔空着）中间就有没日期的流水，这条不启用；
	- ≥10 笔流水印了余额、带日期的每笔都有余额：夹在中间没日期也没余额的金额行是月小计
	  （客户自制多月表每月底一行存取合计，Mrs.Flowers BBLMonthly 实测）。
	"""
	withdrawal_idx = col_map.get("withdrawal", -1)
	deposit_idx = col_map.get("deposit", -1)
	amount_idx = col_map.get("amount", -1)
	balance_idx = col_map.get("balance", -1)
	date_idx = col_map["date"]
	negative = positive = balanced = dated_unbalanced = 0
	chain_dates: List[Optional[date]] = []
	chain = []  # (取款栏原值, 存款, 余额)：带余额的行
	moves = []  # (行号, 有没有日期)：带金额的行
	for index, raw in enumerate(data_rows):
		values = [str(cell_value or "").strip() for cell_value in raw]
		withdrawal = _amount(values[withdrawal_idx])[0] if 0 <= withdrawal_idx < len(values) else 0.0
		deposit = _amount(values[deposit_idx])[0] if 0 <= deposit_idx < len(values) else 0.0
		negative += withdrawal < 0
		positive += withdrawal > 0
		amount = _amount(values[amount_idx])[0] if 0 <= amount_idx < len(values) else 0.0
		has_balance = 0 <= balance_idx < len(values) and bool(values[balance_idx])
		if withdrawal or deposit or amount:
			has_date = 0 <= date_idx < len(raw) and _date_of(raw[date_idx]) is not None
			moves.append((index, has_date))
			balanced += has_balance
			dated_unbalanced += has_date and not has_balance
		if has_balance:
			chain.append((withdrawal, deposit, _amount(values[balance_idx])[0]))
			chain_dates.append(_date_of(raw[date_idx]) if 0 <= date_idx < len(raw) else None)
	# 上一笔余额在哪一侧按日期定（旧→新看上一行，新→旧看下一行）；只看余额链分不出「负号扣款」和「倒序存入」
	dates = [day for day in chain_dates if day]
	sides = (-1,) if dates and dates[0] < dates[-1] else (1,) if dates and dates[0] > dates[-1] else (-1, 1)
	signed_fit = absolute_fit = 0
	for position, (withdrawal, deposit, balance) in enumerate(chain):
		if withdrawal >= 0:
			continue
		neighbours = [chain[position + side][2] for side in sides if 0 <= position + side < len(chain)]
		signed_fit += any(abs(other + deposit - withdrawal - balance) <= 0.01 for other in neighbours)
		absolute_fit += any(abs(other + deposit + withdrawal - balance) <= 0.01 for other in neighbours)
	debits_negative = negative > 0 and not positive and absolute_fit > signed_fit
	footer_from = -1
	dated = [index for index, has_date in moves if has_date]
	if len(dated) >= 10 and all(has_date for index, has_date in moves if dated[0] <= index <= dated[-1]):
		footer_from = dated[-1] + 1
	return debits_negative, footer_from, balanced >= 10 and not dated_unbalanced


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
	col_map = _text_money_columns(raw_rows[header_idx + 1:], col_map)
	date_idx = col_map["date"]
	balance_idx = col_map.get("balance", -1)  # 学习层映射可能没有余额列，缺了按 0（不崩）
	withdrawal_idx = col_map.get("withdrawal", -1)
	deposit_idx = col_map.get("deposit", -1)
	amount_idx = col_map.get("amount", -1)
	description_idx = col_map.get("description", -1)

	def cell(row_list, index):
		return row_list[index] if 0 <= index < len(row_list) else ""

	debits_negative, footer_from, subtotal_rows = _sheet_conventions(raw_rows[header_idx + 1:], col_map)
	for position, raw in enumerate(raw_rows[header_idx + 1:]):
		if 0 <= footer_from <= position:
			break  # 表尾：合计 / 小结 / 客户加的税额小计
		if not any(raw):
			continue
		row_list = [str(cell_value or "").strip() for cell_value in raw]
		date_text = cell(row_list, date_idx)
		parsed_date = parse_date(date_text) if date_text else None
		if parsed_date is None and 0 <= date_idx < len(raw):
			# 旧 .xls 的日期是序号（KTB 网银实测）；日期与时间之间换行（Krungsri 实测）
			parsed_date = _date_of(raw[date_idx])
		if parsed_date is not None:
			last_date = parsed_date
		balance = _amount(cell(row_list, balance_idx))[0]
		withdrawal, tagged = _amount(cell(row_list, withdrawal_idx)) if withdrawal_idx >= 0 else (0.0, False)
		if tagged or debits_negative:
			withdrawal = abs(withdrawal)  # 「-999.99 THB」/ BBL「-1600000」：网银导出的扣款印负号
		deposit, tagged = _amount(cell(row_list, deposit_idx)) if deposit_idx >= 0 else (0.0, False)
		if tagged:
			deposit = abs(deposit)
		# 单一带符号金额列（没有独立存/取列）：正=存款、负=取款
		if amount_idx >= 0 and withdrawal_idx < 0 and deposit_idx < 0:
			amount = _amount(cell(row_list, amount_idx))[0]
			if amount > 0:
				deposit = amount
			elif amount < 0:
				withdrawal = abs(amount)
		elif amount_idx >= 0 and (withdrawal_idx < 0) != (deposit_idx < 0):
			# 只认出一侧时「金额」栏就是另一侧（KBank：จำนวนเงินหักบัญชี 旧词典只命中「จำนวนเงิน」）
			amount = abs(_amount(cell(row_list, amount_idx))[0])
			if withdrawal_idx < 0:
				withdrawal = amount
			else:
				deposit = amount
		description = cell(row_list, description_idx)
		if subtotal_rows and parsed_date is None and not cell(row_list, balance_idx) and (withdrawal or deposit):
			continue  # 月小计：没日期、没余额、只有存取合计

		# 汇总/合计行整行跳过：它的余额常是大额合计，会被当成期末污染余额链
		# （真实小现金件 เงินสดย่อย 实测）。必须在这里跳，不能等到外层事后过滤。
		if description and is_summary_row(description):
			continue
		if parsed_date is None and any(_footer_text(text) for position_text, text in enumerate(row_list)
				if position_text != description_idx and text and not to_float(text)):
			break  # 合计写在别的栏（ICBC 网银「合计金额」在序号栏）：表到此为止，下面是客户加的核对数

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
		tax = abs(_amount(cell(row_list, col_map["tax"]))[0]) if "tax" in col_map else 0.0
		clock = _clock_of(raw[date_idx]) if 0 <= date_idx < len(raw) else None
		rows.append(StatementRow(
			date=parsed_date if parsed_date is not None else last_date,
			description=description,
			withdrawal=withdrawal,
			deposit=deposit,
			balance=round(balance + tax, 2) if tax else balance,
			source_file=filename,
			account_no=account_no,
		))
		if tax:
			# 代扣税另记一笔支出（总账那边也是单独一笔）：余额按表上印的（已扣税）。
			rows.append(StatementRow(date=parsed_date if parsed_date is not None else last_date,
				description=f"Tax · {description}".strip(" ·"), withdrawal=tax, deposit=0.0, balance=balance,
				source_file=filename, account_no=account_no))
		for row in rows[-2 if tax else -1:]:
			row.clock = clock  # 只给同日先后判断用，不进结果
		# "余额真的是 0"（Sweep 归零户的合法期末）与"余额单元格空着"必须分开：
		# row_list 已经把数值 0.0 变成 ""（0.0 是假值），所以这里看**原始单元格**判空。
		raw_balance_cell = raw[balance_idx] if 0 <= balance_idx < len(raw) else None
		if raw_balance_cell is not None and str(raw_balance_cell).strip() != "":
			last_valid_closing = balance
	return rows, opening_balance, last_valid_closing


def _sheet_blocks(sheets):
	"""一张 sheet 里流水之后又出现一行表头：下面是另一段（客户自制工作簿把另一年的数据接在同一张表下面，
	2026-09-30 压测 Mrs.Flowers BBLMonthly），拆成独立的段各自核余额链；接得上的段由 `_join_periods` 再合回去。"""
	for sheet_name, raw_rows in sheets:
		header_idx, col_map = find_statement_header(raw_rows)
		cuts: List[int] = []
		dated = False
		for index in range(header_idx + 1, len(raw_rows)) if col_map else ():
			row = raw_rows[index]
			mapped = map_statement_columns([str(cell_value or "").strip() for cell_value in row])
			if dated and "date" in mapped and "balance" in mapped \
					and ("withdrawal" in mapped or "deposit" in mapped or "amount" in mapped):
				cuts.append(index)
				dated = False
			elif col_map["date"] < len(row) and _date_of(row[col_map["date"]]) is not None:
				dated = True
		starts = [0] + cuts
		for start, end in zip(starts, cuts + [len(raw_rows)]):
			yield sheet_name, raw_rows[start:end]


def _join_periods(accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
	"""同一账户按月分 sheet（sheet 名是月份 / 年份，不是账号）：前一个 sheet 的期末就是后一个的期初、日期接得上，
	合成一个账户——期初取第一个、期末取最后一个，不按多账户把几十个月的期初期末加总
	（2026-09-30 压测 Sangjit 50 个月份 sheet、Mrs.Flowers 多月表）。两边印着不同的真实账号（6 位以上数字）不合；
	合起来少于 20 行也不合。"""
	def number(account):
		digits = "".join(ch for ch in str(account["account_no"] or "") if ch.isdigit())
		return digits if len(digits) >= 6 else ""

	joined: List[Dict[str, Any]] = []
	for account in accounts:
		previous = joined[-1] if joined else None
		if (previous is not None and account["opening_known"] and previous["closing_known"]
				and abs(account["opening"] - previous["closing"]) <= 0.01
				and not (number(previous) and number(account) and number(previous) != number(account))
				and (not previous["rows"][-1].date or not account["rows"][0].date
					or previous["rows"][-1].date <= account["rows"][0].date)
				and len(previous["rows"]) + len(account["rows"]) >= 20):
			for row in account["rows"]:
				row.account_no = previous["account_no"]
			previous["rows"] = previous["rows"] + account["rows"]
			previous["closing"] = account["closing"]
			continue
		joined.append(dict(account))
	return joined


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
	for sheet_name, raw_rows in _sheet_blocks(sheets):
		header_idx, col_map = find_statement_header(raw_rows)
		if not col_map:
			# 没有表头的网银导出：按列的位置认（认不确定就跳过，同原来）。总账不按这个认。
			inferred = None if looks_like_ledger(raw_rows) else headerless_rows(raw_rows, filename)
			if inferred is None:
				continue  # 该 sheet 没有流水表头（汇总页/空页），跳过
			account_no = extract_sheet_account(raw_rows, 0, sheet_name)
			sheet_rows, sheet_opening = inferred
			sheet_closing = sheet_rows[-1].balance if sheet_rows else None
		else:
			account_no = extract_sheet_account(raw_rows, header_idx, sheet_name)
			sheet_rows, sheet_opening, sheet_closing = parse_statement_sheet(
				raw_rows, header_idx, col_map, filename, account_no)
		sheet_rows = [row for row in sheet_rows if not is_summary_row(row.description)]
		if not sheet_rows:
			continue
		_short_buddhist_dates(sheet_rows)
		sheet_rows, reversed_order = _oldest_first(sheet_rows)
		sheet_rows, reordered = _chain_order(sheet_rows)
		if reversed_order or reordered:
			# 新的在前的导出（BBL 网银新版等）/ 按余额重排过的：期末是排好后最后那一行的余额（原来取的是表上最后一行）。
			sheet_closing = next((row.balance for row in reversed(sheet_rows) if row.balance is not None), sheet_closing)
		if sheet_opening is None and col_map:
			sheet_opening = _header_row_opening(raw_rows[header_idx])
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

	accounts = _join_periods(accounts)
	if not accounts:
		if any(looks_like_ledger(raw_rows) for _name, raw_rows in sheets):
			# 会计软件导出的总账放进了账单口：说清楚放错了口（2026-09-30 压测 05-银行导出样本）。
			return {
				"ok": False,
				"error_code": LEDGER_NOT_STATEMENT,
				"error": "The file is a general ledger export, not a bank statement",
			}
		if any(printed_no_data(raw_rows) for _name, raw_rows in sheets):
			# 账单上印着「没有记录」：这个期间没有流水（ICBC 网银实测），按没有流水处理，不说读不出。
			return {"ok": False, "error_code": "no_movement", "error": "The statement prints that it has no transactions"}
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
