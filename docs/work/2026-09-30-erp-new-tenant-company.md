# 2026-09-30 ERP 新租户没有公司：LINE 与 POS 入口消失

状态：已提交到任务分支，候选镜像离线检查通过；线上 mrerp 已手工重建菜单。

- 分支 `codex/erp-new-tenant-company`，基线 `56563d2a`（= 当时 `origin/main`），工作树 `.claude/worktrees/erp-new-tenant-company`。
- 起因（用户）：mrerp 个人信息页没有 LINE；销售系统下没有销售点（POS）、收据记录、轮班日志，但团队权限里都勾着。

## 证据（生产只读）

| 事实 | 来源 |
| --- | --- |
| 账号 `mrerp_erp@gmail.com` 是 ERP Admin，功能齐，`guard.has("integration.read") = True` | 站点内按该账号计算 |
| 12:14 UTC 启动时 `active_company()` 抛"这个账号没有可用的公司"，整段 boot 在一个 try 里，`pearnly_line` 没写 | Error Log |
| 公司 `CO-00001` 12:16 才由用户自己建，`is_default = 0`；此前一家公司都没有、也没删过 | `tabERP Company`、Version、Deleted Document |
| 全站 Workspace Sidebar「Pearnly ERP」9-29 22:20（部署重建）缺收银三页；testerp 同时刻重建是全的 | `tabWorkspace Sidebar Item` |
| 按该账号现算侧栏有 POS 三页，boot 缓存的页面清单也有；但个人投影只能从全站文档里挑 | `navigation.extend_boot` |

## 根因（三处）

1. **全站菜单按当前数据过滤**：`ensure_navigation` 生成全站文档时套用了 ERP 的页面过滤器（"有没有公司开收银"），
   没有公司时收银三页被写死在文档外；每个人启动时只能从文档里减、不能加。
2. **boot 一项失败连带全部**：取公司失败 → 仓库建议值、LINE 标志一起丢，页面无提示。
3. **开户不建公司**：新站点安装时补丁清单里的 `v1_3_company_scope`（会建默认公司）只记为已跑、不执行；开通流程也没建。

## 修复

- `pearnly_core/desk/navigation.py`：`_offered(..., page_filter=False)` 只给全站文档用；按当前数据过滤只在每人 boot 投影里做。
- `pearnly_erp_portal/masters/boot.py`：四项各自 try，失败给默认值并按项记日志（英文标题）。
- `pearnly_core/provisioning.ensure_product_workspace`：写完 `pearnly_company_name` 后调产品声明的 `pearnly_tenant_ready`；
  ERP 声明 `masters.company.ensure_default`（没有公司就用开户公司名建默认公司；有公司没默认就把第一家设为默认并清 boot 缓存）。
- 补丁 `v1_11_default_company`：已开通的 ERP 站点补一次（mrerp 会把 `CO-00001` 设为默认），幂等。
- `infrastructure/production/site-step.py` 的 `verify_site`：ERP 必须有启用的默认公司。

## 线上手工处理（用户："先重建 mrerp 菜单"）

- 2026-09-30 12:31 UTC `bench --site mrerp.pearnly.com execute pearnly_core.desk.navigation.ensure_navigation`；
  回读全站文档含 `pearnly-cashier / pearnly-pos-receipts / pearnly-pos-shifts`（内部已清缓存）。只动菜单，不动业务数据。

## 验证

| 版本 | 环境 | 场景 | 结果 |
| --- | --- | --- | --- |
| 工作树 | 本机 python 3.14 | `tests/test_native_desk_contract.py` 新增"全站文档保留收银页、个人投影按公司过滤"；旧 `navigation.py` 上失败 | PASS |
| 工作树 | 本机 | `tests/test_erp_boot_items.py`（4 项，新增并接入 development.yml）；旧 `boot.py` 上"无公司仍出 LINE"失败 | PASS |
| 工作树 | 本机 | development.yml 24 条 python / node 闸门，含 `check-architecture.py` 与 i18n 检查 | PASS |
| 工作树（五个 App 源码只读覆盖） | 生产 ERP 镜像 `4c2c12ed`，`--network none --memory 2g` | `ci-candidate-checks.sh offline` 全部 | PASS |
| 线上代码 `ensure_default` | 生产 mrerp，事务内执行后回滚 | 补丁效果演练：`CO-00001` 从非默认变默认 | 符合预期，已回滚 |

未覆盖：真实新租户完整开通；补丁在真实站点上的执行（随下次 ERP 激活 migrate 执行）。
