"""自动推送的档位与边界（数字会计员第 3 期第 3–5、7 条，设计见 docs/work/2026-10-16-da-phase3-design.md）。

纯函数，不连库、不依赖框架：推送服务（`push.automation.service`、`push.application.assess`）、扫描（`push.automation.sweep`）、
领单重核（`push.native.service.lease`）与公司档位的评估（`companies.automation`）共用。

- **档位按公司**：L0 只提议（所有公司起点）/ L1 按规则（A 类自动推）/ L2 整月（A、B 都自动推）；C 类一律照现有停下。
- **谁触发**：定时任务（任务默认值是人定的规则，A 类从 L0 起就照推，用户 10-16 定）、网页上传、LINE。CC 对话和待处理里人点的
  推送是人发起的，不经这里。
- **L2 推 B 类的全局前提**（`B_AT_L2`）与图片单（`push.express.automation.IMAGES_ALLOWED`）一样是写在代码里的常量，随发布改，
  不是租户开关：回放里 B 类单独过第 15 条达标线之前，L2 的 B 类仍只出提议。
- **没有新回读就暂停**：这家最近一次合格回读覆盖的会计期间早于本单期间两个月以上，靠档位放行的自动推改出提议（定时任务 A 类
  的基线推送不靠档位，不受这条影响）。
- **只派给能回写入核对的 CC 桌面版**（`VERIFY_MIN_VERSION`）：替这张单干活的电脑里有旧版就改出提议。
- **测试站点的路径守卫**：站点名合 `pearnly_core.execution.replay.TEST_SITES` 时，自动推的账套须在 ``C:\\PearnlyExpress\\`` 下
  （测试机的 CC 桌面版同时看得见 S 盘真账）。这是按站点身份的安全边界，不是功能开关。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from pearnly_cowork.push.express import choices as choice_rules

L0, L1, L2 = "L0", "L1", "L2"
TIERS = (L0, L1, L2)
TASK, UPLOAD, LINE = "task", "upload", "line"
TRIGGERS = (TASK, UPLOAD, LINE)
PUSH, PROPOSE, STOP = "push", "propose", "stop"
# L2 推 B 类的全局前提：回放里 B 类单独过第 15 条达标线才改成 True（随发布改）
B_AT_L2 = False
# 能在写入后回第 6 条核对结果（`verify`）的最低 CC 桌面版版本
VERIFY_MIN_VERSION = "1.8.0"
# 最近一次合格回读覆盖的会计期间早于本单期间超过这么多个月：靠档位的自动推改出提议
READBACK_LAG_MONTHS = 2
# 测试站点上自动推只写这个目录下的账套（规范键已小写、反斜杠）
TEST_ROOT = "c:\\pearnlyexpress\\"
# 提议冻结的每一项（`values`）与比对口径
PROPOSAL_KINDS = ("party", "item", "account", "payment", "vat", "department", "receipt")
# 档位不升不降的原因码与升降的原因码（存在 `Cowork Company Automation.reason`，界面按码翻译）
REASONS = ("readback", "written_mismatch", "changed")


def tier_of(value: Any) -> str:
    """库里 / 钩子给的档位 → 认得的档位；认不出（没有行、出错）按 L0。"""
    return value if value in TIERS else L0


def period_of(value: Any) -> str:
    """日期（`YYYY-MM-DD` 或 date）→ 会计期间 `YYYY-MM`；认不出回空串。"""
    text = value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else str(value or "")
    return text[:7] if len(text) >= 7 and text[4] == "-" and text[:4].isdigit() and text[5:7].isdigit() else ""


def _index(period: str) -> int:
    return int(period[:4]) * 12 + int(period[5:7]) - 1


def months_between(earlier: str, later: str) -> int:
    """两个 `YYYY-MM` 之间隔几个月（later − earlier）。"""
    return _index(later) - _index(earlier)


def readback_ok(last: Any, period: Any) -> bool:
    """这家最近一次合格回读覆盖的期间 ``last`` 不早于本单期间 ``period`` 两个月以上。没有合格回读、本单没有期间都不算。"""
    last, period = period_of(last), period_of(period)
    return bool(last and period) and months_between(last, period) <= READBACK_LAG_MONTHS


def action(cls: str, tier: str, trigger: str, *, readback: bool = True) -> Tuple[str, str]:
    """这张单这次怎么办：``(push | propose | stop, 原因码)``。

    - C 类一律 ``stop``（照现有停下）；
    - 定时任务：A 类照推（基线，不看档位与回读）；B 类只在 L2、全局前提成立、回读不旧时推，否则提议；
    - 网页上传 / LINE：A 类在 L1 / L2 且回读不旧时推；B 类同定时任务；其余提议。"""
    tier = tier_of(tier)
    if cls not in ("A", "B"):
        return STOP, ""
    if trigger == TASK and cls == "A":
        return PUSH, ""
    if trigger not in TRIGGERS:
        return PROPOSE, "trigger"
    if cls == "B":
        if tier != L2 or not B_AT_L2:
            return PROPOSE, "tier"
    elif tier == L0:
        return PROPOSE, "tier"
    if not readback:
        return PROPOSE, "readback_stale"
    return PUSH, ""


def version_tuple(value: Any) -> tuple:
    try:
        return tuple(int(part) for part in str(value or "").strip().split("."))
    except ValueError:
        return (0,)


def verify_capable(versions: Iterable[Any]) -> bool:
    """替这张单干活的电脑（``versions`` 是它们报的 CC 桌面版版本）都能回写入核对：至少一台，且没有一台旧版（旧版也可能领到）。"""
    versions = list(versions)
    return bool(versions) and all(version_tuple(item) >= version_tuple(VERIFY_MIN_VERSION) for item in versions)


def test_path_ok(account_key: Any) -> bool:
    """测试站点上自动推只写 ``C:\\PearnlyExpress\\`` 下的账套（规范键：小写、反斜杠）。"""
    key = str(account_key or "").strip().replace("/", "\\").casefold()
    return key.startswith(TEST_ROOT) and len(key) > len(TEST_ROOT)


# ── 冻结与指纹 ──────────────────────────────────────────────────────────────────


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def subjects(direction: str, fields: Mapping[str, Any], *, pos: bool = False) -> Dict[str, list]:
    """这张单用得着的账套记忆 / 公司规则的归类键（领单重核只比这些，别的供应商被人选了不作废这一张）：对方档按对方 + 分店，
    科目 / 付款 / 抵进项按对方，存货按对方开头（``…|*``），部门整份账套一个（``*``），POS 收款科目只在 POS 票上、全部都算。
    规则同 `push.express.choices` 的归类键。"""
    side = "buyer" if direction == "sales" else "seller"
    name, tax, branch = fields.get(f"{side}_name"), fields.get(f"{side}_tax"), fields.get(f"{side}_branch")
    supplier = choice_rules.supplier_subject(direction, tax, name)
    return {"party": [choice_rules.party_subject(direction, tax, branch, name)], "account": [supplier],
            "payment": [supplier], "vat": [supplier], "item": [supplier + "|*"], "department": ["*"],
            "receipt": ["*"] if pos else []}


def _within(kind: str, subject: str, scope: Optional[Mapping[str, Sequence[str]]]) -> bool:
    if scope is None:
        return True
    for pattern in scope.get(kind) or ():
        pattern = str(pattern)
        if subject == pattern or (pattern.endswith("*") and subject.startswith(pattern[:-1])):
            return True
    return False


def fingerprint(remembered: Mapping[str, Mapping[str, str]], rules: Iterable[Mapping[str, Any]],
                scope: Optional[Mapping[str, Sequence[str]]] = None) -> str:
    """账套记忆与公司规则的版本指纹：这张单用得着的（``scope``，`subjects`；None = 全部）任一项的值变了指纹就变（领单时比，
    第 5 条）。"""
    memory = {kind: {subject: value for subject, value in sorted((items or {}).items()) if _within(kind, subject, scope)}
              for kind, items in sorted((remembered or {}).items())}
    picked = sorted((str(rule.get("kind") or ""), str(rule.get("subject") or ""), str(rule.get("branch") or ""),
                     str(rule.get("value") or "")) for rule in rules or ()
                    if isinstance(rule, Mapping) and _within(str(rule.get("kind") or ""), str(rule.get("subject") or ""),
                                                            scope))
    return digest({"remembered": {kind: items for kind, items in memory.items() if items}, "rules": picked})


def automation_hash(record_version: Any, tier: str, memory: str) -> str:
    """领单重核的那一项（并进 ``basis_hash`` 之外）：票的版本、这家当前档位、账套记忆与公司规则的指纹。"""
    return digest({"record_version": int(record_version or 0), "tier": tier_of(tier), "memory": str(memory or "")})


def frozen(cls: str, tier: str, trigger: str, reasons: Sequence[str], hash_value: str,
           scope: Optional[Mapping[str, Sequence[str]]] = None) -> Dict[str, Any]:
    """冻结目标里的 ``automation``（第 5 条）：类别、判时档位、谁触发、B 类 CC 判断的项，与领单重核用的指纹及其范围。"""
    return {"class": cls, "tier": tier_of(tier), "trigger": trigger, "reasons": list(reasons or ())[:20],
            "hash": hash_value, "scope": {kind: list(items) for kind, items in (scope or {}).items()}}


# ── 提议（第 7 条） ─────────────────────────────────────────────────────────────


def _money(value: Any) -> str:
    try:
        amount = Decimal(str(value if value is not None else "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return ""
    return format(amount.quantize(Decimal("0.01")), "f") if amount.is_finite() else ""


def values(payload: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """一份 Express 载荷里每一项实际写成什么（提议与人推时比对用）：对方档代码、存货代码、费用 / 收入科目、单据类型（现 / 赊）、
    抵不抵进项、部门、POS 非现金收款科目。没有的项不列。"""
    payload = payload if isinstance(payload, Mapping) else {}
    side = "customer" if payload.get("direction") == "sales" else "supplier"
    party = payload.get(side) if isinstance(payload.get(side), Mapping) else {}
    out: Dict[str, str] = {}
    code = str(party.get("code") or "").strip()
    if code or party:
        out["party"] = code or "new"
    items = sorted({str(item.get("code") or "").strip()
                    for item in payload.get("items") or [] if isinstance(item, Mapping)
                    and str(item.get("item_mode") or "").startswith("stock")} - {""})
    if items:
        out["item"] = ",".join(items)
    for kind, key in (("account", "items_account"), ("payment", "doctype"), ("department", "depcod")):
        value = str(payload.get(key) or "").strip()
        if value:
            out[kind] = value.upper() if kind == "payment" else value
    if payload.get("direction") != "sales":
        capitalized = _money(payload.get("vat_capitalized"))
        claimed = _money(payload.get("vat_amount"))
        if capitalized and Decimal(capitalized) > 0:
            out["vat"] = "capitalize"
        elif claimed and Decimal(claimed) > 0:
            out["vat"] = "claim"
    pos = payload.get("pos") if isinstance(payload.get("pos"), Mapping) else {}
    receipts = sorted({str(item.get("acc") or "").strip() for item in pos.get("transfers") or []
                       if isinstance(item, Mapping)} - {""})
    if receipts:
        out["receipt"] = ",".join(receipts)
    return out


def proposal(cls: str, tier: str, trigger: str, payload: Optional[Mapping[str, Any]], fields: Mapping[str, Any],
             *, at: Any, items: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """冻结在票上的提议（``auto_proposal``）：CC 准备好的做法（每一项的值）、日期、单号、对方税号、三项金额，与判时的类别、
    档位、触发与每项的档。人点推送时照常重判，推送准备拿它比对（`compare`）。"""
    payload = payload if isinstance(payload, Mapping) else {}
    side = "customer" if payload.get("direction") == "sales" else "supplier"
    party = payload.get(side) if isinstance(payload.get(side), Mapping) else {}
    return {"class": cls, "tier": tier_of(tier), "trigger": trigger, "at": str(at or ""),
            "values": values(payload), "items": dict(items or {}),
            "date": str(fields.get("date") or fields.get("invoice_date") or "")[:10],
            "ref_no": str(payload.get("ref_no") or fields.get("invoice_number") or ""),
            "party_tax": str(party.get("tax_id") or fields.get("buyer_tax" if side == "customer" else "seller_tax")
                             or ""),
            "before_vat": _money(payload.get("base_amount") or fields.get("subtotal")),
            "vat": _money(payload.get("vat_amount") or fields.get("vat")),
            "total": _money(payload.get("total_amount") or fields.get("total_amount"))}


def compare(proposed: Optional[Mapping[str, Any]], payload: Optional[Mapping[str, Any]], *,
            chosen: Iterable[str] = (), edited: bool = False) -> Optional[Dict[str, Any]]:
    """人推一张有提议的单时，推出去的与提议比（第 7 条）：``{"class", "edited", "changed": [人另选的项], "stale": [因规则 /
    记忆变化而不同的项]}``；没有提议回 None。``chosen``：这次人当场选过的类别（来源是人）；``edited``：推前改过票面。
    「过时」的单不进样本（`companies.automation_rules.tally`）。"""
    if not isinstance(proposed, Mapping) or proposed.get("class") not in ("A", "B"):
        return None
    before = proposed.get("values") if isinstance(proposed.get("values"), Mapping) else {}
    after = values(payload)
    chosen = set(chosen or ())
    differ = [kind for kind in PROPOSAL_KINDS if (before.get(kind) or "") != (after.get(kind) or "")]
    return {"class": proposed["class"], "edited": bool(edited),
            "changed": [kind for kind in differ if kind in chosen],
            "stale": [kind for kind in differ if kind not in chosen]}


__all__ = ["B_AT_L2", "L0", "L1", "L2", "LINE", "PROPOSAL_KINDS", "PROPOSE", "PUSH", "READBACK_LAG_MONTHS", "STOP",
           "TASK", "TEST_ROOT", "TIERS", "TRIGGERS", "UPLOAD", "VERIFY_MIN_VERSION", "action", "automation_hash",
           "compare", "digest", "fingerprint", "frozen", "months_between", "period_of", "proposal", "readback_ok",
           "subjects", "test_path_ok", "tier_of", "values", "verify_capable", "version_tuple"]
