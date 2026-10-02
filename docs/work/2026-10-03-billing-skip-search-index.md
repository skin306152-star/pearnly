# billing 迁移不排网站搜索索引（2026-10-03）

分支 `codex/billing-skip-search-index`，基线 `origin/main` a2eda882。随下一批一起部署（基础层激活时生效）。

## 现象

共用 redis-queue 里滞留 19 个 `billing.internal` 的任务（只读查清后按压测窗口决定逐个删除：删前 long 13 / default 6，删后 0 / 0）：
13 个 `frappe.search.website_search.build_index_for_all_routes`（billing 每次迁移各排一个），4 个已删测试租户的 `delete_dynamic_links`、
1 个桌面图标清理、1 个建联系人。都只写 billing 自己的库，没有外部写入。

原因：billing 只有 web 容器、没有后台进程；产品的后台进程看不到 billing 的站点目录，所以这些任务没人处理。

## 修法

`scripts/production/activate-foundation.sh` 的 billing 迁移加 Frappe 原生开关 `--skip-search-index`（锁定版本的 `bench migrate`
支持）；billing 不对外提供网站搜索。产品站点有后台进程，照常建索引，不动。`scripts/production/test-activate-product.py`
断言 billing 迁移带这个开关（15 项通过）。

其余三类偶发任务（删除链接、建联系人）只在 billing 建删账号时出现，不随每次部署增长；如再出现按同样方式只读查清后处理。
