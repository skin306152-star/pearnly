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
