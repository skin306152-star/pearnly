"""公司域给前端的启动数据：多公司那两项（切换公司要用）。

官方给 App 的扩展点就是 `boot_session`（框架启动时按已装 App 逐个调），所以公司清单
从 ERP 自己这里给 —— 不塞进共用层：DMS / Cowork 站点没装这个 App，共用层也不能依赖它。

每一项**各取各的**：以前整段包在一个 try 里，取公司一抛错（租户还没有公司），后面的
仓库建议值和「LINE」那一块就一起没了，页面上也没有任何提示（mrerp 2026-09-30）。
"""
import frappe


def _put(bootinfo, key, compute, default):
    """取一项；失败只记日志、给默认值，不连累别的项，也绝不往外抛（boot 失败整个 desk 打不开）。"""
    try:
        bootinfo[key] = compute()
    except Exception:
        bootinfo[key] = default
        frappe.log_error(title=f"ERP boot item {key} failed")


def _express_linked():
    """当前公司接了 Express（勾了「接 Express」且 CC 桌面版报过它的账套，用户 10-29）：没接的公司是普通 ERP，页面不出 Express 列与按钮。"""
    from pearnly_erp_portal.express import push
    from pearnly_erp_portal.masters import company as companies

    try:
        company = companies.active_company()
    except Exception:  # 还没有公司的租户（公司清单那一项已记过日志）：就是没接
        return False
    return push.linked(company)


def _can_build() -> bool:
    from pearnly_erp_portal.masters import cc_service

    return cc_service.can_build()


def _new_ledgers() -> int:
    from pearnly_erp_portal.masters import cc_service, ledger_prompt

    return ledger_prompt.unseen() if cc_service.can_build() else 0


def boot_session(bootinfo):
    if not frappe.conf.get("pearnly_product"):
        return
    from pearnly_erp_portal.access import guard
    from pearnly_erp_portal.masters import company as companies

    _put(bootinfo, "pearnly_companies", companies.company_options, [])
    _put(bootinfo, "pearnly_company", companies.active_company, "")
    # 单据的仓库建议不在启动数据里（登录时算一次，切公司不跟）：各表单建单时问 `warehouses.api_prefill`（多仓闭环 P1）。
    # 个人信息页要不要出现「LINE」那一块：与绑定接口同一个能力码（团队管理里逐人勾选）。
    _put(bootinfo, "pearnly_line", lambda: guard.has("integration.read"), False)
    # 当前公司接了 Express 账本：销售 / 采购列表多一列 Express 单号与状态
    _put(bootinfo, "pearnly_express", _express_linked, False)
    # 连接里有还没接进 ERP 的公司（公司接入重做第 2 批）：能建公司的人打开 ERP 时弹「选择要用的公司」，关过的不再弹
    _put(bootinfo, "pearnly_new_ledgers", _new_ledgers, 0)
    _put(bootinfo, "pearnly_can_build", _can_build, False)
