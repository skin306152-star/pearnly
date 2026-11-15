# 上线车 11-16（集成分支 pearnly/integration-1116）

拼车窗口维护。部署不由本窗口发起：主会话外部核对后合入 main、打标记，部署前先告诉用户。
线上现为 `72516d120`（标记 `release-20261115-1126`，11-15 19:06 部署完成）。
这条集成分支装「命中只能真机的入口、要同一提交带真机记录才过产品地图闸」的改动；不命中的由合入队列直接合 main。
基线：`origin/main` 0c87836b7（11-15 21:3x）。

## 这一车装什么

| # | 内容 | 来源分支 → 头 | 并入状态 | 真机要求 |
|---|---|---|---|---|
| 1 | 物料 / 客户 / 供应商选择框账本档优先；搜索统一（采购 / 销售对方与物料框、网页收款客户框、LINE 收款同一条查询，电话带不带分隔符都认，税号命中不被过滤，LINE 回显选中的名称；LINE 选公司提示句已批文案） | link-ledger-code-first f971c8579 + master-search-unify → a15f8c1cf（作者 11-15 晚确认这两个 SHA，顺序 1 → 2；作者本机已过受影响流程 8 条、单测 6 组） | 已并（快进） | `erp.scan.line.stocktake.liff-camera`、`erp.line.postback.erp_item`（产品地图闸要求） |
| 2 | 收款日期可改（当天不在任何一本期间时改问日期；LINE day 步、网页弹窗日期、CC 工具 date / date_needed；隐藏字段 date_chosen） | collection-date 465b5fc7a → 变基后 1fe229338（基于 a15f8c1cf；作者解了 `_after_company` 冲突，认对日期后不再带「No match」并补测试） | 已并（52105b5cc） | 合入前测试机1 NLTTEST 原生对照；LINE / 网页改日期 |
| 2b | 收款公司列表不列「已连接账套一本都没报会计期间」的公司（collection.service.companies_for） | collection-date-no-period 9d9f0c109（叠在 1fe229338 上，1 个提交） | 已并（79e38eccc） | 不命中新的真机入口；并进收款日期那趟核「有期间的公司照常可选」 |
| 2c | LINE 卡片【编辑】按卡片的公司打开那一页、不改用户当前公司（页面级公司头，线上 6）；卡片指向已不存在的单据时回列表（intake-edit-missing 两个提交，队列已合，变基去重） | page-company d9207ed4c（基于 main 9c1e783ad） | 已并（7a2e73226）；**可摘除**（主会话 11-15 定：真机排各组最后，不过或来不及就重建集成分支跳过它） | 三星一行（两家公司账号 B 卡编辑、A 不变）；共用件 erp-originals / erp-company-menu / cowork-intake-upload 的手工入口 |
| 3 | 验收体系第二期第 0 批：收款链路场景 8 条、站点脚本、首次登记 5 行验收属性（基线 903 → 939） | acceptance-collection → 892df7a4e | 已并 | 真环境首跑（Mac 栈测试机一半）；5 行真机 |
| 4 | MR.ERP 这一车之后的界面行 T9–T11、真实 testerp 数据副本升级演练 | 待三条 MR.ERP 分支进 main | 未到 | T9–T11 真浏览器 / 真对话 |
| 5 | CC 桌面版 1.12.22 服务端 | cc-1122-server e97f3b3b2（是否并入本分支待主会话定） | 未定 | 12 行（共享笔记「CC桌面版1.12.22-真机待测-11-15.md」） |
| 6 | 引导式后续分支 | 待引导式 P0 窗口给 | 未到 | 到时附清单 |

## 放行检查表（集成头随分支更新）

| 项 | 结果 | 证据 |
|---|---|---|
| 全闸门（gate.sh = development.yml） | 9f2119f64：251 项过，1 项不过 = 产品地图只差两行真机记录（liff-camera、erp_item，预期，真机后补） | ~/pearnly-devtools/integ1116-gate-1.log |
| 候选离线（线上镜像 02eebaa4…） | 9f2119f64 与合并头 1f2b2de62 均 111 组 LANE EXIT 0 | ~/pearnly-devtools/integ1116-cand-1.log、-cand-2.log |
| Development checks | 未推 | — |
| 产品地图 | 8d415ba14：只差两行真机记录（liff-camera、erp_item），其余通过 | 本地 |
| 共用件连带回归 | 7a2e73226：命中 erp-masters-selection、erp-originals、erp-company-menu、cowork-intake-upload，40 个入口（下表是 8d415ba14 时的 14 个，白天并 main 后重出全表） | `check-impact.py --markdown` |
| 受影响流程 | 1f2b2de62：交叠的 4 条收款流程（erp.line.collection、erp.web.collection、erp.collection.date、erp.collections.refused）5/5 格通过，404 秒，自建小栈 pearnly-integ1116-lab（跑完已 stop）；其余 33 条中的另外 29 条等白天并 main 后 `--changed` 跑（主会话 11-15 晚定） | ~/pearnly-devtools/integ1116-flows-1.log |
| 全闸门（合并头） | 1f2b2de62：251 项过，同上只差两行真机记录 | ~/pearnly-devtools/integ1116-gate-2.log |

### 共用件连带回归（8d415ba14，`erp-masters-selection`）

| 入口 | 方式 | 结果 |
|---|---|---|
| `erp.entry.choices` | manual | |
| `erp.cc.write` | auto：express | |
| `erp.line.intake.submit` | manual | |
| `erp.purchase.original-upload` | manual | |
| `erp.sales.original-upload` | manual | |
| `erp.line.item-questions` | manual | |
| `erp.trade.line-stock` | manual | |
| `erp.line.webhook-sim` | auto：line | |
| `erp.line.chat` | manual | |
| `erp.line.collection` | offline | |
| `erp.collection.server` | offline | |
| `erp.cc.collection` | offline | |
| `erp.masters.scope` | manual | |
| `shared.team.members` | manual | |

## 真机

总表（按机器分组、估时、依赖）：共享笔记「真机总表-11-16.md」。记录按六栏格式贴在下面，「通过」才放行。

```
（待填）
```

## 一、集成分支进 main 的条件（拼车负责）

只看这条集成分支装的东西（主会话 11-15 23:0x 定）：

| 必须 | 内容 | 状态 |
|---|---|---|
| 必须 | 搜索统一：`erp.scan.line.stocktake.liff-camera`、`erp.line.postback.erp_item` 两行真机记录「通过」，绑最终集成头 | 待 11-16 真机（A 组） |
| 必须 | 共用件连带回归：每个 manual 入口记「通过 / 不通过 / 没测到（原因）」 | 待 11-16（A、C 组）；白天并 main 后重出全表 |
| 必须 | 收款日期：NLTTEST 原生对照通过（作者判定）；LINE / 网页改日期；no-period 核点 | 待 11-16（A、B 组） |
| 必须 | 验收第 0 批首跑（验收窗口，E 组） | 待 11-16 |
| 必须 | 白天并最新 origin/main 后：全闸门（只许差已附记录的真机行）、候选离线 LANE EXIT 0、`run.py --changed` 受影响流程全过；Development checks 绿 | 并 main 前的结果见上表 |
| 可摘除 | page-company：三星一行 + 它的共用件入口；不过或来不及就重建集成分支跳过它（不用 revert） | 各组最后做 |

CC 桌面版 1.12.22 那 12 行、A 线两行（月份代录、Cowork 撤回）**不是**本分支进 main 的条件（主会话确认），它们的分支走合入队列。

## 二、下一次部署的条件（主会话定是否部署；按届时 main 上的全部内容算）

全套流程在发布提交上只跑一次 `run.py --all --post-status`；`deploy.sh --dry-run` 全过（流程闸、租户配置、LIFF、站外设置、容量检查）。
下表按 11-15 23:1x 的分支状态列，**缺的如实标**；进 main 的顺序与时间由合入队列与主会话定。共用件命中按 `check-impact.py --files <分支相对 main 的文件>` 算。

| 分支（头） | 进 main | 要过的（真机行 / 演练 / 只读核对） | 现状 |
|---|---|---|---|
| 本集成分支（搜索统一、收款日期、no-period、验收第 0 批、page-company 可摘除） | 拼车 | 见上一节 | 待 11-16 |
| `pearnly/cc-1122-server`（e97f3b3b2，动共用机器通道 cc-desktop） | 合入队列 | 共用件 cc-desktop 的 8 个手工入口（erp.cc.connect、erp.cc.masters、erp.cc.stock-void、cowork.cc.channel、erp.cc.companies、cc.mrerp-login、cc.desktop.bridge、cowork.cc-chat.push）；CC 1.12.22 真机 12 行（服务端先上线、旧 CC 不受影响那几行是部署前必须的：⑫ 领单回复多 requests_waiting 不影响 1.12.21） | 待 11-16（D 组）；**缺** |
| `pearnly/cc-1122-desktop`（1c65aff15） | 不随服务端部署 | 服务端上线后另发 staging → prod（prod 等主会话点头） | 不挡部署 |
| MR.ERP 三条：第 3 批 `company-onboarding-3-307a0a` → `mrerp-ledger-dialog`（a1b09740d）→ `mrerp-catalog-pick`（d58919b6c）（改 Express 主档落库与 Cowork → MR.ERP 写法，命中共用件 express-rules） | 合入队列（今晚） | ① 升级演练：旧代码 + 线上 testerp 重导数据 → 同步落定 → 新代码迁移 v1_30 / v1_31 → 再同步，主档改动 0、修改记录不增、选择框照常（F 组）；② T9–T11 真浏览器 / 真对话（F 组）；③ express-rules 的 12 个手工入口（erp.cc.pos-day、erp.cc.pos-refund、cowork.cc.channel、cowork.mrerp.push、cowork.companies、cowork.connections、cowork.history、cowork.cc-chat.push、cowork.cc.troubleshooting、cowork.line.item-questions、erp.trade.line-stock、erp.place.header）——**总表还没排**，要作者或主会话定由谁测；④ 生产只读核对（写账窗口清单里的「开关前」那项）；开关 `MRERP_LEDGER_ENABLED` 不随部署打开 | ①② 待 11-16；③④ **缺** |
| `pearnly/admin-all-features`（7ec1d4600，管理员自动拥有全部已开通功能） | 合入队列 | 共用件未命中；部署后逐个正式租户只读核：管理员能看到已开通的全部功能、非管理员不变 | **缺**（未排） |
| `pearnly/cc-guided-core`（909971dbb） | 合入队列 | 共用件未命中；引导式窗口的真机清单未给 | **缺**（待引导式窗口） |
| `pearnly/capacity-maintenance-transient`（7c47c996e，开户容量检查不把临时维护容器算常驻） | 合入队列 | `deploy.sh --dry-run` 容量检查通过；生产内存余量约 300 MiB，本批不能新增 ≥300 MiB 常驻进程 | 部署预演时核 |
| `pearnly/control-frontend-source`（b5b0d6393） | 合入队列 | 共用件未命中；控制面页面打开冒烟 | 部署后核 |
| `pearnly/cowork-withdraw-push`（0be7bcb07） | 合入队列 | Cowork 撤回 1 行（D 组 ⑫；第 ① 步要主会话批让测试机1 的 CC 暂时不领单） | 待 11-16；**要批** |
| `pearnly/cc-operator-other-months`（86d0240c9，命中共用件 assistant） | 合入队列 | 月份代录 ①–④（C 组，真模型约 0.5 美元）；⑤ 没批记没测到；assistant 的 8 个手工入口（control / shared / cowork / cc.assistant、cc.operator.thread、erp.cc.companies、cowork.cc-chat.push、cowork.cc.troubleshooting）——**总表还没排** | 部分待 11-16；**缺** |
| `pearnly/cc-approval-cards-7865fe`（f09af0041） | 合入队列（rows.toml 待作者答复） | 共用件未命中；作者的真机清单未给 | **缺** |

注：`check-product-map.py --batch --files` 对上面每条都报「真机单 0 项」，与作者自己列的真机行不符（如 Cowork 撤回），**不作依据**，真机行以作者清单为准；这个差异另报主会话。

## 部署预演与回滚

部署由主会话执行，步骤同 [11-15 上线车](2026-11-14-release-train.md)「部署预演步骤」。本车是否有新增 DocType / 补丁：collection-date 给 ERP Collection 加一个隐藏字段 `date_chosen`（只增不删，回旧镜像不需要反向迁移）；其余待分支到齐后核。
回滚：`scripts/production/deploy.sh --tag release-20261115-1126`。

## 集成记录

| 时间 | 动作 | 头 |
|---|---|---|
| 11-15 21:4x | 从 origin/main 0c87836b7 开分支；快进到 master-search-unify a15f8c1cf；合 acceptance-collection | 8d415ba14 |
| 11-15 21:5x | 合 collection-date 1fe229338（无冲突） | 52105b5cc |
| 11-15 22:4x | 合 collection-date-no-period 9d9f0c109（无冲突；收款三组离线测试 OK） | 79e38eccc |
| 11-15 22:5x | 合 page-company d9207ed4c（无冲突；带进 main 到 9c1e783ad；作者已过全闸门、候选离线、受影响流程 17 条 56/56） | 7a2e73226 |
