"""报税草稿表（``Cowork Tax Draft``）的控制器闸：只许 `tax.service` 写，与对账任务同一个对象标记闸。"""
from pearnly_cowork.reconciliation.recon_documents import ReconDocument
from pearnly_cowork.reconciliation.recon_documents import persist as _persist
from pearnly_cowork.tax import access

WRITE_FLAG = "tax_draft_write"


def persist(doc):
    """Internal only: no whitelisted caller may supply this object identity."""
    return _persist(doc, WRITE_FLAG)


class TaxDraftDocument(ReconDocument):
    write_flag = WRITE_FLAG
    access = access
