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
    "menu_choice": HUMAN,  # 单据菜单落到子菜单、里面几个命令定不下来：只停这一张，候选在结果 meta.menu_options，问一次记住后按 form_choice 再交
    "crash_repeated": HUMAN,
    "cc_restarted": HUMAN,  # 录到一半 CC 重启：上次留下的 Express 已按放弃收拾，这张没录成
    "partial_saved": HUMAN,  # 保存途中停住：账套里留了这张的半截单（结果 meta.operator.partial 带单号与缺什么），不重录、不作废
    "dialog_choice": HUMAN,  # 中途配方外的业务选择框（CC 桌面版 1.12.23）：Express 留着，结果 meta.operator.dialog 带原文、按钮与指纹，对话里出卡
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

# 代录回执原因最多存多长（``[OPERATOR_<原因>] <说明>`` 整段，CC 桌面版说明最多 2000 字）：同
# ``cc_desktop.wire.ack_rules.OPERATOR_REASON_MAX``（测试守住两边一致）
REASON_MAX = 2100
BATCH_STOPPERS = frozenset({"company_mismatch", "login_failed", "login_missing", "license", "network_down", "budget",
                            "account_in_use", "account_set_unavailable"})


def level(reason: str) -> str:
    return REASONS.get(reason, HUMAN)


# ── 执行目标（ADR-026）：录到哪种账本。Express 是 Windows 桌面程序，MR.ERP 是网页（CC 自带网页内核的独立窗口）───────
TARGET_EXPRESS = "express"
TARGET_MRERP = "mrerp"
TARGETS = (TARGET_EXPRESS, TARGET_MRERP)
# MR.ERP 代录开没开（全平台一处，主会话 11-03；不按站点 / 租户开关，10-07 定）：关着时产品对所有公司回 Express、出 Express
# 载荷，与没有 MR.ERP 代录时逐字节相同。第 2 步 CC 执行层核过线上配方、合入的那个提交统一改 True。只在服务端（CC 桌面版
# 那一份契约不需要它：站点不派 MR.ERP 代录单，CC 就不会收到）。
MRERP_OPERATOR_ENABLED = False

# ── 载荷：直写同一份载荷 + 代录标记（载荷 16，CC 桌面版 payload_contract 同号）───────────────────
OPERATOR_PAYLOAD_VERSION = 16
# 代录到 MR.ERP（``operator.target = mrerp``）：CC 桌面版 payload_contract 同号，旧版诚实拒收（ERR_PAYLOAD_VERSION）
MRERP_OPERATOR_PAYLOAD_VERSION = 18


def target_of(payload) -> str:
    """代录载荷的执行目标：没写（载荷 16 / 17 与以前一样）= Express；写了什么就回什么（认不出的由执行方按「不支持」回执）。"""
    operator = payload.get("operator") if isinstance(payload, dict) else None
    found = operator.get("target") if isinstance(operator, dict) else None
    return str(found or TARGET_EXPRESS)


# 引导录入载荷 21（接口约定 docs/work/2026-11-12-cc-guided-entry-express-coverage.md 第 8 节「代录 × 新物料」，接缝负责人 CC 桌面版窗口）：
# 代录单只可以带这四个行键（新物料的单位码、存货科目组、分类、允许负库存），带了版本至少 21 照派；其余载荷 21 新键是直写专用，
# 代录单带了服务端不发（拼载荷时报错），CC 桌面版也回 PAYLOAD_INVALID 兜底。与 CC 桌面版 ``payload_contract.OPERATOR_LINE_KEYS`` 相同、
# 是 ``express_rules.push.common.GUIDED_KEYS`` 的子集（测试守着）；本文件保持纯契约不导入写账规则，所以在这里列一份
GUIDED_PAYLOAD_VERSION = 21
OPERATOR_GUIDED_LINE_KEYS = ("unit_code", "acccod", "stkgrp", "negallow")
_DIRECT_ONLY_HEADER = ("guided", "due_date_be", "cash_account")
_DIRECT_ONLY_LINE = ("cost_choice", "cost")
_DIRECT_ONLY_PARTY = ("type", "payterm")


def _operator_guided_floor(payload: dict) -> int:
    """代录载荷里载荷 21 新键的检查：只带四个行键 → 21；没带 → 0；带了直写专用的新键抛 ValueError（不派）。"""
    if any(key in payload for key in _DIRECT_ONLY_HEADER):
        raise ValueError("operator payload carries direct-write keys")
    for block in ("supplier", "customer"):
        if isinstance(payload.get(block), dict) and any(key in payload[block] for key in _DIRECT_ONLY_PARTY):
            raise ValueError("operator payload carries direct-write keys")
    lines = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    if any(key in item for item in lines for key in _DIRECT_ONLY_LINE):
        raise ValueError("operator payload carries direct-write keys")
    return GUIDED_PAYLOAD_VERSION if any(key in item for item in lines for key in OPERATOR_GUIDED_LINE_KEYS) else 0


PAY_ROUTES = ("cash", "transfer")  # ``operator.pay_route`` 的取值（``menu_choice.pay_route``）
FORM_CHOICE_MAX = 200


def for_operator(payload: dict, batch: dict, *, vat_included: bool = False, target: str = TARGET_EXPRESS,
                 form_choice: str = "", pay_route: str = "") -> dict:
    """直写载荷 → 代录载荷：加 ``entry_mode = operator`` 与批次信息，版本至少 16（旧版 CC 诚实拒收，不会被当直写写进 DBF）。
    ``batch``：``{"id", "index", "total", "start_at", "doc", "company"}``（第几张从 1 数；``start_at`` 为 0 = 现在开始；``doc`` 是站点上
    这张单的编号，CC 用它当批次里的单据号，页面才对得上是哪一张）。

    ``target``（ADR-026）：``express`` 不写目标键，载荷与以前逐字节相同；``mrerp`` 写 ``operator.target``、版本至少 18。

    Express 子菜单多个候选时（评估 2026-11-05 修法 D，CC 桌面版 1.12.15 起读，旧版忽略，不升版本）：``form_choice`` 是这家这本账套
    这种单记住的菜单文字，``pay_route`` 是按单据付款文字认的 ``cash`` / ``transfer``；空的不写键（载荷与以前逐字节相同），MR.ERP 不带。"""
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
    floor = max(OPERATOR_PAYLOAD_VERSION, _operator_guided_floor(payload))
    if target != TARGET_EXPRESS:
        operator["target"] = target
        floor = max(floor, MRERP_OPERATOR_PAYLOAD_VERSION)
    else:
        choice = " ".join(str(form_choice or "").split())[:FORM_CHOICE_MAX]
        route = str(pay_route or "").strip().lower()
        if choice:
            operator["form_choice"] = choice
        if route in PAY_ROUTES:
            operator["pay_route"] = route
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
