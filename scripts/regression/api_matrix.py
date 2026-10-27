"""接口权限矩阵（回归基线 B）：在回归实验室站点里，把 pearnly_core + 本产品 App 的全部白名单接口，按每个岗位账号（再加 Guest）
各走一遍框架的正式入口 `frappe.handler.execute_cmd`（与网页调用同一条：白名单、允许匿名、方法权限都照样核），参数给空，
记下每一格的结果：

- denied：权限层拒了（PermissionError / 未登录）；
- passed：过了权限层（正常返回，或因参数为空在业务校验处报错——说明守卫没拦在它前面）。

只在任务自有的回归实验室跑（PEARNLY_REGRESSION_LAB=1），并且这一跑不留任何痕迹：
- 接口里自己的 `frappe.db.commit()` 换成空操作，每次调用后整体回滚；
- 后台任务（`frappe.enqueue`）、子进程一律不发；
- 对外网络一律断开（socket 只许连库与 Redis），不会调到付费识别、LINE、计费或任何外部服务。
线上租户绝不跑这个。

    ../env/bin/python /workspace/scripts/regression/api_matrix.py <站点> <产品> '<{"岗位": "账号"}>' > 矩阵.jsonl
"""
import importlib
import json
import os
import pkgutil
import socket
import sys
import time

if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
    raise SystemExit("Requires the task-owned regression laboratory")

ALLOWED_HOSTS = {"db", "redis", "localhost", "127.0.0.1"}
_connect = socket.socket.connect


def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    try:
        name = socket.gethostbyaddr(host)[0] if host and host[0].isdigit() and host not in ALLOWED_HOSTS else host
    except OSError:
        name = host
    if isinstance(host, str) and (host in ALLOWED_HOSTS or str(name).split(".")[0] in ALLOWED_HOSTS
                                  or host.startswith("172.") or host.startswith("192.168.") or host.startswith("10.")):
        return _connect(self, address)
    raise ConnectionRefusedError(f"regression lab: outbound connection to {host} blocked")


socket.socket.connect = _guarded_connect

import frappe  # noqa: E402
from frappe import handler  # noqa: E402

APPS = {"erp": "pearnly_erp_portal", "cowork": "pearnly_cowork", "dms": "pearnly_dms"}
DENIED = ("PermissionError", "AuthenticationError", "SessionExpired", "CSRFTokenError")


def endpoints(app_names):
    for app in app_names:
        package = importlib.import_module(app)
        for info in pkgutil.walk_packages(package.__path__, app + "."):
            leaf = info.name.rsplit(".", 1)[-1]
            if leaf.startswith("test") or ".tests" in info.name or ".patches." in info.name:
                continue
            try:
                importlib.import_module(info.name)
            except Exception:  # noqa: BLE001 - 导不进来的模块（缺可选依赖）跳过，结果里不会有它的接口
                continue
    out = {}
    for fn in frappe.whitelisted:
        module = getattr(fn, "__module__", "") or ""
        if module.split(".")[0] in app_names:
            out[f"{module}.{fn.__name__}"] = fn in frappe.guest_methods
    return dict(sorted(out.items()))


PLACEHOLDER = "__regression__"


def placeholders(cmd):
    import inspect

    fn = frappe.get_attr(cmd)
    fn = getattr(fn, "__wrapped__", fn)
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return {}
    return {p.name: PLACEHOLDER for p in params
            if p.default is inspect.Parameter.empty and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}


def call(cmd, user, site):
    from werkzeug.test import EnvironBuilder
    from werkzeug.wrappers import Request

    frappe.set_user(user)
    # 与网页调用同形：一次 POST /api/method/<cmd>（空参数），框架读得到 request、来源 IP 与 Host
    frappe.local.request = Request(EnvironBuilder(path=f"/api/method/{cmd}", method="POST",
                                                  headers={"Host": site}).get_environ())
    frappe.local.request_ip = "127.0.0.1"
    # 必填参数给占位值：不给的话 Python 在进函数体之前就报缺参数，守卫根本没机会跑，测不出它拦不拦
    frappe.local.form_dict = frappe._dict(cmd=cmd, **placeholders(cmd))
    frappe.local.response = frappe._dict()
    started = time.monotonic()
    try:
        handler.execute_cmd(cmd)
        outcome, exc = "passed", ""
    except Exception as error:  # noqa: BLE001 - 每一格都要记结果，不能让一个接口打断整张表
        name = type(error).__name__
        outcome = "denied" if name in DENIED else "passed"
        exc = f"{name}: {str(error)[:120]}"
    finally:
        frappe.db.rollback()
        frappe.local.message_log = []
    return outcome, exc, round((time.monotonic() - started) * 1000)


def _sealed():
    """这一跑不落库、不发任务、不起子进程。"""
    import subprocess

    frappe.db.commit = lambda *args, **kwargs: None
    frappe.enqueue = lambda *args, **kwargs: None
    frappe.enqueue_doc = lambda *args, **kwargs: None

    def refuse(*args, **kwargs):
        raise RuntimeError("regression lab: subprocess blocked")

    subprocess.run = subprocess.Popen = subprocess.call = subprocess.check_output = subprocess.check_call = refuse
    frappe.flags.mute_emails = True


def main():
    site, product, users = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        _sealed()
        apps = ["pearnly_core", APPS[product]]
        found = endpoints(apps)
        users = {**users, "guest": "Guest"}
        for cmd, guest in found.items():
            for role, user in users.items():
                outcome, exc, ms = call(cmd, user, site)
                print(json.dumps({"product": product, "method": cmd, "allow_guest": guest, "role": role, "user": user,
                                  "outcome": outcome, "exc": exc, "ms": ms}, ensure_ascii=False), flush=True)
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()
