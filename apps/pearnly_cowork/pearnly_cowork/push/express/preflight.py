"""Deployed Express checks over explicit, caller-authorized snapshots; no IO.

A ready result is a payload projection, not a target authorization, reservation,
queue insertion, Companion connection, or completed external write.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional
from pearnly_cowork.contracts import push as direction_mod
from pearnly_cowork.contracts.push import clean_tax_id
from pearnly_cowork.push.express.account_rules import account_set_allowed, chart_codes, wire_account_set
from pearnly_cowork.push.express.common import PAYLOAD_VERSION
from pearnly_cowork.push.express.doc_sanity import check_document
from pearnly_cowork.push.express.mapper import build_express_payload
from pearnly_cowork.push.express.sales_mapper import build_express_sales_payload
from pearnly_cowork.push.express.history import flatten_history_for_mrerp
from pearnly_cowork.push.express import confidence

@dataclass(frozen=True)
class ExpressContext:
    enabled: bool
    today: date
    own_tax_id: str
    tenant_id: str
    mappings: dict
    supplier_profile: dict | None = None
    bank_index: tuple | list = ()
    prior_docnum: str | None = None

    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.today) is not date:
            raise ValueError("express.context_invalid")
        if not isinstance(self.mappings, dict):
            raise ValueError("express.context_invalid")


def _resolve_direction(flat, history, own_tax):
    direction = direction_mod.explicit_direction(flat, history)
    if direction is not None:
        return direction, ""
    own_tax = own_tax if flat.get("workspace_client_id") else ""
    return direction_mod.detect_by_tax(flat, own_tax), own_tax


def _attach_payment_evidence(flat, mappings, context):
    if flat.get("direction") == "purchase" and context.tenant_id and flat.get("workspace_client_id"):
        fields = flat.get("fields") if isinstance(flat.get("fields"), dict) else {}
        seller_tax = clean_tax_id(fields.get("seller_tax") or fields.get("seller_tax_id"))
        if seller_tax:
            mappings["_supplier_profile"] = deepcopy(context.supplier_profile)
    mappings["_bank_index"] = deepcopy(context.bank_index) or []


CHECK_KEYS = (
    "feature",
    "account_set",
    "payload_version",
    "direction",
    "direction_enabled",
    "document",
    "mapping",
    "chart",
    "confidence",
)


OK = "ok"


BLOCKED = "blocked"


PENDING = "pending"


@dataclass
class Check:
    key: str
    status: str
    reason: str = ""


@dataclass
class Preflight:
    """体检结果。reason=None 且非 disabled → ready(载荷通过当前预检);disabled → 特性关;其余 blocked。"""

    disabled: bool = False
    reason: Optional[str] = None  # 规范 manual reason(喂 enqueue · 与旧行为逐字一致)
    request_body: Optional[Dict[str, Any]] = None
    payload: Optional[Dict[str, Any]] = None
    direction: Optional[str] = None
    category: str = ""
    verdict: Any = None
    checks: List[Check] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return not self.disabled and self.reason is not None

    @property
    def ready(self) -> bool:
        return not self.disabled and self.reason is None

    def checks_json(self) -> List[Dict[str, str]]:
        return [{"key": c.key, "status": c.status, "reason": c.reason} for c in self.checks]


def _category_of(flat: Dict[str, Any]) -> str:
    fields = flat.get("fields") if isinstance(flat.get("fields"), dict) else {}
    return str(flat.get("category") or (fields or {}).get("category") or "").strip()


def _unknown_account(payload: Dict[str, Any], codes: set) -> Optional[str]:
    """返回首个不在科目表白名单里的分录科目码(None=全部合法)。挡死码/AI 乱码/缓存过期。
    POS 日汇总票的付款方式转账凭证科目(`pos.transfers`)同样核。"""
    pos = payload.get("pos") if isinstance(payload.get("pos"), dict) else {}
    for ln in [*(payload.get("lines") or []), *(pos.get("transfers") or [])]:
        acc = str((ln or {}).get("acc") or "").strip()
        if acc and acc not in codes:
            return acc
    return None


def _allowed_directions(config: Dict[str, Any]) -> tuple:
    """本连接处理的方向(进项/销项/两者)· 默认两者。"""
    raw = config.get("directions")
    if not raw:
        return ("purchase", "sales")
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(",") if x.strip()]
    out = tuple(str(x).lower() for x in raw)
    return out or ("purchase", "sales")


def _grade(history: Dict[str, Any], payload: Dict[str, Any], has_category: bool, direction: str):
    """置信判级(复用 confidence.grade · 按实际方向传)。失败返 None 放行。"""
    try:

        fields = history.get("fields") if isinstance(history.get("fields"), dict) else {}
        party = payload.get("customer") if direction == "sales" else payload.get("supplier")
        # confidence 的"可入账"门是采购/费用语义(销项不在其中)。销项按 Express 政策可过账,
        # 故以 purchase 口径复用其金额/卖家/票号/band 闸 —— direction 仅控可入账门,其余无关。
        grade_direction = "purchase" if direction == "sales" else direction
        return confidence.grade(
            amount=payload.get("total_amount"),
            vendor_name=(party or {}).get("name") or "",
            invoice_number=payload.get("ref_no") or "",
            document_type=str((fields or {}).get("document_type") or "tax_invoice"),
            direction=grade_direction,
            confidence_band=str(history.get("confidence") or ""),
            has_category=has_category,
            require_category=False,
            # 识别质量不拦推送（用户 2026-09-27）：票先识别、再由用户复核改好才推，按复核后的票推。
            # 金额 / 卖方 / 票号缺失与疑似重复仍拦 —— 那是推不了或会重复记账，不是识别好不好。
            require_confidence=False,
        )
    except Exception:
        pass  # Diagnostic logging belongs to the calling service.
        return None


def _fill_pending(checks: List[Check]) -> None:
    present = {c.key for c in checks}
    for k in CHECK_KEYS:
        if k not in present:
            checks.append(Check(k, PENDING))


def _block(
    pf: Preflight,
    key: str,
    *,
    reason: str,
    request_body: Dict[str, Any],
    check_reason: Optional[str] = None,
) -> Preflight:
    pf.checks.append(Check(key, BLOCKED, check_reason if check_reason is not None else reason))
    _fill_pending(pf.checks)
    pf.reason = reason
    pf.request_body = request_body
    return pf


def preflight_express(
    endpoint: Dict[str, Any],
    history: Dict[str, Any],
    *,
    context: ExpressContext,
    posting_kind: Optional[str] = None,
) -> Preflight:
    """对一条 history 跑全部前置条件,返回逐项体检结果(纯读 · 不入队不写库)。

    context:服务层冻结的身份主档、映射、供应商/银行/原凭证号与日期；本层绝不自查。
    posting_kind:本批过账去向(录入向导每批开关)· 销项/进项两个 mapper 都消费:销项据此扣
    库存结转 COGS,进项据此建库存品真入库(合起来才是一条闭环)· 解析口径见 posting_kind 模块。
    """
    endpoint, history = deepcopy(endpoint), deepcopy(history)
    config = (endpoint or {}).get("config") or {}
    pf = Preflight()

    if not context.enabled:
        pf.disabled = True
        pf.checks.append(Check("feature", BLOCKED, "express_disabled"))
        _fill_pending(pf.checks)
        return pf
    pf.checks.append(Check("feature", OK))

    # 账套白名单(逐端点 · 须 == 本端点配置账套)。
    account_set = str(config.get("account_set") or "").strip()
    if not account_set_allowed(account_set, endpoint, config.get("express_root")):
        return _block(
            pf,
            "account_set",
            reason=f"account_set_not_allowed:{account_set or 'none'}",
            request_body={"adapter": "express", "account_set": account_set},
        )
    pf.checks.append(Check("account_set", OK))

    # 载荷版本协商:小助手心跳上报过 max_payload_version 且低于当前契约 → 拦下(会推一份它
    # 解析不了的字段)。未上报(老客户端 · 未升级过心跳)→ 视同支持 1,绝不误拦现有推送。
    reported_pv = config.get("max_payload_version")
    if reported_pv is not None:
        try:
            supported_pv = int(reported_pv)
        except (TypeError, ValueError):
            supported_pv = PAYLOAD_VERSION  # 脏值读不出数 → 不拿它误拦,按支持最新处理
        if supported_pv < PAYLOAD_VERSION:
            return _block(
                pf,
                "payload_version",
                reason=f"payload_version_outdated:{supported_pv}",
                request_body={"adapter": "express", "max_payload_version": supported_pv},
            )
    pf.checks.append(Check("payload_version", OK))

    try:

        flat = flatten_history_for_mrerp(history)
        mappings = deepcopy(context.mappings)
        category = _category_of(flat)
        pf.category = category

        # 方向(确定性·税号锚点):显式标签优先,否则比对自家公司税号 × 票面 seller/buyer。
        direction, own_tax = _resolve_direction(flat, history, context.own_tax_id)
        if direction is None:
            # 区分两种判不出:主体没绑/税号没读到(subject_unbound)vs 票面对不上(ambiguous)。
            # 仅供 UI 提示;喂 enqueue 的规范 reason 仍是 direction_unknown。
            sub = "subject_unbound" if not own_tax else "ambiguous"
            return _block(
                pf,
                "direction",
                reason="direction_unknown",
                request_body={"adapter": "express", "history_id": str(flat.get("id") or "")},
                check_reason=sub,
            )
        flat["direction"] = direction  # 写回 → 下游同口径分流
        pf.direction = direction
        pf.checks.append(Check("direction", OK))

        if direction not in _allowed_directions(config):
            return _block(
                pf,
                "direction_enabled",
                reason=f"direction_not_enabled:{direction}",
                request_body={"adapter": "express", "direction": direction},
            )
        pf.checks.append(Check("direction_enabled", OK))

        # 单据防呆(确定性):外币/贷项/押金/未来日期/坏税号等"不该当普通票直推"的单据 → 转人工。
        doc_reason = check_document(flat.get("fields") or {}, flat, direction, today=context.today)
        if doc_reason:
            return _block(
                pf,
                "document",
                reason=doc_reason,
                request_body={"adapter": "express", "history_id": str(flat.get("id") or "")},
            )
        pf.checks.append(Check("document", OK))

        _attach_payment_evidence(flat, mappings, context)

        if direction == "sales":
            mres = build_express_sales_payload(
                flat, config=config, mappings=mappings, category=category, posting_kind=posting_kind,
                today=context.today, prior_docnum=context.prior_docnum
            )
        else:
            mres = build_express_payload(
                flat, config=config, mappings=mappings, category=category, posting_kind=posting_kind,
                today=context.today, prior_docnum=context.prior_docnum
            )
        if not mres.ok:
            body = {"adapter": "express", "manual_reason": mres.reason}
            if mres.items:
                # 库存类失败要靠它渲染补救卡(derive_stock_fix 读 request_body.items)。
                body["items"] = mres.items
            return _block(pf, "mapping", reason=mres.reason, request_body=body)
        payload = mres.payload
        # 带了选定存货码 / 部门码的载荷是 2:小助手报过支持的版本更低就拦下(旧版会静默忽略这两个键)。
        if reported_pv is not None and int(payload.get("payload_version") or 1) > supported_pv:
            return _block(pf, "payload_version", reason=f"payload_version_outdated:{supported_pv}",
                          request_body={"adapter": "express", "max_payload_version": supported_pv})
        pf.payload = payload
        payload.update(
            {
                "account_root": str(config.get("express_root") or "").strip(),
                "account_dir": str(config.get("account_dir") or account_set).strip(),
                "account_company": str(config.get("account_company") or "").strip(),
                "account_set_row": int(config.get("account_set_row") or 0),
            }
        )
        payload['account_set'] = wire_account_set(payload.get('account_set'), payload['account_dir'])
        # 补期初:会计在「补期初卡」填的期初(name/qty/unit_cost/date)由端点写进 history.merged_fields
        # (一次性·不落库),mapper 从 flat 构造 payload 时不读 merged_fields,故在此显式透传给小助手 —
        # 否则期初永远到不了小助手,补期初卡空转。小助手据此先写期初再过账(仅销项·库存卖需要垫库存)。
        opening = (history.get("merged_fields") or {}).get("opening_stock")
        if opening:
            payload["opening_stock"] = opening
        pf.checks.append(Check("mapping", OK))

        # 防御性复核白名单(mapper 已带 account_set · 与 Agent lease 同口径)· 命中归 account_set 项。
        if not account_set_allowed(
            payload.get("account_set"), endpoint, payload.get("account_root")
        ):
            for c in pf.checks:
                if c.key == "account_set":
                    c.status = BLOCKED
                    c.reason = "account_set_not_allowed"
            _fill_pending(pf.checks)
            pf.reason = "account_set_not_allowed"
            pf.request_body = payload
            return pf

        # 写前科目白名单(闸2):分录每个科目须 ∈ 账套上报科目表;未上报则跳过(不阻塞旧 Agent)。
        chart = chart_codes(config)
        if chart is not None:
            bad = _unknown_account(payload, chart)
            if bad:
                return _block(
                    pf, "chart", reason=f"account_not_in_chart:{bad}", request_body=payload
                )
        pf.checks.append(Check("chart", OK))

        verdict = _grade(history, payload, bool(category), direction)
        pf.verdict = verdict
        if verdict is not None and verdict.action != "post":
            reason = "low_confidence:" + ",".join(verdict.reasons)
            return _block(pf, "confidence", reason=reason, request_body=payload)
        pf.checks.append(Check("confidence", OK))

        return pf  # ready

    except Exception as e:
        pass  # Diagnostic logging belongs to the calling service.
        pf.reason = f"enqueue_error:{type(e).__name__}"
        pf.request_body = {"adapter": "express"}
        _fill_pending(pf.checks)
        return pf
