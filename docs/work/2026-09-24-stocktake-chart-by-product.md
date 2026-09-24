# 盘点工作台图表：按商品分类

- 用户口径（2026-09-24）："我觉得这个柱形图设计得不合理，分类不够明确"；随后"按现在的基础上改一版，
  本地预览拉起来我看看，多搞些数据进去"。
- 授权范围：本地实现 + 本地预览 + 本机验证。**未推送、未合并 main、未部署**；测试环境与线上仍是旧图。
- 基线 main `3c5e4a354bed3463bed2b048c1033e12bf3a1c68`；分支 `codex/stocktake-chart`；
  工作树 `/Users/skin/.codex/worktrees/stocktake-chart/pearnly`。
- 归属 ERP inventory 的盘点工作台前端（图表）；不动其他任务的分支、容器与数据。

## 为什么改

旧图按 `${仓库} / ${仓位 || "未填"} · ${单位}` 分类，同时把数量**加总**。两个后果都能在用户的截图上看到：

- 一次盘点通常只有一个仓、仓位还常常是空的，所有行并成**一个分类**，分类名还是"仓库 / 未填"这种拼接串；
- 同一个分类里正负差异互相抵消：截图那单净差只有 +25，四行实际是 −34/+4/+32/+23（绝对值 93），
  表里四行全标红，图上两根柱却几乎一样高；
- 记录是按**实际仓位**归组的，所以仓位被改过的条目会额外生成一根"账面 0 / 实盘 N"的柱，排序又按 |差异| 靠前，
  容易排到前面当噪声。

## 改了什么

`apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/stocktake.js` 图表段：

- 分类维度改成**商品**：按 `row.product` 分组、标签用商品名，取 |实盘 − 账面| 最大的前 12 个；
  与原生报表 `erp_stock_count_variance` 的"看板按＝商品名称"同一口径。
- 两个数据集仍是账面数量 / 实盘数量，配色（灰 + 产品强调色）不变。
- 记录先按快照行聚合一次（原实现是**每行**都 `state.entries.filter(...)`，复杂度行数 × 记录数）。
- 纵轴数字走 `shortenYAxisNumbers`，与报表图表一致。
- 没有画图时（一行都没盘）保持原样：不渲染图表、不报错。

同一商品的多个仓位行在图上合并成一根柱（这是"商品"这一维的定义）；各仓位明细在下面表格里。

## 本地预览环境

复用上一轮盘点任务的隔离环境：ERP-only 站点 `stocktake.localhost`，Colima context `colima-pearnly-stocktake`，
预览 lane `http://stocktake.localhost:8136`（挂载本任务工作树源码）、验收 lane 8126。

- 切源码：`bash scripts/stocktake-preview.sh up`（只重建预览的两个服务）。
- 预览地址：`/desk/pearnly-stock-count`；示例任务 `STC-00214 / 月底盘点`。
- 灌数据：新增 `scripts/local-review/seed-stocktake-chart.py`（只写 lab 站点，按业务键幂等，整批一个事务）。
  36 个商品、3 个仓 12 个货架、3 份盘点：月底盘点 43 行 / 38 行已盘 / 33 处数量差异 / 6 处仓位差异，
  冷藏区抽盘（跨仓），备用仓快盘（已结束、只读态）。
- 该库停在上一轮（`8a67e7b`），预览 lane 跑主线代码需要升级：先备份到
  `~/.config/pearnly/backups/stocktake-lab-pre-chart-migrate-20260924-230552.sql.gz`，
  再 `bench --site stocktake.localhost migrate --skip-failing`（跳过原因见下）。

## 本机验证（2026-09-24，预览 lane 8136，Chrome + Playwright）

| 场景 | 期望 | 实际 |
| --- | --- | --- |
| 月底盘点（43 行 / 38 已盘） | 柱形图上 12 个分类＝商品名，顺序＝差异绝对值前 12 | 一致：大米/垃圾袋/醋/味精/洗衣粉/面粉/一次性手套/矿泉水/蚝油/牛奶/食盐/辣椒酱 |
| 图上数值 | 与接口口径一致 | 12 组 (账面, 实盘) 与 `api_stock_count_review` 逐组相同 |
| 冷藏区抽盘 / 备用仓快盘 | 按商品出图 | 3 类 / 2 类，均正常 |
| 一行都没盘（STC-00110） | 不画图、无报错 | 无图、无 JS 报错 |
| 窄屏 390×844 | 不横向溢出 | `scrollWidth == innerWidth`，标签按框架规则截断 |
| 页面资源 | 无 4xx/5xx、无 JS 报错 | 通过（图表、44 行表格、汇总数都渲染） |

本机闸门：`check-architecture.py` PASS（0 违规）；`node tests/test-native-desk.cjs`、`tests/test-line-stocktake.cjs`、
`tests/test-stocktake-scanner.cjs`、`node --test tests/test_pos_lifecycle.js`、`test-roster-selection.cjs`、
`test-records-selection.cjs`、`tests/test_native_desk_contract.py`、`tests/test_queue_targets.py`、
`scripts/i18n-build.py --check`、`git diff --check` 全通过。

基线既有失败（与本改动无关，在 main 上同样失败）：`scripts/check-desk-ui.py` 2 条
（`company.js:286`、`stock-opening.js:281`）；`tests/test_product_feature_catalog.py` 3 个 error
（主机 Python 3.9 不支持源码里的 `X | None` 注解）。

## 过程中发现的环境问题（未改这些代码）

1. **升级会卡在 `patches/v1_5_terminal_warehouse`。** 它放在 `[pre_model_sync]`，但要查
   `ERP Warehouse.company`；从没有该列的旧库升级时，这一列要到模型同步才建，于是整次 migrate 直接
   `Unknown column 'company'`。本机临时用 `--skip-failing` 过掉（该站没有收银台，跳过无数据影响）。
   根因是补丁顺序：挪到 `[post_model_sync]` 的 `v1_3_company_scope` 之后、`v1_6` 删列之前即可同时满足
   新老库。归属 POS/公司边界那个窗口，本次只记录。
2. **预览镜像被改过，导致两条 lane 的资源索引互相污染。** 环境里的 `PEARNLY_PREVIEW_IMAGE` 指向
   `pearnly/frappe-sample:16.34.1`（与验收镜像同框架版本，但构建产物 hash 不同）。共享 `sites/assets`
   与 Redis 客户端缓存时，桌面页会拿到另一条 lane 的 `desk.bundle.*.css` 名字，预览 lane 就 404（页面裸样式）。
   处理：删掉 `_83beabe19b43d391|bootinfo` 并重启预览 web，让预览 lane 自洽。**若之后再从 8126 登录浏览，
   污染会复现。** 彻底做法是让预览镜像与验收镜像同源（`stocktake-preview.sh faithful` 那条路）。
3. **登录 origin 调整（本地隔离环境配置，不进仓库）**：lab realm 的 Keycloak 客户端追加了
   `http://stocktake.localhost:8136/api/method/pearnly_core.identity.callback`，站点 `pearnly_oidc_redirect_uri`
   指向 8136（8126 的回调保留）。这样用户只开 8136 就能登录，不会触发第 2 条的污染。

## 未做 / 下一步

- 只做了"按商品分类"这一版：没有加"看板按"切换（商品/仓库/仓位），也没有改成单根"差异柱"（实盘 − 账面）。
- 未推送、未合并、未部署；测试环境 `testerp.pearnly.com` 上看到的仍是旧图。
- 上一轮盘点任务的验收 lane（8126，镜像停在 `1fd06f3`）保留未动；它的资源索引已被预览 lane 接管，
  要继续用它做"和上线一致"的检查，需按 `stocktake-preview.sh faithful` 重建同源镜像。
