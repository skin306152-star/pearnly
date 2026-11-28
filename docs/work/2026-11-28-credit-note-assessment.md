# 需求影响评估：收款里的减项与定金（退货 / 减项进收款）

> 状态：**已审定、施工中**（§14、§15）。范围按用户 11-28 原话「先不做，只做收款」收口：只做收款里的减项与定金，
> 覆盖 11-27 晚「还有什么功能没做到退货／减项也可以改写的，明天要做」。基线 `origin/main` = `568f4b7f6`（= 线上 release-20261127-1225，CC 正式版 1.12.25），
> 分支 `pearnly/credit-note-in-collection`。
> 需求来源：主会话 11-28 任务书；交接 `~/pearnly-devtools/handoff-1127/退货减项-纳入主线-1127.md`（起因、四处落点）。
> 前一份评估：[收款评估 11-11](2026-11-11-collection-receipt-assessment.md)（§12 第 4 条「本批不做」由用户 11-27 作废）。
> 原生证据：本机已有的 NLTTEST 只读副本（`~/pearnly-devtools/stock-parity-1120/nbase`，与 `device-1116/t1/rb/rb_NLTTEST` 同一本，
> 11-20 复制）只读解析；**没有连测试机、没有写任何账套**。MR.ERP 以本机只读源码 `~/mrerp-source` 为准。

## 0. 需求原文与理解

- 原话（用户 11-27 晚）：「退货／减项也要加进去，纳入主线，明天要做」「还有什么功能没做到退货／减项也可以改写的，明天要做」。
- 起因：testerp「นิว ลิงค์」客户 NLT6010018 在 Express 开了 SRTEST01（−100），LINE 收款选客户后卡上只有两张发票。
  读取条件 `RECTYP = '3'` 把它筛掉了（不是同步慢）。
- 用户 11-28 定（经主会话）：「先不做，只做收款」。范围：
  1. Express 收款（LINE / ERP 网页 / CC 对话引导，共用一套）：选客户后列出可抵扣的减项单（RECTYP 5）与定金（RECTYP 0），勾了从收款里扣，写 RE 时按原生记法。
  2. MR.ERP 收款：选单页的减债单（`tab-arsc`）也能勾、导入时按 billtype 4 扣。
- 不做（用户 11-28 定，不再排期）：录入推送识别出 ใบลดหนี้ / credit note 照旧转人工；ERP 销售 / 采购单不加「减项」类型；
  赊销退货（SR CNTYP 2）、采购减项（GR）、MR.ERP 减项导入、代录 SR / GR 配方都不做；MR.ERP 预收不做（§1.5）；
  付款 PS 与应付退货抵扣（用户 11-27 定不做）；Express 原生作废 RE；一笔钱付几家客户；支票。
- §1.3、§1.4 的实录（赊销退货、采购减项的原生记法）只留作事实记录，不是待做事项。

## 1. Express 原生记法（实录，NLTTEST 副本）

### 1.1 单据种类（ISRUN）

| 单据簿 | 前缀 | 名称 | 表 / RECTYP | 本仓现状 |
|---|---|---|---|---|
| CN | SR / SE / EC | ใบลดหนี้/รับคืน、ใบลดหนี้-ค่าส่งเสริมการขาย、ใบลดหนี้-FOC | ARTRN RECTYP 5 | 只写 SR 退现金（CNTYP 3，收银跨天退货） |
| DN | GR / KP | ใบลดหนี้/ส่งคืน（采购）、รายได้ KPI | APTRN RECTYP 5 | 没有 |
| AI | AI | รับเงินมัดจำ（定金） | ARTRN RECTYP 0 | 没有（收款读取同样漏掉，见 §3） |
| RE | RE | รับชำระหนี้ | ARTRN RECTYP 9 | 已做，只认 RECTYP 3 |

### 1.2 带减项的收款单 RE（69 张 RE 的 ARRCPIT 里有 RECTYP 5 行，其中 39 张单头还在本年度；另有 20 张带定金 RECTYP 0 行）

| 处 | 原生记法 | 证据 |
|---|---|---|
| ARRCPIT | 减项一行：`RECTYP 5`、`RCVAMT` **正数**（本次抵多少）、`VATAMT 0`；定金同样一行 `RECTYP 0` 正数 | 71 行 RECTYP 5、73 行 RECTYP 0 |
| 单头 IVCAMT | = Σ发票 − Σ减项 − Σ定金（**净额**）；`NETAMT` = 现金 + 方式 = IVCAMT | 39 / 39（减项）、20 / 20（定金） |
| 被抵的 SR / AI | 同发票：`RCVAMT += 本次`、`REMAMT -= 本次`，抵清 `CMPLAPP Y` | 全部是整张抵清，**没有部分抵的样本**（0 张） |
| 净额 > 0 | 照普通 RE：DOCSTAT M、方式行、凭证只记净额（借方式 / 贷应收），ARBAL `RE{期}` 加净额 | 例 RE2400820：IV 12620 − SR 6310 → TB 6310 |
| 净额 = 0（全抵） | DOCSTAT **N**、NETAMT / IVCAMT 0、**没有方式行、没有凭证**、ARBAL 不变、CMPLAPP Y | 29 张，例 RE2400023：IV 5910 = SR 5910 |
| 净额 < 0 | 只有 1 张 2018 年的（RE1803714：只抵一张 SE、CSHRCV −8000、DOCSTAT R） | 不当正常路径 |
| ARMAS.BALANCE | 减净额（SR 开单时已经减过一次，合起来正好平） | 与现有写法一致 |
| **定金不同**（11-28 副本回放查出） | 定金开单时记负债（AI：借银行 / 贷 ISINFO.ACCNUM31，本账套 2133-01），抵扣时转回应收：凭证多一行 SEQIT 4 **借定金科目** Σ定金，SEQIT 6 贷应收 = 净额 + Σ定金；ARBAL `RE{期}` 加、ARMAS 减的也是「净额 + 定金」；净额 0 的定金收款单照样有凭证、DOCSTAT M | RE2400956（IV 3266 = AI 3266：借 2133-01 / 贷 1130-01 各 3266、DOCSTAT M）；7 位定金客户逐月 ARBAL RE 桶 = IVCAMT + 定金 |

### 1.3 SR 的 CNTYP（赊销退货与退现金是同一类单）

| CNTYP | 张数 | 记法 |
|---|---|---|
| 3 退现金 | 90 | RCVAMT = NETAMT、REMAMT 0；凭证贷现金（本仓 `dbf_refund` 已照此写） |
| 2 冲应收 | 137（72 张已被 RE 抵掉、65 张仍挂着） | RCVAMT 0、REMAMT = NETAMT；凭证 **贷应收 1130-01**（不是现金），其余同 CNTYP 3：借退货科目、借存货 / 贷成本，带 STCRD 退回库存（2024 年 88 / 88 张带库存行）；原单 SONUM 指 IV（38）或 HS（49） |
| 1 | 2 | 已作废，不管 |

### 1.4 采购减项 GR（APTRN RECTYP 5，22 张）

16 / 22 张带 STCRD 出库行；凭证借应付（2120-01）、贷存货（1140-02）、贷进项税（1154-02）；没有挂原单的栏（APTRN 无 SONUM）；
付款 PS 的 APRCPIT 里有 36 行 RECTYP 5（应付端抵扣，属于付款，不做）。

### 1.5 MR.ERP（源码 `impfarev/component/importpc.php`）

- 收款导入 Sheet2 一行一张应收：赊销（billtype 2）、增债（3）**加**进合计，减债（`tbl_arsch`，billtype 4）**减**出合计（第 560 行），金额写正数。
- 预收（`tbl_aradh`，billtype 1）在导入里是**加**进合计，不是抵扣——与 Express 定金方向相反，语义待核，本批不纳入（§12-5）。
- MR.ERP **没有减债 / 采购减项的导入菜单**（`src/imp*` 里只有 aptran / artran / arse / farev / fapay 等），录入推送要走网页表单原生保存
  （同已做的调拨 stktrant / 调整 stktranmod 写法），或继续转人工。

## 2. 现有同类与复用

| 要用的 | 现有 | 结论 |
|---|---|---|
| 读未收单 | `companion/express_receivables.py`（`express_ar_open`，只收 RECTYP 3） | **扩展**：同时收 RECTYP 5（CN 单据簿的 SR/SE/EC）与 0（AI 定金），`rectyp` 照原样交；结果版本 1→2 |
| 快照 | `collection/snapshot.py`、`ERP Receivable Snapshot`（已有 `rectyp` 栏） | **复用**，不加栏 |
| 规则 | `collection/contracts.check / allocate / available / reserved` | **扩展**：抵减行 = 负向；合计按净额；预留同样按单号扣 |
| 写 RE | `companion/dbf_receipt.py`（25 张原生重放全对） | **扩展**：ARRCPIT 抵减行、净额、净额 0 分支（DOCSTAT N、无方式行、无凭证） |
| 收款记录 | `ERP Collection Invoice`（docnum / remaining / amount） | **扩展**：加 `rectyp` 一栏（只读，来自快照），金额仍存正数 |
| 三入口 | LINE `integrations/line_collection*.py`、网页 `pearnly-collections`、CC `collection/cc_tools.py` / `cc_guide.py` / `guide.py` | 都走 `collection.service`，**改一处三处生效**，卡片各自加一个标记 |
| MR.ERP 收款 | `mrerp/receivables.py`（`SKIPPED_TABS = arad, arsc`）、`mrerp/push/xlsx/receipt.py`、`collection/mrerp.py` | **扩展**：读 `arsc` 记 rectyp 4；导入表平衡公式按 billtype 4 减 |
| 赊销退货 | `companion/dbf_refund.py`（SR CNTYP 3，载荷 10） | **扩展同一写入器**：加 CNTYP 2 分支（凭证贷应收、RCVAMT 0、REMAMT = 净额），原单可以是 IV 或 HS |
| 采购减项 | 无 | **新建** `dbf_purchase_return`（GR），库存出库与成本照 `dbf_stock` / `stock_costing`，护栏照 `dbf_sales` |
| 识别后防呆 | `express_rules/push/doc_sanity.py` `credit_note`；ERP 采购识别 `purchasing/recognition.py:197` 直接拒收；MR.ERP `routing.MRERP_SANITY_CODES` | 有落点后**改为分流**到减项单，不再拦；没落点的（MR.ERP 直写）保持转人工 |

## 3. 按类通用

- 类别：「**应收 / 应付的抵减单**」。Express 里是 RECTYP 5（CN / DN 单据簿）与 RECTYP 0（定金 AI）；MR.ERP 里是减债 arsc。
- 一起覆盖：Express 收款读写（5 与 0 同一套）、MR.ERP 收款（arsc）、销项减项（SR/SE/EC 任一 CN 簿，按单据簿认、不写死 SR）、
  赊销退货（CNTYP 2）、采购减项（GR，DN 簿）、Cowork 推送（同用 `doc_sanity`，一起改）、CC 直写与代录（同一套判定）。
- 不覆盖：付款 PS 里的应付抵扣（不做）；MR.ERP 预收（导入方向相反，§12-5）；增债 DR（ใบเพิ่มหนี้，本账套簿里有、ARTRN 没有一张，不扩）。

## 4. 上下游数据

- ERP 单据流：收款不改 ERP 销售单；减项单若在 ERP 建（第 3、4 条），它挂原销售 / 采购单，库存流水随单走（同销售、采购的入库出库行）。
- Express：§1 全部以实录为准。读 Express 为准、ERP 不改 Express 里的 SR；写前在 `PEARNLY.LCK` 下逐张重读发票与抵减单，任一张余额不够整单不写（同现有管钱底线）。
- CC 桌面版：
  - `express_ar_open` 结果版本 2（多 RECTYP 5 / 0 行）：服务端按 CC 最低版本只对新版要抵减行，旧版照旧只回发票（同 `docnums` 的最低版本做法）。
  - 收款载荷带抵减行 → **载荷 23**（旧 CC 诚实拒收）；赊销退货、采购减项各占一个载荷版本（或同 23 一起，施工时定）。
  - 接口约定落进 `tests/test_companion_contract.py`；服务端先上、CC 桌面版后发（心跳带最新版）。
- Cowork：`doc_sanity` 共用，推送分流同步改；Cowork 不做收款，不受第 1、2 条影响。DMS 不涉及。

## 5. 隔离与权限

- 沿用收款现有勾选项（收款岗位能看本公司全部，服务端 `collection.access` 判）；减项单录入沿用采购 / 销售勾选项。不新增权限项。
- 抵减行只能是同一客户、同一账套的：服务端查快照、CC 写前再按客户重读，两道都按客户码比。

## 6. 入口

| 入口 | 第 1、2 条（收款） | 第 3、4 条（减项单） |
|---|---|---|
| LINE | 勾单那一步列出抵减行 | 拍 ใบลดหนี้ 识别 → 出减项单卡（同采购 / 销售卡） |
| ERP 网页 | 新建弹窗的发票表格多出抵减行 | 采购 / 销售单加「减项」类型（§12-6） |
| CC 对话引导 | 同一张收款卡 | 同采购 / 销售引导 |
| 代录 | 收款不走代录（11-14 定） | Express 界面录 SR / GR 的配方要实录，排在直写之后（§12-8） |

不涉及相机改动（识别入口不变）。

## 7. 共用件与连带回归

`check-impact.py` 命中 4 个共用件：`express-rules`（20 个入口）、`mrerp-rules`（7）、`cc-request-worker`（3）、`cc-dbf-write`（9）。
必须实测的入口：`erp.line.collection`、`erp.page.collections`、`erp.cc.collection`、`erp.collection.server`、`erp.collection.mrerp`、
`erp.cc.pos-refund`（改 `dbf_refund`）、`erp.cc.write`、`cowork.express.push`、`cowork.mrerp.push`、`cc.desktop.write`、`cc.desktop.requests`。
新调用方（GR 写入器、减项单入口）同一提交补进 `scripts/impact/shared-parts.toml` 与产品地图。

## 8. 存储与成本

不加外部调用、不加识别次数（减项单本来就在识别，只是停下转人工）、没有新增固定月费。快照每张抵减单一行，NLTTEST 级别约多 60–80 行。

## 9. 删除、作废、追溯

- 收款草稿撤回、放行后撤回：同现有；抵减行跟着预留一起释放。
- 写进 Express 的 RE 写错：同现有口径由财务在 Express 原生作废（作废后 SR 余额由 Express 还原，下次读取自然重新出现）。
- 减项单作废：照 `dbf_void` 现有原生作废写法扩展到 SR CNTYP 2 / GR（施工时实录一张原生作废再定）。

## 10. 文案与官方 UI

- 新增文案（直接按官方风格写，四语）：抵减行的类型标记用 Express 原生叫法「ใบลดหนี้」「รับเงินมัดจำ」（zh 减项单 / 定金，en Credit note / Deposit，ja 値引伝票 / 手付金），金额显示负号；
  错误：抵减大于发票（`credit_exceeds_invoices`）。不加说明性文案。
- 可能要改的已有文案：卡片标题「未收清发票」类（若改成泛称）——施工前列清单发主会话，不擅自改。
- 组件：沿用现有 Grid / LINE Flex / CC 卡片，不加自定义样式。

## 11. 测试与验收

| 层 | 覆盖 |
|---|---|
| 离线单元 | 读取：RECTYP 5 / 0 出现、作废与余额 0 不出、服务品现金制照旧不出；规则：净额 > 0 / = 0 / < 0、只勾抵减行、超抵、预留；MR.ERP 平衡公式 |
| CC 离线回归 | NLTTEST 副本按类重放原生 RE：39 张带减项（净额 > 0 的 9 张、= 0 的 29 张、< 0 的 1 张只核拦下）+ 20 张带定金，退回写入前状态同输入重写，逐字段比 ARTRN / ARRCPIT / ARRCPCQ / GLJNL / GLJNLIT / ARBAL / ARMAS；赊销退货重放 CNTYP 2 原生 SR；GR 重放 22 张 |
| 测试机实写 | NLTTEST 写 3 笔（部分抵 1 笔用来核 Express 收不收部分抵、全抵净额 0、发票 + 减项 + 转账），写后在 Express 界面打开核对；SRTEST01（testerp นิว ลิงค์，NLT6010018，−100）做第一条回归样本——写前确认该公司现用账套是测试账套 |
| MR.ERP | MR.ERP 测试账套 写一笔「赊销 + 减债 + 转账」读回 |
| 三入口 | 实验栈 `erp.line.collection` / `erp.page.collections` / `erp.cc.collection` 走到确认；三星真机 LINE 一遍（部署后） |

验收：用户在 LINE 选 NLT6010018 能看到 SRTEST01 并勾上抵扣。

## 12. 要定的事（问主会话）

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | 卡片上抵减行默认勾不勾、金额能不能改 | 列在发票下方、**默认不勾**；勾了金额默认整张余额、可改小（原生没有部分抵的样本，先在 NLTTEST 实测 Express 收不收，不收就锁整张） | 默认勾会让收款员按少的钱收、和手里现金对不上 |
| 2 | 全抵（净额 0）是否允许、要不要选收款方式 | **允许**，不选方式直接确认（原生 29 张，无凭证、DOCSTAT N）；现有「没收到钱就拒」改为只对「只有折扣」拒 | 不允许的话这类单只能在 Express 里做 |
| 3 | 抵减大于发票（净额 < 0）| **拦下**，回 `credit_exceeds_invoices`；要退钱走退货退现金（原生只有 2018 一张） | 放开要写负现金 RE，原生样本不足 |
| 4 | 收款员只报一个总数自动分配时抵减行怎么算 | 自动分配**不动抵减行**，只有人勾的才抵；总数 = 发票 − 勾的抵减 | 自动抵会悄悄改收款员要收的数 |
| 5 | Express 定金 AI（RECTYP 0，同样从收款里扣，20 / 20 张同一记法）一并纳入？MR.ERP 预收？ | Express 定金**一并纳入**（同类、零额外写法）；MR.ERP 预收**不纳入**（导入里是加不是减，语义待核） | 不纳入 Express 定金的客户收款时照样看不到 |
| 6 | 录入推送、赊销退货的 ERP 入口 | 已定：**不做**（用户 11-28「先不做，只做收款」） | — |
| 7 | MR.ERP 减项导入 | 已定：不做 | — |
| 8 | 代录 SR / GR 配方 | 已定：不做 | — |
| 9 | 范围 | 已定：只做收款（Express 收款三入口 + 定金；MR.ERP 收款减债），一次做完 | — |

## 13. 工作量（机器耗时）

| 部分 | 内容 | 写代码 | 机器跑（测试 + 重放 + 实写 + 闸门） |
|---|---|---|---|
| CC 桌面版 | `express_ar_open` 加 `offsets`、`dbf_receipt` 抵减行与净额 0、载荷 23、接口约定、NLTTEST 副本重放 | 2.5 h | 1.5 h |
| 服务端 | 规则、快照、收款记录 `rectyp`、LINE / 网页 / CC 三处卡片、四语 | 3 h | 1.5 h |
| MR.ERP 收款减债 | 读 arsc、导入表平衡、全抵现金记 0；**与 Express 收款同一批做完**（代码已在本分支，只差 MR.ERP 测试账套 实写一笔，约 0.5 h 机器） | 1 h | 0.5 h |
| 实测 | 测试机1 NLTTEST 原生实录 2 张 + 直写 3 笔；SRTEST01 走 LINE（等菜单窗口释放 erp 副本） | — | 1.5 h |

合计约 6.5 h 写代码 + 5 h 机器。CC 桌面版读写口径变了：服务端先上，桌面版随下一版发（版本号发版时由主会话统一改）。

## 14. 主会话审定（11-28）

- 第一批（第 1、2 条收款）可以施工。§12：1、2、4 照建议；5 Express 定金一并纳入，MR.ERP 预收**未做**；7、8、9 照建议。
- 3 改：不永久拦。第一批在 NLTTEST 用原生 Express 实录一张净额 < 0 的收款单：Express 收且记法逐字段对得上就照做；Express 自己不收或对不上才拦下，原因写进本记录。
- 6 用户 11-28 定「先不做，只做收款」：范围收口见 §0，不再排期其它部分。
- 施工：三入口同一套；CC 桌面版约定进 `tests/test_companion_contract.py`，服务端先上、桌面版随下一版发，旧桌面版遇到新服务端不能出错；新文案按官方风格直接写，改已有文案先列清单给主会话。
- 测试机1：实写 / 原生实录先报主会话排时间（今天排在库存窗口后，19:30 前释放）；离线回放、实验栈不用等。

## 15. 实施状态（11-28，分支 `pearnly/credit-note-in-collection`）

- 代码：CC 桌面版 `8379a23fd`、服务端与三入口 `6fd6408e6`、审查修复 `600624e18`，之后的提交见分支。
- 兼容：读取参数 `offsets` 只派给最低版本 1.12.26 起的 CC 桌面版（`PARAM_MIN_COMPANION`，发版时与带它的那一版 VERSION 对齐）；
  够得到的电脑都还旧时服务端退回不带 `offsets` 的读取，照旧只列发票、照常收款；旧版的读回不删快照里已有的抵减单。
  带抵减单的收款载荷 23 只交给报过支持 23 的电脑；不带抵减单的照旧 20。
- 本批放出范围：**Express 收款三入口（LINE、网页、CC 对话引导）带减项单与定金**，含部分抵扣、正好抵平（净额 0）、
  抵减大于发票（净额 < 0，差额退现金；主会话 11-28 定本批放开）。CC 对话引导的「正好抵平（净额 0）」已由共用引擎
  「无需分摊」标记补上（`steps.NOTHING_TO_SPLIT`），三入口一致。
- MR.ERP 收款减债（arsc）**本批不放**（主会话 11-28 定）：代码与离线测试保留，用户可见行为同现在（减债页签不列）。
  开关是代码常量 `mrerp/receivables.py · OFFSETS_RELEASED = False`（不做租户开关、不进站点配置），测试守着「现在不列」。
  放开条件：有 MR.ERP 管理员权限的人在 MR.ERP 测试账套给菜单 124（减债）关联账簿并开一张减债单，按 §11 实写「赊销 + 减债 + 转账」读回通过后改常量。
  产品侧结论：减债菜单没关联不影响收款——收款走的是收款菜单（impfarev），减债单只在选单页按客户列出。
- Express 原生实录（69EXP\TEST「9.บริษัท Pearnly จำกัด」，11-28，逐步整本快照比对）：
  - SR0000001（ด001，挂未收 IV0000033 535）：Express 保存即自动抵（CNTYP 1），发票随之收清，不进收款。
  - SR0000002（นาย ทดสอบ，挂已收清 IV0000001，减价 100 + 税 7）：提示 4023 后选「留到收款时抵」→ CNTYP 2、RCVAMT 0、
    REMAMT 107、CMPLAPP N、DOCSTAT N；凭证贷应收 107；客户余额 −107。
  - RE0000033 **部分抵扣**：IV0000002 收 1000 + SR0000002 抵 50 → 现金 950。ARRCPIT 5 行 RCVAMT 正 50；SR RCVAMT 50 / REMAMT 57，
    CMPLAPP 仍 N；单头 NETAMT = IVCAMT = CSHRCV = 950、DOCSTAT M；凭证借现金 950 / 贷应收 950；客户余额 −950、ARBAL RE11 +950。
    与分支写法逐栏一致（抵清才 CMPLAPP Y，没抵清不动），减项单部分抵扣照原生放开。
  - RE0000034 **净额 < 0**：IV0000020 收 20 + SR0000002 抵剩余 57 → Express 接受现金 −37 并保存：NETAMT = IVCAMT = CSHRCV = −37、
    DOCSTAT M；凭证借应收 37 / 贷现金 37（金额正数、借贷反向）；客户余额 +37、ARBAL RE11 −37；SR 抵完 CMPLAPP Y。
    即 §12.3 有原生证据，本批照它放开（见下「净额 < 0」）。
  - Express 收款窗在「执行」后先把 RE 存成未完成（DOCSTAT R、REMAMT = 应收），填付款方式再存才变 M——这是原生的两步，不影响直写。
  - 这几张单留在 TEST（SR0000001、SR0000002、RE0000033、RE0000034），已登记占用表。
- 发现的原有问题（不是本分支引入，未修）：CC 桌面版 `dbf_fields.audit_fields` 给 CC 写的赊销减项单也写 CMPLAPP 'Y'，原生未收的
  是 'N'，于是 Express 自己的收款窗选单时看不到这张（提示 4045「没有未收单据」）；我们自己的读取按 REMAMT 认，不受影响。
- 连带回归（5 个共用入口）：control.assistant、cowork.assistant、cowork.cc-chat.push、cowork.cc.troubleshooting、cc.operator.thread
  由列车回归覆盖（流程 id cowork.cc.guide.purchase、cowork.cc.guide.pending、cowork.cc.send-unblocked、cc.operator.replay、
  cc.operator.stop、cc.chat、cc.companies、erp.cc.guide.collection、erp.cc.typed-collection；control.assistant 没有流程），不是本窗口实测。
- 净额 < 0（11-28 主会话定本批放开）：
  - CC 桌面版写入器：现金只在净额 < 0 时可为负；单头 NETAMT = IVCAMT = CSHRCV = 净额；凭证 SEQIT 2 贷现金、SEQIT 6 借应收（正数）；
    ARBAL 加负数、ARMAS 加回。转账退（原生 RE0000035）：方式行 RCVAMT 负数、单头 CHQRCV = CHQPAS 负数、BKTRN 负数、凭证 SEQIT 2
    贷银行科目；支票退负数 Express 自己拒收（Message 13「ยอดที่ใส่ต้องไม่ติดลบ หรือเท่ากับศูนย์」），服务端本来就不列支票。退款里夹正数行
    载荷不合格。载荷仍是 23（23 本批新加、还没有发布的桌面版认它），不加版本号。
  - 服务端规则（三入口同一套 `collection.contracts.check`）：净额 < 0 时现金、转账可填负数（合计等于净额），什么都不填按现金退；
    夹了折扣或正数回 `offsets_exceed_invoices`；账套没有现金科目回 `cash_account_missing`。
  - 入口：网页在转账格填负数即转账退；LINE 与 CC 对话引导默认现金退（不问方式，同正好抵平），要转账退走网页。
  - LINE：勾完发票直接出确认卡（现金 −xx、合计 −xx，现有金额格式）；网页：现金不填即可；CC 对话引导：方式那张卡应分为负，
    交空的分摊（共用引擎「合计不是正数可交空」，只有收款用分摊题），复核卡列现金 −xx。没加新文案。
  - 回放（分支写入器在 TEST 快照副本上重写，与 Express 原生逐表逐字段比，只差时间、操作员、单号备注）：s4 → RE0000034（净额 −37）、
    s2 → RE0000033（部分抵 950），ARBAL / ARMAS / ARRCPIT / ARTRN / GLJNL / GLJNLIT / ISRUN 七张表全一致。
  - NLTTEST 里唯一一张原生净额 < 0（RE1803714，2018）是未完成单（DOCSTAT R、NETAMT −8000 ≠ IVCAMT −15463.92、无凭证），不当样本。
  - 13:14 用代录引擎 `Session.ensure_open` 重开 69EXP（凭据管理器，69EXP 首次验证能登）原生补录：SR0000003（留到收款时抵 107）、
    RE0000035（转账退 −20，Express 收）、支票退 −20（Express 拒收 Message 13，该单改成现金退 −20 = RE0000036）。
  - TEST 真写（分支写入器，持 PEARNLY.LCK）：RE0000037（转账退 −20）与原生 RE0000035 逐表逐字段一致（只差被收的发票号），
    RE0000038（现金退 −7）与原生 RE0000036 只差金额；客户余额 +27、RE 期桶 −27；Express 界面打开两张都正常、无「ยอดเงินไม่เรียบร้อย」。
- 未做 / 待定：
  - MR.ERP 减债：见上，本批不放。
  - MR.ERP 预收：不做（§1.5）。
