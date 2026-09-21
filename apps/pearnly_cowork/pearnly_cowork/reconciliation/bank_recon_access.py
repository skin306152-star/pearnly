"""Bank reconciliation permissions for its dedicated tasks and rows.

The rules themselves live in ``recon_access``; this module binds them to the bank
DocType pair so hooks and callers keep their imported names.
"""
from pearnly_cowork.reconciliation import recon_access as _shared

TASK = "Cowork Bank Reconciliation"
ROW = "Cowork Bank Reconciliation Row"
TYPES = (TASK, ROW)
ROLES = _shared.ROLES

_access = _shared.recon_access(TASK, ROW)

allowed = _access.allowed
require = _access.require
has_permission = _access.has_permission
reject_share = _access.reject_share
task_query = _access.task_query
row_query = _access.row_query
