"""Bank reconciliation controllers: the shared service-only write gate."""
from pearnly_cowork.reconciliation import bank_recon_access
from pearnly_cowork.reconciliation.recon_documents import ReconDocument
from pearnly_cowork.reconciliation.recon_documents import persist as _persist

WRITE_FLAG = "bank_recon_write"


def persist(doc):
	"""Internal only: no whitelisted caller may supply this object identity."""
	return _persist(doc, WRITE_FLAG)


class BankReconciliationDocument(ReconDocument):
	write_flag = WRITE_FLAG
	access = bank_recon_access
