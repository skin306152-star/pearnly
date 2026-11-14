# 代录会话：选中就上传、发送后才识别（2026-11-14，主会话 local_6e6aa7b0 派）

## 用户原话与口径
- 11-14：「选中就上传当初是我问为什么上传很慢，要转圈半天才能发送，而不是上传就识别，愚蠢的设计」「难道上传还未发送就开始识别了吗」。
- 要的是：选中文件立刻上传（点发送不再等上传）；识别等点了发送才开始；没发送就移除的不识别、不扣费。
- 11-06 的「选中就上传」（`docs/work/2026-11-06-operator-attach-upload.md`）把「上传」和「交识别」做成了一步，这是误解。

## 改前现状（只读查清，已报主会话）
- 上传口 `assistant/files._upload` 一收到文件就调 `_operator_take` → `express_operator.uploads.take` → 产品 `intake` 钩子：
  Cowork（`operator_source.intake`）存原件（`files.create`，生产走甲骨文，约 8 秒）+ `ocr.submit`；
  ERP（`express/operator.intake` → `intake.receive_chat`）存原件、建收单记录、排识别。扣费在识别跑完后（Cowork `settle`、ERP 识别带计费参考号）。
- 拖入 / 粘贴 / 「+」/ 从文件夹选，电脑与手机，Cowork 与 ERP：同一个附件适配器、同一个上传口，行为相同。直写会话不受影响（发出后 CC 调工具才识别）。
- 没发送就移除：识别结果进了这家公司的资料、原件留着、照常扣费、任务上留一行没有时刻的上传行、本站留存一份；前端移除时不告诉服务端。
- 同类：直写会话、LINE（发出即发送）、录入工作台（点上传就是要识别）、CC 桌面版文件夹定时都不是「没确认就先识别」，只有代录输入框这一处。

## 改法（Cowork / ERP、各入口同一套）
| 处 | 改成 | 文件 |
|---|---|---|
| 上传口 | 只把文件放进对话、本站留一份，在任务上记一行「待发送」（`uploads.hold`：不存原件层、不交识别、不扣费、没有时刻）；回包只带 `task` | `assistant/files.py`、`express_operator/uploads.py`、`products.can_intake` |
| 发送 | `sessions.send` 认出消息带着的几份（`uploads.keys_in`），交给唯一入口 `files.commit(row, file_ids, via="send")`：记上时刻（上传卡片排在消息后面），提交后排后台任务 `files.start_sent`，以发消息的人、那时的界面语言读本站留存交产品 `intake`（存原件 + 排识别都在这里，发送不等）。见下「接缝」 | `assistant/sessions.py`、`assistant/files.py`、`uploads.commit` |
| 交识别 | `uploads.start`：按份加锁、锁里重读，只交已发出、还没交的；交不成记原因进新字段 `start_code`（`upload.deferred` / 产品的码 / `start_refused`），下次再来照样重交 | `uploads.py`、子表 JSON 加 `start_code` |
| 识别工具 | `files.taken`：已交过回原来的；已发出、后台还没交上的当场交（同一把锁，后台再来不重交）；没发出的不交 | `assistant/files.py`（Cowork `tools.py` 只改注释） |
| 卡片状态 | 已发出、还没交上：排队中；交不成：失败（带原因）；`task_uploads` 不再列还在输入框里的 | `shapes.unstarted`、`feed.upload_states`、`uploads.read` |
| 移除 | 前端移除还没发出的附件时调新接口 `assistant.api.drop_upload`：删掉那一行「待发送」，提交后删本站留存；已发出的、直写会话不动 | `assistant/api.py`、`files.drop`、`uploads.drop`、`attachments.ts` |
| 输入框 | 附件方块只显示上传中 / 失败；删掉 `attachment-recognition.ts`（不再轮询 `task_uploads`、不显示排队中 / 识别中） | `attachment.tsx`、`attachments.ts`、`api.ts` |

- 关页面丢下、没发出的：不识别、不扣费；任务上那一行「待发送」不进对话、不出卡片、不算任务里的上传，本站留存随删对话一起删（沿用 `files.forget`）。没有加定时清理（用户口径：不开定时任务）。
- `task_uploads` 接口页面不再调：登记进 `not_entries.toml`（待删候选，测试还在用）。
- 文案：无新增、无改动；输入框里不再出现「排队中 / 识别中」是去掉显示。新字段标签用已有词 Reason。

## 接缝：用户交出文件 → 存原件并交识别（本窗口负责，主会话 11-14 定）

规则一句话：**用户明确交出文件的那一刻才识别**——输入框是点发送，引导卡是在卡上提交。不许另写「上传即识别」的路。

| 项 | 约定 |
|---|---|
| 唯一入口 | `pearnly_core.assistant.files.commit(row, file_ids, via) -> list[str]`，**直写、代录两种会话通用**（主会话 11-14：引导式后续在直写会话的卡片上收转账凭证、银行流水、总账、销项税报表、POS 导出、盘点 Excel、预扣税票据）。`row`：本人的会话记录（`sessions.owned(session_id)`）；`file_ids`：上传口回的 `fileId`（`upl_…`）；`via`：`"send"`（输入框点发送）或 `"guide"`（引导卡上提交），别的值抛 `ValueError`。回这次认下的交出文件编号 |
| 它做什么 | 只认这段对话里用户上传、本站留了一份的（别的编号丢掉）；给每份记「已交出」（会话目录 `handed/<编号>` 空标记，随删对话删）。**直写会话到此为止**：不识别，引导引擎拿返回的编号把文件交给对应业务函数（文件内容按 `files.attachment(file_id)` 取）。**代录会话**另外：`express_operator.uploads.commit` 给还没交出的那几行记上此刻（进对话、出上传卡片），提交后排后台 `files.start_sent` → `uploads.start` 按份加锁读本站留存交产品 `intake`（存原件、排识别、之后扣费都从这里开始） |
| 幂等、可重入 | 同几份再交：标记已在、不重记时刻；代录里已交成识别的不再排，还没交上的照样排（`uploads.start` 锁里重读，只交一次）。识别工具 `files.taken` 等不及时当场交，也走 `uploads.start` |
| 谁调、何时调 | 输入框：`sessions.send` 消息发给引擎之后，按消息里带着的附件位置认编号（`files.ids_in`），`via="send"`，两种会话都调。引导卡：用户在卡上点提交、站点收下这次作答的同一个请求里（`via="guide"`），传卡上交的那几份的 `fileId`。前端不直接调识别，也不调 `express_operator.api.intake` |
| 交出之前 | 上传口 `assistant.api.upload` 只上传：本站留一份（两种会话），代录另在任务上记一行「待发送」（`uploads.hold`），不识别、不扣费。卡上 / 输入框里的文件显示上传状态（上传中 / 失败）即可 |
| 移除 / 丢弃 | 交出前移除：前端调 `assistant.api.drop_upload(session_id, file_id)`，**两种会话都可用**：没有「已交出」标记的删本站留存（提交后），代录另删「待发送」那一行；已交出的回 `dropped: false` 不动。引导卡上移除同样调它。没交出就关页面 / 卡片作废：什么都不处理，本站留存随删对话删（`files.forget`）。不加定时清理 |
| 上传卡片状态 | 代录交出后从代录事件源读（`express_operator.feed.upload_states`：已交出还没交上识别 = 排队中；交不成 = 失败带 `start_code`；交上了 = 产品 `uploads` 钩子现读）。`task_uploads` 只列已交出的，页面不再轮询它。直写会话没有上传卡片，处理结果由各业务函数自己回 |
| 跨边测试 | `tests/test_assistant.py`（`files.commit` via guide / send：两种会话认下才回、没留存的编号丢掉、代录才排识别、直写不排；`drop` 两种会话、已交出不删；`ids_in` 只认本对话的）、`tests/test_express_operator_code_cc.py`（`commit` 幂等、交出才记时刻、入口本身不识别）、`tests/test_cc_operator.py`（前端两种会话移除未发出的都调 `drop_upload`） |

引导式现状（`pearnly/guided-3-0cc915`，11-14 读）：第 1 期没有卡上交文件的步骤。选了业务只把占位字换成「上传采购票据 / 上传销售票据」，文件走输入框原来的入口（选中上传、点发送），所以已经落在 `via="send"` 这条路上；没有自己的上传口，也不自己触发识别。**后续各期凡是卡上交文件（直写、代录都一样），必须调 `files.commit(..., via="guide")`，移除调 `drop_upload`；不另写上传口或「上传即处理」。**

## 对扣费的影响
- 以前：选中就识别，移除或不发也扣费。现在：只有随消息发出的文件才识别、才扣费；移除的、没发出的不扣。
- 发出后识别比以前晚开始几秒（后台交原件层那几秒，以前在等用户打字时就做完了）；识别本身的时间不变。

## 验证（基线 origin/main a3e1a4c38）
- 单测（还原改动即失败的断言）：`test_express_operator_code_cc.py`（待发送不识别、发出后才交、交出入口幂等、移除的不识别、多份各交一次、交不成的三种原因与重交）、
  `test_assistant.py`（上传口只记待发送、`taken` 当场交、后台按人与语言交、移除删留存、`files.commit` 跨边：via guide / send）、
  `test_cc_operator.py`（输入框只上传：传完不读识别状态，没发出就移除调 `drop_upload`，已发出的、直写的不调）、`test_assistant_ui.py`、`test_cc_idle_motion.py`、`test_upload_limits.py` 跟着改。
- 本地全闸门（= development.yml）：244 步全过。
- 流程用例（本机实验栈 pearnly-uos-lab，挂本分支，假智能体）：新流程 `cc.operator.upload-on-send` / `cowork.cc.operator.upload-on-send`，
  加受影响的 `cc.attach-delete`、`cowork.cc.attach-delete`、`cc.operator`、`cc.operator.replay`、`cc.send-unblocked`、`cowork.cc.send-unblocked`：
  8 条 × 数据集 × 电脑 / 手机 **20/20 通过**。新流程实测：选两份都传上、方块上没有「排队中 / 识别中」、任务里没有上传；移除一份后服务端那行没了；
  上传接口 503 时留失败卡、可移除、任务不多记；发出后只有发出的那份进对话：ERP 这时才建收单记录（实验栈识别队列忙，产品记 `queue_busy`），
  Cowork 交到产品后被拒（实验栈没配识别参数，`start_refused`，已过工作空间与权限）；被移除那份的留存删了，识别队列里没有它。
- 第二个提交（直写一半，统一入口两种会话通用）后复跑：全闸门 244 步全过；上面 8 条加 `cc.chat` 共 9 条 **24/24 通过**。
- `start_sent` 逐份交识别那一行按逐行查询闸门写了 `# loop-query:`（一条消息 / 一张卡交出的几份，有界）。
- 识别没接真模型（主会话口径：识别用替身即可）。

### 共用件连带回归（`check-impact.py`：cc-composer、assistant）
| 入口 | 结果 |
|---|---|
| cc.assistant（对话页附件、发消息） | 通过（cc.attach-delete、cc.send-unblocked 两产品 × 两宽度） |
| cc.operator.page / cc.operator.thread（代录会话） | 通过（cc.operator、cc.operator.replay、新流程）；离线 test_cc_operator / test_express_operator_feed / tasks 通过 |
| erp.cc.chat-push、erp.cc.stocktake、erp.cc.collection、erp.cc.companies | 没测到：只改附件上传与交出后交识别，这几个工具不经过这条路；离线测试在全闸门里通过 |
| cowork.assistant、cowork.cc-chat.push、cowork.cc.troubleshooting | Cowork 识别工具只改注释；`files.taken` 行为变化由单测覆盖；没在实验栈真点（没有真识别） |
| control.assistant、shared.assistant、shared.exports | 没测到：不经过改动的路径 |
