"""MR.ERP 代录开录前的服务端准备（ADR-026 第 2 条、评估 §4、§13 第 3 步；``contracts.MRERP_OPERATOR_ENABLED`` 开着才用到）。

与直写同一份准备（`push.application.service.mrerp_basis`：冻结的菜单、默认值、本方税号），在服务端登录这条连接的 MR.ERP
一次（与直写执行器同一个账号、同一把短锁），按 `pearnly_core.mrerp.push.operator_draft.stage` 走直写同一套分流与准备——
对方 / 商品在 MR.ERP 里没有的照直写自建（定不下来、建不成就这张转人工，不交 CC）——出网页表单要填的值（载荷 ``mrerp`` 块）。

- 对外请求期间不持任何行锁：读完准备就提交（这一步什么都没写），再登录 MR.ERP；
- 这个账号正被代录批次占着（`session_lock.operator_holds`）时不登录：登录会把正在录的那台电脑踢下线。这张回「等账号」
  （已有原因 ``account_busy``，自动级），批次放开账号后再交；
- 不在请求里记日志、不回密码。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import frappe

from pearnly_core.mrerp.push import operator_draft
from pearnly_core.mrerp.push.models import MRERPAuthError, MRERPBusinessError, MRERPTechnicalError
from pearnly_core.mrerp.push.transport import MrErpImport
from pearnly_core.mrerp.session import lock_key
from pearnly_core.session_lock import SessionLockUnavailable, operator_holds
from pearnly_cowork.connections import service as connections
from pearnly_cowork.history import storage

ACCOUNT_BUSY = "account_busy"
_ERRORS = {MRERPAuthError: "ERR_AUTH", MRERPBusinessError: "ERR_BUSINESS", MRERPTechnicalError: "ERR_TECHNICAL",
           SessionLockUnavailable: "ERR_SESSION_LOCK"}


def account_set_of(loaded, key) -> dict:
    """账套键（``comidyear:seldb``）→ CC 核对用的账套名与公司名（连接目录里采集到的，`pearnly_core.mrerp.catalog`）。"""
    comidyear, _sep, seldb = str(key or "").partition(":")
    found = {"comidyear": comidyear, "seldb": seldb or "1", "name": "", "company_name": ""}
    accounts = (loaded.evidence.snapshot or {}).get("account_sets") or []
    if isinstance(accounts, str):
        accounts = frappe.parse_json(accounts) or []
    for account in accounts:
        if str(account.get("source_id") or "") == str(key or ""):
            attributes = account.get("attributes") or {}
            found.update(name=str(account.get("label") or ""), company_name=str(attributes.get("company_name") or ""))
            break
    return found


def stage(name, connection, account_set_key=None) -> dict:
    """一张票开录前的服务端准备 → ``{"block": mrerp 块}``，或 ``{"status": "failed" / "waiting", "reason"}``（不交 CC）。"""
    from pearnly_cowork.push.application import service

    doc = storage.read(name)
    loaded = connections.read_for_push(connection, doc.workspace, doc.client)
    target, _frozen, payload = service.mrerp_basis(doc, loaded, account_set_key, lock=False)
    secrets = connections.credentials_for_use(loaded)
    system_url = (loaded.endpoint.get("config") or {}).get("system_url") or ""
    account = account_set_of(loaded, target.account_set)
    frappe.db.commit()  # 只读了准备：放开连接行锁，之后才对外请求
    username = secrets.get("username") or ""
    if username and operator_holds(lock_key(system_url, username)):
        return {"status": "waiting", "reason": ACCOUNT_BUSY}
    today = datetime.now(ZoneInfo("Asia/Bangkok")).date()
    try:
        importer = MrErpImport(system_url=system_url, username=username, password=secrets.get("password") or "",
                               comidyear=account["comidyear"], seldb=account["seldb"])
        with importer:
            blocks, failed = operator_draft.stage(importer, payload["histories"], payload["mappings"], today=today,
                                                  url=system_url, connection=loaded.doc.name, account_set=account)
    except tuple(_ERRORS) as error:
        code = next(value for kind, value in _ERRORS.items() if isinstance(error, kind))
        return {"status": "failed", "reason": code}
    if name in blocks:
        return {"block": blocks[name]}
    return {"status": "failed", "reason": "; ".join(failed.get(name) or ["ERR_UNKNOWN_UPLOAD_OUTCOME"])[:300]}
