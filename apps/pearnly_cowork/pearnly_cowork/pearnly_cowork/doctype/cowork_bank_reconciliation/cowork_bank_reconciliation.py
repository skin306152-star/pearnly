"""Bank reconciliation task: the controller, its file entry and its model seam.

The task store itself lives in the reconciliation domain. Two things cannot live
there and therefore live here, in the app's module (orchestration) layer:

1. reading the uploaded original — the file store belongs to the files domain and
   the reconciliation domain may not depend on it;
2. the model path — the OCR document tier belongs to the ocr domain, and the two
   domains do not depend on each other either.

Both are compositions, which is exactly what this layer is for. The model reading
itself runs in a durable OCR Work (``recon_center.document_ocr``), injected by the
reconciliation job: scans and photos are rendered and read page by page there, and
every provider call sits inside the Work the provider layer requires. A file no
reader can decode is refused with a clear error instead of being stored as an
empty task.
"""
import frappe

from pearnly_cowork.files import service as files_service
from pearnly_cowork.ocr.engine import document_tier
from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.providers.client import ProviderFailure
from pearnly_cowork.reconciliation import bank_parse, bank_recon_access, bank_recon_service
from pearnly_cowork.reconciliation.bank_recon_documents import BankReconciliationDocument


class CoworkBankReconciliation(BankReconciliationDocument):
    pass


def document_model_rows(gl_account_code="", *, model_call=None, model="", read=None):
	"""Build the intake's model seam on the OCR document tier.

	The returned callable takes ``(document_type, file_bytes, filename)`` and gives
	back the same rows shape the deterministic readers give, so the intake can
	compare the two readings and keep the more trustworthy one.

	``read`` is the reconciliation job's durable reader (``recon_center.document_ocr``):
	each file becomes one OCR Work, so scans and photos are rendered, read page by page
	and billed there, and every provider call runs inside the Work the provider layer
	requires (2026-09-29: called directly from the job, a file that needed the model
	failed the whole reconciliation with ``ocr.durable_work_required``). ``model_call``
	is the injected text-only seam the offline tests use. Without either (a plain web
	request), there is no model path and the deterministic readers' answer stands.
	"""
	def rows(document_type, file_bytes, filename):
		if not document_tier.admitted(document_type):
			return None
		if read is not None:
			legacy = read(document_type, file_bytes, filename)
		elif model_call is not None:
			pages = document_tier.text_pages(file_bytes)
			if not pages or not any(page.strip() for page in pages):
				return None
			try:
				_document, legacy, _warnings = document_tier.read_document(document_type,
					"\n".join(pages), model_call, model=model)
			except (ContentError, ProviderFailure) as error:
				frappe.log_error(title="cowork document tier", message=f"{type(error).__name__}: {error}")
				return None
		else:
			return None
		if not legacy:
			return None
		if document_type == "bank_statement":
			return bank_parse.statement_from_pipeline(legacy, filename)
		return bank_parse.gl_from_pipeline(legacy, gl_account_code)

	return rows


@frappe.whitelist(methods=["POST"])
def create_from_sources(workspace, title, request_id, statement_source, gl_source,
        header=None, gl_account_code="", reconciliation_type="bank", anchors=None, lang="th"):
    """Create one reconciliation from two uploaded originals.

    Both sources are read through the files domain, which checks the actor's
    source access and the stored checksum before any byte is returned.
    """
    bank_recon_access.require(workspace)
    statement_filename, statement_bytes = files_service.original(statement_source)
    gl_filename, gl_bytes = files_service.original(gl_source)
    return bank_recon_service.create_from_documents(
        workspace, title, request_id, statement_bytes, statement_filename,
        gl_bytes, gl_filename, header=header, gl_account_code=gl_account_code,
        reconciliation_type=reconciliation_type, anchors=anchors, lang=lang,
        model_rows=document_model_rows(gl_account_code))
