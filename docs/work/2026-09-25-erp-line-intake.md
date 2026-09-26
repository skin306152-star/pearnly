# ERP 测试环境与 LINE 收单迁移（2026-09-25）

**范围状态：LINE 收单已上线（2026-09-26）；第 7 节 LINE 绑定即登录进行中。** 测试环境保留数据待复用；真机验收以用户确认为准。 本记录管两件事：① 在 Windows 测试机为 ERP 起测试环境并把测试租户
`testerp.pearnly.com` 切过去（已完成，见第 1 节）；② 用户在真机验收中要求把 ERP 的 LINE 收单
迁回旧线上的业务逻辑，并按用户 2026-09-25 的新口径调整（方案见第 3 节，分批实施）。

## 0. 基线与归属

- 仓库 `pearnly`，任务分支 `codex/erp-testenv`，工作树
  `/Users/skin/pearnly/.claude/worktrees/strange-swartz-a8b667`；开工基线远端 `main` = `f033c9c`。
- 集成：`513d296`（修复主线镜像构建的 ERP LINE 测试）已按主线规则快进进 `main` 并推送，远端回读一致。
  之后的提交在分支上，测试环境验证后再合。
- 交接保留、不属于本任务：`/Users/skin/.codex/worktrees/cowork-frontend/pearnly`（Cowork 未提交工作）。

### 迁移线上基准（LINE 收单）

- 旧 ERP LINE 最后承接线上流量的版本：`pearnly-app@ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`
  （依据 [ERP / POS 迁移记录](2026-09-21-erp-pos-migration.md) 19–22、263 行的 100% 流量 revision
  `…-ec2e5c957e6b-s3`、镜像 `sha256:178af2ed…`，以及 [旧云退役记录](2026-09-24-legacy-cloud-retirement.md)
  13、20 行退役前复核未变；旧系统已删除，不会再变）。
- 本机源码 `/Users/skin/Developer/Pearnly/pearnly-app`（HEAD `9aa7a6b2`，比基准多 2 个提交，只改
  dependabot 与部署状态文档，LINE 相关文件与基准零差异）作为只读参考。
- 无法回读的旧运行配置：`LINE_ERP_LIFF_ID`、官方号 ID、灰度名单（Secret Manager 与库已删）。
  「旧 ERP 通道就是 ERP-TEST `@063eadty`」为推断，不作为证据。

## 1. ERP 测试环境（已完成）

- 测试机只在用户确认 DMS 窗口不用时启用；ERP 栈 `pearnly-testenv-erp` 第一次建立：卷
  `pearnly-testenv-erp_{database,queue,sites-erp,sites-billing}`，站点 `erp.test.localhost`、导入的
  `testerp.pearnly.com`（租户 T-2026-0283，286 表，14 个文件）与账本站 `billing.internal`。
- 口令：用原 windows-testenv 那份（`~/.config/pearnly-testenv/testenv-secrets`，09-24 生成，DMS 窗口
  复制保存）；测试机旧环境文件口令不同，但当时无卷，新库以这份初始化，前后一致，未重新生成。
- 镜像：`main@513d296` → `sha256:0a902f56…`（第一版），分支 `34f4751` → `sha256:7cdfebd5…`（当前）。
- 域名：用户授权后 `switch-lab erp`，`testerp.pearnly.com` CNAME → 隧道；带唯一标记的公网请求出现在
  测试机 `erp-frontend` 访问日志。**当前仍切在测试机**，测完 `lab-stop erp` 切回。
- LINE：用户授权后把 ERP-TEST（频道 2011288286，`@063eadty`）Webhook URL 从退役的
  `https://pearnly.com/api/line/erp/webhook` 改为 `https://testerp.pearnly.com/api/method/pearnly_core.line.webhook`，
  读回一致，Verify 成功（当时由线上 testerp 接收）。
- 驱动缺陷（已修，`34f4751`）：`lab-start` 不拉镜像；每次整目录搬 `/root/te-export/*`（含别的产品与旧批次）；
  账本导入落到默认 dms 栈；每次都重新导出导入。现为默认复用、只取本次导出、账本跟随本产品栈、
  口令只认 `~/.config`。复用路径实测换镜像 112 秒。
- 主线镜像构建曾失败（`test_line_intake` 未随「按人开 LINE」更新），`513d296` 修复；另一次 DMS 隔离站点
  用例 `test_confirm_real_queue_service_ledger_receipt_and_duplicate` 失败一次、重跑通过，判为偶发。

## 2. 真机验收已发现的问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 个人信息页绑定后弹「日期…必须采用格式」 | `bound_at` 带微秒给 Datetime 控件 | `8b3deea` 修复，测试机已换 |
| 管理员给自己勾 LINE 报「没有权限」，没勾也有二维码 | 管理员默认拥有全部，服务端拒改本行，但界面给了可勾空框 | `13521b2`：本行全部勾选、灰色只读（含之后新建的公司） |
| 点菜单 ซื้อ/ขาย 无反应 | 旧富菜单 postback `a=mode:purchase|sales`，新处理器不接 postback | 第 3 节迁移 |
| 第一次发绑定码 434905 测试机回空 400，无事件记录 | 未定位（非验签失败：验签失败有 Error Log）；第二次同样操作成功 | 未复现，继续观察 |
| 发单据后网页看不到 | 新实现只存 `ERP LINE Intake` 草稿，需在采购页「LINE」里人工确认 | 第 3 节：改为 LINE 内入账，去掉草稿与列表 |

## 3. LINE 收单：用户定稿的业务逻辑（2026-09-25）

来源标记：〔旧〕照旧线上 `ec2e5c95`；〔定〕用户本次口径（优先于旧行为）。

1. 开通与绑定（已实现）：团队管理按人勾 LINE，管理员默认全开；绑定码只在本人个人信息页；取消勾选即解绑。
2. 〔定〕富菜单**按权限**：只有采购岗位只显示 ซื้อ，只有销售只显示 ขาย，两者都有显示两个；绑定、解绑、改岗位时挂对应菜单。
   〔旧〕手打 เมนู/menu 回菜单，1/ซื้อ、2/ขาย 等同点菜单；无岗位回无权限。
3. 〔定·09-26〕选方向后先问「上传识别 / 手动录入」：手动录入是快捷回复的链接按钮，直接打开网页端原生空白新建页
   （不多发消息、不走下面各步）；上传识别再依次用 **LINE 原生快捷回复按钮**（不做卡片）：
   ① 公司（本人有权限的公司；只有一家自动选中跳过）→
   ② 供应商（采购）/ 客户（销售）：「按票面识别」、兜底按钮（〔定·09-26〕采购「一般供应商」统一记到同名供应商、
   销售「散客」不建客户档案，都不用票面对方信息），其后为这家公司最近用过的 10 个 →
   ③ 已付 / 未付；已付再选付款方式（网页现有付款方式类别，付款日期取单据日期）→
   ④ 商品类别：服务（不动库存、不进收发存）/ 库存商品（采购自动入库、销售自动出库）→
   ⑤ 仓库：沿用网页端默认仓规则；定不下来（多仓且无收银台指定）才弹该公司仓库按钮 →
   ⑥ 提示上传 PDF 或图片。每步按钮末尾有「取消」；选项保留 30 分钟，过期从头来。
4. 〔旧〕收到图片/PDF：后台队列 OCR，识别中显示加载动画；按同一组选项逐张排队，每张一张预览卡。
   识别失败回「识别失败请重发」，选项保留。〔定〕不做票面匹配公司，任何公司的票都能录。
5. 〔旧版式、定内容〕预览卡：采购、销售颜色区分；内容 = 已选主档信息 + 识别字段。
   〔定·第二版〕卡上**只有「编辑」**：没有确定入账 / 丢弃、不过期、不发回执（省官方号消息额度）。
6. 〔定·第二版〕点「编辑」在 LINE 内置浏览器打开**网页端原生**的采购 / 销售新建页
   （`/desk/pearnly-purchase/line/<记录>`、`/desk/pearnly-sales/line/<记录>`），识别结果与 LINE 里的选择已填好，
   字段照原生页可改；按 LINE 里选的公司打开（当前公司不同就先切过去）。**登录用方案 A**：LINE 内置浏览器
   没登录就先走正常登录页，不新增登录通道（用户 2026-09-25 选 A；LINE 身份自动登录如需另立项）。
7. 点「提交」才入账过账，走 `integrations.intake.submit` → 网页端同一个 `entry.post`（公司、源文件以卡片为准）；
   采购直接过账、销售直接开出；供应商/客户、商品匹配不到按网页端规则新建，销售匹配不到商品另按 LINE 口径新建、
   允许负库存；重复单据放行（含同一文件）；库存走同一条过账路径，收发存按现有功能关联，服务类不关联库存。
   提交后再点「编辑」转去那张单的查看页，不能再改；同一张卡只入一次。
8. 〔定〕不回执。
9. 〔定〕不是草稿：没提交的单据网页端列表里看不到，只能从卡片进；删除采购 / 销售页顶部「LINE」待确认列表。
10. 网页端沿用「当前公司」规则，多公司用「公司 >」切换。

### 授权（用户 2026-09-25「统一授权 / 同意授权」）

- ERP-TEST 官方号 `@063eadty`：新建按权限的富菜单并按用户挂载、这项功能需要的 LINE 后台设置（含 LIFF 入口）。
- 人工验收时的付费 OCR（测试机 `/opt/pearnly/secrets/ocr.json` 为线上真凭据）；不进自动回归。
- 范围仅限测试官方号与测试租户；正式租户与正式官方号不动。旧菜单 `pearnly-erp-v1` 保留不删。

### 架构

- ERP 的 LINE 编辑页沿用 DMS 的 LINE 一次性票据编辑器做法（Vue + Frappe UI，`frontend/`）；
  依据用户「编辑框参考 DMS 的 LINE 编辑框」，补登 ADR-005 例外范围。
- 第一版（`a19a1b5`，卡上确定入账 / 丢弃、10 分钟、文字回执）未上测试机即按用户第二版口径改写；
  不再需要单独的 LINE 编辑页（ADR-005 例外不扩展），编辑就是网页端原生页。

### 实现落点

| 能力 | 落点 |
| --- | --- |
| 分派：绑定、菜单、步骤、收单 | `integrations/handlers.py` |
| 录单前的选择（原生快捷回复、30 分钟、取消、默认仓） | `integrations/line_flow.py` |
| 收件 → 共用 OCR 队列（`erp.line_intake`）→ 卡片；打开 / 提交；排队超时清理 | `integrations/intake.py`（hooks：`pearnly_ocr_handlers`、`pearnly_ocr_failure_handlers`、每小时 `expire_stale`） |
| 只有「编辑」的预览卡（Flex） | `integrations/line_cards.py` |
| 网页端接口 | `integrations/api.py`：`api_open_line_intake`、`api_submit_line_intake`（均 POST，只认发件本人） |
| 网页端原生页入口 | `public/desk/purchase.js`、`sales.js`（`line/<记录>` 路由）、`*-form.js`（提交走上面的接口） |
| 按权限的三套富菜单（图由旧菜单改出，按钮数据仍是 `a=mode:*`） | `integrations/line_menu.py`、`public/images/line-menu/` |
| 销售明细匹配不到商品时新建 | `products/sales.py`（只给 LINE 收单用） |
| 重复放行 | `purchasing/entry.py`、`sales/entry.py` 的服务端专用参数 `allow_duplicate` |
| 删除网页「LINE」待确认列表、旧草稿接口、识别里的草稿分支 | 删 `*-intake.js`；`purchasing|sales/api.py`、`*/recognition.py` |

旧线上「กรอกเอง（手填）」用户新流程未要求，不迁。

### 09-26 真机反馈后的修正

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 发单据后回「识别失败」 | 原判断「按文件地址取到了上一张单那份」→ 新证据：`bf05ce3` 已按编号取仍失败，本单自己的 File 被挂在了收单记录上 → 当前结论：收单记录的 `source_file` 是 Attach 字段，Frappe 保存时把被引用的 File 自动挂到记录上（`attach_files_to_document`），识别的 `files.upload` 只接受本人未挂出的上传，于是判无权 | `9508cf4`：`source_file` 改 Data（`source_file_name` 保留，按编号取）；回归测试禁止该表出现 Attach 字段 |
| 失败提示是中文 | 后台识别以账号身份运行，用了账号的个人语言（zh），对话其余部分是官方号语言（th） | LINE 里的话统一用站点系统语言（`channel_language()`） |
| 公司页右下角收银台表格跑偏 | 编辑页付款方式那一排 4 列后没分段，表格落进最后一列 | 表格前补「收银台」分段（与新建页一致） |

### 09-26 第二轮真机反馈（接手窗口 f58107）

原件复核：`LI-00054`（采购，横拍 NBC 税票）→ `PUR-2026-00059`；`LI-00046`（销售，Sister Makeup 小票）→ `SD-00050`。

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 点「编辑」进页面停在「上传识别」，只有图没字段 | 编辑页收到了 `intake`，但没放进页面状态，`*-entry.js` 判不出「LINE 来的、已识别」 | `a2308dd` 把 `intake` 放进状态，采购、销售都改 |
| 采购新建商品用的是票面货号（110814SX001），销售是本系统续号 | `products/purchase.py` 优先用票面 `code` | `a2308dd`：新建一律 `_allocate_code()`，票面货号只用来匹配 |
| 收发存「单据编号」显示「期初」 | 不是编号问题：流水号是 `PE2569-000001` / `SI2569-000001`；收发存页默认只看本月，这两张单是 5 月的日期，被并进了期初行 | 不改；把日期往前调就能看到 |
| 采购合计 84,816.00，票面 67,852.80 | 票面行折扣印成「20.00%」，本单折扣字段是金额，被整个丢掉 | `2e20c84`：百分比按行 / 整单金额折成钱 |
| 采购商品名全是编的（票面「เอสเอ็มเอ็กซ์อาร์ …คุชชั่น」） | 照片侧着。实测同一张横拍读 3 次出 3 组不同的假名字，转正后读对 | `2e20c84`：提示词要求照抄；照片自带 EXIF 方向就在本地转正。用户 09-26 定：**不为转正多花识别费**，不做「模型报角度再读一次」，改为上传前提示拍正（`9c9687a`：LINE 上传那一步与网页上传识别都加了提示） |
| 销售合计 523.23，小票 489.00（VAT Incl） | 新平台销项丢了旧线上的 `price_includes_vat`（控制器读了，DocType 没字段） | `2e20c84`：补回字段与网页「价格含税」勾选；识别读出含税就勾上 |
| 销售付款日期记成当天 | 网页表单默认今天，LINE 没预填 | `2e20c84`：已付时付款日期 = 单据日期 |
| 散客存成中文「散客」 | 兜底名按打开网页那个人的界面语言翻译 | `2e20c84`：存进数据的兜底名用站点语言 |
| 卡片不是旧格式 | — | `9c9687a`：照旧卡片版式与 19 个字段顺序，加 LINE 里的选择，**全部商品**逐行列出；只有「编辑」 |

用户 09-26 新增：
- **LINE 语言跟手机**（`c027c4e`）：回复、快捷按钮、卡片用 LINE profile 的 `language`（缓存 1 小时），取不到退回站点语言；存进数据的名字仍用站点语言。
- **富菜单加「盘点」、按语言**（`e4e56a4`）：右上格盘点，点开网页端原生 `/desk/pearnly-stock-count`（有 `count.write` 才显示）；无权限的格子只画锁、不写「敬请期待」。格子由 `scripts/render-line-menu-tiles.py` 用镜像里的开源字体预先画好，发布时按「语言 × 权限组合」（4 × 7 = 28 套）拼图；每个事件核一次，语言或岗位变了就重挂。旧的三套泰文 `pearnly-erp-{both,purchase,sales}-*` 不删（新发布后不再挂）。

### 09-26 第三轮真机反馈

| 单 | 现象 | 原因 | 处理 |
| --- | --- | --- | --- |
| `LI-00063` 采购（กิจสยามยานยนต์ 税票） | 用户未报问题 | 原件复核：单号、日期、供应商、税号、地址、3 行、含税、合计 7,500 全对；新建商品 `P-000008…10` | — |
| `LI-00072` 销售（小票 02000137） | 金额全没出来 | 小票只印「数量 + 金额」，没有单价；提示词要求「不许自己算」，单价全给 0 | `0fa74d0`：识别同时读行金额，单价 = 票面行金额 ÷ 数量（采购同样处理）；收据号读出来显示在卡上（本单没有外部单号字段，不入库） |
| `LI-00073` 采购（又发那张横拍 NBC） | 识别很久；卡片不是手机语言 | 第一次调模型被 Google 限流（429），队列推迟后重试，共 1 分 38 秒；重试时语言回成了泰文（依赖缓存 / 临时问 LINE 不稳） | `0fa74d0`：收到时把手机语言记在收单记录（新字段 `language`），识别与回卡只用它；LINE 语言没问到不再缓存。商品名仍是编的：照片侧着且无 EXIF 方向，按 09-26 口径不花钱转正，待用户定（见下） |
| 卡片 | 明细那块有底色 | — | `0fa74d0`：去掉底色 |
| 收发存 | 编号仍显示「期初」 | 流水编号正确（`PE2569-000002/3`、`SI2569-000001`）；收发存默认看本月，5 月的单据并进期初行 | 未改，待用户定默认日期范围 |

待用户定：① 侧拍照片要不要在本地免费识别方向（需在镜像里加 tesseract 方向检测，属于底座改动）；② 收发存默认日期范围（本月 / 本年 / 全部）；③ 销售收据号要不要存（需加字段）。

### 09-26 OCR 迁移遗漏排查与第一批补回

三路只读排查（旧线上 `pearnly-app@ec2e5c95` 的 ERP LINE 收单与网页上传识别、新平台现状），运行配置依据
[OCR 运行策略取证](cowork-backend/ocr-runtime-policy.md)：旧线上发票为 economy 档，首读 `gemini-3.1-flash-lite`、
升级 `gemini-3.8-flash`（Vertex global），文字快路开，直读 ≤20 页，Vision 备用路线线上缺凭据不可用。

| # | 旧线上 | 新平台（本批前） | 本批 |
| --- | --- | --- | --- |
| 1 | 首读 3.1-lite 思考 MINIMAL，升级 3.8 思考 LOW（旧 `vertex.py:118-132`） | 每张直接 3.8、不设思考（默认深度思考，一张思考 10,553 token、76 秒） | 补回：`ocr.thinking_for` + `recognition.policy`（ERP 显式 economy，不跟站点档位；DMS 不传思考参数、不变） |
| 2 | 硬闸（合计缺失、小计−折扣+VAT=合计、负数、同税号、MOD-11、VAT 7%、明细和、合计小于单行）不过就换路；日期软标注；非发票拒 | 只看有没有明细 | 补回 `recognition.checks`；另加旧线上入账时才做的「明细算出的合计 / VAT 等于票面」、按票面数字判含税 / 不含税（旧 `invoice_amounts.py:28-58`）；没过的在卡片上红字、网页原生提示 |
| 3 | PDF 渲染 200 DPI、文字快路、≤20 页 | PDF 整件给模型 | 未做 |
| 4 | 一图 / 一文件多票拆分、按票号归组 | 无 | 未做 |
| 5 | 佛历年份由代码换算 | 模型直接给公历 | 未做（日期软标注已能抓「差十年」） |
| 6 | 后台任务里下载 LINE 图片 | webhook 里同步下载（499） | 未做 |
| 7 | 识别前查余额、按页扣费、记成本 | ERP 不扣费不记录（无决策记录） | 待用户定 |
| 8 | 网页识别配额错误退避 | 网页同步路径 429 原样抛给页面（读代码推断） | 未做 |

未搬但不算漏：照片转正（旧线上后端也没有）；影子双读、Vision / Document AI 备用、合计救援等有记录的有意不迁。
文档不一致：ADR-0002、`docs/p4/01-issuing.md` 写 A 档用 Document AI，旧发票与新代码都没有。

付费实测（测试机，4 张 09-26 真机原件）：

| 版本 | 原件 | 结果 |
| --- | --- | --- |
| `685f8f3` 前 | 两张采购票 | 票面「小计」读成含税额 → 误报并多升级一次；NBC 20% 折扣读成 20 泰铢 → 被合计检查拦下 |
| `9d2c4fe` | กิจสยามยานยนต์ 采购票 | 1 次 3.1-lite，5.1 秒，无思考 token，合计 7,500 与票面一致，判为含税，无复核项 |
| `9d2c4fe` | 小票 489 | 1 次，4.1 秒，合计 / VAT / 收据号 02000138 全对 |
| `9d2c4fe` | 小票 2,768（只印金额） | 1 次，3.5 秒，单价由金额算出，全对 |
| `685f8f3` | NBC 横拍采购票 | 3.1 + 3.8 两次，商品名数量仍错，合计对不上 → 标「需复核」（横拍问题不在本批范围） |

另见：Google 429 后本仓 Core 的容量闸门会整桶冷却，实测排队约 60 秒（共用层设计，未改，待定）；3.8 偶发 26–66 秒是供应商延迟，与思考无关（无思考 token）。

### 09-26 前端（POS / 盘点）

| 现象 | 原因 | 处理（`ed0ec1a`） |
| --- | --- | --- |
| POS 扫码后不进购物车 | 框架相机扫到后 `set_value()` 回填，不发 `input`；官方搜索框只听 `input` 才筛选并「唯一命中进车」 | 相机回填后补发 `input`，其余走官方 `auto_add_item_to_cart` |
| POS 手机端搜索框、扫码按钮太小 | 官方 12 列筛选行给搜索 5 列（物料组那格已删） | 本仓 `cashier.css` 窄屏下搜索占满整行、44px 高、扫码按钮加大；官方样式未改 |
| 盘点明细手机上 11 列挤在一起 | — | 窄屏只显示物料 / 账面 / 实际 / 差异 / 状态，其余点物料看 |
| 盘点扫码按钮不见了 | 待用户确认指哪一颗（连续盘点对话框里「扫描条形码或二维码」按钮与相机在） | 未改 |

共享文件：Cowork 前端窗口在改 `pearnly_core` 的术语表与翻译包，本批只动 ERP 自己的文件。

### 09-26 第四轮：Giffarine 单、MR.ERP 计算规则、其余 OCR 遗漏补齐

**Giffarine 采购单（`LI-00089` → `PUR-2026-00091`）**：识别与票面一致（两行 40×300、6×135，整单折扣 3,162，合计 9,648，
VAT 631.18）；入账 VAT 却是 838.04 —— 采购计算「先按折扣前算 VAT、再扣整单折扣」。旧线上手工录入同一公式
（旧 `services/purchase/totals.py:84`，不是迁移退化），但旧线上识别入账用票面金额 + 按行分摊净额
（旧 `purchase_leg.py:80-91`、`invoice_amounts.py:61-87`），所以没暴露。

用户指定按 **MR.ERP**（`/Users/skin/mrerp-source`，泰国 ERP 参考源码）口径：`src/appe/component/javascript/allform.js:650-721`
`calcgrandtotal()`：`beforevat = grandtotal − discount`，`vat = beforevat × rate`；采购、销售同一函数；无行折扣、无百分比
折扣；折扣不分摊到入库成本（记「折扣收入」）。处理（`a0c63f7`）：采购**计算版本 3**（新单据）整单折扣先于 VAT，
折扣按输入原样存（与行折扣同口径，重复保存结果不变）；版本 2 旧单据保持原算法；离线金额合同测试
`tests/test_purchase_amounts.py` 加入 CI。与 MR.ERP 仍有的差别（未改，待定）：不含税票 MR.ERP 整单一次取整 VAT，
我们逐行取整（个别单可能差 0.01）；含税票 MR.ERP 的折扣录不含税额，我们录票面含税额再折算（结果相同）。

**POS 扫码**（`a0c63f7`）：POS 原用框架 `frappe.ui.Scanner`，改为与盘点、选商品同一个读码器（`scan.js` `p.scan`），
扫到后交官方搜索框「唯一命中直接进车」。

**其余 OCR 遗漏**：

| 项 | 处理 |
| --- | --- |
| LINE 回调里同步下载（499） | `a062451`：回调只登记排队；识别任务里下载一次、存本人私有文件、先提交再识别 |
| 佛历日期由模型换算 | `a062451`：模型照抄票面日期，代码换算（年份 ≥ 2400 减 543；两位年份只取落在 5 年内的那个解释） |
| 网页识别限流原样抛出 | `a062451`：网页入口给「识别繁忙，请稍后再试」；LINE 队列照旧自动重试 |
| PDF 无页数上限、无文字快路 | `0f69c95`：超过 20 页拒；单个 PDF 文字层每页 ≥ 200 字先按文字读，不过检查再升级读原件 |
| 多页、一文件多票 | `c72348d`：多页 PDF 逐页读（不并发）、按单号归组；一页多票拆开；LINE 每张单一条记录一份文件一张卡；网页填第一张并提示其余张数 |
| 旧线上 13 页撞 429（旧 `ec2e5c95` 修了退避；「一页失败整份作废」遗留） | 新平台逐页读 + 队列断点：某页被限流整任务推迟，已读页从断点重放不重复付费、不整份作废 |

实测（测试机，`c72348d` / `43d4d36b`，付费）：两张小票拼成的两页 PDF → 2 次调用 7.1 秒，拆成 2 张（489、2,768）全对；
两张小票并排一张图 → 首读没过检查，升级 3.8 后正确拆成 2 张（中途一次 429，由脚本重试）。

### 09-26 第五轮：识别失败、计费、卡片重排

| 事项 | 处理 |
| --- | --- |
| 「识别又失败」`LI-00092` | `a062451` 后台下载里 `Document.update()` 用了关键字参数（Frappe 要字典），下载后保存报错；测试替身同样写错没拦住。`a9ac980` 修复、替身改成 Frappe 签名。测试机真实环境重跑：从 LINE 下载 → 存文件 → 识别 → 推卡 通过 |
| 用户委托决定的会计细节 | VAT 整单（同税率一组）一次取整、再分摊到行（`61b56dc` 起的版本 3，与票面一致；MR.ERP 不含税票同样整单一次）；含税票的整单折扣照票面录；限流冷却不改；计费见下 |
| ERP 识别计费（用户：要扣，按套餐） | `00c427a`：与 DMS / Cowork 同一计费底座，`kind=pdf`、单位物理页、B 档；开工前查余额（不足告诉本人充值，查不到不说成余额不足、排队重试），识别成功才报账，LINE 每条收单固定 reference 不重复扣。测试机：`LI-00092` 扣 1.5 泰铢（测试账本 1,000 → 998.5） |
| NBC 票「ส่วนลด %」一列漏读 | `974cb6b`：提示词写明无 % 号的百分比列；印了行金额且少于数量×单价时差额即折扣。重测：三行折扣读出 706.8 / 706.8 / 235.6；票面第三行无金额、合计只含前两行，仍标「需复核」（票面本身不一致，应由人定） |
| 卡片重排（用户） | `21cf594`：表头不变；采购只列卖方、销售只列买方（我方公司即「公司」一行，原采购卡上的买方取自公司主档、不是识别）；小计至付款方式移到明细下面 |

测试机现为 `21cf594` / `57784648`。今天 Google 对 `gemini-3.1-flash-lite` 限流频繁（一次实测连续 3 次 429）。

## 4. 验证

| 版本 | 环境 | 场景 | 结果 |
| --- | --- | --- | --- |
| `513d296` / `0a902f56` | 测试机 testerp | 公网探测落到测试机、LINE 绑定、发图入草稿 | 通过（绑定第二次成功；发图入 `LI-00042`） |
| `34f4751` / `7cdfebd5` | 测试机 testerp | 复用路径换镜像；公网 ping 200 / login 302 | 通过；管理员只读、绑定时间待用户目视 |
| `e18c556` / `8e5e1e40` | 本地 Frappe 运行时 | `test_line_intake`（19）+ 团队 / 成员服务（共 37） | 通过 |
| `e18c556` / `8e5e1e40` | 测试机 testerp | 复用路径换镜像 116 秒；新表字段已迁移；公网新 Desk 脚本含打开 / 提交接口，旧 `purchase-intake.js` 404 | 通过 |
| `e18c556` | ERP-TEST 官方号（已授权外部写入） | `line_menu.publish()`：`pearnly-erp-{both,purchase,sales}-*` 三套；管理员账号挂 both；默认菜单仍为旧 `pearnly-erp-v1` | 读回一致 |
| — | 真机 | LINE 选择步骤、识别、卡片、原生页提交、菜单 | 待用户验收 |
| `9508cf4` / `45858eed` | 真机（用户 09-26） | 采购 / 销售识别成功、自动出入库、收发存有记录 | 用户确认；反馈见「09-26 第二轮」 |
| `e4e56a4` | 本地运行时（`pearnly/frappe-sample:16.34.1`，无网络） | `test_line_intake` + 团队 / 成员服务共 53；CI 同名闸门 | 通过 |
| `dc47e8c` / `2324a28b` | CI | Development checks、生产镜像构建（含镜像内 ERP LINE 用例） | 通过 |
| `dc47e8c` / `2324a28b` | 测试机 testerp | 复用路径换镜像 114 秒；`price_includes_vat` 列已迁移；读出绑定账号手机语言 `zh`、权限组合「采购+销售+盘点」 | 通过 |
| `dc47e8c` | ERP-TEST 官方号（已授权外部写入） | `line_menu.publish()`：28 套（4 语言 × 7 组合）；本人挂 `pearnly-erp-zh-purchase-sales-stocktake-*`，标签「采购 / 销售 / 盘点」，盘点指向 `/desk/pearnly-stock-count`；默认菜单未变 | 读回一致 |
| `dc47e8c` | 测试机 testerp（只生成不发送） | 用 `LI-00054` 的识别结果生成新卡：合计 67,852.80、VAT 4,438.97、折扣 16,963.20，与票面一致；60 行泰文明细卡片超 30 KB | 金额通过；超限由 `74543e1` 修（按 28 KB 截断并报「还有 N 项」），待换镜像 |

另：`e18c556` 修了 DMS 隔离站点用例的随机失败（回复令牌里带标签，按整包搜订单号会多数一条），
此前 `f033c9c` 之后约四分之一的生产镜像构建因此失败。

HTTP 200、页面能打开不算业务验收；真机验收以用户确认为准。

## 7. LINE 绑定即登录（2026-09-26 第三窗口 7e25ab，testerp 已开启，待真机验收）

分支 `codex/line-login`（工作树 `/Users/skin/pearnly/.claude/worktrees/gifted-lehmann-d677ce`），基线 `origin/main` = `19ade38`。
线上核对（开工时）：ERP 7 个容器均 `sha256:b7c07a7f…`（revision `a1f6488`）；`testerp.pearnly.com` 经 `@1.1.1.1` 解析 `107.155.65.74`（线上）；测试机关机。

### 用户定稿（2026-09-26）

| 问题 | 决定 |
| --- | --- |
| 路线 | Keycloak 身份代理：业务 realm 为每个站点加隐藏的 LINE（OIDC）身份提供者，会话仍由 Keycloak 签发、照常登记 |
| 关联方式 | **C：扩展现有员工建号服务**（计费站点 `pearnly_control.account.member_api`），站点**不拿** Keycloak 管理凭据（沿用 `site-step.py` 断言与 `member_client.py` 的既有决定）。B（站点受限管理钥匙）被否：实测 Keycloak 细粒度权限按组授予 `manage-members` 时同时允许改本组人员密码与邮箱，拆不掉 |
| 范围 | 共用层一次做好；本窗口接 ERP、DMS；Cowork 等本分支合入后由 Cowork 窗口（`codex/cowork-identity-realm`）接。顺序由用户定：本分支先合，Cowork 分池暂停 |
| 解绑 / 取消 LINE | 立即作废经 LINE 登录的本站会话 |
| 已有绑定 | 上线时自动补关联 |

### 原型证据（本机临时 Keycloak 26.7.4，同 digest `82a77884…`，容器 `line-login-kc-proto`，用完删除）

以一个假 LINE realm 充当外部身份提供者，脚本走完整浏览器流程（`kc_idp_hint`）：

- 已关联的 LINE → 直接登录，不输密码；未关联的 → Keycloak 返回 Access denied，**不建号**（首次登录流程只放一个 `deny-access-authenticator`）。
- `hideOnLogin` 后登录页不出现 LINE 按钮；密码重新验证页也不出现。
- IdP 映射器 `oidc-user-session-note-idp-mapper`（`sub`，正则）+ realm 默认客户端作用域里的 `oidc-usersessionmodel-note-mapper`：ID token 带 `pearnly_idp`（入口别名）与 `pearnly_line_user_id`（LINE 用户 ID）。
- **跨产品**：LINE 登录站点 A 后单点进站点 B，B 的 ID token 同样带这两项（B 在作用域建立之后创建，默认作用域自动带上）→ 各站点回调可拒绝不属于本站的 LINE 登录。
- 风险实测：LINE 登录后直接 `kc_action=UPDATE_PASSWORD` 会出「设新密码」而不要旧密码；把该必需动作的 `max_auth_age` 设为 1 秒后改为要求输入当前密码（仍有 1 秒理论窗口，记为残留风险）。
- B 方案实测（已否）：`manage-members` 可关联/取消关联本组成员、不能碰其他组、不能建/删号，但能 `reset-password` 与改邮箱；对全体用户加拒绝规则会把关联也一并拒掉。

### 实现（分支 `codex/line-login`）

| 层 | 落点 | 要点 |
| --- | --- | --- |
| 共享约定 | `pearnly_core/account/line_login_contract.py` | 别名 `line-<产品>-<租户>`、ID token 声明名、LINE 用户 ID 格式；纯函数，站点 / 建号服务 / 开通步骤共用 |
| 建号服务（计费站点） | `pearnly_control/account/member_service.py`、`member_provider.py`、新表 `Pearnly Tenant Identity` | `link-line` / `unlink-line`：只替本租户登记过的身份（员工台账 + 开户 `register` 时登记的管理员）；别名服务端算；**Keycloak 26.7.4 实测允许同一 LINE 挂两人**，所以先查持有人，只在同租户登记身份之间搬 |
| 站点共用层 | `pearnly_core/account/line_login.py`、`identity.py`、`sessions.py`、`hooks.py` | 绑定变化钩子：先作废失效的 LINE 会话，再排队同步；每小时对账（上线补关联、失败重试、停用账号摘除）；回调核对来源（不通过 → 不建会话并走 Keycloak 结束会话）；会话登记记 `identity_provider` / `line_user_id`；入口 `line_login.enter` 仅在 LINE 内置浏览器带 `kc_idp_hint` |
| 表结构 | `Pearnly LINE Binding.login_link/login_link_at`、`Pearnly Session.identity_provider/line_user_id` | 原生 DocType JSON（`modified` 已更新） |
| ERP | `integrations/line_cards.editor_link`、`line_menu` 盘点格 | 经 `line_login.entry_url`；富菜单 URI 变了 → 部署后需重新发布（按内容散列，只新建含盘点的 16 套） |
| 生产配置 | `scripts/production/configure-line-login.py`、`site-step.py set_line`、`bootstrap-auth.py` | realm 一次性：拒绝首次登录流程 `pearnly-line-deny`、默认作用域 `pearnly-login-source`（加到所有客户端）、敏感必需动作 `max_auth_age=1`；站点入口随 `set_line` 建（渠道带 LINE Login 密钥才开，写 `pearnly_line_login_idp`）；bootstrap 断言改为只允许隐藏、拒绝首次登录的 `line-*` |
| 控制面 | `Pearnly LINE Channel.login_channel_secret` / `login_callback_url` | 密钥由用户在表单填；回调地址自动算出供登记到 LINE Login 频道 |
| DMS | 不改 | 操作员按旧产品只走 LINE、没有网页账号（无 Keycloak 身份 → 同步自动跳过）；DMS 已有 LIFF 一次性票据门户，本来就无感（用户 2026-09-26 确认只要"无感、不加登录按钮"） |

登录页**不加任何 LINE 按钮**（`hideOnLogin`，实测登录页与重新验证页都不出现）；只有从 LINE 里点链接时自动走。

### 验证

| 层 | 证据 | 结果 |
| --- | --- | --- |
| 单元（镜像内 Python） | `pearnly_control.account.test_member_service` 21、`pearnly_core.account.test_line_login` 10、`pearnly_erp_portal.integrations.test_line_intake` 等共 94 条；已加入 `build-production.yml` | 通过 |
| 仓库闸门 | Development checks 同名本地项全部通过（本机 Python 3.9 跑不了的 4 项改在镜像 Python 3.14 内通过）；CI `36231330278` Development checks、`36231337957` Build production image（`d25378b`）通过 | 通过 |
| 真实 Keycloak 26.7.4 | 生产脚本连跑两遍幂等；已有客户端自动带作用域；假 LINE 已关联 → 免密、ID token 带正确 LINE ID；未关联 → Access denied 且不建号；LINE 登录后改密要求当前密码；建号服务真实调用（关联 / 冲突 / 换绑 / 取消） | 通过 |
| 真实 Frappe 站点端到端 | 新增实验环境 `infrastructure/compose/line-login-lab.yml` + `scripts/line-login-lab.sh`（独立 Colima `pearnly-line-login`，不开宿主端口，合成数据）；`line-login-lab-verify.py` 24 项：绑定→自动关联、LINE 内免密进深链、别处不走 LINE、密码登录不受影响且登录页无 LINE、解绑即踢 LINE 会话而密码会话保留、解绑后 Keycloak 拒绝、Keycloak 残留关联被站点拒并结束会话、LINE 改绑给同租户另一人（关联迁移、旧会话踢掉）、对账修复 | 24/24（从零重建两次） |
| 升级路径 | 用 `origin/main` 代码建站后以本分支 `bench migrate`：新列、新表、渠道密钥字段均迁出 | 通过 |
| 测试机 / 线上 / 真机 | — | **未做** |

限制（如实）：LINE 本身是假的 OIDC realm —— 真 LINE 的 HS256 ID token、无 `preferred_username`、LINE 内置浏览器自动登录与首次授权页，只能在真机验；"官方号与 LINE Login 频道同一 Provider"未核实；`max_auth_age=1` 仍有 1 秒理论窗口；Keycloak 里 LINE 会话在解绑后不能由站点直接终止（无管理凭据），靠站点回调拒绝 + 结束会话兜底。

### 合并与上线（2026-09-26，用户授权「合入 main，上线 testerp 真机验」）

- 合入：`f41cb55`（功能）→ 远端 `main`；随后 `bf497be`（配置脚本修正，见下）→ 远端 `main`；每次都是分支 Development checks 通过后主目录快进、立即推送、回读一致。
  Cowork 窗口的 `member-by-product` 补丁原样在 `73e6c33`（另修一条随之失效的断言）。
- 镜像：main 上 Build `36232328077` → `sha256:52cf808c1be5e4dd74d3d725f5e00d18c9243b67ce0204195cd9ed67a80fd2dd`（`f41cb55`，与分支构建 digest 相同）；
  Build `36233152128` → `sha256:235f71c56ac93de9a4617b09f80fc1c90d04ffa5bd7b54eabb8814cb63d3fd93`（`bf497be`）。均经 stage-production 暂存。
- 部署目录：先备份 `/opt/pearnly/state/predeploy-line-login-20260926092447`，从镜像同步后 `check-deploy-image` 通过；现与 `235f71c5` 同源。
- 计费站点：`activate-foundation.sh 52cf808c…` → billing-web `f41cb55`（原 `6a13b98`，区间内他人改动只有 `d54c33b` 控制面开户修复）；激活前备份
  `20260926_162520-billing_internal-*`；`Pearnly Tenant Identity` 已建；`active-image.json` = `52cf808c…`。日志 `/opt/pearnly/state/activate-foundation-f41cb55.log`。
- ERP：`activate-product.sh 52cf808c… erp` → 7 容器 `f41cb55`；备份 `20260926_162725-testerp_pearnly_com-*`；新列已迁；从站点查计费余额正常（1,000）；日志 0 错误。
  日志 `/opt/pearnly/state/activate-erp-f41cb55.log`。DMS（`4207bf1`，另一窗口 09:13 部署）、Cowork 未动。
- Keycloak（线上 realm `pearnly-business`）：执行前快照 `/opt/pearnly/state/keycloak-before-line-login.json`（600）。**快照发现**线上 `max_auth_age` 还覆盖资料、语言、
  邮箱验证等动作，原脚本会一并改成 1 秒 → 先修 `bf497be`（只改凭据类：改密、邮箱、TOTP、恢复码、通行密钥、删凭据、关联账号），本机实验 25/25 后才执行。
  回读：`pearnly-line-deny` 流程；`pearnly-login-source` 为默认作用域，12 个客户端中 10 个带上（另 2 个为 bearer-only 内置客户端，按设计跳过）；外部身份提供者 0 个；
  凭据类 `max_auth_age=1`、资料 / 语言 / 邮箱验证未动；注册仍关闭。
- 计费站点登记 testerp 管理员：`T-2026-0283` / `009eae24-…`（= `ISS-2026-0282` 的 `identity_subject` = testerp `Pearnly Identity`）。
- ERP 富菜单重新发布：28 套（含盘点的新建），绑定用户已换挂新菜单，盘点 URI = `…/api/method/pearnly_core.account.line_login.enter?to=%2Fdesk%2Fpearnly-stock-count`。
- 本机控制面（`control.localhost`，挂主目录代码 `bf497be`）：用户同意后 `bench migrate` 并重启 web / worker / scheduler；LINE 渠道表单已有新字段；
  testerp 渠道 `LINE-DEP-2026-0285` 的回调地址计算为 `https://auth.pearnly.com/realms/pearnly-business/broker/line-erp-t-2026-0283/endpoint`。

**当前状态：代码与配置已上线，LINE 登录开关仍关**（testerp 无 `pearnly_line_login_idp`、realm 无 LINE 入口）—— 等用户填 LINE Login 频道密钥（控制面 → 开户执行器 `set_line` 自动建入口并开关）、
在 LINE Login 频道 2010411313 登记上面的回调地址、核实与 ERP-TEST 同一 Provider；之后立即触发一次关联同步，再真机验收。

### 改为"全平台只配一次"（2026-09-26 用户在填密钥时提出）

用户问"这个 id 以后每个租户都要填一次吗"，并要求 LINE 渠道表单上的说明文字全部去掉。改动：

- **入口按 LINE Login 频道共用**：别名 `line-<频道号>`（testerp 为 `line-2010411313`）。所有租户的 LIFF 都挂在同一 Provider「Pearnly」的这个频道下
  （控制面 4 条渠道的 LIFF 均为 `2010411313-*`；2026-09-26 在 LINE 后台核实 ERP-TEST `2011288286` 与该频道同属 Provider「Pearnly」），回调地址只登记一次。
- **密钥放开户目标**（`Pearnly Target.line_login_channel_id / line_login_channel_secret`，回调地址自动算出只读显示）；`set_line` 只给 LIFF 属于该频道、
  且产品在 `LINE_LOGIN_PRODUCTS = ("erp",)` 的站点（DMS 不接；Cowork 接入时加）。保存目标 → 该目标下已开通的 ERP 渠道重排 `set_line`。
- **按站点记账**：计费站点新表 `Pearnly LINE Login Link`（租户 × 产品 × 身份 → LINE）。解绑时只删本站一条；该身份在别的站点还绑着就保持关联，最后一处解绑才去掉。
  改绑时只从"本租户登记、且这个 LINE 只挂在本站"的旧持有人身上摘。
- 安全边界不变："只能进绑定了它的站点"由各站点回调核对"本站该账号生效的绑定"保证。代价（如实）：共用入口下一个 LINE 在 Keycloak 里只能挂一个账号，
  同一个人若在不同租户用**不同**账号、却绑同一个 LINE，只有先关联的那个能用 LINE 登录（另一处留 `failed`，密码登录不受影响）。
- 迁移：`pearnly_core` 补丁 `v0_0_5` 清空绑定的关联状态（由对账在新入口下重关联）；`pearnly_control` 补丁 `v0_0_1` 把已填在渠道上的密钥搬到其开户目标并删除渠道旧存档
  （实验库实测：内容一致、只存掩码、回调地址正确、重跑不覆盖）。旧的租户专用入口 `line-erp-t-2026-0283` 与 LINE 后台对应回调在切换后删除。
- LINE 渠道表单：去掉新旧全部字段说明，分节标题改为「凭据」；控制面新字段补四语词条。
- 验证：单元 100 条（建号服务新增多站点记账用例）；实验环境加第二个 ERP 站点（另一租户、同一管理员），从零重建 31/31，新增 L1–L6：
  第二站点经共用入口关联并免密进入、一处解绑后另一处仍可用且 Keycloak 关联保留、已解绑的站点拒绝、最后一处解绑才去掉关联。

### 共用入口上线（2026-09-26）

- 合入 `15dea26`；main 上 Build `36235505740` → `sha256:317b2feb485df9524a90d47b495cddc329ed488974782c73282656e1438bcdb8`，stage `36235837841`。
- 部署目录备份 `/opt/pearnly/state/predeploy-line-login-shared-20260926102803` 后同步（只差 `site-step.py`），同源检查通过。
- 先计费站点（`activate-foundation`，备份 `20260926_172823-billing_internal-*`，`Pearnly LINE Login Link` 已建），后 ERP（`activate-product erp`，备份
  `20260926_172951-testerp_pearnly_com-*`）；billing / ERP 均 `15dea26`；补丁把 testerp 绑定关联状态清空；计费余额查询正常。DMS / Cowork 未动。
- 本机控制面迁移（第一次迁移输出中出现一次 `Killed`，重跑 `rc=0` 且无 `Killed`；补丁结果已回读）并重启：开户目标 `production-ssdnodes` 有频道号 `2010411313`、
  回调 `…/broker/line-2010411313/endpoint`、密钥存档 1 条；渠道上的旧存档 0 条。`requeue_line_login("production-ssdnodes")` → testerp `set_line` done。
- 回读：testerp `pearnly_line_login_idp` = `line-2010411313`；管理员 `login_link=linked`；计费站点关联登记 1 条（`T-2026-0283` / erp / `line-2010411313`）。
  删除旧入口 `line-erp-t-2026-0283`（关联随之去掉），realm 只剩 `line-2010411313`，`verify_realm` 通过。
- LINE 后台（频道 2010411313）回调改为：`https://auth.pearnly.com/realms/pearnly-business/broker/line-2010411313/endpoint` + 原有 `https://pearnly.com/api/auth/line/callback`，回读一致。
- 以后新租户：开户单填官方号与 LIFF（`2010411313-*`）即可，LINE 登录随开通自动打开，不再填密钥、不再登记回调。

**待用户真机验收**（用户确认前不算）：LINE 里点卡片「编辑」、菜单「盘点」免密进入；解绑后不能再免密进入。

### 其他产品补上登录核对（2026-09-26）

"LINE 登录只能进绑定了它的站点"的回调核对只在新共用层里。LINE 登录 ERP 后，同一 LINE 内置浏览器里 Keycloak 单点会放行进**仍跑旧共用层**的站点。

- DMS：`4207bf1..15dea26` 只有本任务的共用层改动（DMS 窗口已发布完），用户让本窗口处理 → `activate-product.sh 317b2feb… dms`：8 容器 `15dea26`；
  备份 `20260926_181035-ris_pearnly_com-*`、`20260926_181039-testdms_pearnly_com-*`；两站 LINE 登录保持关闭（`enabled_alias` 空）；ping 200，日志 0 错误；已通知 DMS 窗口。
- Cowork：原为 `dd81a3f`（之间有大量共用层 / desk 改动，如 `f848f91` 共用单据查看器）。Cowork 窗口答复其用户确认"现在激活到 `317b2feb`"（Cowork 新前端与号池拆分在
  未合入的 `codex/cowork-frontend`，不在本次）→ `activate-product.sh 317b2feb… cowork`：7 容器 `15dea26`；备份 `20260926_182910-testcowork_pearnly_com-*`；
  LINE 登录关闭；会话表新列已迁；ping 200，web / worker 日志 0 错误；未做 Cowork 业务 / UI 验收。至此线上 ERP / DMS / Cowork / 计费均为 `15dea26`，缺口关闭。
- 另一个 ERP 窗口已被告知线上 ERP 为 `15dea26`，发布须基于最新 main。

### 本窗口登记的后续小项（2026-09-26）

- **旧改密接口清理**（低优先级，非用户可见缺陷）：`pearnly_core.password.change_password` 与 `sessions.logout_everywhere` 依赖站点上的 Keycloak 管理凭据，
  云端租户站点没有，手动调用会报错；但云端站点（`pearnly_native_account`）的改密按钮与 `/pearnly-password` 都直接跳 Keycloak 原生改密，界面无任何入口调用这两个接口，
  `public/js/pearnly_account.js` 也未被任何页面加载。改密后踢其他设备由 Keycloak 改密页"从其他设备登出" + 回通道登出承担（未实测）。建议：云端站点直接拒绝这两个接口并删除无引用脚本。
  用户点开的后台会话「Verify and fix "log out everywhere"」首条命令即被中断，未做任何改动。
- **ERP 扫码器去重**：Cowork 窗口已把 ERP `public/desk/scan.js` 通用部分复制到 `pearnly_core/public/desk/scan.js`（分支 `codex/cowork-frontend`）；其合入后可删 ERP 副本、
  `www/stocktake.html` 改指 core 路径。动手前与 Cowork 窗口约定，避免同时改。

### 待用户处理 / 授权

- LINE 后台核实：ERP-TEST 官方号（2011288286）与 LINE Login 频道 2010411313 是否同一 Provider（不同则 LINE 用户 ID 不一致，关联全失败）；在该频道登记 Keycloak 回调地址（已授权范围内，改前告知）。
- LINE Login 频道 secret 由用户填入控制面。
- 线上 Keycloak realm 配置、租户管理员补登、生产部署：分别授权。

## 6. 交接（2026-09-26 第二窗口 f58107 收尾，交接保留）—— 以本节为准

**范围状态：已上线，交接保留。** 下一个窗口先等用户发问题；下面是接手时的事实与待办。第 5 节是第一个窗口的交接，已被本节取代。

### 现状（如实）

- 代码：全部在 `main`（代码 `a1f6488`，之后只有文档）；任务分支 `codex/erp-testenv` 已合并并删除（本地、远端）。
- 线上：ERP 只有 `testerp.pearnly.com` 一个站点，运行 `a1f6488`（`sha256:b7c07a7f…`），部署记录见 5a 节。
- 测试租户：已切回线上。测试机（`lenovo-office-lan`）**已关机**；ERP 栈停、4 个数据卷保留（用户定），DMS 无卷。
  复用：开机后 `PEARNLY_TESTENV_IMAGE=<digest> PEARNLY_TESTENV_SRC=<工作树> bash scripts/testenv.sh lab-start erp`，
  详见 [测试环境](../development/test-environment.md)「当前状态与交接」。
- 外部：ERP-TEST（`@063eadty`）Webhook = testerp 域名；富菜单 28 套（线上与测试机配置指向同一批）；默认菜单仍是旧 `pearnly-erp-v1`；
  LIFF `2010411313-pBXFYD2S` Endpoint 仍是 DMS 的 `/liff/dms-booking`（ERP 未用）。

### 待用户真机验收（线上，用户确认前不算）

LINE 收单（识别、新卡片排版：采购只列卖方 / 销售只列买方 / 金额在明细下、红字复核提示）、识别扣费、多页 PDF 与一图多票各出卡、
POS 扫码直接进车与手机端搜索框、盘点明细手机五列、采购整单折扣 VAT 与票面一致（计算版本 3）。

### 讨论过、尚未做（建议顺序）

1. **LINE 绑定即登录（用户选的「方案 B」，暂缓，先用方案 A）**：已绑定 LINE 的人点卡片「编辑」/ 菜单「盘点」免密进网页端。
   属于**新增登录通道、身份安全改动**，动手前必须先和用户确认设计。两条路：
   - **Keycloak 身份代理**（倾向）：在 Keycloak 业务 realm 配 LINE 作为外部身份提供者（LINE Login 是 OIDC），与现有账号关联；
     LINE 内置浏览器里点「用 LINE 登录」走 Keycloak 原生流程，会话仍由 Keycloak 签发，身份、授权、会话票据保护不变。
   - **LIFF id_token**：LIFF 页取 `liff.getIDToken()` → 服务端向 LINE 验签 → 按 `Pearnly LINE Binding` 找绑定账号 → 签发会话。
     要讲清会话范围与时效、解绑 / 取消 LINE 勾选即失效、与 Keycloak 身份的关系；且需把上面那个 LIFF 的 Endpoint 改成 ERP。
   迁移保护里的「身份、授权、会话票据与幂等」不得削弱；需要 LINE 后台与 Keycloak 配置（外部写入，按授权执行）。
2. **OCR 改进**（本窗口 09-26 提出，用户说下次再说；每项做完都要真实原件付费实测）：
   ① 被 429 时自动换区域 / 模型重试（如亚太区 `gemini-3.5-flash`，不同容量池）；② 本地免费识别方向并转正（镜像加方向检测组件，
   底座改动先确认）；③ LINE「拍单据」专用页：自动找边拉正裁边（新页面，先给设计）；④ 供应商纠错记忆（旧线上 fewshot，按税号取样）；
   ⑤ 泰国 e-Tax Invoice PDF 内嵌 XML 直读。
3. **构建加速 B**（隔离站点测试并行）：未做；A 已在 `a38d043`。要保证只有全部通过的那次运行产出可用 digest。
4. **主机 nginx HTTP/2**（[ERP 前端交接](2026-09-25-erp-frontend.md)「部署时额外要做」）：`d156e39` 的开户脚本已随部署目录上线，
   但主机上已有的站点文件（`/etc/nginx/conf.d/pearnly-tenant-*.conf` 等）未改 `listen 443 ssl http2`、未 reload —— 线上写入，需授权。
5. **识别报账补偿**：报账暂时失败只记 Error Log「ERP OCR charge pending」（reference 固定可安全重报），没有像 Cowork 那样的持久补报。
6. 已知限制：网页端上传识别一个文件多张单据只填第一张（提示其余张数）；盘点「扫码按钮不见了」用户未指明哪一颗，之后说「其它没问题」，未改。
7. 与 MR.ERP 的已知差别（已按用户委托决定，不再改）：含税票整单折扣照票面录（MR.ERP 录不含税额，VAT 结果相同）；VAT 按整单（同税率）一次取整。
8. 限流冷却约 60 秒（共用层设计）与 Google「优先级」档（每页约多 0.2–0.4 泰铢）：维持现状，频繁再议。

### 留存与清理登记

已清理（用户 2026-09-26 要求）：第一个窗口的工作树 `strange-swartz-a8b667`（分离头 `312c387`）。删前核对：12 个未提交改动
最后修改 01:21、早于工作移交，关键改动均已在 main，唯一未进 main 的「模型报角度再读一次」是用户定「不为转正多花钱」后有意去掉的；
忽略的 `.testenv-secrets` 与 `~/.config/pearnly-testenv/testenv-secrets` 逐字节相同（正本保留）；无进程、容器挂载或符号链接。
Mac `/tmp/te-export/`（第一个窗口取回的测试租户导出）：用户要求删除时已不存在 —— 本机 09-26 重启时 macOS 清空了 `/tmp`。

| 保留项 | 用途 / 原因 | 解除条件 |
| --- | --- | --- |
| 本会话工作树 `funny-mendel-36f374` | 当前会话所在 | 会话结束后由应用清理 |
| 测试机 ERP 数据卷 4 个、9 个无容器引用的匿名卷、基础与候选镜像 | 用户定保留；匿名卷来源未核实 | 需要时 `reset erp` / 逐个核实后删 |
| 线上 `/root/te-export/` 旧导出 | DMS 与 ERP 共用中转目录 | 负责测试环境的窗口统一清理 |
| 线上部署前备份 `20260926_052428-testerp_pearnly_com-*`、激活日志 | 恢复依据 | 按站点备份保留策略 |

## 5a. 合并与上线（2026-09-26，接手窗口 f58107，用户授权「合并推送部署」）

- 合并：`codex/erp-testenv` 快进进 `main` = `a1f6488d199096a924668dfcb60439b9b0ef3e69`（`a38d043..a1f6488`，远端回读一致；
  分支与 main 的 Development checks 均通过）。从本会话工作树直接推送：主目录 `/Users/skin/pearnly` 的本地 main 当时停在
  `a38d043`（落后、未改动，下次拉取即同步），因本会话不写其它工作树。
- 镜像：main 上 Build production image `36195771036` 通过 → `ghcr.io/skin306152-star/pearnly/frappe@sha256:b7c07a7f7104a1b5c8048f06d054570ccbec81d28fac744e3e6c5266afe95a5c`
  （revision `a1f6488`）；Stage `36196351744` 成功。
- 部署工具同源：服务器 `/opt/pearnly/deploy` 落后 main 5 个文件（ERP 前端交接里未部署的 `7f68801` 静态资源缓存模板、
  `d156e39` HTTP/2 开户脚本等），按 `check-deploy-image.py` 给出的方式从该镜像 `docker cp` 同步后检查通过。
  主机 nginx 已有站点文件改 `http2` 与 `reload`（ERP 前端交接「部署时额外要做」）**未做**。
- 激活：`activate-product.sh <digest> erp` → “Product erp activated with verified image.”，日志
  `/opt/pearnly/state/activate-erp-a1f6488.log`；部署前备份 `20260926_052428-testerp_pearnly_com-*`（库、文件、私有文件、站点配置）。
  上一版 `sha256:a3d007a7…`（源码 `f0f5a43`）。
- 回读：7 个 ERP 容器均 `b7c07a7f`；`maintenance_mode=0`、调度器开；`price_includes_vat`、`ERP LINE Intake.language` 已迁移；
  采购计算版本 3 生效；计费 `status` 可查（测试租户余额 1,000）；主机经 https 访问 ping 200，新容器日志 0 错误。
- 线上站点发布富菜单：28 套全部复用（LINE 上新建 0），默认菜单未变。
- 测试租户切回：`lab-stop erp` → `testerp.pearnly.com` 回落通配记录，`@1.1.1.1` 解析 `107.155.65.74`，公网 ping 200 落在线上
  erp 前端；测试机 ERP 栈已停，4 个数据卷**按用户要求保留**（见 [测试环境](../development/test-environment.md) “当前状态与交接”）。

仍未验收（用户确认前不算）：线上真机走一遍 LINE 收单（识别、卡片新排版、扣费）、POS 扫码进车、盘点手机端、采购整单折扣 VAT。

## 5. 交接（2026-09-26 第一个窗口收尾）—— 历史，已被第 6 节取代

**范围状态：进行中，交接保留。** 下一个窗口**先等用户发问题**，再逐一排查和修复；下面是接手时的事实与待办。

### 代码与分支（如实）

- 全部成果在任务分支 **`codex/erp-testenv`**（远端同名，HEAD 见本节所在提交之后的 `git log`；本节写入前为 `528cde8`），
  工作树 `/Users/skin/pearnly/.claude/worktrees/strange-swartz-a8b667`。**未合入 `main`**：`main` 只含本任务的
  `513d296`（修主线构建的 ERP 测试）与另一个小任务 `a38d043`（构建层缓存，见下）。
- 分支基于 `513d296`，比 `main` 落后 `a38d043`。**合入前先变基到最新 `origin/main`**（变基后新构建才吃到层缓存），
  本地跑同名闸门 → 推分支 → 等 Development checks → 快进 `main` → 立即推送 → 回读（AGENTS「主线集成」）。
- 分支上 CI：各提交 Development checks 均通过；生产镜像构建 `9508cf4` 通过。
- **线上生产未部署**任何本任务改动（ERP 线上仍 `f0f5a43`）；是否部署由用户另定，部署用测试机验过的同一 digest。

### 测试机与外部状态（当前）

- `testerp.pearnly.com` **仍切在测试机**（CNAME → 隧道）；ERP 栈 `pearnly-testenv-erp` 在跑，镜像
  `ghcr.io/skin306152-star/pearnly/frappe@sha256:45858eed23086c7c65b905f5b70fee735d367a2efbf7d42117f9d4092a0f7fef`（= `9508cf4`）。
  换镜像：`PEARNLY_TESTENV_SRC=<工作树> PEARNLY_TESTENV_IMAGE=<digest> bash scripts/testenv.sh lab-start erp`（复用路径约 2 分钟）；
  测完：`bash scripts/testenv.sh lab-stop erp`（切回线上、停栈、卷保留）。口令只认 `~/.config/pearnly-testenv/testenv-secrets`。
- ERP-TEST 官方号（频道 2011288286，`@063eadty`）：Webhook = `https://testerp.pearnly.com/api/method/pearnly_core.line.webhook`；
  已发布 `pearnly-erp-{both,purchase,sales}-*` 三套富菜单，管理员账号挂 both；默认菜单仍是旧 `pearnly-erp-v1`（保留）。
- 该官方号 LIFF `2010411313-pBXFYD2S` 的 Endpoint 仍是 `https://testerp.pearnly.com/liff/dms-booking`（从 DMS 照搬，ERP 站点无此页）；
  现方案不用 LIFF（「编辑」直接打开 Desk 页）。盘点页若要从 LIFF 进，需另改 Endpoint（外部写入，已在本任务授权范围内，改前告知用户）。
- 测试机 OCR 为线上真凭据，发单据即付费调用；只限人工验收。
- 测试租户数据：`LI-00042`（旧方案遗留，status=received）、`LI-00043/44`（识别失败，文件已清）。

### 待用户真机验证（最后一轮刚部署 `9508cf4`）

1. 发单据识别成功并回卡（`9508cf4` 修了源文件被自动挂上导致的「无权」，测试机上已用真代码验证文件不再被挂、权限检查通过，未调 OCR）。
2. 采购选完「现金」后是否出现「服务 / 库存商品」一步：服务器重放正常、当次事件与回复无报错，疑为用户 3 秒内点了下一个菜单；复现再查。
3. 卡片「编辑」→ Desk 原生页（LINE 内置浏览器，未登录先走正常登录）→ 提交 → 跳查看页；再点「编辑」只读；不回执。
4. 散客 / 一般供应商兜底；「手动录入」打开空白新建页。
5. 按权限菜单（只有采购 / 只有销售的账号各试一次）；团队管理改岗位后菜单是否跟着变。
6. 网页端：管理员行灰色全勾；个人信息绑定时间；公司页收银台表格；ERP 前端交接里的
   「切换公司」「供应商/客户框不带扫码」「静态资源缓存」三项（[ERP 前端交接](2026-09-25-erp-frontend.md)）仍未验。

### 讨论过、尚未做

1. **LINE webhook 499**：发图片时回调里同步下载图片 + 建文件 + 入队，LINE 先断开（访问日志 499）。改法：回调只落记录并入队，下载挪到识别任务里。
2. **LINE 身份自动登录（用户选的「方案 B」，暂缓先用 A）**：已绑定的 LINE 点「编辑」免密进入网页端。属于新增登录通道、身份安全改动，
   需单独设计与审查（LIFF id_token 验签 → 只对该绑定账号签发会话？会话范围与时效、解绑即失效、与 Keycloak 身份的关系），迁移保护里的
   「身份、授权、会话票据」不得削弱；动手前与用户确认方案。
3. **构建加速 B（隔离站点测试并行）**：未做。A（层缓存）已合入 `main` `a38d043`，实测打包 144→76 秒、推送 48→6 秒、全程约 8→6 分钟。
   B 需先推镜像再并行测试，要保证只有全部通过的那次运行产出可用 digest；用户同意先只做 A，B 视 A 之后的耗时再定。
4. LINE 收单的图片若是 PDF 多页、或一个文件多张单据，目前只按识别函数原样处理，未专门验证。

### 留存与清理登记

- 保留：本任务工作树与分支 `codex/erp-testenv`（本地 + 远端）——未合入的全部成果；解除条件：用户验收后按主线规则合入并清理。
- 保留：测试机 ERP 栈与卷、`testerp` 切在测试机——用户正在真机验收；解除条件：验收完 `lab-stop erp`。
- 保留：线上服务器 `/root/te-export/` 下 09-24/09-25 的旧导出文件（DMS 与 ERP 共用的中转目录，非本任务独占，未删）；本任务之后的导出改为
  `/root/te-export/<时间戳>/` 子目录，也未删除。复核：确认不再需要后由负责测试环境的窗口统一清理。
- Mac 本机：`/tmp/te-export/`（本任务第一次导入时整目录取回的 25 个文件，含测试租户数据库备份）——可删，未删，留给下个窗口确认后删除；
  工作树根 `.testenv-secrets`（口令副本，已忽略不入库）随工作树清理；`/Users/skin/.codex/worktrees/cowork-frontend/pearnly` 与本任务无关，不动。
- 已清理：构建加速的工作树、本地与远端分支 `codex/ci-build-cache`（已合入 `a38d043`，回读确认删除）。
