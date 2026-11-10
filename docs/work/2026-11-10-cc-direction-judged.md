# 销售票在代录资料里被标成「采购」（A 写账主线）

状态：本次范围完成，待合入。分支 `pearnly/cc-direction-judged`，基线 origin/main c9e9eedf9，工作树 `.claude/worktrees/a-line-direction`。
来源：第 1 批身份真模型场景 Cowork 第 6 次（11-09）：直写放的 4 张本公司开出的销售票截图，在代录资料区显示为「采购 4」。

## 根因

票上的方向只在识别页面声明了才有（`direction` 字段）；截图识别没声明。推送按「声明的 → 工作空间税号 → 账套目录里的税号」判出销项
（`contracts.push.resolve_account_direction`），但列表与资料区只看声明，没声明一律当进项：

- CC 单据列表 `tools._with_page`（方向列、按方向筛选）；
- 代录资料区分组 `operator_source.sources` / 开录前落分组 `_settle` / 拖动记键 `regroup`；
- 对方说买方还是卖方：代录重启状态 `status`、挡着的单 `_blocker`、上传卡 `uploads`、同组接着判的一张 `_same_group_document`、
  开始前卡片的 `party`。

方向判反的后果：资料区归错组（人可能照着拖成采购）、对方写成本公司自己、按对方记的分组键记错。推送本身判对，所以已写进去的不受影响。

## 修法（按类）

`tools.judged_direction`（票上声明的优先；没声明按推送同一套税号判据）+ `tools.direction_anchors`（一次列表 / 一批单内缓存工作空间与
账套目录税号）。上面各处都改调它；票上没有税号的不去读账套目录。

## 验证

| 版本 | 场景 | 结果 |
|---|---|---|
| 旧代码 | `test_cowork_assistant_tools` ListAndRuns「没声明方向按推送判」、`test_cowork_companies` OperatorSource「销售票按销售命名分组」 | 红（列表方向为空；对方写成卖方本公司） |
| bd03d0cee | 同上 + 受影响单测文件 | 过 |

| c2e56e57d | gate（development.yml 全部步骤） | 只红 test_express_operator_code_cc（替身缺新函数，c2e56e57d 修；该测试单跑过） |
| c2e56e57d | 候选离线（生产镜像 fb1037ad） | 107 组，LANE EXIT 0 |
| c2e56e57d | 受影响流程（parity-lab，`--changed`） | 3 条流程 × 两套数据 6/6 过；cowork.cc.sidebar-modes old/desktop 首跑页面步骤 900 秒超时、补跑 19 秒过（环境） |
