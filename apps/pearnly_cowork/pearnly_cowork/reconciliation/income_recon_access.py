"""Income reconciliation permissions for its dedicated tasks and rows.

Same rules as the bank store (see ``recon_access``): the recon feature, the recon
operator role and the caller's company scope. The deployed recon permissions did
not distinguish reconciliation types, so one operator role covers both.
"""
from pearnly_cowork.reconciliation import recon_access as _shared

TASK = "Cowork Income Reconciliation"
ROW = "Cowork Income Reconciliation Row"
TYPES = (TASK, ROW)
ROLES = _shared.ROLES

_access = _shared.recon_access(TASK, ROW)

allowed = _access.allowed
require = _access.require
has_permission = _access.has_permission
reject_share = _access.reject_share
task_query = _access.task_query
row_query = _access.row_query
