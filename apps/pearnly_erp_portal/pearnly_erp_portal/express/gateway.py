"""交单到事务所 Cowork 网关（`pearnly_cowork.erp_gateway`）的内网调用。

站点配置 ``pearnly_express_gateway``：``{"api": "http://cowork-web:8000", "host": "<事务所 Cowork 站点>",
"client": "<本 ERP 站点>", "token": "<网关令牌>"}``，由运维脚本写入（`scripts/erp-express-link.py`）。没配就是这个 ERP
不接 Express：单据照旧只在 ERP 里，不推、不同步。
"""
import frappe
import requests

CONFIG = "pearnly_express_gateway"
TIMEOUT = 30
MASTERS_TIMEOUT = 120  # 整套主档几 MB，内网一次取回


class Unavailable(Exception):
    """连不上网关 / 网关没答复（不是单据被拒）：调用方过一会儿再试，不改单据状态。"""


def _conf():
    found = frappe.conf.get(CONFIG)
    if not isinstance(found, dict):
        return None
    conf = {key: str(found.get(key) or "").strip() for key in ("api", "host", "client", "token")}
    return conf if conf["api"] and conf["client"] and conf["token"] else None


def configured() -> bool:
    return _conf() is not None


def call(method: str, body: dict, timeout=TIMEOUT) -> dict:
    """POST 到网关的一个入口，回网关的结果（``{"ok": 1, ...}`` 或 ``{"ok": 0, "code"}``）。"""
    conf = _conf()
    if conf is None:
        raise Unavailable("not configured")
    headers = {"X-Pearnly-Gateway-Client": conf["client"], "X-Pearnly-Gateway-Token": conf["token"],
               "Accept": "application/json"}
    if conf["host"]:
        headers["Host"] = conf["host"]
    url = f"{conf['api'].rstrip('/')}/api/method/pearnly_cowork.erp_gateway.api.{method}"
    try:
        response = requests.post(url, json=body, headers=headers, timeout=timeout)
    except requests.RequestException as error:
        raise Unavailable(type(error).__name__) from None
    if response.status_code != 200:
        raise Unavailable(f"HTTP {response.status_code}")
    try:
        message = response.json().get("message")
    except ValueError:
        raise Unavailable("invalid response") from None
    if not isinstance(message, dict) or "ok" not in message:
        raise Unavailable("invalid response")
    return message
