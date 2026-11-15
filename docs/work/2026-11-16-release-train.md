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
| 共用件连带回归 | 8d415ba14：命中 `erp-masters-selection`，14 个入口，见下 | `check-impact.py --markdown` |
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

## 上线前置

- 上面要求的真机行在最终集成头上「通过」。
- 全套 `--all --post-status` 在要部署的提交上通过（头冻结后只跑一次）。
- collection-date 的 NLTTEST 原生对照通过。

## 部署预演与回滚

部署由主会话执行，步骤同 [11-15 上线车](2026-11-14-release-train.md)「部署预演步骤」。本车是否有新增 DocType / 补丁：collection-date 给 ERP Collection 加一个隐藏字段 `date_chosen`（只增不删，回旧镜像不需要反向迁移）；其余待分支到齐后核。
回滚：`scripts/production/deploy.sh --tag release-20261115-1126`。

## 集成记录

| 时间 | 动作 | 头 |
|---|---|---|
| 11-15 21:4x | 从 origin/main 0c87836b7 开分支；快进到 master-search-unify a15f8c1cf；合 acceptance-collection | 8d415ba14 |
| 11-15 21:5x | 合 collection-date 1fe229338（无冲突） | 52105b5cc |
| 11-15 22:4x | 合 collection-date-no-period 9d9f0c109（无冲突；收款三组离线测试 OK） | 79e38eccc |
