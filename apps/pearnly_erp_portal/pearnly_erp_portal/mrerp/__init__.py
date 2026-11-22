"""ERP 接 MR.ERP 当账本：对话 / 机器产生的单导入直写（评估 2026-11-02 §13 第 6 步「导入直写」一半，§14.6）。

ERP 公司选账本（Express / MR.ERP）、ERP 自己的 MR.ERP 连接（网址；账号密码只经 CC 桌面版授权写入，`connection`）、账套目录与
选账套（`catalog`）、导入菜单 / 主档默认值按公司选一次（`choices`）、ERP 单据 → 规范载荷（`contracts`，纯函数）、导入直写与
单据上的通用账本栏（`push`）、对外入口（`api`）。MR.ERP 规则与导入执行都调共用层 `pearnly_core.mrerp`（与 Cowork 同一份，导入表
逐字节相同由 `tests/test_erp_mrerp_golden.py` 锁住）；不导入 Cowork（ADR-009）。代录不在这里（另做）。

**11-22 用户定对用户开放**（原 11-03 起关着）：服务端所有 MR.ERP 入口（选账本、连接、账套、选择、导入、对话工具与待处理的
MR.ERP 分支、CC 授权钩子）都先读 :data:`MRERP_LEDGER_ENABLED`；它是唯一总闸，改回 ``False`` 就一律挡住、ERP 回到只有 Express
的行为与输出。不按站点 / 租户开。打开后不经人触发的读取（每小时整读主档、写单后的那次整读）只用还热的 MR.ERP 会话、不登录
（`masters.run(warm=True)`），不会自己去顶掉会计浏览器的登录；MR.ERP 写入只在用户操作（提交单据、收款写入、人点的同步）后发生。
"""

MRERP_LEDGER_ENABLED = True


def enabled() -> bool:
    """MR.ERP 账本此刻开没开（读模块常量，测试里打补丁）。"""
    return bool(MRERP_LEDGER_ENABLED)
