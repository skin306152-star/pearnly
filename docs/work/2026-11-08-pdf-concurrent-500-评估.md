# 需求影响评估：同一网页进程两份 PDF 同时生成偶发 500

- 主会话 local_e395dace 派（交接队列第 5 项），窗口工作树 `.claude/worktrees/frosty-faraday-cdfc2b`，分支 `pearnly/frosty-faraday-cdfc2b`，
  基线 `2ead204d5`（= 当前 origin/main，11-07 夜上线的提交）。锁定版 Frappe `16.33.0`、cssutils `2.11.1`（生产镜像
  `sha256:1560bb95…` 内实查）。
- 本文先交主会话审定，审定后再施工。原型代码与测试已写在工作树里（未提交），只为拿下面的对照证据。

## 0. 需求原文与理解

- 来源：11-07 清欠测试基线（`docs/work/2026-11-07-untested-baseline-flows.md`「发现的问题」），错误原文在共享笔记
  `local_3d36d798…/PDF并发500-错误原文-11-07.txt`：采购单「补开收据」请求 500，`xml.dom.SyntaxErr: CSSStyleDeclaration: Syntax Error in Property: font-family: "InterVariable", "Inter", -apple-system", …`。
- 理解：同一个网页进程里两份 PDF 同时生成时，偶发其中一份 500、单上没有那份 PDF。要一次修好、不改打印外观。
- 不在范围：打印版面、字体、文案；下面「另外发现」的凭证并发死锁（单独报主会话，不在本项里顺手修）。

## 根因（已在锁定版源码与实验栈确认）

1. 框架默认打印字体写坏了：`frappe/www/printview.py` `get_font()` 默认值（第 628 行起）
   `"InterVariable", "Inter", -apple-system", "BlinkMacSystemFont", …`——`-apple-system` 前少一个引号。打印设置字体为 Default 时
   （新开户、实验栈都是 Default），每份打印页 / PDF 的 `standard.css` 都带这一句；浏览器与 wkhtmltopdf 都把这条声明整条丢掉，所以平时看不出来。
2. Frappe 出 PDF 前用 cssutils 解析整页样式取页边距：`frappe.utils.pdf.get_pdf → prepare_options → read_options_from_html → get_print_format_styles → cssutils.parseString`。
3. cssutils「解析出错是记日志还是抛」是**进程级**开关 `cssutils.log.raiseExceptions`（默认「抛」）。`cssutils.parseString` 每次新建一个
   `CSSParser`：建时记下当时的开关值，解析中改成「不抛」，解析完改回记下的值（`cssutils/parse.py` `__init__` / `__parseSetting`）。
   同一进程两个线程交错：A 建（记「抛」）→ A 改「不抛」→ B 建（记「不抛」）→ **A 先结束改回「抛」** → B 撞上坏字体就抛 `SyntaxErr` → 500；
   开关最后停在哪个值，看最后结束的那个解析器记的是什么，所以它会来回翻：只要有一份正在解析时另一份结束、改回「抛」，这一份就中。
   同时出 PDF 的人越多越容易中；平时一个人一份一份打不会中——这就是「偶发」。
4. 生产网页是 gunicorn `gthread`（每进程 4 线程，`infrastructure/production/compose.yml`），满足条件；实验栈是 `bench serve` 多线程，同样满足。

### 复现证据

| 层 | 做法 | 不修 | 修（原型） |
|---|---|---|---|
| 纯库（生产镜像） | 4 线程 × 40 次解析带坏字体的样式（`sys.setswitchinterval(1e-5)` 放大交错） | 160 次中 2 次 `SyntaxErr` | 0 |
| 锁定版 Frappe 代码（生产镜像，确定性） | 用 Frappe 自己的 `get_print_format_styles`、`get_font` 默认值、`standard.css`；B 解析中让更早建好的 A 跑完一遍 | 必抛 `SyntaxErr` | 不抛，`.print-format` 其余样式照常取到 |
| 实验栈 HTTP（`pearnly-pdflock-lab`，镜像 `1560bb95`，源码本工作树） | 每轮重启网页进程，立刻并发 12 个「补开收据」（12 张不同的单），各 10 轮 | 120 份里 `SyntaxErr` 至少 17 份，10 轮里至少 6 轮中（第 1、2 轮只记了前 6 条错误） | 120 份里 `SyntaxErr` **0** 份 |
| 实验栈 HTTP | 每轮重启后并发 7 个打印格式「存 PDF」×2，共 8 轮 | 112 份全 200（没撞上） | — |

打印格式那条没撞上的原因：我们七个格式的 HTML 里内嵌了泰文字体（约 470 KB 的 base64 样式），坏字体在最前面（第 2 KB），
后面要解析很久；两份同时开始时，先结束的那份结束时另一份早已过了坏字体。凭证（`purchasing/voucher.html`）只有标准样式，很短，最容易中。
两类混着来（一份打印格式 + 一份凭证）也会中。

## 1. 现有同类与复用

- 全仓经框架出 PDF 的入口只有 ERP（Cowork、DMS、控制面、CC 桌面版、LINE 都没有，`grep get_pdf / download_pdf / printview` 实查）：
  - 采购单「…」→「补开收据」「预扣税凭证」：`purchasing/api.py:api_purchase_voucher → purchasing/output.py:voucher → frappe.utils.pdf.get_pdf`；
  - 七个标准打印格式（`pearnly_erp_portal/print_format/*`：收货单、领料单、采购收货单、销售单、盘点、收银小票、交班小结）在原生打印页
    （`/printview`）里点「PDF」，以及盘点打印弹窗「PDF」（`stocktake.js:240 → p.trade.print(..., {pdf: true}) → frappe.utils.print_format.download_pdf`）。
  - 浏览器里直接看 / 打印 `/printview` 不走 cssutils，不受影响。
- 结论：不新建业务功能；在共用层加一个钩子，覆盖上面所有入口。

## 2. Frappe 原生能力

- 打印设置 `Print Settings.font`（Select：Default / Helvetica Neue / Arial / Helvetica / Inter / Verdana / Monospace）、打印格式自己的 `font` 字段：
  选非 Default 就不再输出那句坏字体（方案 a）。
- 没有原生办法改 cssutils 的进程级开关，也没有原生钩子能包住 `get_print_format_styles`；Frappe 原生钩子 `before_request` / `before_job`（锁定版
  `frappe/app.py:244` 每个请求都跑）可以在请求开始时设定进程状态（方案 c 用它）。

## 方案比较

| | 做法 | 能不能一次到位 | 外观 | 并发打印耗时 | 代价 / 风险 |
|---|---|---|---|---|---|
| (a) 打印设置配有效字体 | 每个站点 `Print Settings.font` 改成 Inter（补丁 + 开户默认） | **不能单独到位**：只去掉这一句坏样式；开关照样来回翻，打印格式 / 打印样式里只要还有一句 cssutils 认为有语法错的样式（用户自定义 CSS、以后框架升级），同样会中 | **会变**，见下「改前改后截图」 | 不变 | 每个租户一份数据、新开户要带；改外观要用户点头 |
| (b1) 打印入口加进程内锁 | 我们的凭证 `get_pdf` 外面包一把进程锁 | 不能：原生 `download_pdf`（七个格式「PDF」）是框架接口，包不到，除非改写框架函数 | 不变 | 同一进程的 PDF 排队：实验栈一份凭证单独出约 0.54 秒（8 份顺序 4.3 秒），12 份并发约 2 秒出完；加锁后变成顺序，约 6.5 秒，最后一个人多等约 4.5 秒 | 锁住的是整段 wkhtmltopdf（每份约 1 秒），人多时排队 |
| (b2) 只锁 cssutils 解析 | 替换 `frappe.utils.pdf.get_print_format_styles` 为加锁版 | 能 | 不变 | 几乎不变（只锁几毫秒解析） | 等于改写框架函数（猴子补丁），升级 Frappe 时要跟；违背「不改 Frappe、只用扩展点」 |
| (b3) 自有 App 里修正 CSS 来源 | 凭证里把坏字体串替换掉；七个格式在打印格式上配 `font` | 不能：同 (a)，只去掉这一句；七个格式配字体也会变外观 | 七个格式会变 | 不变 | 同 (a) |
| **(c) 建议：进程默认改成「不抛」** | 共用层 `pearnly_core.desk.print_css.lenient_css_parsing` 挂 `before_request`（第一个）和 `before_job`：`cssutils.log.raiseExceptions = False` | **能**：之后建的解析器记下的、改回的都是「不抛」，开关不再翻，任何坏样式、任何出 PDF 入口都不会因别的线程而抛 | **不变**：生成的 HTML 一字不改；解析结果与单线程时一字不差（`parseString` 本来就在「不抛」下解析） | 不变（每个请求多一次属性判断） | 见下「副作用」 |

结论：**(c) 单独就是一次到位的修法，不需要和 (a) 并用**。(a) 只能去掉今天这一句坏样式，修不了「开关是进程级」这个根因，还会改外观。

## 主会话审定（11-08）

方案 (c) 施工，(a)(b) 不做。施工时补三点，结论如下。

### 1. cssutils 的全部使用者，谁依赖「出错就抛」

在生产镜像（`sha256:1560bb95…`）里对 `apps/frappe`、`apps/erpnext`、整个 `site-packages`，以及本仓 `apps/`、`companion/`、`scripts/`、`infrastructure/` 全量 `grep cssutils`：

| 使用者 | 怎么用 | 原来靠不靠「抛」 | 改「不抛」后 |
|---|---|---|---|
| `frappe/utils/pdf.py:317` `get_print_format_styles` | `cssutils.parseString` 解析整页样式，只读规则取页边距等 | 不靠：`parseString` 解析期间本来就是「不抛」；本项要修的就是它被别的线程意外改成「抛」 | 单线程结果一字不差；并发时不再 500 |
| `frappe/utils/pdf.py:29` | `cssutils.log.setLog(frappe.logger("cssutils"))`，只换日志去处 | 不涉及 | 不变 |
| `frappe/email/email_body.py:471` `inline_style_in_html` → premailer（`premailer.py:71/200` `parseString`、`:519` 与 `merge_style.py:26` `parseStyle`） | 发邮件时把样式内联进 HTML；解析完只读规则、拼字符串 | 不靠：全部是 `parse*` 调用（解析期间本来「不抛」），解析后没有给样式对象赋值的写法 | 不变 |
| `frappe/email/doctype/notification/test_notification.py:771` | 测试数据里的一段文字，不是调用 | 不涉及 | 不涉及 |
| 本仓各 App、CC 桌面版、脚本 | 没有任何直接使用 | 不涉及 | 不涉及 |

- 「保存自定义 CSS / 打印样式时校验」：锁定版里没有这种校验（Print Style、Print Format 的 css 字段、Website Theme 都不经 cssutils，实查），所以没有一条路径因为「不抛」而少拦一次。
- 全仓（含 `site-packages`）改 `cssutils.log.raiseExceptions` 的只有 cssutils 自己的 `parse.py:75/77`（就是那对「改成不抛 / 改回」），没有人设成「抛」，也没有人用 `CSSParser(raiseExceptions=True)`。

### 2. 会不会刷满日志

- 不会多写。cssutils 的日志由框架接到 `frappe.logger("cssutils")`：写 bench 的 `logs/cssutils.log` 和站点的 `sites/<站点>/logs/cssutils.log`，
  都是 `RotatingFileHandler`（单文件 100 KB、留 20 份，各自上限约 2 MB），**不进网页 / 后台进程的标准输出**（实验栈 `docker logs` 里 0 行）。
- 现在单线程出 PDF 本来就在「不抛」下解析，每份 PDF 已经写好几行 ERROR（坏字体、`var(--gray-900)` 等；实验栈约 500 份 PDF 写了约 6600 行，
  文件已在 100 KB 处轮换）。改了以后行数相同；只有原来会 500 的那一份变成多记一行。所以不另外压日志级别——压了等于改框架的日志口径，不在本项。

### 3. 为什么挂 before_request + before_job，而不是进程启动时设一次

- 框架没有给自有 App 的「进程启动」扩展点：生产 gunicorn `--preload` 在主进程里只导入 `frappe.app`，我们的 App 要等某个请求读 hooks 时才导入；
  hooks 本身还缓存在 Redis，进程可能一直不导入 `pearnly_core` 的某个模块。写在模块导入时就保证不了「在第一份 PDF 之前」。
- `before_request` 是锁定版 `frappe/app.py:244` 每个请求必跑的原生钩子，`before_job` 是每个后台任务必跑的原生钩子，任何出 PDF 的代码都在它们之后执行，
  所以一定先于第一次解析。
- 没有代码会把默认值改回「抛」（上表最后一条），所以每次调用只是读一次属性、已是「不抛」就什么都不做；按请求调只是保证新进程（`--max-requests` 回收、
  重启、后台任务进程）里也一定先设好，不靠哪个模块碰巧先被导入。

### (c) 的副作用核对

- 进程里用 cssutils 的只有 `frappe.utils.pdf`（出 PDF）和 premailer（邮件内联样式，Frappe 发邮件时用；我们的产品发邮件在后台任务里）。
  它们都只调 `cssutils.parseString` / `parseStyle`，解析期间本来就是「不抛」；改了以后，只有「解析完对样式对象再赋非法值」这种情况会从抛变成记日志——
  框架与我们的代码里没有这种用法（全仓与锁定版 Frappe `grep cssutils` 实查）。
- 坏样式照旧写 cssutils 日志（`logs/cssutils.log`，框架 `frappe.logger("cssutils")`），单线程时现在就在写，量不变。
- 钩子放 `pearnly_core` 的 `before_request` 第一个（框架自己的 `frappe.recorder.record` 在更前，它不解析样式）：后面的钩子（登录跳转等）
  可能直接跳转或抛出，这一步要先做；它不读请求、不碰数据库，失败不了。`pearnly_core` 装在所有产品站点上，同一进程服务的每个站点都会跑到。
- 进程刚起、第一个请求前就在解析的情况不存在（解析只在请求 / 任务里发生，钩子先于它们）。

## 改前改后截图（方案 a：打印设置字体 Default → Inter）

在实验栈 ERP 新开户站点上，每个格式取库里最新一张单，打印设置字体 Default（现状）与 Inter 各出一遍：PDF 每页转图逐像素比；
打印页 HTML 用无头 Chrome（本机 Mac）截图逐像素比。截图与对比图在共享笔记 `PDF并发500-截图与日志-11-08/截图/`。

| 打印格式 / 入口 | 出 PDF（wkhtmltopdf） | 浏览器打印页 |
|---|---|---|
| 收货单 Pearnly Goods Receipt Note（入库单打印） | 一致（0 像素差） | 一致 |
| 领料单 Pearnly Goods Issue Slip（出库单打印） | 一致 | 一致 |
| 采购收货单 Pearnly Purchase Goods Receipt | 一致 | 一致 |
| 销售单 Pearnly Sales Document | 一致 | 一致 |
| 盘点 Pearnly Stock Count | 一致 | 一致 |
| 收银小票 Pearnly POS Receipt | 一致 | 一致 |
| 交班小结 Pearnly Shift Summary | 一致 | 一致 |
| 采购「补开收据」凭证 | 一致 | **变了**：衬线体 → 无衬线体（约 2.6 万像素差，见 `voucher-html-before-after.png`） |
| 采购「预扣税凭证」 | 一致 | **变了**（同上） |

- 七个格式不变：版面都包在自己的 `.pp` / `.tp` 里、自己指定了泰文字体 Prompt，`.print-format` 上的字体根本用不到。
- 凭证 PDF 不变：生产镜像的 wkhtmltopdf 没装 Inter，回落到同一个默认字体。凭证只以 PDF 交付（挂在单上下载），用户实际看到的不变；
  浏览器里的差别只在有人直接打开凭证 HTML 时出现（产品里没有这个入口）。但要是以后镜像装了 Inter，凭证 PDF 就会变。
- Cowork、DMS、控制面没有任何打印格式（实查 `print_format` 目录只有 ERP 七个）。
- 结论：(a) 眼下对用户看到的 PDF 外观无变化，但它修不了根因（见上表），不建议做；若主会话仍要做 (a)，凭证 HTML 的字体变化要按规矩转问用户。

## 3. 按类通用

- 类：所有经 `frappe.utils.pdf.get_pdf` 出 PDF 的入口（网页请求与后台任务）。(c) 是进程级设定，按类全覆盖，不按入口打补丁。

## 4. 上下游数据

- ERP 单据流：凭证 PDF 挂在采购单附件上，修后不再丢；其余不变。
- Express：不涉及（PDF 不进 Express）。
- CC 桌面版：不取 PDF，载荷不变。
- Cowork / DMS：没有 PDF 入口；钩子在共用层，对它们的请求只多一次属性判断。

## 5. 隔离与权限

- 不涉及：不读写数据，不改权限；进程级开关对所有站点一样。

## 6. 入口：网页、手机、LINE、拍照

- 电脑网页、手机网页：采购凭证两项、七个格式的「PDF」、盘点打印弹窗「PDF」——都走上面的服务端，同一修法。
- LINE、CC 桌面版、CC 对话：没有出 PDF 的入口（实查），不涉及。拍照：不涉及。

## 7. 共用件与连带回归

- 改 `apps/pearnly_core/pearnly_core/hooks.py`（登记表里「只改到调用方」的 10 个入口）+ 新文件 `pearnly_core/desk/print_css.py`（未登记共用件）。
  `check-impact.py` 清单：`shared.line.login`、`erp.cc.connect`、`shared.exports`、`shared.navigation`、`shared.upload.shrink-photo`、
  `shared.originals.download`、`shared.login`、`shared.execution`、`shared.assistant`、`shared.browser-text`。施工后逐项记「通过 / 不通过 / 没测到（原因）」：
  能在实验栈跑的用现有流程（导出、导航、原件、登录会话、CC 对话页）；`erp.cc.connect`、`shared.line.login` 要测试机 / 真 LINE，按规矩不碰，记「没测到」。
- 打印相关：`erp.print.all`、`erp.purchase.actions` 两条流程（新老数据集）。

## 8. 存储与成本

- 不涉及：没有新数据、文件、外部调用，零月费。

## 9. 删除、作废、追溯

- 不涉及。

## 10. 文案与官方 UI

- 无文案改动；(c) 不改任何界面与版面。

## 11. 测试与验收

- 新测试 `tests/test_pdf_css_threads.py`（开发检查跑登记那一条，候选离线检查在生产镜像里跑 Frappe 那几条）：
  钩子登记在 `before_request` 第一个和 `before_job`；用锁定版 Frappe 自己的代码确定性复现交错（不修必抛、修了不抛）；真线程并发解析 0 次抛错；
  另一条断言「根因还在」（哪天升级 Frappe 后不再抛，提醒复核这个钩子还要不要）。
- 先红后绿（生产镜像实跑，原文在共享笔记 `PDF并发500-截图与日志-11-08/日志/测试-红-*.txt`、`测试-绿-*.txt`）：把钩子函数改成什么都不做，
  确定性交错那条每次都红（`SyntaxErr`）；真线程那条是概率性的，两次里一次红（1 次 `SyntaxErr`）；恢复后 4 条全过。
- 实验栈对照：上表 HTTP 那行（同一套请求，修前 / 修后）。
- 闸门：`gate.sh`、`cand-local.sh offline`、受影响流程、CI；不需要用户看外观（(c) 不改外观）。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 只有选 (a) 才要问：打印字体换成 Inter 后，凭证在浏览器里看是无衬线体（PDF 眼下不变） | 不选 (a)，用 (c)，不用问用户 | 选 (c) 无影响 |

## 13. 工作量与排期

- 施工：钩子 + 测试 + 候选检查登记，已有原型；机器耗时：`gate.sh` 约 10 分钟、`cand-local.sh` 约 30 分钟、受影响流程约 15 分钟、CI 约 15 分钟。
- 不部署，随主会话下一批。

## 另外发现（不在本项，报主会话定）

- **不同采购单同时生成凭证会数据库死锁 500**（`QueryDeadlockError`）。实验栈每轮 12 张不同的单同时点「补开收据」，大部分轮次 12 份里 5–11 份死锁。
  `SHOW ENGINE INNODB STATUS`：两笔都在 `INSERT INTO tabERP Purchase Attachment`，各自持有子表 `parent` 索引末尾（supremum）的 X 间隙锁、又等对方的插入意向锁——
  框架保存子表时先按 parent 删旧行、拿了间隙锁，最新的几张单的附件都落在索引末尾。真实场景是两个人同时给最新的两张采购单出凭证。
  主会话 11-08 交本窗口另修，记录见 [采购凭证两处缺陷](2026-11-08-purchase-voucher-fixes.md)。
- 顺带看到（不在本项）：凭证 PDF 公司地址为空时印出「None」（`purchasing/voucher.html` 的 `company.get('address', '')` 取到 `None`）；
  实验栈里凭证标签是英文（实验账号界面语言下的翻译缺口，未核）。

## 施工结果（11-08）

- 提交：`f17aa1d34`（PDF 并发修法 + 测试）、`5cccaf15d` 与 `c41b6025a`（另两件，见 [采购凭证两处缺陷](2026-11-08-purchase-voucher-fixes.md)）。
- 本地闸门：`gate.sh` 203 项全过（GATE EXIT 0）；候选离线检查 `cand-local.sh offline` 104 组（原 102 + 本次新增 2 组）、LANE EXIT 0。
- 受影响流程（实验栈 `pearnly-pdflock-lab`，新老两套数据，38 格全过）：`erp.purchase.actions`、`erp.purchase.voucher-concurrent`、`erp.print.all`、
  `erp.stocktake.report`、`erp.exports`、`cowork.exports`、`cc.chat`、`cc.settings`、`cowork.intake.review`、`shared.upload.take-photo`、`erp.company.settings`。
- 打印页「PDF」/ 盘点「PDF」（原生 `download_pdf`，流程里没有这条）：实验栈每轮重启网页进程后并发出 7 个格式的 PDF ×2，3 轮 42 份全部 200、都是 PDF。
  （实验栈的 wkhtmltopdf 要在容器 `/etc/hosts` 补站点名才连得上本站，只是实验栈的事，生产不涉及。）

### 共用件连带回归（`python3 scripts/check-impact.py`）

| 入口 | 结果 |
|---|---|
| `erp.purchase.voucher`（transaction 新调用方） | 通过：`erp.purchase.voucher-concurrent`、`erp.purchase.actions` 新老两套 |
| `shared.transaction`（CC 登记电脑、回执） | 没测到：要 CC 桌面版与测试机，按任务书不碰；本次没改 `transaction` 本身，只新加调用方 |
| `cowork.transaction`（Cowork CC 成员通道） | 没测到：同上 |
| `cc.mrerp-login` | 没测到：要 CC 桌面版与 MR.ERP 账号 |
| `cc.assistant` | 通过：`cc.chat`、`cc.settings` 新老两套、电脑与手机 |
| `shared.exports` | 通过：`erp.exports`、`cowork.exports`（CC 附件下载没测到：要测试机） |
| `shared.navigation` | 通过：各流程都从侧栏页面进入（`erp.company.settings` 等）；用户菜单逐项没单测 |
| `shared.upload.shrink-photo` | 通过：`shared.upload.take-photo` |
| `shared.originals.download` | 通过：`cowork.intake.review`（录入复核开原件）；ERP「下载原件」没单测 |
| `shared.login` | 部分：实验栈用会话票据登录，所有流程登录后照常；OIDC 登录 / 退出 / 改密码没测到（实验栈没有 Keycloak 走查流程） |
| `shared.execution` | 部分：登录会话照常；LINE 交互、上传识别没测到（不碰 LINE、不付费识别） |
| `shared.assistant` | 通过：`cc.chat` |
| `shared.browser-text` | 通过：所有流程页面正常打开 |
| `shared.line.login` | 没测到：要真 LINE |
| `erp.cc.connect` | 没测到：要测试机 CC |

`hooks.py` 只在 `before_request` 最前面加了一条、在文件末尾加了 `before_job`，原有钩子的内容与顺序没变；上面「没测到」的入口都只是因为它们的调用方写在同一个 `hooks.py` 里。
