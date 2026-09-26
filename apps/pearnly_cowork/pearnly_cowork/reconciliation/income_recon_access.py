"""Income reconciliation permissions for its dedicated tasks and rows.

Same rules as the bank store (see ``recon_access``): the recon feature and the
caller's scope, with its own operator role (split from the shared recon role,
user 2026-09-27).
"""
from pearnly_cowork.reconciliation import recon_access as _shared

TASK = "Cowork Income Reconciliation"
ROW = "Cowork Income Reconciliation Row"
TYPES = (TASK, ROW)
ROLE = "Cowork Income Reconciliation"

_access = _shared.recon_access(TASK, ROW, ROLE)
ROLES = _access.ROLES

allowed = _access.allowed
require = _access.require
has_permission = _access.has_permission
reject_share = _access.reject_share
task_query = _access.task_query
row_query = _access.row_query
