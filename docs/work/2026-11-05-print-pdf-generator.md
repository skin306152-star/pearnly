# 施工记录：ERP 打印页「获取PDF」报 417（11-05）

- 分支 `pearnly/print-pdf-generator`，工作树 `.claude/worktrees/print-pdf-generator`，基线 `0fab31ad5`（= origin/main）。
- 来源：CC 1.12.14 实测（共享笔记 `CC1.12.14实测-11-05/结果.md`「顺带发现」第 1 条）：testerp 采购单 → ··· → 打印 → 获取PDF，
  报「服务器错误 417：未捕获异常」；地址带 `pdf_generator=None`（还有 `letterhead=None`、`key=None`），去掉它就回 200 PDF。不是 CC 的问题，任何浏览器都这样。

## 1. 根因

- 我们 ERP 的 7 个打印格式（`apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/print_format/*/*.json`：领料单、收货单、
  POS 小票、采购收货单、销售单据、班次汇总、盘点单）都没写 Frappe 原生字段 `pdf_generator`，这个字段在 Print Format 上没有默认值，库里是空。
- Frappe 16.33 原生打印页 `frappe/www/printview.py`：`pdf_generator = getattr(print_format, "pdf_generator", "wkhtmltopdf")` 取到 None，
  模板 `printview.html` 的「获取PDF」链接写 `pdf_generator={{pdf_generator|e}}`，渲染成字符串 "None"。
- `frappe.utils.print_format.download_pdf` 的参数类型是 `Literal["wkhtmltopdf", "chrome"] | None`，白名单接口按类型校验，"None" 不过 → 417。
- 不受影响的：取不到打印格式（Standard）时 `getattr` 给默认值 wkhtmltopdf；我们页面里直接下 PDF 的按钮（`transactions.js` 的
  `p.trade.print(..., {pdf: true})`）不带这个参数，`download_pdf` 收到 None 自己补 wkhtmltopdf。
- 同类：Cowork、DMS、共用层、控制面都没有自定义打印格式，走 Standard，不受影响。地址里另外两个 `letterhead=None`、`key=None`
  是同一模板渲染：我们打印都带 `no_letterhead=1`，`key` 只给访客链接用，不影响下载（实测去掉 pdf_generator 一项就回 200）。

生产核实（只读）：用 e2e ERP 账号经 fatcc.com 登 testerp，取一张采购单打开原生打印页，读到「获取PDF」地址里
`pdf_generator=None`，同一地址请求回 417（类型校验就拒，不生成文件；打开打印页会按 Frappe 原生规则记一条 Access Log）。
其他 ERP 租户没有测试账号、没法从外部登；打印格式是随 App 同步的标准格式，各租户库里这个字段都是空，结论相同。

## 2. 修法（Frappe 原生）

- 7 个打印格式 JSON 写明 `"pdf_generator": "wkhtmltopdf"`（Frappe 自己的默认值；生产镜像带 wkhtmltopdf 0.12.6.1，服务端
  `purchasing/output.py` 出 PDF 也用它），`modified` 改到 2026-11-05，`bench migrate` 按标准格式同步进各租户库。
- 测试 `tests/test_print_formats_pdf.py`：本仓每个打印格式都要写明 download_pdf 收得下的生成器（改前失败、改后通过），进 Development checks。
- 产品地图：ERP 采购、销售、盘点页头「Print」三行引用这条测试和已有的 `tests/test_erp_printing.py`（版式规则），
  `untested_baseline.txt` 同步删掉这三行的 6 条。

## 3. 实验室实测：7 个打印格式 × ERP 两套数据都真拿到 PDF

自起的一套回归实验栈 `pearnly-print-lab`（端口 18791–18793，主会话指定；用完连容器带卷删掉），装 ERP 新 / 老两套数据，
再跑收银、交班、采购、销售、出入库、盘点几条流程把单据建出来。验法与页面同一条路、走真 WSGI：打开原生打印页
`/printview?doctype=…&name=…&format=…&no_letterhead=1`，从 HTML 里取「获取PDF」的链接原样请求，核状态码、Content-Type 与文件头。
库里 7 个打印格式的 `pdf_generator` 在建站同步后都是 wkhtmltopdf。

| 打印格式 | 新数据（单号 → 结果） | 老数据（单号 → 结果） |
|---|---|---|
| Pearnly Goods Issue Slip（领料单，出库） | STE-2026-00086 → 200 · application/pdf · %PDF- · 35825 B | STE-2026-00117 → 200 · application/pdf · %PDF- · 34905 B |
| Pearnly Goods Receipt Note（收货单，入库） | STE-2026-00084 → 200 · application/pdf · %PDF- · 44982 B | STE-2026-00115 → 200 · application/pdf · %PDF- · 44412 B |
| Pearnly POS Receipt（收银小票） | SALE-2026-00098 → 200 · application/pdf · %PDF- · 31319 B | SALE-2026-00129 → 200 · application/pdf · %PDF- · 27737 B |
| Pearnly Purchase Goods Receipt（采购收货单） | PUR-2026-00034 → 200 · application/pdf · %PDF- · 45276 B | PUR-2026-00084 → 200 · application/pdf · %PDF- · 44383 B |
| Pearnly Sales Document（销售单据） | SD-00107 → 200 · application/pdf · %PDF- · 38067 B | SD-00138 → 200 · application/pdf · %PDF- · 37701 B |
| Pearnly Shift Summary（交班汇总） | SHIFT-2026-00094 → 200 · application/pdf · %PDF- · 32468 B | SHIFT-2026-00125 → 200 · application/pdf · %PDF- · 28683 B |
| Pearnly Stock Count（盘点单） | STC-00121 → 200 · application/pdf · %PDF- · 34986 B | STC-00155 → 200 · application/pdf · %PDF- · 34120 B |

链接里的 `pdf_generator` 14 格都是 wkhtmltopdf。

实验环境的两处限制（与本次改动无关，如实记下）：
- wkhtmltopdf 按站点地址回取页面里的字体和图片；编排文件的 `extra_hosts` 只把 `erp.regression.localhost` 指回本机，`erp-new` / `erp-old`
  两个数据站点解析不了，第一次下载回 500（OSError）。只在这套自起实验栈的容器里补了这两个站点名的解析后通过。共用回归实验室没有改；
  要在共用实验室验 PDF，`regression-lab.yml` 的 `extra_hosts` 要补上这两个站点名（列为后续）。
- 第一次试时老数据只有一张入库单，按领料单打被打印规则拒（只给出库单打领料单），所以先跑流程建单。

## 4. 共用件连带回归（`check-impact`：erp-printing 6 个入口）

| 入口 | 结果 |
|---|---|
| `erp.print.stock-entry` 出入库单弹窗「打印」 | 通过：领料单（出库）、收货单（入库）两套数据都从原生打印页拿到 PDF；版面由流程 `erp.print.all`（两套数据）覆盖、通过。作废水印、拒打读不到的单没有单独再点（本次只改生成器字段，不碰取数与版面） |
| `erp.print.purchase` 采购单详情「打印」 | 通过：采购收货单两套数据拿到 PDF；`erp.print.all` 通过 |
| `erp.print.sales` 销售单详情「打印」 | 通过：销售单据两套数据拿到 PDF；`erp.print.all` 通过 |
| `erp.print.pos-receipt` 收银「打印收据」 | 通过：收银小票两套数据拿到 PDF；`erp.print.all` 通过。浏览器直打（`trigger_print=1`）没测到：实验室是无头浏览器，没有打印机对话框 |
| `erp.print.pos-shift` 交班汇总「打印」 | 通过：交班汇总两套数据拿到 PDF；`erp.print.all` 通过 |
| `erp.print.stock-count` 盘点详情「打印」 | 通过：盘点单两套数据拿到 PDF；`erp.print.all` 通过。点数表 / 结果表两种版式由 `tests/test_erp_printing.py` 与 `erp.print.all` 覆盖 |

## 5. 检查

| 层 | 结果 |
|---|---|
| 本地全闸门（`gate.sh`，189 步，含新测试） | 全过 |
| 新测试改前 / 改后 | 改前（origin/main 的打印格式）失败；改后通过 |
| 产品地图 | 通过；3 行页头「Print」由红变绿，存量基线少 6 条 |
| 受影响流程 `run.py --changed` | `erp.print.all` 新 / 老两套数据通过 |
| 建数据时跑的 7 条流程 | 27/28 格通过；`erp.stock-entry.scan` 新数据手机宽度那一格 900 秒超时（模拟架构下慢），单独重跑整条 4/4 通过 |
| e2e 场景（主会话提议在 e2e 里加一条「获取PDF」） | 这次没加：施工时 `scripts/e2e/` 还在 product-map-next 的分支上，变基时才进 main；`deploy.sh` 现在部署后会跑 e2e、失败就停，没在 testerp 跑通过的新场景不该直接挂进去。建议另起一小步：加场景、先在 testerp 跑一次（部署前应失败、部署后应通过），再登记进地图。部署后由主会话用 e2eerp 在 testerp 点一次。**11-07 已加**（小件窗口）：场景 `e2e.erp.print-pdf`（`scripts/e2e/scenarios.py`，登记 `tests/e2e/registry.toml`），用 e2e ERP 账号登 testerp，打开最新一张采购单的原生打印页（采购收货单格式），点「获取PDF」：链接不带 `pdf_generator=None`、回 200 `application/pdf`、文件头 `%PDF-`；线上 08f32a3b1（已含本修复）电脑宽度跑通过，产品地图 `erp.desk.pearnly-purchase.action.Print` 引用它。部署前那一版应失败没法再验（线上已是修好的版本），场景断言直接查链接里的 `pdf_generator=None` 与 417 |

没有部署。部署后要在一个租户上真点一次「获取PDF」能下载到 PDF，才算业务验收。
