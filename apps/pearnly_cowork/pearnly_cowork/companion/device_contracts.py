"""CC 桌面版认电脑的两条机器方法的报文（ADR-021），不依赖框架与 IO。

  device_hello {device_key, name, always_on, host_names}：把这把已认证的密钥挂到这台电脑上；这台电脑在这条连接上
    已有自己的密钥时回 ``superseded``，这把（旧槽位密钥）随即吊销，桌面端丢掉它、改用自己那把；
  device_leave {}：吊销这把密钥本身（电脑上「断开连接」）。

`host_names` 原样存（第 0 批真机核过：测试机还有 WSL / Tailscale 虚拟网卡地址；CC 桌面版 1.7.0 起只报有默认网关的网卡，
见 companion/src/companion/device_facts.py），这里只限条数与长度，不筛（旧版或别的写法报来的照收）。
"""
from .contracts import CredentialError, device_key, text

PROTOCOL = 1
ATTACHED = 'attached'
CLAIMED_ELSEWHERE = 'claimed_elsewhere'
LEFT = 'left'
SUPERSEDED = 'superseded'
STATUSES = (ATTACHED, CLAIMED_ELSEWHERE, LEFT, SUPERSEDED)
MAX_HOST_NAMES = 32
MAX_HOST_NAME = 255
MAX_NAME = 140


def hello_request(body):
    """规整 device_hello 的请求体；多出的键忽略（同领取请求的惯例），缺 / 坏的键报 ``companion.request_invalid``。"""
    if not isinstance(body, dict):
        raise CredentialError('companion.request_invalid')
    name = body.get('name')
    name = text(name, maximum=MAX_NAME, optional=True) if name is not None else ''
    always_on = body.get('always_on', False)
    if type(always_on) is not bool:
        raise CredentialError('companion.request_invalid')
    hosts = body.get('host_names', [])
    if (not isinstance(hosts, list) or len(hosts) > MAX_HOST_NAMES
            or any(not isinstance(host, str) or not host or len(host) > MAX_HOST_NAME or '\x00' in host
                   for host in hosts)):
        raise CredentialError('companion.request_invalid')
    return {'device_key': device_key(body.get('device_key')), 'name': name, 'always_on': always_on,
            'host_names': list(hosts)}
