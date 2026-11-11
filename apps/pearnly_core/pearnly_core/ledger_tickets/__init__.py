"""同一张票在账本里的状态与写者（多产品共用：Cowork、ERP × 直写、代录 × Express、MR.ERP；复核 5 批之第 1 批，设计 1.2、1.3）。

同一张票（`express_rules.contracts.ticket_identity` 的身份）只该写进账本一次：
- 交单、推送前先判一次（`entered`）：同一公司、同一账本目标里，另一条记录写的同一张票已经写成，或正在写（排着、在录）。
  这样的票交单时跳过（最后一道防线），回那一条和它在账本里的单号；``again`` 里的照写。
- 录过的票先核账本再问（用户 11-10，`check_entered`）：建写入计划时对写过的票问账本还在不在——删了 / 作废了的当没录过、记成
  再录照写；还在的、核不了的由 CC 在复述里问跳过还是再录，用户说再录才进 ``again``（`assistant.write_plan`）。
- 同一张票同一时刻只一个写者（`one_writer`，按身份键的短锁）：两条路同时交同一张，后到的拿到锁后再判，就读到前一条的结果。
- 只看我们自己的推送记录：会计在 Express / MR.ERP 里手录的同号照写（「推送不查重不拦」，用户 10-08）。

产品经 hooks ``pearnly_ledger_tickets`` 声明 ``{"twin": "<点路径>"}``：``twin(doc_id)`` 回 ``{"doc_id", "docnum", "state", "key"}``
（``state`` 是 ``ENTERED`` 或 ``QUEUED``）或 ``{"key": ...}``（认出了身份、没有写过的）或 ``{}``（认不出身份）。共用层不认识产品的表。

核账本（录过的票先核账本，用户 11-10「万一 exp 那边删了呢」）见 `ledger_tickets.check`：
录过的票（多产品共用）：我们写过的单此刻在账本里还在不在（录过的票先核账本，用户 11-10「万一 exp 那边删了呢」）。

入口 :func:`check_present`（接口定稿见共享笔记「A线-核账本接口定稿-给D线-11-10」，签名改动先通知 D 线）。共用层只分派、补齐：
怎么去核由产品在自己的 ``hooks.py`` 声明（ADR-009），共用层不认识产品的通道与 MR.ERP 的读法：

    pearnly_ledger_check = {
        "express": "<点路径>",  # Cowork / ERP 各自经自己的 CC 桌面版请求通道（express_doc_check，CC 1.12.20 起）
        "mrerp": "<点路径>",    # D 线：MR.ERP 读单（查到 present、查不到也回 unknown，gone 等探路证实后再放开）
    }

函数签名同 :func:`check_present`，回 ``{doc_id: {"state", "docnum"?}}``（可以缺项）。共用层补齐：每张都有一项、``state`` 只认
present / gone / unknown（别的都算 unknown）、``docnum`` 缺了用调用方给的、``checked_at`` 是这次核的 UTC 时刻；产品的函数抛错整批
unknown（记 Error Log，不报给用户）。只读：不写账、不改推送记录，结果不存（下一次再核）。
"""
from contextlib import contextmanager

import frappe

from pearnly_core.ledger_tickets.check import (EXPRESS, GONE, ITEMS_MAX, LEDGERS, MRERP, PRESENT, STATES, UNKNOWN,  # noqa: F401
                                               WAIT_MAX, check_present, parse_result, request_params)

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


QUEUED_STATE = "queued"  # check_entered 回的：另一条正在写（排着、在录），不核账本


def check_entered(doc_ids, again=()) -> dict:
    """录过的票先核账本再问（用户 11-10「万一 exp 那边删了呢」，主会话批复）：这几张里我们写过的（:func:`entered`）此刻在账本里
    还在不在。``{doc_id: {"state": present | gone | unknown | queued, "docnum", "twin"}}``，只含写过 / 正在写的；``again``（用户
    说了再录一次的）不核、不在结果里。

    - gone（账本里删了 / 作废了）：当没录过，照写、不问；
    - present、unknown（核不了：电脑不在线、太旧、超时）：写前复述里照实说、问跳过还是再录；
    - queued（另一条正在写）：这次不写，不核。
    按 twin 回的账本、连接、账套分组，每组调一次 :func:`check_present`；twin 没给账本信息的按 unknown。"""
    skip = {str(item) for item in again or ()}
    found, groups = {}, {}
    for doc_id in dict.fromkeys(str(item) for item in doc_ids or () if item):
        if doc_id in skip:
            continue
        twin = lookup(doc_id)
        state = twin.get("state")
        if state == QUEUED:
            found[doc_id] = {"state": QUEUED_STATE, "docnum": "", "twin": str(twin.get("doc_id") or "")}
            continue
        if state != ENTERED:
            continue
        found[doc_id] = {"state": UNKNOWN, "docnum": str(twin.get("docnum") or ""), "twin": str(twin.get("doc_id") or "")}
        if twin.get("docnum") and twin.get("connection") and twin.get("direction") in ("purchase", "sales"):
            group = (str(twin.get("ledger") or EXPRESS), str(twin["connection"]), str(twin.get("account_set") or ""))
            groups.setdefault(group, []).append({"doc_id": doc_id, "docnum": str(twin["docnum"]), "direction": twin["direction"]})
    for (ledger, connection, account_set), items in groups.items():
        for start in range(0, len(items), ITEMS_MAX):
            for doc_id, row in check_present(connection, account_set, items[start:start + ITEMS_MAX], ledger=ledger).items():
                found[doc_id]["state"] = row["state"]
    return found
