# 盘点扫到没有的码：「新建物料」看不到 / 点不动（岳父 11-01）

分支 `pearnly/stocktake-new-visible`（基线 origin/main 99ac4898），工作树 `.claude/worktrees/erp-dev-2-f29f34`。

## 现象

岳父 11-01 12:07 在 test01 用手机浏览器开 Desk 盘点录入，相机扫到系统里没有的码，只看到原生提示「ไม่พบสินค้าที่ตรงกับรหัสนี้」，
看不到「新建物料」；用户随后实测：点「新建物料」没反应。

## 根因（有证据）

1. 查不到码时服务端 `count.lookup_count` 抛 417，原生提示框盖在按钮上。证据：在 origin/main 版本上跑新流程（手机宽度），
   按钮中心点 `elementFromPoint` 是提示框（`on_top: false`、`prompt: 2`），按位置点击打不开新建表单。
2. 真机（三星 SM-A146P，本机实验栈）相机画面占满一屏，提示和按钮都在画面下面（截图在本窗口 scratchpad s3.png）。
3. 相机扫到的码只传给 `match(code)`，没进编码栏；按钮是 `create_here(d.get_value("code"))`，空值直接 return，所以「点了没反应」。
   旧流程走手输、先关提示框、直接 JS 触发按钮，三处都测不出。

## 已做（提交 2a511769，已推分支）

- `inventory/count.py`：`lookup_count` 按码查不到时回 `{"not_found": true}`，不再抛提示框（照官方 POS 扫不到码的做法）；
  按商品查不到仍抛。这个接口只有 ERP 盘点录入一个调用方（Cowork 有自己的 `desk_api`）。
- `public/desk/stocktake-entry.js`：查不到 → 停相机、收起画面，浮动提示 + 弹窗内一行，「新建物料」出来并聚焦；
  新建用这次查不到的码（`missing`），不读输入框；记完下一件相机自己重开（`rescan`）。删掉按提示文字判断「没有这个码」的解析。
- 流程 `erp.stocktake.new-product`：码改合法 EAN-13，`camera = true` 手机宽度另用假摄像头扫一遍；断言没有提示框、相机已停、
  按钮在可视区且中心点就是它、按位置点、新建表单里是扫到的码、保存后相机重开。`run.py` 相机那一遍改读它自己那份准备里的码。
- `tests/test-stocktake-entry-scan.cjs` 加相机扫未知码那一段；吞错基线删掉已不存在的那条。
- 本地闸门全过；流程 erp.stocktake.new-product + erp.stocktake.scan 8/8（含相机格）；`--changed --post-status` 5 条在跑。
- 三星真机（实验栈 erp-new，真相机、adb 真触摸）：查不到 → 相机停、无提示框、按钮在眼前 → 点开新建（码是扫到的）→ 提交即认出 →
  填 2 保存 → 相机重开；库里新商品来由 stocktake / 这份盘点、条码 = 扫到的码。读码那一下是在页面里截住读码器回调喂的码
  （手机镜头对着别处），其余全是真机。

## 同类入口

- LINE 手机盘点 `www/stocktake.html`：读到码就 `stopCamera()`，`offerNew(code)` 用的是传进来的码（不读输入框），新建框插在码那张卡片里、
  没有提示框，同类问题不存在（代码核对，没真机走）。
- Cowork Desk 盘点录入没有当场新建，不涉及。
- 还没逐个核对：`products.js`（扫码直接写进 code_value，应无问题）、`serials.js`、`entry-choice.js`、`pos/pearnly-pos-adapter.js`
  里「扫到的码没进字段、后续按钮取字段」的情况。

## 交接：还没做（用户新口径，主会话 11-01 转达）

用户原话：「点击新建商品原地弹出新建物料的弹窗给新建不就好了吗」。要求：

- 盘点里点「新建物料」原地弹出物料页同一个标准新建弹窗（`products.js` 的 `product_fields`），不要现在这个 4 格简化弹窗；
  码预填进「条码/二维码」一格；保存后关弹窗、回盘点自动按这个码接着记数。
- 来由仍记「盘点新建 · 盘点单号」，权限仍按盘点录入权（`count.write`），`count.create_product_here` 的守卫保留。
- LINE 页是独立页面不跑 Desk：保持同一套字段口径，交代怎么处理。

施工要点（我读代码的判断，未实施）：

- `product_fields` 在 `p.products` 闭包里；已有内联入口 `p.link_editors["ERP Product"](context)` → `open_quick`，但它提交
  `products.service.api_create_product`（要 `products.manage`，来由写 manual），不满足「盘点录入权 + 来由盘点」。
  建议把 `open_quick` 的提交函数做成可传入（内联 context 给 `submit`），盘点传自己的提交：调
  `api_stock_count_create_product`，服务端 `create_product_here` 扩成收标准字段（品名、内部编码、条码/二维码、分类、单位、库存单位、
  售价、成本、仓位、最低库存、VAT、图片），校验沿用 `product_service` 的 `_text` / `_assert_unique` / `_price_fields` 等，
  仍 `guard.require("count.write")`、盘点锁、先按码再认一次、`origin=stocktake`。期初网格、全屏编辑、按台两格在盘点里不出。
- 码预填：内联 context 加 `code` → `product_fields({..., barcode: code})` 那一格默认值。
- 流程 / 单测跟着改（新建表单字段名变了），再跑 `--changed` 与三星真机。用手机前先在共享「测试机占用.md」登记，跟真机验收窗口错开。

## 现场

- 实验栈 `pearnly-verify-lab`（端口 18781–18783）在跑，挂本工作树源码；已跑 lab_billing、测试账号时区已改 Asia/Bangkok。不用了就
  `docker compose -p pearnly-verify-lab -f infrastructure/compose/regression-lab.yml stop`（保留数据卷）。
- 三星手机已释放（adb forward / reverse 都已删），占用表已补登。

## 第二步：点「新建物料」原地弹物料页的标准新建弹窗（stocktake-product-dialog，11-01）

分支 `pearnly/stocktake-product-dialog-eb38c3`，基于 `origin/pearnly/stocktake-new-visible`（b00e51a9），合入前变基到 main。

### 改了什么

| 位置 | 改动 |
|---|---|
| `public/desk/products.js` `open_quick` | 内联入口（`p.link_editors["ERP Product"](context)`）多收三项：`code` 预填进「条码/二维码」（按码型落条码 / 二维码）、`preview()` 取编码预览、`submit(payload, values)` 自己保存。给了 `submit` 时期初网格不出、不调商品页的 `toggle_serial`、扫码图标不当场查重（借用方不一定有商品查看权，撞码交给它的服务端）。字段就是 `product_fields` 同一份，没有复制 |
| `public/desk/stocktake-entry.js` `create_here` | 删掉自做的 4 格弹窗，改调上面那个入口：编码预览 `api_stock_count_next_product_code`、保存 `api_stock_count_create_product`（码交给服务端认码型，系统编码服务端发），保存后弹窗关掉、收起「新建物料」、按存下的码接着记数（码清空了就按商品） |
| `products/service.py` | 抽出 `standard_fields`（单位、售价、含 VAT、图片、低库存、参考成本、库存单位、分类、仓位、号码种类 → 落库列与校验），`create_product` 与盘点建档共用 |
| `inventory/count.py` `create_product_here` | 守卫、盘点锁、先按码再认一次、GS1 记 GTIN、来由「盘点新建 · 盘点单号」都不变；多收标准字段（白名单后交 `standard_fields`）；按台公司勾了按台就在建档时打开（新商品没库存，同 `serials.turn_on` 无库存分支）；挂图片。新增 `next_product_code`（`count.write`，只读预览不占号） |
| `inventory/api.py` | `api_stock_count_next_product_code` |
| `inventory/line_mobile.py`、`www/stocktake.html` | LINE 页不跑 Desk、弹不出原生弹窗：新建卡片照标准弹窗的各格（同名词条 `product.field.*` / `serial.*`、同一个服务端函数），码预填；按台两格只在汽车 / 手机电器公司出现（`task.unit_kind`）。只少「图片」一格（这页没有附件控件，进物料页再补） |

权限口径：盘点里新建只要盘点录入权（`count.write`），三个接口都走 `count.write`；商品页的 `api_create_product` / `api_next_product_code` / `api_find_product` 盘点不调用。

### 同类入口

- 入库 / 出库 / 采购 / 销售 / 期初的物料选择框「新建物料」本来就是 `link_editors["ERP Product"]` → 同一个 `open_quick`，不用改。
- 全仓搜过 `api_create_product` 与自做新建物料表单：除盘点录入（已改）与 LINE 盘点页（见上）外没有别的。
- Cowork 盘点没有当场新建。

### 验证（提交 4c398b6d）

- 本地：`.github/workflows/development.yml` 155 步全跑通过（共用件登记表一处误认调用方改了注释；读码器契约那步在实验栈初始化时偶发失败，重跑通过）；
  `tests/test-stocktake-entry-scan.cjs` 加了「点新建 → 借物料页弹窗、预览与保存走盘点接口、建好接着认出」一段。
- 流程（实验栈 pearnly-stdlg，`--changed` 13 条 × 两套数据 × 宽度）：43/43 通过（`erp.stocktake.scan` new 两格首轮报「页面被导航」环境问题，
  同一提交 `--rerun-failed` 补跑通过）。第一轮抓到一个真缺陷：`create_product_here` 收标准字段的参数名与函数里 GS1 解析结果同名，
  普通码时被覆盖成空、建档失败（GS1 码碰巧能过），已改名修好。
  - `erp.stocktake.new-product`：断言标准弹窗 13 格齐全、编码是 P- 预览、码预填、无期初、提交后弹窗关掉；库里内部编码 / 单位 / 库存单位 /
    售价与已设 / 成本与已设 / 仓位 / 低库存 / VAT / 来由 / 未开按台都对。
  - `erp.line.stocktake`：汽车公司下新建表单各格与 NEW_FIELDS 一致、码预填、号码种类默认「车」、VAT 默认勾；库里单位、售价落库。
- 三星 SM-A146P 真机（本机实验栈 erp-new，真相机拍测试机屏幕上的未知 EAN-13 8859791340435，真触摸）：相机读到 → 停相机、无提示框、
  「新建物料」在眼前 → 点开就是物料页的新建弹窗（编码 P-000015 预览、条码/二维码已填扫到的码）→ 真键盘输品名、提交 → 弹窗关掉、
  回盘点认出这件 → 记 4 件保存 → 相机自动重开，又读到同一个码直接认出新商品。库里 PRD-00435：条码 = 扫到的码，单位 ชิ้น、售价 59、
  成本 35、低库存 3、含 VAT，来由 stocktake / STC-00434，建档人 stock@erp.regression（盘点岗）；盘点记录 4。
  截图在共享笔记 `盘点标准新建弹窗-真机截图-11-01/`。LINE 页没上真机（流程覆盖）。
- 没测到：按台公司在 Desk 盘点里勾按台新建（流程数据集是普通公司；服务端在建档时开按台、LINE 流程覆盖了汽车公司的表单）。
