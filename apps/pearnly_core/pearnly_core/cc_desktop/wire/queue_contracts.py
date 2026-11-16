"""Pure Companion queue wire values; no authority, persistence or transport.

Source: live-server snapshot ec2e5c957e6b, routes/erp_agent.py. Optional text
and outcome/meta are preserved; unknown request keys are ignored like the old
Pydantic models. Intentional native boundaries: strict JSON types (no int/string
coercion), canonical UUID mapping IDs, and dictionary-only line_modes entries.
No native attempt secret is encoded in a wire ID. The caller supplies its UUID.
"""
from __future__ import annotations

import hashlib
from typing import Any
import math
from uuid import UUID


LEASE_SECONDS = 120
MAX_JOBS = 50
_ACK_KEYS = ("log_id", "result", "express_docnum", "error", "agent_id", "line_modes", "outcome", "meta")
_JOB_KEYS = frozenset(("log_id", "history_id", "invoice_no", "payload"))
# 领取回复里每件任务的可选键（ADR-021）：``attended`` = 录入人可能就在这台电脑前（不是定时任务、录入人在这台电脑上登录过 CC，
# 第 55 条，`route_contracts.attended`）；``task_key`` = 同一条推送记录每次领都一样的不透明键（:func:`task_key`，ADR-021
# 11-02 修订）。没有这些键的旧服务端照旧，旧客户端只按键取它认识的。
JOB_OPTIONAL_KEYS = frozenset(("attended", "task_key"))
# 领取请求的可选键（ADR-021）：``index_pending`` = 这台电脑主档索引还没重建的账套文件号（按账套黏住用）。旧服务端忽略。
LEASE_OPTIONAL_KEYS = frozenset(("index_pending",))
MAX_INDEX_PENDING = 500
# 写前核文件号对不上（1.7.0 起，回执按 ``waiting_lock`` 回报、错误原因带这个码）：服务端把这台电脑对这份账套的可达行标
# stale、要它整份重报，单子回到排队交给别的电脑（ADR-021，方案 2.5）。
ERR_ACCOUNT_UID_MISMATCH = "ERR_ACCOUNT_UID_MISMATCH"
_REFRESH_KEYS = frozenset(("request_id", "account_set_key", "scope_kind"))
# 领单回复的 ``operator_controls``（CC 桌面版 1.12.23 ④）：对话里人对留着等人的代录单做的选择，执行电脑在取任务线程里照做。
# 每条 {action, batch, doc, digest, button}；现在只有 answer（按指纹点留着的业务选择框的某个钮）。1.12.22 及以前不发。
OPERATOR_CONTROL_ACTIONS = ("answer",)
OPERATOR_CONTROLS_MAX = 20
OPERATOR_CONTROL_KEYS = ("action", "batch", "doc", "digest", "button")
OPERATOR_CONTROL_TEXT = {"batch": 80, "doc": 80, "button": 60}


class QueueContractError(ValueError):
    def __init__(self, code="companion.queue_request_invalid"):
        self.code = code
        super().__init__(code)


def _invalid(response=False):
    raise QueueContractError("companion.queue_response_invalid" if response else "companion.queue_request_invalid")


def _object(value, *, response=False):
    if not isinstance(value, dict):
        _invalid(response)
    return value


def _optional_text(value, *, response=False):
    if value is not None and not isinstance(value, str):
        _invalid(response)
    return value


def _uuid(value, *, response=False):
    if not isinstance(value, str):
        _invalid(response)
    try:
        valid = str(UUID(value)) == value
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        _invalid(response)
    return value


def _json_copy(value, *, response=False):
    """Preserve JSON values exactly, rejecting non-JSON/cyclic values atomically."""
    active = set()

    def clone(item):
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                _invalid(response)
            return item
        if not isinstance(item, (list, dict)) or id(item) in active:
            _invalid(response)
        active.add(id(item))
        try:
            if isinstance(item, list):
                return [clone(child) for child in item]
            if any(not isinstance(key, str) for key in item):
                _invalid(response)
            return {key: clone(child) for key, child in item.items()}
        finally:
            active.remove(id(item))

    try:
        return clone(value)
    except RecursionError:
        _invalid(response)


def _index_pending(value):
    """``index_pending``：文件号字符串的列表；形状不对当没报（旧服务端同样忽略它，不为一个可选键拒绝整次领取）。"""
    if not isinstance(value, list) or len(value) > MAX_INDEX_PENDING:
        return None
    if any(not isinstance(item, str) or not item.strip() or len(item) > 200 for item in value):
        return None
    return sorted({item.strip() for item in value})


def lease_request(raw: dict) -> dict:
    """Old defaults and 1..50 range; extras ignored, integer coercion rejected.

    ``index_pending`` (ADR-021) is returned only when the computer sent a well-formed list."""
    raw = _object(raw)
    maximum = raw.get("max", 10)
    if type(maximum) is not int or not 1 <= maximum <= MAX_JOBS:
        _invalid()
    result = {"max": maximum, "agent_id": _optional_text(raw.get("agent_id"))}
    pending = _index_pending(raw.get("index_pending")) if "index_pending" in raw else None
    if pending is not None:
        result["index_pending"] = pending
    return result


def ack_request(raw: dict) -> dict:
    """Validate all wire values before callers look up or mutate an attempt.

    Outcome remains an optional string: the source lifecycle decides whether it
    overrides success. Meta is not filtered here, on success or failure; native
    receipt construction retains its existing source sanitizer and stage rules.
    """
    raw = _object(raw)
    result = {key: raw.get(key) for key in _ACK_KEYS}
    result["log_id"] = _uuid(result["log_id"])
    if result["result"] not in ("success", "failed") or not isinstance(result["result"], str):
        _invalid()
    for key in ("express_docnum", "error", "agent_id", "outcome"):
        _optional_text(result[key])
    modes = result["line_modes"]
    if modes is not None and (not isinstance(modes, list) or any(not isinstance(row, dict) for row in modes)):
        _invalid()
    if result["meta"] is not None:
        _object(result["meta"])
    return _json_copy(result)


def job(log_id: str, native_record: dict, attended: bool | None = None, task_key: str | None = None) -> dict:
    """Project only the four legacy wire fields from native.service.project(), plus optional ``attended`` / ``task_key``.

    The UUID identifies the caller's opaque mapping, never the native record or
    attempt. Frozen target, actor, fence and attempt token are not projected.
    ``task_key``（ADR-021 11-02 修订）：同一条推送记录每次领都一样的不透明键（不是记录号本身），CC 桌面版本机按它记「这件写过
    没有」——丢在电脑手里的单重发时投递号换新，按投递号记就认不出。旧版 CC 桌面版不认这一项，照旧按投递号。
    """
    record = _object(native_record, response=True)
    payload = _object(record.get("frozen_payload"), response=True)
    history = _optional_text(record.get("history"), response=True) or ""
    result = {"log_id": _uuid(log_id, response=True), "history_id": history,
              "invoice_no": _optional_text(record.get("invoice_no"), response=True),
              "payload": _json_copy(payload, response=True)}
    if attended is not None:
        if type(attended) is not bool:
            _invalid(True)
        result["attended"] = attended
    if task_key is not None:
        result["task_key"] = _uuid(task_key, response=True)
    return result


# 认得 ``task_key`` 的 CC 桌面版（本机记账按它认写过没有，`companion.task_journal.key_of`）。更旧的按投递号记、认不出重发的同一条
# 记录：服务端不把丢了的单重发给它（照旧到期转结果不明，ADR-021 11-02 修订兼容一节）
TASK_KEY_MIN_COMPANION = "1.12.11"


def task_key(record_name: str) -> str:
    """推送记录号 → 不透明的稳定键（UUID 形状；不能反推出记录号，同 ``log_id`` 不暴露账本记录）。"""
    digest = hashlib.sha256(("pearnly-task:" + str(record_name)).encode("utf-8")).hexdigest()
    return str(UUID(digest[:32]))


def operator_control(raw: Any) -> dict | None:
    """一条代录指令的形状（``operator_controls`` 的一项）；不合格回 None（不发）。纯规则。"""
    if not isinstance(raw, dict) or raw.get("action") not in OPERATOR_CONTROL_ACTIONS:
        return None
    digest = raw.get("digest")
    if not isinstance(digest, str) or len(digest) != 16 or any(c not in "0123456789abcdef" for c in digest):
        return None
    out = {"action": raw["action"], "digest": digest}
    for key, limit in OPERATOR_CONTROL_TEXT.items():
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            return None
        out[key] = value
    return out


def lease_response(jobs: list, refresh: dict | None = None, requests_waiting: bool = False,
                   operator_controls: list | None = None) -> dict:
    """Build the top-level old response, with an optional directory command. ``requests_waiting``: an on-demand request
    this computer can take was just made (`request_wake`); CC Desktop 1.12.22+ asks for it at once, older ones ignore it.
    ``operator_controls``: choices made in the conversation for this computer's held operator documents (CC Desktop
    1.12.23+, the caller only passes them to such a computer); malformed items are dropped."""
    if not isinstance(jobs, list) or len(jobs) > MAX_JOBS:
        _invalid(True)
    values = []
    for row in jobs:
        row = _object(row, response=True)
        if not _JOB_KEYS <= set(row) or set(row) - _JOB_KEYS - JOB_OPTIONAL_KEYS:
            _invalid(True)
        values.append(job(row["log_id"], {"history": row["history_id"],
            "invoice_no": row["invoice_no"], "frozen_payload": row["payload"]}, row.get("attended"), row.get("task_key")))
    result = {"ok": True, "lease_seconds": LEASE_SECONDS, "jobs": values}
    if refresh is not None:
        _object(refresh, response=True)
        if refresh:
            if (set(refresh) != _REFRESH_KEYS or refresh.get("scope_kind") not in ("endpoint", "account_set")
                    or not isinstance(refresh.get("account_set_key"), str) or not refresh["account_set_key"]
                    or (refresh["scope_kind"] == "endpoint") != (refresh["account_set_key"] == "@endpoint")):
                _invalid(True)
            _uuid(refresh.get("request_id"), response=True)
            result["master_refresh"] = _json_copy(refresh, response=True)
    if requests_waiting:
        result["requests_waiting"] = True
    controls = [found for found in map(operator_control, (operator_controls or [])[:OPERATOR_CONTROLS_MAX]) if found]
    if controls:
        result["operator_controls"] = controls
    return result


def ack_response(native_projection: dict) -> dict:
    """Rebuild the old legacy ACK response after a trusted native receipt.

    Never return the native projection itself. An unknown result is not an ACK;
    non-success replay reconstructs the same response rather than new effects.
    Native extension: a finished pending attempt may later be quarantined. Its
    exact replay acknowledges only the manual state, without a retry promise.
    """
    row = _object(native_projection, response=True)
    body = _object(row.get("response_body"), response=True)
    stage = _object(body.get("meta"), response=True).get("stage")
    status = row.get("status")
    if row.get("result_unknown"):
        _invalid(True)
    if (row.get("replayed") is True and status == "manual"
            and stage in ("waiting_lock", "failed", "rolled_back")
            and row.get("reason") in ("push.native_history_changed", "push.native_target_changed",
                                      "push.native_target_mismatch", "companion.queue_access_revoked")):
        return {"ok": True, "status": "manual", "idempotent": True}
    if stage == "success" and status == "success":
        if row.get("replayed") is True:
            return {"ok": True, "status": "success", "idempotent": True}
        return {"ok": True, "status": "success",
                "express_docnum": _optional_text(row.get("external_docnum"), response=True)}
    if stage == "waiting_lock" and status == "pending":
        return {"ok": True, "status": "pending", "stage": stage, "retry": True}
    if stage in ("needs_mapping", "needs_review") and status == "manual":
        return {"ok": True, "status": "manual", "stage": stage}
    if stage in ("failed", "rolled_back") and status in ("pending", "manual"):
        attempt = row.get("wire_attempt")
        if type(attempt) is not int or attempt < 1:
            _invalid(True)
        return {"ok": True, "status": status, "stage": stage,
                "attempt": attempt, "agent_failures": attempt - 1}
    _invalid(True)
