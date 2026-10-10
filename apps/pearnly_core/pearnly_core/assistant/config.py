"""智能体的站点配置（ADR-019）。三项都**只由租户管理后台写入**（开户时填，之后在后台改 / 换），
经开通步骤推到本站 `site_config.json`；用户从不接触，站点只读不写：

- `pearnly_assistant_engine`：用哪家引擎（取值见 `engines.ENGINES`）。
- `pearnly_assistant_api_key`：该引擎的密钥。
- `pearnly_assistant_model`：模型；留空用该引擎的默认模型。

远端的智能体与运行环境不配置，站点按这组设置自动备好（见 `engine`），换引擎、换密钥、换账号都不需要手工步骤。
只有业务站点（开通时写过 `pearnly_product`）能用；本机管理后台没有产品，直接拒绝。
"""
from collections import namedtuple

import frappe
from frappe import _

from pearnly_core.assistant import engines

Settings = namedtuple("Settings", "engine api_key model")


def product() -> str:
    code = str(frappe.conf.get("pearnly_product") or "").strip()
    if not code:
        frappe.throw(_("The assistant is only available on product sites."), frappe.PermissionError)
    return code


def engine_code() -> str:
    """当前引擎；没配好返回空串（会话列表据此只列本引擎的会话）。"""
    try:
        return settings().engine.code
    except frappe.ValidationError:
        return ""


def settings() -> Settings:
    try:
        code, key, model = engines.check(frappe.conf.get("pearnly_assistant_engine"),
                                         frappe.conf.get("pearnly_assistant_api_key"),
                                         frappe.conf.get("pearnly_assistant_model"))
    except engines.EngineError:
        frappe.throw(_("The assistant is not set up for this site yet."))
    engine = engines.get(code)
    return Settings(engine, key, model or engines.default_model(code))
