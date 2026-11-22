# 需求影响评估：记住的选择在用时按当前判据复核（不合格当没记住、重新问）

分支 `pearnly/remembered-choice-recheck`，基线 origin/main `f095336cf`。状态：评估，待主会话审定。

## 0. 需求原文与理解

- 主会话 11-22 派：1.12.23 窗口真机在 69EXP\TEST 发现，11-19 记住的存货科目组 P002 在 main `0c892923c` 收紧判据（存货、销货成本两个科目都要配）后已经不合格。
  但 `stock_acc_group.new_item_group` 只要见到记住的组（`stock_acccod`）就直接用、不再核，新建存货写账停在 `STOCK_IN_FAILED`。
  要求按类修：凡「记住的选择」在用时已不满足当前判据，一律当没记住、重新问（引导卡 / 开始前卡 / LINE 同一套），不静默用，也不静默换。
  先只读算生产影响。
- 我的理解：服务端在用记住的值之前，先对照这份账套当前上报的名单（心跳 `reported_*`）；不在名单里就当没记住，走这个入口「没记住」时本来的路
  （人在场的入口出题；不问的口径照旧落它的默认）。不改记住的数据本身，人重新选后覆盖那一行。
- 不在范围：判据本身（CC 桌面版 `catalog_probe._fits_inventory`）不改；「没记住时」各入口问不问的口径不改（主会话 11-17 定 C）；不做定时清理。

## 1. 现有同类与复用（全仓盘点结论）

写账规则那一类（`pearnly_core.express_rules.push.choices.resolve`：对方档、存货对照、进项 / 收入科目、现赊、抵进项、服务税口径、部门、仓、POS 收款、
退货科目、A7），以及账套选择、公司规则、代录子菜单、MR.ERP 菜单、库存单单据簿 / 存货对照，**用之前都已经对照当前名单**，不合格就当没记住。
ERP `express/choices.py` 文件头也写明了这条口径。

**没复核的（本次修）：**

| # | 记住的选择 | 存处 | 使用处 | 现在不合格时 |
| --- | --- | --- | --- | --- |
| 1 | 存货科目组（共用判定） | 端点 config / 载荷 `stock_acccod` | `express_rules/push/stock_acc_group.py` `resolve_stock_acc_group`、`new_item_group`：有值直接用 | 静默用 → CC 停在 `STOCK_IN_FAILED`（或 `STOCK_ACC_GROUP_UNFIT`） |
| 2 | 存货科目组（Cowork） | 连接 `stock_acc_groups`（按账套） | `push/application/service.py` `_apply_stock_group`（推送、开始前判定、自动推判档三处）；`stock_groups.view` → 录入页、`folder_intake/rules._stock_ready` | 同上；Cowork 的停下出口指向 `precheck:stock_group`，但开始前判定仍套用旧值、不出题，**回不去** |
| 3 | 存货科目组（ERP） | `ERP Express Choice` kind `stock_group` | `express/prepare.py` `_config`；`express/stock_entry.py` `_with_group`（库存单） | 同上；ERP 只有 `STOCK_ACC_GROUP_UNFIT` 会事后重新问，`STOCK_IN_FAILED` 只能重试 |
| 4 | ERP 库存单单别科目 | `ERP Express Choice` kind `account`、subject `stock:<单别>` | `express/stock_entry.py` 342-371：记过就直接下发，只有没记过时才核科目表 | CC 停在 `ACCOUNT_NOT_POSTABLE`，ERP 不重新问 |

修法（一处判据、各处同用）：

- 共用层纯函数 `stock_groups.still_fit(acccod, groups)`：候选未知（这份账套还没读到）→ 照旧用；读到了、组不在合格名单 → 回空（当没记住）。
  `stock_groups.chosen` / `view`、`stock_acc_group.resolve_stock_acc_group` / `new_item_group`（端点 config 和载荷里的 `stock_acccod`）都过它。
  Cowork `_apply_stock_group`、ERP `_config` / `_with_group` 也只在合格时放进 config。然后各入口原有的「没定组」路径照常接手：
  合格的只有 1 个就自动用；几个就出题（开始前卡 / 引导卡 / LINE 同一道 `stock_group` 题）；一个都没有就停在「缺存货科目组」。
- ERP 库存单科目：这份账套读到了科目表、记住的科目却不在表里 → 当没记过，走现有的「默认科目不在表里 → 出 `account` 题」。

**复核了、不合格时走「没记住」那条路，不算静默换（不改）：** 非引导入口的收入科目（没记住时本来就落连接默认，主会话 11-17 定 C）、
任务默认收入科目、部门（10-08 定不问，由单据簿默认）、自动推的默认仓（没人在场，本来就写主仓）、没有 13 位税号的对方档（本来就交 CC 按名称认）。
这几项不合格时的表现和从没记过完全一样，符合「当没记住」。

**CC 桌面版侧（要改 CC，先问主会话，排 1.12.23 之后）：**

- a. `stock_template.pick` 只核存货科目，不核销货成本科目，判据比心跳上报的 `_fits_inventory` 松。补上 `stock_sale.stock_accounts` 后，
  不合格组会改报 `STOCK_ACC_GROUP_UNFIT`（ERP / Cowork 都已把它接到重新问），不再报 `STOCK_IN_FAILED`。服务端修好后，只剩旧版 CC（1.12.22 以前）上报的过时 fit 会走到这里。
- b. 本机 `default_depcod`（`worker.py:342`，载荷没带部门时用）不核部门表，静默写入。
- c. 代录子菜单：交到 CC 时记住的菜单文字已不在候选里，CC 会按付款方式自己挑唯一一项（`express_operator/menu.py:114-129`），这是静默换。

**另记：** `book_questions.py` 的单位记忆还没有调用方，接线时要核当前选项，本次不动。

## 2. Frappe 原生能力

不涉及：这是写账准备的服务端判据，没有界面和 DocType 变更。

## 3. 按类通用

- 类别：「所有记住的选择 × 所有写账入口」。服务端已复核的不动；上表 4 项用同一个纯函数修。
- 覆盖的入口：ERP（网页、CC 直写、库存单）、Cowork（推送、开始前卡、LINE 保存、自动推判档、文件夹录入规则）。代录走同一个写账准备，一并覆盖。

## 4. 上下游数据

- Express：不改写入内容；只是不把不合格的组 / 科目交给 CC。
- CC 桌面版：载荷不变，不升版本。上面 a–c 三项另排。
- 记住的数据不删、不改，人重新选后覆盖同一行（ERP `remember`、Cowork `choose_stock_acc_group`）。

## 5. 隔离与权限

不涉及：只读本公司 / 本连接自己记住的值和这份账套的上报资料，不新增读写面。

## 6. 入口

各入口沿用现有的出题方式（开始前卡、引导卡、LINE 选择卡、ERP 单据上的「要人选」），不新增入口和卡片。

## 7. 共用件与连带回归

- 改到共用的写账判据（`pearnly_core/express_rules`），交付前贴 `scripts/check-impact.py` 的输出，逐入口跑受影响流程：
  `scripts/flows/cc_operator_stop_choice.py`（存货科目组那几格），以及 ERP / Cowork 开始前判定的流程。
- 单测：每处各补「记住的组已不在合格名单 → 出题」「候选未知 → 照旧用」两条；ERP 库存单科目一条。

## 8. 存储与成本

不涉及：没有新增数据、外部调用或月费。

## 9. 删除、作废、追溯

不涉及。

## 10. 文案与官方 UI

不新增文案，沿用现有题目。

## 11. 测试与验收

- 单测 + 全闸门 + 候选离线 + 受影响流程。
- 真机：69EXP\TEST 用 P002 原样复现（1.12.23 窗口的场景），修后应先出选组卡、选合格组后写成。这项要排测试机 / 测试租户，等主会话安排。
- 生产只读统计（脚本 `~/pearnly-devtools/prod-ro-1122/remembered-group.py`），看现在有几条记住的组 / 库存单科目已不合格。

## 12. 要主会话定的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | CC 桌面版 a–c 三项是否本批做 | a 跟 1.12.23 之后的下一版做（让 CC 判据和心跳一致）；b、c 一并做 | 不做 a：旧版 CC 上报过时 fit 时仍停 `STOCK_IN_FAILED`；不做 b、c：仍有静默用 / 静默换 |
| 2 | 生产只读脚本并进明早那批 | 并进 | — |
| 3 | 不合格时要不要顺手删掉记住的那行 | 不删，重新选后覆盖 | 删了会丢「谁选的、何时选的」 |

## 13. 工作量与排期

1. 服务端 4 项 + 单测：写代码约 1 小时；全闸门 + 候选离线约 1 小时机器时间。
2. 受影响流程 + 真机复现：等测试机排期。
3. CC 桌面版 a–c：等主会话定。
