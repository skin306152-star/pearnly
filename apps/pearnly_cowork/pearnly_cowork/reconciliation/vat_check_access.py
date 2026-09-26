"""销项税报告核查的权限：与银行 / 收入对账同一套（对账功能、对账角色、公司范围）。

旧线上对账权限不分类型，一个对账角色管三种。结果以 JSON 存在任务上（核查工作簿由两边的输入重建），没有明细表。
"""
from pearnly_cowork.reconciliation import recon_access as _shared

TASK = "Cowork VAT Check"
TYPES = (TASK,)
ROLES = _shared.ROLES

_access = _shared.recon_access(TASK, None)

allowed = _access.allowed
require = _access.require
has_permission = _access.has_permission
reject_share = _access.reject_share
task_query = _access.task_query
