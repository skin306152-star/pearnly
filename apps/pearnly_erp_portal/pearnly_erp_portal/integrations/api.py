"""集成域接口层。业务与权限在 services，这里只做参数解析。"""

import frappe

from pearnly_erp_portal.integrations import binding, intake


def _clean(kwargs):
    kwargs.pop("cmd", None)
    return kwargs


def _json_args(kwargs, *names):
    for name in names:
        if isinstance(kwargs.get(name), str):
            kwargs[name] = frappe.parse_json(kwargs[name])
    return kwargs


@frappe.whitelist()
def api_line_bot(**kwargs):
    """本站点 LINE 官方号对外身份（名字、Basic ID、加好友链接）。"""
    return binding.account()


@frappe.whitelist()
def api_line_binding(**kwargs):
    return binding.status()


@frappe.whitelist(methods=["POST"])
def api_line_binding_code(**kwargs):
    """发一个新的绑定码（旧的未用码同时作废）。"""
    return binding.start()


@frappe.whitelist(methods=["POST"])
def api_line_unbind(**kwargs):
    return binding.unbind()


# ── LINE 收进来的单据草稿 ───────────────────────────────────────────────────


@frappe.whitelist()
def api_list_line_intakes(**kwargs):
    return intake.list_intakes(**_clean(kwargs))


@frappe.whitelist()
def api_get_line_intake(**kwargs):
    kwargs = _clean(kwargs)
    return intake.get_intake(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_confirm_line_intake(**kwargs):
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    return intake.confirm(kwargs.pop("name", None), **kwargs)


@frappe.whitelist(methods=["POST"])
def api_discard_line_intake(**kwargs):
    kwargs = _clean(kwargs)
    return intake.discard(kwargs.pop("name", None), kwargs.get("reason"))
