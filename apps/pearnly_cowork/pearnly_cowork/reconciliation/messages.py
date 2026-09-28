"""对账出错时给人看的一句话：合同错误是 `reconciliation.<码>:<字段>`，只按码取词条，字段名不外露。

2026-09-28 录制：流水一行摘要为空，弹窗直接显示 `reconciliation.text_required:statement.description`；
多个 `*_invalid` 码也没有泰文。没有词条的码回「对账没有完成」，不把原始码给用户。
"""
import frappe
from frappe import _


def error_text(error) -> str:
	code = str(error or "").split(":", 1)[0].strip()
	if not code:
		return _("reconciliation.failed")
	text = _(code)
	if text != code:
		return text
	# 不是码（服务端已经翻好的一句话）就原样给；像码却没词条的，回通用说明
	return _("reconciliation.failed") if code.startswith("reconciliation.") else str(error)


__all__ = ["error_text"]
