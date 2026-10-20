"""银行对账给人看的银行名（结果列表、导出、CC 回答）：取账单上印着的，读不出就空着。

银行码（``kbank`` / ``generic`` …）是内部代号：认银行的版式、记对账预设用。认不出银行的通用版式是 ``generic``，
它不是银行名，不能写到任何给人看的地方（2026-10-20 录制：导出的银行一栏写着「GENERIC」，CC 回答把虚构的
ธนาคารสาธิต 说成 กสิกรไทย）。认得出银行、又没读到印着的名字时，照旧写银行码（``KBANK``）。

纯函数：只读传入的值。
"""
from collections import Counter

GENERIC = "generic"
NAME_LIMIT = 80


def code_label(bank_code) -> str:
	"""认得出的银行码大写（``KBANK``）；通用版式、空的回空。"""
	code = str(bank_code or "").strip()
	return "" if not code or code.lower() == GENERIC else code.upper()


def printed(parsed) -> str:
	"""一份账单读出的银行名：PDF 文字层表头上印着的（``bank_name``），没有就取识别读出的（``identity.bank``）。"""
	parsed = parsed or {}
	name = str(parsed.get("bank_name") or (parsed.get("identity") or {}).get("bank") or "")
	return " ".join(name.split())[:NAME_LIMIT]


def file_label(record) -> str:
	"""一份账单（``parse_info.stmt_files`` 的一条）给人看的银行名。"""
	record = record or {}
	return str(record.get("bank_name") or "").strip() or code_label(record.get("bank_code"))


def task_label(bank_code, stmt_files) -> str:
	"""一次对账给人看的银行名：读成功的几份账单上印着的（几份不同取最多的），没有就是认得出的银行码，都没有回空。"""
	names = Counter(str(record.get("bank_name") or "").strip() for record in stmt_files or []
		if isinstance(record, dict) and record.get("ok"))
	names.pop("", None)
	return names.most_common(1)[0][0] if names else code_label(bank_code)


__all__ = ["code_label", "file_label", "printed", "task_label"]
