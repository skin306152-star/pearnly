# CC 直写：点「采购」后用输入框「+」上传，票据不进识别（11-28）

- 分支 `pearnly/cc-upload-into-recognition`，基线 origin/main 3093f1d16。
- 来源：11-28 夜 testerp 副本（express-cloud-stage2 窗口）三次复现，证据 `~/pearnly-devtools/handoff-1128/express-cloud-upload-noocr/`。

## 1. 结论（先只读定位）

**不是云端公司、不是那条分支、不是副本环境的问题：ERP 直写模式下「选业务 → 输入框 + 上传 → 发送」这条路，任何公司都不会识别。**
线上 568f4b7f6、main 3093f1d16、列车头 9f70f4ecc、stage2 分支 ebad9ce0d 四处这几段代码一致（逐个 `git show` 核过）。

### 正常路径应该怎么走

ERP「采购 / 销售」声明是按单据办（`express/cc_guides.py`，`source: "documents"`，直写、代录两种模式，三种账本都在）。
按单据的引导一次 = 一条 `Pearnly Assistant Guide Run`：① 交文件那一题（`input: file`）→ `run.answer` 里 `files.commit(via="guide")`
→ 产品 `guide_documents.intake`（ERP = `intake.receive_chat`，建收单记录与识别任务）→ ② 识别中 → ③ 题卡 → ④ 复核 → ⑤ 确定。

### 哪一步断了

| 环节 | 代码 | 实际 |
|---|---|---|
| 点菜单「采购」 | `thread.tsx` `pickBusiness`：只有 `forms`（`api.businesses` 里 `source == "form"`，即收款）才 `guide_start` | 采购是 documents 源，只记本对话业务（`chooseBusiness`）、页面画一句「请上传…」，**不开引导运行** → Guide Run 0 行 |
| 输入框「+」上传 | `files.upload` → 引擎会话附件 + 本站留存 | 正常（附件在会话里） |
| 发送 | `sessions.send` → `files.commit(via="send")` | 直写会话只记「已交出」；**只有代录会话**（`operator_task`）才排 `start_sent` 交识别 |
| 模型 | 系统说明带 `business_note`（「用户选了采购、页面已请他交票」） | ERP 直写**没有任何能交识别的工具**（Cowork 有 `cowork_recognize_attachments`）；`pearnly_guide` 只收 `doc_ids`，设计约定 11-16 第 54 行写的「`pearnly_guide` 收 `files` 从 ① 开」没做 → 模型只能 `erp_list_documents`（0 份）后说「文件在会话里没被识别」 |

所以「引导卡里的上传」与「输入框的 + 上传」是两条路：前者只在已开着的 documents 运行的交文件题里出现，而直写点菜单根本开不出这张卡
（documents 运行只在待处理 / 打字工具带 `doc_ids` 时开）。回归 `erp.cc.guide.purchase` 一直过，是因为它在 `prepare` 里服务端直接
`run.start(doc_ids=[I:…], at="asking")`，跳过了「交文件 → 识别」（`scripts/flows/erp_cc_guide_direct_lib.py` 开头写明），这一格从没被测过。

### 各组合

| 组合 | 结果 | 依据 |
|---|---|---|
| ERP 直写 × 本地 Express / 云端 Express / MR.ERP | 都不识别 | 上表各环节与账本种类无关（账本只影响菜单出不出） |
| ERP 代录 | 识别 | `files.commit` → `start_sent` → `operator.uploads` |
| Cowork 直写 | 识别靠模型调 `cowork_recognize_attachments`（真模型多半会调） | 同一套前端、同一个 `sessions.send`，只是有工具 |
| Cowork 代录 | 识别 | 同 ERP 代录 |
| 桌面 / 手机 | 一样 | `pickBusiness` 不分宽度 |
| 线上 | 受影响（代码级，未在线上点） | 线上 568f4b7f6 同上各段代码一致；要在 testerp 实点先排主会话 |

## 2. 回英文

`sessions.send` 按「这句话自己的文字」判回复语言（`contracts.message_language`，11-10 S6），判不出才用界面语言。
只发文件时消息文字是页面拼的附件说明 `[Attached file, mounted read-only in the sandbox:\n- z1-test-invoice.png → /mnt/session/uploads/…]`，
拉丁词远多于 2 个 → 判成 **English**，覆盖了界面中文（`pearnly_whoami` 的 zh）。本地照搬函数实测：只发文件 → `English`，「采购」+ 文件 → `Chinese`。
与 cc-reply-language（E1–E3）同一个函数，但那批没处理附件说明。标题不受影响（`titles` 去掉附件说明后按界面语言）。
影响面：任何产品、任何模式、非英文界面下只发文件不打字的那一句（以及它这一轮的工具结果 `reply_language`）。

## 3. 修法（主会话 11-28「可以」，三件同一分支）

按类：任何模式下用户把票据文件发进对话，都进对应业务的识别与复核流程；直写与代录、ERP 与 Cowork 同一套判据（共用层，不按产品分叉）。

| 件 | 落点 | 改法 |
|---|---|---|
| A 主路径 | 新 `pearnly_core/assistant/guide/composer.py`；`sessions.send` | 发出前 `composer.plan`：直写会话、消息带本人上传的文件、本对话选了按单据办的业务（这个模式能办、账本相符、声明点齐单据函数）→ 有开着且还在交文件那一步的同业务引导就用它，没有开着的同业务引导就 `run.start` 开一次（开新的照旧取消别的业务那条）；同业务引导已过交文件（识别 / 题卡 / 复核，或别处交来的已有单）不动它。发出后排后台 `composer.hand_in`：以发消息的人、按当时语言调 `run.answer({documents: 文件})`——唯一入口 `files.commit(via="guide")` → 产品 `intake`。批末系统消息告诉模型「已交给页面上的某业务引导卡，别再识别 / 录入」。接不住（出错）照旧发消息，只记日志 |
| B 没选业务 | `guide_tool.prepare`、`tools.py` 的 `pearnly_guide` | 系统消息叫模型用 ask_user 问一次是哪项业务（按这段对话能开的按单据业务列），再 `pearnly_guide {business, files}`：`composer.open_with_files` 开引导并当场交进去（同输入框那条）；业务不在能开的里面、没有文件、卡不收各回原因码 |
| C 回英文 | `contracts.message_language` | 判语言前去掉附件说明（`ATTACHMENT_NOTE`），只发文件判不出 → 用界面语言。标题本来就按界面语言（`titles.language`），不受影响（当时两条标题都是中文，证据一致） |

约束照旧：识别按对话文件编号去重（产品 `intake` 的请求键：ERP `receive_chat(request_key=文件编号)`、Cowork `operator_source.intake` 同），
引导与模型都去交也只识别一次、只扣一次；识别后走题卡 → 复核 → 确定，确定前不写账（11-09）；代录照旧发出即由任务识别（`files.start_sent`）。

## 4. 测试登记缺口（典型例子）

`erp.cc.guide.purchase` / `erp.cc.guide.sales`（以及 Cowork 同名两格）在 `prepare` 里服务端直接 `run.start(doc_ids=[I:…])`，跳过「交文件 → 识别」，
登记的标题写了「实验栈没有识别，收单记录由 run.start(doc_ids) 交进来」，但没有任何一格从页面交文件。总表把这几格记成「做完」，而用户真正会走的
「点菜单 → 输入框 + 上传」从没跑过。补：`erp.cc.guide.purchase-composer`、`erp.cc.guide.sales-composer`、`cowork.cc.guide.purchase-composer`
（桌面 + 手机），只靠页面点，判据是引导卡出现并离开交文件、库里一条该业务引导运行、交的是那一份、产品建了识别记录。

## 5. 线上实点（一次，11-28 22:18–22:20，线上 568f4b7f6 / release-20261127-1225）

- 环境：testerp 22:17 由云端窗口 lab-stop 切回线上（主会话外部回读）；内置浏览器里用户本人的现有登录（主会话定：不换账号、不登出）。
- 公司 CO-140489 บริษัท Pearnly จำกัด（税号 1234567890123，测试公司）。核账套依据：公司详情 `cc_companies.detail` 记住的现用账套是「9.บริษัท Pearnly จำกัด」；
  新建页账套胶囊直接显示路径 `<账套服务器>\…\69EXP\TEST`；与退货减项窗口 11-28 在测试机1 Express 公司列表读到的「路径 TEST」一致
  （`~/pearnly-devtools/handoff-1128/退货减项.md` 第 16 行）。真客户公司没有选、没有打开。
- 步骤：直写 → 新建 → 选这家公司 → 点「采购」（出「请上传采购票据。」）→ 输入框「+」挂一张自造测试票 `cc-upload-test-1128.png`
  （画布生成，写着 TEST ONLY - NOT AN INVOICE，合计 0.00）→ 不打字点发送。只做这一次。
- 结果（与副本三次一致）：会话 `sesn_019rggiEX3hp5wQJzMDxxbgy`（标题「上传测试图片」）；对话里引导卡 0 张（页面引导事件 0；读 Guide Run 表没有权限）；
  ERP 对话收单记录（`ERP LINE Intake`，按会话）0 条；`businesses` 清单里 `forms` 不含 purchase / sales；CC 用**英文**回「I received the file …
  but it has not been recognized yet … This conversation has no recognized documents so far.」，并反问要不要开始采购录入。没有识别、没有建单、没有写账。
  截图 `~/pearnly-devtools/handoff-1128/cc-upload-online/testerp-online-568f4b7f6.jpg`。
- 收尾：用 CC 自己的「删除对话」删掉这段，回读侧栏清单已无此会话（22:20）。识别没发生，没有扣费。

## 6. 验证（分支 pearnly/cc-upload-into-recognition，基于 train-1128e 9f70f4ecc）

- 单测：`tests/test_assistant.py` 268（新：只发文件判不出语言、发出时交进引导 / 问一次 / 卡还开着）、`tests/test_cc_guide_composer.py` 6（新）、`test_cc_guide_run` 73。
- 本地全闸门（`gate.sh`）exit=0；候选离线（生产镜像 4cf9c… 挂本分支）LANE EXIT 0，115 组。
- code-review（medium）：

  | 发现 | 真问题 | 修了 | 耗时 |
  |---|---|---|---|
  | 4 | 2（同业务卡已过交文件时第二张票没人接；代录会话里打字路径会再识别一遍） | 2（另两条：选过业务后之后的文件都进识别——按「已选业务就按已选的」不改；发不出时空卡留着——重发复用，不改） | 约 10 分钟 |

- 页面流程（pearnly-cov3-lab，镜像 4cf9c…，数据集 new；实验栈不受理识别，流程每格临时配预算 Redis + 占位凭据、收尾作废并还原，不真识别）：
  - 修前（9f70f4ecc + 新流程）`erp.cc.guide.purchase-composer` / `erp.cc.guide.sales-composer` / `cowork.cc.guide.purchase-composer` × 桌面 + 手机 **6/6 不通过**：
    对话里没有卡、引导运行 0、ERP 收单记录 0（业务记着、上传的文件在）。
  - 修后 **6/6 通过**（每格约 55 秒）：卡出现并进「识别中」，一条该业务引导运行、交的是那一份、有识别批次，ERP 建了对话收单记录（来源 cc、方向 = 菜单业务、请求键 = 文件编号）。
- 共用件连带回归（`check-impact`：CC 对话 13 个入口）：同栈跑 14 条流程 × new / old × 桌面 / 手机 **29/29 通过**——
  `erp.cc.guide.purchase`、`erp.cc.guide.sales`、`cowork.cc.guide.purchase`、`cowork.cc.guide`（pearnly_guide 卡）、`erp.cc.guide.collection`、
  `cc.attach-delete`、`cc.send-unblocked`、`cowork.cc.send-unblocked`、`cc.chat`、`cc.operator.upload-on-send`、`erp.cc.operator.intake` 与三条新流程。
  覆盖到的入口：cc.assistant、shared.assistant、cowork.assistant、cowork.cc-chat.push、erp.cc.chat-push（直写引导确定交直写）、cc.operator.thread / cc.operator.start-card（代录上传与开始卡）、erp.cc.collection。
  没测到：control.assistant（fatcc.com 登录，本支不碰登录与入口）、erp.cc.companies、erp.cc.stocktake、cowork.cc.troubleshooting、erp.page.collections——本支只改发消息时附件的去向与语言判定，不碰这些入口的取数与页面。
- 没做：B（没选业务、模型问一次再 `pearnly_guide {business, files}`）的真模型复验——实验栈是假智能体，要测试机 / 线上真模型，最小样本，排在合入后由验收顺带看；离线由单测守住。
