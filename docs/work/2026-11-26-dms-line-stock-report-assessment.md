# 需求影响评估：DMS LINE「报表查询」→ 库存停放报表（图片 + 四个文件）

评估窗口 2026-11-26，分支 `pearnly/dms-line-stock-report-3be9bb`，基线 `origin/main` f18fa1ec8。**本文只评估，未改产品代码、未动任何租户菜单与绑定、未连生产。**
MR.ERP 实查只用 DMS 测试库账号（`dmstest`、`sale02`，凭据取自本机 `~/.config/pearnly/credentials/legacy-endpoints.json`，未写进任何记录），只读：登录、打开报表表单、导出 Excel / PDF、打开两张车型主档表单，随后注销；没有新建或修改任何数据。

## 0. 需求原文与理解

- 原话（用户 11-26，经主会话转述）：DMS 的 LINE 菜单「查询资料」改名「报表查询」，里面 1 = 新的库存停放报表、2 = 现有销售记录；点 1 先出快捷按钮选范围，再出报表：先回一张转换后的**图片**，再给一张卡片带 **4 个下载按钮**（原件 Excel、原件 PDF、转换后 Excel、转换后 PDF）。转换样式照岳父手工样表：每行一个「车型 + 颜色」，列为 车名 / 车型代码 + 颜色代码 / 台数 / 停放位置及各自台数（斜杠分隔），底部更新时间与合计；表头「รถรุ่น 26 MY ว่าง Model/Color · จำนวน · สถานที่จอดรถ」。手工蓝字备注、青色底不做。不进群、不定时、不 push，只 reply。
- 来源报表：MR.ERP DMS「รายงานสต๊อกคงค้างแสดงสถานที่จอดรถ」（`/dms/rpcarallocate/form.php`）。
- 理解：经销商的销售 / 管理者在租户自己的 DMS 官方号里，点两下拿到「现在有哪些空车停在哪」的汇总图，需要时下载四个文件转发或打印。
- 不在范围：进群推送、定时推送、DMS 网页端同一份报表、手工备注列、按客户 / 顾问的其它报表、改销售记录本身的功能。

## A. 实查：MR.ERP 这份报表到底是什么（测试库 2026-11-26 11:20）

**表单与提交**（`rpcarallocate/form.php`，JS `rpcarallocate/component/js/form.js` + `component/js/mtformrp.js`）：
- 字段：สาขา / ทีมสาขา / รุ่นรถ / สีรถ 四组「起–止」（隐藏字段带 id 与序号），หมายเลขเครื่อง、หมายเลขตัวถัง（文本），`selallocate`、`selreport`。
- **表单打开时 MR.ERP 已按登录人的权限预填起止范围**（`dmstest`：分店 BANGKOK–BANGKOK、团队 BKK-A–BKK-A、车型 123–zzz、颜色 03–บรอนซ์）。不改任何条件直接「ออกรายงาน」= 这个账号能看到的全部库存。
- 「ออกรายงาน」= 把同一个表单 POST 到 `rpcarallocate/component/report/<selreport>.php`：`1.php` 回 xlsx（`attachment`，文件名「รายงาน…-ทั้งหมด [26112569_112012].xlsx」），`2.php` 回 PDF（`inline`）。没有中间页、没有异步任务。
- `การจัดสรรทีมสาขา`（`selallocate`）展开只有三项：`1 ทั้งหมด` / `2 ยังไม่จัดสรรทีมสาขา` / `3 จัดสรรทีมสาขาแล้ว`。它是「车有没有分给某个分店团队」，**不是**「有没有被客户订走」。测试库 9 台全部已分配团队，选 2 出 0 行。

**导出的 Excel**（一张表、9 列、**一台车一行**、未汇总）：

| 列 | 表头 | 测试库样例 |
| --- | --- | --- |
| A | รุ่นรถ（车型**代码**） | DM-2D19 |
| B | สีรถ（颜色**代码**） | MGY |
| C | หมายเลขเครื่อง | DMX19-000102 |
| D | หมายเลขตัวถัง | DMCGO19-0000102 |
| E | **สถานะรถ** | รถว่าง / รถว่าง-โชว์ / รถทีมสาขาจอง / รถแต่งเสร็จ / รถแต่งเสร็จส่งคืน |
| F | **สถานที่จอดรถ (คลัง)** | BANGKOK / คลังที่ 1 |
| G | เลขที่ใบจอง | BK000002610000015 或空 |
| H | ชื่อที่ปรึกษาการขาย | dmstest 或空 |
| I | ทีมสาขา | BKK-A |

第 1 行标题（含「-ทั้งหมด / -ยังไม่จัดสรรทีมสาขา / -จัดสรรทีมสาขาแล้ว」）、第 2 行「วันที่ 26/11/2569」、末行「รวม 9 รายการ」，均为合并单元格。PDF 内容与 Excel 相同（1 页，约 155 KB）。

**结论**
- 「ว่าง」来自 **E 列 สถานะรถ** 的「รถว่าง」（MR.ERP 自己的车辆状态），与 `selallocate` 无关。样表表头「…ว่าง Model/Color」就是只列空车。注意测试库里有一台「รถว่าง-โชว์」（展示车）**带着订车单号**，一台「รถทีมสาขาจอง」没有订车单号——所以不能用「有没有订车单号」推断空车，按状态列判断。
- 停放位置在 F 列；车名、年款报表里没有。
- **车型主档**（`car/form.php`，`status=e`）有 `txtmodelcode`（รหัสรุ่นรถ）、`txtmodelname`（ชื่อรุ่นรถ）、`txtdescmodelname`（**ชื่อรถ**）、`txtmanuyear`（ปีที่ผลิต，如 2026）。仓库已有：DMS 本地快照「vehicles」里就存着每个车型主档表单的这些字段（`snapshots/directory.py:114 car_names` 正是读 `txtdescmodelname`），按 `txtmodelcode` 对上报表 A 列即可，**不需要再读一次 MR.ERP**。快照里没有这个车型（新车型还没同步）→ 车名回落 ชื่อรุ่นรถ → 再没有就只显示代码。
- 年款：报表按「车」没有年款，只能按车型主档的 ปีที่ผลิต 取（车型级别）。样表「26 MY」我自己定为：转换表**按车型的 ปีที่ผลิต 分段**，每段段头「รถรุ่น 26 MY ว่าง Model/Color」；全部同一年款时外观与样表一致；主档没填年份的段头不写年款。理由：不写死「26」，明年不用改；不另增筛选。
- **数据范围随登录账号**：`sale02`（普通销售）打开表单时分店 / 团队为空，原样提交得 0 行（网页上会被「สาขา เป็นค่าว่าง」拦）。所以「用谁的 DMS 账号去导出」直接决定看到什么，见待问 1。

**快捷按钮建议（3 个，标签已按 ≤20 字核过）**

| 按钮（th / zh / en / ja） | 转换表 | 原件两份（MR.ERP） |
| --- | --- | --- |
| รถว่าง / 空车 / Available / 空き車 | 只取 สถานะรถ 以「รถว่าง」开头的车（含 รถว่าง-โชว์，见待问 2） | `selallocate=1`（MR.ERP 不能按状态筛，原件是全部，标题「-ทั้งหมด」） |
| ทั้งหมด / 全部 / All / 全部 | 全部车，表头去掉「ว่าง」 | `selallocate=1` |
| ยังไม่จัดสรรทีม / 未分配团队 / No team yet / チーム未割当 | 全部状态、只限未分配团队 | `selallocate=2`（原件与转换表同范围） |

「空车」放第一个（=样表）。不加按分店 / 车型的打字筛选（样表没有，范围已随账号）。

## B. 取数路子

- 现有 DMS 全部读 MR.ERP 都走 `adapter/operations.run_logged_in(endpoint, fn)`：Playwright 真浏览器登录（`adapter/session.py:_perform_login`，填 `#txtusers/#txtpasswords` 点登录，读 `checklogin.php` 回包），`fn(client, adapter)` 里用同一会话的 `client._post_text` / `adapter.transport().get/post`（`client/transport.py`，`Resp` 带 `content` 字节），做完注销；有会话缓存、慢登录阶梯、连不上冷却、DMS 账号互斥等待。菜单 5 销售记录用的是**操作员自己的 DMS 账号**（`query/snapshot_sync.py` 文件头：「one employee login per request」），读完把结果 reply 给发起人。
- 本报表**走同一条路**：`run_logged_in` 里 ① GET `rpcarallocate/form.php` 取预填的隐藏字段（=MR.ERP 按该账号给的范围）；② 同一份表单 POST `component/report/1.php` 拿 xlsx 字节、POST `2.php` 拿 PDF 字节；③ 注销。不需要新登录模块、不需要管理员会话。抓取函数自己只许这三个地址（同 `snapshot_reader` 的 `READ_PATHS` 做法），防止误发写请求；若待问 1 改用管理员账号，还要把这三个地址登记进 `adapter/admin_read.py` 的只读闸门（`_READ_ONLY_POST_PATHS`，它们是 POST 但不写数据）。
- 耗时实测：测试库登录 0.1–0.2 秒、表单 0.1 秒、两份导出各 ≤0.1 秒（9 台车；本机直连）。生产上慢的是 Playwright 冷登录（代码注释记生产实测 6–16 秒，卡 TLS 握手；会话缓存命中时快得多），加上解析与出图约 2–4 秒，**预计 10–25 秒**，在 LINE 加载动画 60 秒 / 我们兜底 55 秒之内。真实经销商库存几百台时导出时间未实测，第 11 栏在 testdms 上补测一次。

## C. LINE 交互（官方文档 2026-11-26 复核）

- 回复令牌：收到 webhook 后约 **1 分钟**内使用，只能用一次；一次回复最多 **5 个**消息对象；quick reply 最多 13 个；加载动画只在一对一聊天、5–60 秒（步长 5）；**reply 不计入月度消息额度**，push / multicast / broadcast 计（pricing 页 "Reply messages" 列在不计数的方式里）。图片消息须 HTTPS、JPEG / PNG、原图 ≤10 MB、预览图 ≤1 MB（主会话 11-25 已查）。
- 流程（全程 reply）：
  1. 点富菜单「报表查询」→ 文案 + 快捷按钮【1 库存停放报表】【2 销售记录】（只显示有权限的那几个；形状同现有 `query_cards.query_type_message`）。
  2. 点 1 → 文案 + 快捷按钮【空车】【全部】【未分配团队】。
  3. 点范围 → 立刻开 LINE 原生加载动画，交后台任务；用共用件 `pearnly_core.line_ui.receipt`（DMS 经 `line/out.begin/deliver`，11-23 已接入）拿这次的回复令牌回结果：**图片 1–3 张 + 一张 Flex 卡片（4 个按钮）**，合计 ≤5 条，一次回完。
  4. 55 秒还没好 → 回兜底：沿用查询现有「正在读取」原句 +【查看结果】（11-23 评估 D7 已定，不新增文案）；结果出来后存着，点【查看结果】再用那次的令牌回。全程不 push。
  5. 失败 → 回现有 `TXT_QUERY_FAILED`「从 DMS 读取资料失败，请重试。」；没绑定 / 无权限 → 现有 `TXT_DENIED`。
- 下载入口：11-26 用户先提改成原生快捷按钮，得知快捷按钮点一个后整排可能消失后定回卡片（原话「那算了，还是做成卡片吧」，主会话转达）；维持一张 Flex 卡片四个按钮。
- 语言：报表固定文字、卡片按钮、选择提示与按钮、0 行那句都按发起人的 LINE 语言（webhook `line/handlers.py` 与后台任务 `line/out.run_guarded` 都在 `i18n.line_language(user)` 块里），不用站点默认语言；汇总文件名用同一语言的标题，日期戳与「更新时间」同一规则（泰文佛历、其它公历）。用户 11-26：「不要写死中文」。
- 卡片口径对齐 `dms-line-cards-vs-text`：选择步骤用「文案 + 原生快捷按钮」；结果的 4 个下载入口用一张 Flex 卡片（用户点名要按钮卡片，属结果卡，不是提示）。按钮为 URI 动作，地址加 `openExternalBrowser=1` 让手机用系统浏览器下载（LINE 内置浏览器下载 xlsx 在安卓上不可靠，施工时三星真机核实；不行就去掉该参数改为内置浏览器打开预览）。
- 图片太长看不清：一张图最多约 40 行（宽 1080 px），超过拆第 2、3 张；超过 3 张（约 120 个车型颜色组合）只出前 3 张，卡片照给，完整内容看文件。

## D. 图片与文件：生成、存放、下载

- **转换后 Excel**：`openpyxl`（镜像已锁 3.1.5，`frappe.Dockerfile:55`）。列：ชื่อรถ / Model·Color / จำนวน / สถานที่จอดรถ；按年款分段，段内按车型代码、颜色代码排序；末行合计与「ข้อมูล ณ 26/11/2569 11:20」（佛历，与 MR.ERP 一致）。
- **转换后 PDF 与图片**：同一份 HTML 表格，用 DMS 已在用的 Playwright Chromium（镜像已装，`frappe.Dockerfile:89–94`）一次出 `page.pdf()` 与 `page.screenshot()`（PNG），趁登录用的浏览器还开着做，不另起进程。泰文字体随 DMS 应用带一份 Sarabun（OFL，仓库 `scripts/fonts/` 已有 Bold，施工时补 Regular，约 90 KB）用 `@font-face` 引用。**不引新依赖**；不用 Pillow 画（Pillow 无 raqm 时泰文上下标会叠错）。
- 文案语言：转换表的固定文字（表头、合计、更新时间）跟发起人的 LINE 语言（th / zh / en / ja），泰文照样表原词；数据（车名、地点）照 MR.ERP 原样。原件两份是 MR.ERP 的原文件，不动、保留原文件名。
- **存放**：生成物不是原件，ADR-024 第 6 条明确「生成的单据 PDF」不进原件层。存为本站**私有 Frappe File**（`is_private=1`，挂在新 DocType「DMS Stock Report」一条记录上：发起人、范围、时间、5 个文件、过期时间）。单次约 0.2–0.6 MB（测试库：原件 PDF 155 KB、xlsx 8 KB；图片估 100–300 KB）。
- **下载 / 图片地址**：一个 `allow_guest` 白名单接口 `pearnly_dms.api.stock_report.file?t=<令牌>`，令牌 = 记录名 + 文件种类 + 过期时间的 HMAC（用站点 `encryption_key` 派生），LINE 服务器取图片、手机点按钮都走它；过期 / 被篡改 / 记录已删回 404（官方「文件不存在」页）。**建议有效期 24 小时**（库存数据时效短；过期了在菜单再点两下就有新的）。
- **清理**：挂进 DMS 现有的定时任务（不新增 cron 项、不新增常驻进程），删 24 小时前的记录及其文件（File 一起真删）。

## 1. 现有同类与复用

- 菜单入口、权限门、快捷按钮形状：`query/query_flow.py`、`query/query_cards.py`、`query/query_access.py`（扩展，不新建流程）。
- 登录与读 MR.ERP：`adapter/operations.run_logged_in` + `adapter/session.py`（复用）。
- 车名 / 年款：`snapshots/directory.Directory`（新增一个按 `txtmodelcode` 取 ชื่อรถ / ชื่อรุ่นรถ / ปีที่ผลิต 的读法，复用快照）。
- 等结果与兜底：`pearnly_core.line_ui.receipt` 经 `line/out.begin/deliver/show_result`（复用）。
- 富菜单图：`scripts/draw-dms-line-menu-originals.py`（泰文原图）+ `scripts/render-dms-line-menus.py`（其余三语）+ `menu/rich_menu.publish`（复用，重出图）。
- 新建的只有：报表抓取与转换模块（`pearnly_dms/reports/stock_location.py` 一类域包）、下载接口、一个 DocType。

## 2. Frappe 原生能力

- 私有 File、DocType、白名单接口、定时任务钩子、权限查询都用原生。界面不涉及 Desk 组件（LINE 端）。DocType「DMS Stock Report」只读、只给 DMS Admin 在 Desk 看（不加菜单入口）。

## 3. 按类通用

- 属于「LINE 菜单触发的 MR.ERP 只读读取」一类：与销售记录同一权限门、同一等待机制、同一失败文案。
- 文件下载接口与令牌写成通用的「LINE 生成文件下载」（DMS 域内），以后其它报表 / 进群推送复用；本次只有这一个调用方，不提前搬进共用层。

## 4. 上下游数据

- 只读 MR.ERP，不写任何东西；不影响 DMS 推送记录、订车、客户主档、快照同步。
- 车型主档取本地快照：快照比 MR.ERP 新加的车型慢一拍时车名回落 ชื่อรุ่นรถ / 代码（不额外读）。
- Express、CC 桌面版、ERP、Cowork：不涉及（DMS 专属）。

## 5. 隔离与权限

- **更正（用户 2026-11-26，看着操作员弹窗说，主会话转达）**：「权限是这个吧？允许在 LINE 使用查询功能，勾选了即可用这个菜单，未勾选就不可以用」。所以**不新增勾选项、不加补丁**：库存停放报表与销售记录都只看现有的 `can_query_dms`（界面「允许在 LINE 使用查询功能」，文字不改）。勾了整个「报表查询」（1、2 都）可用，没勾就没有；富菜单挂载条件不变。原先写的「新增 `can_stock_report`、初值 = `can_query_dms`」作废。
- 服务端每一步（打开报表查询、点 1、选范围、后台出结果前后）都再查一次：绑定有效 + 操作员启用 + 勾了这一格（`query_access.can_query`）。
- 数据范围：用**操作员自己的** MR.ERP 账号导出（主会话 11-26 定，同销售记录口径，MR.ERP 自己的分店 / 团队权限原样生效，不越权）；得到 0 行回一句「没有可显示的车辆」、不出下载卡片。
- 多租户：每租户各自站点、各自 DMS 端点与官方号；文件存本站私有目录，令牌按站点密钥签，跨站无效。
- 下载链接 = 持有即可下载（24 小时），被转发给别人也能下——这是「点按钮直接下载」的代价，若不接受见待问 3。

## 6. 入口：网页、手机、LINE、拍照

- LINE：唯一入口（富菜单格 + 文字「เมนู」后的菜单卡第 5 项，两处同改）。
- DMS 网页：**不做**同一份报表（默认不做；网页用户可直接在 MR.ERP 网页导出原件）。DocType 只读记录在 Desk 可见，供 DMS Admin 排查，不进工作区菜单。
- 拍照 / 相机：不涉及。

## 7. 共用件与连带回归

`python3 scripts/check-impact.py --files query/query_flow.py query/query_cards.py menu/rich_menu.py line/out.py i18n/messages.tsv adapter/operations.py` 输出（11-26）：

```
[auto]   dms.line.webhook-sim  DMS · LINE 模拟 webhook（实验栈） · scripts/smoke/line_flow.py
[manual] dms.line.chat         DMS · LINE DMS 测试号一对一 · 菜单 1/2/5/6、查询 …
[manual] dms.line.binding      DMS · 人员页绑定码 / 解绑 / 停用 → 绑定变化后富菜单重挂
[manual] dms.ocr.id-card       DMS · 身份证识别
[manual] dms.execution         DMS · 证件识别、订车、查询、实时同步
```

- 不改 `pearnly_core`（receipt、line 都只调用）；`adapter/operations.py` 不改。
- 冒烟：`scripts/smoke/line_flow.py` 加「报表查询 → 1 → 空车」一条（离线桩 MR.ERP 回包）；产品地图目前没有 DMS LINE 的地图文件（`scripts/impact/product-map/` 只有 erp / cowork / shared），本次在 `shared-parts` 的 `dms.line.chat` 入口说明里补上新菜单项，不另建 DMS 地图（若主会话要求则新建 `dms.toml` 登记 `dms.line.stock-report`）。

## 8. 存储与成本

- 外部调用：每次点击 = 1 次 MR.ERP 登录 + 3 个请求；LINE 只用 reply（**不计额度，0 元**）。
- 存储：每次 ≤0.6 MB，24 小时后删；按每租户每天 20 次算峰值 ≤12 MB / 租户，可忽略。
- 不用识别、不用云存储、不新增常驻进程、不新增定时项、无新增固定月费。**零常驻、零按量。**

## 9. 删除、作废、追溯

- 记录与文件 24 小时后真删（轻便工具口径）；不留追溯（只读报表，MR.ERP 是源头）。Error Log 照旧记失败。
- 不写 Express / MR.ERP，无作废问题。

## 10. 文案与官方 UI

**改已有文案（用户已同意改名「报表查询」，以下译名与连带改动请主会话转用户确认）**

| 键 | 现在（zh / th / en / ja） | 改为（zh / th / en / ja） |
| --- | --- | --- |
| `dms.card.rich.query.title`、`dms.card.TXT_MENU_ITEM5` | 查询资料 / ค้นหาข้อมูล / Search data / データ検索 | 报表查询 / รายงาน / Reports / レポート |
| `dms.card.rich.query.sub`（富菜单小字） | DMS 最新销售 / ยอดขายล่าสุดจาก DMS / Latest sales from DMS / DMS の最新販売 | 库存与销售 / สต๊อกและยอดขาย / Stock and sales / 在庫と販売 |
| `dms.card.query.TXT_PICK_TYPE` | 请选择要查询的资料类型 / กรุณาเลือกประเภทข้อมูลที่ต้องการค้นหา / … | 请选择报表 / กรุณาเลือกรายงาน / Please choose a report / レポートを選んでください |
| `dms.card.query.TYPE_SALES` | 1 销售记录 / 1 บันทึกการขาย / 1 Sales records / 1 販売記録 | 2 销售记录 / 2 บันทึกการขาย / 2 Sales records / 2 販売記録 |

富菜单是**图片上的字**（泰文原图 `public/images/menu/rich-query.png` 由 `draw-dms-line-menu-originals.py` 画，其余三语由 `render-dms-line-menus.py` 生成），改名要重出 4 张图（泰中英日 × 带查询版），随部署对每个租户执行现有菜单发布（内容散列变了会新建菜单并重挂，已绑定用户下一次发消息时按语言换上）。基础版菜单不变。

**新增文案**（按官方风格直接写，施工时过 `check-copy.py`）：按钮「1 库存停放报表 / 1 สต๊อกตามที่จอดรถ / 1 Stock by location / 1 在庫・保管場所」；范围提示「请选择范围 / กรุณาเลือกขอบเขต / Choose a scope / 範囲を選んでください」；三个范围按钮见 A 节；卡片按钮「原件 Excel / 原件 PDF / 汇总 Excel / 汇总 PDF」（th「ต้นฉบับ Excel / ต้นฉบับ PDF / สรุป Excel / สรุป PDF」）；转换表表头（th 照样表）；花名册勾选项「库存停放报表」。无说明性文案。

## 11. 测试与验收

- 单元 / 离线：Excel 解析（含 0 行、合计行、合并单元格、状态列变体）、汇总（车型 + 颜色、位置斜杠串、年款分段、车名三级回落）、令牌签发与过期 / 篡改、权限门四条（未绑定、停用、未勾、已勾）、receipt 成功 / 兜底 / 查看结果、消息条数 ≤5。`check-dms-offline.py`、架构检查、`check-copy.py`、候选离线道。
- 实验栈冒烟：`line_flow.py` 新增一条（桩 MR.ERP）。
- testdms（测试租户，经测试官方号）：用**专门的测试操作员**（不绑真实账号）走一遍三种范围，记录真实耗时；下载四个文件核对内容与 MR.ERP 网页导出一致；过期令牌回 404。
- 三星真机（adb，测试官方号对话）：看图片清晰度、四个按钮在系统浏览器能下 xlsx / PDF、泰文显示；测完删手机上的测试下载文件。
- 用户 / 岳父只需看一次真机截图确认样式像样表。

## 12. 要问用户 / 岳父的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 报表用谁的 MR.ERP 账号导出？ | **操作员自己的账号**：MR.ERP 给他看哪些分店就出哪些，不越权；普通销售账号若在 MR.ERP 里没有分店 / 团队范围会得到空表（测试库 `sale02` 实测 0 行），需要时在 MR.ERP 给他开范围 | 若要「勾了就看全公司」，改用租户配置的管理员账号读，等于绕过 MR.ERP 的分店权限，要用户明确同意 |
| 2 | 「空车」是否包括「รถว่าง-โชว์」（展示车）？ | 包括（状态名以 รถว่าง 开头都算） | 不定就按建议做；岳父说不含就改一个判断 |

其余（年款分段、按钮选项、文件存放与 24 小时有效期、语言跟随、新勾选项初值）已按上文自定，理由见各节。下载链接持有即可下（5 节末）若用户不接受，改为点按钮先经 LINE 登录（LIFF）再下，工作量 +2 小时。

## 13. 施工拆分与机器耗时

| 步 | 内容 | 写代码 | 机器跑 | 依赖 |
| --- | --- | --- | --- | --- |
| 1 | 抓取与转换：`run_logged_in` 内取表单 → 导出两份 → 解析 → 汇总 → openpyxl / Chromium 出 xlsx、PDF、PNG；只读路径登记；离线测试 | 1.5 h | 0.3 h | 待问 1 |
| 2 | 存放与下载：DocType、私有 File、令牌接口、并进现有定时清理；测试 | 1 h | 0.2 h | 1 |
| 3 | LINE 流程：报表查询二级按钮、范围按钮、receipt 回图 + 卡片、权限勾选项 + 补丁 + 富菜单挂载条件；四语词条 | 1.5 h | 0.3 h | 1、2 |
| 4 | 富菜单改名：改词条、重画 4 张图 | 0.3 h | 0.2 h | 文案确认 |
| 5 | 全闸门、候选离线、code-review（medium）、推分支等检查 | — | 1 h | 1–4 |
| 6 | testdms + 三星真机验收（测试机与三星按占用表排） | — | 1 h | 5、主会话排期 |

合计约 **4.3 h 写代码 + 3 h 机器**，另加等主会话排测试机与用户确认文案的时间；部署后逐租户发布菜单由主会话按部署流程做。

**以后加「进群定时推送」可复用**：第 1 步的抓取 / 转换整块（输入 = 端点 + 账号 + 范围，输出 = 5 个文件）、第 2 步的存放与令牌下载、图片消息与 4 按钮卡片的组装；新增的只有群绑定、定时触发和 push（按群人数计费，需另评估成本）。


## 审定与施工记录（2026-11-26）

主会话审定：待问 1 = 操作员自己的账号；待问 2 = 「空车」含 รถว่าง-โชว์（`stock_report.is_available` 一处）；其余照评估。施工分支 `pearnly/dms-line-stock-report`。
与评估的出入：文件只存私有 File（文件名前缀 `dms-stock-`），不另建 DocType「DMS Stock Report」——一次报表的五个文件不需要另一张表来关联，清理按文件名前缀与创建时间删，少一张表、少一次迁移。

### 已批准的文案改动（用户 2026-11-26「可以」，主会话转达）

| 键 | 原（zh） | 改为（zh / th / en / ja） |
| --- | --- | --- |
| `dms.card.TXT_MENU_ITEM5`、`dms.card.rich.query.title` | 查询资料 | 报表查询 / รายงาน / Reports / レポート |
| `dms.card.rich.query.sub` | DMS 最新销售 | 库存与销售 / สต๊อกและยอดขาย / Stock and sales / 在庫と販売 |
| `dms.card.query.TXT_PICK_TYPE` | 请选择要查询的资料类型 | 请选择报表 / กรุณาเลือกรายงาน / Please choose a report / レポートを選んでください |
| `dms.card.query.TYPE_SALES` | 1 销售记录 | 2 销售记录 / 2 บันทึกการขาย / 2 Sales records / 2 販売記録 |

富菜单带查询版四语图已按新词条重画（`rich-query{,-en,-zh,-ja}.png`）；基础版与菜单卡图标像素不变，未提交。

### 菜单入口清单（主会话要求两处同步）

1. 关键词（เมนู / 菜单 / menu）弹出的菜单卡第 5 项：标题 `TXT_MENU_ITEM5`，点了 postback `action=menu_query`。
2. 六宫格富菜单（带查询版）那一格：图上字为 `rich.query.title / .sub`，格子的 label 用 `TXT_MENU_ITEM5`，postback 同为 `action=menu_query`。
3. 菜单态打字「5」：`menu_flow._MENU_CHOICES["5"]` → 同一个 `query_flow.open_query`。
4. 聊天记录里的旧按钮（`action=menu_query`）：同上。
DMS 网页没有这个入口；没有可直接打字进入查询的关键词。四处都进 `open_query`，先出「1 库存停放报表 / 2 销售记录」；`tests/test_dms_stock_report.py` 断言两处名称四语一致、都走同一个选择。

### 真机验收（2026-11-26 12:01–12:07，测试机1 测试环境副本，镜像 fdf20d98 + 本分支实时源码 9995e6026；为过公开扫描改写历史后同一代码为 c6b6b7c1c，只换了测试数据里的一个车型代码）

做法（主会话批方案 1）：testdms 切到测试环境；用副本里已有的 skin 绑定（三星这个 LINE），不新建、不重绑；skin 的 DMS 账号只在副本里临时换成 dmstest 拍有数据的几项、换回 sale02 拍没有分店范围的 0 行；权限勾只在副本里取消再勾回。富菜单发布到测试官方号前记下了原状（25 套、默认 richmenu-0ea96ece…、各用户挂载），测完删掉新建的 5 套、默认与挂载恢复，回读逐项一致；副本的菜单配置也改回原值。线上 testdms 的操作员与绑定没动。截图在 `~/pearnly-devtools/stock-report-out/shots/`（只截对话内）。

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 入口①：打 menu 弹出的菜单卡第 5 项「报表查询 / 库存与销售」→ 先出「1 库存停放报表 / 2 销售记录」 | 通过 | d3、d4 |
| 入口②：六宫格「报表查询 / 库存与销售」→ 同一个选择 | 通过 | e1、e2 |
| 范围三个按钮「空车 / 全部 / 未分配团队」 | 通过 | d5、e3 |
| 空车（dmstest）：LINE 原生加载动画 → 1 张汇总图 + 四按钮卡片，合计 5 台 | 通过，点按钮到出结果 ≤7 秒 | d6a、d6b |
| 全部（dmstest）：合计 9 台 | 通过，约 6 秒 | t3、t6、t14 |
| 未分配团队（dmstest）：「没有可显示的车辆。」，不出卡片 | 通过 | e5 |
| 没有分店范围的账号（sale02）选全部：同上一句 | 通过 | e6 |
| 四个下载按钮在三星系统浏览器（Chrome）下载：原件 Excel 7,747 B、原件 PDF 155,248 B（MR.ERP 原文件名含「-ทั้งหมด」）、汇总 Excel、汇总 PDF | 通过；原件 Excel 与同时刻 MR.ERP 网页导出逐行一致，原件 PDF 除日期行外文字一致 | d7、phone/ 与 web/ 比对 |
| 没勾「允许在 LINE 使用查询功能」：点旧六宫格那一格 → 服务端回无权限那句；六宫格换成基础版、菜单卡没有第 5 项 | 通过 | f1、f2 |
| 过期 / 篡改 / 错签名的下载地址 → 404（经公网域名 testdms.pearnly.com） | 通过 | server-run.json 与 curl 记录 |
| 真实 MR.ERP 测试库服务端实读三个范围 | 通过，1.1–1.6 秒 | server-run.json |

没测到：泰文语言下的手机画面（三星这个 LINE 是中文；泰文富菜单图与出图样张见四语图与 stock-*.jpg）；几百台车的真实数据量（测试库只有 9 台，600 台桩出图约 1 秒）。

真机记录 | dms.line.stock-report | 2026-11-26 | c6b6b7c1c | 三星 SM-A146P · DMS-TEST 官方号 · testdms 测试环境副本 | 通过 | 两处入口（菜单卡第 5 项、六宫格）→ 1 库存停放报表 → 空车 / 全部 / 未分配团队；图片 + 四个下载按钮；0 行一句；无权限拒绝；四个文件在系统浏览器下载并与 MR.ERP 网页导出一致
