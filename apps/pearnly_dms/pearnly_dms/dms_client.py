# -*- coding: utf-8 -*-
"""DMS 业务客户端 —— **一句话都不提 Playwright**。

它只认一个注入的 `transport`（有 `get`/`post` 就行），所以：

· 生产：`dms_adapter.MrerpDmsAdapter.transport()` 把"已登录的浏览器"包成管道塞进来；
· 单测：塞个假管道，几百行表单逻辑不用起浏览器就能验。

这个分层是老产品的设计（`services/erp/mrerp_dms_client.py` 的注释写得很清楚），
照搬 —— 它让"业务对不对"和"浏览器起不起得来"能分开排查。

三个 mixin 按职责拆开（也是照老产品拆的，避免堆成一个巨石文件）：

· `DmsClientFormsMixin` —— 跟 PHP 表单页对话的脏活（抠默认值、解析下拉、地址级联）
· `DmsClientIntakeMixin` —— 客户的查/建/改
· `DmsClientOpsMixin` —— 订车单、主档、编号
"""

from __future__ import annotations

from typing import Any

# 公开再导出，调用方只需要 import 这一个模块
from pearnly_dms.dms_client_base import (  # noqa: F401
    DmsClientError,
    excel_serial,
    to_be_date,
)
from pearnly_dms.dms_client_forms import DmsClientFormsMixin
from pearnly_dms.dms_client_intake import DmsClientIntakeMixin
from pearnly_dms.dms_client_ops import DmsClientOpsMixin


class DmsClient(DmsClientOpsMixin, DmsClientFormsMixin, DmsClientIntakeMixin):
    """各 mixin 里的 `self.*` 经 MRO 解析回这个类，所以它们能互相调用。"""

    def __init__(self, transport: Any, base_url: str, *, admin_transport: Any = None):
        # base_url 以 /dms/ 结尾
        self.transport = transport
        self.base_url = base_url.rstrip("/") + "/"
        # 凭据组：配了 admin（admin_transport 非 None）时，客户档**写**操作走它；读操作不变。
        # 可以是已建好的 transport，也可以是一个零参工厂（生产侧懒登录，
        # 避免只读也起一个 admin 会话）。
        self._admin_transport = admin_transport
        self._admin_transport_cached: Any = None

    def _resolve_admin_transport(self) -> Any:
        """惰性解析 admin 凭据组的 transport（工厂只调一次）。未配 → None。"""
        src = self._admin_transport
        if src is None:
            return None
        if self._admin_transport_cached is None:
            self._admin_transport_cached = src() if callable(src) else src
        return self._admin_transport_cached

    @property
    def admin_transport(self) -> Any:
        """配没配独立管理员 —— **只读判据，绝不触发管理员登录**。

        配了但还没懒起会话时返回那个工厂本身（非 None）。权威只读层靠它决定
        要不要走管理员会话：如果这里顺手把会话建起来，一次只读就会多登一次 DMS，
        而 DMS 对同一账号是单会话的（会把别的会话踢下线）。
        """
        if self._admin_transport_cached is not None:
            return self._admin_transport_cached
        return self._admin_transport
