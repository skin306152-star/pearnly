# 来源：旧线上 ec2e5c957e6b · services/erp/mrerp_adapter_models.py + services/erp/exceptions.py（只改导入路径）
# 有意变化（2026-09-28）：成功单号取回读到的 MR.ERP 实际单号，删去自拼前缀的 bill_no_for；加「结果不明」一类。
"""
mrerp_adapter_models.py · MRERPAdapter 返回值 dataclasses

从 mrerp_adapter.py 抽出（REFACTOR-WB-modularize M0 · verbatim 搬家 0 逻辑改）。
InvoiceRecord / FailedRow / SuccessRow / ImportResult —— 纯 dataclass 定义,零外部依赖。
作 leaf 模块供 mrerp_adapter 主体 + 各 Mixin 共同 import,破「主类 ←→ mixin」循环。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FailedRow:
    """One invoice that failed business validation.

    `reasons` holds the raw Thai/English text exactly as MR.ERP wrote it
    in the report.xlsx (or our ERR_* code for preflight failures).
    `reasons_friendly` is a parallel list of `{lang: translation}` dicts
    sourced from `services.erp.mrerp_business_friendly`; the UI picks
    whichever language matches the viewer."""

    invoice_no: str
    reasons: List[str]
    original: Dict[str, Any]
    reasons_friendly: List[Dict[str, str]] = field(default_factory=list)
    evidence_screenshot: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SuccessRow:
    """One invoice that landed in MR.ERP's DB."""

    invoice_no: str
    mrerp_bill_no: str  # MR.ERP 列表里回读到的实际单号(菜单前缀 + 我方单号)
    original: Dict[str, Any]
    already_pushed: bool = False  # MR.ERP 报「单号已存在」,且在所选菜单里回读查到
    # 推进了哪类单据、哪个导入菜单（2026-10-19：CC 答得出 MR.ERP 的单是现销还是赊销、进了哪个菜单）
    doc_type: str = ""
    menu: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ImportResult:
    """Return value of upload_invoice_batch."""

    total: int
    success: List[SuccessRow] = field(default_factory=list)
    failed: List[FailedRow] = field(default_factory=list)
    elapsed_ms: int = 0
    xlsx_size_bytes: int = 0
    report_xlsx_path: Optional[str] = None
    # 导入已受理、在所选菜单列表里却查不到(或列表提示未关联日记账)· 可能已落库 · 结果不明,要人工核对
    unknown: List[FailedRow] = field(default_factory=list)

    @property
    def all_success(self) -> bool:
        return self.total > 0 and not self.failed and not self.unknown

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "success": [s.to_dict() for s in self.success],
            "failed": [f.to_dict() for f in self.failed],
            "unknown": [f.to_dict() for f in self.unknown],
            "elapsed_ms": self.elapsed_ms,
            "xlsx_size_bytes": self.xlsx_size_bytes,
            "report_xlsx_path": self.report_xlsx_path,
            "all_success": self.all_success,
        }


class MRERPError(Exception):
    """MR.ERP 推送失败的基类（旧 services/erp/exceptions.py）。"""


class MRERPAuthError(MRERPError):
    """凭据无效 / 会话被踢回登录页：不重试，要用户改连接。"""


class MRERPTechnicalError(MRERPError):
    """网络、超时、5xx 等：可重试。"""


class MRERPBusinessError(MRERPError):
    """服务器受理但按业务规则拒收（缺客户、重号、商品不存在……）：重试同一份也会被拒。"""

    def __init__(self, message: str, failed_rows: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.failed_rows = failed_rows or []
