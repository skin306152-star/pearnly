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


# 站点对外的协议与端口。**只有这两项在换环境时会变**，所以单独拎出来：
#   本机样板：http + 每组一个端口（cowork 8101 / erp 8102 / dms 8103）
#   生产    ：https + 443（不带端口）
# 站点地址被四处用到（webhook 地址、OIDC 回调、Keycloak 客户端、部署上的本机地址），
# 以前各自拼 `http://{站点}:{端口}` —— 上生产前得挨个改。现在统一走这里。
#
#   pearnly_public_scheme = "https"
#   pearnly_public_port   = 0        # 0 或留空 = 不带端口，走 443
DEFAULT_SCHEME = "http"


def public_scheme() -> str:
    return str(frappe.conf.get("pearnly_public_scheme") or DEFAULT_SCHEME).strip().lower()


def _port_part(explicit: int | None) -> str:
    """端口后缀。生产在 `pearnly_public_port` 里显式定（0 = 不带）；
    没配时用这个部署自己的端口（本机样板就是这种）。"""
    if "pearnly_public_port" in frappe.conf:
        value = int(frappe.conf.get("pearnly_public_port") or 0)
    else:
        value = int(explicit or 0)
    return f":{value}" if value else ""


def site_base_url(site_name: str, port: int | None = None) -> str:
    """站点对外地址（不带结尾斜杠）。webhook、OIDC 回调都用它算，
    保证四处永远一致。"""
    name = (site_name or "").strip().lower()
    return f"{public_scheme()}://{name}{_port_part(port)}"


def deployment_base_url(dep) -> str:
    """某个部署（Pearnly Deployment）的对外地址。"""
    if getattr(dep, "url", None):
        return dep.url.rstrip("/")
    return site_base_url(getattr(dep, "site_name", ""), getattr(dep, "port", None))


def target_settings(target=None):
    if not target:
        target = frappe.db.get_value("Pearnly Target", {"is_default": 1}, "name")
    return dict((frappe.conf.get("pearnly_target_runtime") or {}).get(target) or {})


def is_cloud(target):
    return target_settings(target).get("executor") == "production"


def target_site_url(target, site, product):
    config = target_settings(target)
    if config.get("executor") == "production":
        return f"https://{site}"
    return site_base_url(site, port_of(product))
