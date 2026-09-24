# 盘点工作台图表：按商品分类

- 用户口径（2026-09-24）："我觉得这个柱形图设计得不合理，分类不够明确"；随后"按现在的基础上改一版，
  本地预览拉起来我看看，多搞些数据进去"。
- 授权范围：本地实现 + 本地预览 + 本机验证。**未推送、未合并 main、未部署**；测试环境与线上仍是旧图。
- 基线 main `3c5e4a354bed3463bed2b048c1033e12bf3a1c68`；分支 `codex/stocktake-chart`；
  工作树 `/Users/skin/.codex/worktrees/stocktake-chart/pearnly`。
- 归属 ERP inventory 的盘点工作台前端（图表）；不动其他任务的分支、容器与数据。

## 为什么改

旧图按 `${仓库} / ${仓位 || "未填"} · ${单位}` 分类，同时把数量**加总**。两个后果都能在用户的截图上看到：

- 一次盘点通常只有一个仓、仓位还常常是空的，所有行并成**一个分类**，分类名还是"仓库 / 未填"这种拼接串；
- 同一个分类里正负差异互相抵消：截图那单净差只有 +25，四行实际是 −34/+4/+32/+23（绝对值 93），
  表里四行全标红，图上两根柱却几乎一样高；
- 记录是按**实际仓位**归组的，所以仓位被改过的条目会额外生成一根"账面 0 / 实盘 N"的柱，排序又按 |差异| 靠前，
  容易排到前面当噪声。

## 改了什么

`apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/stocktake.js` 图表段：

- 分类维度改成**商品**：按 `row.product` 分组、标签用商品名，取 |实盘 − 账面| 最大的前 12 个；
  与原生报表 `erp_stock_count_variance` 的"看板按＝商品名称"同一口径。
- 两个数据集仍是账面数量 / 实盘数量，配色（灰 + 产品强调色）不变。
- 记录先按快照行聚合一次（原实现是**每行**都 `state.entries.filter(...)`，复杂度行数 × 记录数）。
- 纵轴数字走 `shortenYAxisNumbers`，与报表图表一致。
- 没有画图时（一行都没盘）保持原样：不渲染图表、不报错。

同一商品的多个仓位行在图上合并成一根柱（这是"商品"这一维的定义）；各仓位明细在下面表格里。

## 本地预览环境

复用上一轮盘点任务的隔离环境：ERP-only 站点 `stocktake.localhost`，Colima context `colima-pearnly-stocktake`，
预览 lane `http://stocktake.localhost:8136`（挂载本任务工作树源码）、验收 lane 8126。

- 切源码：`bash scripts/stocktake-preview.sh up`（只重建预览的两个服务）。
- 预览地址：`/desk/pearnly-stock-count`；示例任务 `STC-00214 / 月底盘点`。
- 灌数据：新增 `scripts/local-review/seed-stocktake-chart.py`（只写 lab 站点，按业务键幂等，整批一个事务）。
  36 个商品、3 个仓 12 个货架、3 份盘点：月底盘点 43 行 / 38 行已盘 / 33 处数量差异 / 6 处仓位差异，
  冷藏区抽盘（跨仓），备用仓快盘（已结束、只读态）。
- 该库停在上一轮（`8a67e7b`），预览 lane 跑主线代码需要升级：先备份到
  `~/.config/pearnly/backups/stocktake-lab-pre-chart-migrate-20260924-230552.sql.gz`，
  再 `bench --site stocktake.localhost migrate --skip-failing`（跳过原因见下）。

## 本机验证（2026-09-24，预览 lane 8136，Chrome + Playwright）

| 场景 | 期望 | 实际 |
| --- | --- | --- |
| 月底盘点（43 行 / 38 已盘） | 柱形图上 12 个分类＝商品名，顺序＝差异绝对值前 12 | 一致：大米/垃圾袋/醋/味精/洗衣粉/面粉/一次性手套/矿泉水/蚝油/牛奶/食盐/辣椒酱 |
| 图上数值 | 与接口口径一致 | 12 组 (账面, 实盘) 与 `api_stock_count_review` 逐组相同 |
| 冷藏区抽盘 / 备用仓快盘 | 按商品出图 | 3 类 / 2 类，均正常 |
| 一行都没盘（STC-00110） | 不画图、无报错 | 无图、无 JS 报错 |
| 窄屏 390×844 | 不横向溢出 | `scrollWidth == innerWidth`，标签按框架规则截断 |
| 页面资源 | 无 4xx/5xx、无 JS 报错 | 通过（图表、44 行表格、汇总数都渲染） |

本机闸门：`check-architecture.py` PASS（0 违规）；`node tests/test-native-desk.cjs`、`tests/test-line-stocktake.cjs`、
`tests/test-stocktake-scanner.cjs`、`node --test tests/test_pos_lifecycle.js`、`test-roster-selection.cjs`、
`test-records-selection.cjs`、`tests/test_native_desk_contract.py`、`tests/test_queue_targets.py`、
`scripts/i18n-build.py --check`、`git diff --check` 全通过。

基线既有失败（与本改动无关，在 main 上同样失败）：`scripts/check-desk-ui.py` 2 条
（`company.js:286`、`stock-opening.js:281`）；`tests/test_product_feature_catalog.py` 3 个 error
（主机 Python 3.9 不支持源码里的 `X | None` 注解）。

## 过程中发现的环境问题（未改这些代码）

1. **升级会卡在 `patches/v1_5_terminal_warehouse`。** 它放在 `[pre_model_sync]`，但要查
   `ERP Warehouse.company`；从没有该列的旧库升级时，这一列要到模型同步才建，于是整次 migrate 直接
   `Unknown column 'company'`。本机临时用 `--skip-failing` 过掉（该站没有收银台，跳过无数据影响）。
   根因是补丁顺序：挪到 `[post_model_sync]` 的 `v1_3_company_scope` 之后、`v1_6` 删列之前即可同时满足
   新老库。归属 POS/公司边界那个窗口，本次只记录。
2. **预览镜像被改过，导致两条 lane 的资源索引互相污染。** 环境里的 `PEARNLY_PREVIEW_IMAGE` 指向
   `pearnly/frappe-sample:16.34.1`（与验收镜像同框架版本，但构建产物 hash 不同）。共享 `sites/assets`
   与 Redis 客户端缓存时，桌面页会拿到另一条 lane 的 `desk.bundle.*.css` 名字，预览 lane 就 404（页面裸样式）。
   处理：删掉 `_83beabe19b43d391|bootinfo` 并重启预览 web，让预览 lane 自洽。**若之后再从 8126 登录浏览，
   污染会复现。** 彻底做法是让预览镜像与验收镜像同源（`stocktake-preview.sh faithful` 那条路）。
3. **登录 origin 调整（本地隔离环境配置，不进仓库）**：lab realm 的 Keycloak 客户端追加了
   `http://stocktake.localhost:8136/api/method/pearnly_core.identity.callback`，站点 `pearnly_oidc_redirect_uri`
   指向 8136（8126 的回调保留）。这样用户只开 8136 就能登录，不会触发第 2 条的污染。

## 第二轮：更标准的数据 + 更直观的图（同日晚）

用户口径："你多加点种类进去，做标准点嘛，翻下官方有没有可以就地取材的更好用于展示的表格，
有没有比这个更好更直观的"。

### 先查官方能就地取材的东西（结论，都在这台机器的框架源码里核过）

- 图表库就是框架自带的那一个：`frappe-charts` **2.0.0-rc27**（`apps/frappe/node_modules/frappe-charts`），
  框架自己的报表图表（`get_chart_data`）也用它。支持的 type：`bar / line / pie / donut / percentage /
  heatmap / axis-mixed`；可用的轴与柱选项：`axisOptions.{shortenYAxisNumbers,xAxisMode,yAxisMode,
  seriesLabelSpaceRatio,xIsSeries}`、`barOptions.spaceRatio`、`valuesOverPoints`、`stacked`、
  `truncateLegends`、`isNavigable`。
- **负值柱是原生支持的**，`valuesOverPoints: 1` 会把数字标在柱顶（含负号）—— 实测：y 轴自动含负区间，
  柱顶显示 `62 / -62`。
- 想"少货红、多货紫"就得拆成两个 dataset（颜色按 dataset 走）：实测柱宽减半、在组内偏左/偏右，
  空的那一侧还会标出 `0`（传 `null` 则标 `NaN`）—— 不干净，放弃。
- 轴标签的字符预算按 **Latin 约 7px/字符**算（`calcLabels` 里 `width / labels * 0.6 / 7`），
  中文名一律被截短：桌面 12 组时约留 5 个字，手机 6 组时只剩 2 个字（`卷 ...`）。
  所以窄屏改成"少画几组 + 隔一个标一个全名"，而不是每根柱都顶着省略号。
- 框架另有 Dashboard Chart / Number Card / Dashboard 这套展示件，但它们是**站点级配置文档**
  （按 Dashboard 组装、面向 Workspace），不是"一份单据一页"的形态，这里用不上；原生报表页
  仍是框架另一条展示路径（工作台按设计把它重定向掉了）。

### 改了什么

- 图表**口径可切**（原生 Select，与框架报表页那条"看板按"同形态）：
  - 默认 **差异**（实盘 − 账面）：一根柱一个商品，负值＝少货、正值＝多货，柱顶带数字；
    再也不用两根柱互相"目测相减"。
  - 另一档 **账面数量 / 实盘数量**：保留原来的两根柱，给要看量级的人。
- 窄屏（<700px）只画差异最大的 **6 组**，并用 `axisOptions.xIsSeries` 隔一个标一个全名。
- 文案新增术语表词条 `stock.count.chart`（Chart / 图表 / แผนภูมิ / グラフ），
  其余复用既有键（`stock.count.difference`、`stock.count.book`、`stock.count.counted`），
  翻译包由 `scripts/i18n-build.py` 重新生成。
- 演示数据换成**标准样本**（`scripts/local-review/seed-stocktake-chart.py`）：
  - 商品编码由系统发（`P-0000xx`），条形码是**校验位正确的 EAN-13**（8851 段），
    66 个商品按 6 个分类、9 种单位铺开（饮料/调味/粮油/清洁/日用/冷藏）；
  - 货位用标准货位号：`A-01…C-02`（主仓）、`R-01…R-03`（冷藏仓）、`T-01/T-02`（退货仓），
    每 6 个商品留一个没有货位（对应老口径的"未填"）；
  - 三份盘点：**2026-09 月度盘点**（主仓 · 64 行 · 55 已盘）、**冷藏仓周盘**、**退货仓复盘**（已过账，只读态）；
  - 差异有种类：大幅少货、大幅多货、微小差异、正好对上、未盘点、放错货位。
  - 同时收回本任务**上一版**造的演示数据（`DEMO-CHART-` 前缀商品与两份进行中的盘点：先清结存再停用），
    免得同一套预览里混着两批口径不同的样本。别的任务留下的站点数据（验收仓库那几份）不动。

### 验证（2026-09-24 晚，预览 lane 8136，Chrome + Playwright）

| 场景 | 期望 | 实际 |
| --- | --- | --- |
| 月度盘点 · 差异档（默认） | 12 组＝商品名，柱顶带数字，负值向下 | 一致：`62 / -62 / 58 / -55 / -55 / -48 / 47 / 45 / 42 / 41 / -41 / 41`；y 轴 −80…80 |
| 切到"账面数量 / 实盘数量" | 回到两根柱 | 26 个 rect（12 组 × 2 + 2 图例），图例＝账面数量/实盘数量 |
| 表与汇总 | 64 行快照按行展开 | 65 行（含表头）、汇总 55 / 9 / 47 / 6，无 4xx/5xx、无 JS 报错 |
| 窄屏 390×844 | 不横向溢出、能看清是哪几个商品 | 无横向滚动（scrollWidth＝390），6 根柱、3 个全名轮标 |
| 空态（一行都没盘） | 不画图、无报错 | 通过 |

门槛：`check-architecture.py` PASS、`tests/test_native_desk_contract.py`、`i18n-build.py --check`、
`node tests/test-native-desk.cjs`、`test-line-stocktake.cjs`、`test-stocktake-scanner.cjs`、
`git diff --check` 全通过（`check-desk-ui.py` 与 `test_product_feature_catalog.py` 的既有失败与 main 相同）。

### 顺带看到、但这次没动的

- 工作台表格走共享层 `p.table`（`layout: "fluid"`），列宽是**按比例摊**的：列多的时候
  "物料名称 / 编码 / 条码 / 账面仓位"都会被压出省略号（标准样本名更长后更明显）。
  要更"好展示"有三条路，取舍不同：改 `layout: "fixed"` 让列宽生效（换来横向滚动）、
  去掉"编码/条码"两列、或者把"账面仓位"在单仓盘点里只显示货位。这次没动，等用户挑。

## 第三轮：折线/柱形图换成"有差异"清单（同日晚）

用户口径："现在这个太专业了，很多人看不懂的，我要的是直观易懂，且只要有差异的都在显示"。

判断：带正负轴的"差异柱"、图例、前 12 组上限这三样，都是给看报表的人准备的；一线同事要的是
"哪些货对不上、差多少"，而且**一条都不许被截掉**。所以不再画图。

### 改了什么

- **撤掉柱形图与"图表口径"下拉**（`frappe.Chart`、`chart_limit`、`chart_axes`、`stock.count.chart`
  词条一并删除，不留死代码）。
- 汇总四格下面换成**「有差异」清单**：用原生 DataTable，一行一个**有差异**的快照行，按差得多少排序；
  列＝物料名称 / 账面仓库·仓位 / **差异条** / 数量差异，条长＝差多少（最长的占满），
  红＝少货、紫＝多货，数字那格直接写**"少货 70 瓶 / 多货 62 提"**（放错货位但数量对的写"仓位差异"）。
- 条用的是 desk 包自带的 **Bootstrap `.progress` / `.progress-bar`**（框架 CSS 里现成的，不引第三方、不写新样式）；
  卡片沿用框架 `.report-wrapper` / `.chart-wrapper` 那套留白。
- 清单**不做条数上限**：这次演示数据有 48 行差异，48 行全列出来（用户口径"只要有差异的都在显示"）。
- 新增词条：`stock.count.diffRankHint`（有差异的商品，差得最多的排在最前）、`stock.count.short`（少货）、
  `stock.count.over`（多货）；翻译包重新生成。

### 验证（2026-09-24 晚，预览 lane 8136，Chrome + Playwright）

| 场景 | 期望 | 实际 |
| --- | --- | --- |
| 月度盘点（64 行 / 55 已盘 / 47 数量差异 / 6 仓位差异） | 有差异的行全部列出、按差得多少排序 | 清单 48 行、48 条进度条；首行＝生抽 700ml / 主仓 A-03 / 少货 70 瓶 |
| 文案 | 不用正负号、不用图例 | 单元格文本＝"少货 70 瓶""多货 62 提""仓位差异"；提示行＝"有差异的商品，差得最多的排在最前" |
| 汇总与明细 | 不受影响 | 汇总仍是 55 / 9 / 47 / 6；下面明细表 65 行 |
| 窄屏 390×844 | 不横向溢出 | 无横向滚动（scrollWidth＝390）；名称与单位会被框架按列宽截断（同明细表的既有行为） |
| 出错面 | 无 4xx/5xx、无 JS 报错 | 通过 |

门槛：`check-architecture.py`、`i18n-build.py --check`、`tests/test_native_desk_contract.py`、
`node tests/test-native-desk.cjs`、`test-line-stocktake.cjs`、`test-stocktake-scanner.cjs`、
`git diff --check` 全通过。

## 第四轮：横向条（灰＝账面）＋ 位置差异单独一块（同日晚）

用户口径："还是做横向的柱形图吧，用你这种形状，灰色代表账面，有差异就按百分比减少，
超出就超出灰色容量，同时仓库的差异如何显示呢？"

### 数量差异：横向双色条

- 每行一条横条：**灰＝账面数量**（这一行的"满格"容量），**彩＝实盘数量** —— 少货时彩条比灰条短
  （短多少就是少了百分之多少），多货时彩条压过灰条，右边那格照旧写"少货 70 瓶 / 多货 62 提"。
- 彩条颜色沿用产品的语义：少货红（`--red-600`）、多货产品强调色；灰用与看板账面柱同一个 `#a3a3a3`。
- **整条轨道按最大比例归一化**：这样超盘的彩条能压过灰条又不会被表格单元格裁掉。
  轨道上限取 **账面的 1.5 倍** —— 实测一版没有上限时，"账上 14 件却数出 61 件"这种极端行
  会把别所有条压成一小截（灰条只剩 23%），加上限后灰条稳定在 2/3，超 1.5 倍的按满格画（真值照写）。
- 条仍然用 desk 包自带的 Bootstrap `.progress` / `.progress-bar`，灰条与彩条用行内几何叠在一起，
  没有新样式表、没有自绘 SVG。
- 实盘为 0 的行只显示灰条（"一件都没数出来"）——这是"灰＝账面容量"这个口径的必然结果，
  说法由上面那行提示讲清楚（"少了比灰条短、多了压过灰条"），严重程度由排序（差异大的在最前）与红字承担。

### 仓库/仓位的差异：不能塞进数量条，单独列一块

用户问"仓库的差异如何显示呢"——答案是：数量条只能表达数量，**放错位置的行数量往往正好对上**
（在条上什么都看不出来）。所以：

- 数量条那一块只放**有数量差异**的行；
- 下面单独一块「位置不一样的商品」：物料名称 / 账面仓库·仓位 / **实际仓位及数量**（红字），
  行数正好等于汇总里的"仓位差异"（这单 6 行），一眼能对上；
- 既少货又放错位的行会同时出现在两块里：数量块说数量、位置块说位置，不互相掩盖；
- 落点（哪个仓、哪个位、多少件）下面明细表的"实际仓位及数量"列照旧有。

### 验证（2026-09-24 晚，预览 lane 8136，Chrome + Playwright，任务 `STC-00743 / 2026-09 月度盘点`）

| 场景 | 期望 | 实际 |
| --- | --- | --- |
| 数量条几何 | 灰条等宽（＝账面容量），彩条按 实盘/账面 成比例 | 行内宽度：所有灰条 22.95%→加上限后 66.7%；`实盘/账面` 与 `彩条/灰条` 一致（如 90→20 画 22%，14→61 画满格） |
| 超盘 | 彩条压过灰条、不被裁 | 湿巾 80片（14→61，比值 4.36）按上限画满格，数字仍写"多货 47 包" |
| 有差异的都显示 | 数量差异一行不漏 | 数量块 47 行；位置块 6 行；汇总仍 55 / 9 / 47 / 6 |
| 位置差异 | 账面位置 → 实际位置（红） | 味精 B-03→A-01(86)、橙汁 B-01→A-02(82)、洗衣粉 未填→A-03(182)…（重灌数据后六行各不相同） |
| 窄屏 390×844 | 不横向溢出 | 无横向滚动（scrollWidth＝390） |
| 出错面 | 无 4xx/5xx、无 JS 报错 | 通过 |

顺带修的两处：

- 演示数据的"实际仓位"原本全是同一个货位（脚本固定挑第一个不同货位）→ 改成在候选货位里轮着取。
- 填充脚本的幂等从"主任务在就整体跳过"改成**每份盘点按标题各自幂等**：不然中途删掉一份就补不回来
  （记录的业务键是全局唯一的，重建时会撞上旧记录 —— 这次就是这么撞出来的）。

## 第五轮：竖起来的柱形图 + 信息进悬停（同日晚）

用户口径："不是不是，理解错了，柱子立起来，把信息放在鼠标放上去才显示。柱形图 柱形图 之前那种柱形图"。

### 改了什么

- 横条清单换回**框架自带的柱形图**（`frappe.Chart`，`type: "bar"`）：**灰柱＝账面数量、彩柱＝实盘数量**，
  只画有数量差异的行、一条不落，按差得多少排序。
- **信息进标签，标签只在悬停时看全**：每根柱的标签是
  `商品名 · 主仓 / 货位 · 少货 70 瓶`（位置也不同的再加一段 `仓位差异 → 主仓 · B-01 (177)`）。
  轴上的字会被框架按宽度截短/抽样（标签长的时候只在轴上留几个全名），
  鼠标放上去的悬停提示里是**完整标签 + 两个数量**（实测：`气泡水 500ml · 主仓 / A-02 · 多货 58 瓶
  166 账面数量 224 实盘数量`）。这正是用户要的"信息放鼠标上才显示"。
- 提示文案随之改成："数量差异，差得最多的排在最前。灰柱是账面数量、彩柱是实盘数量；鼠标放到柱子上
  能看到商品、仓位和具体数量。"
- 「位置不一样的商品」那块**保留**（数量对得上、只是放错位置的 6 行），它回答的是上一轮用户问的
  "仓库的差异如何显示" —— 悬停提示表达不了"数量对得上但位置不对"这种情况。

### 验证（2026-09-24 晚，预览 lane 8136，Chrome + Playwright，任务 `STC-00743`）

| 场景 | 期望 | 实际 |
| --- | --- | --- |
| 柱形图 | 支柱子、灰＝账面、彩＝实盘 | 96 个 rect（47 组 × 2 + 2 图例），y 轴 0…500，图例＝账面数量/实盘数量 |
| 有差异的都显示 | 一行不漏 | 数量块 47 组；位置块 6 行；汇总 55 / 9 / 47 / 6 |
| 悬停 | 完整信息（商品·仓位·差多少 + 两个数量） | 提示文本＝`气泡水 500ml · 主仓 / A-02 · 多货 58 瓶 166 账面数量 224 实盘数量` |
| 轴 | 标签长时只在轴上留几个全名 | 轴上约 3 个完整标签，其余留白（悬停可看全） |
| 窄屏 390×844 | 不横向溢出 | 无横向滚动（scrollWidth＝390） |
| 出错面 | 无 4xx/5xx、无 JS 报错 | 通过 |

已知限制（要跟用户确认）：**悬停是鼠标行为，手机上取不到这份信息**；窄屏只看到一片柱子（轴上字也不够）。
如果手机也要这份信息，可选：窄屏退回清单/表格形态、或给图表加点击弹出的原生对话框。

## 第六轮：位置差异进图（短字段），位置表保留（同日晚）

用户口径："把仓库差异，字段简短放在有差异的商品柱形图中，那个表不要取消掉你新增的文案，
不要随意加其它文案说明"。

### 改了什么

- 图上的行从"有数量差异"扩成**所有有差异的行**（数量对不上 + 数量对得上但放错位置）：
  这单 48 组（47 组有数量差异、其中 1 组只是位置不对），柱数 98 = 48 × 2 + 2 图例。
- **位置字段做短**：标签里的位置就是货位，放错位置时写成 `账面货位→实际货位`（如 `C-01→B-01`），
  跨仓才带仓库名；账面侧不再重复写仓库名。悬停实测：
  `玻璃清洁剂 600ml · C-01→B-01 · 少货 35 瓶 / 212 账面数量 / 177 实盘数量`。
- 「位置不一样的商品」那块**保留**（用户明确要求不取消）：柱高看不出"放错位置"这件事，
  那块给的是完整的"账面位置 → 实际位置"，行数＝汇总里的"仓位差异"。
- 没有新增任何说明文案（沿用上一轮那行提示与那块的标题）。

### 验证（2026-09-24 晚，预览 lane 8136，Chrome + Playwright，任务 `STC-00743`）

| 场景 | 期望 | 实际 |
| --- | --- | --- |
| 图含位置差异 | 只是放错位置的行也在图上 | 48 组 / 98 个 rect；位置字段在轴与悬停里都是短形式 |
| 悬停文本 | 商品 · 位置 · 少货/多货 + 两个数量 | `生抽 700ml · A-03 · 少货 70 瓶 180 账面数量 110 实盘数量`；`玻璃清洁剂 600ml · C-01→B-01 · 少货 35 瓶 …` |
| 位置表 | 保留 | 6 行 + 表头，仍与汇总的"仓位差异 6"对得上 |
| 其它 | 汇总/明细/窄屏不变 | 汇总 55 / 9 / 47 / 6；明细 65 行；390px 无横向溢出；无 4xx/5xx、无 JS 报错 |

## 第七轮：位置差异改成柱顶小红点，撤掉多余文案与位置表（次日凌晨）

用户口径（原话）："如果对应的商品仓库位置有差异蓝色柱子上方加一个小红点表示，然后文案进鼠标放上去
会显示的这个图里，你下面的那个仓库差异图表删了"，以及"数量差异，差得最多的排在最前。灰柱是账面数量、
彩柱是实盘数量；鼠标放到柱子上能看到商品、仓位和具体数量。这些文案删了，蚝油 500g · B-02→C-01 ·
多货 28 瓶这些文案删了，不要随意增加文案"。

### 改了什么

- **位置差异＝柱顶小红点**：位置有差异的那一行，在它那根**彩柱（实盘）正上方**画一个红色小圆点
  （`r=2.5`，颜色取 `--red-600`，与表格里的红同一处红）。红点数＝汇总里的"仓位差异"。
- **柱下那行标签抹掉，字只留在悬停里**：标签在框架里是**同一份数据画两处**（柱下那行 + 悬停提示），
  这里把柱下那行去掉（`y>0` 的 `<text>`；y 轴数字与图例保留）。
- **删掉那行提示文案与「位置不一样的商品」那张表**（`stock.count.diffRankHint`、`stock.count.locationHint`
  两个词条一并从术语表移除）。页面现在只有：汇总四格 → 柱形图 → 明细表。
- 没有新增任何文案；悬停提示仍是原有的"商品 · 仓位 · 少货/多货"两数量。

### 实现的坑（记下来，免得下次再踩）

框架是**分帧**把图（含轴标签）画出来的：在 `new frappe.Chart()` 之后立刻做 DOM 标注会跑在绘图之前 ——
第一版就是这么"什么都没发生"（标签还在、红点 0 个）。改成逐帧标注、抹标签幂等，红点按"数量对不上就整批
重画"补齐；用 `performance.now()` 限时 1.5 秒，另用一个自增 token 防止上一笔渲染的回调标到新图上
（换筛选会重画图表）。

### 验证（2026-09-25 凌晨，预览 lane 8136，Chrome + Playwright）

| 场景 | 期望 | 实际 |
| --- | --- | --- |
| `STC-00743` 月度盘点（64 行 / 55 已盘 / 47 数量差异 / 6 仓位差异） | 48 组柱、6 个红点、轴上无标签 | 98 个 rect（48×2 + 2 图例）、红点 6 个、轴标签 0 个（字仍在悬停：`一次性手套 50只 · A-02 · 少货 36 盒 / 36 账面数量 / 0 实盘数量`） |
| 页面只剩三块 | 汇总 / 图 / 明细表 | `.chart-wrapper` 1 块、`.text-muted` 0 条、明细表 65 行；汇总 55 / 9 / 47 / 6 |
| `STC-00863` 冷藏仓周盘（7 已盘 / 0 仓位差异） | 不出现多余红点 | 14 个 rect（7×2）、红点 0 个 |
| 无差异 / 未盘点（`STC-00652` 已过账、`STC-00110` 未盘） | 不画图 | 无图、无报错 |
| 换筛选后重画 | 红点不重复、标签不复活 | 98 rect / 6 红点 / 0 轴标签 |
| 窄屏 390×844 | 不横向溢出 | `scrollWidth＝390` |

门槛：`check-architecture.py`、`i18n-build.py --check`、`tests/test_native_desk_contract.py`、
`node tests/test-native-desk.cjs`、`test-line-stocktake.cjs`、`test-stocktake-scanner.cjs`、
`git diff --check` 全通过。

## 第八轮：柱形图整块删掉（同日晚）

用户口径："把柱形图全部删了，不要了"。

### 改了什么

- 工作台现在只有**汇总四格 + 明细表**：删掉图表那块的容器、`frappe.Chart` 构建、位置红点标注
  （`mark_chart`）、只服务图表的辅助函数（`amount_text` / `counts_at` / `place_text` / `chart_label`）、
  状态变量（`chart` / `rank_host` / `paint_token`）与相关注释，不留死代码。
- 只被图表用到的两个词条 `stock.count.short` / `stock.count.over` 从术语表移除。
  `stock.count.book` / `stock.count.counted` 仍被明细表列头使用，保留。

### 验证（2026-09-25 凌晨，预览 lane 8136，Chrome + Playwright）

| 场景 | 期望 | 实际 |
| --- | --- | --- |
| `STC-00743` 月度盘点 | 没有图表，明细表照常 | `.chart-wrapper` 0 块、图表 SVG 0 个；明细 65 行；汇总 55 / 9 / 47 / 6 |
| `STC-00863` 冷藏仓周盘（无差异） | 不画图也不报错 | 同上（无图表），汇总 7 / 1 / 6 / 0 |
| `STC-00652` 退货仓复盘（已过账只读） | 同上 | 无图表，明细 7 行 |
| 筛选 | 仍然生效 | 选"未盘点"→9 行；选"有差异"→48 行（都含表头行） |
| 窄屏 390×844 | 不横向溢出 | `scrollWidth＝390` |
| 出错面 | 无 4xx/5xx、无 JS 报错 | 通过 |

门槛：`check-architecture.py`、`i18n-build.py --check`、`tests/test_native_desk_contract.py`、
`node tests/test-native-desk.cjs`、`test-line-stocktake.cjs`、`test-stocktake-scanner.cjs`、
`git diff --check` 全通过。

### 取舍与后续

- 生成的翻译包按仓库规矩"不删已有的行"，所以三条已停用词条（`stock.count.diffRankHint`、
  `stock.count.locationHint`、`stock.count.short`/`stock.count.over`）仍留在 `translations/*.csv` 里
  （术语表已无这些键，不会再被引用）。
- 图表各版（按商品的账面/实盘柱、差异柱、横向条、红点）都还在 git 里（本任务第 2、4、6、7 个提交），
  要恢复任一版都便宜。
- 手机端的问题随图表一起消失（不再有"触屏没有悬停"这件事）；明细表在窄屏仍然按列宽截断，
  前面第二轮记的三条取舍（fixed 布局 / 减列 / 单仓只显示货位）仍然成立。

### 紧接着：最上面那张汇总卡片也删掉

用户口径："最上面卡片也删"。

- 删掉 `.report-summary` 那张四格卡片（已盘点 / 未盘点 / 数量差异 / 仓位差异）与它的渲染循环；
  现在页面只剩「搜索+筛选 → 明细表」。
- 任务进度没有丢：仍在页面指示器上（`进行中 · 已盘点 55 / 64 · 差异 48`，原生 pill），
  四个数字也都能在明细表里逐行核对。
- "库存被别的业务改过要重盘"那条警告原先挂在卡片里，现在换成表格上方一个**空的** `<p class="text-danger">`：
  没有冲突时是空字符串、不占视觉；有冲突时照旧显示原话。
- 四个汇总用的词条仍被别处使用（列表列头 / 明细表列头 / 明细表状态），没有词条要移除。

| 场景 | 期望 | 实际 |
| --- | --- | --- |
| `STC-00743` 月度盘点 | 没有卡片、没有图表 | `.report-summary` 0 个、`.summary-item` 0 个、图表 0 个；明细 65 行（含表头）；指示器＝`进行中 · 已盘点 55 / 64 · 差异 48` |
| `STC-00863` / `STC-00110` | 同上 | 卡片 0、图表 0；指示器分别是 `7 / 8 · 差异 6`、`0 / 5 · 差异 0` |
| 冲突警告位 | 空的时候不可见 | 页面里有一个空的 `p.text-danger`（文本为空），没有多余文字 |
| 筛选 / 窄屏 / 出错面 | 照常 | 选"有差异"→48 行；窄屏无横向溢出；无 4xx/5xx、无 JS 报错 |

## 第九轮：列头排序三项的覆盖与"按看得见的文字排"（次日凌晨）

用户口径："升序 / 倒序 / 重置排序帮我放出来呗，覆盖所有有这种列的界面"，并问 Freeze/Unfreeze 是做什么的。

### 现状核对（只读）

- **三项本来就在**：共享层 `p.table_menu` 只隐藏官方的"移除栏 / 冻结 / 解冻"，排序三项一直保留。
  实测明细表列头菜单：`升序 / 倒序 / 重置排序`（可见）＋ `移除栏 / Freeze / Unfreeze`（隐藏）。
- 入口是**列表头右侧那个小箭头**（鼠标移到列头上才出现），不是点列名本身。

### 但排序结果是错的（这一轮真正修的东西）

框架 `frappe-datatable` 的 `_sortRows` 拿单元格的**原始值**直接比大小：
数字比数值、其余按字符串。而我们很多列的原始值就是**拼好的 HTML**：
商品名列是 `<a …data-count-row…>香皂 100g</a>`，于是升/降序实际是按**内部单据号**排——
用户看到的顺序是乱的；数量列若是 `1,204` 这种带千分位的字符串，还会按字符排（`9` > `1,204`）。

修法（在共享层 `p.table` 一处，覆盖所有用这套表格的界面）：原始值统一做成
**「排序键 + `\u0000` + 要显示的 HTML」**，`format` 里再把键切掉：

- 排序键：能当数字看就按数值（`n:` + 负号档 + 18 位整数补零 + 6 位小数补零，所以负数排在前面、
  小数不会比错），否则按文字（`s:` + 文字）；框架只比字符串，键必须自己补零。
- 显示不变：单元格内容仍是原来那份 HTML（链接、红字都在）；框架的单元格 `title` 取的是
  **显示内容**，所以悬停提示里不会漏出排序键（实测 `title="一次性手套 50只"`）。

### 验证（2026-09-25 凌晨，预览 lane 8136，Chrome + Playwright）

| 场景 | 期望 | 实际 |
| --- | --- | --- |
| 明细表 物料名称 升序 / 倒序 / 重置 | 按看得见的商品名排 | 升序＝原顺序（快照本就按名称升序）、倒序＝`鸡精 200g / 鱼露 700ml / 香皂 100g / 食盐 500g / 铝箔纸 10m`、重置＝回到原顺序 |
| 账面数量 升序 | 按数值 | `28 / 28 / 36 / 36 / 36 / 40`（修前会先出 `1,204` 这类） |
| 数量差异 升序 | 负数在前、按数值 | `-5 / -5 / -9 / -9 / -12 / -12` |
| 差异金额 升序（千分位 + 负号） | 按数值 | `-30 / -117 / -135 / -287 / -300 / -360` |
| 链接与红字 | 修完不丢 | 64 个 `a[data-count-row]`、148 处红字；点行仍能打开"记录"弹窗（实测标题 `记录 — 一次性手套 50只`） |
| 排序键不外泄 | 单元格文本/标题都不含 `\u0000` | 0 处 |
| 其它 `p.table` 界面 | 不受影响 | 库存页点行 → 流水弹窗表格正常（3 行、无键外泄、菜单三项可见）；盘点"记录"弹窗表格正常（2 行） |
| 全部产品页面 | 无报错 | 15 个页面逐个打开：无 pageerror、无 5xx（`billing_www.home` 一条 417 是实验室没接计费服务，既有现象） |

门槛：`check-architecture.py`、`i18n-build.py --check`、`tests/test_native_desk_contract.py`、
`node tests/test-native-desk.cjs`、`test-line-stocktake.cjs`、`test-stocktake-scanner.cjs`、
`node --test tests/test_pos_lifecycle.js`、`test-roster-selection.cjs`、`test-records-selection.cjs`、
`git diff --check` 全通过。

### 未验 / 未做

- Cowork 的录单页（`pearnly_cowork/public/desk/intake.js`）也用同一个 `p.table`，但本机 lab 站点没装 Cowork，
  这次没验；改动是纯数据/格式化层的，风险低，Cowork 窗口恢复后应顺手复验一次。
- Freeze/Unfreeze（列冻结）仍按 2026-09-22 的口径隐藏着；用户只是问它是做什么的，本轮没动。

## 第十轮：列头点一下直接排序（次日凌晨）

用户口径："点列头我要的是这种，不要那种繁琐的点开，选择"。

### 改了什么

- 共享层 `p.table` 里把**官方自己的排序通道**（菜单里那三项调用的同一个 `columnmanager.sortColumn`）
  绑到列头本身上：**点一次升序 → 再点倒序 → 第三次取消**（回到原始顺序），列头右侧仍然显示官方的
  ↑ / ↓ 指示。排序规则就是第九轮修好的"按看得见的文字、数字按数值"。
- 菜单那三项**保留**（官方行为一个字不改）；点在小箭头、列宽拖拽把手或输入控件上时不会误触发排序。
- 补一条皮肤：列头给 `cursor: pointer`（官方只给小箭头设了手型，整格可点却不提示会让人以为点不动）。

### 验证（2026-09-25 凌晨，预览 lane 8136，Chrome + Playwright）

| 场景 | 期望 | 实际 |
| --- | --- | --- |
| 点"物料名称"列头 ×1 / ×2 / ×3 | 升序 → 倒序 → 取消 | 列头指示依次 `物料名称 ↑` / `物料名称 ↓` / `物料名称`；顺序：原顺序 → 鸡精/鱼露/香皂/食盐 → 原顺序 |
| 点"账面数量"列头 ×1 | 按数值升序 | `28 / 28 / 36 / 36 / 36 / 40`，列头显示 ↑ |
| 小箭头 | 仍能打开原来的菜单 | 菜单可见（`display:block`） |
| 点行 | 仍打开"记录"弹窗 | `记录 — 一次性手套 50只` |
| 另一端 `p.table`（库存页流水弹窗） | 同样点列头能排序 | 点"结存"列头两次 → `202 / 101`（原为 `101 / 202`） |
| 光标 | 列头是手型 | `cursor: pointer` |
| 出错面 | 无报错 | 无 pageerror |

门槛：`check-architecture.py`、`i18n-build.py --check`、`tests/test_native_desk_contract.py`、
`node tests/test-native-desk.cjs`、`test-line-stocktake.cjs`、`test-stocktake-scanner.cjs`、
`node --test tests/test_pos_lifecycle.js`、`test-roster-selection.cjs`、`test-records-selection.cjs`、
`git diff --check` 全通过。

### 列表（不是表格）的情况 —— 后来做了，见第十一轮

下面这段是当时的只读核对，保留当背景；结论在第十一轮（用户"列表的也做"）。

用户同一轮也问了"列表有这类功能吗"。核对结果：

- 官方 `ListView` **支持点列头排序**（列头挂 `data-sort-by`，点击后 `sort_selector.set_value(...)`），
  并且是**服务端排序**（`sort_by` / `sort_order` 进列表查询）。
- 我们的共享列表层 `p.list` **故意关掉了**：`hide_sort_selector: true` ＋ 渲染表头时摘掉 `data-sort-by`
  （`apps/pearnly_core/pearnly_core/public/desk/list.js`）。实测「物料」列表点列头前后顺序不变。
- 要开的话不是一行：列表是**服务端分页**（一页 20 条，数据来自各页自己的 `api_list_*`，接口里有固定
  `order_by`），只放开前端只会重排当前这 20 行。正确做法是给每张列表声明可排序字段白名单、对应接口
  接受 `sort_by/sort_order`，再放开列头点击；建议分批（先 物料 / 库存 / 盘点）。
- 列表没有"列冻结"这个能力（那是表格 datatable 的）；列表对应的原生能力是列设置、列宽拖拽、行内编辑等。
  点击弹原生对话框），暂不做。
- 明细表走 `p.table`（`layout: "fluid"`，列宽按比例摊），窄屏上"编码 / 条码 / 单位"这类列会被压出省略号；
  前面第二轮记的三条取舍（fixed 布局 / 减列 / 单仓只显示货位）仍然成立。

## 第十一轮：列表（不是表格）也点列头排序（当日）

用户口径："列表的也做"（承接第十轮的表格列头排序），更早那句是"覆盖所有有这种列的界面"。
第十轮末尾列的"待用户决定"到这里有了结论：**做**。

### 改了什么

服务端（`pearnly_erp_portal`）：新增 `access/listing.py`，两条判据 + 六张列表接参数。

- 放 `access/` 而不是产品根层：根层只允许 `__init__.py / hooks.py / product_manifest.py /
  purchase.py`，`check-architecture.py` 会拦（第一版放根层直接 FAIL）。
- `order_by(sort_by, sort_order, allowed, default)`：白名单命中才给 `"<列> asc|desc"`，
  认不出来的字段或方向一律回落该列表的默认顺序 —— **绝不把用户输入拼进 SQL**。
- `sort_rows(rows, sort_by, sort_order, allowed)`：内存排序（库存一览那种先算好整批再切页的），
  空值不管升序降序都排最后。
- 接参数的：物料 `list_products`、库存 `stock_overview`（内存排）、盘点 `list_counts`、
  入出库 `list_entries`、采购 `records.rows` / `api_purchase_records`、
  销售 `records.rows`+`page`（销售单 + 收银小票**两路归并**，排任意字段时两侧整批取回）、
  收银小票 `cashier.list_sales`。都是关键字参数带默认值，导出那条路（`records.rows(params, limit=0)`）
  不受影响。

前端（`pearnly_core` 共享列表层 `list.js`）：

- `p.list` 新增 `sort_fields` 声明；`render_header()` 只给声明过的列保留官方的 `data-sort-by`
  与"点击排序"提示，其余列照旧摘掉属性 —— 点了没反应比不给点更糟。
- 绑 `this.$result.on("click", "[data-sort-by]")`：**升序 → 倒序 → 取消**（回本页默认顺序），
  当前排序列挂 ↑ / ↓。框架自己把箭头画在排序控件里，而那个控件在我们这是隐藏的，所以箭头得自己挂。
- `refresh()` 把 `sort_by` / `sort_order` 交给各页 `load` → 服务端。列表是服务端分页的
  （一页 20 条），只在前端重排这一页是假排序。
- 新增 `p.sort_rows(rows, {sort_by, sort_order, fields, keys})`：一次取全的列表在本地排，
  口径与服务端一致（白名单、空值最后、数字按数值）；`keys` 只给"列名和行里的键不一样"的列用。
- 各页声明并透传：物料、库存、盘点、采购、销售、入出库、收银小票、交班、收发存、客户、供应商、
  仓库、公司、团队、LINE 销售待处理、LINE 采购待处理（共 17 张；交班 / 收发存 / 客户 / 供应商 /
  仓库 / 公司 / 团队 / LINE 待处理是一次取全、本地排）。

### 探针抓出来的三个真问题（都已修）

接口级探针（把每张列表声明的每个字段都按 asc / desc 打一遍）抓出三个"点了会出事"的：

1. **销售列表按「单据日期」排 → 500**：`TypeError: '<' not supported between instances of
   'str' and 'datetime.date'`。那一页是销售单 + 收银小票两路归并，同一列一半是 `datetime.date`
   一半是字符串。修在 `listing._comparable()`：数字按数值比，其余按文本比（日期的文本是 ISO
   形状，字符串序与时间序一致）。
2. **入出库按「备注」排 → `PermissionError: 您无权访问字段：ERP Stock Entry.note`**：`note`
   是字段级受权限保护的字段，`frappe.get_list(order_by="note")` 直接被拒。**这一列不开放排序** ——
   要排它就得把这一页从"服务端分页"改成"整批取回内存排"，单据只会越积越多，为一列备注不值当。
3. **团队列表的「状态」列点半天不动**：那一列显示的是"启用 / 停用"，值在 `row.enabled` 上，
   而列名是 `status`。靠 `keys: {status: "enabled"}` 修掉（`p.sort_rows` 新增的 `keys` 映射）。

### 验证（2026-09-25，预览 lane 8136，Chrome + Playwright）

界面：17 张列表逐个访问，点每页第一个可排列表头 ×3，箭头依次 `列名 ↑` / `列名 ↓` / `列名`，
全程无 pageerror、无 5xx（`/tmp/listsort5.mjs`）。抽样顺序证据：

| 列表 | 列 | 结果 |
| --- | --- | --- |
| 物料 | 物料名称 | 升序 `123 / 123 / 一次性手套 50只 / 保鲜膜 30m` ← 库里的升序就是编码序；倒序 `黄油 / 鸡精 / 鲜牛奶 / 鱼露`；复位回原顺序 |
| 物料 | 成本 | 升序 `฿4 / 6 / 7 / 8`；倒序 `฿175 / 155 / 128 / 118` |
| 库存 | 在库 | 升序 `5 / 7 / 28 / 28`；倒序 `394 / 390 / 380 / 376` |
| 盘点 | 有差异 | 升序先 0（0 显示为空）；倒序 `48 / 6 / 1 / 1` |
| 客户（8 行） | 客户名称 | 升序按泰文字典序 `เชียงใหม่ / ไทยฟู้ดส์ / นครพาณิชย์ / ภูเก็ตซีฟู้ด`；倒序 `ร้านป้าแดงของชำ / ร้านเจริญพานิช / อีสานค้าส่ง / สยามเทรดดิ้ง` |
| 供应商（6 行） | 供应商 | 升序 / 倒序翻转，复位回原顺序 |
| 仓库（7 行） | 仓库名称 | 升序 `123 / 备用仓 / 测试主仓 / 冷藏仓`（拼音序）；倒序 `主仓 / 验收仓库 / 退货仓 / 冷藏仓` |
| 收发存 | 结存 | 升序先空行；倒序 `394 / 390 / 380 / 376` |
| 交班 / 入出库 / 收银小票 / 公司 / 团队 / LINE 待处理 | 第一列 | 箭头三态正常；这些列表在演示站点只有 0～1 行，看不出重排（见下面"边界"） |

接口（`/tmp/listserversort.mjs` + `/tmp/sortprobe.mjs`）：把 7 张服务端列表声明的**每个字段**
按 asc / desc 各打一遍（物料 8 / 库存 9 / 盘点 6 / 采购 7 / 销售 8 / 入出库 5 / 收银小票 5 个字段），
全部无错；再打一个不存在的 `sort_by=no_such_column` → 正常回落默认顺序，不报错。

纯函数单测：新增 `tests/test_listing_sort.py`（5 条：白名单、方向、空值最后、混类型、数值/Decimal），
`python3 tests/test_listing_sort.py` 通过。

门户：`check-architecture.py` PASS、`i18n-build.py --check` PASS（新增词条
`Click to sort by {0}` → 中文"点击按 {0} 排序"，已生成四语翻译包）、
`tests/test_native_desk_contract.py` 22 项 OK、`node tests/test-native-desk.cjs`、
`test-line-stocktake.cjs`、`test-stocktake-scanner.cjs`、`node --test tests/test_pos_lifecycle.js`（9 pass）、
`test-roster-selection.cjs`、`test-records-selection.cjs`、`test-stock-entry.cjs`、`test-stock-card.cjs`、
`test-selection-action.cjs`、`git diff --check` 全通过。

### 故意不可排的列（不是漏掉）

- 盘点「仓库」：显示的是仓库名（由 link 名翻出来），排 link 名没意义。
- 收银小票「客户」：回填的抬头，服务端排不了。
- 团队「角色 / 公司 / 最后登录」：前两个是拼接文本；最后登录在名单里也只有 1 行能看到效果。
- 公司「当前公司」：按登录人算出来的，不在数据里。
- 入出库「备注」：字段级权限（上面第 2 条）。

### 演示数据（客户 / 供应商本来是空的）

客户、供应商两张列表在演示站点是 **0 行**，排序点了看不出效果。已在本任务自己的实验室种子里
补了 8 个客户 + 6 个供应商（`scripts/local-review/seed-stocktake-chart.py::seed_directory`，
按业务键幂等，跑第二遍输出 `customers: 0, suppliers: 0`）。**没有**动团队 / 交班 / 收银记录 ——
那几个要建真实账号或走收银流程，演示站点上本来就只有 1 行。

### 边界 / 未做

- **DMS / Cowork 的列表**（`roster.js` / `records.js` / `intake.js`）没有声明 `sort_fields`，
  列头照旧不可点：那是另外两个产品，这个预览 lane（`stocktake.localhost`）也没装它们，等用户
  点头再单独开一条验证。
- 服务端列表的中文 / 泰文排序跟的是数据库（按编码），"一次取回本地排"的那几张跟的是语言习惯
  （拼音 / 泰文字典序）。所以同一列的「默认顺序」与「升序」可能不完全一样（两者都算有序，
  客户的例子在表里）。要不要统一（服务端也按语言排）另说，本轮没动。
- 未推送、未合并、未部署；真机验收、测试环境验收都没有。

## 第十二轮：合并、推送与仅 ERP 发布（2026-09-25 01:30–02:00 Asia/Bangkok）

用户指令（01:35）："推上去吧，另一个窗口在做 dms，看下有没碰到共享文件，如果没问题就合并推送部署。"
即：先做共享文件冲突核对，通过后合并、推送、部署。

### 共享文件核对（先做，结论：零重叠）

- 本任务的改动文件 42 个（`git diff --name-only main...codex/stocktake-chart`）。
- 同仓在跑的工作树逐个比对（各自的 `main...HEAD` 加工作区改动）：
  `codex/dms-five-minute-snapshots`（DMS 窗口，143 个文件）、`codex/testenv-per-app`（8 个）、
  `codex/dms-line-full-flow-fix`（1 个）、`cowork-backend-migration` / `erp-*` / `stocktake-workbench`
  / `windows-testenv`（各 0 个）。**交集为空**。
- 我改到共享层 `pearnly_core` 的 8 个文件（`desk/list.js`、`desk/product.js`、`desk/product.css`、
  `i18n/messages.tsv`、四个 `translations/*.csv`）；DMS 窗口分支**没有**改动 `pearnly_core` 任何文件，
  也没有改到我碰过的任何文件。文档命名也不撞（他们 4 份 dms-*，我 1 份 stocktake-chart-*）。
- 结论：不需要"共享组件同一批只由一个窗口改"的协调让步；DMS 分支的未验证成果整条不纳入本轮发布。

### 合并与推送

- 合并前：本地/远端 main 均为 `3c5e4a3`，本分支领先 29 个提交、main 领先 0 个（纯快进），main 工作区干净。
- 合并前跑了 CI 同名闸门全部通过（见下"门槛"），其中包含共享层影响的 DMS 侧检查。
- `git merge --ff-only codex/stocktake-chart` → main = `fd5a9790cfbe4f72d72f503ef687c7399976764e`；推送后回读
  `origin/main` 与本地 HEAD **一致**（`3c5e4a3..fd5a979`）。

### 门槛（CI 同名清单逐条实跑）

`tests/test_architecture_guard.py`、`tests/test_native_desk_contract.py`、
`tests/test_product_feature_catalog.py`、`tests/test_queue_targets.py`、
`scripts/test-dms-call-contracts.py`、`scripts/test-dms-customer-identity.py`、
`scripts/check-architecture.py`（0 违规）、`scripts/check-dms-offline.py`（27 通过 / 0 失败）、
`scripts/production/test-provision-agent.py`、`scripts/check-production-capacity.py`、
`scripts/production/test-activate-product.py`、`node tests/test-native-desk.cjs`、
`node --test tests/test_pos_lifecycle.js`（9 pass）、`tests/test-roster-selection.cjs`、
`tests/test-records-selection.cjs`、`node scripts/production/test-issue-actions.cjs`、
`tests/test_welcome_email.py`、`scripts/i18n-build.py --check`、`git diff --check` 全通过；
另加本轮新增的 `tests/test_listing_sort.py`（5 条）。

- 本机默认 `python3` 是 macOS 自带 3.9，`test_product_feature_catalog.py` 与 `test_welcome_email.py`
  会因 `int | None` 注解报 `TypeError`；换成 `/opt/homebrew/bin/python3.14` 后通过。该失败在**干净的
  main 上同样复现**，与本轮改动无关（属本机解释器问题，不是代码问题）。
- `scripts/check-desk-ui.py` 报 2 处违规（`company.js` 的 Table 紧跟 Column Break、`stock-opening.js` 同类），
  在干净 main 上逐字复现，**不是本轮引入**，且该脚本不在 CI 清单里；本轮不顺手改（避免扩大范围）。

### 镜像、投递与激活

| 步骤 | 结果 |
| --- | --- |
| 生产镜像构建 | 开发检查后触发 `build-production.yml`（run [36042640623](https://github.com/skin306152-star/pearnly/actions/runs/36042640623)，main）success，约 6.5 分钟 |
| 镜像 | `ghcr.io/skin306152-star/pearnly/frappe@sha256:5ea2e4850895e8edfd570d46aa366c9bf09d1bbeb5f4c6cc8e661a920e3d90b0` |
| 镜像标签回读 | `org.opencontainers.image.revision=fd5a9790cfbe4f72d72f503ef687c7399976764e`（发布代码 SHA） |
| 投递（只拉取不启动） | `stage-production.yml`（run [36043430288](https://github.com/skin306152-star/pearnly/actions/runs/36043430288)）success |
| 部署工具同源检查 | 服务器上 `check-deploy-image.py <image> /opt/pearnly/deploy` → **Deploy tooling matches this image**（`infrastructure/production` 与 `scripts/production` 自 695325f1 起未变，无需同步） |
| 激活 | `bash /opt/pearnly/deploy/scripts/production/activate-product.sh <image> erp`，**2 分 21 秒**，输出 `Product erp activated with verified image.`（维护模式 → 排空 → 旧镜像整站备份 → migrate → enable-scheduler → 重建 7 个服务 → 关维护） |

### 发布后回读（只读）

- **7 个 ERP 容器**（web / worker / scheduler / socketio / frontend / ocr-worker / ocr-dispatch）全部运行新 digest，
  标签均为发布代码 SHA `fd5a979`；`/desk/pearnly-products` 返回 302（未登录跳登录，预期），静态资源 200，
  erp-web 近 6 分钟日志无 traceback/error。
- **其他产品与底座没被碰**：DMS 7 个容器 ID 与启动时间逐项与激活前一致（`3afd6dbc21af` / `c6a5b20bc447` /
  `98d5dd1d3386` / `126cbae9cc00` / `76c20b31e4aa` / `247b803a7835` / `8d32bcb655f0`，均 Up 18 hours）；
  billing-web、keycloak、mariadb、两个 redis 的容器 ID 与 Up 时间也未变。
- **镜像记录**：`active-image-erp.json` → 本次 digest；全局 `active-image.json` 仍为 `a11eb9e…`（未迁移产品保持旧候选）。
- **站点状态**：`maintenance_mode=0`、`enable_scheduler=True`、`pause_scheduler=0`；已装 App 与版本不变。
- **恢复材料**：本次迁移前整站备份 `20260925_014936-testerp_pearnly_com-{database,files,private-files,site_config_backup}`（站点私有 backups，保留）。
- **改动内容逐一核对**：`695325f1..fd5a979` 的 **39 个 App 文件**在 erp-web 容器内逐个 SHA-256 比对，
  缺失 0、内容不一致 0（另 3 个是 `docs/work`、`tests/`、`scripts/local-review` 下的非镜像文件）。
- **对外分发的静态资源**：`https://testerp.pearnly.com` 上 20 个改动过的 `public/` JS/CSS 逐个下载比对字节，
  全部与仓库一致（含 `pearnly_core/desk/list.js`、`desk/product.js`、`desk/product.css` 与 17 个 ERP 列表页 JS）。
- **业务计数前后一致**：商品 4 / 公司 2 / 盘点 1 / 客户 0 / 供应商 1 / 用户 2。
- 本轮**没有 schema 或数据迁移**（无新 patch、无 DocType 变更），`bench migrate` 只是标准动作。

### 未验 / 边界

- **生产页面上的点击验收没做**：那需要在 `testerp.pearnly.com` 登录后点列头看排序，本轮没有生产登录凭据，
  也没有用真实单据冒充分验收。生产侧目前是"代码/镜像/静态资源/状态回读一致"，不是"人在生产页面上点过"。
- 本轮**没有**做 Windows 真实环境验证，也**没有**切测试租户（`testenv.sh lab-start`）——那个环境另一个窗口在用。
- 发布不等于真机、真实业务或用户验收；原汇报口径里的这几层仍需单独确认。
- 预览 lane（8136，挂本工作树源码）与验收 lane（8126，旧镜像）保留未动；本工作树、本地分支
  `codex/stocktake-chart` 也保留（用户还要看预览）。远端无本任务分支，未创建。

## 第十三轮：官方有没有"这张表"的原生组件（2026-09-25）

用户问（贴的是收发存明细那张表）："你看下这个表有没有官方原生的，这是我们自己画的，如果有做一个
预览版出来我看看"。补一句口径："我要的是官方原生表格，那个模版格式不能动"、"有就有，没有就算了，
别硬画"。

### 结论：**分组表头没有官方组件**；合计行官方是有的

证据（都在预览站点装的 frappe 16.33.0 里看的）：

1. 表格组件是 `frappe-datatable` **1.20.7**（`p.table`、Desk 列表、报表页都用它）。它的源码里
   **没有 colspan/rowspan**：表头由 `rowmanager.getRowHTML(columns, {isHeader: 1})` 生成，就是
   **一行**；列定义里没有 children / group 这种概念。所以"单据 | 入库 | 出库 | 结存"这种两层表头
   它画不了。
2. 官方报表页（`frappe/views/reports/query_report.js`）与报表列表页（`report_view.js`）用的**就是
   同一个 DataTable** —— 换报表也还是单层表头。
3. 官方**合计行是有的**：`showTotalRow` + `hooks.columnTotal`（`frappe.utils.report_column_total`），
   报表页与报表列表页都挂这一对；列还能按 `disable_total` 逐列关掉合计。
4. 官方 JS 里没有别的"分组表头表格"：`erpnext/erpnext/public/js` 搜 colSpan 命中 **0**；frappe 里
   命中都在**表单布局**（dashboard / layout / column / naming_series），不是报表表格。框架自己的
   报表打印件 `print_grid.html` 也是单层表头。
5. 顺带核实：这几个租户**没装 erpnext**（`bench list-apps` 只有 frappe + `pearnly_core` +
   `pearnly_erp_portal`），所以 ERPNext 的 Stock Ledger / Stock Balance 一类"官方报表"在这里
   根本不存在，不能拿它们当参照。

### 所以这一页保持原样（预览已撤）

按用户"那个模版格式不能动 / 别硬画"：分组表头 + 合计行的格式继续按旧项目参考图保留，
DOM 仍只用官方类名（`.table .table-bordered` + `text-right`，与框架自己的报表打印件同一套）；
中途做过的"官方 DataTable 单层表头 + 官方合计行"预览（`?pearnly_native_table=1`）**已删除**，
页面回到零变化。

### 顺手修的两处（撤预览时暴露出来的真实缺陷，保留）

1. `p.table`（共享层）的"点列头排序"处理器改成**带命名空间、先解绑再绑**：同一个宿主重复建表时
   （页面重取会重建），旧表的处理器会留在宿主上 —— 点一次列头等于同时驱动好几张**已经销毁**的表，
   那些表的样式表已经摘掉，于是每张抛一次 `Cannot read properties of null (reading 'cssRules')`
   （实测 4 张表 = 4 次）。
2. 收发存明细页**一进页面就把 `api_stock_card` 打了 5 次**：建表时给 Date 默认值也会触发 `change`，
   原来自个儿照单全收。改成"日期真的变了才重取"，右上角那颗刷新仍旧强制重取（实测 5 次 → 1 次，
   改日期仍会重取）。

### 验证（预览 lane 8136）

- 明细页：`table.table-bordered` 1 张、两行表头（`单据 | 入库 | 出库 | 结存` / `日期 | 单据编号 |
  数量 单价 金额 ×3`）、`tfoot` = `合计 · 21 条明细 | 22 | — | 154.00 | 23 | — | 161.00 | -1 | — |
  -7.00`，没有任何 DataTable 实例；带旧参数 `?pearnly_native_table=1` 也不再改变页面。
- 一进页面 `api_stock_card` **1 次**；改起始日期 → 再 1 次；点右上角刷新 → 再 1 次。
- 无 pageerror、无 5xx。
- 门槛：`check-architecture.py` PASS、`i18n-build.py --check` PASS、
  `tests/test_native_desk_contract.py` OK、`node tests/test-native-desk.cjs`、
  `node tests/test-stock-card.cjs`（6 项，含分组表头与合计行）、`node tests/test-stock-entry.cjs`、
  `git diff --check` 全通过。
- **未推送、未合并、未部署**：这两处修复目前只在本地工作树。生产跑的还是 `fd5a979` 那版镜像
  （不含这两处），要带上就再走一次构建 + 投递 + 激活。

## 未做 / 下一步

## 第十五轮：第二次发布（第十三、十四轮一起上线，2026-09-25 02:15–02:30）

用户口径："就这样合并推送部署，有没有动共享文件"。

### 共享文件核对：**动了一个**

- 这次上线的内容是第十三、十四轮那两笔：`apps/pearnly_core/pearnly_core/public/desk/**product.js**`
  （共享层：新增 `p.field_grid`，"点列头排序"的绑定改成带命名空间先解绑）、
  `apps/pearnly_erp_portal/.../desk/stock-card.js`（只 ERP 用）、以及本任务记录。
- 同仓 11 个工作树逐个比对（各自 `main...HEAD` 加工作区改动）：**只有本分支自己**命中；
  DMS 窗口（`codex/dms-five-minute-snapshots`，HEAD 已到 `073f735`）**没有改过 `product.js`**，
  其余 ERP / Cowork / testenv 工作树也都没有交集 —— 无冲突、无需协调让位。

### 合并、闸门与推送

- 合并前 `main` = `origin/main` = `5ab7c2f`，本分支领先 2 个提交（纯快进）；main 工作区干净。
- CI 同名闸门逐条实跑全通过（架构 0 违规、DMS 离线 27/0、原生 Desk 22 项、产品激活 10/10、
  容量、开户、欢迎邮件、队列目标、i18n、POS 生命周期 9/9、收发存 6 项、入出库、选择/删除、
  issue-actions、`git diff --check`）。
- `git merge --ff-only` → `f0f5a43`；推送后回读 `origin/main` 与本地 HEAD **一致**。

### 镜像、投递与激活

| 步骤 | 结果 |
| --- | --- |
| 生产镜像构建 | run [36047068432](https://github.com/skin306152-star/pearnly/actions/runs/36047068432) success（约 6 分钟） |
| 镜像 | `ghcr.io/skin306152-star/pearnly/frappe@sha256:a3d007a76ec95fa40547cb2348fe3f61b4f2385175677ec356bbc9e392352044` |
| 镜像标签 | `org.opencontainers.image.revision=f0f5a4325711637c9f1cb849219824c6d1976cd6` |
| 部署工具同源 | `check-deploy-image.py` → **Deploy tooling matches this image** |
| 投递 | run [36047758396](https://github.com/skin306152-star/pearnly/actions/runs/36047758396) success |
| 激活 | `activate-product.sh <image> erp`，**1 分 35 秒**，`Product erp activated with verified image.` |

### 发布后回读（只读）

- 7 个 ERP 容器（web / worker / scheduler / socketio / frontend / ocr-worker / ocr-dispatch）全部是
  新 digest，标签均为 `f0f5a43`。
- 其余服务逐项与激活前一致：DMS 7 个（`3afd6dbc21af` / `c6a5b20bc447` / `98d5dd1d3386` /
  `126cbae9cc00` / `76c20b31e4aa` / `247b803a7835` / `8d32bcb655f0`，Up 18 hours）、
  billing `fca3624c404d`、keycloak、mariadb、两个 redis 的容器 ID 与 Up 时间都没变。
- `active-image-erp.json` = 本次 digest；全局 `active-image.json` 仍是 `a11eb9e…`（未迁移产品不动）。
- `maintenance_mode=0`、`enable_scheduler=True`；备份 `20260925_022519-testerp_pearnly_com-*` 已留。
- 本次两个 App 文件在容器内 SHA-256 与仓库一致；`https://testerp.pearnly.com` 上
  `assets/pearnly_core/desk/product.js` 与 `assets/pearnly_erp_portal/desk/stock-card.js`
  下载比对也**字节一致**。`/desk/pearnly-stock-card` 返回 302（未登录跳登录，预期）。
- 业务计数前后一致：商品 4 / 公司 2 / 盘点 1 / 客户 0 / 供应商 1 / 用户 2；本轮无 schema 或数据迁移。

### 仍未验的层

- **生产页面上的点击验收没做**（需要登录生产账号）：本次是"代码 / 镜像 / 静态资源 / 状态回读一致"，
  不等于"人在生产页面上点过"。收发存那页现在应该是一排 4 个字段的布局，请你在线上看一眼。
- 没做 Windows 真实环境验证、没切测试租户。

## 第十四轮：收发存明细页的字段改成一排 4 个（2026-09-25）

用户口径："把上面那7组字段，分成一排4组方，视觉对齐"。

### 原来的样子 / 为什么不是 4 个一排

那一页原来是 `p.field_rows([…7 个字段…], 4)`。这个共享 helper 的 `columns` 是**每栏几个**
（Frappe 的"栏"是竖着堆字段的，`Column Break` 换下一栏），所以 7 个字段排出来是
**2 栏 × 每栏 3~4 行**（左：开始日期 / 结束日期 / 物料 / 仓库；右：仓位 / 期初 / 期末）——
和用户要的"一排 4 个"正好差 90 度。

### 改法

共享层新增 `p.field_grid(fields, per_row)`（与 `field_rows` 并存，不动它原来那个语义）：

- **一排 N 个**：一个字段一栏，行与行之间用 `Section Break` 断开；整行控件（HTML / Table /
  Button / Section）仍自己占一整排。
- **末尾补空栏**：框架是按"本节里可见栏数"平均分 12 格的（`column.js` 的
  `resize_all_columns`：`col-sm-<int(12/栏数)>`）。第二排只有 3 个字段时会被算成 3 栏、
  每栏 4/12，比第一排的 3/12 宽，两排的竖线就对不齐 —— 补一栏空的，两排都是 4 栏。

收发存明细页改成 `p.field_grid([…7 个字段 + 明细表…], 4)`：第一排「开始日期 / 结束日期 /
物料 / 仓库」，第二排「仓位 / 期初数量 / 期末数量 + 空栏」，明细表照旧另起一节占满整宽。

### 验证（预览 lane 8136，1440 与 390 两种宽度）

- 1440：两排每栏都是 `form-column col-sm-3`、宽 313px，左边缘 x = 235 / 548 / 860 / 1173
  **两排一致**（视觉对齐就是这条）；明细表 `col-sm-12`、1250px 占满。
- 390（手机宽度）：`scrollWidth == innerWidth == 390`、**无横向溢出**，`col-sm-3` 落到
  断点以下自动堆成一列（每栏 359px）。
- 同一页的"库存 → 查看"弹窗仍走 `p.field_rows`（两栏 `col-sm-6` + 整宽明细表），未被这轮影响。
- 无 pageerror、无 5xx。
- 门槛：`check-architecture.py` PASS、`i18n-build.py --check` PASS、
  `tests/test_native_desk_contract.py` OK、`node tests/test-native-desk.cjs`、
  `node tests/test-stock-card.cjs`、`node tests/test-stock-entry.cjs`、`git diff --check` 全通过；
  `check-desk-ui.py` 仍只有那 2 处 main 上就有的历史违规。
- **未推送、未合并、未部署**（连同第十三轮那两处修复一起停在本地）。

- 没有"看板按"切换（商品/仓库/仓位）。
- DMS / Cowork 的列表排序没做（等用户点头，见第十一轮"边界"）。
- 未推送、未合并、未部署；测试环境 `testerp.pearnly.com` 上看到的仍是旧图。
- 上一轮盘点任务的验收 lane（8126，镜像停在 `1fd06f3`）保留未动；它的资源索引已被预览 lane 接管，
  要继续用它做"和上线一致"的检查，需按 `stocktake-preview.sh faithful` 重建同源镜像。
