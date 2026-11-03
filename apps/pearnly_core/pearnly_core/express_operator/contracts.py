"""代录的纯规则（多产品共用）：单据 / 批次状态、原因代码与三级、执行目标。无框架、无 IO。

执行目标不只 Express（ADR-026）：载荷 ``operator.target`` 写录到哪种账本（``express`` 桌面程序 / ``mrerp`` 网页），没写 = Express。

与 CC 桌面版 ``companion/src/companion/express_operator/contract.py`` 一字不差（两边各一份，因为 CC 桌面版是独立安装包，
不导入服务端代码；``tests/test_express_operator_shared.py`` 比对）。前端只按代码显示文案。
"""
from __future__ import annotations

ENTRY_MODE = "operator"

PENDING, ENTERING, SAVED, VOIDED, WAITING, SKIPPED = "pending", "entering", "saved", "voided", "waiting", "skipped"
DONE_STATES = frozenset({SAVED, VOIDED, SKIPPED})

BATCH_READY, BATCH_RUNNING, BATCH_PAUSED, BATCH_STOPPED, BATCH_FINISHED = (
    "ready", "running", "paused", "stopped", "finished")

AUTO, AUTO_REPORTED, HUMAN = "auto", "auto_reported", "human"

REASONS = {
    "party_missing": HUMAN,
    "item_missing": HUMAN,
    "tax_setting_mismatch": HUMAN,
    "period_locked": HUMAN,
    "period_outside": HUMAN,
    "negative_stock": HUMAN,
    "login_failed": HUMAN,
    "login_missing": HUMAN,
    "license": HUMAN,
    "company_mismatch": HUMAN,
    "company_pick": HUMAN,  # 停在 Express 公司列表、要人选这家（配方没核过或登记表里找不到）
    "account_in_use": HUMAN,  # MR.ERP：这个账号在别处登录，本批被踢下线（不自动重登，整批停）
    "account_set_unavailable": HUMAN,  # MR.ERP：账套打不开（库不存在等，整批停）
    "recipe_mismatch": HUMAN,
    "unsupported": HUMAN,
    "readback_mismatch": HUMAN,
    "unknown_screen": HUMAN,
    "crash_repeated": HUMAN,
    "cc_restarted": HUMAN,  # 录到一半 CC 重启：上次留下的 Express 已按放弃收拾，这张没录成
    "placeholder_left": HUMAN,
    "network_down": HUMAN,
    "budget": HUMAN,
    "duplicate_ref": AUTO_REPORTED,
    "voided": AUTO_REPORTED,
    "ai_resolved": AUTO_REPORTED,
    "express_restarted": AUTO_REPORTED,
    "screen_locked": AUTO,
    "user_active": AUTO,
    "network_wait": AUTO,
    "account_busy": AUTO,
    "waiting_start": AUTO,
}

BATCH_STOPPERS = frozenset({"company_mismatch", "login_failed", "login_missing", "license", "network_down", "budget",
                            "account_in_use", "account_set_unavailable"})


def level(reason: str) -> str:
    return REASONS.get(reason, HUMAN)


# ── 执行目标（ADR-026）：录到哪种账本。Express 是 Windows 桌面程序，MR.ERP 是网页（CC 自带网页内核的独立窗口）───────
TARGET_EXPRESS = "express"
TARGET_MRERP = "mrerp"
TARGETS = (TARGET_EXPRESS, TARGET_MRERP)

# ── 载荷：直写同一份载荷 + 代录标记（载荷 16，CC 桌面版 payload_contract 同号）───────────────────
OPERATOR_PAYLOAD_VERSION = 16
# 代录到 MR.ERP（``operator.target = mrerp``）：CC 桌面版 payload_contract 同号，旧版诚实拒收（ERR_PAYLOAD_VERSION）
MRERP_OPERATOR_PAYLOAD_VERSION = 18


def target_of(payload) -> str:
    """代录载荷的执行目标：没写（载荷 16 / 17 与以前一样）= Express；写了什么就回什么（认不出的由执行方按「不支持」回执）。"""
    operator = payload.get("operator") if isinstance(payload, dict) else None
    found = operator.get("target") if isinstance(operator, dict) else None
    return str(found or TARGET_EXPRESS)


def for_operator(payload: dict, batch: dict, *, vat_included: bool = False, target: str = TARGET_EXPRESS) -> dict:
    """直写载荷 → 代录载荷：加 ``entry_mode = operator`` 与批次信息，版本至少 16（旧版 CC 诚实拒收，不会被当直写写进 DBF）。
    ``batch``：``{"id", "index", "total", "start_at", "doc", "company"}``（第几张从 1 数；``start_at`` 为 0 = 现在开始；``doc`` 是站点上
    这张单的编号，CC 用它当批次里的单据号，页面才对得上是哪一张）。

    ``target``（ADR-026）：``express`` 不写目标键，载荷与以前逐字节相同；``mrerp`` 写 ``operator.target``、版本至少 18。"""
    if target not in TARGETS:
        raise ValueError("bad operator target")
    info = {"id": str(batch.get("id") or ""), "index": int(batch.get("index") or 0),
            "total": int(batch.get("total") or 0), "start_at": float(batch.get("start_at") or 0)}
    if batch.get("doc"):
        info["doc"] = str(batch["doc"])[:140]
    if batch.get("company"):
        info["company"] = str(batch["company"])[:140]  # 站点上的公司编号：CC 重启后页面拿它向站点核这一批
    if not info["id"] or info["index"] < 1 or info["total"] < info["index"]:
        raise ValueError("bad operator batch")
    operator = {"batch": info, "vat_included": bool(vat_included)}
    floor = OPERATOR_PAYLOAD_VERSION
    if target != TARGET_EXPRESS:
        operator["target"] = target
        floor = MRERP_OPERATOR_PAYLOAD_VERSION
    return {**payload, "entry_mode": ENTRY_MODE, "operator": operator,
            "payload_version": max(int(payload.get("payload_version") or 1), floor)}


def is_operator(payload) -> bool:
    """这份写账载荷是不是交给代录的（``entry_mode=operator``）。产品据此判一张单归代录：归代录、还没录成的单直写一律不接
    （主会话 10-27）。``payload`` 可以是 dict 或存库的 JSON 文本，认不出按不是。"""
    if isinstance(payload, (str, bytes)):
        import json

        try:
            payload = json.loads(payload or "{}")
        except ValueError:
            return False
    return isinstance(payload, dict) and str(payload.get("entry_mode") or "") == ENTRY_MODE
