"""Bank reconciliation task: the controller and its file entry point.

The task store itself lives in the reconciliation domain. The one thing that
cannot live there is the composition of "read the uploaded original" with
"parse it": the file store belongs to the files domain and the reconciliation
domain may not depend on it, so this orchestration layer (the app's module
directory) is where the two meet, exactly as the architecture rules describe.

The model path is not wired here: ``bank_recon_service.create_from_documents``
takes it as an injected callable, and this entry point does not pass one, so a
document that no native reader can decode is refused with the readers' own
error instead of silently producing an empty task. Wiring the document tier of
the OCR engine in is the next step.
"""
import frappe

from pearnly_cowork.files import service as files_service
from pearnly_cowork.reconciliation import bank_recon_service
from pearnly_cowork.reconciliation.bank_recon_documents import BankReconciliationDocument


class CoworkBankReconciliation(BankReconciliationDocument):
    pass


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
        reconciliation_type=reconciliation_type, anchors=anchors, lang=lang)
