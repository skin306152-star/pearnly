# 需求影响评估：CC 代录做成通用（加 MR.ERP 网页）＋ ERP 版 CC 左侧按公司

> 按 [需求影响评估模板](../development/templates/impact-assessment.md) 填写，**交主会话审定后再施工**。
> 分支 `pearnly/mrerp-operator-erp-company`，基线 `343f9b78`（= origin/main，11-02）。本文只写评估，没有改代码。
> 第 0–13 节照模板；另加 §14「通用代录的结构」、§15「ERP 左侧公司的结构」，正文里引用时写「见 §14 / §15」。
> 依据（都读过原文，不凭印象）：
> - 代录现状：[CC 代录 Express 评估](2026-10-26-express-operator-product-评估.md)（§1–§15 与 11-02 各节）、ADR-021（11-02 修订）、
>   [直接科目行](2026-11-02-cc-direct-account-item.md)；代码 `companion/src/companion/express_operator/`（5180 行）、
>   `apps/pearnly_core/pearnly_core/express_operator/`、`frontend/assistant/src/components/operator/`、两产品 `hooks.py` 的
>   `pearnly_express_operator`。
> - MR.ERP 现状：[推送方向](2026-10-19-mrerp-direction.md)、[CC 对 MR.ERP](2026-10-19-cc-mrerp.md)；代码 `pearnly_cowork/push/mrerp/`、
>   `connections/mrerp_*`、`targets/mrerp_*`；MR.ERP 源码只读 `/Users/skin/mrerp-source/src`（用户给的）。
> - 公司主档：ADR-020、ADR-025、`frontend/assistant/src/assistant.tsx` 开头注释、`lib/companies.ts`、`lib/company-panel.ts`、
>   `pearnly_core/assistant/projects.py`、`pearnly_cowork/companies/`、ERP `express/companies.py`、`masters/company.py`。

---

## 0. 需求原文与理解

- **原话**（用户 11-02，经主会话转达）：「我们只是完成了 exp，mr.erp 还未完成，这是一个网页端 erp 系统，我们产品 pearnly 网页端已经支持
  后台导入直写，就差和 cc 一样的打开界面录入了，这个如何设计，这个做成通用，cowork 和 erp 都要，然后再把 cowork 那边左侧的公司搬到 erp
  这边，先按 erp 创建公司，然后左侧再按公司进行任务，cowork 那边已经有了成熟的做好了的，不要重写」。
- **起因**：岳父问（LINE，泰文）「让 AI 把资料录进 MR.ERP 网页版，你爸那边也行吗」。
- **我的理解**，分两块：
  - **（A）代录通用**：现在「代录」只会打开 Express（Windows 桌面程序）一张张录。要加第二种目标：在办公室电脑上由 CC 打开 MR.ERP 网页
    （mrerp4sme.com），登录、选账套、像会计一样在网页表单里一张张录、存、读回核对。Cowork 账号和 ERP 账号登录 CC 都有，入口、流程、
    异常分级、汇报和 Express 代录同一套，只是「目标」不同。
  - **（B）ERP 版 CC 左侧按公司**：Cowork 版 CC 的对话侧栏按公司分组（「公司」「待处理 N」、组头、公司页、输入框下巴的公司 / 月份、
    对话顶栏公司名、右侧会话栏），ERP 版现在是平的「最近」。要把这一套原样用到 ERP：公司就是 ERP 自己的公司主档（`ERP Company`，
    在 ERP 里建，或由 CC 报来的账套自动建），侧栏按公司开对话、开代录任务。复用 Cowork 现成的前端组件与共用层接口，不重写。
- **四格口径**（用户 11-02 补充，经主会话转达；按这张表验收）：ERP 和 Cowork 一样，每家公司按它的账本目标有两种录法。
  对话 = 直写：看不到界面、后台完成；代录 = 打开界面一张张录。对话里直写按公司的账本目标自动走对应那条，用户不用选。

  | 账本目标 | 直写（CC 对话 / 网页，后台） | 代录（实操，打开界面） |
  |---|---|---|
  | Express | Express 直写（Cowork 已有，ERP 已有） | Express 代录（两产品已有） |
  | MR.ERP | MR.ERP 导入直写（Cowork 已有；ERP 第 6 步） | MR.ERP 代录（两产品新做：第 1–4 步 Cowork，第 6 步 ERP） |

  ERP 对话里的直写由第 9 步补齐（ERP 版 CC 现在对话里推不了单）。四格 × 两产品，缺一格不算完成。
- **几个现状事实（影响设计）**：
  1. MR.ERP「后台导入直写」只在 **Cowork**：服务端拿连接上加密存的账号密码登录 MR.ERP，生成 Excel 走导入 5 步，再在列表里回读
    （`push/mrerp/transport.py`）。**ERP 产品现在完全没有 MR.ERP**：ERP 公司只认 Express（`ERP Company.express_enabled / express_book`）。
    所以「ERP 也要」= ERP 先要能把 MR.ERP 当账本（连接、账套、要选的菜单 / 默认值、对方与商品），再谈代录。
  2. MR.ERP 导入能做的只有：赊销、现销、赊购（货 / 费）与三类主档（`push/mrerp/modules.py`；「采购只能赊账」）。网页界面能录的远多于此
    （现购、收付款、退货、日记账……），这正是代录对 MR.ERP 的价值，和 Express 代录「全覆盖」同一个理由。
  3. **MR.ERP 一个账号同时只能在一处登录**：登录时把本次会话号写进 `tbl_useruse.browserid`，每个业务页 `checkuseruse.php` 核对不一致就
    踢下线（源码 `login/checklogin.php:37–71`、`component/php/checkuseruse.php:34`）。服务端直写已为此按「网址 + 用户名」加锁
    （`session_lock.account_lock`）。代录如果和直写、或和会计本人用同一个账号，会互相踢——这是本设计最硬的约束。
  4. MR.ERP 表单是普通网页（`aptran/allform.php` 等，jQuery，存档提交到 `allsave.php`），存档前服务器上什么都不写。和 Express「录第一行
    明细就占号写账、放弃只能作废」不同（**待实测确认**，见 §12）。
  5. CC 桌面版已经带网页内核（QtWebEngine，CC 窗口在用，`companion/packaging/companion.spec:2–4`），窗口按需加载、关掉即退。
  6. 代录宿主本来就是注入式的：`batch.Runner(state, store, engine, session, book, …)`，Express 的引擎、会话、账套读取是传进去的
    （`express_operator/batch.py:138`、`host.py:365–377`）。换目标不用改批次、进度、续录、存证这些。
- **不在本需求范围**（防止顺手扩展）：
  - 不改 Express 代录和任何直写（Express 直写、MR.ERP 导入直写）的现有行为；
  - 不做 MR.ERP 的设置类操作（用户、权限、期间、菜单关联日记账）、报表、月结；
  - 不改识别读法；不改 Cowork 公司主档的数据与接口语义；
  - ERP 版左侧公司**不**带 Cowork 专属的定时任务、文件夹授权、月结台账、记住的选择（这些在 ERP 没有对应能力，见 §15）；
  - 不在服务器上跑浏览器（理由见 §14.2）。

## 1. 现有同类与复用

| 现有 | 路径 | 怎么用 |
|---|---|---|
| 代录批次、进度落盘、暂停 / 停止 / 跳过、断点续录、整批停的原因 | CC `express_operator/batch.py`、`host.py`、`contract.py` | **原样复用**：Runner 本来就注入引擎 / 会话 / 账套读取，MR.ERP 换三样注入物 |
| 存证截图（本机 30 天按批次清单删） | `express_operator/evidence.py` | **原样复用**：截图来源换成网页视图的截取 |
| 多会话并行调度（看 CPU、内存、响应） | `express_operator/scheduler.py` | **复用**：「响应」换成网页最近一次操作的往返时间 |
| AI 接手（经产品站点，密钥不下发） | `express_operator/helper.py`、站点 `express_operator/tools.py` | **复用**：给模型的从截图改为优先给网页文字（更便宜、更准），截图兜底 |
| 本机凭据（Windows 凭据管理器，DPAPI）＋ 原生「授权登录」小窗 | `express_operator/credentials.py`、`login_dialog.py` | **复用**：多一种凭据目标名，小窗同一个 |
| 服务端代录：批次、预检、任务记录、权限钩子 | `pearnly_core/express_operator/`（`products.py` 的 `pearnly_express_operator`） | **扩展**：契约加「目标」，产品钩子多一项「这家公司用哪种账本」 |
| 前端代录工作区（新建任务、开始前确认、进行中、汇报、按公司分组的任务侧栏） | `frontend/assistant/src/components/operator/` | **复用**：目标只影响几处文字和「授权登录」按钮 |
| MR.ERP 登录、选账套、读目录、读主档 / 菜单、对方与商品自建、导入、列表回读 | Cowork `connections/mrerp_session.py`、`mrerp_catalog.py`、`mrerp_master_reader.py`、`push/mrerp/*`、`targets/mrerp_*` | **复用**：开录前的准备（菜单、默认值、对方 / 商品没有就用导入自建）照直写一行不改；ERP 要用时按 ADR-025 选项 C 机械下沉到共用层（§14.6） |
| 公司侧栏、公司页、会话栏、输入框下巴、侧栏分组 / 拖动 / 置顶 | 前端 `assistant.tsx`、`assistant-ui/projects.tsx`、`thread-list.tsx`、`companies.tsx`、`company-page.tsx`、`session-panel.tsx`、`add-company.tsx`、`lib/companies.ts`、`lib/company-panel.ts` | **原样复用**；唯一要改的是把写死的 Cowork 接口前缀改成由站点声明（§15） |
| 共用层「对话挂在哪一项」 | `pearnly_core/assistant/projects.py`（`pearnly_assistant_projects`） | **原样复用**：ERP 声明自己的 provider 即生效；`listing()` 多回两个声明字段 |
| ERP 公司主档、可见公司、按账套自动建公司 | ERP `ERP Company`、`masters/company.allowed_companies`、`express/companies.py` | **原样复用**：公司就是它，不另建表 |

**结论**：两块都是「扩展现有」，不新建平行的一套。新写的只有：CC 端 MR.ERP 执行层（网页驱动、MR.ERP 表单配方、读回）、两产品各自的
「MR.ERP 代录单从哪来」薄接口、ERP 的 provider 与公司接口（只出 Cowork 前端已有的形状）。

## 2. Frappe 原生能力

- 服务端只加白名单接口、DocType 字段和产品钩子；权限用原生角色加服务端校验（同代录现状）。
- ERP 公司的新建、改名、停用仍在 ERP 自己的公司页（Desk 原生表单，`erp.page.company`），CC 里不另做一份编辑表单。
- CC 前端是 ADR-019 登记的 React 例外，照 `globals.css` 标准写法与闸门；MR.ERP 窗口是 CC 桌面版的原生 Qt 窗口（不是 Desk）。
- ERP 若要存 MR.ERP 连接（§14.6），密码用 Frappe 原生 `Password` 字段（站点密钥加密存库，Cowork 的 `Cowork ERP Connection` 就是这样）。
- **结论**：服务端全用原生；没有自定义 Desk 样式。

## 3. 按类通用

- **（A）属于「代录的执行目标」这一类**：现在只有 Express（Windows 程序），本次加 MR.ERP（网页）。抽象按「目标」分（§14.1），以后再加别的
  网页账本（例如 FlowAccount、PEAK）或别的桌面程序，只加一个目标实现 + 配方，不动批次、进度、入口、汇报。
- MR.ERP 单种按量分期，同 Express 代录：
  | 期 | 单种 | 说明 |
  |---|---|---|
  | 第 1 期 | 赊购（货 / 费）、赊销、现销 | 与 MR.ERP 导入直写同四种，能和直写结果逐张对比（对比报告同 Express 第 0 期） |
  | 第 2 期 | 现购、付款、收款 | 导入做不到、界面能录：代录对 MR.ERP 真正多出来的部分 |
  | 第 3 期 | 退货减价、日记账、其他 | 按岳父客户实际单量排 |
- **（B）属于「CC 按公司组织对话」这一类**：Cowork 已有；ERP 本次接；DMS 没有「客户公司」概念，不接（侧栏照旧）。

## 4. 上下游数据

- **单据流**：代录吃的仍是「直写同一份准备」——上游识别 / ERP 单据、要人选的项、记住的选择都不变，只换最后一步的执行方式。
  - Cowork：`push/application/service.py::_prepare_mrerp` 冻结的菜单、默认值、工作空间税号照旧；对方 / 商品在 MR.ERP 里没有的，**开录前在服务端
    用导入自建**（`push/mrerp/autocreate.py`，与直写同一条），对应 Express 代录「开工前建主档复用直写建档」的口径（10-27 ③）；
  - ERP：ERP 采购 / 销售单 → 同一套准备（下沉到共用层后，§14.6）。
- **MR.ERP 映射**：
  - 写入一律经 MR.ERP 自己的网页表单，由 MR.ERP 编号、算税、过账；
  - 存档后在同一个浏览器会话里打开这张单的查看页（`allform.php?…&status=vie`）逐字段核对（表头、明细、合计、税额），再记下 MR.ERP 单号
    （菜单前缀 + 号）；口径同直写回读「在所选菜单列表里查到才算成功」；
  - 结果回到原来的地方：Cowork 回推送记录 / 识别单据的推送状态，ERP 回单据（新增 `mrerp_state / mrerp_docnum`，或与 express 两列合成
    「账本状态」，见 §12 第 6 条）。
- **CC 桌面版载荷**：要变。
  - `payload_contract.py` 载荷版本 +1（现为 16），代录载荷加 `operator.target`（`express` / `mrerp`，没有 = express，旧载荷语义不变）；
    MR.ERP 载荷带：网址、账套（`comidyear` / `seldb` 与账套显示名，用来核对）、单种与菜单号、表头、明细（品码、数量、单位、单价、折扣、
    税率）、合计、默认科目（现销收款槽等）、对方码；**不带任何密码**；
  - 旧版 CC 诚实拒收（`ERR_PAYLOAD_VERSION`）；服务端只把 `mrerp` 代录单派给心跳报了能力 `operator_targets` 含 `mrerp` 的电脑。
- **Cowork / DMS**：DMS 不涉及（DMS 对接的是另一个系统的适配器，`pearnly_dms/adapter`，与本需求无关）。Cowork 的 Express 代录、MR.ERP 直写
  行为不变（§7 列回归）。

## 5. 隔离与权限

- **谁能发起**（服务端判，不靠前端隐藏，与 Express 代录同一判据）：
  - Cowork：工作空间「收单」权限，且能用这条 MR.ERP 连接（`connections` 成员）；
  - ERP：公司在本人可见公司里（`allowed_companies`）且有这类单据的开单权（`express/operator.py::allowed` 同一条）。
- **谁的电脑录**：MR.ERP 是网页，不挑「够得到账套」的电脑——派给发起人选定的那台（默认：在哪台电脑的 CC 窗口里点的「开始」就是哪台）；
  那台要报了 `mrerp` 能力、且这台电脑上存了这条连接的 MR.ERP 登录。ADR-021「谁的活谁的电脑做」不变：电脑关了就排队，不转给别人。
  网页、手机只能发起和看进度。
- **账号互踢（§0 第 3 条）的处理**：
  - 代录用的 MR.ERP 账号**建议专用**（客户在 MR.ERP 里给 CC 另开一个用户）；
  - ~~开录前比对直写用户名、相同就预检拦下~~（11-02 晚取消，改为同账号互斥：直写与代录共用「网址 + 用户名」一份占用，代录占着时直写排队「等账号」，见 §14.4）；
  - 同一个代录账号同一时刻只给一个批次用：站点按「网址 + 用户名」记一份代录租约（随领单续期，电脑不在就过期），第二个批次排队写
    「等账号」；同一台电脑的不同账号可以并行（各用独立的浏览器资料，Cookie 不共享）；
  - 录到一半被踢回登录页（会计本人用这个账号登录了）→ 这一批暂停、**必须人工**，**不自动重新登录**（重登会把人踢掉，来回互踢）。
- **跨公司不串**：每批只录一家公司的一个账套；登录后选账套（`login/mainmenu.php?comidyear=…&seldb=…`），再读页面上的公司名与年度和载荷核对，
  不符整批停（同 Express 的 `company_mismatch`）。
- **凭据**：见 §14.4；服务端、模型、日志、截图都拿不到密码。

## 6. 入口：网页、手机、LINE、拍照

| 入口 | （A）MR.ERP 代录 | （B）ERP 左侧公司 |
|---|---|---|
| CC 桌面版窗口 | 主入口：切到「实操」、选公司（账本是 MR.ERP 的公司）、开始前确认、开始后弹出 MR.ERP 窗口可看可不看 | 侧栏按公司分组、公司页、输入框下巴公司 / 月份 |
| CC 网页（电脑浏览器） | 发起、看进度和汇报；执行在选定的电脑 | 同桌面窗口 |
| 手机 | 看进度、处理「需要你」；不发起整批 | 抽屉里「公司」，同 Cowork 手机端 |
| CC 对话 | 只跳转到代录（同 Express 代录口径，入口分开） | 对话挂在公司下，CC 拿到公司说明 |
| ERP 网页（Desk） | 不另加入口 | 「添加公司」= 打开 ERP 公司页新建（先按 ERP 建公司） |
| LINE、拍照 | 不直接涉及：照旧进识别，代录从待录的单里取 | 不涉及 |

相机共用件不动。

## 7. 共用件与连带回归

`python3 scripts/check-impact.py --files` 按预计改动跑过（基线 343f9b78）：命中 **cc-desktop**（6 个入口）、**cc-desktop-bridge**（4）、
**cc-sidebar-list**（2）、**assistant**（5），入口含 `erp.cc.connect / erp.cc.write / cowork.cc.channel / cc.operator.page / cc.desktop.operator /
cc.desktop.write / cc.assistant / cowork.assistant / cowork.cc-chat.push / cc.operator.thread` 等（完整输出在施工时贴进本任务记录）。

| 共用件 | 文件 | 要改什么 | 回归 |
|---|---|---|---|
| `cc-dbf-write` | `worker.py::_handle_operator_task`、`payload_contract.py` | 按 `operator.target` 分路：`mrerp` 不进 Express 建主档（`operator_masters.prepare`）、不拿 `PEARNLY.LCK`；载荷版本 +1 | Express 代录与直写字节不变（测试锁住）；旧 CC 拒收 |
| `cc-places-heartbeat` | 心跳 | 报 `operator_targets`（有网页内核就有 `mrerp`）与本机存了哪些 MR.ERP 登录（只报连接号，不报账号） | 两种成员心跳照旧 |
| `cc-desktop` | `pearnly_core/cc_desktop/jobs.py`（ERP 通道） | `mrerp` 代录单不按账套可达派、只派给 `route_device`，能力要含 `mrerp` | ERP 写 Express 照旧 |
| （Cowork 通道） | `push/native/service.py` 派单、`push/mrerp/runner.py` | MR.ERP 代录记录走 Cowork 通道派给选定电脑；服务端导入执行器跳过代录记录（`is_operator` 已有） | Cowork MR.ERP 直写照旧 |
| `cc-desktop-bridge` | `lib/desktop.ts`、`tomo_bridge.py` | 「授权登录」带目标（MR.ERP 按连接存）；看 MR.ERP 窗口 / 收起 | 桥其余方法照旧 |
| `assistant` | `pearnly_core/assistant/projects.py::listing` | 回包多 `api`、`sections` 两个声明字段（§15.2） | Cowork 侧栏逐字段一致 |
| `cc-sidebar-list` | 只读用，不改 | ERP 复用 | 对话 / 实操侧栏照旧 |
| （前端外壳，未登记） | `lib/companies.ts`、`assistant.tsx`、`session-panel.tsx`、`company-page.tsx` | 接口前缀与分区改由声明决定 | Cowork 公司页、待处理、会话栏照旧 |
| `express-rules` | 不改 | — | — |

- 同一提交登记新共用件 `cc-operator-target`（CC 端目标分路）与新入口 `cowork.cc.operator-mrerp`、`erp.cc.operator-mrerp`、`erp.cc.companies`，
  每个入口实测记「通过 / 不通过 / 没测到（原因）」。
- 新增冒烟：
  - 离线：**假 MR.ERP 站点**（本机起一个静态站，页面结构按 `mrerp-source` 的四种表单与登录 / 选账套页做，含 `alert`、被踢回登录页、存档失败），
    回放四种单、被踢、断网、续录、放弃；
  - 前端：`tests/test_assistant_ui.py` 加 ERP 侧栏按公司（假站点）；`tests/test_cc_operator.py` 加 MR.ERP 目标三态。

## 8. 存储与成本

- **AI 费用**：常规单不调 AI（同 Express 代录，流程定型后 0 次）；认不出的弹框 / 页面才交模型。网页能直接读出页面文字，先给 Haiku 5.5 文字
  （一次约 ฿0.003–0.01），文字判断不了再给截图；电脑操作接手（Opus 5.5）只在最后一档，上限同 Express（每张、每批都有封顶）。识别费与直写同一份。
- **每张耗时（估算，待第 0 步实测给真数）**：MR.ERP 一张表单约 3–6 次页面往返（打开表单、对方 / 商品选择框的查询、存档、查看页读回）；
  每次往返按 0.5–2 秒粗估（没有实测，第 0 步给真数），**一张约 15–30 秒**（Express 后台做法实测 10–11.6 秒，网页多在网络往返）。
- **电脑资源**：
  - 不录时：零占用（不导入 MR.ERP 执行层，不开网页内核进程）；
  - 录时：多一个网页内核渲染进程（QtWebEngineProcess，估约 100–200 MB 内存，第 0 步实测）；CC 窗口本来开着时共用同一个内核主进程；
  - 录完关窗、释放；空闲 CPU 回到 0。
- **存证截图**：同 Express 代录，本机 30 天，约 150 KB / 张；不上传、不进原件层。
- **固定月费**：不新增。MR.ERP 的用户数是否按人收费（专用账号会不会让客户多付钱）未知，列入 §12。

## 9. 删除、作废、追溯

- **放弃一张**：MR.ERP 存档前服务器上不写任何东西（表单提交到 `allsave.php` 才落库，源码可见；**待第 0 步在测试公司实测确认**）。
  所以放弃 = 不按存档、离开表单，不需要作废；这一点比 Express 简单。若实测发现某些表单中途就落库（例如明细行单独存），那一种单照 Express 的
  「两道作废闸」口径：只处理本张开录后新出现的单号。
- **已存的单错了**：代录不改、不删（MR.ERP 有删除 `delprocess.php` 与取消 `canprocess.php`，一律不自动用）；汇报里标红，人决定；人点确认后
  可由代录用 MR.ERP 自己的「取消」功能处理（第 2 期起，先实测这个功能的效果）。
- **追溯**：每张留发起人、电脑、目标与配方版本、每一步（填了哪格、读回什么）、存档截图、MR.ERP 单号与核对结果、AI 接手摘要——与 Express
  代录同一份批次文件格式，多一个 `target` 字段。
- **删除批次**：批次记录与本机截图一起真删；MR.ERP 里的单不受影响。截图到期同 Express。
- **ERP 左侧公司**：删公司仍在 ERP 公司页（停用为主）；CC 侧栏随站点事件更新。对话不随公司删除（落回「最近」，同 Cowork 口径）。

## 10. 文案与官方 UI

- 原则：沿用代录与公司主档已有词条；新增只写完成操作必需的字，施工时过 `scripts/check-copy.py` 并附 `--report`。
- **预计新增词条（草稿，施工时定稿）**：
  | 位置 | 中文 | 说明 |
  |---|---|---|
  | 实操设置 | 「MR.ERP 登录」 | 与已有「Express 登录」并列 |
  | 原因 | 「同一账号已在别处登录」 | 被踢回登录页 |
  | ~~原因~~ | ~~「与直接写入同一账号」~~ | 11-02 晚取消（同账号改为互斥排队，沿用「等账号」） |
  | 状态 | 「等账号」 | 同一代录账号被另一批占着 |
  | 原因 | 「MR.ERP 页面不同」 | 配方核对不过（对应 Express 的「这种单暂不能录」，若能沿用原词条就不新增） |
- **不改已有文案**；若施工中发现要改已有词条，先把清单贴进本记录给用户看。
- **官方 UI**：
  - MR.ERP 窗口是 CC 桌面版的原生 Qt 窗口，只放网页本身，不加我们的说明文字；
  - ERP 左侧公司全部用 CC 现有组件（侧栏分组、公司页卡片），不新增样式；
  - 「添加公司」在 ERP 打开 ERP 公司页（Desk 原生表单）。

## 11. 测试与验收

| 层 | （A）MR.ERP 代录 | （B）ERP 左侧公司 |
|---|---|---|
| 单元 / 契约 | 载荷版本、目标分路（Express 载荷与执行路径字节不变）、旧 CC 拒收、能力派单、账号租约、同账号拦下 | provider 的 allowed / view / context；`listing()` 声明字段；Cowork 回包不变 |
| 离线回放 | 假 MR.ERP 站点：四种单成功、读回不符、被踢、断网续录、放弃、认不出的弹框交「需要你」 | 假站点下 ERP 侧栏按公司、公司页只出声明的分区 |
| 实验栈 | testcowork（本机实验栈）挂本分支：Cowork MR.ERP 公司建任务、预检、交单、派到电脑 | ERP 实验栈：两家公司、员工只看到自己可见的那家 |
| 真站点 | **只在 MR.ERP 测试公司**（testcowork 已有连接 `CWEC-283edc03…`，test01 · TEST2019）录，每种单 1–2 张，录完列出单号；需要用户授权（§12） | 不涉及外部系统 |
| 真机 | 测试机 / 录制机装 CC 测试包：MR.ERP 窗口看得见、收起照录、锁屏照录、人点进窗口就暂停 | Cowork、ERP 两种账号各登录 CC（网页、手机、桌面窗口）看侧栏 |

- 验收由主会话复核；用户要亲自看：MR.ERP 窗口里 CC 录单的样子（录一张给他看）、ERP 侧栏按公司的样子。
- 不碰任何真实客户数据：MR.ERP 只写测试公司；Cowork / ERP 只用测试租户。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | MR.ERP 代录用不用专用账号（客户在 MR.ERP 里给 CC 另开一个用户） | **用专用账号**；同账号时预检拦下 | 不用专用：代录与直写、与会计本人互踢，只能整段错开 |
| 2 | 真站点测试授权：在 MR.ERP 测试公司 TEST2019 用代录录约 10–15 张测试单（四种单各 2–3 张 + 放弃 / 被踢各一次）；并请用户在该测试公司开一个专用测试用户 | 同意，录完列单号；开用户属账号设置，由用户自己点 | 没有授权只能做到离线假站点，不能给出真耗时与对比 |
| 3 | 登录方式：用户本人在 CC 原生小窗输一次、存这台电脑，之后 CC 自动填 MR.ERP 登录页（同 Express 代录已定口径）；或者每批由人自己在 MR.ERP 窗口里登录 | **存本机、CC 自动填**（与 Express 一致，可无人值守） | 每批人工登录：晚上集中录做不到 |
| 4 | ERP 有没有真实客户要用 MR.ERP 当账本 | 有才做 ERP 这一半（§13 第 5–6 步），先做 Cowork | 没有就只是预留，省约一半工作量 |
| 5 | ERP 接 MR.ERP 时要不要同时有「导入直写」 | **要**：ERP 机器产生的单固定走直写（主会话 10-26 定），只有代录没有直写，ERP 单据就没有默认去处；直写与代录共用同一份准备，下沉后多一步导入即可 | 不要：ERP 的 MR.ERP 公司只能手动交代录 |
| 6 | ERP 单据上账本状态：MR.ERP 另加两列，还是 Express 两列改成通用「账本状态 / 账本单号」 | 改成通用（一列一义，同一张单只会有一个账本）；需要数据补丁把旧列复制过去 | 另加列：页面与筛选多一套，以后再加账本还要再加 |
| 7 | ERP 左侧公司第一批带不带「知识库」「待处理」 | 第一批只带侧栏分组、公司列表、公司页基础卡（名称、税号、分店、账本、账套）、输入框下巴、会话栏；知识库、待处理做第二批 | 一起做：多约 4 小时，要在 ERP 加一张知识库表 |
| 8 | MR.ERP 用户数是否按人收费 | 请岳父问 MR.ERP 厂商 | 专用账号可能给客户多一份月费 |
| 9 | MR.ERP 账号密码在哪输、直写谁来登录（用户 11-02 补充：「mr.erp 这边直写和代录也是一样哦，在 cc 输入账号密码授权，cc 可以自己去登录，做成和 exp 一样，cowork/erp 同步」） | (b)：CC 原生小窗输一次，同一次输入存两处——电脑凭据管理器（代录用）＋经机器通道加密交给站点（直写、目录采集、主档读取用）；不选 (a)「直写改在电脑上跑」，因为直写会变成要电脑在线、整条链要搬进 CC（约 20–30 小时） | 不定：网页仍收密码，与 Express 体验不一致 |

> 第 1、2、4、5、6、7 条我先发主会话定；第 2、8 条需要用户 / 岳父那边动作。

### 12.1 定案（主会话 main-2 11-02 审定，评估通过）

- #1 用专用账号；同一账号的冲突在预检拦下。
- #3 凭据存这台电脑、CC 自动填（同 Express 代录）。
- #4 ERP 这一半也做（用户原话「cowork 和 erp 都要」，不以有没有客户为条件）；顺序：Cowork 先通，第 5、6 步跟在后面。
- #5 ERP 接 MR.ERP 时导入直写一起做。
- #6 改成通用「账本状态 / 账本单号」。条件：单独提交，原生 patch 迁旧数据；Express 链接状态四条规则（`push.linked`、`express_linked_at`）语义不变；
  旧字段的读取方逐一核对，必要时留薄兼容；ERP 列表、筛选、导出在流程里断言。
- #7 第 8 步（知识库、待处理）紧接第 7 步做，不做过渡版。
- #2、#8 由主会话问用户 / 岳父，答复后转来；第 0 步等 TEST2019 授权。
- 现在开工：第 7 步、第 1 步（顺带 ADR-026）、第 5 步，三步各自分支与工作树、同一工作树只一个写者。
- **11-02 晚补充**：#8 作废（MR.ERP 是自家产品，用户原话「mr.erp 就是我们自己的」）；#1 改为「专用用户只作建议、不强制」，原来的「同账号预检拦下」取消，
  改成同账号互斥（见 #9 与 §14.4）。
- **#9 定 (b)**（主会话 11-02 审定）：CC 原生小窗只输一次，同一次输入存两处，一处在电脑凭据管理器给代录用，一处经机器通道加密交给站点给直写用；
  服务端从不把密码往下发。网页上不再收密码，只显示「已授权：用户名 · 电脑 · 时间」，再加一个「在 CC 桌面版中授权」按钮（只在 CC 桌面版窗口里有效）；
  老连接已存的密码照用，不迁移。两个产品一样。
- **第 0 步实况（11-02 19:10 前后）**：没有 owner 账号，改用 test01 已有年度；test01 登录成功，但 TEST2019、TEST2020 进主菜单都报
  `Unknown database`（MR.ERP 服务器上两个年度库已不存在），没写、没改任何东西。已实测「同账号重复登录被踢」：旧会话弹
  「"User" ดังกล่าวมีผู้ Login ซ้ำ จะทำการออกจากระบบโดยอัตโนมัติ」后回到登录页。真录 12 张等有可用账套再做（主会话去问库的恢复）。
- 每步合入前跑本地全闸门与受影响流程；动了 `pearnly_core` 的在生产镜像跑候选离线检查；Express 代录与 Cowork 公司页零变化是硬闸；
  `frontend/assistant` 样式只走 `globals.css`；部署由主会话安排。

## 13. 工作量与排期（按机器耗时）

拆成可独立合入的小步骤；每步自带测试，Express 代录与 Cowork 公司页零变化是每步的硬闸。

| # | 步骤 | 依赖 | 可独立合入 | 预计机器耗时 |
|---|---|---|---|---|
| 0 | **实测探路**（不合入）：CC 测试脚本在录制机上用网页内核打开 MR.ERP 测试公司：登录、选账套、打开赊购表单、填一张不存、读回、关窗；测内存、CPU、收起窗口后页面是否照跑、`alert` 能否截获、被踢回登录页的样子 | §12 第 2 条授权 | — | 2–3 小时（含等录制机） |
| 1 | **代录目标分路**：契约加 `target`、载荷 +1、心跳能力、CC 宿主按目标装配（Express 路径不变，字节一致测试）、站点按能力派单 | 无 | 是（行为对 Express 不变） | 3–4 小时 |
| 2 | **CC 端 MR.ERP 执行层**：网页驱动、登录 / 选账套 / 被踢检测、四种单配方、存档与读回、续录查重、有人在用、存证；假 MR.ERP 站点离线回放 | 0、1 | 是（没有站点派 `mrerp` 单时不会被用到） | 8–10 小时 |
| 3 | **Cowork 接线**：`operator_source` 对 MR.ERP 公司放行、开录前服务端自建对方 / 商品、出 MR.ERP 代录载荷、经 Cowork 通道派给选定电脑、账号租约、导入执行器跳过代录记录；前端「MR.ERP 登录」按钮 | 1、2 | 是 | 5–6 小时 |
| 4 | **测试包与真站点验收**：CC 测试包、testcowork 实验栈、TEST2019 每种单 1–2 张、与直写对比报告 | 3、§12 第 2 条 | — | 3 小时 + 等人 |
| 5 | **MR.ERP 规则下沉共用层**（ERP 要用才做，§12 第 4 条）：会话、目录、主档读取、菜单 / 默认值、方向、Excel 生成、回读解析机械搬到 `pearnly_core.mrerp`，Cowork 只改导入路径，推送载荷与导入文件逐字节相同（测试锁住） | 无（可与 1–4 并行，不同文件） | 是 | 4–5 小时 |
| 6 | **ERP 接 MR.ERP**：ERP 公司选账本（Express / MR.ERP）、MR.ERP 连接（密码加密存）、选账套、菜单 / 默认值按公司选一次、单据准备、导入直写（§12 第 5 条）与代录接线、单据账本状态（§12 第 6 条） | 5、1–3 | 是 | 8–10 小时 |
| 7 | **ERP 左侧公司（第一批）**：ERP provider、ERP 公司接口（只出 Cowork 前端已有形状）、`listing()` 声明字段、前端前缀与分区改由声明决定、ERP 公司变动推站点事件 | 无（与 A 不同文件，可最先做） | 是 | 5–6 小时 |
| 8 | **ERP 左侧公司（第二批）**：知识库、待处理（§12 第 7 条） | 7 | 是 | 约 4 小时 |
| 9 | **ERP 对话直写工具**（用户 11-02 补充、主会话同日同意）：ERP 在自己的 `pearnly_assistant_tools` 声明与 Cowork 同形状的对话工具（按公司列 ERP 单据、要选的项、交直写、推送结果、结果不明核对）；与 Cowork 真正相同的部分（工具形状、结果不明核对、GUIDANCE 通用段）先下沉 `pearnly_core` 两边引用同一份，Cowork 工具行为与输出字节一致由测试锁住；交直写只走 ERP 现有 `express/push`，推送不查重不拦；先 Express，第 6 步后接 MR.ERP | 7–8（公司上下文）；MR.ERP 部分依赖 6 | 是 | 5–6 小时（MR.ERP 部分另约 2 小时） |
| 10 | **MR.ERP 凭据由 CC 授权**（#9 定 b）：CC 原生小窗授权（复用 `login_dialog`），同一次输入存本机凭据管理器并经机器通道交给站点（Cowork 通道与共用通道各一个只收机器令牌的接口，写进连接的 Password 字段）；站点回「已授权：用户名 · 电脑 · 时间」；网页连接设置去掉密码框、加「在 CC 桌面版中授权」按钮（两产品同一组件）；账号互斥改成「同账号互斥」（直写短锁与代录批次租约共用「网址 + 用户名」一把），取消同账号预检拦截 | 1；与 3 同批上线 | 是 | 4–6 小时 |

- **建议顺序**：7 与 0 可以马上开始（互不相干）；然后 1 → 2 → 3 → 4；5 可与 1–4 并行；6 在 5 之后；8 紧接 7；9 在 7–8 合入后，可与 1–4 并行。
- **验收口径**（用户 11-02 补充）：ERP 版 CC 与 Cowork 版同一结构——左侧按公司 → 对话里直写（Express / MR.ERP）→ 同一家公司开代录任务；验收逐项对照 Cowork 版（公司列表、公司页、对话直写、代录入口与工作区），差异列表。
- 合计机器耗时约 47–58 小时（不含等人与等授权）；每步合入前跑本地全闸门与受影响流程；部署批次由主会话安排。
- **新 ADR**：ADR-026「代录：执行目标与边界（Express 桌面程序 / MR.ERP 网页）」——写明入口分开、目标分路、各目标的执行方式、密码只在本机、
  放弃方式、账号互踢规则；第 1 步提交。

---

## 14. 通用代录的结构（A）

### 14.1 目标适配层

```
站点（Cowork / ERP）            共用层 pearnly_core.express_operator（批次、预检、任务记录、权限钩子）
   │ 准备：直写同一份（菜单 / 默认值 / 对方 / 商品）        │ 契约：for_operator(payload, batch, target)
   ▼                                                     ▼
写账队列（Cowork 通道 / ERP 共用通道）── 只派给能力含该目标、被选中的电脑 ──► CC 写账线 worker
                                                                       │ entry_mode=operator
                                                                       ▼
                                             宿主 host：按 operator.target 装配三样注入物
                                     ┌─────────────────────────────┴──────────────────────────────┐
                              target = express（现有，一行不改）                    target = mrerp（新）
                       desk(bg_desk / win32_desk) · engine · session · book      web_desk · engine · session · book
                                     └───────── 共用：batch.Runner · BatchStore · contract · evidence · scheduler · helper ─────────┘
```

- **不改名、不搬家**：包名 `express_operator`（CC 与共用层）保留，免得为改名动几十个文件、牵连 Express 回归；文件头补一句「目标不只 Express」。
  改名如需要，另起纯机械提交。
- **CC 端新增 `companion/src/companion/mrerp_operator/`**，与 `express_operator` 并列，只放 MR.ERP 专有的四样：
  - `web_desk.py`：网页驱动（§14.2）；
  - `session.py`：开窗（独立浏览器资料）、取本机凭据填登录页、选账套、核对公司名与年度、识别「被踢回登录页」、录完退出登录并关窗；
  - `engine.py`：一张单的固定流程——打开表单（菜单号）→ 填表头 → 选对方（选择框按码查）→ 逐行填明细 → 读页面合计与载荷对上才存 →
    存档 → 读单号 → 打开查看页逐字段核对；
  - `book.py`：`find_saved`（续录前在 MR.ERP 列表里按对方单号 + 日期 + 金额查这张存没存，解析口径照服务端回读 `push/mrerp/readback.py`，
    契约测试守两边一致）与 `verify`；
  - `recipes/mrerp-<版本>.json`：每种表单的格子（`txtapmas`、`txtdate`、`txtcode1`…，以源码为准、第 0 步在真站点核对）、存档按钮、
    单号格、查看页地址、已知弹框文字 → 处理方式。
- **宿主改动只有一处**：`host._build_runner` 按 `payload["operator"]["target"]` 选装配函数；Express 装配函数就是现在那段代码原样搬进一个函数。
  `worker._handle_operator_task` 对 `mrerp` 跳过 Express 建主档与 `PEARNLY.LCK`（MR.ERP 是多用户服务器程序，同一账套几个人同时录本来就正常，
  不需要账套写者锁；要互斥的是「同一个账号」，见 §5）。

### 14.2 MR.ERP 网页在 CC 里怎么打开、怎么操作

**选用 CC 已带的网页内核（QtWebEngine），开一个 CC 自己的原生窗口放 MR.ERP 页面。**

- **独立的浏览器资料**：每批一个不落盘的资料（Cookie、缓存只在内存，关窗即无），不和 CC 窗口、用户自己的浏览器共享登录；
- **操作方式**：在「隔离脚本环境」（页面自己的脚本看不到我们的脚本）里运行小段脚本：找格子、填值并触发页面自己的 `input / change / blur`
  事件（MR.ERP 的金额、税额由它自己的 `allform.js` 算）、点按钮、读格子与合计。选择框（对方、商品）按页面原本的查询方式选，不改隐藏值；
- **弹框**：接管页面的 `alert / confirm`，不弹出、不卡住，文字交给配方规则表判断（认得出的按规则、认不出的交 AI / 必须人工）；
- **线程**：网页内核只能在 CC 的界面线程用；代录批次在自己的线程跑，每个动作排进界面线程执行、等结果回来（一个小的转交层），不新增常驻线程；
- **看得见 / 看不见**：窗口默认收起（不抢屏幕），在 CC 实操页点「查看」才弹出；收起、锁屏照录。Chromium 对看不见的页面会降速，
  第 0 步实测收起后计时器与网络是否照常；不照常就把页面设为常驻活动状态（网页内核有这项设置），不行再改为放在屏幕外、不最小化；
- **有人在用**：只有人在这个 MR.ERP 窗口里点或打字才算冲突（窗口上挂事件过滤），停在原地、30 秒没人动或点「继续」再接着；人用别的程序不冲突；
- **轻量**：不录时不导入这个包、不建网页内核进程；录完关窗，渲染进程随之退出；不新增常驻进程、不加轮询（复用写账线领单）。

**和 Express 代录同一观感**（用户 11-02 晚原话「要达到真实界面和 exp 代录那样的效果」；第 2 步施工后对照，分支 pearnly/cc-mrerp-operator，
施工记录 docs/work/2026-11-02-cc-mrerp-operator.md）：

- 结构调整：MR.ERP 页面开在每批一个的子进程里（`companion.exe --mrerp-desk`），不放进托盘进程——10-18 轻量化后托盘不加载网页内核，网页内核
  一旦进托盘就卸不掉、等于常驻；录完关窗，子进程与渲染进程都退出。窗口默认开在屏幕上（不抢前台、不抢焦点），人看得到一格一格地填、一张一张地存。
- 逐项对照（「待第 0 步实测」的两项做不到当场证明一致，在测试站上实测后补结论）：

| 项 | Express 代录 | MR.ERP 代录 | 一致？ | 说明 |
|---|---|---|---|---|
| 录的时候窗口在不在屏幕上 | 代录照常启动 `ExpressI.exe`（普通窗口，在屏幕上，可能在别的窗口后面）；后台发消息，从不抢前台 | `--mrerp-desk` 子进程开一个普通窗口在屏幕上，`WA_ShowWithoutActivating`：不抢前台、不抢焦点；页面里一格一格地填、点 Save、回列表、开查看页，人看得到 | 一致 | 第一版是默认收起，按本条改成开在屏幕上 |
| CC 实操页工作区默认显示 | 任务列表、进度、每张状态、要处理的项（`operator_state` 的批次快照）；不显示实时画面 | 同一个工作区、同一份批次快照（同一个 `Runner.snapshot`，`operator_state` 原样带出），不用改前端 | 一致 | 前端零改动 |
| 「查看」 | 桥 `operator_show` → `Host.show` → `desk.show_main`（Express 主窗口切到最前面）；页面目前没有调这个桥方法 | 同一个桥方法 → `Host.show` → `DeskView.show_main` → 子进程 `show`（MR.ERP 窗口切到最前面）；等批次手上的动作最多 0.5 秒，不卡 CC 窗口 | 一致 | 页面将来加按钮时两种目标同一个调用 |
| 实时画面 | 桥 `operator_frame` → `Host.frame` → `bg_desk` 的 `PrintWindow`（只画 Express 主窗口，被挡也能截）；页面目前没有轮询这个桥方法 | 同一个桥方法 → `Host.frame` → `DeskView.screenshot` → 子进程 `view.grab()`（只画 MR.ERP 窗口）；同样等不到 0.5 秒就这次不给 | 一致 | 截图频率、「只在页面看得见时刷新」现在两种目标都没有（页面没轮询），以后加在页面一处、两种目标共用 |
| 进度 | `Runner` 落盘、`snapshot_of` | 同一个 `Runner` / `BatchStore` / `snapshot_of` | 一致 | |
| 每张存证截图 | 存档后截一张，`Evidence` 存本机 30 天，汇报里「回看」 | 存档并核对后截查看页一张，同一个 `Evidence`、同一个到期清理；登录页不截 | 一致 | |
| 有人在用 | 后台做法：只有人把本批的 Express 切到最前面又在动键盘鼠标才暂停（低级键鼠钩子，排除 CC 自己发的）；锁屏、用别的程序不算；恢复 = 连续 30 秒没人动或点「继续」；状态 `paused / user_active`，证据进汇报 | 窗口部件上的事件过滤：在 MR.ERP 窗口里点、打字、滚轮才算，只是鼠标移过不算（窗口在最前面时移动也算，同 Express 的「切到前面又在动」）；我们只跑页面脚本、不发 Qt 输入事件，所以不用排除自己；恢复、状态、证据、「继续」（`Host.control resume → presence.resume_now`）同一套 | 一致 | 证据多一个 `source: mrerp_window` |
| 锁屏照录 | 后台发消息不经过输入桌面，锁屏照录 | 只跑页面脚本、不碰键盘鼠标，锁屏不影响操作本身；页面始终设为「看得见」防降速 | 待第 0 步实测 | 本机只能在 offscreen 下测（下一行）；真 Windows 锁屏下网页内核会不会把页面当看不见、截图能不能出画面，第 0 步实测 |
| 窗口收起 / 最小化 / 被挡住照录 | 照录（发消息、`PrintWindow`） | 本机实测（offscreen）：窗口藏起时 20 毫秒的页面计时器只走约 0.9 次/秒（网页内核降速），设回「看得见」后 50 次/秒；最小化、放在屏幕外都是 50 次/秒。所以每次加载完、批次每次看页面状态时都把页面设回「看得见」（测试守住：藏起后两秒仍走几十次） | 本机一致；真机待第 0 步 | 真 Windows 上最小化 / 被完全挡住时网页内核另有的「看不见」判定第 0 步实测；不行按评估 §14.2 改成放屏幕外而不是最小化 |

**没选的做法**：

| 做法 | 不选的原因 |
|---|---|
| 驱动系统自带的 Edge（调试协议） | 要另带 WebSocket 库、管另一个浏览器的进程与资料、用户的 Edge 版本不受控；CC 已有同源内核，没必要 |
| Playwright 等自动化框架 | 安装包多数百 MB 浏览器，违背「CC 要轻量」 |
| 在服务器上跑无头浏览器 | 生产内存紧、每个租户都多一份浏览器；也不是「在 CC 里打开界面」；账号互踢问题一样存在 |
| 不开页面、直接按表单格式发请求 | 那就是另一种直写：跳过 MR.ERP 页面自己的计算与校验，页面一改就悄悄错；也不是用户要的「打开界面录入」 |

### 14.3 失败暂停与重试（照 Express 代录已有规则，只列不同处）

| 情况 | 级别 | MR.ERP 的做法 |
|---|---|---|
| 登录失败 / 密码改了 | 必须人工 | 自动登录只试一次，失败停（防锁账号），「需要你：重新授权登录」 |
| 被踢回登录页（账号在别处登录） | 必须人工（整批暂停） | 不自动重登；这张若已点存档，续录前先查存没存 |
| 与直写同账号 | 必须人工（预检拦） | §5 |
| 网络断 / 网站 5xx / 超时 | 先自动等、超时转人工 | 每分钟只探一次登录页；10 分钟不恢复整批暂停（同 -70 口径） |
| 存档后结果不明（点了存档、页面没回来） | 自动后报告 | 续录前按 `find_saved` 查：查到就记单号、不重录；查不到才重录；同一张两次不明转人工 |
| 页面结构与配方不符（MR.ERP 改版） | 这种单暂停 | 「探路」同 Express：新账套 / 新版本第一次先打开表单读格子、不存；对不上报开发补配方 |
| 认不出的弹框 | 规则表认得出自动；否则先 AI（文字）后人工 | 同 Express 三级 |
| 读回不符 | 必须人工 | 不自动改已存的单，汇报标红 |
| 对方 / 商品在 MR.ERP 里没有 | 开录前服务端自建（同直写）；自建失败转人工 | §4 |
| 日期不在期间 / 期间已关 | 必须人工（预检或存档时 MR.ERP 提示） | 不改日期 |
| 疑似重复 | 自动后报告（照录，口径「推送不查重不拦」）；MR.ERP 自己拒收同号就报原因 | 同 Express |

- 写者锁 / 租约：Express 的「CC 被结束后锁不回收」修法（ADR-021 11-02）对账号租约同样适用：电脑在场续期，不在场过期后同一张再派回原电脑、
  按本机批次文件 + `find_saved` 判断，不重录。
- 多电脑：同一账号同一时刻只有一个批次（站点租约）；不同账号、不同电脑并行；一台电脑上的并发由 `scheduler` 按负载放。

### 14.4 MR.ERP 登录凭据（11-02 晚按 #9 定 (b) 改写）

- **谁输、在哪输**：用户本人在 CC 桌面版的原生小窗（`login_dialog`，同 Express「授权登录」）输一次 MR.ERP 用户名和密码。网页（CC 对话页、
  Cowork / ERP 的连接设置）不再收密码，只显示「已授权：用户名 · 电脑 · 时间」和一个「在 CC 桌面版中授权」按钮（只在 CC 桌面版窗口里有效，
  先例：文件夹授权只在 CC 里做，ADR-020）。两个产品同一组件、同一口径。
- **同一次输入存两处**：
  1. 这台电脑、这个 Windows 用户的凭据管理器（DPAPI），一条连接一条（目标名带站点网址与连接号）——代录用；
  2. 经机器通道（HTTPS、机器令牌）交给站点，写进连接上现有的 Password 字段（站点密钥加密存库，不落明文）——直写导入、账套目录采集、主档 / 菜单读取用。
  服务端从不把密码回传给页面，也不下发给任何电脑；另一台电脑要做代录，在那台电脑上再授权一次。
- **怎么用**：代录只有 `mrerp_operator/session` 在确认当前页面就是这个网址的登录页后从本机取出来填，只回成败；直写照旧由服务端会话登录（与今天一样）。
  模型、日志、截图（有密码格的页不截）都拿不到。
- **老连接**：已存在服务端的密码照用，不迁移；在 CC 里重新授权就覆盖。断开连接 / 移除电脑时本机那条一并删，服务端那份随连接删除。
- **账号互斥**（取代原「代录必须用专用账号、同账号预检拦下」）：直写、目录采集、代录可能用同一个账号，按「网址 + 用户名」只给一份占用——
  代录整批持有（随领单续期，电脑不在就过期，同 ADR-021 11-02 口径），直写导入照旧拿现有短锁（`session_lock.account_lock`），代录占着时直写排队显示「等账号」；
  人自己用这个账号登录会把正在跑的踢掉（MR.ERP 一个账号一处登录）：代录暂停、不自动重登，正在导入的按现有规则判结果不明。
  给 Pearnly 开专用 MR.ERP 用户只作建议，不强制。

### 14.5 两个产品怎么选「直写」还是「代录到 MR.ERP」

沿用已定口径「入口分开」：

- **直写**：Cowork 识别单在推送里选 MR.ERP 账套照旧走服务端导入；ERP（接了 MR.ERP 之后）提交单据照旧排直写。机器产生的单固定走直写。
- **代录**：只从「实操」入口进：选公司（这家公司账本是 MR.ERP）→ 列出这个月还没交进写账队列的单（Cowork：等着录入的识别单；ERP：还没写进账本的
  采购 / 销售单，同 Express 代录的 `sources`）→ 开始前确认 → 开始。交给代录的单直写不再接（`is_operator` 已有规则）。
- 一家公司同时有 Express 和 MR.ERP 账套时：任务建在公司上，开始前确认里按单据日期选账套（同 Express「同期两份账套按公司按年度问一次」），
  一个任务只录一个账套（换账套 = 换任务）。

### 14.6 ERP 怎么用上 MR.ERP（不复制 Cowork 代码）

ERP 不能导入 Cowork（ADR-009），所以照 ADR-025 选项 C：

- **机械下沉**（单独提交、不改逻辑）：`connections/mrerp_session.py`、`mrerp_catalog.py`、`mrerp_master_reader.py`、`targets/mrerp_menus.py`、
  `targets/mrerp_masters.py`、`push/mrerp/` 里与产品无关的部分（方向判定、模块表、Excel 生成、预览 / 报告 / 回读解析、对方商品自建）
  → `pearnly_core.mrerp`（文件头写明多产品共用）。Cowork 调用方直接改导入新位置；证据：Cowork 推送单元测试全过、同一批用例生成的导入文件
  逐字节相同。哪些带 Cowork 表（推送记录、连接表）的留在 Cowork，下沉部分只收参数。
- **ERP 自己的部分**（`pearnly_erp_portal.mrerp`，新域）：ERP 公司选账本（Express / MR.ERP）、MR.ERP 连接（网址、用户名、密码加密存）与账套
  选择、菜单 / 默认值按公司选一次（ERP 单据上停下等人选，同 ERP 现在 Express 的做法）、ERP 单据 → 下沉规则的准备、导入直写（§12 第 5 条）与
  代录接线（`express/operator.py` 按公司账本分出 MR.ERP 来源）。
- 这一半依赖 §12 第 4 条：ERP 没有 MR.ERP 客户就先不做，只做到 Cowork。

---

## 15. ERP 左侧按公司的结构（B）

### 15.1 现状

- 前端是**同一份**（`frontend/assistant`，三产品共用）：侧栏是否按公司分组只看共用层 `projects.listing()` 回的 `kind`（`useProjects.kind`），
  `kind = "company"` 就出现「公司」行、按公司分组、公司页、会话栏、输入框下巴；ERP 没声明 provider，所以是平的。
- 但公司页、公司列表、待处理等的数据接口在前端**写死成 Cowork 的**：`lib/companies.ts:18` `const API = "pearnly_cowork.companies.api."`，ERP 站点没有
  这个 App，直接打开就会报错。会话栏卡片（`lib/company-panel.ts`：公司、月份、待处理、知识库、文件夹、定时任务、记住的选择、草稿、输出）
  大半是 Cowork 专有能力。
- 实操侧栏本来就按公司分组（两产品都是），ERP 的公司选择用 `search_link('ERP Company')`（`operator/live-source.ts:600`）。
- ERP 已有公司主档 `ERP Company`（公司名、简称、税号、分店、增值税登记、`express_enabled / express_book`…），本人可见公司由
  `masters/company.allowed_companies` 决定；CC 报来的账套会自动按「税号 + 分店」建公司（`express/companies.py`）。

### 15.2 怎么改（只加声明，不重写组件）

1. **ERP 声明 provider**：`pearnly_erp_portal` 的 `hooks.py` 加 `pearnly_assistant_projects = ["pearnly_erp_portal.masters.cc_projects"]`：
   - `KIND = "company"`，`PERIOD = True`，`SCOPE = False`（ERP 一家公司已是「税号 + 分店」，不再分店）；
   - `allowed`：公司在本人可见公司里、且启用；`view`：公司名与税号（分店不是总公司时带上）；
   - `context`：给 CC 的公司说明（名称、税号、分店、增值税登记、账本是 Express 哪本 / MR.ERP 哪个账套、最近同步），照 Cowork 的转义与字数上限。
2. **共用层 `listing()` 多回两个声明字段**（provider 里的常量，没有就不回）：
   - `api`：这个产品公司接口的前缀（Cowork 声明 `pearnly_cowork.companies.api.`，ERP 声明 `pearnly_erp_portal.masters.cc_companies.`）；
   - `sections`：这个产品有哪些分区（Cowork：全部现有的；ERP 第一批：会话栏的公司卡，以及公司列表、公司页基础卡；会话栏其余卡逐张核对是否依赖 Cowork 接口，
     不依赖的照常显示）。
   前端 `lib/companies.ts` 的 `API` 改为取声明；侧栏「待处理」行、会话栏卡片、公司页各段按 `sections` 显示。**Cowork 拿到的前缀与分区和今天
   完全一样，行为不变**（测试比对 Cowork 回包与页面逐项一致）。不在前端按产品名写分支。
3. **ERP 公司接口**（`pearnly_erp_portal.masters.cc_companies`，只出 Cowork 前端已有的形状，字段是子集）：
   - `companies`（服务端分页、按名称 / 税号搜，只回本人可见的启用公司）、`detail`（公司卡 + 账本 + 账套：Express 从 CC 报来的账套按税号 + 分店
     对上，MR.ERP 从连接对上）、`pending_counts`（第一批回空，第二批接）；
   - **不提供** `create / rename / delete`：ERP 公司在 ERP 里建、改、停用。CC 里「添加公司」在 ERP 打开 ERP 公司页新建（「先按 ERP 创建公司」），
     改名 / 删除菜单项按 `sections` 不出现，公司页给「在 ERP 中打开」；
   - ERP 公司新建 / 改名 / 停用时推 `pearnly.project`（`upsert / delete`）给能看这家的人，侧栏即时更新（共用层 `projects.announce` 已有）。
4. **实操的公司选择**改用同一个公司列表接口（ERP 也走声明的 `companies`），两边一个来源；搜索仍在服务端。
5. **不动的**：对话表、共用层会话字段、侧栏组件、拖动、置顶、手排顺序、实操侧栏；Cowork 的 `companies` 域与接口。

### 15.3 ERP 公司主档与这个左栏的关系

- 左栏的「公司」**就是** `ERP Company` 的一行（编号 = 文档名），不另建主档、不复制数据。员工看到的公司 = 他在 ERP 里能切换到的公司。
- ERP 页面右上角的「当前公司」和 CC 对话挂的公司互不影响：CC 对话按自己挂的公司说话、调工具；实操任务按任务选的公司取单（`express/operator.py::sources`
  已经是这样，10-29 修过）。
- 停用的公司不出现在左栏，挂在它下面的对话落回「最近」（同 Cowork 删公司口径）。

### 15.4 对 Cowork 的回归影响

- 服务端：Cowork provider 加两个常量；`listing()` 多两个键（旧页面忽略）。Cowork 接口一行不改。
- 前端：接口前缀改为取声明、各段按声明显示——对 Cowork 是同值替换。回归：`tests/test_assistant_ui.py` 全过；Cowork 账号打开公司列表、公司页
  （全部卡片）、待处理、会话栏、添加公司、定时任务，逐项与 main 一致（入口 `cowork.assistant`、`cc.assistant`）。
- 风险点：前端某处还有直接写死的 `pearnly_cowork.*` 调用（盘点到 `pending.tsx`、`company-cards.tsx`、`month-ledger.tsx`、`scheduled-tasks.tsx`、
  `settings.tsx` 里有 Cowork 专有接口）。这些段对 ERP 按 `sections` 根本不渲染，所以不改它们；施工时用一条检查列出所有 `pearnly_cowork.` 字面量，
  确认都在 Cowork 专有分区里。
