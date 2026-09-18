# -*- coding: utf-8 -*-
"""订车单**只提交一次**，并把落库后的那张单核对清楚才敢报成功。

这是整条链上最不能出错的一环：写出去的单是**业务凭证**，重发会多一张单、不核实就报成功
则是谎报。所以这里的设计全围着两件事转：

1. **只有"明确的单号重复"才允许再写一次。** 其它任何错误（被拒、超时、断连）都不重发 ——
   超时/断连可能发生在**提交之后**，此时回读是安全的、再 POST 不是。
2. **结果没核出来就不许说成功**：抛 `DmsBookingOutcomeUnknown`（`submitted=True`、
   `retry_safe=False`），由调用方保留草稿并禁止新的 POST。

回读全程只读（列表搜索 + 详情表单），**绝不重发 `drfcbc/new.php`**。

照搬老产品 `services/erp/mrerp_dms_booking_submit.py`（线上基准 `501589f0`），逻辑未改。
"""

from __future__ import annotations

import hashlib
import logging
import time

from pearnly_dms import dms_docno
from pearnly_dms.dms_booking_readback import (
    MAX_READBACK_CANDIDATE_IDS,
    READBACK_MAX_ATTEMPTS,
    STAGE_AMBIGUOUS,
    STAGE_SEARCH_EMPTY,
    STAGE_VERIFIED,
    evaluate_candidate,
    merge_stage,
    page_is_last,
    parse_row_ids,
    readback_backoff_seconds,
)
from pearnly_dms.dms_client_base import DmsClientError

logger = logging.getLogger(__name__)


class _LegacyDocnoState:
    """兼容"完整号即 `txtdocno`"的旧形态（无原生两栏/隐藏字段可用时）。

    真实建单路径一律传 `DMSBookingAutonumState`；这里只是让旧形态仍能跑重复号 bump
    （末尾数字 +1），不参与原生前缀/隐藏字段。
    """

    __slots__ = ("docno",)

    def __init__(self, docno: str):
        self.docno = docno

    def with_docno(self, docno: str):
        return _LegacyDocnoState(docno)


# 原生订车单列表搜索（只读）。**绝不含 `drfcbc/new.php`** —— 回读永不写。
_BOOKING_LIST_PATH = "drfcbc/component/showdata.php"
_BOOKING_FORM_PATH = "drfcbc/form.php"
_SEARCH_PAGE_SIZE = 30
_MAX_SEARCH_PAGES = 2


class DmsBookingOutcomeUnknown(DmsClientError):
    """已经**写过一次**、但结果没核出来。调用方必须保留草稿并禁止再次 POST。"""

    def __init__(
        self,
        docno: str,
        *,
        http_status=None,
        body: str = "",
        stage: str = "",
        source: str = "",
        readback_attempts: int = 0,
    ):
        super().__init__(
            f"booking {docno!r} was submitted but its stored result could not be verified",
            "ERR_DMS_BOOKING_OUTCOME_UNKNOWN",
        )
        self.booking_no = docno
        self.response_body = {
            "booking_no": docno,
            "submission_status": "unknown",
            "submitted": True,
            "retry_safe": False,
            "http_status": http_status,
            # 定因阶段（search_empty / identity_mismatch / critical_mismatch / ambiguous）
            # + 只读来源/尝试次数。都没有单号以外的业务值，更无 PII/token。
            "readback_stage": stage or STAGE_SEARCH_EMPTY,
            "readback_source": source or "unknown",
            "readback_attempts": readback_attempts,
            # 留下"对方回过话"的证据，但不落任意 HTML/PII。
            "response_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest() if body else "",
        }


def _read_candidate(client, row_id: str) -> dict:
    return client._parse_form_defaults(
        client._post_text(_BOOKING_FORM_PATH, {"status": "e", "id": row_id})
    )


def _readback_once(client, docno: str, submitted: dict) -> tuple:
    """一轮只读回读。返回 `(booking_id, 阶段, 脱敏字段名, 告警字段名, 搜索页数)`。

    只读：`showdata` + `form.php`。任何一轮都不会 POST `drfcbc/new.php`。
    首页已确认就停，不白翻第二页；首页没结论才按需翻页（上限 `_MAX_SEARCH_PAGES`）。
    """
    # 原生视图会给新会话初始化可见性筛选。它不写任何东西。
    client._post_text("drfcbc/view.php", {"idmenu": "25", "menulv": "2"})
    seen: list = []
    mismatch_stage = None
    mismatch_fields: tuple = ()
    pages = 0
    warnings: tuple = ()
    for page in range(1, _MAX_SEARCH_PAGES + 1):
        body = client._post_text(
            _BOOKING_LIST_PATH,
            {
                "sdtamt": str(_SEARCH_PAGE_SIZE),
                "sdtpage": str(page),
                "sd": docno,
                "ftd": "1",
                "selcolsort": "1",
                "selcolsorttype": "1",
            },
        )
        pages = page
        row_ids = parse_row_ids(body, limit=MAX_READBACK_CANDIDATE_IDS)
        identity_hits = []
        for row_id in row_ids:
            if row_id in seen:
                continue
            seen.append(row_id)
            form = _read_candidate(client, row_id)
            outcome = evaluate_candidate(submitted, form, row_id)
            if outcome.status == STAGE_VERIFIED:
                identity_hits.append(row_id)
                warnings = outcome.warnings
            elif mismatch_stage is None:
                # 只要有一行是我们那张单，别的模糊命中就不算事；它们只在全军覆没时定因。
                mismatch_stage = outcome.status
                mismatch_fields = outcome.diagnostic_fields
        if len(identity_hits) > 1:
            # 多张完全匹配的单据无法区分是哪一张 —— 报歧义，不许随便挑一张。
            return None, STAGE_AMBIGUOUS, (), warnings, pages
        if identity_hits:
            return identity_hits[0], STAGE_VERIFIED, (), warnings, pages
        if page_is_last(body, _SEARCH_PAGE_SIZE):
            break
    return None, mismatch_stage or STAGE_SEARCH_EMPTY, mismatch_fields, warnings, pages


def _log_readback(
    stage: str, source: str, pages: int, attempts: int, fields: tuple, warnings: tuple
):
    """脱敏回读日志：只有阶段、来源、次数与**字段名**，没有单号以外的业务值。"""
    extra = {
        "readback_stage": stage,
        "readback_source": source,
        "readback_pages": pages,
        "readback_attempts": attempts,
        "readback_fields": list(fields),
        "readback_display_warnings": list(warnings),
    }
    if stage == STAGE_SEARCH_EMPTY:
        logger.warning("[dms] 回读没搜到这张单：%s", extra)
    elif stage != STAGE_VERIFIED:
        logger.warning("[dms] 回读没有确定结论：%s", extra)
    elif warnings:
        logger.info("[dms] 回读只有展示字段差异：%s", extra)


def _readback_with_retries(client, docno: str, submitted: dict, source: str) -> tuple:
    """同一会话内有限次重查：短时搜索不可见/索引延迟不该直接判"订单不存在"。"""
    stage = None
    diagnostic: tuple = ()
    warnings: tuple = ()
    pages = 0
    attempts = 0
    for attempt in range(1, READBACK_MAX_ATTEMPTS + 1):
        attempts = attempt
        try:
            booking_id, outcome, fields, found_warnings, pages = _readback_once(
                client, docno, submitted
            )
        except Exception as exc:  # 传输/解析异常：只读失败也要留下定因
            logger.warning(
                "[dms] 回读（%s）第 %s 次失败：%s",
                source,
                attempt,
                type(exc).__name__,
            )
            pages = 0
            booking_id, outcome, fields, found_warnings = None, STAGE_SEARCH_EMPTY, (), ()
        if booking_id:
            _log_readback(STAGE_VERIFIED, source, pages, attempts, (), found_warnings)
            return booking_id, STAGE_VERIFIED, attempts, 0, found_warnings
        stage = merge_stage(stage, outcome)
        if not diagnostic:
            diagnostic = fields
        if not warnings:
            warnings = found_warnings
        if outcome != STAGE_SEARCH_EMPTY or attempt == READBACK_MAX_ATTEMPTS:
            break
        delay = readback_backoff_seconds(attempt)
        if delay:
            time.sleep(delay)
    final = stage or STAGE_SEARCH_EMPTY
    _log_readback(final, source, pages, attempts, diagnostic, warnings)
    return None, final, attempts, pages, warnings


def _admin_reader(client):
    """配了 admin 凭据组时返回一个独立 reader（避免销售端备忘串味），否则 None。"""
    admin = client._resolve_admin_transport()
    if admin is None:
        return None
    from pearnly_dms.dms_client import DmsClient

    return DmsClient(admin, client.base_url)


def verify_created_booking(
    client, docno: str, submitted: dict, *, stage_out: dict | None = None
) -> str | None:
    """先让**销售自己的会话**读一遍（有限次重查），再按配置借同端点管理员读。

    只读：两条会话都只用列表搜索 + 详情表单读取，**绝不重发 `drfcbc/new.php`**。
    """
    stage = None
    diagnostic: tuple = ()
    warnings: tuple = ()
    attempts = 0
    booking_id = None
    try:
        booking_id, stage, attempts, _pages, warnings = _readback_with_retries(
            client, docno, submitted, "sales"
        )
        if booking_id:
            if stage_out is not None:
                stage_out.update({"stage": STAGE_VERIFIED, "attempts": attempts, "vendor": "sales"})
            return booking_id
    except Exception as exc:
        logger.warning("[dms] 销售会话回读失败：%s", type(exc).__name__)
    if stage not in (None, STAGE_SEARCH_EMPTY):
        # 销售会话已经**看得见**这张单，只是身份/关键字段对不上或有多张候选：
        # 换管理员权限不会让这些字段变得一致，不必多起一个会话。
        if stage_out is not None:
            stage_out.update({"stage": stage, "attempts": attempts, "vendor": "sales"})
        return None
    try:
        reader = _admin_reader(client)
        if reader is None:
            if stage_out is not None:
                stage_out.update(
                    {
                        "stage": STAGE_SEARCH_EMPTY,
                        "attempts": attempts,
                        "vendor": "admin_unavailable",
                    }
                )
            return None
        booking_id, admin_stage, admin_attempts, _pages, admin_warnings = _readback_with_retries(
            reader, docno, submitted, "admin"
        )
        stage = merge_stage(stage, admin_stage)
        attempts += admin_attempts
        if not warnings:
            warnings = admin_warnings
        if booking_id:
            stage = STAGE_VERIFIED
    except Exception as exc:
        logger.warning("[dms] 管理员会话回读失败：%s", type(exc).__name__)
        if stage_out is not None:
            stage_out.setdefault("vendor", "admin_unavailable")
    if stage == STAGE_VERIFIED and booking_id:
        if stage_out is not None:
            stage_out.update({"stage": STAGE_VERIFIED, "attempts": attempts, "vendor": "admin"})
        return booking_id
    if stage_out is not None:
        stage_out.update({"stage": stage or STAGE_SEARCH_EMPTY, "attempts": attempts})
    return None


def submit_booking(
    client,
    base: dict,
    state,
    *,
    on_attempt=None,
    write_payload=None,
    readback_payload=None,
) -> tuple[str, str]:
    """**只有明确的重复号拒绝才允许再写一次。**

    `state` 是 DMS 原生取号状态（`autonum_state` 的产物）。写入体由
    `write_payload(base, state)` 构造；不给回调时（旧形态）把完整号放进 `txtdocno`
    仍能跑，真实建单路径一律传原生两栏构造器 + 带完整号的回读视图。

    重复号 bump 走 `state.with_docno`：数字主体与 `natn` 同步前进，绝不脱节；
    非重复错误与 outcome unknown 都**不会**重写（后者抛异常交调用方保留草稿）。
    """
    if isinstance(state, str):
        state = _LegacyDocnoState(state)
    if callable(write_payload) and callable(readback_payload):

        def build_write(current):
            return write_payload(base, current)

        def build_readback(current):
            return readback_payload(base, current)

    else:

        def build_write(current):
            return {**base, "txtdocno": current.docno}

        build_readback = build_write

    last_body = ""
    for _ in range(dms_docno.BOOKING_DOCNO_MAX_TRIES):
        docno = state.docno
        data = build_write(state)
        status = None
        if on_attempt is not None:
            # **跨过写边界之前**先把这次尝试落盘。POST 之后崩溃，
            # 不能让下一个 worker 的投递变成第二张业务单据。
            on_attempt(docno)
        readback_input = build_readback(state)
        try:
            resp = client.transport.post(
                client._url("drfcbc/new.php"), data=data, timeout_ms=120000
            )
            status, last_body = resp.status_code, (resp.text or "").strip()
        except Exception:
            # 超时/断连可能发生在**提交之后**。回读是安全的，再 POST 不是。
            last_body = ""
        if status == 200 and last_body.startswith("err::"):
            if dms_docno.is_duplicate_docno_error(last_body):
                state = state.with_docno(dms_docno.bump_docno(docno))
                continue
            raise DmsClientError(f"booking create rejected: {last_body[:300]!r}", "ERR_DMS_IMPORT")
        readback = {}
        booking_id = verify_created_booking(client, docno, readback_input, stage_out=readback)
        if booking_id:
            return booking_id, docno
        raise DmsBookingOutcomeUnknown(
            docno,
            http_status=status,
            body=last_body,
            stage=str(readback.get("stage") or ""),
            source=str(readback.get("vendor") or ""),
            readback_attempts=int(readback.get("attempts") or 0),
        )
    raise DmsClientError(f"booking create rejected: {last_body[:300]!r}", "ERR_DMS_IMPORT")
