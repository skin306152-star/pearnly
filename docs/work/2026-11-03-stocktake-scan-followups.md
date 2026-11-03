# 盘点框内扫码上线后的四处后续（11-03）

基线：`790dc639b`（= origin/main，含 11-02 盘点框内扫码 b8bb356d）；分支 `pearnly/stocktake-scan-followups`。
来由：11-02 上线后三星从 LINE 入口补测发现三处小问题 + 一条测试数据错误，用户已同意修（主会话 11-03 派活，交接见共享笔记
`交接-盘点扫码后续-11-03.md`）。前一份评估：[2026-11-02 盘点框内扫码](2026-11-02-stocktake-scan-inline-评估.md)。

## 需求影响评估

| 项 | 结论 |
| --- | --- |
| 同类复用 | ② 用 `link.js` 现有的 `pearnly_no_create`（收银台客户框已在用）；③ 网址码判断原在 ERP `products/contracts.py`，挪到共用层一份，两个产品同一个判断、同一条文案；① 只用读码器现有的 `pause()` / `resume(skip)`，不动共用读码器 |
| Frappe 原生 | 下拉仍是原生 Link（awesomplete）；画面显隐改成直接切框架自己的 `hide-control` 类，不调 `set_df_property`（它会 `refresh_input` 重画 HTML 格、换掉 `<video>`，16.34.1 `ControlHTML.refresh_input` / `FieldGroup.set_df_property` 已读源码核实；段落隐藏只切段落外层的类，不刷新里面的格） |
| 按类通用 | 网址码按类判断（`http://` / `https://` 开头），不针对 TIS 一家；ERP 认码 / 建码 / 盘点 / LINE 手机盘点 / 导入 与 Cowork 盘点查码全走 `pearnly_core.desk.scan_codes.web_address` |
| 上下游与 Express / CC | 记数载荷、盘点结果、Express 不变；Cowork 的 CC 盘点工具 `stocktake_count` 遇到网址码回「未保存 + 这不是商品条码」（原来会 KeyError） |
| 隔离与权限 | Cowork `counting.lookup` 先过 `service._task`（任务与工作区权限）再判网址码，权限顺序不变；判网址码不查库 |
| 各入口 | 电脑 / 手机网页、LINE 富菜单进的 LIFF 都是这两个弹窗；LINE 手机盘点页 `/stocktake` 只换了判断的出处，行为不变 |
| 共用件 | 新登记共用件 `scan-codes` 与入口 `shared.scan-codes.web-address`；改到 `erp-count-code`（`inventory/count.py`）。没改 `scan.js` / `reader.js` / `link.js` |
| 成本 | 无新增费用 |
| 作废删除 | 不涉及 |
| 文案 | 无新增 / 改动文案；「这不是商品条码」从 ERP 术语表挪到 core 术语表（四语原样），Cowork 站点才有译文 |
| 待问 | 无 |

## 改动

| # | 文件 | 内容 |
| --- | --- | --- |
| ② | ERP / Cowork `public/desk/stocktake-entry.js` | 录入框 `code` 加 `pearnly_no_create: 1`：打字下拉不再有「+ 新建物料」（管理员才看得到那项），也藏掉框旁去物料表单的箭头；新建只在扫到 / 输入系统没有的码时给 |
| ③ | `pearnly_core/desk/scan_codes.py`（新） | 多产品共用的纯函数 `web_address` 与文案 `NOT_PRODUCT_CODE` |
| ③ | ERP `products/contracts.py`、`products/codes.py`、`inventory/count.py`、`inventory/line_mobile.py` | 删 ERP 自己那份 `web_address` / `NOT_PRODUCT_CODE`，改用共用层；调用方核过全部（`grep -rn web_address apps`：认码 `codes.refuse_web_address`（物料选择框 `masters/selection.py` 经它）、盘点 `lookup_count` / `products_of_code` / 导入、LINE 手机盘点 `line_mobile`），都不是公开 API / 队列 / 持久化路径，不留兼容壳 |
| ③ | Cowork `stocktake/counting.py`、`stocktake/desk_api.py`、`assistant/tools.py` | `lookup` 遇网址码回 `{"not_product_code": 译文}`（同 ERP 形状），接口原样交回；CC 工具回未保存 |
| ③ | Cowork `public/desk/stocktake-entry.js` | `match()` 补 `not_product_code` 分支：写进弹窗 + 原生浮动提示、失败音、扫码器继续开着并先跳过这个码（原来 417 走 catch，提示「找不到盘点记录」） |
| ① | ERP / Cowork `public/desk/stocktake-entry.js` | 读到物料、查不到、手动「关闭」/ 再点图标：只**收起**扫码器（`park_camera`：`scanner.pause()`、切 `hide-control`、关手电），相机流不关；「继续」/ 再点图标在同一路流上恢复（`unpark_camera`：露出画面、`video.play()`（iPhone 收起的 `<video>` 会被暂停，被拦就等点一下画面）、`resume(上一个码)`）。真关只在：关掉弹窗、去拍照（手机相机应用要用相机）、收起超过 3 分钟（状态栏相机指示不一直亮）、读码出错；恢复时流已断（被别的应用占走）就照新开。收起着时打字查的码没认出来不让藏着的相机开始读（`rescan` 只在扫码器开着时续扫） |
| — | `tests/test-stocktake-entry-scan.cjs` | 两个产品各断言：「继续」不重新要相机（`starts` 不增、`stops` 为 0）、画面不用 `set_df_property` 收、恢复时再播与跳过刚扫的码、3 分钟真关、关弹窗真关、流断了重开、收起着不续扫、网址码提示且扫码器继续开、`pearnly_no_create` |
| — | `tests/test_erp_product_attribute.py` | 网址判断改测共用层；加源码守卫：ERP 不再有自己的 `web_address`，四个调用方都用 `scan_codes.web_address(`；文案读 core 术语表 |
| — | `scripts/impact/shared-parts.toml` | 登记共用件 `scan-codes` 与入口 `shared.scan-codes.web-address` |
| ④ | 生产测试租户 testerp（数据，不进代码） | QA1101-item（`PRD-169992`，P-000008）条码 `8851234567895`（校验位错）→ `8851234567898`：生产 ERP web 容器内 Frappe 客户端，先核原值与 `codes.assert_free`（新码无人占用），`doc.save()`，读回 `barcode=8851234567898`、`modified=2026-11-03 09:55:18`。只动这一条 |

## 验证

（施工后补：本地闸门、实验栈流程、共用件连带回归、三星真机。）
