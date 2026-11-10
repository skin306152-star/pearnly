# 测试机 11-09 发现的 CC 直写 3 个问题（A 写账主线）

状态：本次范围完成，待合入。分支 `pearnly/cc-precheck-three-fixes`，基线 origin/main c9e9eedf9，工作树 `.claude/worktrees/a-line-work`。
来源：发布窗口 11-09 测试机验收 ②（Cowork 直写销货 TST691031-01，截图 `~/pearnly-devtools/release-1108/shots/12–15`，
推送记录与读回 `~/pearnly-devtools/release-1108/tst691031-01-*`）。

## 现象与根因

| # | 现象 | 根因 | 修法（按类） |
|---|---|---|---|
| ① | 第 1 项「录入设置」选了按服务录入，第 2 项还问「库存出库从哪个仓库」 | 卡片按预选「走库存」重判一次，多出来的出库仓、存货对照与入账方式并排列成无条件的题；`ask_user` 不能看前一题的答案 | 共用契约 `write_precheck.contracts`：项可带 `when`（另一项答成某值才成立），不成立时不留单、答案不展开；Cowork 预检给「按预选重判才多出来的项」带 `when`；`ask_user` 加 `show_if`，提问卡随前面的回答收起 / 放出、计数只算要问的题，条件不成立的题交回 `not_asked`；待处理框自己出题时按 `when` 排序并带 `show_if` |
| ② | 品名「ค่าบริการหน้าร้าน 7-11」与 Express 物料（码 ค่าบริการ）一字不差，CC 说「Express 中尚无商品资料」推荐新建 | 不是规范化、括号或主档没读全：主档里有这件，是**非库存品**。第一遍判定（没声明过账类型）已按同名定成不动库存；卡片预选「走库存」后的第二遍只拿库存品比同名，非库存品同名当没有 | 共用规则 `express_rules.push.choices.resolve`：整批走库存（预选或公司选的）而这一行与 Express 非库存品同名 → 这一行就是那件非库存品（不动库存、不问存货、不问出库仓）；行上逐行声明了存货的照旧问。Cowork 预检：每一行都已按行定下时不再整张问「走库存 / 记费用」 |
| ③ | CC 说「收入科目 41-01-00-00（账套默认）」，实际写入 42-10-00-00 | CC 桌面版回执 `meta.revenue` 已带回实际记的收入（照物料科目组），给 CC 的 `written` 只照冻结载荷的 `items_account` | `push.express.pushed.written` 收回执：销货有 `meta.revenue` 时 `revenue`=[{account, amount}] 照回执、单一科目给 `revenue_account`，与载荷不同时 `account_from=item_group`；`_entered` 把回执交进去；给模型的说明：收入科目与金额只按 `written.revenue` 说，推前 `planned` 对销货只是默认、不承诺 |

ERP 直写的推送结果只回单号、不说科目，③ 不涉及；① ② 的共用规则 ERP 同样生效（`express_rules`、`write_precheck` 共用）。

## 验证

| 版本 | 场景 / 命令 | 结果 |
|---|---|---|
| 旧代码 c9e9eedf9 | 新加的测试：`test_cowork_express_choices` NonStockSameName、`test_cowork_write_precheck` 两条、`test_write_precheck` Conditional、`test_assistant` show_if、`test_cc_ask` show_if（纯函数与卡片计数）、`test_cowork_account_suggest` 回执收入 | 全红（复现） |
| 72b04675d | 上述测试 + 受影响单测文件 | 全过 |
| 72b04675d | gate（development.yml 全部步骤） | 2 项红：回放 G07 断言旧行为（问存货）、工具说明黄金样本——都是本改动的预期结果 |
| 9b25b9136 | G07 改断言为「不问、入账方式对上」（Express 里这张就是挂非库存品 SV1）；黄金样本 `--write`，只多出本次的说明文字 | 两项过；gate 重跑见下 |

## 共用件连带回归

`scripts/check-impact.py` 命中 express-rules、write-precheck、assistant。结果在合入前补：候选离线、受影响流程。

## 不在本次

- 复核卡（第 2 批）；ERP 销售单身份（第 4 批）。
- 真模型场景验 ③：随第 1 批身份分支的真跑一起（预算由主会话定）。
