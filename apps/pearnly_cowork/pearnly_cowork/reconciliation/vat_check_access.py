"""销项税报告核查的权限：与银行 / 收入对账同一套规则（对账功能、公司范围），角色单独一个。

旧线上一个对账角色管三种；用户 2026-09-27 在团队管理里拆成三个可单独勾选的角色。结果以 JSON 存在任务上（核查工作簿由两边的输入重建），没有明细表。
"""
from pearnly_cowork.reconciliation import recon_access as _shared

TASK = "Cowork VAT Check"
TYPES = (TASK,)
ROLE = "Cowork VAT Check"

_access = _shared.recon_access(TASK, None, ROLE)
ROLES = _access.ROLES

allowed = _access.allowed
require = _access.require
has_permission = _access.has_permission
reject_share = _access.reject_share
task_query = _access.task_query
