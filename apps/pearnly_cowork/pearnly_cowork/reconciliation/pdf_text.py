"""对账读 PDF 文字层的缓存：同一份字节只抽一次（2026-10-02 生产压测 q3 第二轮：long-worker 22.7% 的 CPU 在 pdfminer 抽字）。

等识别的对账每次被唤醒都从头重跑，同一份 PDF 在一次读取里还要抽两三次（判银行、免费解析、读不出时的提示），
每次都是整份 pdfminer。抽字只取决于字节，按字节摘要缓存：进程内留最近几份，队列间用站点缓存（Redis，6 小时）。
结果与直接抽完全相同；缓存不可用（离线、没有站点）时照旧现抽。"""
import hashlib
from collections import OrderedDict

from pearnly_cowork.tabular import readers

PREFIX = "pearnly-pdf-text:v1:"
TTL_SECONDS = 6 * 3600
RECENT = 8
_recent = OrderedDict()


def extract_pdf_text_pages(file_bytes):
	"""同 ``tabular.readers.extract_pdf_text_pages``，按字节摘要缓存。"""
	key = hashlib.sha256(file_bytes or b"").hexdigest()
	pages = _recent.get(key)
	if pages is None:
		pages = _shared_get(key)
		if pages is None:
			pages = readers.extract_pdf_text_pages(file_bytes)
			_shared_set(key, pages)
		_recent[key] = pages
		while len(_recent) > RECENT:
			_recent.popitem(last=False)
	_recent.move_to_end(key)
	return list(pages)


def _cache():
	try:
		import frappe

		return frappe.cache if getattr(frappe.local, "site", None) else None
	except Exception:
		return None


def _shared_get(key):
	cache = _cache()
	if cache is None:
		return None
	try:
		pages = cache.get_value(PREFIX + key)
		return pages if isinstance(pages, list) else None
	except Exception:
		return None


def _shared_set(key, pages):
	cache = _cache()
	if cache is None:
		return
	try:
		cache.set_value(PREFIX + key, list(pages), expires_in_sec=TTL_SECONDS)
	except Exception:
		pass
