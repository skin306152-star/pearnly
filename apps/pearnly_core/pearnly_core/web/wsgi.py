"""网页进程入口（ADR-028）：先装好 `web.multipart`，再交出 Frappe 自己的 `application`。

- 生产 gunicorn：`pearnly_core.web.wsgi:application`（compose 的 `PEARNLY_WSGI_APP`，回退时设成 `frappe.app:application`）；
- 本机开发服务器（回归实验室，代替 `bench serve --noreload`）：在 sites 目录里 `python -m pearnly_core.web.wsgi <端口>`。
"""
import sys

import frappe.app

from pearnly_core.web import multipart

INSTALLED = multipart.install()
application = frappe.app.application


def application_with_statics():
    return frappe.app.application_with_statics()


def serve(port):
    """同 `bench serve --port <port> --noreload`（frappe/commands/utils.py 的 serve 不带站点时）。"""
    frappe.app.serve(port=port, no_reload=True, sites_path=".")


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
