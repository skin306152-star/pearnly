"""销项税报告核查任务的控制器：与其它对账同一个“只许服务写”的闸。"""
from pearnly_cowork.reconciliation import vat_check_access
from pearnly_cowork.reconciliation.recon_documents import ReconDocument
from pearnly_cowork.reconciliation.recon_documents import persist as _persist

WRITE_FLAG = "vat_check_write"


def persist(doc):
    """Internal only: no whitelisted caller may supply this object identity."""
    return _persist(doc, WRITE_FLAG)


class VatCheckDocument(ReconDocument):
    write_flag = WRITE_FLAG
    access = vat_check_access
