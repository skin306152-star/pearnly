"""租户的智能体设置：引擎、模型、密钥（ADR-019）。开户单与租户表单共用这一份校验与读取。

可选的引擎与模型、各家密钥格式都来自 `pearnly_core.assistant.engines`（全平台唯一登记处），
这里不写死任何厂商。三项全空表示不开通；填了就必须是一组能用的设置。
"""
import frappe
from frappe import _

from pearnly_core.assistant import engines

FIELDS = ("assistant_engine", "assistant_model", "assistant_api_key")


def _api_key(doc) -> str:
    raw = (doc.assistant_api_key or "").strip()
    # 已保存的密码字段在表单里是一串星号：没改就读库里的那一份。
    if raw and set(raw) == {"*"} and not doc.is_new():
        return doc.get_password("assistant_api_key", raise_exception=False) or ""
    return raw


def validate(doc) -> None:
    """保存前校验；顺带记下这次是否改了设置（密码字段存库的是等长星号，换一把同长度的密钥看不出变化）。"""
    raw = (doc.assistant_api_key or "").strip()
    doc.flags.assistant_changed = bool(raw and set(raw) != {"*"}) or any(
        doc.has_value_changed(f) for f in FIELDS if f != "assistant_api_key")
    doc.assistant_engine = (doc.assistant_engine or "").strip()
    doc.assistant_model = (doc.assistant_model or "").strip()
    key = _api_key(doc)
    if not (doc.assistant_engine or doc.assistant_model or key):
        return
    if not doc.assistant_engine:
        frappe.throw(_("Select the CC engine."))
    if not key:
        frappe.throw(_("Enter the CC API key."))
    try:
        engines.check(doc.assistant_engine, key, doc.assistant_model)
    except engines.EngineError:
        frappe.throw(_("The CC engine, model or API key is not valid."))


def read(doc) -> dict:
    """推到站点的设置；没开通时三项为空。"""
    key = _api_key(doc)
    if not (doc.get("assistant_engine") and key):
        return {"engine": "", "model": "", "api_key": ""}
    return {"engine": doc.assistant_engine, "model": doc.assistant_model or "", "api_key": key}


@frappe.whitelist()
def engine_options() -> list[dict]:
    frappe.only_for("System Manager")
    return engines.options()
