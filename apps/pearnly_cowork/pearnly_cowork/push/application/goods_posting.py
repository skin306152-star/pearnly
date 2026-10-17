"""商品单入账方式按公司选一次（数字会计员第 3 期，用户 2026-10-16；规则见 `push.express.goods_posting`）：复核页推送停在
`posting_needs_review` 时点「走库存 / 记费用」，或 CC 同一个工具（`cowork_choose_document_policy`），记下这家公司之后都这样。

同转账凭条 / 预扣税凭证的「选一次」（`history.document_policy.choose`）：存在工作空间同一个字段里，只是键按公司。公司按这张票
推往的账套（复核页提交的那份，没给按票上记的去向）在连接目录里那一行认；要有录入权限、能读这张票、能用这条连接推送。

主档页「记住的选择」按公司改入账方式、删入账方式或现用账套（键 ``current_book|…``）也经这里写（companies 不导入 history）：
:func:`set_company` / :func:`forget_company` 只认这家公司各身份（`companies.identity.identities`）下按公司记的键，归属与连接授权
由 `companies.choices` 先核。
"""
from __future__ import annotations

import json

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.connections import service as connections
from pearnly_cowork.contracts import document_policy as policy_rules
from pearnly_cowork.history import document_policy, storage
from pearnly_cowork.push.express import goods_posting
from pearnly_cowork.targets.identity import normalize_express_account_key


def _row(loaded, account_set):
    rows = (loaded.evidence.snapshot or {}).get("account_sets") or []
    if isinstance(rows, str):
        rows = json.loads(rows)
    key = normalize_express_account_key(account_set)
    return next((row for row in rows if isinstance(row, dict)
                 and normalize_express_account_key(row.get("source_id")) == key), None) if key else None


def choose(history, value, connection=None, account_set_key=None) -> dict:
    """记下这张票推往的那家公司的商品单入账方式（``stock`` 走库存 / ``service`` 记费用）；回 ``{"goods_posting": 值}``。"""
    scope.require("intake")
    doc = storage.read(history)
    value = str(value or "").strip()
    connection = str(connection or doc.push_connection or "").strip()
    account_set = str(account_set_key or doc.push_account_set or "").strip()
    if value not in policy_rules.GOODS_POSTING_VALUES or not connection or not account_set:
        frappe.throw(_("cowork.policy.invalid"))
    loaded = connections.read_for_push(connection, doc.workspace, doc.client, lock=True)
    if loaded.endpoint.get("adapter") != "express":
        frappe.throw(_("cowork.policy.invalid"))
    key = policy_rules.goods_posting_key(goods_posting.company_key(loaded.doc.name, _row(loaded, account_set)))
    if not key:
        frappe.throw(_("cowork.policy.invalid"))
    document_policy.record(doc.workspace, key, value)
    return {"goods_posting": value}


def _identities(company_identities) -> list:
    if isinstance(company_identities, str):
        company_identities = [company_identities]
    return [str(item) for item in company_identities or () if str(item or "").strip()]


def _owned(key, identities) -> bool:
    return any(key == policy_rules.goods_posting_key(item) or policy_rules.current_book_parts(key, item)
               for item in identities)


def set_company(workspace, company_identities, value) -> dict:
    """主档页把这家公司的商品单入账方式直接改成 ``stock`` / ``service``：记在主档身份下，这家别的身份（按名称建档时的
    ``name:express:…``，旧年度没报税号的账套推送时按它认）已经记过的一起改；回 ``{"goods_posting": 值}``。"""
    scope.require("intake")
    value = str(value or "").strip()
    identities = _identities(company_identities)
    key = policy_rules.goods_posting_key(identities[0]) if identities else ""
    if not workspace or not key or value not in policy_rules.GOODS_POSTING_VALUES:
        frappe.throw(_("cowork.policy.invalid"))
    stored = document_policy.read(workspace)
    for item in identities:
        found = policy_rules.goods_posting_key(item)
        if found == key or found in stored:
            document_policy.record(workspace, found, value)
    return {"goods_posting": value}


def forget_company(workspace, company_identities, keys) -> int:
    """删这家公司按公司记的几项（入账方式、现用账套）：每个键都须是这家某个身份下的；别家的、按租户记的（转账凭条 /
    预扣税凭证）不认。回删了几项。"""
    scope.require("intake")
    identities = _identities(company_identities)
    keys = [str(key or "") for key in ([keys] if isinstance(keys, str) else keys or ())]
    if not workspace or not keys or not all(_owned(key, identities) for key in keys):
        frappe.throw(_("cowork.policy.invalid"))
    return sum(int(document_policy.forget(workspace, key) or 0) for key in dict.fromkeys(keys))


__all__ = ["choose", "forget_company", "set_company"]
