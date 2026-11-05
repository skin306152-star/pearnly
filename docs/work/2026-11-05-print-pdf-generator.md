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

## 3. 共用件连带回归（`check-impact`：erp-printing 6 个入口）

（实验室结果待补）

## 4. 检查

（待补：全闸门、实验室、CI、合入 SHA。部署后要在一个租户上真点一次「获取PDF」才算业务验收。）
