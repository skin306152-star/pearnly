"""Pure contracts from legacy services/stocktake/{excel,entries}.py.

Source: ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188. The XLSX adapter must
enforce compressed/uncompressed file limits before passing cell values here.
Callers must resolve the Site, company and user before using these contracts.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence

FIELDS = ("product_code", "product_name", "barcode", "warehouse", "location", "unit", "book_qty")
TEMPLATE_FIELDS = ("product_code", "product_name", "barcode", "unit", "book_qty", "warehouse", "location")
LEGACY_THAI_HEADERS = (
	"รหัสสินค้า", "ชื่อสินค้า", "บาร์โค้ด", "คลังสินค้า", "ช่องเก็บ", "หน่วย", "จำนวนตามบัญชี",
)
TEMPLATE_HEADERS = (
	"รหัสสินค้า", "ชื่อสินค้า", "บาร์โค้ด / รหัส QR", "หน่วย", "จำนวนตามบัญชี",
	"คลังสินค้าเริ่มต้น (ไม่บังคับ)", "ตำแหน่งเริ่มต้น (ไม่บังคับ)",
)
MAX_BYTES = 5 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 40 * 1024 * 1024
MAX_ROWS = 10000


class StocktakeValidationError(ValueError):
	"""Stable legacy error code; the transport chooses its HTTP/Frappe response."""

	def __init__(self, code: str, row_number: int | None = None):
		self.code = code
		self.row_number = row_number
		super().__init__(code if row_number is None else f"{code}:{row_number}")


@dataclass(frozen=True)
class ImportCell:
	"""A decoded cell; data_type follows the XLSX reader ('s', 'n', 'f', etc.)."""

	value: object
	data_type: str


def quantity(value, *, nonnegative: bool = False) -> Decimal:
	"""Retain the source's six-place precision and exclusive magnitude limit."""
	try:
		if isinstance(value, bool) or value is None or not str(value).strip():
			raise ValueError
		result = Decimal(str(value).strip())
		if not result.is_finite() or abs(result) >= Decimal("100000000000000"):
			raise ValueError
		if result != result.quantize(Decimal("0.000001")) or (nonnegative and result < 0):
			raise ValueError
		return result
	except (ValueError, InvalidOperation):
		raise StocktakeValidationError("stocktake.quantity_invalid") from None


def validate_import_rows(
	header: Sequence[object], rows: Iterable[Sequence[ImportCell]],
) -> list[dict]:
	"""Validate a decoded first worksheet without opening files or evaluating formulas.

	Identifiers retain case and leading zeroes. A product may occur only once,
	and no product code/barcode may identify a different product. Blank rows
	still consume the source worksheet row limit, matching the deployed parser.
	"""
	header_names = tuple(str(value or "").strip() for value in header)
	layouts = {
		FIELDS: FIELDS,
		LEGACY_THAI_HEADERS: FIELDS,
		TEMPLATE_FIELDS: TEMPLATE_FIELDS,
		TEMPLATE_HEADERS: TEMPLATE_FIELDS,
		TEMPLATE_FIELDS[:5]: TEMPLATE_FIELDS[:5],
		TEMPLATE_HEADERS[:5]: TEMPLATE_FIELDS[:5],
	}
	layout = layouts.get(header_names)
	if layout is None:
		raise StocktakeValidationError("stocktake.headers_invalid")
	result, seen, identifiers = [], set(), {}
	for number, cells in enumerate(rows, 2):
		if number > MAX_ROWS + 1:
			raise StocktakeValidationError("stocktake.too_many_rows")
		if all(cell.value is None for cell in cells):
			continue
		if len(cells) != len(layout) or any(cell.data_type == "f" for cell in cells):
			raise StocktakeValidationError("stocktake.row_invalid", number)
		item = {
			key: str(cell.value if cell.value is not None else "").strip()
			for key, cell in zip(layout, cells)
		}
		item.setdefault("warehouse", "")
		item.setdefault("location", "")
		if any(not item[key] for key in ("product_code", "product_name", "unit")):
			raise StocktakeValidationError("stocktake.row_invalid", number)
		if any(len(value) > 300 for value in item.values()):
			raise StocktakeValidationError("stocktake.row_invalid", number)
		if any(cells[index].value is not None and cells[index].data_type != "s" for index in (0, 2)):
			raise StocktakeValidationError("stocktake.code_as_text", number)
		item["book_qty"] = quantity(item["book_qty"])
		key = item["product_code"]
		if key in seen:
			raise StocktakeValidationError("stocktake.duplicate", number)
		seen.add(key)
		for code in {key, item["barcode"]} - {""}:
			if code in identifiers and identifiers[code] != key:
				raise StocktakeValidationError("stocktake.ambiguous_code", number)
			identifiers[code] = key
		result.append(item)
	if not result:
		raise StocktakeValidationError("stocktake.empty")
	return result


def entry_request(
	task_id, target_id, qty, warehouse: str, location: str, *, actor_id,
	version: int | None = None, voided: bool = False, photo_digests: Sequence[str] = (),
) -> dict:
	"""Canonical payload compared by the persisted operation-id replay check.

	UUID/version/type validation and image validation belong to the caller.
	Photo digests must come from validated images, never a browser claim.
	This function does not acquire a lease or establish idempotency itself.
	"""
	warehouse, location = warehouse.strip(), location.strip()
	if not warehouse or len(warehouse) > 300 or len(location) > 300:
		raise StocktakeValidationError("stocktake.warehouse_required")
	qty = quantity(qty, nonnegative=True)
	request = {
		"task_id": str(task_id),
		"target_id": str(target_id),
		"version": version,
		"quantity": format(qty, ".6f"),
		"warehouse": warehouse,
		"location": location,
		"voided": voided,
		"actor_id": actor_id,
	}
	if photo_digests:
		request["photos"] = list(photo_digests)
	return request
