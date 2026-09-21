"""Income reconciliation controllers: the shared service-only write gate."""
from pearnly_cowork.reconciliation import income_recon_access
from pearnly_cowork.reconciliation.recon_documents import ReconDocument
from pearnly_cowork.reconciliation.recon_documents import persist as _persist

WRITE_FLAG = "income_recon_write"


def persist(doc):
	"""Internal only: no whitelisted caller may supply this object identity."""
	return _persist(doc, WRITE_FLAG)


class IncomeReconciliationDocument(ReconDocument):
	write_flag = WRITE_FLAG
	access = income_recon_access
