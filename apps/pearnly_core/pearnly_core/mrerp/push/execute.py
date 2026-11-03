"""一次 MR.ERP 导入直写的执行（纯网络，不碰数据库）：载荷 ``{histories, mappings}`` + 账套 + 凭据 → 结论（多产品共用）。

函数体原样来自 Cowork `push/mrerp/runner.py`（2026-11-03 下沉，评估 2026-11-02 §14.6）：Cowork 的排队执行与 ERP 的导入直写
（`pearnly_erp_portal.mrerp.push`）调同一份，导入文件与发出的请求因此逐字节相同（`tests/test_mrerp_rules_golden.py`、
`tests/test_erp_mrerp_golden.py`）。记录、领取、回执、凭据都留在各产品里，只经参数交进来。
"""
from __future__ import annotations

import logging
from copy import deepcopy

from pearnly_core.session_lock import SessionLockUnavailable
from pearnly_core.mrerp.push.models import MRERPAuthError, MRERPBusinessError, MRERPTechnicalError
from pearnly_core.mrerp.push.transport import MrErpImport
from pearnly_core.mrerp.push.user_data import is_user_data_error

logger = logging.getLogger(__name__)


def execute(payload, target, secrets, system_url, today):
    """一次 MR.ERP 导入（纯网络，不碰数据库）→ {status: success|failed|unknown, reason, docnum, meta, menus}。"""
    comidyear, _, seldb = str(target.get("account_set") or "").partition(":")
    importer = None
    try:
        importer = MrErpImport(system_url=system_url, username=secrets.get("username") or "",
                               password=secrets.get("password") or "", comidyear=comidyear, seldb=seldb)
        with importer:
            result = importer.upload_routed_batch(deepcopy(payload.get("histories") or []),
                                                  deepcopy(payload.get("mappings") or {}), today=today)
    except (MRERPAuthError, MRERPBusinessError, MRERPTechnicalError, SessionLockUnavailable) as error:
        code = {MRERPAuthError: "ERR_AUTH", MRERPBusinessError: "ERR_BUSINESS",
                MRERPTechnicalError: "ERR_TECHNICAL"}.get(type(error), "ERR_SESSION_LOCK")
        text = str(error)
        return _exception(importer, text if text.startswith(code) else f"{code}: {text}")
    except Exception as error:  # noqa: BLE001 — 契约：任何异常都要落到账本，不冒到队列
        logger.exception("MR.ERP push raised")
        return _exception(importer, f"ERR_UNEXPECTED: {type(error).__name__}: {error}")
    meta = {"elapsed_ms": result.elapsed_ms, "xlsx_size_bytes": result.xlsx_size_bytes}
    menus = importer.observed_menus
    masters = importer.account_defaults.observed if importer.account_defaults else {}
    if result.unknown:
        # 导入受理了、所选菜单里却查不到：可能已落库，不能当失败重推，也不能当成功。
        text = "; ".join(str(r) for r in result.unknown[0].reasons)[:500]
        return {"status": "unknown", "reason": text, "meta": meta, "menus": menus, "masters": masters}
    if result.success:
        row = result.success[0]
        if row.already_pushed:
            meta["already_pushed"] = True
        # 进了哪类单据、哪个导入菜单：记进推送回执，CC 说得出「记成现销，进了 ใบขายเงินสด」（`application.service._entered`）
        meta.update({key: value for key, value in (("doc_type", row.doc_type), ("mrerp_menu", row.menu)) if value})
        return {"status": "success", "docnum": row.mrerp_bill_no, "reason": "", "meta": meta, "menus": menus,
                "masters": masters}
    failed = result.failed[0] if result.failed else None
    reasons = failed.reasons if failed else ["ERR_UNKNOWN_UPLOAD_OUTCOME"]
    text = "; ".join(str(r) for r in reasons)[:500]
    return {"status": "failed", "reason": text, "meta": {**meta, "user_data": is_user_data_error(text)},
            "menus": menus, "masters": masters}


def _exception(importer, reason):
    sent = bool(importer and importer.import_sent)
    return {"status": "unknown" if sent else "failed", "reason": reason[:500],
            "menus": importer.observed_menus if importer else {},
            "masters": importer.account_defaults.observed if importer and importer.account_defaults else {}}
