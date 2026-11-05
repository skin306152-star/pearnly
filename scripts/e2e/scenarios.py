"""登录后端到端的场景步骤（登记 tests/e2e/registry.toml，执行器 scripts/e2e/run.py）。

每个场景一个函数 `step(page, ctx)`，登记在 `STEPS`；没登记的场景执行器记「没测到：场景步骤还没写」。
步骤只断言业务结果（落在哪个站、页面上出现了什么、服务端回读的状态），不只看能不能打开：
- 断言不对 → raise Failed（人话写清哪一步、期望什么、实际什么）；
- Keycloak 拒绝登录 → raise LoginRejected（执行器不重试，防锁号）；
- 页面导航超时 → raise NavigationTimeout（执行器把这一格重跑一次）；
- 这一格在此刻测不了 → raise NotTested（说原因）。
密码只经 `ctx.accounts[...]` 传给 `page.fill`，不拼进地址、不打印。

步骤要等 e2e 账号建好（方案 §12 第 4 问，主会话问用户中）后对着生产测试租户逐个调通；跑通一个就在登记表里写上 since。
"""
from __future__ import annotations

from dataclasses import dataclass


class NotTested(Exception):
    """这一格测不了（前置自检不过、步骤还没写）：不算通过也不算不通过。"""


class LoginRejected(Exception):
    """Keycloak 拒绝登录：不重试，免得把 e2e 账号锁住。"""


class NavigationTimeout(Exception):
    """页面导航超时：这一格重跑一次。"""


class Failed(AssertionError):
    """断言的业务结果不对。"""


@dataclass
class Context:
    product: str          # erp / cowork
    run: str              # desktop / mobile / iphone
    sites: dict           # erp / erp-second / cowork → 测试租户域名
    entry: str            # CC 入口 https://fatcc.com
    accounts: dict        # erp / cowork → Account（repr 不带密码）

    @property
    def site(self) -> str:
        return self.sites[self.product]


STEPS: dict = {}
