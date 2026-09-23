"""站点货币：把「钱用哪个币种、符号长什么样」放进启动数据。

为什么这件事要在服务端做：前端要显示 `฿ 62.00`，符号只有两条路能拿到 ——

  ① `frappe.model.get_value(":Currency", <币种>, "symbol")`，读的是**客户端缓存**，
     缓存里没有就会去服务端读 `Currency` 这张表；而 `Currency` 的权限在本站是收紧的，
     那次读会**弹一个「无权限操作货币」的 403 弹窗**（本机实测，收银台一进页面就弹）；
  ② `frappe.boot` —— 服务端读一次，前端零权限、零请求。

所以符号从服务端给：`boot.pearnly_currency = {code, symbol}`。前端（`desk/product.js`）
只消费这一个来源，不再自己去问 `Currency` 表。站点货币本身由补丁
`pearnly_core/patches/v0_0_4_site_default_currency.py` 落到 `DefaultValue`（框架
`get_currency_symbol()` 不传币种时读的就是它）；这里再兜一层，保证老站点也不会花。
"""

import frappe

FALLBACK_CURRENCY = "THB"


def boot_session(boot):
    # boot 失败会让整个 Desk 打不开，所以这里绝不往外抛 —— 与公司域那份 boot 扩展同一口径。
    try:
        currency = _currency_in_use(boot)
        boot.pearnly_currency = {"code": currency, "symbol": _symbol(currency)}
        sysdefaults = boot.get("sysdefaults")
        if sysdefaults is not None and not sysdefaults.get("currency"):
            sysdefaults["currency"] = currency
    except Exception:
        frappe.log_error(title="pearnly_core.desk.currency.boot_session 失败")


def _currency_in_use(boot) -> str:
    """站点实际在用的币种。启动数据里已经有就用它，没有再往后找。"""
    currency = (boot.get("sysdefaults") or {}).get("currency")
    if currency:
        return currency
    # `Currency` 表服务端读一次；读不到（站点没配）就用我们产品的币种。
    return frappe.db.get_single_value("System Settings", "currency") or FALLBACK_CURRENCY


def _symbol(currency) -> str:
    """币种符号，读站点配置（客户改过就用客户的）；读不到就留空，前端退回自己的表。"""
    return frappe.db.get_value("Currency", currency, "symbol") or ""
