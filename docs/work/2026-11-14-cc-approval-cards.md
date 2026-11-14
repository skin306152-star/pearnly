# CC 对话里删除、结束、撤回、立即运行先出「允许」卡片（2026-11-14）

用户 11-12 决定（全覆盖总表第 4 条）：CC 对话里「立即运行定时任务」与删除类（删识别单据、删回读、删对账、删报税草稿、结束盘点等）
各先出一张「允许 / 拒绝」卡片，卡上列清这一次动的是哪几条，只这一次确认；网页上用户自己点的地方不变。做法按 CC 引导式总设计
12 / 12.1 节：用现成的允许卡（工具声明 `approval: True` + `ask` + `preview`，共用层 `pearnly_core/assistant/tools.py` 的
`approval_view` 出卡、`toolrun.decide` 放行），不改写入计划、不碰前端与题卡。

## 盘点（origin/main 12e71d3a9）

| 工具 | 动作 | 之前 | 这次 |
| --- | --- | --- | --- |
| `cowork_delete_documents` | 删识别单据与原件 | 无任何确认（说明里还写「点名了就直接删、不另确认」） | 加卡；说明改为「卡片上确认，不再另问」 |
| `cowork_express_readback_delete` | 删回读与四份工作簿 | 无 | 加卡 |
| `cowork_recon_delete` | 删对账任务 | 无 | 加卡 |
| `cowork_tax_draft_delete` | 删报税草稿 | 无 | 加卡 |
| `cowork_stocktake_close` | 结束盘点（之后只读） | 无 | 加卡 |
| `cowork_stocktake_delete` | 删盘点 | 无 | 加卡 |
| `cowork_run_folder_task` | 立即运行定时任务（识别扣费并推送） | 无 | 加卡 |
| `erp_stocktake_close` | 结束盘点（锁定结果） | 无 | 加卡 |
| `erp_collection_withdraw` | 撤回还没被 CC 桌面版取走的收款 | 无 | 加卡 |
| `cowork_delete_folder_tasks`、`cowork_company_note_remove` / `choice_remove`、`erp_company_note_remove` | 删除 | 已有允许卡 | 不动 |
| `cowork_push_documents`、`erp_push_documents`、`erp_collection_push` | 写账 | 写入计划（复述 + 点头） | 不动 |

不在这批（只记录）：`express_operator_control` 的 redo（代录重录，总设计定随引导式 P0 并卡）；`cowork_stocktake_count` 的
recount（清掉该物料之前的计数再记，属修改）；`cowork_recon_start` / `cowork_recon_answer`（扫描件按页识别扣费，工具说明已要求
先说明再开始）；`erp_stocktake_adjust` 只生成草稿。

### 网页上也没有任何确认的（只记录，这批不改网页）

- Cowork 文件夹定时任务「立即运行」：`public/desk/intake-folders.js:128-129` 点了直接调 `folder_intake.api.run_now`。
- ERP 收款「撤回」：`public/desk/collections.js:82`（详情弹窗）与 `:144`（编辑弹窗）点了直接调 `api_collection_withdraw`。
- Cowork 回读删除、报税草稿删除：网页上没有入口（只有 CC 工具）。
- 其余（录入删除、对账删除、盘点结束 / 删除、ERP 盘点结束）网页上都有 `frappe.confirm`。

## 卡片上列什么（`preview`，只读）

| 工具 | 问句 | 每行 |
| --- | --- | --- |
| 删单据 | Delete these documents? | 单号（没有就文件名）· 对方（进项卖方 / 销项买方）· 金额；看不到的照编号 |
| 删回读 | Delete this readback? | 账套 · 月份 |
| 删对账 | Delete these reconciliations? | 对账标题 |
| 删报税草稿 | Delete these tax drafts? | 公司 分店 · ภ.พ.30 或 ภ.ง.ด.3 / ภ.ง.ด.53 · 月份 |
| 结束盘点（两产品） | Finish this stock count? | 盘点名；已盘点 N；未盘点 M |
| 删盘点 | Delete these stock counts? | 盘点名 |
| 立即运行 | Run this scheduled task now? | 任务名、账套、方向、文件夹；「新文件将识别（扣费）并推送」 |
| 撤回收款 | Withdraw this collection? | 客户、发票、金额 |

执行的就是卡片预览时存下的那份参数（执行记录 `tool_input`，模型改不了）；参数不同的新调用是一张新卡。拒绝 / 关卡片不执行（共用层）。
读名字用各业务已有的只读入口，新加 4 个只读小函数：`recon_center.api.titles`、`readback.service.label`、`tax.service.labels`、
`stocktake.upload.summaries`（各一次查询，按工作空间 / 连接可见范围）。

## 问句下面那一行事实（`note`，主会话 11-14 转用户口径：允许卡照 Claude 桌面应用的权限确认框）

前端（引导式总设计分支 `pearnly/cc-guided-design-39cce4` 05af2139f）的新版式是：问句 → 一行事实 → 带边框的内容块（最多 5 行 +
「还有 N 项」）→ 拒绝 / 允许；那一行读 `note`。服务端这一半在本分支：工具声明 `"note": "<函数路径>"`，共用层 `approval_view`
有事实才放进 `note`（回空、出错、没声明都不给，那一行不出；出错记日志、卡片照出）。只写名字，不写说明句、不写内部编号，没有新词条。

| 工具 | 那一行 |
| --- | --- |
| 立即运行 | 公司名 · 账套名（会识别并推进的那本） |
| 删单据 | 这几张要推往的账套名（去重；没定去向的不算） |
| 删回读 | 账套名 |
| 删报税草稿 | 公司名（去重） |
| ERP 结束盘点 | 盘点所在仓的公司名 |
| ERP 撤回收款 | 公司名 · 账套名 |
| 删对账、Cowork 删盘点 / 结束盘点 | 不给（工作空间里不分公司） |

允许卡永远只有「拒绝 / 允许」、只管这一次，不做始终允许 / 记住允许。
注意：设计分支的 `frontend/assistant/src/lib/managed-agents/api.ts`（`confirm` 的返回）目前只取 status / ask / preview，没带 `note`；
重开对话时卡片的恢复路径也要带上，这一半在前端分支。

## 真页面实测（本机实验栈 `pearnly-approval-lab`，假智能体，11-14）

新流程两条（登记在 `tests/flows/registry.toml`，产品地图对应行已挂上）：

- `cowork.cc.approval-cards`（`scripts/flows/cc_approval_cards.py`）：说「删掉这两张」→ 卡上两行单号 · 对方 · 金额 → 拒绝 → 三张都在、
  模型收到 denied → 再说一次 → 允许 → 只删这两张、第三张还在；立即运行 → 卡上任务名、账套、方向、文件夹与「新文件将识别（扣费）并推送」
  → 允许 → 运行请求记 1 次；再把账号语言换成泰 / 英 / 日，各出一张立即运行卡与一张删单据卡（挂着时截图），问句与事实行是该语言、
  不回退英文、不横向溢出，都点拒绝（运行请求仍是 1 次、第三张仍在）。
- `erp.cc.approval-withdraw`（`scripts/flows/erp_cc_approval_withdraw.py`）：卡上客户、发票、金额 → 拒绝 → 仍是草稿 → 再说一次 → 允许 → 已撤回。

结果：电脑与手机宽度各一遍，4/4 格通过（Cowork 155 秒、ERP 75 秒），页面无报错、Error Log 无新增。
截图：`~/pearnly-devtools/approval-shots/<流程>-new-<desktop|mobile>/`（删单据卡、允许后、立即运行卡中泰英日、删单据卡泰英日、撤回卡、撤回后）。
截图里的卡片仍是旧版式（问句 + 改动 + 按钮，没有事实行、内容块无边框）属正常：新版式在引导式总设计分支，合入后才有。

## 测试

- `tests/test_cc_approval_cards.py`（新）：产品源码里真实的 9 条声明 + 共用层真实 `tools` / `toolrun`，逐个工具：没点允许不执行、
  允许后执行一次且参数就是预览那份、拒绝不执行并交回 denied；假智能体流程「删掉这两张」→ 拒绝 → 都在 → 再来 → 允许 → 只删这两张。
- `test_cowork_assistant_tools.ApprovalCardTests`：各卡片列的是名字（单号 · 对方 · 金额、标题、账套 · 月份、已盘 / 未盘）。
- `test_erp_cc_stocktake`、`test_erp_collection_cc`：ERP 两张卡片的内容、出卡只读；声明断言随改动更新（结束、撤回要卡，其余不要；
  ERP 与 Cowork 同名工具要不要卡一致）。
- 黄金样本 `tests/fixtures/assistant/cowork-push-tools-golden.json` 按新声明重写（只 `tools` 一项变了）。

## 文案（`python3 scripts/check-copy.py --report`：新增 10 · 改动 0 · 拦截 0）

| 来源 | 中文 | English | ไทย | 日本語 |
| --- | --- | --- | --- | --- |
| Cowork | 确定要删除以下单据吗？ | Delete these documents? | ลบเอกสารเหล่านี้หรือไม่ | これらの書類を削除しますか？ |
| Cowork | 确定要删除此回读吗？ | Delete this readback? | ลบการอ่านข้อมูลกลับนี้หรือไม่ | この読み戻しを削除しますか？ |
| Cowork | 确定要删除以下对账任务吗？ | Delete these reconciliations? | ลบงานกระทบยอดเหล่านี้หรือไม่ | これらの照合を削除しますか？ |
| Cowork | 确定要删除以下申报草稿吗？ | Delete these tax drafts? | ลบร่างแบบภาษีเหล่านี้หรือไม่ | これらの申告下書きを削除しますか？ |
| Cowork / ERP | 确定要结束此盘点吗？ | Finish this stock count? | ปิดการตรวจนับนี้หรือไม่ | この棚卸を終了しますか？ |
| Cowork | 确定要删除以下盘点吗？ | Delete these stock counts? | ลบการตรวจนับเหล่านี้หรือไม่ | これらの棚卸を削除しますか？ |
| Cowork | 确定要立即运行此定时任务吗？ | Run this scheduled task now? | เรียกใช้งานตามกำหนดเวลานี้ตอนนี้หรือไม่ | この定期タスクを今すぐ実行しますか？ |
| Cowork | 新文件将识别（扣费）并推送 | New files are recognized (charged) and pushed | ไฟล์ใหม่จะถูกอ่าน (มีค่าบริการ) และส่งเข้าระบบ | 新しいファイルを認識（課金）して送信します |
| ERP | 确定要撤回此收款吗？ | Withdraw this collection? | ถอนรายการรับชำระนี้หรือไม่ | この入金を取り下げますか？ |

卡片上其余字段名全部复用已有词条（任务名称、账套、方向、文件夹、已盘点、未盘点、库存盘点、客户、发票、金额、ภ.พ.30 等）。

## 共用件影响清单（`scripts/check-impact.py`）

| 入口 | 结果 |
| --- | --- |
| erp.cc.stocktake（offline） | 通过：`test_erp_cc_stocktake` 12 条 |
| erp.cc.collection（offline） | 通过：`test_erp_collection_cc` 25 条 |
| cowork.assistant / cowork.cc-chat.push | 通过（离线）：`test_cowork_assistant_tools` 264 条、黄金样本、`test_cc_approval_cards` 4 条；真模型对话没测到（这批不动测试机） |
| cowork.express.push、cowork.ocr、cowork.execution、cowork.history、cowork.cc.mrerp-setup、cowork.exports、cowork.tax-documents、cowork.browser-text | 没测到：本次只给工具声明加卡与只读预览、新增只读查询，推送、识别、导出、上传路径未改；离线全套随闸门跑 |

## 限制

- 测试机没动；真页面实测在本机实验栈（见上），用的是假智能体，没跑真模型对话。
- 网页「立即运行」与 ERP 收款「撤回」仍没有确认（见上），这批不改网页。
