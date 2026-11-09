"""同一张票在账本里的状态与写者（多产品共用：Cowork、ERP × 直写、代录 × Express、MR.ERP；复核 5 批之第 1 批，设计 1.2、1.3）。

同一张票（`express_rules.contracts.ticket_identity` 的身份）只该写进账本一次：
- 交单、推送前先判一次（`entered`）：同一公司、同一账本目标里，另一条记录写的同一张票已经写成，或正在写（排着、在录）。
  这样的票默认跳过，回那一条和它在账本里的单号。人点了「再写一次」的照写（`again`），只对点了的那几张、只这一次。
- 同一张票同一时刻只一个写者（`one_writer`，按身份键的短锁）：两条路同时交同一张，后到的拿到锁后再判，就读到前一条的结果。
- 只看我们自己的推送记录：会计在 Express / MR.ERP 里手录的同号照写（「推送不查重不拦」，用户 10-08）。

产品经 hooks ``pearnly_ledger_tickets`` 声明 ``{"twin": "<点路径>"}``：``twin(doc_id)`` 回 ``{"doc_id", "docnum", "state", "key"}``
（``state`` 是 ``ENTERED`` 或 ``QUEUED``）或 ``{"key": ...}``（认出了身份、没有写过的）或 ``{}``（认不出身份）。共用层不认识产品的表。
"""
from contextlib import contextmanager

import frappe

HOOK = "pearnly_ledger_tickets"
ENTERED = "entered"  # 另一条写的同一张票已写进账本（带单号）
QUEUED = "queued"  # 另一条写的同一张票正在写（排着、在录）
LOCK_SECONDS = 30


def _twin_path() -> str:
    found = frappe.get_hooks(HOOK) or {}
    values = found.get("twin") if isinstance(found, dict) else None
    values = values if isinstance(values, list) else [values]
    values = [value for value in values if isinstance(value, str) and value]
    return values[-1] if values else ""


def lookup(doc_id) -> dict:
    """这张单的身份与同一张票的写入情况（产品声明的 ``twin``）；没声明回 {}。"""
    path = _twin_path()
    return dict(frappe.get_attr(path)(doc_id) or {}) if path else {}


def entered(doc_id, again=()) -> dict:
    """交单 / 推送前判一次：同一张票已由另一条写成或正在写 → ``{"doc_id", "docnum", "state"}``；点了「再写一次」的、没写过的、
    认不出身份的回 {}（照写）。"""
    if str(doc_id) in {str(item) for item in again or ()}:
        return {}
    found = lookup(doc_id)
    return {key: found.get(key) or "" for key in ("doc_id", "docnum", "state")} if found.get("state") in (ENTERED, QUEUED) else {}


@contextmanager
def one_writer(doc_id):
    """同一张票同一时刻只一个写者：按身份键加短锁，锁里再判、再写。认不出身份的不加锁（没法认出是同一张）。"""
    key = str(lookup(doc_id).get("key") or "")
    if not key:
        yield
        return
    with frappe.cache.lock(f"{frappe.local.site}:pearnly_ledger_ticket:{key}", timeout=LOCK_SECONDS,
                           blocking_timeout=LOCK_SECONDS):
        yield
