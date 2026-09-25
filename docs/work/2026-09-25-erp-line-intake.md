# ERP 测试环境与 LINE 收单迁移（2026-09-25）

**范围状态：进行中。** 本记录管两件事：① 在 Windows 测试机为 ERP 起测试环境并把测试租户
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

## 4. 验证

| 版本 | 环境 | 场景 | 结果 |
| --- | --- | --- | --- |
| `513d296` / `0a902f56` | 测试机 testerp | 公网探测落到测试机、LINE 绑定、发图入草稿 | 通过（绑定第二次成功；发图入 `LI-00042`） |
| `34f4751` / `7cdfebd5` | 测试机 testerp | 复用路径换镜像；公网 ping 200 / login 302 | 通过；管理员只读、绑定时间待用户目视 |
| `e18c556` / `8e5e1e40` | 本地 Frappe 运行时 | `test_line_intake`（19）+ 团队 / 成员服务（共 37） | 通过 |
| `e18c556` / `8e5e1e40` | 测试机 testerp | 复用路径换镜像 116 秒；新表字段已迁移；公网新 Desk 脚本含打开 / 提交接口，旧 `purchase-intake.js` 404 | 通过 |
| `e18c556` | ERP-TEST 官方号（已授权外部写入） | `line_menu.publish()`：`pearnly-erp-{both,purchase,sales}-*` 三套；管理员账号挂 both；默认菜单仍为旧 `pearnly-erp-v1` | 读回一致 |
| — | 真机 | LINE 选择步骤、识别、卡片、原生页提交、菜单 | 待用户验收 |

另：`e18c556` 修了 DMS 隔离站点用例的随机失败（回复令牌里带标签，按整包搜订单号会多数一条），
此前 `f033c9c` 之后约四分之一的生产镜像构建因此失败。

HTTP 200、页面能打开不算业务验收；真机验收以用户确认为准。

## 5. 未完成 / 下一步

- 第 3 节四批实施与各批测试机验证。
- 测完 `lab-stop erp` 切回线上；LINE 回调地址保持新地址（域名不变，切回后打线上 testerp）。
- 线上生产（`erp` 组仍为 `f0f5a43`）未部署任何本任务改动；是否部署另行决定。
