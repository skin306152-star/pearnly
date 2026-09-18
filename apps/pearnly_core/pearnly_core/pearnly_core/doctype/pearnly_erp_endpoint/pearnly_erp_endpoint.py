# -*- coding: utf-8 -*-
"""外部系统连接（对端地址 + 凭据）。

这是**所有外部调用**的地基：DMS 的客户查重/建档/改写、Cowork/ERP 的推送，
都要先有一条连接才知道往哪打、用什么账号打。

照老产品搬（`services/erp/endpoint_config.py` + `routes/erp_endpoints_routes.py`
+ `erp_endpoints` 表）。三处**刻意保持一致**：

1. `adapter` 取值沿用老产品的枚举，不自己发明；
2. 凭据分**两组**：操作员自己的（`username`/`password`）和"有修改权限的"
   （`admin_*`）—— 老产品原话是"销售人员仍用自己的账号匹配提成归属，
   需要改写客户档时由服务端继承老板的这组凭据"；
3. `enabled` 是"同批不误投"的闸：停用后后端按 `enabled=TRUE` 过滤。

与原样照搬的一处**有意差异**：老产品给 `system_url` 做了 SSRF 校验
（`services/erp/ssrf_guard.py`），新平台还没接，见 `validate()` 里的说明。
"""

import frappe
from frappe import _
from frappe.model.document import Document


# ⚠️ 类名必须**精确**等于 DocType 名去掉空格：Frappe 用
# `doctype.replace(" ", "")` 推导类名（`frappe/model/base_document.py`），
# 推导不出来的报错是 `ImportError: Pearnly ERP Endpoint`——只给文档名，
# 不提类名，很难看出来是大小写问题（这里栽过一次：写了 PearnlyERPendpoint，
# 少了一个大写 E，结果 migrate 把它当"孤儿 DocType"直接删掉）。
class PearnlyERPEndpoint(Document):
    def validate(self) -> None:
        # 同一账号 + 同一适配器下只能有一条默认连接（老产品的唯一索引：
        # `idx_erp_endpoints_one_default_per_user`）。少了这条约束会出现
        # "默认连接到底是哪条"说不清的情况。
        if self.is_default and self.enabled:
            clash = frappe.db.get_value(
                self.doctype,
                {
                    "is_default": 1,
                    "enabled": 1,
                    "adapter": self.adapter,
                    "owner_user": self.owner_user or "",
                    "name": ("!=", self.name),
                },
                "name",
            )
            if clash:
                frappe.throw(
                    _("同一账号下「{0}」已经有一条默认连接（{1}），请先取消它的默认").format(
                        self.adapter, clash
                    )
                )

        # 连接要能用，至少要有一个账号；否则"配好了"是假的。
        # 老产品是"打开向导时必须有账号密码"，这里放宽成"允许先建后补"——
        # 因为向导分两步保存（先地址、后凭据），卡死会让人配不下去。
        if self.system_url and not (self.username or self.admin_username):
            frappe.msgprint(
                _("还没填任何账号，这条连接暂时不能用（配好了凭据再来测试连接）"),
                indicator="orange",
                alert=True,
            )
