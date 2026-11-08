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

## 8. 本记录的状态（初稿时）

- 本工作树 `.claude/worktrees/stoic-hugle-e7ba17`，分支 `pearnly/stoic-hugle-e7ba17`（桌面 App 自动建），只加了本文档。
- 审定后按批施工；施工另开 `pearnly/<主题>` 分支。本分支与工作树在本步收尾时按「收工清理」处理（文档随第一批施工带入或单独合入，由主会话定）。

---

## 9. 11-08 下午新口径（主会话转用户，以这一节为准）

- **CC = 这个产品网页上能做的事 + 代录。** 对照对象从「ERP 比 Cowork 的工具表」改成「ERP 的 CC 比 ERP 网页本身」（Cowork 同理）。网页能做的 CC 都能做，
  权限和网页一样（同一组服务端函数），不加确认卡；只有确实不适合对话的（如收银台现场收款）写明原因。
- **代录是 CC 独有的，两个产品做成一份实现**：intake / uploads / regroup 下沉共用层；resolve 两边一套（先查清 11-02 定不同的理由）；target 接口两边都有，
  MR.ERP 执行层一起等岳父账号；代录界面逐项核一致。
- **P0-1 不单独上**：只上「不许自读票据」而 ERP 还没有识别，比现在退步。P0-1 改和 P0-2 同批。
- **P0-2 口径修正**：用户只说「识别 / 把内容发给我」时，CC 走产品识别、把识别出的内容回给用户（可导出表格），每张票先落成**待提交记录**（同 LINE 卡片），
  不自动入账；用户说「录入 / 入账 / 提交」或开始前卡片选了入账才提交。与 Cowork「识别完进工作台、推送另做」一致。（覆盖 §7 第 1 条与 P0-2 评估 §12 的「每张票建成已提交单据」。）
- 正式标准写成 [ADR-027 CC 能力架构与标准](../adr/0027-cc-capability-architecture.md)；本窗口是 CC 这条线（工具层、代录接法、共用规矩）的唯一负责窗口。

## 10. 场景清单（验收用，两个产品各跑一遍，电脑 + 手机，真模型）

「期望」写用户看到的结果；「—」表示该产品没有这项业务（不适用）。

| # | 用户对 CC 说 | ERP 期望 | Cowork 期望 |
|---|---|---|---|
| 1 | 传一张票据：「帮我看看这张写了什么」 | 调识别，回识别出的对方、单号、日期、合计、明细；落成一条待提交记录，不入账；不自己读图 | 调识别，回识别值；进录入工作台（识别记录），不推送 |
| 2 | 传 27 页 PDF：「把内容整理成表格给我」 | 识别（分段读、挂着时有页数进度），每张票一行，产出 Excel 放进对话 | 同左，识别记录各一条，产出复核工作簿 |
| 3 | 传票据：「识别并录入」 | 识别 → 开始前一张卡问完要定的（对方认不到照票面新建、已付未付按记忆预选）→ 提交成采购 / 销售单 → 照规则排 Express，报单号 | 识别 → 卡片 → 推送进 Express / MR.ERP，报单号 |
| 4 | 「把这几张推到 Express」 | 推送；结果入账带 Express 单号；电脑离线说清排队 | 同左 |
| 5 | 「这个月还有哪些单卡着？」 | 列待处理与原因（`erp_pending`） | 同左（`cowork_pending`） |
| 6 | 「IV001 那张供应商读错了，应该是 X 公司」 | 采购单（没进 Express、没另记收付款）走网页「修改」：作废原单、按改后新开一张，带依据；销售单说明去网页作废重开 | 改识别记录字段，带依据，再推 |
| 7 | 「这张采购单付了 5,000 现金」 | 记一笔付款（同网页「付款」） | —（Cowork 不管收付款，说明在 Express 里做） |
| 8 | 「把 SI-0005 作废」 | 作废（同网页）；已进 Express 的照现有规则排 Express 作废 | 删识别记录 / 说明已入账的不能在 CC 改 |
| 9 | 「A001 还有多少库存？在哪个仓？」 | 按物料合计 + 分仓 / 仓位 | — |
| 10 | 「A001 这个月进出明细」 | 单品收发存明细（期初、逐笔、期末） | — |
| 11 | 「开一份主仓的盘点」→「A001 数到 12」→「结束」→「按结果调库存」 | 新建、记数、结束（不动库存）、生成调整单草稿并说出单号 | 盘点工作台对应动作（Cowork 盘点不生成调整单） |
| 12 | 「新建物料：A 牌纸巾，单位包，进价 20」 | 建物料（同网页，按码查重） | — |
| 13 | 「新加一个供应商，税号 0105…」 | 查税号公开登记 → 建供应商 | 记进公司规则 / 说明在 Express 主档建 |
| 14 | 「导出这个月的采购表」 | 产出 Excel（同网页导出） | 导出复核工作簿 / 推送记录 |
| 15 | 「打印 PO-0003 的凭证」 | 产出 PDF 放进对话（同网页打印） | — |
| 16 | 代录：「把这一批录进 Express」+ 拖进 3 个文件 | 放进输入框即识别、资料区按单分组、开始前卡片、电脑一张张录、状态与结果 | 完全相同的界面与流程 |
| 17 | 代录中：「第 2 张跳过」「为什么第 3 张没录」 | 跳过、按任务真实进度回答 | 同左 |
| 18 | 「给张三建个任务卡：明天对账」 | 任务看板建卡（负责人、到期） | 同左 |
| 19 | 「办公室电脑在线吗？重读一下账套」 | 列这家的电脑与在线状态；重读账套 | 同左 |
| 20 | 「记住：这家公司的油费都记 53-01」 | 记进团队备注 / 记住的选择 | 同左 |
| 21 | 用泰文问任何一条 | 用泰文回答（跟着这句话的语言），工具出错也不换成英文 | 同左（11-08 真模型复测 Cowork 出错时答成英文，段 2 修） |
| 22 | 「帮我在收银台收这笔钱」 | 说明收银要在收银台当面做（不适用） | — |

## 11. ERP 网页动作对照表（每页每个动作：CC 已有 / 在做 / 缺）

「在做」= 已有分支（盘点 P1-1、库存 P2-1、改单 P1-2、识别 P0-2）。「缺」给做法（按 ADR-027 模板）与写码估时（小时，机器另计约 30%）。
接口名是 ERP 网页实际调用的服务端函数（`apps/pearnly_erp_portal/pearnly_erp_portal/<域>/api.py` 等），CC 工具调同一组。

| 页 | 动作 | 网页接口 | CC | 做法 / 原因 | 估时 |
|---|---|---|---|---|---|
| 首页 | 看概况、账单、充值、套餐 | `api_summary`、计费页 | 缺 / 不适用 | 概况可查（单据模板「汇总」）；充值、套餐涉及付款，CC 不碰（安全规矩），说明去页面 | 1 |
| 物料 | 列表 / 搜索 / 看 | `list_products` `count_products` `get_product` `find_product` | 缺 | 主档模板「列 / 看」 | 1.5 |
| 物料 | 新建（含编码、单位、价格、条码） | `create_product` `next_product_code` | 缺 | 主档模板「建」，按码查重同网页 | 1.5 |
| 物料 | 改 / 停用启用 | `update_product` `set_active` | 缺 | 主档模板「改 / 停用」 | 1 |
| 物料 | 导入（Excel） | `import_template` `import_products` | 缺 | 对话附件 → 同一导入函数；模板放进对话 | 1.5 |
| 物料 | 分类建改 | `create_category` `update_category` | 缺 | 主档模板 | 0.5 |
| 物料 | 按台管理开关、期初、台查询导出 | `serials.*` | 缺 | 只读查询先做；开关属设置，管理员可做 | 2 |
| 库存 | 在库一览 / 按物料合计 | `stock` `stock_count` `stock_totals` `stock_summary` | 在做 | `erp_stock_lookup`（P2-1） | — |
| 库存 | 导出 | `stock_export` | 缺 | 单据模板「导出」（产出文件放进对话） | 0.5 |
| 库存 | 期初（预览 / 提交 / 模板 / 读文件） | `preview_stock_opening` 等 | 缺 | 对话附件 → 同一函数；先预览给人看再提交（同网页两步） | 2 |
| 仓库 | 建 / 改仓库、仓位 | `create_warehouse` `update_warehouse` `get_locations` `save_locations` | 缺 | 设置类，管理员；主档模板 | 1.5 |
| 入库 / 出库 / 调拨 / 物料调整 | 列 / 看 | `list_stock_entries` `get_stock_entry` `stock_entry_total` | 部分（`erp_list_documents` 只看写 Express 的状态） | 单据模板「列 / 看」覆盖四种 | 1.5 |
| 同上 | 建草稿 / 改草稿 / 提交 / 删草稿 | `create_stock_entry` `update_stock_entry` `submit_stock_entry` `delete_stock_entry_draft` `receive` `issue` `adjust` | 缺 | 单据模板「建 / 改 / 提交」；按行物料与仓位照网页 | 3 |
| 同上 | 作废 | `void_stock_entry` | 缺 | 单据模板「作废」；已进 Express 的照现有规则排作废 | 1 |
| 同上 | 附件 | `stock_upload*` `add_stock_entry_files` `remove_stock_entry_file` | 缺 | 对话附件挂到单据 | 1 |
| 收发存报表 | 单品明细 | `stock_card` | 在做 | `erp_stock_card`（P2-1） | — |
| 盘点 | 列 / 看差异 / 新建 / 记数 / 重盘 / 结束 / 生成调整单 | `list_stock_counts` `stock_count_review` `create_stock_count` `stock_count_add_entry` `stock_count_recount` `stock_count_close` `stock_count_adjust` | 在做 | P1-1 六个工具 | — |
| 盘点 | 导出差异表、删除、改 / 作废一条记数、就地新建物料、上传表导入 | `stock_count_export` `stock_count_delete` `stock_count_edit_entry` `stock_count_create_product` `create_stock_count(file_url)` | 缺 | 盘点模板补全 | 2.5 |
| 采购 | 列 / 看 / 看原件 | `purchase_records` `purchase_document` `purchase_file_page` | 已有 | `erp_list_documents` `erp_document_detail` `erp_document_original` | — |
| 采购 | 上传识别 | `recognise_purchase` | 在做 | P0-2（待提交记录 → 用户说录入才提交） | — |
| 采购 | 新建并提交（手录） | `post_purchase` `purchase_defaults` `purchase_amounts` | 缺 | 单据模板「提交」，开始前卡片问完要定的 | 2 |
| 采购 | 修改（作废原单 + 新开） | `post_purchase(amended_from)` | 在做 | P1-2：只挡已进 Express、提交后另记过收付款的；带依据 | — |
| 采购 | 作废 | `cancel_purchase` | 缺 | 单据模板「作废」 | 1 |
| 采购 | 付款 | `purchase_payment` | 缺 | 单据模板「收付款」，金额 Decimal、幂等请求号 | 1.5 |
| 采购 | 删草稿、附件 | `delete_purchase_drafts` `purchase_attachments` `remove_purchase_attachment` | 缺 | 单据模板 | 1 |
| 采购 | 导出 / 打印（凭证、代收据、预扣税凭证） | `purchase_export` `purchase_voucher` | 缺 | 单据模板「导出 / 打印」，文件放进对话 | 1.5 |
| 采购 | Express（推送、选一次、再写一次、票面合计） | `express.api.*` | 已有 | `erp_push_documents` 等 + 确认卡 | — |
| 供应商 | 列 / 建 / 改 | `list_suppliers` `create_supplier` `update_supplier` | 缺 | 主档模板；税号查用已有 `pearnly_tax_id_lookup` | 1.5 |
| 销售 | 列 / 看 / 看原件 / Express | `sales_records` `sales_document` `sale_file_page` | 已有 | 同采购 | — |
| 销售 | 上传识别 | `recognise_sales` | 在做 | P0-2 | — |
| 销售 | 新建并提交 | `post_sale` `sales_defaults` `sales_amounts` | 缺 | 单据模板「提交」 | 2 |
| 销售 | 作废 / 收款 / 删草稿 / 附件 / 导出 / 打印 | `cancel_sale` `sale_payment` `delete_sale_drafts` `sale_attachments` `sales_export` 打印 | 缺 | 单据模板（销售没有「修改」，同网页） | 3 |
| 收银台 | 开班 / 收款 / 关班 | `open_shift` `create_sale` `close_shift` | 不适用 | 现场收现金、点钱，要人在柜台；CC 说明去收银台 | — |
| 收银台 | 当前班次、班次汇总 | `current_shift` `shift_summary` | 缺 | 只读查询 | 1 |
| 小票 | 列 / 看 / 合计 | `list_sales` `get_sale` `sale_total` | 缺 | 单据模板「列 / 看」 | 1 |
| 小票 | 退款 / 作废 | `refund_sale` `void_sale` | 待定 | 网页能做，但通常在柜台当面退钱；先问主会话（ADR-027 §6） | 1 |
| 班次 | 列 / 导出 | `list_shifts` `count_shifts` `shifts_export` | 缺 | 只读 + 导出 | 1 |
| 收银设置 | 终端建改、付款方式 | `create_terminal` `update_terminal` `set_payment_methods` `save_company_terminals` | 缺 | 设置类，管理员 | 1 |
| 客户 | 列 / 看 / 建 / 改、税号查 | `list_customers` `get_customer` `create_customer` `update_customer` `lookup_tax` | 缺 | 主档模板 | 1.5 |
| 任务 | 看板 / 卡片全部动作（含成员、标签、字段、规则、归档、导出） | `work.api.*` | 缺 | 看板先下沉共用层（P2-3），任务看板模板一份两产品用 | 随 P2-3 |
| 公司 | 列 / 资料 / 团队备注 / 待处理 | `masters.cc_companies.*` | 已有 | `erp_companies` `erp_company_profile` `erp_company_note_*` `erp_pending` | — |
| 公司 | 新建 / 改资料 / 切换当前公司 | `create_company` `update_company` `update_profile` `set_active_company` | 缺 | 公司模板；切换当前公司在 CC 里就是换对话挂的公司 | 1.5 |
| 公司 | 同步 Express、断开 Express、MR.ERP 设置 | `sync_company` `disconnect_express` `mrerp.api.*` | 缺 | 电脑与连接模板；断开属危险操作但网页能做，照做；MR.ERP 等岳父账号 | 1.5 |
| 团队 | 列成员 / 加人 / 改角色 / 停用 | `list_members` `add_member` `set_member_roles` `set_member_active` | 缺 | 管理员；团队模板 | 1.5 |
| 团队 | 改成员密码 | `set_member_password` | 不适用 | CC 不经手密码（安全规矩），说明去页面 | — |
| 个人资料 | 姓名、语言、通知 | 官方 User 页、`pearnly_core.account.preferences` | 缺 | 只改语言 / 通知等偏好；密码不碰 | 0.5 |
| LINE | 绑定码 / 解绑 | `line_binding_code` `line_unbind` | 缺 | 绑定码放进对话；解绑照网页 | 0.5 |
| 电脑 | 列电脑 / 在线 / 移除（浏览器设置里「连接」） | 共用机器通道（P2-2b 新做） | 在做 | P2-2b | — |

合计「缺」约 48–52 小时写码（多数是同一模板换声明，越往后越快）；与 ADR-027 段 3 的估时一致。

## 12. 防跑偏检查（网页动作 ↔ CC 工具对上号）可行性

- 产品地图已经自动发现网页动作：ERP 81 行 `page-action`（页头按钮）+ 20 行 `nav-page`，Cowork 同类若干；CC 工具 94 行 `cc-tool`（锚点 `tool:<产品>:<工具>`）。
- 做法：`page-action` / `nav-page` 行加一个字段 `cc = ["<工具>"]` 或 `cc_na = "<原因>"`；`scripts/check-product-map.py` 加一项：网页动作行两个都没写就拦（「新加网页功能没配 CC 工具」）；
  `cc` 里写的工具必须真有（锚点存在）。存量 101 行一次补齐（照 §11 表），补不齐的先进基线文件、只许减少（同现有 `untested_baseline.txt` 做法）。
- 只看页头按钮会漏掉列表里的行内动作（如「付款」在单据详情里）：行内动作已在产品地图的 `scan` / `cc-action` 类里的一并登记；后续新加的页面动作照现有锚点发现规则会自动出现。
- 成本：检查脚本 2–3 小时、存量 101 行补字段 3–4 小时、测试 1 小时；机器检查每次 CI 多约 1 秒。不引入新服务、零运行费。

## 13. 结果不明的核对：两产品为什么不同、怎么统一（11-08 只读调查，ADR-027 §6 已定稿）

### 现状（读代码）
- **Cowork**：人去 Express 看了告诉 CC「已入账（单号）/ 没入账」→ `push/application/service.resolve_unknown` → 账本 `ledger.resolve_unknown`：
  已入账就直接记成功（带人报的单号，`resolution: checked_in_erp`），走正常成功收尾；没入账就放开可重推。不经电脑。
- **ERP**：`erp_resolve_push_result` 不收人报的结果，统一 `express.api.retry` → 共用队列 `cc_desktop.jobs.retry`：结果不明的那件**钉回原来那台电脑**
  （`route_device`），那台电脑按单号核对 Express，有就报回单号、没有才写一次（11-02 `erp-cc-chat-push.md` §5）。

### 硬理由（确有）
ERP 的写账回执除了 Express 单号，还带**对方档代码和每行存货代码**（`view.party_code` / `view.line_codes`），ERP 收到后：
- `express.push.link_party` / `link_products`：把这张单随单新建的本地供应商 / 客户 / 商品挂上 Express 实际用的码；
- `masters.queue`：排一次主档同步（库存按 Express 对齐）。
人报单号拿不到这两组码 → 新建的本地档挂不上码 → 下一次主档同步按码再建一条同名档（10-24 验收出过的「写进去后同步回来又多一条」）。
Cowork 不自己养主档（Express 主档只读回读），所以人报单号不缺这一步。

### 建议的一套（两产品同一流程，放共用层）
1. 结果不明 → **先自动钉回原电脑核对**（两产品都这样；原电脑在线时几十秒出结论，带单号和码）。
2. 原电脑核对不了（离线超过约定时间、已退役、MR.ERP 这种没有本机记录的目标）→ 才出「人去账本核对后报单号 / 没入账」，两产品同一张卡片、同一个工具。
3. ERP 人报「已入账」后：记成功，同时登记「待补码」；该公司任一台电脑上线时按单号读回一次补挂码（读回已有的按需请求通道），补完才排主档同步；
   补码前这几个本地新档不参与主档同步认领（防重复建档）。
4. Cowork 第 1 步是新增（原来直接问人）：要先核 Cowork 写账件的本机记录能否按单号核对（`companion` 写账路径同一份，预计可以），不行就 Cowork 保持直接问人。

估时：写码 6–8 小时 + 机器 2 小时；属 ADR-027 段 4（统一代录）。
