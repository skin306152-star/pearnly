"""Human management RPCs only; exp_ tokens do not authenticate any RPC."""
import frappe

# 授权判据走连接域那一份（companion 的域依赖只有 connections/push/targets；
# `pearnly_cowork.access` 不在它的允许列表里，不能在这里直接 import）。
from pearnly_cowork.connections import access

from . import credentials

INSTALLER_URL = 'https://pearnly.com/downloads/companion/CC-Setup.exe'


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


# 管理用的 issue / revoke / status 三个接口已下线（ADR-021）：没有界面调用，多台电脑后「当前那把」也说不清。
# 电脑一律经 `register` 拿自己的密钥；断开连接由电脑自己 `device_leave`。


@frappe.whitelist(methods=['GET'])
def online(connection):
    """录入入口看 Express 能不能选：本人能用这条连接、助手当前在线。不要求管理权限（与 LINE 同一判据）。

    只有「录入」角色的员工原来走 `status`（管理用）被拒，进不了上传（2026-09-28 录制发现）。"""
    return {'connection': connection, 'online': bool(credentials.online(connection))}


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


@frappe.whitelist(methods=['POST'])
def register(request_key, express=False, replace=False, expected_generation=None):
    """CC 桌面端登录后连上这台电脑：回给对话页的密钥只交给本机桌面端，不显示（见 credentials.register）。"""
    return _local_transaction(credentials.register, request_key, express, replace, expected_generation)
