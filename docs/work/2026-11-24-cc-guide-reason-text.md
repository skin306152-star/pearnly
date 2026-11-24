# CC 引导没办成时说原因（cc-guide-reason-text，11-24，P3）

主会话 11-24 批准的 (a)：引导确定出错时结果卡只写「未完成」，不说原因。

## 改了什么
- 共用原因表 `apps/pearnly_core/pearnly_core/assistant/guide/reasons.py`：原因码 → `guide.reason.<码>`（四语在 `pearnly_core/i18n/messages.tsv`）。单据源的 `reason_text` 改走它。
- 引擎两处兜底（`guide/run.py` 按单据的确定、表单的确定）：结果带 `reason_text`——产品 `frappe.throw` 的正式文案原样用（去标签、还原实体、截 300 字）→ 原因码表 → 通用一句「处理出错」。错误编号只进 Error Log，不上卡片；接住后清掉消息队列，回包不再重复弹那句。
- 补了原来没有文字的九个码：没有权限、未找到、不可用、无法写入、没有账套、期间不符、未选账套、不入账（人移到不入账）、处理出错。
- 写入计划没建成时整次的原因显示在逐张行上面（前端 `guide-review.tsx`）。
- 状态词（已入账 / 进行中 / 已跳过 / 未完成）一个没改；要不要统一成 LINE 回执口径的清单已发主会话，批前不动。
- 「不支持」那句（`guide.reason.unsupported`）在 cc-guide-mrerp-direct 分支，两条都进 main 后再给代录 × MR.ERP 写专门一句。

## 验证
- 离线：`test_cc_guide_run`（新：正式文案 / 码表 / 通用一句三种、卡上不出现 Error Log）、`test_cc_guide_documents`（新：结果行缺字的码补上、整次原因）。
- 页面流程（本机实验栈，换本分支代码并重建 CC 前端，电脑 + 手机 12 格全过）：`cowork.cc.guide.purchase`、`cowork.cc.guide.operator-sales`、`erp.cc.guide.purchase`、`erp.cc.guide.operator-purchase`、`erp.cc.guide.collection`、`cowork.cc.guide.recon-follow`。
- 没有页面流程走到「确定出错」那一支（实验栈里造不出产品拒收的确定），由单测守。

## 共用件连带回归

| 入口 | 结果 |
|---|---|
| `control.assistant` | 没测到（原因：fatcc.com 入口不在本机实验栈；本分支没改登录与入口，只改引导结果文字） |
| `shared.assistant` | 通过（上面 6 条流程都打开对话页、无页面报错） |
| `cowork.assistant` | 通过（Cowork 引导 3 条流程） |
| `cc.assistant` | 通过（同上，新建对话、发卡、结果卡） |
| `cc.operator.thread` | 通过（代录对话两条：`cowork.cc.guide.operator-sales`、`erp.cc.guide.operator-purchase`） |
| `erp.cc.companies` | 通过（ERP 引导 3 条流程按公司开对话） |
| `erp.cc.chat-push` | 通过（离线同名检查，闸门） |
| `erp.cc.stocktake` | 通过（离线同名检查，闸门） |
| `cowork.cc-chat.push` | 没测到（原因：打字推送那条路本分支没改；引导结果卡由上面流程覆盖） |
| `cowork.cc.troubleshooting` | 没测到（原因：同上，没改待处理与自检） |
| `erp.cc.collection` | 通过（离线 + 页面流程 `erp.cc.guide.collection`） |
| `erp.page.collections` | 没测到（原因：网页收款页本分支没改，只因共用词条表被列入） |
| `cc.operator.start-card` | 通过（离线同名检查 + 代录两条流程） |

## 代码审查（code-review medium）

| 发现 | 真问题 | 修了 | 耗时 |
|---|---|---|---|
| 5 | 2（兜底后没清消息队列，回包重复一句；HTML 实体没还原） | 2 | 约 10 分钟 |

其余：产品自带说明的错误码（如收款规则错误）落到兜底时只得通用一句——各产品 confirm 现在都自己接住规则错误，记下待核；框架内部英文校验文案按任务书口径原样显示；整次原因行没有前端测试。
