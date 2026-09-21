# -*- coding: utf-8 -*-
"""模型返回的对账单 / 总账 JSON → 对账能用的行（第 ④ 层的纯逻辑）。

来源：ec2e5c957e6b ``services/recon/bank_stmt_gemini.py`` 与
``services/recon/bank_gl_gemini.py`` 里"拿到模型结果之后"的那一段。

这里不管怎么调模型、用哪个模型、怎么缓存、怎么切页 —— 那些是器件层与调用方的事（旧侧写
在 ``services/ocr/model_client`` 与 ``gemini_models.try_with_fallback`` 里）。本模块只做
三条判定，都是钱路上的：

· `statement_has_content`：**整张读空不能当成功**。旧侧注释写明，没有这条判据时
  ``{"rows": []}`` 会被当成功放行，对账里就出现一个假的 0.00 期末；返回 False 触发升级
  重试，再空才算失败。
· `statement_from_model`：逐行归一 —— 期初行（无发生 + 期初关键词）不入交易行、只用来补
  期初（模型自己给的期初优先）；缺日期沿用上一行（同日多笔银行常省略重复日期），首行无可
  沿用就保持 None；纯噪声行（无可继承日期 + 无金额 + 无余额）跳过；缺一侧金额或缺余额的行
  置信度降为 low，非法置信度按 high 收。
· `gl_from_model_chunks`：长总账按页分块请求后的合并 —— **坏块不能拖垮其它块**（整块失败就
  跳过）；期初只认第一块（第一块失败时保持 0，与旧侧一致）；没有日期的行丢掉；给了科目号
  就按前缀过滤。

未搬：模型客户端、升级兜底策略、内存/磁盘缓存、fitz 切页、日志与"异常吞掉返回 ok=False"。
只读传入的字典：不读文件、不打数据库、不走网络、不调模型。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pearnly_cowork.reconciliation.bank_matching import GlRow
from pearnly_cowork.reconciliation.bank_parse import parse_date
from pearnly_cowork.reconciliation.bank_statement import StatementRow

# 期初行的描述关键词（模型路径专用；与余额链那份 `OPENING_BALANCE_KEYWORDS` 不同 ——
# 这里判的是"模型把期初印成了一行交易"）。
OPENING_ROW_KEYWORDS = (
	"ยอดยกมา",
	"brought forward",
	"opening balance",
	"balance b/f",
	"期初余额",
	"上期结转",
)


def statement_has_content(data: Any) -> bool:
	"""模型是不是真抽到了东西：有交易行，或（零交易单）至少抽到期初/期末余额。

	两者皆空 = 整张读空（扫描件识别失败），必须触发升级重试而不是当成功。非字典一律 False。
	"""
	if not isinstance(data, dict):
		return False
	if data.get("rows"):
		return True
	return bool(data.get("opening_balance") or data.get("closing_balance"))


def _number_or_none(value: Any) -> Optional[float]:
	"""页脚汇总字段：能转就转，转不了就是 None（旧侧 `_nz`）。"""
	try:
		return float(value) if value is not None else None
	except (TypeError, ValueError):
		return None


def statement_from_model(data: Dict[str, Any]) -> Tuple[List[StatementRow], float, Dict[str, Any]]:
	"""模型的 JSON → ``(rows, opening, printed_totals)``。

	调用方要先用 `statement_has_content` 判过这张单子，再进这里（旧侧是升级策略在判）。
	"""
	rows: List[StatementRow] = []
	opening = float(data.get("opening_balance", 0) or 0)
	last_date = None  # 同日多笔银行常省略重复日期 → 沿用上一行
	for raw in data.get("rows") or []:
		tx_date = parse_date(str(raw.get("date", "")))
		if tx_date is not None:
			last_date = tx_date
		else:
			# 首行无可沿用则保持 None（不丢交易、不断余额链）。
			tx_date = last_date
		withdrawal_raw = raw.get("withdrawal")
		deposit_raw = raw.get("deposit")
		balance_raw = raw.get("balance")
		# 只有"日期继承不了 + 两侧都没金额 + 没余额"的纯噪声行才丢；有金额或有余额必留。
		if tx_date is None and not withdrawal_raw and not deposit_raw and balance_raw is None:
			continue
		description = str(raw.get("description", ""))
		confidence = (raw.get("confidence") or "high").lower()
		if confidence not in ("high", "medium", "low"):
			confidence = "high"
		if withdrawal_raw is None and deposit_raw is None:
			confidence = "low"
		if balance_raw is None:
			confidence = "low"
		withdrawal = float(withdrawal_raw or 0)
		deposit = float(deposit_raw or 0)
		balance = float(balance_raw or 0)
		is_opening = (withdrawal == 0 and deposit == 0) and any(
			keyword in description or keyword in description.lower()
			for keyword in OPENING_ROW_KEYWORDS
		)
		if is_opening:
			# 期初行不是交易：只在模型没给期初时用它补上。
			if not opening and balance:
				opening = balance
			continue
		rows.append(
			StatementRow(
				date=tx_date,
				description=description,
				withdrawal=withdrawal,
				deposit=deposit,
				balance=balance,
				confidence=confidence,
			)
		)
	printed_totals = {
		"total_debit": _number_or_none(data.get("printed_total_debit")),
		"total_credit": _number_or_none(data.get("printed_total_credit")),
		"debit_count": _number_or_none(data.get("printed_debit_count")),
		"credit_count": _number_or_none(data.get("printed_credit_count")),
	}
	return rows, opening, printed_totals


def gl_row_from_model(raw: Dict[str, Any], account_code: str = "") -> Optional[GlRow]:
	"""总账的一行模型输出 → `GlRow`；没有日期（或科目号不合前缀）就是 None。"""
	tx_date = parse_date(str(raw.get("date", "")))
	if tx_date is None:
		return None
	account = str(raw.get("account_code", "")).strip()
	if account_code and account and not account.startswith(account_code):
		return None
	return GlRow(
		date=tx_date,
		doc_no=str(raw.get("doc_no", "")),
		account_code=account,
		description=str(raw.get("description", "")),
		debit=float(raw.get("debit", 0) or 0),
		credit=float(raw.get("credit", 0) or 0),
	)


def gl_from_model_chunks(chunk_payloads: List[Optional[Dict[str, Any]]],
		account_code: str = "") -> Dict[str, Any]:
	"""分块请求的模型结果 → 与部署版同形的总账结果字典。

	``chunk_payloads`` 按页码顺序给出每一块的结果；整块失败（升级后仍没拿到）就是 ``None``，
	**跳过该块继续**,不让它拖垮其余块。期初只认第一块。
	"""
	rows: List[GlRow] = []
	accounts_seen = set()
	opening = 0.0
	for index, data in enumerate(chunk_payloads):
		if data is None:
			continue
		if index == 0:
			opening = float(data.get("opening_balance", 0) or 0)
		for raw in data.get("rows") or []:
			row = gl_row_from_model(raw, account_code)
			if row is None:
				continue
			accounts_seen.add(row.account_code or "?")
			rows.append(row)

	if not rows:
		return {"ok": False, "rows": [], "error": "gemini returned no usable rows"}

	# 借 = 钱进（与本地解析器和核对器一致：对账单存款 ↔ 总账借方）。
	closing = round(opening + sum(row.debit for row in rows) - sum(row.credit for row in rows), 2)
	return {
		"ok": True,
		"rows": rows,
		"accounts": sorted(accounts_seen - {"?"}),
		"opening": opening,
		"closing": closing,
		"row_count": len(rows),
	}
