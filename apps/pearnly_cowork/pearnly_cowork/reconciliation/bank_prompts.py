# -*- coding: utf-8 -*-
"""银行对账单 / 总账文档级 OCR 的提示词（与部署版逐字相同）。

来源：ec2e5c957e6b ``services/recon/bank_stmt_gemini.py`` 与
``services/recon/bank_gl_gemini.py``（部署版把这条模型路径放在对账包里，原生照此落点）。

提示词就是这一层的规格书：模型返回的每个字段都由它规定，改一个字都可能改变读数，所以两份
文本**逐字照搬**，不做"顺手润色"。固定源差分会把本模块的文本与旧文件里的字面量按字节比对
（见 ``tests/test_cowork_document_model_path_source_parity.py``）。

``STATEMENT_PROMPT_VERSION`` 是缓存键的一部分：旧侧把版本串拼在文件哈希后面，改提示词后旧
缓存自动失效（否则同一份 PDF 一直拿回旧结果）。沿用同一个版本串。

``GL_PROMPT`` 带 ``{hint}`` 占位（科目过滤提示）与 ``{{...}}`` 转义花括号 —— 旧侧就是这么写的，
用 `gl_prompt()` 组装，别手工 replace。
"""

STATEMENT_PROMPT_VERSION = 'v63-totals'

# 扫描件对账单的抽取提示词（部署版原文：含页脚汇总与余额自检两条纪律）。
STATEMENT_PROMPT = (
	'You are extracting EVERY transaction row from a bank statement (scanned PDF) for FINANCIAL RECONCILIATION. Both ACCURACY and COMPLETENESS matter: never invent digits, but also never drop a real transaction row.\n'
	'\n'
	'RULES:\n'
	"1. Extract EVERY transaction row, top to bottom, on EVERY page. Do NOT skip a row just because one field is unclear — extract the row, set only the unclear field to null, and mark confidence='low'. Missing one real transaction breaks reconciliation.\n"
	"2. NEVER guess or fabricate digits. If a number is blurry/ambiguous, return null for THAT field (not a guess) and mark confidence='low'.\n"
	"3. NEVER 'fix' the math by adjusting amounts — extract exactly what is printed, even if the running balance looks off.\n"
	"4. RUNNING-BALANCE SELF-CHECK: normally each row's balance = previous row's balance ± its amount. Use this to verify you did NOT miss a row: if two consecutive balances differ by more than one transaction's amount, you missed a row in between — look again.\n"
	"5. Do NOT output summary/total rows (e.g. 'Total', 'Total Credit/Debit/Deposit', 'รวมรายการ', 'ยอดรวม', 'grand total', 合计/总计) as transactions. Output ONLY individual transactions. BUT capture the statement's PRINTED footer summary numbers separately (see schema: printed_total_*/printed_*_count) — these are the bank's own totals used to verify completeness. If the footer prints 'No. of Credits 4 / Total Credit 8,000.00', set printed_credit_count=4 and printed_total_credit=8000.00. null if not printed.\n"
	"6. Thai number formats: '115.586,50' and '115,586.50' both mean 115586.50. '115.586.50' (dot thousands separator) also means 115586.50.\n"
	'7. withdrawal and deposit are MUTUALLY EXCLUSIVE — one is the amount, the other 0. Use the printed column; if direction is ambiguous, use the running balance (balance went DOWN = withdrawal; UP = deposit).\n'
	'\n'
	'Return JSON only (no markdown fences) with this exact schema:\n'
	'{\n'
	'  "bank_code": "kbank"|"bbl"|"kkp"|"ktb"|"scb"|"generic",\n'
	'  "opening_balance": number|null,\n'
	'  "closing_balance": number|null,\n'
	'  "printed_total_debit": number|null,\n'
	'  "printed_total_credit": number|null,\n'
	'  "printed_debit_count": number|null,\n'
	'  "printed_credit_count": number|null,\n'
	'  "rows": [{"date":"YYYY-MM-DD"|null, "description":"text exactly as printed","withdrawal":number|null, "deposit":number|null, "balance":number|null,"confidence":"high"|"medium"|"low"}]\n'
	'}\n'
	"Mark confidence='low' if ANY field in the row required interpretation of unclear characters. Mark confidence='medium' if mostly clear but you had minor doubts. Mark 'high' only when every digit is unambiguous."
)

# 长总账一次读不完，按页分块请求，每块不超过这么多页。
GL_PAGES_PER_CHUNK = 4

# 总账抽取提示词模板（``{hint}`` 由 gl_prompt 填入科目过滤提示）。
GL_PROMPT = (
	'This is a General Ledger (GL) document.{hint} Extract ALL transaction rows as JSON with keys:\n'
	'  "opening_balance": number,\n'
	'  "accounts": [list of account codes found],\n'
	'  "rows": [{{date:"YYYY-MM-DD", doc_no:string, account_code:string, description:string, debit:number, credit:number}}]\n'
	'debit = money into the account (deposits), credit = money out. Return ONLY valid JSON.'
)


def gl_prompt(account_code: str = "") -> str:
	"""组装总账提示词；给了科目号就加上过滤提示。"""
	hint = f" Filter to account code starting with '{account_code}'." if account_code else ""
	return GL_PROMPT.format(hint=hint)
