"""Pure external-push semantics from deployed legacy ec2e5c957e6b.

Sources: erp/express_push/{direction,posting_kind}.py, erp/internal_push_guard.py,
cowork_line/document_preflight.py and purchase/field_clean.py:clean_tax_id.
These functions normalize declarations or reject an invalid local condition.
They do not authorize a target, verify a connection, enqueue or perform a push.
"""

from copy import deepcopy
import re
from typing import Any

from pearnly_core.tax_documents.amounts import repaired_tax_id, valid_tax_id

INTERNAL_SOURCES = frozenset({"erp_web", "line_erp"})
VALID_POSTING_KINDS = ("service", "stock")
_SALES_TOKENS = ("sales", "income")
_PURCHASE_TOKENS = ("purchase", "expense")
_DIRECTIONS = frozenset({"purchase", "sales"})
_PAYMENTS = frozenset({"cash", "credit"})


class PushContractError(ValueError):
	"""Legacy machine code; transport adapters map it to their native response."""

	def __init__(self, code: str):
		self.code = code
		super().__init__(code)


def clean_tax_id(raw) -> str:
	"""Retain the source's exact 13-digit length rule, not a checksum decision.

	A 12-digit read gets its dropped leading zero back only when the check digit
	then agrees (shared rule, erpcowork IV69/08-011 was held back by it on 2026-09-30)."""
	digits = repaired_tax_id(re.sub(r"\D", "", str(raw or "")))
	return digits if len(digits) == 13 else ""


def _fields(flat: dict[str, Any]) -> dict[str, Any]:
	fields = (flat or {}).get("fields")
	return fields if isinstance(fields, dict) else {}


def normalize_direction(value: Any) -> str | None:
	"""Recognized invoice declaration; bank deposit/withdrawal are not directions."""
	direction = str(value or "").strip().lower()
	if direction in _SALES_TOKENS:
		return "sales"
	if direction in _PURCHASE_TOKENS:
		return "purchase"
	return None


def explicit_direction(flat: dict[str, Any], history: dict[str, Any]) -> str | None:
	"""Retain source precedence: flat, history, then per-document fields.

	The first truthy declaration is normalized once. An invalid nonempty value
	does not silently fall through to a lower-precedence declaration.
	"""
	return normalize_direction(
		(flat or {}).get("direction")
		or (history or {}).get("direction")
		or _fields(flat).get("direction")
	)


def apply_batch_direction(fields: dict[str, Any] | None, direction: str | None) -> None:
	"""Set a pre-normalized batch declaration without overriding a row decision."""
	if direction and fields is not None and not fields.get("direction"):
		fields["direction"] = direction


def declared_direction_pages(pages: Any, primary: dict[str, Any] | None, direction: Any) -> list | None:
	"""会计员事后说明的进 / 销项写到已识别的票上（同 `apply_batch_direction`：不覆盖已有的方向）。

	``primary`` 是 ``pages`` 里推送读的那一页（`contracts.history.primary_page` 选出、同一对象）。回改好的页（副本），
	方向认不出、没有主页面、主页面已有方向时回 None（不用写）。"""
	direction = normalize_direction(direction)
	if not direction or not isinstance(pages, list) or not isinstance(primary, dict):
		return None
	try:
		index = next(i for i, page in enumerate(pages) if page is primary)
	except StopIteration:
		return None
	fields = primary.get("fields")
	if not isinstance(fields, dict) or fields.get("direction"):
		return None
	changed = deepcopy(pages)
	changed[index]["fields"]["direction"] = direction
	return changed


def detect_by_tax(flat: dict[str, Any], own_tax_id: Any) -> str | None:
	"""Exactly one own-company match selects a direction; ambiguity remains None."""
	own = clean_tax_id(own_tax_id)
	if not own:
		return None
	fields = _fields(flat)
	seller = clean_tax_id(fields.get("seller_tax") or fields.get("seller_tax_id"))
	buyer = clean_tax_id(fields.get("buyer_tax") or fields.get("buyer_tax_id"))
	match_seller = bool(seller) and seller == own
	match_buyer = bool(buyer) and buyer == own
	if match_seller and not match_buyer:
		return "sales"
	if match_buyer and not match_seller:
		return "purchase"
	return None


def resolve_direction(flat: dict[str, Any], history: dict[str, Any], *, own_tax_id: Any) -> str | None:
	"""Explicit invoice direction takes precedence over the verified company anchor."""
	return explicit_direction(flat, history) or detect_by_tax(flat, own_tax_id)


def _has_buyer(fields: dict[str, Any]) -> bool:
	"""票上印了买方（名称或税号，读得清读不清都算）。"""
	return any(str(fields.get(key) or "").strip() for key in ("buyer_name", "buyer_tax", "buyer_tax_id"))


def detect_by_account(flat: dict[str, Any], account_tax_id: Any) -> str | None:
	"""以推往的账套的税号判进 / 销项（2026-10-15 测试环境：事务所替客户记账，工作空间税号不是客户账套的税号，
	快递公司开的无买方简易税票判不出方向，推送拿卖方税号比账套、报「本方税号不符」）。

	卖方是这份账套、买方不是 → sales；买方是这份账套、卖方不是 → purchase；票上没有买方、卖方是别家（校验位对、
	与账套税号至少差 3 位，见 `_other_seller`）→ purchase（别家开给这家公司的无买方简易税票 / 收据）；其余（账套没报税号、两边都对不上、两边都是）判不出 → None。
	"""
	account = clean_tax_id(account_tax_id)
	if not account:
		return None
	fields = _fields(flat)
	seller = clean_tax_id(fields.get("seller_tax") or fields.get("seller_tax_id"))
	buyer = clean_tax_id(fields.get("buyer_tax") or fields.get("buyer_tax_id"))
	if seller == account and buyer != account:
		return "sales"
	if buyer == account and seller != account:
		return "purchase"
	if _other_seller(seller, account) and not _has_buyer(fields):
		return "purchase"
	return None


def _other_seller(seller: str, account: str) -> bool:
	"""卖方确是别家：税号校验位对，且与账套税号至少差 3 位。

	2026-10-15 审查：客户自开的无买方简易税票，卖方税号读错一位（0105551234568 对账套 0105551234567）
	会被当成别家开来的票按进项记进自己账套、销项税漏记。校验位抓不住所有一位错（第 3 位权重 11、
	余数 0 与 10 同校验位），所以只差 1–2 位的一律视为可能读错，判不出、交人选。"""
	if not seller or seller == account or not valid_tax_id(seller):
		return False
	return sum(a != b for a, b in zip(seller, account)) > 2


def resolve_account_direction(flat: dict[str, Any], history: dict[str, Any], *, own_tax_id: Any,
		account_tax_id: Any) -> str | None:
	"""推往一份账套时的方向：明确声明的优先，其次工作空间税号（`detect_by_tax`），都判不出再按账套税号
	（`detect_by_account`）。推送准备、复核页要选的项与 Express 预检同用这一套。"""
	return resolve_direction(flat, history, own_tax_id=own_tax_id) or detect_by_account(flat, account_tax_id)


def normalize_posting_kind(value: Any) -> str | None:
	"""None means undeclared; never invent a service/stock or workspace default."""
	if not isinstance(value, str):
		return None
	value = value.strip().lower()
	return value if value in VALID_POSTING_KINDS else None


def resolve_posting_kind(explicit: Any, history: dict[str, Any] | None = None) -> str | None:
	return normalize_posting_kind(explicit) or normalize_posting_kind((history or {}).get("posting_kind"))


def item_posting_kinds(items: Any) -> list[str] | None:
	"""None: no row opted in; []: declarations are incomplete or invalid."""
	rows = items if isinstance(items, list) else []
	declared = any(isinstance(row, dict) and "posting_kind" in row for row in rows)
	if not declared:
		return None
	result = []
	for row in rows:
		if not isinstance(row, dict):
			return []
		kind = normalize_posting_kind(row.get("posting_kind"))
		if kind is None:
			return []
		result.append(kind)
	return result


def require_external_history(history) -> None:
	if str((history or {}).get("source") or "") in INTERNAL_SOURCES:
		raise PushContractError("erp.internal_only")


def require_external_user(user) -> None:
	if user.get("entry") == "erp":
		raise PushContractError("erp.internal_only")


def require_document_choices(direction, payment=None) -> tuple[str, str | None]:
	"""LINE document precheck accepts only explicit purchase/sales, not aliases."""
	direction = str(direction or "").strip().lower()
	if direction not in _DIRECTIONS:
		raise PushContractError("direction_required")
	return direction, normalize_payment(payment)


def normalize_payment(value: Any) -> str | None:
	"""入口选的付款方式：只认现金/赊账两个值；没选是 None。

	空串、False、0 这类值是**非法**，不当成「没选」（合同原本就把两者分开，
	见 `test_payment_invalid_is_distinct_from_not_declared`）：调用方没选就别传。
	这是**人工裁决**那一层（`posting_payment_manual`），在付款判定的六级漏斗里
	优先级最高 —— 录入时选了现金就是现金，不再让 OCR 或票种把它推翻。
	"""
	if value is None:
		return None
	payment = str(value).strip().lower()
	if payment not in _PAYMENTS:
		raise PushContractError("payment_invalid")
	return payment


def apply_batch_payment(fields: dict[str, Any] | None, payment: str | None) -> None:
	"""把入口勾的付款方式写成人工裁决；票面已给出同一层的值时不覆盖。"""
	if payment and fields is not None and not fields.get("posting_payment_manual"):
		fields["posting_payment_manual"] = payment


def require_history_workspace(history, workspace_id) -> int:
	"""Check a previously authorized history against the selected document company.

	Call this only after refreshing target access. It does not read a Site or
	check membership. Source equality stays type-sensitive: a stored string ID
	does not equal the normalized integer workspace ID.
	"""
	if workspace_id is None:
		raise PushContractError("workspace_required")
	workspace_id = int(workspace_id)
	if not history:
		raise PushContractError("history_not_found")
	if history.get("workspace_client_id") != workspace_id:
		raise PushContractError("history_workspace_mismatch")
	return workspace_id


def require_adapter_choices(adapter, direction, *, posting_kind=None, payment=None) -> str | None:
	"""Local adapter declarations only; return the Express kind, otherwise None.

	The caller must separately refresh target access, validate document company
	and own-company subject, and execute the actual adapter preflight. A normal
	return is not evidence that the target, connection, payload or push is ready.
	"""
	direction, payment = require_document_choices(direction, payment)
	if adapter == "express":
		kind = normalize_posting_kind(posting_kind)
		if kind is None:
			code = "posting_kind_required" if posting_kind in (None, "") else "posting_kind_invalid"
			raise PushContractError(code)
		return kind
	if adapter != "mrerp":
		raise PushContractError("adapter_not_supported")
	if payment is None:
		raise PushContractError("payment_required")
	if direction == "purchase" and payment == "cash":
		raise PushContractError("mrerp_purchase_cash_unverified")
	return None


def with_manual_fields(history: dict[str, Any], direction: str, payment: str | None):
	"""Apply validated document choices to a copy; preserve original OCR evidence."""
	prepared = deepcopy(history)
	pages = prepared.get("pages") if isinstance(prepared.get("pages"), list) else []
	primary = next(
		(
			page for page in pages
			if isinstance(page, dict) and not page.get("is_duplicate") and not page.get("is_copy")
		),
		pages[0] if pages and isinstance(pages[0], dict) else None,
	)
	if primary is None:
		primary = {"fields": {}}
		pages = [primary]
		prepared["pages"] = pages
	fields = primary.get("fields") if isinstance(primary.get("fields"), dict) else {}
	fields = dict(fields)
	fields["direction"] = direction
	if payment is not None:
		fields["posting_payment_manual"] = payment
	primary["fields"] = fields
	return prepared
