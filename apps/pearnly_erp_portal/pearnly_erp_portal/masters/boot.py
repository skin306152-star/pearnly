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
    """这个站点有 CC 桌面版报过账套（ADR-025：接没接 Express 由数据定，不是开关）。"""
    return bool(frappe.db.exists("Pearnly CC Book", {}))


def boot_session(bootinfo):
    if not frappe.conf.get("pearnly_product"):
        return
    from pearnly_erp_portal.access import guard
    from pearnly_erp_portal.masters import company as companies

    _put(bootinfo, "pearnly_companies", companies.company_options, [])
    _put(bootinfo, "pearnly_company", companies.active_company, "")
    # 「这家公司卖货的那个仓」：进货/出入库那些表单的仓库建议值用它（原来取的是商品上
    # 那个遗留的"默认仓"，会让进货落在一个仓、收银从另一个仓卖）。没有收银台时是空串。
    _put(bootinfo, "pearnly_warehouse", lambda: companies.selling_warehouse() or "", "")
    # 个人信息页要不要出现「LINE」那一块：与绑定接口同一个能力码（团队管理里逐人勾选）。
    _put(bootinfo, "pearnly_line", lambda: guard.has("integration.read"), False)
    # 这个站点接了 Express 账本（CC 桌面版报过账套）：销售 / 采购列表多一列 Express 单号与状态
    _put(bootinfo, "pearnly_express", _express_linked, False)
