"""Pure external-push semantics from deployed legacy ec2e5c957e6b.

Sources: erp/express_push/{direction,posting_kind}.py, erp/internal_push_guard.py,
cowork_line/document_preflight.py and purchase/field_clean.py:clean_tax_id.
These functions normalize declarations or reject an invalid local condition.
They do not authorize a target, verify a connection, enqueue or perform a push.
"""

from copy import deepcopy
import re
from typing import Any

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
	"""Retain the source's exact 13-digit length rule, not a checksum decision."""
	digits = re.sub(r"\D", "", str(raw or ""))
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
