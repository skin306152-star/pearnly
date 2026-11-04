# 真机记录：ERP LINE 盘点 LIFF 相机（2026-11-04）

- 行 id：`erp.scan.line.stocktake.liff-camera`（产品地图 `scripts/impact/product-map/erp.toml`，device_only）
- 日期：2026-11-04 19:49–19:51（曼谷）
- 被测代码：本分支 `pearnly/scan-expected-no-report` 47cb82a0e（相机被拒只提示、不上报；代码同 e70f0169c，之后只加了 Cowork 真机记录），在测试机集成树里与 `pearnly/cc-unified` 5c28d1900、`pearnly/cc-gate-fresh` 052505f0f 一起合在 main 590626e98 上（集成提交 dc5a562f4，只在本机，不推送）
- 环境：办公室测试机 `testenv.sh lab-start erp`，镜像 = 生产 c9057f6f 那份 `frappe@sha256:29ae8ca65e5cb183694da3ee53631be690507bbd942f5158c463b728cda07bf0`，实时源码挂集成树；testerp.pearnly.com 已切到测试机（CNAME → 隧道），外部读回 `scan/reader.js` 与集成树同一份
- 设备：三星 SM-A146P（adb R92W60HEQPF），LINE 官方号 ERP-TEST；测试机屏幕用 Edge 全屏显示 EAN-13 码图 8850511321123

## 步骤与结果

| # | 步骤 | 结果 |
|---|---|---|
| 1 | LINE 打开 ERP-TEST 对话 → 富菜单「盘点」 | 经 LINE 登录入口 `line_login.enter?to=/desk/pearnly-stock-count?pearnly_liff=1` 打开 LIFF 盘点列表；列表是测试机导入的那两份（「111」与「月底盘点」），测试机前端日志有这几次请求 |
| 2 | 打开「111」（STC-00039）→「开始盘点」→ 输入框右侧扫码图标 | LINE 弹原生授权「是否允许此网站访问您的相机?」 |
| 3 | 点「不允许」 | 只出提示（LINE 的相机权限已关闭……返回 LINE 后将自动重试），没有报错弹窗；Error Log 仍是 4 条、最新 2026-10-26，没有新条目（10-26 那条正是旧代码把被拒上报成的「Browser error: The request is not allowed by the user agent…」） |
| 4 | 再点扫码图标 → 「仅允许本次」 | 相机打开，对着测试机屏幕约 2 秒读出；请求 `api_stock_count_lookup?count=STC-00039&code=8850511321123` |
| 5 | 看弹窗 | 条码/二维码 8850511321123，认出商品「番茄酱」（编码 P-000002），账面 Test repository · 74 瓶，光标停在「本次实盘数量」 |
| 6 | 不录数量，关闭弹窗 | STC-00039 已盘仍 0、盘点记录 0 条，数据未改；Error Log 仍 4 条 |

结论：通过。读出号码、认出商品；拒绝相机只提示、不上报。

## 截图（本机，不进仓库）

`/Users/skin/pearnly-devtools/device-1104/`：e05 列表、e06 盘点单、e08 授权弹窗、e09 拒绝后提示、e11 / e14 扫到并认出商品、e15 关闭后（各有 `-s` 缩小版）。
