"""导入表里按账套取的值（分店 / 部门 / 工作 / 仓库 / 员工 / 销售区 / 运输）：生成器只从载荷里取，不再写死。

推送前由 `push/mrerp/defaults.py` 按这家公司的主档与这份账套选过的值算好，放进 mappings：
`_mrerp_header` = {branch, dept, job, store, employee, area, delivery}（员工 / 区域 / 运输写**名称**，其余写代码，
与 MR.ERP 导入的比对方式一致）；`_mrerp_customer_header` = {客户码: {employee, area, delivery}}（客户档里的，
MR.ERP 网页选了客户也改用这几个）。缺了就写空串：MR.ERP 会明确拒掉这张单据，不会写进别家公司的值。
"""
from typing import Any, Dict

_CUSTOMER_KEYS = ("employee", "area", "delivery")


def header(mappings: Dict[str, Any], customer_code: Any = "") -> Dict[str, str]:
    base = (mappings or {}).get("_mrerp_header") if isinstance(mappings, dict) else None
    base = base if isinstance(base, dict) else {}
    own = ((mappings or {}).get("_mrerp_customer_header") or {}) if isinstance(mappings, dict) else {}
    own = own.get(str(customer_code or "").strip()) if isinstance(own, dict) else None
    own = own if isinstance(own, dict) else {}
    out = {key: str(base.get(key) or "") for key in ("branch", "dept", "job", "store", "employee", "area", "delivery")}
    for key in _CUSTOMER_KEYS:
        if str(own.get(key) or "").strip():
            out[key] = str(own[key])
    return out
