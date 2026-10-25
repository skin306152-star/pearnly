# 打印第一批：领料单、收货单（岳父样板 1:1）

基线 main `d8d42e6f`，分支 `pearnly/print-batch1`。评估：`docs/work/2026-10-25-print-formats-评估.md`（主会话 10-26 审定）。

## 做了什么

| 单据 | 打印成 | 入口 | Print Format |
| --- | --- | --- | --- |
| 出库单 `ERP Stock Entry` direction=out | 领料单 ใบเบิกสินค้า / GOODS ISSUE SLIP（样板 17） | 出入库单弹窗「打印」（已提交 / 已作废） | Pearnly Goods Issue Slip |
| 入库单 `ERP Stock Entry` direction=in | 收货单 ใบรับสินค้า / GOODS RECEIPT NOTE（样板 18） | 同上 | Pearnly Goods Receipt Note |
| 带入库的采购单 `Purchase Document` | 收货单（同上版面） | 采购单页菜单「打印」（入了库、已提交 / 已作废） | Pearnly Purchase Goods Receipt |

- 全走原生打印链路：`/printview` 预览、打印、存 PDF（wkhtmltopdf 0.12.6）。标准 Print Format 随代码维护（`pearnly_erp_portal/print_format/`），
  版面在 `printing/templates/`、取数在 `printing/context.py`（Jinja 方法 `pearnly_print_context`）、纯规则在 `printing/contracts.py`。
- 抬头按单据所属公司取（出入库单按仓库的公司、采购单按单上的公司）：名称、地址、电话、邮箱、Logo。公司新增 `logo`（Attach Image），
  没传就用 App 自带的 PEARNLY Logo（从样板裁出的那一版）；页脚不放 Pearnly 小字。Logo 内嵌成 data URI（PDF 生成器拿不到私有文件地址）。
- 接了 Express 的公司：名称、地址、电话并进 Express 锁定（`express.locks.COMPANY_LOCKED`），公司页只读，同步时以 Express 为准覆盖
  （名称取电脑报的账套公司名，地址 / 电话取 ISINFO；账套里是空的不动）。
- 作废单照样能打，加「ยกเลิก / CANCELLED」水印，只在作废单上出现；正常单据版面 1:1。
- 日期照样板用公历 日/月/年；采购单的对方单号不冒充 DO No.（留空）。每页 10 行，超过续页（序号接着往下、页码 n / N）。
- ERP 没有的栏位一律留空线（领用部门 / 领用人 / 申请数 / PO、DO 号与日期 / 运输方式 / 签字……），勾选按评估第 4 节映射：
  内部领用 → ใช้งานภายใน；报损 / 其他 → อื่นๆ 并写原因；收货单公司主仓（这家公司最先建的仓）→ คลังหลัก，其余 → อื่นๆ 并写仓库名。
- 出入库单弹窗标题改显示单据号（原来是内部名 STE-…，验收 10-26 低项）。

## 与评估的出入

- **print 权限位不补**：实验栈复现，收银员对自己的小票只有读权限（print = 0）也能打开原生打印视图、下载 PDF——Frappe 16 的
  `validate_print_permission` 是「读或打印任一就放行」。「收银员打不开打印收据」在权限层面复现不出来，所以不给各岗位加 print 位
  （不无谓放宽权限）。用户若再遇到，要现场看是哪一步（弹窗被拦、打开的是默认格式之类）。
- **抬头不用 Letter Head**：样板的抬头和标题框、单号框在同一行，并且每页重复；整条抬头直接放在 Print Format 里逐页画，打印时 `no_letterhead=1`。
- **字体**（主会话 10-26）：开源授权（SIL OFL 1.1）的泰文字体随 App 放在 `public/fonts/print/`（`<家族>-Regular.ttf` / `-Bold.ttf`），
  `printing/context.py` 的 `fonts_css` 内嵌成 `@font-face`（PDF 生成器不读本机文件、也不一定连得上站点，内嵌最稳），不改镜像；
  目录空着就退回镜像自带的 Loma。样板是无圈头泰文（认作 Prompt），本机没有，下载要用户同意，等主会话答复；现先放 Sarabun（同为 Cadson Demak、OFL，
  授权写在字体文件自带的元数据里），PDF 里实际嵌入 Sarabun-Regular / Bold，泰文上下标（ผู้、ที่、ค้า、จ่าย）300 dpi 看位置正确；
  勾号 ✓ Sarabun 没有，落到 DejaVu。Frappe 出 PDF 时会用 cssutils 解析样式，内嵌两份字体后页面约 26 万字符，解析约 1 秒（amd64 模拟下测）。
- **Logo**（主会话 10-26）：改用正式品牌包的横版矢量 lockup（`pearnly-erp/assets/branding/pearnly-v1/pearnly-lockup.svg`，2026-09-05 定稿，
  与官网图标同一个 P），放 `public/images/print/pearnly-logo.svg`，以 `image/svg+xml` 内嵌，打印不失真；样板裁图已删。

## 验证

- 单元：`tests/test_erp_printing.py`（分页、数量 / 金额 / 日期写法、勾选映射、版面标签与数据、水印、多页）；版面渲染部分在生产镜像（候选检查）跑，
  开发检查只跑纯规则。`tests/test_erp_express_direct.py` 补：公司资料以 Express 为准、接了 Express 的公司改名称被拦、Logo 照常可改。
- 实验栈（10-26 02:10–02:29，挂 a4dc0377 之后的工作树，REFRESH 导入）：建演示公司（不接 Express）与出库 / 入库 / 作废出库 / 带入库采购各一张，
  走框架原生 `get_print` 出 HTML（Chromium 截图当预览）与 PDF：四张都是一页、版式与样板一致；公司传 Logo 后打印用公司的、清掉回落 PEARNLY、
  不存在的文件地址被拒；接了 Express 的公司（CO-00162）改名称报「Maintained in Express」，改备注照常。
- 样张：共享笔记「打印样张-第一批/」（领料单、收货单、采购单收货单与样板并排的对照图 + 三份 PDF + 作废水印 PDF）。

## 待确认 / 下一步

- 样张交主会话转用户和岳父确认，确认后收尾（过闸门、交验收、合入）。
- 第二批（销售单、收银小票、采购订单、报价单、盘点、交班）开工前先给主会话清单；税票口径照评估写死。
