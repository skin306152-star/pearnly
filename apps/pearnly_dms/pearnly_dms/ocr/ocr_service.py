# -*- coding: utf-8 -*-
"""身份证识别的**共用服务层**（LINE 入口与网页入口走同一条路）。

照搬老产品 `services/erp/dms_id_ocr.py`：端点解析 + 余额闸 + PDF 栅格化 +
专用身份证识别 + 校验位标记。出错抛 `DmsOcrError`，带 code，上层按 code 出人话。

### 一处**未搬**，已标出（不是设计变更）

老产品这里有**余额闸**（`_billing_gate`）：余额不足直接 402 拦下，绝不"先识别再扣成负"。
我们的计费子系统还没搬，所以这里现在是**放行**。这一点在 `_billing_gate` 里写得很显眼 ——
搬计费时把那段按原逻辑接上即可，其余代码不用动。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple
from frappe import _

logger = logging.getLogger(__name__)

# 身份证图片大小上限（MB）· 与发票 OCR 同量级 · 防超大文件拖垮模型
MAX_ID_CARD_MB = 15


class DmsOcrError(Exception):
    """识别前置/识别失败的类型化错误。"""

    def __init__(self, code: str, http_status: int, detail: Any):
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.detail = detail


def resolve_dms_endpoint(user: str, endpoint_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """选一个 `mrerp_dms` 端点。

    **带 id 时必须校验它确实还是 mrerp_dms 且没被停用** —— 停用是老板的收权动作，
    会话里残留的 endpoint_id 不得绕过它。不带 id 时取该账号下第一个启用的。

    返回的 dict 里 `id` 和 `name` 都填端点文档名（我们下游认文档名）。
    """
    import frappe

    dt = "Pearnly ERP Endpoint"
    try:
        if endpoint_id:
            if not frappe.db.exists(dt, endpoint_id):
                return None
            info = frappe.db.get_value(dt, endpoint_id, ["adapter", "enabled", "owner_user"], as_dict=True) or {}
            if info.get("adapter") != "mrerp_dms" or not info.get("enabled"):
                return None
            owner = info.get("owner_user")
            if owner and str(owner) != str(user):
                return None
            return {"id": endpoint_id, "name": endpoint_id}
        name = None
        if user:
            name = frappe.db.get_value(
                dt, {"adapter": "mrerp_dms", "enabled": 1, "owner_user": user}, "name"
            )
        if not name:
            # 没有归属给这个账号的，退一步取任意启用的（与老产品"取第一个"一致）
            name = frappe.db.get_value(dt, {"adapter": "mrerp_dms", "enabled": 1}, "name")
        return {"id": name, "name": name} if name else None
    except Exception:
        logger.warning("[dms ocr] 解析端点失败", exc_info=True)
        return None


def recent_dms_customer_ids_by_tail(people_id_tail: str, limit: int = 5) -> list:
    """按证号**尾 4 位**找本租户最近推过的客户号 —— 台账兜底。

    为什么要它：DMS 的搜索对新客户有"隐身期"（刚建/刚改的客户对新会话隐身数分钟）。
    查重前先看台账，能省掉退避重试那几秒，也少一次"误标新客"。

    取数来自推送台账（`Pearnly Push Log`）里记的 `people_id_tail` 与业务键。
    """
    import frappe

    tail = str(people_id_tail or "").strip()
    if not tail:
        return []
    try:
        rows = frappe.get_all(
            "Pearnly Push Log",
            filters={
                "business_key": ("!=", ""),
                # ⚠️ 这里要 `%%`：字符串里那个字面 `%` 不转义的话，`%` 会被当成格式符
                # （实测报 `unsupported format character '"'`，台账兜底整段失效）。
                "request_body": ("like", '%%"people_id_tail": "%s"' % tail),
            },
            fields=["business_key"],
            order_by="creation desc",
            limit=limit,
        )
        out = []
        for r in rows:
            key = str(r.get("business_key") or "")
            if key and key not in out:
                out.append(key)
        return out
    except Exception:
        logger.warning("[dms ocr] 台账兜底取数失败（按无候选继续）", exc_info=True)
        return []


def ensure_image_bytes(content: bytes, content_type: str = "") -> bytes:
    """识别器只吃图片。**共享上传流水线会把图片包成单页 PDF**（相机/相册图片在到达接口前
    经 `imagesToPdf` 转 `photo_*.pdf`），直接按图读 PDF 会失败 —— 这里把 PDF 首页
    栅格化成 PNG 兜底；非 PDF 字节原样返回。

    300 DPI：与发票 OCR 渲染同量级，身份证小字够清晰。
    """
    is_pdf = content[:5] == b"%PDF-" or (content_type or "").strip().lower() == "application/pdf"
    if not is_pdf:
        return content
    try:
        try:
            import pymupdf as fitz  # 新版官方名（无弃用警告）
        except ImportError:
            import fitz  # 老名字，仍然可用（老产品用这个）

        doc = fitz.open(stream=content, filetype="pdf")
        if doc.page_count < 1:
            doc.close()
            return content
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72.0, 300 / 72.0), alpha=False)
        png = pix.tobytes("png")
        doc.close()
        return png or content
    except Exception as exc:  # noqa: BLE001
        logger.warning("[dms ocr] PDF→图栅格化失败 · 退回原字节: %s", exc)
        return content


def editable_id_card(id_card: Dict[str, Any]) -> Dict[str, Any]:
    """面板/复述可用的识别字段（完整 · 非遮蔽 · 用户自己的扫描件）。"""
    addr = id_card.get("address") or {}
    first = id_card.get("first_name", "")
    last = id_card.get("last_name", "")
    return {
        "prefix_name": id_card.get("prefix_name", ""),
        "first_name": first,
        "last_name": last,
        "name": f"{first} {last}".strip(),
        "people_id": id_card.get("people_id", ""),
        "birthday_be": id_card.get("birthday_be", ""),
        "issue_date_be": id_card.get("issue_date_be", ""),
        "expiry_date_be": id_card.get("expiry_date_be", ""),
        "phone": "",
        "address": {
            "house_no": addr.get("house_no", ""),
            "moo": addr.get("moo", ""),
            "soi": addr.get("soi", ""),
            "road": addr.get("road", ""),
            "province": addr.get("province", ""),
            "district": addr.get("district", ""),
            "subdistrict": addr.get("subdistrict", ""),
            "zipcode": addr.get("zipcode", ""),
        },
    }


def _billing_gate(pages: int = 1) -> Dict[str, Any]:
    """余额闸：钱不够就不许开工。

    身份证 OCR 走 **A 档**（高精，见 `services/ocr` 的 task 覆写表）——
    银行件、总账、VAT、身份证都是财务件，跟发票那种 B 档是两种业务。

    **三条出路各说各的**（老产品栽过：把系统故障报成"余额不足"，
    结果会计去充了一笔本来就够的钱）：

    - 钱够 → 放行；
    - `insufficient_balance` → 402，界面引导去充值；
    - `lookup_error` → 503，是**我们**查不出来，让用户稍后重试。
    """
    from pearnly_core import billing_client

    status = billing_client.status("pdf", pages, tier="A")
    if status.get("allowed"):
        return status

    code = status.get("error_code") or "lookup_error"
    if code == "lookup_error":
        raise DmsOcrError(
            "billing.lookup_error", 503,
            {"code": "billing.lookup_error", "msg": status.get("reason") or _("The balance could not be read")},
        )
    raise DmsOcrError(
        "insufficient_balance", 402,
        {
            "code": "insufficient_balance",
            "balance_thb": status.get("balance_thb"),
            "estimate_thb": status.get("estimate_thb"),
        },
    )


def _charge_id_card(reference: str = "") -> dict:
    """识别成功之后报账。

    只在这一刻扣：**识别失败不收费**（老产品全站口径）。报账失败只记日志，
    绝不把已经做完的识别说成失败 —— 记账是记在后面的账，不是识别的前置条件。
    """
    from pearnly_core import billing_client

    return billing_client.charge(
        "dms", "pdf", 1, tier="A", reference=reference,
        description=_("ID card recognition (tier A)"),
    )


def _flag_bad_id_checksum(ocr: Dict[str, Any]) -> None:
    """身份证号 mod-11 校验位对不上 → 标 `needs_review` 并追加 `people_id_checksum`。

    **不抛错**：校验只降信心，最终由人复核定夺。空号已由识别归一层列进 missing，
    这里只管"读满 13 位但校验位错"。
    """
    from pearnly_dms.contracts.id_validate import is_valid_thai_id

    pid = str((ocr.get("id_card") or {}).get("people_id") or "").strip()
    if not pid or is_valid_thai_id(pid):
        return
    ocr["needs_review"] = True
    missing = ocr.setdefault("missing_fields", [])
    if "people_id_checksum" not in missing:
        missing.append("people_id_checksum")


def recognize_id_card(
    user: str,
    content: bytes,
    filename: str,
    content_type: str = "",
    endpoint_id: Optional[str] = None,
    billing_reference: str = "",
) -> Tuple[Dict[str, Any], Dict[str, Any], int]:
    """选端点 + 识别身份证 + 打校验位标记。返回 `(ep, ocr, elapsed_ms)`。

    同步重活（几秒到十几秒），调用方负责放到后台任务里，别堵住 webhook。
    """
    if not content:
        raise DmsOcrError("ocr.empty_file", 400, "ocr.empty_file")
    if len(content) > MAX_ID_CARD_MB * 1024 * 1024:
        raise DmsOcrError(
            "ocr.file_too_large", 400, {"code": "ocr.file_too_large", "mb": MAX_ID_CARD_MB}
        )
    ep = resolve_dms_endpoint(user, endpoint_id)
    if not ep:
        raise DmsOcrError("dms.no_endpoint", 400, "dms.no_endpoint")
    _billing_gate()

    image_bytes = ensure_image_bytes(content, content_type)
    t0 = time.time()
    try:
        from pearnly_core import ocr as _ocr
        from pearnly_dms.ocr import ocr_id_card as _idcard

        ocr = _idcard.extract(image_bytes)
    except _ocr.OcrUnavailable as exc:
        # ⚠️ **调不动模型 ≠ 读不出**：限流/配额是基础设施问题，绝不能报成"证件识别不了"
        # （否则运维看不到，用户以为系统认不出他的卡）。这条是搬 OCR 时专门分开的。
        logger.warning("[dms ocr] 调不动模型: %s", exc)
        _record_ocr("failed", filename, error=_("Could not call the model: {0}").format(str(exc)[:200]), user=user)
        raise DmsOcrError(
            "ocr.unavailable", 503, {"code": "ocr.unavailable", "msg": str(exc)[:200]}
        )
    except Exception as exc:  # noqa: BLE001
        is_unreadable = type(exc).__name__ == "IdCardExtractError"
        if not is_unreadable:
            logger.exception("dms id-card OCR failed")
        _record_ocr("failed", filename, error=str(exc)[:200], user=user)
        raise DmsOcrError(
            "ocr.id_card_unreadable" if is_unreadable else "ocr.failed",
            422 if is_unreadable else 500,
            {
                "code": "ocr.id_card_unreadable" if is_unreadable else "ocr.failed",
                "msg": str(exc)[:200] if is_unreadable else type(exc).__name__,
            },
        )
    elapsed = int((time.time() - t0) * 1000)
    _flag_bad_id_checksum(ocr)
    # LINE retries reuse the message identity; a different card must be a new charge.
    # The former per-user reference is not an idempotency key.
    from uuid import uuid4
    reference = billing_reference or f"idcard:{uuid4().hex}"
    charge = _charge_id_card(reference=reference)
    # 识别记录：读出来了但校验位不对的标 needs_review（要人看一眼），其余算 ok
    _record_ocr(
        "needs_review" if ocr.get("needs_review") else "ok",
        filename,
        user=user,
        charged=charge.get("charged_thb"),
        detail=(ocr.get("id_card") or {}).get("thai_name") or "",
        reference=reference,
    )
    return ep, ocr, elapsed


def _record_ocr(status: str, filename: str, *, user: str = "", charged=None,
                detail: str = "", error: str = "", reference: str = "") -> None:
    """写一条识别记录。**失败也不许影响识别本身** —— 落不上就只记日志。"""
    try:
        from pearnly_core import ocr_record

        ocr_record.record(
            "dms", status, filename=filename, page_count=1, charged_thb=charged,
            reference=reference, detail=detail, error=error, user=user,
        )
    except Exception:  # noqa: BLE001
        logger.exception("写识别记录失败（不影响识别本身）")
