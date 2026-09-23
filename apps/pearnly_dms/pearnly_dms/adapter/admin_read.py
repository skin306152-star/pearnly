# -*- coding: utf-8 -*-
"""DMS 权威只读：配了独立管理员凭据组时，读取一律借管理员会话。

销售的 DMS 账号常常看不到客户档、车型/颜色、收款银行等主档（权限按账号发）。这些数据
决定 LINE 能给出哪些选项、能不能把客户匹配上、以及提交前的合法性复核 —— 拿销售的
**不完整视图**去判，会静默把"DMS 里明明存在"的客户/车型当成不存在。所以：同租户配置
（或从老板 endpoint 继承）了独立管理员凭据组时，Pearnly 的**读**必须走管理员会话，
销售只是从这份权威映射里挑。

边界（与产品契约一一对应）：

· **只借读。** 写永远留在原销售 transport 上 —— 订车 `new.php` 用销售会话提交一次，
  保留销售归属；DMS 拒绝管理员看得见的 ID 时如实报错，绝不改用管理员偷偷重写订单。
· 没配管理员 → 原样用销售会话（单凭据路径逐字节不变）。
· 配了管理员但管理员认证失败 → **明确失败关闭**（抛 `ERR_DMS_ADMIN_AUTH`），
  绝不静默降级回销售的不完整视图。
· 一个读取块只解析一次管理员 transport（复用 `DmsClient._resolve_admin_transport`
  的惰性工厂缓存），不按字段/每一步重复登录。

凭据来源集中在这里判定：`DmsClient._resolve_admin_transport()` —— 也就是适配器
`client()` 注入的懒 admin 工厂。各模块不再自己去读端点配置或私自分叉判定。

照搬老产品 `services/erp/dms_admin_read.py`（线上基准 `501589f0`）。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from pearnly_dms.client.gateway import DmsClient
from pearnly_dms.contracts.client_base import DmsClientError

# 权威只读会话允许 POST 的路径：主档取数、客户/订车单搜索、级联选项、表单回显。
# 判据用「包含」而不是「结尾」—— showdata.php 之后还带分页/查询串。
# 写路径（cus/new.php、cus/edit.php、drfcbc/new.php、drfcbc/edit.php、附件上传）都不命中。
_READ_ONLY_POST_PATHS = (
    "component/",
    "/form.php",
)


class _ReadOnlyTransport:
    """权威只读闸：管理员会话只允许 GET/POST 到显式登记的只读路径。

    管理员凭据在 DMS 侧权限更高，一次误写代价不可回收。这里把"只读"做成**机械约束**，
    而不是靠调用方自觉（`authoritative_read_client(read_only=False)` 才拿得到无闸的会话）。
    """

    def __init__(self, transport: Any):
        self._transport = transport

    def _refuse(self, url: str):
        raise DmsClientError(
            f"admin read session refused a non read-only request to {url!r}",
            "ERR_DMS_TECHNICAL",
        )

    def get(self, url: str, timeout_ms: Any = None):
        return self._transport.get(url, timeout_ms=timeout_ms)

    def post(self, url: str, data: Any = None, files: Any = None, timeout_ms: Any = None):
        if files:
            # 附件挂载是写操作，永不属于权威只读块。
            self._refuse(str(url))
        path = str(url).split("?", 1)[0].split("#", 1)[0].rstrip("/")
        if any(read in path for read in _READ_ONLY_POST_PATHS) or path.endswith(("/car/view.php", "/bank/view.php")):
            return self._transport.post(url, data=data, timeout_ms=timeout_ms)
        self._refuse(str(url))


def _admin_transport_of(client: Any) -> Any:
    """这个 client 的管理员 transport（惰性工厂只解析一次）。无此能力 → None。"""
    resolve = getattr(client, "_resolve_admin_transport", None)
    if resolve is None:
        return None
    return resolve()


def admin_read_available(client: Any) -> bool:
    """配了独立管理员凭据组 → True。读它**不触发登录**（只看工厂/已解析的 transport）。"""
    if client is None:
        return False
    if getattr(client, "admin_transport", None) is not None:
        return True
    return getattr(client, "_admin_transport_cached", None) is not None


def authoritative_read_client(
    client: Any, *, read_only: bool = True, require_admin: bool = False
) -> Any:
    """读取用 client：配了管理员 → 独立只读 client（同 base_url）；未配 → 原 client。

    一个读取块内复用它，整块只解析一次管理员登录。配了管理员但登录失败时**不吞异常**：
    调用方按 `ERR_DMS_ADMIN_AUTH` 明确失败关闭，绝不拿销售的不完整视图顶上。
    `require_admin=True` 用于"这一步必须权威"的读取块：未配管理员直接抛
    `ERR_DMS_NO_ADMIN_CREDS`，避免静默走销售。
    """
    admin = _admin_transport_of(client)
    if admin is None:
        if require_admin:
            raise DmsClientError(
                "authoritative DMS read requested without admin credentials",
                "ERR_DMS_NO_ADMIN_CREDS",
            )
        return client
    reader: Any = _ReadOnlyTransport(admin) if read_only else admin
    return DmsClient(reader, getattr(client, "base_url", "") or "")


@contextmanager
def authoritative_read_session(client: Any, *, read_only: bool = True,
                               require_admin: bool = False) -> Iterator[DmsClient]:
    """把后续读取临时切到管理员会话，退出时**原样换回**（异常路径也换回）。

    进出都显式落回进入前的 transport，`_writer_session` 的保存/恢复因此不会把 client
    留在管理员态。未配管理员时产出原 client（行为与单凭据逐字节一致）。

    `require_admin=True`：这一块**必须**落在管理员会话上 —— 没配管理员就抛
    `ERR_DMS_NO_ADMIN_CREDS`。`run_logged_in` 在"跳过了销售登录"那一档用它兜底：
    那种情况下回落到销售会话等于在一个**没登录**的会话上读，只会读出一片空，
    而"看起来成功其实什么都没有"正是最难查的失败。
    """
    reader = authoritative_read_client(client, read_only=read_only, require_admin=require_admin)
    if reader is client:
        yield client
        return
    previous = client.transport
    client.transport = reader.transport
    try:
        yield client
    finally:
        client.transport = previous
