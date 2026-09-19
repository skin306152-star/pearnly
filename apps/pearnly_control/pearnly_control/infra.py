# -*- coding: utf-8 -*-
"""本机样板环境里"产品 → 运行组/端口"的映射。

为什么单独放一处：控制面站点**只装了 frappe + pearnly_core + pearnly_control**，
它自己跑在 control 组里，看不到产品组的容器。开通要落到正确的组（站点建在对应
产品的 Bench 里，多个站点共享同一套 Bench 进程），所以这份映射是开通的输入之一。

全部可在站点配置里覆盖，换环境不用改代码：
  pearnly_group_ports  = {"cowork": 8101, "erp": 8102, "dms": 8103}
  pearnly_docker_ctx   = "colima-pearnly"
  pearnly_compose_file = "infrastructure/compose/01-bench.yml"
"""

import frappe
from frappe import _

# 产品 → 运行组。目前一对一；分开写是因为"组"是部署概念，"产品"是业务概念。
PRODUCT_GROUP = {"cowork": "cowork", "erp": "erp", "dms": "dms"}

PRODUCT_APP = {
    "cowork": "pearnly_cowork",
    "erp": "pearnly_erp_portal",
    "dms": "pearnly_dms",
}

# 所有产品站点都要装共用层
CORE_APP = "pearnly_core"

DEFAULT_PORTS = {"cowork": 8101, "erp": 8102, "dms": 8103, "control": 8104}


def group_ports() -> dict:
    raw = frappe.conf.get("pearnly_group_ports") or {}
    ports = dict(DEFAULT_PORTS)
    ports.update({k: int(v) for k, v in dict(raw).items()})
    return ports


def docker_context() -> str:
    return str(frappe.conf.get("pearnly_docker_ctx") or "colima-pearnly")


def compose_file() -> str:
    return str(
        frappe.conf.get("pearnly_compose_file") or "infrastructure/compose/01-bench.yml"
    )


def group_of(product: str) -> str:
    group = PRODUCT_GROUP.get(product)
    if not group:
        frappe.throw(_("Unknown product: {0}").format(product))
    return group


def app_of(product: str) -> str:
    return PRODUCT_APP.get(product, "")


def port_of(product: str) -> int:
    return int(group_ports().get(group_of(product), 0))


def local_url(site_name: str, product: str) -> str:
    return f"http://{site_name}:{port_of(product)}"
