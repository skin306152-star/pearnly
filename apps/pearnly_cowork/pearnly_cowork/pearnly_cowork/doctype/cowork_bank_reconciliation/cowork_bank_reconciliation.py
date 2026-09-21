"""Bank reconciliation task: the controller, its file entry and its model seam.

The task store itself lives in the reconciliation domain. Two things cannot live
there and therefore live here, in the app's module (orchestration) layer:

1. reading the uploaded original — the file store belongs to the files domain and
   the reconciliation domain may not depend on it;
2. the model path — the OCR document tier belongs to the ocr domain, and the two
   domains do not depend on each other either.

Both are compositions, which is exactly what this layer is for. The model call
itself comes from the site's own OCR configuration (the same frozen snapshot the
invoice engine uses): until that is configured the tier cannot be built, and a
file no reader can decode is refused with a clear error instead of being stored
as an empty task.
"""
import frappe

from pearnly_cowork.files import service as files_service
from pearnly_cowork.ocr.engine import document_tier
from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.providers.client import ProviderFailure
from pearnly_cowork.reconciliation import bank_parse, bank_recon_service
from pearnly_cowork.reconciliation.bank_recon_documents import BankReconciliationDocument

OCR_IDENTITY_KEYS = ("backend", "project", "location", "credentials_path")


class CoworkBankReconciliation(BankReconciliationDocument):
    pass


def configured_model_call():
    """The site's model call and the two model names the document tier uses.

    Mirrors the invoice engine: the frozen snapshot is built from the site's
    explicit OCR configuration, and the client is built from the same snapshot.
    An unconfigured site throws here, before any model is contacted. The first
    model reads the page; the stronger one is only asked again when the balance
    chain has a break that arithmetic could not resolve.
    """
    from pearnly_cowork.ocr.execution import settings as ocr_settings
    from pearnly_cowork.ocr.policy import invoice_policy
    from pearnly_cowork.ocr.providers.core import build_client

    frozen = ocr_settings.snapshot()
    environment = frozen["environment"]
    selected = invoice_policy(environment, frozen["policy"])
    client = build_client(environment, core_identity={key: frozen["target"][key]
        for key in OCR_IDENTITY_KEYS})
    return client.model_call, selected.first_model, selected.l3_model


def document_model_rows(gl_account_code="", *, model_call=None, model=""):
    """Build the intake's model seam on the OCR document tier.

    The returned callable takes ``(document_type, file_bytes, filename)`` and gives
    back the same rows shape the deterministic readers give, so the intake can
    compare the two readings and keep the more trustworthy one. The model call and
    the model name are resolved once, when the seam is actually needed.
    """
    def rows(document_type, file_bytes, filename):
        if not document_tier.admitted(document_type):
            return None
        pages = document_tier.text_pages(file_bytes)
        if not pages or not any(page.strip() for page in pages):
            # No text layer: the deployed equivalent would need image rendering,
            # which this platform does not have for documents yet.
            return None
        escalated = ""
        if model_call is not None:
            call, chosen = model_call, model
        else:
            call, chosen, escalated = configured_model_call()
        try:
            _document, legacy, _warnings = document_tier.read_document(document_type,
                "\n".join(pages), call, model=chosen, escalated_model=escalated)
        except (ContentError, ProviderFailure) as error:
            # The deployed equivalent falls back to another path; here there is no
            # other model path, so the answer is "no model reading" and the intake
            # reports what the deterministic readers said instead of storing a
            # half-read task.
            frappe.log_error(title="cowork document tier", message=f"{type(error).__name__}: {error}")
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
    statement_filename, statement_bytes = files_service.original(statement_source)
    gl_filename, gl_bytes = files_service.original(gl_source)
    return bank_recon_service.create_from_documents(
        workspace, title, request_id, statement_bytes, statement_filename,
        gl_bytes, gl_filename, header=header, gl_account_code=gl_account_code,
        reconciliation_type=reconciliation_type, anchors=anchors, lang=lang,
        model_rows=document_model_rows(gl_account_code))
