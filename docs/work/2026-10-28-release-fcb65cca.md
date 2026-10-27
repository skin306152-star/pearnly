# 上线 fcb65cca：出入库与收银写进 Express、跨天退货 SR、按台车辆、库位清单、打印两批、页头按钮与路由白名单、多仓 P0、无守卫接口修复、LINE 菜单锁、团队邮箱可空、识别自检（2026-10-28）

主会话 10-27 批准部署，用户说「部署不用卡时间，随时可以」；验收总结论「可部署」（范围 5afc3755 + team-placeholder-email + ocr-self-check），23:2x 开始。线上原来是 `08bfb419`（[上次发布](2026-10-25-release-08bfb419.md)）。
这一批另有一个直接目的：新租户 polymer（ISS-2026-0312）、gize（ISS-2026-0316）开户卡在「创建工作台」，原因是生产 ERP 还没有
`ERP Serial No` / `ERP Serial Movement` 两张表；部署后由「Line Bot 创建与配置」窗口点「重试开通」。

## 版本

- main `fcb65cca`（相对 08bfb419 共 202 个提交）。中途 stage 过的 6c8860b9（`sha256:5ea6001c…`）、5afc3755（`sha256:e5b00eb6…`）两个镜像都作废，
  这一批最后又带上团队邮箱可空与识别自检，从 fcb65cca 重新构建。
- 镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:6fe735e7175ab24b96e101d45ad8de3cdf1e19a0ea5972f455df0667924cc247`：构建 run 40540609323
  （候选检查三车道：离线检查、DMS 流程实验室、DMS 快照实验室都通过），stage run 40541883316；服务器上核对 revision = fcb65cca。
- CC 桌面版 1.12.6 正式版：`companion-release.yml` target=prod run 40545810830（见下）。

## 这一批包含（按任务记录）

- **出入库与盘点写进 Express**（[计划](2026-10-24-erp-stock-express-plan.md)、[B 段剩余设计](2026-10-25-stock-b-remaining-design.md)）：出入库单 OU / JU、盘点调整 TK（载荷 6），
  包装换算（载荷 7）、作废原地作废（载荷 8），收银日汇总现销 HS（B3a）与免税基数（载荷 9）、跨天退货 SR（载荷 10，[记录](2026-10-26-stock-refund-sr.md)、
  [CC 侧](2026-10-26-stock-refund-sr-cc.md)），混合税率与免税行（载荷 11，测试包 e）。CC 桌面版 1.12.3 → 1.12.6 的客户端改动随这些一起。
- **出入库附件、按台车辆、库位清单、内部编码**：[附件](2026-10-26-stock-attachments.md)、[按台](2026-10-26-vehicle-units.md)、[库位](2026-10-26-locations.md)、
  [内部编码](2026-10-26-internal-code.md)。
- **打印**：[第一批](2026-10-26-print-batch1.md)、[第二批](2026-10-26-print-batch2.md)（采购订单、报价、销货单、收银小票与退货、交班、盘点点数表与结果表）。
- **页头入口与路由白名单**：[native-buttons](2026-10-26-native-buttons.md)（产品页只出声明过的页头入口，个人页不再露出「权限」「创建用户电子邮件」）、
  native-routes（只放行产品声明且已实现的页面路由）。
- **多仓闭环 P0**（[记录](2026-10-26-multi-warehouse-closed-loop.md)）：差异报表「只看」、有货的仓不能停用、收银台只绑启用仓、按台期初按本公司认仓、
  推送遇到没有 Express 仓库码的仓停下交人。
- **无守卫接口修复**（[记录](2026-10-27-unguarded-endpoints-fix.md)）：DMS 推送记录 `api_list` / `api_status` 与共用层连接清单接口删除，控制面 LINE 渠道只给运维，
  出入库单号预览要出入库 / 盘点权限，CI 拦新增无守卫接口。
- **LINE 菜单发布锁**（`5afc3755`）：ERP / Cowork / DMS 新频道第一次发布菜单期间一直持锁、复用一条连接（原来锁 120 秒、建菜单要 145 秒）。
- **控制面**（`61052381`）：开户单没填邮箱时给「首个产品前缀@pearnly.com」，hello 为保留前缀。
- **团队加人邮箱可空**（`f1f7b02f`、`f9517a09`）：ERP 与 Cowork 团队加人不填邮箱时给 `<登录名>.<站点前缀>@pearnly.com`，填了要合法；站点初始设置未完成时
  外壳路由白名单放行（设置向导不再被弹回产品首页）。
- **Claude 识别自检**（`aa983160`、`fcb65cca`）：各行合计对不上的单交 Opus 重读、价格含税与否按数字定、首读里没被质疑的字段保留；
  有录下的重放基准支撑（起因 mrerp LINE 录入 LI-00029 单价读成整箱价，核对交 Line Bot 窗口做）。

迁移补丁：ERP `v1_14_pos_day_start`（已接 Express 的公司收银日汇总从上线当天起）、`v1_15_pos_receipt_vat`（增值税登记户的完成小票逐行拆出税额，应收合计不变）；
Cowork `v0_0_26_internal_tables_no_native_read`（内部表不再给 All / Guest 读，清掉这些表的存量共享）。

## 部署前（只读）

- 服务器：可用内存 27 GB，没有非生产容器（10-27 19:4x）。
- DocShare（Cowork 网页容器里按「Cowork 模块非子表」分组，`docshare-count.py`）：ccdemo 总 1、erpcowork 总 1、testcowork 总 2；落在 Cowork 内部表上的都是 0
  → v0_0_26 实际不删记录。
- v1_15 会动的小票（ERP 网页容器里按补丁同一口径推算）：**mrerp 1 家公司、完成小票 0 张，不改任何数据**；testerp 只有 CO-00001 有 3 张（10-01 至 10-03，含税价、
  都含计税商品），这家税务查询缓存里是 VAT 登记户，补丁会记成登记户并把 3 张拆出税额、合计不变；这家没接 Express。生产当时还没有收银日汇总表与小票上的日汇总字段
  （HS 是这一批新上的），没有任何「已写进 Express」的小票；补丁本身也只挑没进日汇总、或日汇总还没写进去的小票（written / mismatch / handled 一律不动）。
- 备份：`activate-product.sh` 迁移前按现有流程备份各站点（文件名见下）。
- 服务器上另有开户代理留下的维护容器 `pearnly-maintenance-erp`（polymer / gize 开户卡住时 `ensure_maintenance` 建的，只跑 sleep、旧镜像）：
  主会话定不人工停，ERP 激活时按 `activate-product.sh` 的规则收掉（已收）。

## 部署

服务器脚本照 08bfb419 的模板（`/opt/pearnly/state/deploy-fcb65cca.sh`：产品之间等可用内存 ≥ 8 GB，每 10 秒记内存到 `mem-fcb65cca.log`）。

| 步骤 | 时间（UTC，曼谷 +7） | 结果 |
|---|---|---|
| 部署工具核对 | 16:33:21 | Deploy tooling matches this image |
| ERP | 16:33:23–16:35:59 | **失败**：进维护、停服务、备份、迁移；mrerp 迁移成功，**polymer 迁移在 after_migrate 中断**（见下「中断」），testerp 未迁移；ERP 容器停着，mrerp / testerp 502 |
| ERP 向前补完（主会话定 A） | 16:39:55–16:43:12 | `resume-erp-fcb65cca.sh`：只读核对 mrerp 补丁齐全 → 单独迁移 testerp（成功）→ polymer 跳过 → 启用定时任务 → 新镜像重建 erp 全部 7 个服务（含 frontend）→ 关维护 → 写 `active-image-erp.json` → 预热 → 刷 nginx（无改写）。**16:43:12 ERP 恢复** |
| Cowork | 16:44:01–16:48:27 | activated with verified image |
| DMS | 16:48:27–16:50:40 | activated with verified image |
| 基础层 | 16:50:40–16:51:47 | Foundation activated |

整机可用内存全程 ≥ 26 GB（部署前 28.4 GB，结束 26.2 GB）。

迁移前备份（各站点 `private/backups/`）：

| 站点 | 数据库备份 |
|---|---|
| mrerp | `20261027_233342-mrerp_pearnly_com-database.sql.gz`（398 KiB） |
| polymer | `20261027_233347-polymer_pearnly_com-database.sql.gz`（297 KiB） |
| testerp | `20261027_233351-testerp_pearnly_com-database.sql.gz`（10.4 MiB） |
| ccdemo / erpcowork / testcowork | `20261027_234450-ccdemo…`（1.5 MiB）、`20261027_234500-erpcowork…`（2.0 MiB）、`20261027_234510-testcowork…`（4.8 MiB） |
| ris / testdms | `20261027_234854-ris…`（4.3 MiB）、`20261027_234858-testdms…`（1.0 MiB） |
| billing（基础层） | `20261027_235053-billing_internal-database.sql.gz`（524 KiB） |

补丁输出：
- `v1_14_pos_day_start`：mrerp、polymer、testerp 都已执行。
- `v1_15_pos_receipt_vat`：mrerp **0 家公司、0 张小票**；polymer 0 / 0；testerp **0 / 0**。部署前按税务查询缓存推算 testerp CO-00001 的 3 张会拆税，
  补丁实际判定 0 家公司——补丁对缓存的判定与我的推算不一致，原因没查（测试租户、3 张小票保持原值，对客户站点没有影响），记入后续项。
- `v0_0_26_internal_tables_no_native_read`：ccdemo、erpcowork、testcowork 都已执行；迁移后 DocShare 与迁移前相同（ccdemo 1、erpcowork 1、
  testcowork 2，Cowork 内部表 0），没有删记录。

### 中断（ERP 停机约 10 分钟）

- **时刻**：16:33:2x UTC（曼谷 23:33）进维护、停 ERP 容器 → 16:35:59 激活失败 → 16:43:12 恢复。ERP 三个站点（mrerp、testerp、polymer）约 10 分钟不可用
  （维护 + 容器停）；Cowork、DMS、基础层不受影响。时间在深夜，mrerp 维护期间没有写入。
- **根因**：`bench --site all migrate` 依次迁移 mrerp → polymer → testerp。polymer 是 10-27 晚开户卡在「创建工作台」的新租户，站点配置里还没有
  `pearnly_features`；迁移收尾钩子 `pearnly_erp_portal.work.service.ensure_on_migrate` → `boards.default_board` 建默认任务看板时走
  `work.access.require()`，因为站点没买「任务管理」被判无权，抛 `PermissionError`，**整个迁移命令中断**，激活脚本按设计停在维护、不放开流量。
  这段任务看板代码 08bfb419 与 fcb65cca 完全相同，与本批改动无关；用旧镜像迁移 polymer 同样会失败，所以照常用激活脚本回退也会卡住。
- **处理**：激活脚本禁止迁移中断后让旧 worker 连已部分升级的库，回退要用 23:33 备份恢复 mrerp、polymer 数据库。主会话在 A（向前补完）/ B（备份恢复回退）中定 A：
  只读核对 mrerp 迁移完整（本仓三个应用补丁全部记入 Patch Log、`ERP Serial No` / `ERP Serial Movement` 已建、部署后 Error Log 0）→ 迁 testerp →
  polymer 跳过 → 照激活脚本后半段原样完成。续做脚本第一次跑时补丁核对写得太宽（把 Frappe 自身补丁也算进去）、第二次站点列表混入容器启动输出，
  都在迁移前停下、没有改动数据，第三次跑通。
- **polymer**：这次只差迁移收尾建默认看板，表结构与补丁已迁好、维护已关；它还没发布域名、不对外服务。由「Line Bot 创建与配置」窗口点「重试开通」配好功能后再迁移一次补上。
- **根因修复列为 10-28 第一项（今晚不改）**：迁移收尾钩子遇到功能未配置的站点跳过并记日志、不得中断整个迁移；激活脚本对未完成开户的站点单独处理。

## 回读（16:43–16:53 UTC）

- 产品容器 24 个全是新镜像 `6fe735e7`；三个 ocr-worker 都带 `FRAPPE_BACKGROUND_WORKERS_NOFORK=1`；`active-image-erp.json` 是新镜像。
- 八个站点（mrerp / polymer / testerp / ccdemo / erpcowork / testcowork / ris / testdms）维护模式都已关闭；23:33（曼谷）之后 Error Log 全部为 0。
- 七个域名 `/api/method/ping` 200（mrerp、testerp、erpcowork、testcowork、ccdemo、ris、testdms）；mrerp / testerp `/login` 302 到统一登录页。
  fatcc.com 首页 200，`/login` 与 `/api/method/ping` 403（入口只放首页，部署前状态未比对，记作观察）。
- mariadb 873 MiB / 8 GiB（没有 exec 进数据库容器）；billing-web 16:51:47 起、重启次数 0。
- 非生产容器：无（`pearnly-maintenance-erp` 已收）。

## 线上冒烟

- **全量只读冒烟**（`run.py --target production --readonly --all`，测试租户、不写数据）：通过 42、不通过 2、没测到 135（人工项）。
  不通过两项是首页打开时限：ERP 首页 4.2 秒、Cowork 首页 3.1 秒（时限 3 秒），刚部署完的冷启动；单独重跑 ERP 1.9 秒、Cowork 1.5 秒，都通过。
  个人页 `erp.user-profile`、`cowork.user-profile`（testcowork）通过：页头没有「权限」「创建用户电子邮件」。
- **mrerp 盘点页**：Administrator 临时会话只读打开 `/desk/pearnly-stock-count`，正常、无报错、页头无多余按钮（冷启动 3.0 秒），会话用完即删。
- **DMS 推送记录接口已删**：生产 DMS 容器里 `push_log.api_list` / `api_status` 不存在（`status_of` 保留），共用层 `api_list_endpoints` 不存在；
  线上直接调 `push_log.api_list` ris、testdms 都回 417。
- **团队加人邮箱可空**：生产 ERP、Cowork 的 `team.service.add_member` 邮箱参数默认空、带 `<登录名>.<前缀>@pearnly.com` 占位逻辑（代码回读；没在线上真加人）。

## CC 桌面版 1.12.6 正式版

- 服务端回读、冒烟通过之后发：`companion-release.yml` target=prod run 40545810830（源码 fcb65cca）。
- latest.json 回读：1.12.6、源码 `fcb65cca`、sha256 `c6f01d54a8515084a7c00e4b15b6e585138c02af71eb9c71d9dc4419f6002c0e`、size 153,158,945、发布时间 17:04:44Z。
- 心跳：生产 ERP、Cowork 容器里 `pearnly_core.cc_desktop.release.latest()` 都是 1.12.6（DMS 不挂发布目录、CC 不连 DMS，为空属正常）。
  客户端随下一次心跳回复拿到新版。上一个正式版 1.12.1（08bfb419）。

## 回退

三个产品与基础层回 `sha256:fb1037adfa97d292ab81c3a6dc51ec4a0b4b533556cda6f6ce7e7e4577a43d79`（main `08bfb419`）。
- 激活脚本回退同样要跑迁移，polymer 那一步在根因修复之前仍会中断：回退前先处理 polymer（配好功能，或回退时逐站迁移跳过它）。
- 新增表 / 字段 / 索引是加法，旧代码不受影响；三个补丁的数据改动：v1_14 只写已接 Express 公司的 `express_pos_from`，v1_15 本次 0 张，v0_0_26 本次 0 条。
- CC 正式版要回 1.12.1 需重新发布 08bfb419 的源码。

## 没验证 / 后续

- **10-28 第一项**：迁移收尾钩子（`work.ensure_on_migrate` 及同类 after_migrate）对功能未配置的站点跳过并记日志、不中断迁移；激活脚本对未完成开户的站点单独处理。
- polymer（ISS-2026-0312）、gize（ISS-2026-0316）「重试开通」：已通知 Line Bot 窗口；polymer 要补一次迁移把默认看板建出来。
- LI-00029（mrerp LINE 录入单价读成整箱价）核对：Line Bot 窗口做。
- 多仓闭环 P1（`pearnly/multi-warehouse-p1` 84e489c6，验收复核通过）不在这一批，等主会话安排变基合入、随下一批上线。
- fatcc.com `/login` 与 ping 403 是否与部署前一致：没比对，下次部署前先记一次。
- v1_15 在 testerp CO-00001 判定 0 家公司、与部署前按缓存推算的 3 张不一致：查 `companies._rd_vat` 对缓存（分店号 0 / 公司分店为空）的判定。
