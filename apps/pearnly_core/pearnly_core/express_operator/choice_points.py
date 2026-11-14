"""代录时账本可能弹出的框的登记表（多产品共用，纯契约：不依赖 Frappe、不读库、不做 IO；总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 第 10 节）。

用户 11-14：引导式的作用是「把一切可能会发生的问题、要用户做的选择提前备好」，代录开跑后不再冒出新问题。站点要在开始前问，就得先知道
账本会弹哪些框——这张表就是那份清单。CC 桌面版的配方（``companion/.../recipes/*.json`` 的 ``dialogs``）是「遇到了怎么点」，这里是
「它是哪一类、开始前要不要问、问哪一题」；两边按名字一一对上、共同的字段同值，由 ``tests/test_operator_choice_points.py`` 比对：
**配方里出现、这里没登记的框 = 闸门红**（反过来也红）。

每个框一行：

- ``cls``：:data:`NOTICE` 只是知会（只有确定钮，自动点过）· :data:`CHOICE` 业务选择（几个按钮在账上意思不同）·
  :data:`BLOCK` 账本拒收这张单 · :data:`SESSION` 登录 / 连接类（整批的事，不是这张单的事）。
- ``reason``：落成哪个代录原因码（``contracts.REASONS``；``master_missing`` 由引擎按位置拆成 ``party_missing`` / ``item_missing``）。
- ``ask``（只有 CHOICE）：开始前问哪一题——``write_precheck`` 的题类；``answer``：答案走载荷的哪个键下发、选项码对哪个按钮。
  ``answer`` 为空 = **现在还没接上**（CC 桌面版遇到这个框只会按配方的默认钮点，或转人），登记在 :data:`NOT_WIRED`，只许减少。
- ``verified``：有没有原生实录（哪天哪台机器在配方的说明里）。MR.ERP 的多数还没有。

这里只登记、不改行为：配方与引擎怎么点照旧。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

NOTICE, CHOICE, BLOCK, SESSION = "notice", "choice", "block", "session"
CLASSES = (NOTICE, CHOICE, BLOCK, SESSION)
EXPRESS, MRERP = "express", "mrerp"
# 配方与登记表必须同值的字段（配方里有才比）
SHARED_FIELDS = ("match", "auto", "button", "button_by_srv_vattyp", "outcome", "reason", "close", "verified")
ACCRUAL, CASH_BASIS = "เกณฑ์สิทธิ", "เกณฑ์เงินสด"

EXPRESS_DIALOGS: List[Dict[str, Any]] = [
    # 明细行的单位对不上：框不自动点（引擎在录明细时自己处理，`engine.close_unit_dialog`）；开始前由「单位」那一题定
    {"name": "unit", "cls": CHOICE, "match": "หน่วยนับ", "auto": False, "ask": "unit", "answer": None, "verified": True},
    {"name": "picker", "cls": BLOCK, "match": "ค้นหา", "auto": True, "outcome": "blocked", "reason": "master_missing",
     "close": "ยกเลิก", "verified": True},
    {"name": "duplicate_ref", "cls": NOTICE, "match": "\\b1029\\b", "auto": True, "button": "ตกลง", "reason": "duplicate_ref",
     "verified": True},
    # 赊购带服务品：进项税按权责还是收付实现。配方现在一律点权责；开始前那一题（`express_rules.push.choices` 的服务品进项税口径）
    # 答了收付实现也不会按它点——没接上
    {"name": "service_vat_basis", "cls": CHOICE, "match": "ภาษีซื้อของสินค้าบริการ", "auto": True, "button": ACCRUAL,
     "ask": "service_vat", "answer": None, "options": [{"code": "1", "button": ACCRUAL}, {"code": "2", "button": CASH_BASIS}],
     "verified": True},
    # 赊销带服务品：销项税口径。答案走载荷表头 ``srv_vattyp``，2 点收付实现，其余点权责
    {"name": "service_output_vat_basis", "cls": CHOICE, "match": "ภาษีขายของสินค้าบริการ", "auto": True, "button": ACCRUAL,
     "button_by_srv_vattyp": {"2": CASH_BASIS}, "ask": "service_vat", "answer": "srv_vattyp",
     "options": [{"code": "1", "button": ACCRUAL}, {"code": "2", "button": CASH_BASIS}], "verified": True},
    {"name": "no_history", "cls": NOTICE, "match": "\\b4108\\b", "auto": True, "button": "ตกลง", "verified": True},
    {"name": "never_bought", "cls": NOTICE, "match": "\\b4079\\b", "auto": True, "button": "ตกลง", "verified": True},
    {"name": "zero_price_list", "cls": NOTICE, "match": "\\b4107\\b", "auto": True, "button": "ตกลง", "verified": True},
    {"name": "stock_warning", "cls": NOTICE, "match": "\\b605\\b", "auto": True, "button": "ตกลง", "verified": True},
    {"name": "negative_stock", "cls": BLOCK, "match": "\\b4224\\b|\\b4091\\b", "auto": True, "outcome": "blocked",
     "reason": "negative_stock", "verified": True},
    {"name": "period_outside", "cls": BLOCK, "match": "Message#\\.\\s*70\\b", "auto": True, "outcome": "blocked",
     "reason": "period_outside", "verified": True},
    {"name": "license", "cls": SESSION, "match": "-330\\b", "auto": True, "outcome": "blocked", "reason": "license",
     "verified": True},
    {"name": "network", "cls": SESSION, "match": "-70\\b|90621|Reading File", "auto": True, "outcome": "crash",
     "reason": "network_wait", "verified": True},
]

MRERP_DIALOGS: List[Dict[str, Any]] = [
    {"name": "kicked", "cls": SESSION, "match": "มีผู้ Login ซ้ำ", "outcome": "kicked", "reason": "account_in_use", "verified": True},
    {"name": "not_logged_in", "cls": SESSION, "match": "กรุณา Login เข้าสู่ระบบ", "outcome": "kicked", "reason": "account_in_use",
     "verified": False},
    {"name": "bad_login", "cls": SESSION, "match": "User หรือ Password ไม่ถูกต้อง", "outcome": "login_failed",
     "reason": "login_failed", "verified": False},
    {"name": "period", "cls": BLOCK, "match": "วันที่ไม่อยู่ในรอบบัญชี|ย้อนหลังได้ถึง", "outcome": "blocked",
     "reason": "period_outside", "verified": False},
    {"name": "required", "cls": BLOCK,
     "match": "^กรุณาป้อน|^ป้อน.*ให้ครบ|^กรุณาบันทึกรหัสสินค้า|ยอดสุทธิต้องมากกว่าหรือเท่ากับศูนย์",
     "outcome": "blocked", "reason": "recipe_mismatch", "verified": False},
    {"name": "save_error", "cls": BLOCK, "match": "^Error", "outcome": "blocked", "reason": "unknown_screen", "verified": False},
]

DIALOGS: Dict[str, List[Dict[str, Any]]] = {EXPRESS: EXPRESS_DIALOGS, MRERP: MRERP_DIALOGS}
# 配方回的原因不是代录原因码本身、由引擎再拆的：登记它拆成哪几个
REASON_ALIASES = {"master_missing": ("party_missing", "item_missing")}
# 业务选择框里「开始前问了、但运行时不会按答案点」的（账本, 名字）：存量，只许减少（接上一个删一个）
NOT_WIRED = frozenset({(EXPRESS, "unit"), (EXPRESS, "service_vat_basis")})


def of(ledger: str) -> List[Dict[str, Any]]:
    return [dict(row) for row in DIALOGS.get(ledger, ())]


def choices(ledger: str) -> List[Dict[str, Any]]:
    """这种账本登记过的业务选择框（开始前要备好答案的那些）。"""
    return [row for row in of(ledger) if row["cls"] == CHOICE]


def reasons(row: Mapping[str, Any]) -> tuple:
    """这个框落成的代录原因码（没有回空）。"""
    reason = row.get("reason")
    return REASON_ALIASES.get(reason, (reason,)) if reason else ()


def differences(ledger: str, recipe_dialogs) -> List[str]:
    """登记表与一份配方的 ``dialogs`` 哪里对不上（空 = 一致）：名字要一一对上，配方里有的共同字段要同值。"""
    mine = {row["name"]: row for row in DIALOGS.get(ledger, ())}
    theirs = {row.get("name"): row for row in recipe_dialogs or ()}
    out = [f"{ledger}: {name} is in the recipe but not registered" for name in theirs if name not in mine]
    out += [f"{ledger}: {name} is registered but not in the recipe" for name in mine if name not in theirs]
    for name in mine.keys() & theirs.keys():
        for field in SHARED_FIELDS:
            if field in theirs[name] and theirs[name][field] != mine[name].get(field):
                out.append(f"{ledger}: {name}.{field} differs (recipe {theirs[name][field]!r}, registered {mine[name].get(field)!r})")
            if field in mine[name] and field not in theirs[name] and field not in ("reason", "verified"):
                out.append(f"{ledger}: {name}.{field} is registered but the recipe has none")
    return sorted(out)


__all__ = ["BLOCK", "CHOICE", "CLASSES", "DIALOGS", "EXPRESS", "MRERP", "NOTICE", "NOT_WIRED", "REASON_ALIASES", "SESSION",
           "SHARED_FIELDS", "choices", "differences", "of", "reasons"]
