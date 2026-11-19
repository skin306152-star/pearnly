"""LINE 写前判定出题器（:mod:`line_ask`）在站点上用的那一份：取卡、记答、搜索、权限、翻译都走写账前置判定现有的接口与产品钩子，
不另写判定（多产品共用）。

- ``card``：``api.card``（同 CC 引导卡的取数）；
- ``record``：``api.record(guided=True)``（同 CC 引导卡：没点过的预选不生效，按公司记住）；产品记答时 ``frappe.throw`` 的（值不合法）
  退回到记答之前，回 ``{"refused": 原话}``，出题器回原因并重问这一题；
- ``search``：``products.search``（服务端分页，搜索类型同 ``api.SEARCH_KINDS``）；
- ``allowed``：``products.allowed``（这家公司能不能写账，同三个白名单接口的第一道）。
"""
import secrets

import frappe
from frappe import _

from pearnly_core.write_precheck import api, products

SAVEPOINT = "pearnly_precheck_line_answer"


class FrappeBackend:
    def card(self, company, doc_ids) -> dict:
        return api.card(company, doc_ids)

    def record(self, company, doc_ids, answers) -> dict:
        frappe.db.savepoint(SAVEPOINT)
        try:
            return api.record(company, doc_ids, answers, guided=True)
        except frappe.ValidationError as error:
            frappe.db.rollback(save_point=SAVEPOINT)
            frappe.clear_messages()
            return {"refused": str(error) or _("This option is no longer current")}

    def search(self, company, doc_id, kind, txt, start, page_length) -> dict:
        if kind not in api.SEARCH_KINDS:
            return {"options": [], "more": False}
        return products.search(company, doc_id, kind, txt, start, page_length)

    def allowed(self, user, company) -> bool:
        return products.allowed(user, company)

    def user(self) -> str:
        return frappe.session.user

    def text(self, key) -> str:
        return _(key)

    def token(self) -> str:
        return secrets.token_hex(4)


BACKEND = FrappeBackend()

__all__ = ["BACKEND", "FrappeBackend"]
