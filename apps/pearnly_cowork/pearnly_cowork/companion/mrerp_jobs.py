"""Cowork 通道上的 MR.ERP 代录单（ADR-026 第 5、8 条；评估 §5、§14.3、§14.4；第 3 步）。

CC 桌面版的通道密钥按连接发（`companion.credentials`），MR.ERP 代录单冻结在 MR.ERP 连接的推送记录上。这里把两者接起来：
同一工作空间里 Express 连接的密钥能领（第 3 步原样）；没有 Express 连接时 CC 经 MR.ERP 连接自己的密钥领（11-03 选 A，
``heads(own=True)``，常量开着才放行，`http_auth.folder_only_allows`）。

- **找单**：同一工作空间 / 客户、``route_device`` 是这台电脑、排队中、载荷带代录标记且目标是 ``mrerp`` 的记录，按建立先后；
  派不派由第 1 步纯规则 `route_contracts.decide`（``Job.target = mrerp`` → 只给选定的那台、它报过 ``mrerp``、载荷版本够）定，
  不看账套可达。用哪把密钥来领都行（同一台电脑同一工作空间的任何一把），投递记录挂在来领的那把密钥的连接上，回执照原路认证；
- **核归属**（:func:`context`）：记录与密钥同一工作空间 / 客户 / 分支 / 代数，记录的连接是这个工作空间里的 MR.ERP 连接，记录派给这台
  电脑、是代录单。账本的推送上下文与 adapter 仍按记录自己的连接（账本按连接核锁、核权限，与直写同一套）；
- **账号租约**（ADR-026 第 8 条）：领一张时按「网址 + 用户名」（`mrerp.session.lock_key`）租这个 MR.ERP 账号，持有者 =
  站点 + 批次号；同批次后续领单续期；这批再没有排着 / 领着的单时（最后一张回执）放开；电脑不在按 TTL（300 秒）过期，过期后同一张
  照 ADR-021 11-02 口径重发回原电脑（重发前重新租上，租不到先不发）。租不到（直写正在导入、别的批次占着）就不发这张、单子照旧排着。
  用户名：这台电脑心跳报的「连接号 → 用户名」（`mrerp_users`，本机授权的那个）优先，没报的用站点连接上存的。
"""
from __future__ import annotations

import json

import frappe

from pearnly_core.express_operator import contracts as operator_contracts
from pearnly_cowork.push.native.contracts import ReservationContext

RECORD = "Cowork Push Record"
CONNECTION = "Cowork ERP Connection"
DEVICE = "Cowork CC Desktop Device"
OPEN = ("pending", "leased")
SCAN = 20  # 一次最多看几张（按建立先后；这台电脑上排着的 MR.ERP 代录单）


def _payload(row) -> dict:
    raw = row.get("payload_json") if isinstance(row, dict) else getattr(row, "payload_json", None)
    try:
        found = json.loads(raw or "{}") if isinstance(raw, (str, bytes)) else (raw or {})
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def is_job(row) -> bool:
    """这条推送记录是不是 MR.ERP 代录单（载荷带代录标记、目标 mrerp）。"""
    payload = _payload(row)
    return operator_contracts.is_operator(payload) and operator_contracts.target_of(payload) == operator_contracts.TARGET_MRERP


def batch_of(row) -> str:
    return str(((_payload(row).get("operator") or {}).get("batch") or {}).get("id") or "")


def context(row, credential, device):
    """MR.ERP 代录单 → 推送上下文（按记录自己的连接）；不归这把密钥 / 这台电脑领的一律无权。"""
    if ((row.workspace, row.client or "", row.branch, int(row.binding_generation or 0)) !=
            (credential.workspace, credential.client or "", "legacy", 0)
            or not device or (row.route_device or "") != device or not is_job(row)):
        frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)
    found = frappe.db.get_value(CONNECTION, row.connection, ["adapter", "workspace", "client"], as_dict=True)
    if not found or found.adapter != "mrerp" or (found.workspace, found.client or "") != (
            credential.workspace, credential.client or ""):
        frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)
    return ReservationContext(row.actor, row.workspace, row.client or "", row.history, int(row.history_version),
                              row.connection, row.account_key, branch=row.branch,
                              binding_generation=int(row.binding_generation), connection_revision=int(row.connection_revision))


def heads(credential, device, *, own=False) -> list:
    """这台电脑这个工作空间里排着的 MR.ERP 代录单，按建立先后。只读、不加锁。

    - Express 密钥来领（``own`` 假）：同一工作空间别的连接上的（不含来领的这把密钥自己的连接：那是 Express）；
    - MR.ERP 连接的密钥来领（``own`` 真，11-03 选 A，`transport._operator_channel`）：只看它自己这条连接上的。"""
    if not device or device == credential.credential_id:
        return []
    rows = frappe.db.sql(f"""SELECT r.name, r.payload_json, r.payload_version, r.actor, r.entered_by, r.route_device,
            r.account_key, r.connection FROM `tabCowork Push Record` r
        INNER JOIN `tabCowork ERP Connection` c ON c.name=r.connection
        WHERE r.workspace=%s AND COALESCE(r.client,'')=%s AND r.route_device=%s AND r.connection{'=' if own else '<>'}%s
          AND c.adapter='mrerp' AND r.branch='legacy' AND r.binding_generation=0 AND r.status='pending'
          AND COALESCE(r.result_unknown,0)=0
        ORDER BY r.created_at, r.name LIMIT %s""",
                         (credential.workspace, credential.client or "", device, credential.connection, SCAN),
                         as_dict=True)
    return [row for row in rows if is_job(row)]


def _usernames(device) -> dict:
    raw = frappe.db.get_value(DEVICE, device, "mrerp_users") if device else None
    from pearnly_core.cc_desktop.wire import route_contracts

    return route_contracts.users_of(raw)


def account_key(row, device) -> str:
    """这张单要占的 MR.ERP 账号（``lock_key``）；连用户名都没有（还没授权过）回空串。"""
    found = frappe.db.get_value(CONNECTION, row.connection, ["config_json", "credential_username"], as_dict=True) or {}
    try:
        config = json.loads(found.get("config_json") or "{}")
    except ValueError:
        config = {}
    username = _usernames(device).get(row.connection) or ""
    if not username and found.get("credential_username"):
        from frappe.utils.password import get_decrypted_password

        username = get_decrypted_password(CONNECTION, row.connection, "credential_username", raise_exception=False) or ""
    if not username:
        return ""
    from pearnly_core.mrerp.session import lock_key  # 用到才导入（会话模块带网络库）

    return lock_key(config.get("system_url") or "https://www.mrerp4sme.com", username)


def holder(row) -> str:
    return f"{frappe.local.site}:{batch_of(row)}"


def take(row, device) -> bool:
    """领这张前租 / 续这个账号；租不到回 False（这张不发、照旧排着）。不知道用户名的不拦（站点与电脑都没有这个账号）。"""
    from pearnly_core.session_lock import lease_account

    key = account_key(row, device)
    return not key or lease_account(key, holder(row))


def release_if_done(row, device) -> bool:
    """这批在这条连接上再没有排着 / 领着的单（全部已存、撤回或转人工）：放开账号。

    回执后的这一张自己也算：它回到排队（``pending``，例如 CC 回「等账套」）就是这批还有单没录，账号不放——放了直写可能趁 CC 再领到它
    之前登录同一账号、把 CC 的网页窗口踢下线（11-03 修，原来排掉了这一张自己）。调用方在回执写好状态之后调，读的是同一事务里的新状态。"""
    key = account_key(row, device)
    batch = batch_of(row)
    if not key or not batch:
        return False
    rows = frappe.db.sql("""SELECT name, payload_json FROM `tabCowork Push Record` WHERE connection=%s AND route_device=%s
        AND status IN %s LIMIT 200""", (row.connection, row.route_device or "", OPEN), as_dict=True)
    if any(batch_of(item) == batch for item in rows):
        return False
    from pearnly_core.session_lock import release_account

    return release_account(key, holder(row))
