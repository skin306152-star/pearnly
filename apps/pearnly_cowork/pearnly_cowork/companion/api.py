"""Human management RPCs only; exp_ tokens do not authenticate any RPC."""
import frappe

# 授权判据走连接域那一份（companion 的域依赖只有 connections/push/targets；
# `pearnly_cowork.access` 不在它的允许列表里，不能在这里直接 import）。
from pearnly_cowork.connections import access

from . import contracts, credentials

INSTALLER_URL = 'https://pearnly.com/downloads/companion/PearnlyCompanion-Setup.exe'


def _local_transaction(function, *args, **kwargs):
    # after_request applies no-store/no-cache/nosniff. Set on errors and replays too, so
    # the one-time plaintext response cannot become a reusable HTTP artifact.
    frappe.local.cowork_private_response = True
    for attempt in range(3):
        try:
            return function(*args, **kwargs)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist(methods=['POST'])
def issue(connection, request_key, reset=False, expected_generation=None):
    return _local_transaction(credentials.issue, connection, request_key, contracts.wire_flag(reset),
                              expected_generation)


@frappe.whitelist(methods=['POST'])
def revoke(connection, request_key, expected_generation, reason=''):
    return _local_transaction(credentials.revoke, connection, request_key, expected_generation, reason)


@frappe.whitelist(methods=['GET'])
def status(connection):
    return _local_transaction(credentials.status, connection)


@frappe.whitelist(methods=['GET'])
def installer():
    """助手的安装包在哪里。

    2026-09-27 起小助手源码在本仓 `companion/`，安装包由发布流水线放到我们自己的服务器
    `pearnly.com/downloads/companion/`（固定名始终是最新正式版），全平台一个地址，默认就用它；
    站点配置 `pearnly_cowork_agent_installer` 只在要临时指向别处时覆盖。
    """
    access.require_actor()
    url = str(frappe.conf.get('pearnly_cowork_agent_installer') or '').strip() or INSTALLER_URL
    version = str(frappe.conf.get('pearnly_cowork_agent_version') or '').strip()
    return {'url': url, 'version': version, 'configured': bool(url)}
