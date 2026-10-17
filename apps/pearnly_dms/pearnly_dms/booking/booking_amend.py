# -*- coding: utf-8 -*-
"""修改一张**已经开出的**订车单的几栏（开单回执差异卡的「更新资料 / 修改」用）。

## 协议（2026-10-16 测试 DMS 实测）

DMS 原生修改页 `drfcbc/form.php?status=e` → `drfcbc/edit.php`：提交的是**整张表单**，
DMS 用它替换整张单（附件也一样，见 `client/attachments.py`）。所以每次都取一张新鲜表单，
只换要改的那几栏，其余（含附件的隐藏编号）原样带回。测试单 BK000002610000005 上改电话再改回，
整张表单与改前逐字段一致、附件保留、客户主档不动。

## 只在 DMS 自己也允许改的时候改

DMS 的规则（读自它的 `view.js` / `form.js`）：

- 列表行的 `data-allo` 含 `e`（这个账号能改这张单）且 `data-cf` 为空（未审核 / 核对 / 审批）才有「แก้ไข」；
- `stcbc=2`（ใบจอง）打开修改页时所有文字栏只读。Pearnly 开出的单是 `stcbc=1`（แบบร่าง）。

任何一条不满足就**不写**，回 `locked`。

## 写之后

返回非 `err::` 不等于改成了：一律重读一张新鲜表单交给调用方核对。提交超时、断连也只读回，
**绝不重发**（调用方在提交前已把这次写入登记成"已发出"）。
"""

from __future__ import annotations

import logging
import re
from typing import Dict

from pearnly_dms.booking.booking_readback import docno_equal
from pearnly_dms.contracts.client_base import DmsClientError

logger = logging.getLogger(__name__)

_FORM = "drfcbc/form.php"
_EDIT = "drfcbc/edit.php"
_LIST = "drfcbc/component/showdata.php"

UPDATED, LOCKED, REJECTED, UNVERIFIED = "updated", "locked", "rejected", "unverified"


def _form(client, booking_id: str, booking_no: str) -> Dict[str, str]:
    """新鲜的修改页表单；不是这张单就抛（绝不拿别的单去写）。"""
    fields = client._parse_form_defaults(client._post_text(_FORM, {"status": "e", "id": str(booking_id)}))
    if fields.get("stsel") != "e" or fields.get("idsel") != str(booking_id) or not docno_equal(booking_no, fields):
        raise DmsClientError("Booking edit form identity mismatch", "ERR_DMS_IMPORT")
    return fields


def _files(fields: Dict[str, str]) -> set:
    return {value for key, value in fields.items() if key.startswith("fulcurrname")}


def editable(client, booking_id: str, booking_no: str) -> bool:
    """这个账号此刻能不能改这张单（DMS 列表行的 `data-allo` / `data-cf`，与原生「แก้ไข」按钮同一判据）。"""
    client._post_text("drfcbc/view.php", {"idmenu": "25", "menulv": "2"})
    body = client._post_text(_LIST, {"sdtamt": "30", "sdtpage": "1", "sd": booking_no, "ftd": "1",
                                     "selcolsort": "1", "selcolsorttype": "1"})
    for match in re.finditer(r"<div\b[^>]*>", body or ""):
        tag = match.group(0)
        if f'data-val="{booking_id}"' not in tag:
            continue
        allo = re.search(r'data-allo="([^"]*)"', tag)
        cf = re.search(r'data-cf="([^"]*)"', tag)
        return bool(allo and "e" in allo.group(1)) and bool(cf) and not cf.group(1).strip()
    return False


def amend(client, *, booking_id: str, booking_no: str, writes: Dict[str, str], on_posted=None) -> Dict[str, object]:
    """把 `writes` 写进这张单。返回 `{"status": updated|locked|rejected|unverified, "form": 改后表单, "message": …}`。

    `on_posted`：**跨过写边界之前**调用（调用方据此登记"已发出"，崩溃后绝不重放）。
    """
    before = _form(client, booking_id, booking_no)
    if before.get("stcbc") != "1" or not editable(client, booking_id, booking_no):
        return {"status": LOCKED}
    if on_posted is not None:
        on_posted()
    body = ""
    try:
        resp = client.transport.post(client._url(_EDIT), data={**before, **writes}, timeout_ms=120000)
        body = (resp.text or "").strip()
    except Exception:
        # 超时可能发生在提交之后：只读回，不重发。
        logger.warning("[dms] 改单提交没有拿到回应，改为只读核对", exc_info=True)
    if body.startswith("err::"):
        return {"status": REJECTED, "message": body.split("::", 1)[1].strip()[:200]}
    try:
        after = _form(client, booking_id, booking_no)
    except Exception:
        logger.warning("[dms] 改单后读回失败", exc_info=True)
        return {"status": UNVERIFIED}
    if not _files(before).issubset(_files(after)):
        logger.warning("[dms] 改单后附件少了")
        return {"status": UNVERIFIED, "form": after}
    return {"status": UPDATED, "form": after}


__all__ = ["LOCKED", "REJECTED", "UNVERIFIED", "UPDATED", "amend", "editable"]
