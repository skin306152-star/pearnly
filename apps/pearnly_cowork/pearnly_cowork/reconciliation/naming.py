"""对账导出的文件名：用任务名称，认得出是哪一份（旧版用任务编号 `BankRecon_v2_CBR-…`，2026-09-28 录制反馈认不出）。"""
import re

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def export_filename(title, fallback) -> str:
	"""任务名称去掉文件名里不能用的字符（保留泰文 / 中文），太长截断；没有名称用 `fallback`。"""
	name = re.sub(r"\s+", " ", _UNSAFE.sub(" ", str(title or ""))).strip(" .")[:80].strip(" .")
	return f"{name or fallback}.xlsx"


__all__ = ["export_filename"]
