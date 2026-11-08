# ERP / Cowork 版 CC 差异盘点（直写 / 代录四格，11-08）

- 主会话 local_e395dace 11-08 派出；只读盘点，**没有改任何代码**。基线 `ae387515b`（= origin/main，11-08 12:20）。
- 起因：11-08 用户在 testerp 手机 CC 传 27 页发票 PDF 要识别，ERP 版 CC 没有识别工具，模型在沙箱里自己逐页看图，还说「服务器没有泰文识别引擎」。
- 用户原话：「开窗口，查下到底还有哪些和 cowork 不一样的，工具调用为何这么少，erp 的直写和代录，cowork 的直写和代录」。
- 全部结论从代码与仓库记录读出，没有连测试机、没有连生产、没有真跑识别。

---

## 0. 先说结论（大白话）

1. **工具不是按「直写 / 代录」分的，是按「产品」分的。** 每个产品一份工具表，直写和代录用同一份；只有「推送单据」限直写、「查代录进度 / 跳过重做」限代录。
   所以四格其实是两份：**Cowork 76 个工具，ERP 19 个**（两边都含共用 5 个 + 代录 2 个）。
2. **ERP 少的 57 个里，34 个是 ERP 本来就没有的业务**（文件夹定时录入 11、月结回读 3、对账 8、报税 4、POS 导入 2、MR.ERP 设置 / 存货科目组 2、
   识别记录专属的录入选择 / 处理口径 / 删除 / 复核工作簿 4），不该补；
   **23 个是 ERP 网页上有这项业务、CC 没接**——最要紧的是**识别**（2；ERP 网页采购 / 销售都能上传识别），其次盘点 8、任务看板 8、改单 1、
   记住的选择 2、电脑状态 / 重读账套 2；另外物料库存查询、收付款、作废两边都没有工具。
3. **为什么少**：ERP 版 CC 是 11-02 才开始接工具的，当时的评估只定了「四格写账」（公司、待处理、列单、推送、结果）这一个范围；
   识别、看板写的是「不在本步」，盘点、改单写的是「不适用」（其实 ERP 网页都有）。之后没有一步回头补。**不是有意不做，是范围只做了写账、后面没跟上。**
4. **沙箱自读**：ERP 直写、ERP 代录**每次**有人传票据都会这样（没有工具可用）；Cowork 正常走识别工具，但共用层没有一条「不许自己读票据」的硬规矩，
   识别失败、文件类型不收时也可能退回自己读。另外：一个文件里有几张票时，ERP 网页只填第一张（LINE 才每张一张），所以 CC 不能只把网页那条路包一层。
   （页数：生产租户都走 Claude 路线，ERP 与 Cowork 同是共用层 50 页、分段读，27 页不会被拒；ERP 只有谷歌路线还留着旧的 20 页，见 §4 更正。）
5. **补法**：做成共用层一份「识别附件」骨架（取附件、代录里不重复识别、挂着等结果、进度、结果形状），两个 App 只声明「怎么识别、落成什么」；
   ERP 接自己现成的采购 / 销售识别（同网页、同计费、同自检）。估时见 §6。有 4 件事要主会话定（§7）。

---

## 1. 四格是怎么拼出来的（技术事实）

| 事实 | 位置 |
|---|---|
| 工具表 = 共用 `CORE_TOOLS`（5 个）+ 本站 App 在 `hooks.py` 声明的 `pearnly_assistant_tools` | `apps/pearnly_core/pearnly_core/assistant/tools.py` 的 `declared()` |
| Cowork 声明：`assistant.tools.TOOLS`（62）+ `assistant.company_tools.TOOLS`（7）+ 共用代录 `express_operator.tools.TOOLS`（2） | `apps/pearnly_cowork/pearnly_cowork/hooks.py:604` |
| ERP 声明：`express_operator.tools.TOOLS`（2）+ `masters.cc_tools.TOOLS`（4）+ `express.cc_tools.TOOLS`（8） | `apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py:638` |
| 直写 / 代录不分两份工具表；工具声明 `"modes"` 时，不符的调用服务端拒绝（`wrong_mode`） | `tools.mode_allowed`、`assistant/toolrun.py:141–149` |
| 限直写：`cowork_push_documents`、`erp_push_documents`；限代录：`express_operator_status`、`express_operator_control` | 各工具声明 |
| 系统提示 = 共用 `SYSTEM`（不认产品）+ 各产品 `pearnly_assistant_guidance` + 引擎的沙箱说明 `WORKSPACE` | `assistant/definition.py`、`assistant/engines/anthropic_managed_agents.py:33–47` |
| 代录接法：产品在 `pearnly_express_operator` 声明 `allowed / sources / precheck / intake / uploads / regroup / submit / target / resolve / status / pending` | `pearnly_core/express_operator/products.py` 文件头 |
| 开始前确认卡片：两产品都声明了 `pearnly_write_precheck`，直写、代录、待处理同一次判定 | 两个 `hooks.py`、`pearnly_core/write_precheck/` |

所以：**Cowork 直写 = Cowork 代录 − 1 + 2，ERP 直写 = ERP 代录 − 1 + 2**，差别都在「产品」这一维。

---

## 2. 四格工具对照（按业务分组）

「✓」能用；「—」没有；「直写」「代录」表示只在那一格。

| 业务 | Cowork 直写 | Cowork 代录 | ERP 直写 | ERP 代录 | 工具 |
|---|---|---|---|---|---|
| 账号、税号查询 | ✓ | ✓ | ✓ | ✓ | `pearnly_whoami`、`pearnly_tax_id_lookup`（共用） |
| 开始前确认卡片（要人定的一次问完、记住） | ✓ | ✓ | ✓ | ✓ | `pearnly_precheck`、`pearnly_precheck_answer`、`ask_user`（共用） |
| 代录任务进度、跳过 / 重做 | — | ✓ | — | ✓ | `express_operator_status`、`express_operator_control`（共用） |
| 公司列表、公司资料、团队备注 | ✓ | ✓ | ✓ | ✓ | Cowork `cowork_companies / company_profile / company_note_save / company_note_remove`；ERP `erp_` 同名 4 个（两份实现，规则共用） |
| 公司规则（记住的选择）存 / 删 | ✓ | ✓ | — | — | `cowork_company_choice_save / remove` |
| 待处理 | ✓ | ✓ | ✓ | ✓ | `cowork_pending` / `erp_pending` |
| 列单据、单据详情、看原件 | ✓ | ✓ | ✓ | ✓ | Cowork `cowork_list_documents / document_detail / document_original`；ERP `erp_list_documents / document_detail / document_original`（对象不同：识别记录 vs ERP 单据） |
| 改单（带依据改字段） | ✓ | ✓ | 只改「票面合计」 | 只改「票面合计」 | `cowork_edit_document` / `erp_set_printed_total` |
| 推送、推送结果、结果不明核对 | ✓ | 结果、核对 | ✓ | 结果、核对 | `cowork_push_documents / push_results / resolve_push_result`；ERP `erp_` 同名 3 个 |
| **识别附件、识别结果** | ✓ | ✓ | **—** | **—** | `cowork_recognize_attachments`、`cowork_recognition_results`、`cowork_intake_options` |
| 单据处理口径（转账凭条 / 预扣税凭证录不录）、删识别记录、复核工作簿 | ✓ | ✓ | — | — | `cowork_choose_document_policy / delete_documents / export_review_workbook` |
| 存货科目组、MR.ERP 导入设置 | ✓ | ✓ | — | — | `cowork_stock_group`、`cowork_mrerp_setup`（ERP 的存货科目组在确认卡片里问） |
| 电脑在不在线、刷新账套 | ✓ | ✓ | — | — | `cowork_companion_status`、`cowork_refresh_account_sets` |
| POS 收银导出导入 | ✓ | ✓ | — | — | `cowork_pos_import / pos_layout` |
| 文件夹、定时任务（11 个） | ✓ | ✓ | — | — | `cowork_folder_*`、`cowork_*_folder_task*` |
| 月结回读（3 个） | ✓ | ✓ | — | — | `cowork_express_readback*` |
| 对账（8 个）、报税草稿（4 个） | ✓ | ✓ | — | — | `cowork_recon_*`、`cowork_vat_draft / wht_draft / wht_answer / tax_draft_delete` |
| **盘点（8 个）** | ✓ | ✓ | **—** | **—** | `cowork_stocktake_*` |
| **任务看板（8 个）** | ✓ | ✓ | **—** | **—** | `cowork_work_*` |
| 物料、库存、库存卡、仓位 | — | — | — | — | 两边都没有（ERP 网页有） |
| 收付款、作废 | — | — | — | — | 两边都没有（ERP 网页有） |

工具全表（名字、标签、模式、处理函数、说明前 140 字）由 AST 抽出，见共享笔记 `CC工具全表-11-08.txt`。

---

## 3. 工具以外的差异

### 3.1 系统提示与工具说明

| 项 | Cowork | ERP | 用户感到的 |
|---|---|---|---|
| 共用 `SYSTEM` | 同一份 | 同一份 | 无 |
| 引擎沙箱说明 `WORKSPACE`（教模型用 pdftotext / pypdf 读大文件） | 同一份 | 同一份 | ERP 没识别工具时，模型照这条在沙箱里自己读扫描件（见 §4） |
| 「报识别值、不报自己读图」 | 只写在 `cowork_recognize_attachments` 的说明里 | 没有 | ERP 里模型自己读、自己报数 |
| **「不许自己读票据当业务数据」「没工具时怎么说」** | **两边都没有**（共用层只有「有工具就用工具」一句，`definition.py:25`） | 同左 | ERP 说出「服务器没有泰文识别引擎」这种误导话 |
| 产品说话规矩（`GUIDANCE`） | 19 条：账套按公司名说、所有公司怎么答、按单号找单、用户说单据属于哪家就录哪家、MR.ERP、CC 桌面版、排查…… | 10 条：公司备注 3 条 + 时间 / 本月 / 泰铢 / 表格 / CC 桌面版 / Express 已入账不能改 / 排查 | 「按单号、对方、金额找单」「这张是 X 公司的」这类说法 ERP 没教，模型自己猜 |

### 3.2 代录接法（`pearnly_express_operator`）

| 接口 | Cowork | ERP | 用户感到的 |
|---|---|---|---|
| allowed / sources / precheck / submit / status / pending | ✓ | ✓ | 一样 |
| **intake / uploads**（代录输入框放文件 → 交识别 → 识别完进任务） | ✓ | **—** | ERP 代录里放文件只进沙箱，模型自己读；Cowork 是「选中就上传、识别、录」 |
| regroup（资料区拖到别的组） | ✓ | — | 跟着 intake，ERP 没有资料区上传就没有 |
| target（录到 Express 还是 MR.ERP） | ✓ | — | ERP 代录只到 Express。ERP 的 MR.ERP 整体关着（`mrerp.MRERP_LEDGER_ENABLED = False`，等岳父测试账号），**有意暂缓** |
| resolve（结果不明，人核对后报单号） | ✓ | — | ERP 由原电脑自己核对（`2026-11-02-erp-cc-chat-push.md` §5 写明的有意不同） |

### 3.3 入口

| 入口 | Cowork | ERP |
|---|---|---|
| 电脑网页 `/assistant`、手机网页（同一个 `frontend/assistant`） | ✓ | ✓ |
| CC 桌面版 | ✓（Cowork 通道 `pearnly_cowork.companion.*`） | ✓（共用机器通道 `pearnly_core.cc_desktop.machine.*`，ADR-025） |
| LINE | 不是 CC 入口：LINE 里是各产品自己的收单流程（Cowork `line/`、ERP `integrations/intake.py`），不经 CC 对话 | 同左 |

CC 桌面版两条服务端通道：线上约定共用（`cc_desktop.wire`），文件夹自动录入、认电脑（`device_api.hello`）只 Cowork 有（`companion/src/companion/channel.py` 文件头）。这是 ADR-025 定的结构，不算这次要补的。

### 3.4 前端界面（`frontend/assistant`）

前端基本是一套，按服务端声明的「分区」出功能，不按产品名分支。差别来自声明：

| 界面 | Cowork | ERP | 依据 |
|---|---|---|---|
| 对话页、侧栏（按公司 / 月份分组）、开始前确认卡片、公司选择弹窗、代录切换 | ✓ | ✓ | 同一套组件 |
| 右侧公司栏卡片 | 公司、月结、待处理、备注、文件夹、定时任务、记住的选择、税表草稿 | 公司、待处理、备注、「在 ERP 打开」 | `SECTIONS`：Cowork `companies/provider.py:22`，ERP `masters/cc_projects.py:20` |
| 公司改名、删除 | ✓ | — | ERP 公司在 ERP 里建（有意） |
| 公司选择弹窗的「刷新账套」 | ✓ | — | ERP 没声明 `catalog` |
| 识别完自动继续、挂着时的识别进度 | ✓ | — | ERP 没有识别工具 |
| **浏览器里设置的「连接」（只读看连着哪台电脑）** | ✓ | **—** | 写死 `product === "cowork"`：`components/settings.tsx:114`（10-10）、`lib/desktop.ts:54`（11-07 照它写） |
| **Windows 浏览器提示「下载 CC 桌面版」** | ✓ | **—** | 写死 `product === "cowork"`：`lib/install.ts:105`（10-12） |

后两处写在 ADR-025（10-23，ERP 员工也用 CC 桌面版）之前或照着之前的写，**没跟上**：ERP 员工在 Windows 浏览器里看不到下载提示，公司选择弹窗一家都没有时也没有「连接」按钮。

---

## 4. 沙箱自读：在哪种情况下发生，Cowork 会不会

**机制**：对话里传的附件放进引擎沙箱 `/mnt/session/uploads/`（`assistant/files.py`），本站另留一份原件。引擎说明 `WORKSPACE`
教模型「大文件用 pdftotext / pypdf 抽」。扫描件 PDF 没有文字层，模型就去找 OCR，沙箱里没有泰文 OCR，于是说「服务器没有泰文识别引擎」，再逐页把图读进对话。

| 格 | 会不会 | 原因 |
|---|---|---|
| ERP 直写 | **每次都会** | 没有任何识别工具 |
| ERP 代录 | **每次都会** | ERP 没声明 `intake`，`uploads.take` 回空，文件只进沙箱（`assistant/files.py` 的 `_operator_take`） |
| Cowork 直写 / 代录 | 正常不会 | 有 `cowork_recognize_attachments`，说明里写「用户发票据要识别、读、录就用它」「报识别值，不报自己读图」；代录里放进输入框的文件已交任务识别，工具按 `files.taken` 取回同一次，不重复收费 |
| Cowork 仍可能退回自读的情况（代码推断，**没拿线上证据**） | 可能 | ① 工具拒收：工具自己生成的文件、不收的文件类型；② 识别失败、站点没配识别、余额不足；③ 用户只问一句「这张多少钱」时模型判断不用识别。共用层没有「不许自己读票据」的硬规矩兜底 |

**后果（用户能感到的）**：读得慢（27 页逐页进对话）、读法没过产品的自检（税号校验、金额自洽、Haiku→Opus 分级）、**不扣租户识别费、费用算在我们 Anthropic 账上**（Opus 读 27 张图远贵于识别按页）、读完不生成单据、不能推送，还说误导话。

**还要注意**：一个文件里多张票时**ERP 网页只填第一张**（`purchasing/recognition.py`「网页一次只填第一张」，LINE 才每张一张卡）。所以 ERP 补识别不能只把网页那条路包一层。

**页数更正（11-08 施工前复核）**：初稿写「ERP 超过 20 页直接拒」不对。`recognition/inputs.py` 的 `MAX_PAGES = 20` 只在**谷歌路线**用到
（`policy.read` 的文字层快路、`policy.read_all` 拆页）；选了 Claude 的租户（生产全部）走 `recognition/claude.py`，上传过共用层原件检查
（`pearnly_core/originals/rules.py` `MAX_PDF_PAGES = 50`），识别按 `claude_ocr.segments` 每段 8 页、最多 16 段读完，与 Cowork 同一套。
27 页在生产 ERP 网页能识别；只剩谷歌路线的 20 页与共用常量不一致（P0-3 收掉）。

---

## 5. 差异清单（每条先写用户感到的差别）

| # | 用户感到的差别 | 哪格缺 / 不同 | 类 | 依据位置 |
|---|---|---|---|---|
| 1 | ERP 里传票据，CC 不能识别，只能自己看图读、说「服务器没有识别引擎」，读完也进不了单据 | ERP 直写、ERP 代录 | **B** | `express/cc_tools.py` 无识别工具；`2026-11-02-erp-cc-chat-push.md` §3「对话里识别不在本步」；ERP 网页有 `purchasing/recognition.py`、`sales/recognition.py`、`recognition/background.py` |
| 2 | 两边都可能退回自己读票据并照读出的数说 | 四格（Cowork 少见） | **C**（共用规矩缺） | `assistant/definition.py` 只有「有工具就用」；「报识别值」只在 Cowork 工具说明 |
| 3 | 一个文件几张票，ERP 网页只填第一张（CC 要每张一张，同 LINE）；谷歌路线的 ERP 还留旧的 20 页上限（生产都走 Claude，不受影响） | ERP | **C** | `purchasing/recognition.py` extras 注释；`erp recognition/inputs.py:13`；共用 `originals/rules.py:16` |
| 4 | ERP 代录里放文件不会识别进任务 | ERP 代录 | **B** | ERP `hooks.py` 的 `pearnly_express_operator` 无 `intake / uploads` |
| 5 | ERP 里问「盘点做到哪了」「建个盘点」CC 做不了 | ERP 两格 | **B** | ERP 网页 `inventory/api.py` `api_list_stock_counts / create / add_entry / close / adjust`；chat-push 记录写「不适用」 |
| 6 | ERP 里让 CC 建任务卡、看看板做不了 | ERP 两格 | **B**（另有业务层 **C**：两份看板代码约 1200 行近乎复制，`work/service.py` 只差 221 行） | ERP `work/api.py`；chat-push 记录「不在本步」 |
| 7 | ERP 里问「某物料还有多少库存」「库存卡」CC 答不了 | ERP 两格（Cowork 也没有，但 Cowork 不管库存） | **B** | ERP `inventory/api.py` `api_stock_card`、`products/service.py` |
| 8 | ERP 卡住的单 CC 只能改「票面合计」，供应商、金额读错改不了 | ERP 两格 | **B** | `erp_set_printed_total`；Cowork `cowork_edit_document` 可改钱与身份字段带依据 |
| 9 | ERP 里「记住的选择」看不到、删不掉（Cowork 右栏有卡片、CC 能删） | ERP 两格 | **B**（低） | ERP `express/choices.py` 只有 remember / remembered，无删；`SECTIONS` 无 `choices` |
| 10 | ERP 里问「电脑在线吗」「重读一下账套」CC 没工具（推送结果里带在线与否） | ERP 两格 | **B**（低） | ERP `express/api.py:27` `sync_company`（只给管理员）；Cowork `cowork_companion_status / refresh_account_sets` |
| 11 | ERP 收付款、作废 CC 做不了 | ERP 两格 | **B**（要定：写操作要不要「允许」卡片） | ERP `purchasing/api.py:57,64`、`sales/api.py:53,58` |
| 12 | ERP 员工在 Windows 浏览器里不提示下载 CC 桌面版；浏览器设置里没有「连接」；公司弹窗空时没有「连接」按钮 | ERP 网页 | **C**（没跟上 ADR-025） | `lib/install.ts:105`、`components/settings.tsx:114`、`lib/desktop.ts:54` |
| 13 | ERP 少几条说话规矩：按单号 / 对方 / 金额找单、用户说「这张是 X 公司的」 | ERP 两格 | **C** | 两边 `GUIDANCE` 对照（§3.1） |
| 14 | 结果不明的单：Cowork 由人去 Express 看了报单号；ERP 交原电脑核对 | 两产品 | C（**有意**，已记录，保留） | `2026-11-02-erp-cc-chat-push.md` §5 |
| 15 | ERP 代录只到 Express，不到 MR.ERP | ERP 代录 | 暂缓（等岳父测试账号） | `2026-11-03-erp-mrerp-direct.md`；记忆「MR.ERP 实测等岳父账号」 |
| 16 | 文件夹定时录入、月结回读、对账、报税草稿、POS 导入、MR.ERP 导入设置、公司改名删除 | ERP 两格 | **A**（ERP 不该有） | ERP 功能目录 `features.json` 无这些（只有物料、出入库、库存卡、盘点、采购、销售、客户、收银、任务、外部推送）；评估 `2026-11-02-mrerp-operator-and-erp-company-评估.md` §0「不在本需求范围」；ERP 有自己的收银台；ERP 公司在 ERP 里建 |
| 17 | 盘点在两个产品是两份代码（Cowork `stocktake/`、ERP `inventory/count*.py`） | 业务层 | C（结构，不在本次） | hooks 注释「库存盘点照搬 ERP」 |

---

## 6. 补齐方案（按优先级；估时 = 写码 + 机器跑，小时级）

原则：共用层出一份骨架，两个 App 只声明接法（ADR-009）；每项先写会红的测试；Cowork 现有行为用黄金样本逐字节锁住再下沉（同 11-02 `direct_push` 的做法）。

| 优先 | 项 | 做法（一套怎么落） | 写码 | 机器 | 成本影响 | 与在做的窗口交叠 |
|---|---|---|---|---|---|---|
| **P0-1** | 共用规矩：票据、照片、扫描件一律交产品识别工具；没有可用工具时直说「这里识别不了，在 X 页上传识别」，不自己读成业务数据、不说服务器没有识别引擎 | 改共用 `definition.py` 的 `SYSTEM` 一条（不分产品）；引擎 `WORKSPACE` 那条大文件说明加「票据类不适用」 | 0.5 | 1（闸门 + 智能体说明更新） | 省钱（不再整份图读进对话） | 无（`definition.py` 没人在改）；改说明会让各站点下次使用时更新远端智能体 |
| **P0-2** | ERP 识别附件工具（直写） | 共用层新增「识别附件」骨架（`pearnly_core.assistant`）：按文件编号取本站原件、代录里按 `files.taken` 取回同一次、`waits.park / finished / touch` 挂着等、结果形状与「报识别值」规矩统一；产品声明 `pearnly_assistant_recognition = {start, check, …}`。ERP 声明接自己的 `purchasing / sales.recognition.recognise`（同网页、同计费 `billing.charge`、同 Claude 自检），**每张票各落一张**（同 LINE），要人定的（对方、已付未付、按行物料 11-07）走确认卡片一次问完。Cowork 第二步再改成声明（黄金样本锁住） | 5–7 | 2（闸门、候选离线、受影响流程；真识别小样本 ≤0.5 美元，要先问） | 识别费按页向租户收（同网页）；我们的 Anthropic 账反而降 | **`pearnly/cc-send-while-waiting`** 在改 `waits.py` 与 Cowork 识别进度——骨架**只调用** waits 的现有接口、不改 waits.py，排在它合入之后开工；`ocr-haiku-split` 若改 `claude_ocr.segments` 只影响读法，不冲突 |
| **P0-3** | ERP 谷歌路线页数改用共用常量 | ERP `recognition/inputs.py` 的上限直接引用共用 `originals.rules.MAX_PDF_PAGES`（50），不再自己写 20；Claude 路线本来就是它。多张票每张一张放进 P0-2 | 0.5 | 1 | 零（生产走 Claude，不变） | 无 |
| **P0-4** | ERP 代录里放文件交识别 | ERP 在 `pearnly_express_operator` 声明 `intake / uploads`，内部调 P0-2 同一个识别入口 | 2–3 | 1.5（代录流程 `cc.operator.thread`） | 同 P0-2 | `companion/` 不动；Cowork 代录不动 |
| P1-1 | ERP 盘点工具 | 工具声明形状照 Cowork `cowork_stocktake_*`（列、建、记数、关、导出、生成调整单），ERP 包自己的 `inventory` 接口；两份盘点代码不在本次合并 | 3–4 | 1.5 | 零 | 无 |
| P1-2 | ERP 改单（带依据改字段） | 照 `cowork_edit_document` 的依据规则（共用层下沉规则），ERP 改「还没进 Express 的单」走单据页同一个入账 / 改正函数 | 3–4 | 1.5 | 零 | 无 |
| P1-3 | 说话规矩补齐（#13） | 「按单号找单」「用户说属于哪家」两条下沉共用层，Cowork 原位引用（黄金样本锁住） | 1 | 1 | 零 | 无 |
| P2-1 | ERP 物料 / 库存 / 库存卡查询 | 只读工具，服务端分页 | 2 | 1 | 零 | 无 |
| P2-2 | 前端写死 Cowork 三处（#12） | 改按声明：产品声明有没有「浏览器只读连接」与「桌面版下载」（`projects()` 加一项），页面不按产品名分支 | 1 | 1（两产品浏览器点页） | 零 | `pearnly/cowork-express-warehouse` 改 `i18n.ts`、`company-cards.tsx`，这三个文件没碰 |
| P2-3 | ERP 任务看板工具 | 工具形状照 `cowork_work_*`；两份看板代码近乎复制，**建议先把看板下沉共用层**（另立一项，约 6–8 + 3 小时），再一份工具两边用 | 3（只接工具）| 1 | 零 | 无 |
| P2-4 | ERP 记住的选择看 / 删、电脑状态 / 重读账套（#9、#10） | 共用卡片 `choices`；ERP 加 forget 与工具 | 2–3 | 1 | 零 | 无 |
| P3 | ERP 收付款、作废（#11） | 等 §7 第 4 条定了再做 | 3 | 1 | 零 | 无 |
| 不做 | #14 保留（有意）；#15 等岳父账号；#16 A 类；#17 不在本次 | — | — | — | — | — |

P0 合计约 **写码 9–12 小时 + 机器 5–6 小时**（另加用户试用），P1 约 8–9 + 4 小时。

---

## 7. 主会话定案（11-08）

主会话 local_e395dace 11-08 审过本盘点，五条定案（下面「原问题」保留作记录）：

1. **ERP 对话识别完**：每张票建成**已提交的 ERP 单据**，和 LINE / 网页点「提交」走同一个入账函数，提交后照现有规则排 Express。要人定的
   （对方、已付未付、付款方式、按行物料与入库）在开始前一张卡片里一次问完，问一次记住。
2. **页数上限**：ERP 识别上限提到和 Cowork 同一个共用常量（50），网页跟着变。（施工前复核：Claude 路线本来就是共用 50 页，
   实际只改谷歌路线那处 20，见 §4「页数更正」。）
3. **一套**：共用层一份识别骨架。先 ERP 声明接上，再把 Cowork 下沉成声明，黄金样本锁住；分两次提交。
4. **不加「允许」卡片**：收付款、作废、改单和 Cowork 推送、改单一样不出卡片；管钱的底线留在代码里守（金额、状态、权限、幂等），不靠确认卡。
5. **不查 testcowork**；P0-1 那条共用规矩照做。

施工口径：按 P0-1 → P0-3 →（`pearnly/cc-send-while-waiting` 合入后）P0-2 → P0-4 → P1 → P2 → P3 连续做，一项闭环接下一项；P2-3 看板先下沉共用层，
再一份工具两边用。每项单独分支、单独合入：`gate.sh` + `cand-local.sh offline`（102 组以上）+ 受影响流程 + CI 绿，发主会话等「可以合」。
真识别小样本每次 ≤0.5 美元，跑前发主会话，台账照记。

### 原问题（已定，留作记录）

1. **ERP 对话里识别完落成什么？**（影响库存与付款）
   - 建议：每张票建成**已提交的 ERP 单据**（等于 LINE 卡片上点「提交」、网页点「提交」，同一个入账函数；提交后照现有规则排 Express），
     要人定的（对方、已付 / 未付、付款方式、按行物料与入库）开始前一张卡片一次问完、问一次记住。
   - 另两种：落成 ERP 草稿让人去网页提交；只回识别值不建单。后者和 Cowork「识别完进工作台、可推送」不一致。
2. **ERP 识别页数上限从 20 提到与 Cowork 同（50）？** 改的是 ERP 网页与 CC 共用的那一处，网页也会跟着变。
3. **识别附件做成共用层骨架、Cowork 随后改成声明（一套），还是先只给 ERP 接（暂时两份）？** 建议一套，按「先 ERP 声明接上 → 再 Cowork 下沉」两步提交。
4. **ERP 收付款、作废、改单这类写操作，CC 做时要不要「允许」卡片？** Cowork 现在只有改全公司设置、定时安排才出卡片，推送不出。
5. （可选）要不要我只读查 testcowork 近期会话事件，确认 Cowork 有没有发生过沙箱自读？

## 8. 本记录的状态

- 本工作树 `.claude/worktrees/stoic-hugle-e7ba17`，分支 `pearnly/stoic-hugle-e7ba17`（桌面 App 自动建），只加了本文档。
- 审定后按批施工；施工另开 `pearnly/<主题>` 分支。本分支与工作树在本步收尾时按「收工清理」处理（文档随第一批施工带入或单独合入，由主会话定）。
