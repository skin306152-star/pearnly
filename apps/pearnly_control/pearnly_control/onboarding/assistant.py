"""CC（智能体）设置：全平台一份（ADR-019；用户 2026-10-18：「所有租户默认，不在开户和自由切换……未来开户和现在租户全部
默认走这个值」）。

引擎、模型、密钥存在本机控制面的站点配置里：`pearnly_assistant_engine` / `pearnly_assistant_model` /
`pearnly_assistant_api_key`，与全平台一把的引擎回调签名密钥 `pearnly_assistant_webhook_secret` 放在一起，不进仓库。
开户单与租户表单不再有这三项：开户计划的 `set_assistant` 一律带这一份；改了就跑 :func:`apply_to_all` 推到每个租户。
可选的引擎与模型、各家密钥格式来自 `pearnly_core.assistant.engines`（全平台唯一登记处），这里不写死任何厂商。
三项都没配表示全平台不开通。
"""
import frappe
from frappe import _

from pearnly_core.assistant import engines

CONF = ("pearnly_assistant_engine", "pearnly_assistant_model", "pearnly_assistant_api_key")
EMPTY = {"engine": "", "model": "", "api_key": ""}


def read() -> dict:
    """推到站点的设置；全平台没配时三项为空。配了却不是一组能用的设置：报错，不下发半套。"""
    engine, model, key = (str((frappe.conf or {}).get(name) or "").strip() for name in CONF)
    if not (engine or model or key):
        return dict(EMPTY)
    try:
        engine, key, model = engines.check(engine, key, model)
    except engines.EngineError:
        frappe.throw(_("The CC engine, model or API key is not valid."))
    return {"engine": engine, "model": model, "api_key": key}


def apply_to_all() -> int:
    """全平台设置定了 / 改了：把每个租户的每个云端部署重排 ``set_assistant``，返回排了几个部署。

    会写生产站点配置（开户代理执行），跑之前先和开发线约时间：``bench --site <控制面> execute
    pearnly_control.onboarding.assistant.apply_to_all``。"""
    from pearnly_control import provisioning

    read()  # 先校验：配错了一个都不排
    count = 0
    for tenant in frappe.get_all("Pearnly Tenant", filters={"status": ["!=", "deleting"]}, pluck="name"):
        count += provisioning.queue_assistant_config(tenant)
    frappe.db.commit()
    return count
