# 真机记录：Cowork LINE 盘点 LIFF 相机（2026-11-04）

- 行 id：`cowork.scan.line.stocktake.liff-camera`（产品地图 `scripts/impact/product-map/cowork.toml`，device_only）
- 日期：2026-11-04 17:46–17:49（曼谷）
- 被测代码：本分支 `pearnly/scan-expected-no-report` e70f0169c（相机被拒只提示、不上报），在测试机集成树里与 `pearnly/cc-unified` f003fd940 一起合在 main 590626e98 上（集成提交 19e6a7473，只在本机，不推送）
- 环境：办公室测试机 `testenv.sh lab-start cowork`，镜像 = 生产 c9057f6f 那份 `frappe@sha256:29ae8ca65e5cb183694da3ee53631be690507bbd942f5158c463b728cda07bf0`，实时源码挂集成树；testcowork.pearnly.com 已切到测试机（CNAME → 隧道）
- 设备：三星 SM-A146P（adb R92W60HEQPF），LINE 官方号 Cowork-test；测试机屏幕用 Edge 全屏显示 EAN-13 码图 8851932100014

## 步骤与结果

| # | 步骤 | 结果 |
|---|---|---|
| 1 | LINE 打开 Cowork-test 对话 → 富菜单「库存盘点」 | 经 LINE 登录入口 `line_login.enter?to=/desk/pearnly-stocktake?pearnly_liff=1` 打开 LIFF 盘点列表；列表是测试机导入的那两份（「123」与上月盘点），测试机前端日志有这几次请求 |
| 2 | 打开「123」→「开始盘点」→ 输入框右侧扫码图标 | LINE 弹原生授权「是否允许此网站访问您的相机?」 |
| 3 | 点「不允许」 | 只出提示（LINE 的相机权限已关闭……返回 LINE 后将自动重试），没有报错弹窗；Error Log 仍是 2 条、最新 2026-10-30，没有新条目 |
| 4 | 再点扫码图标 → 「仅允许本次」 | 相机打开，对着测试机屏幕约 2 秒读出；请求 `api_stock_count_lookup?count=a9fb5098…&code=8851932100014` |
| 5 | 看弹窗 | 认出商品「สบู่ก้อนลักส์ 70 กรัม」（P-0014），账面 คลังสาขาบางนา / S-02-02 · 150 ก้อน，光标停在「本次实盘数量」 |
| 6 | 不录数量，关闭弹窗 | 盘点汇总仍 0/7、0/10，数据未改；Error Log 仍 2 条 |

结论：通过。读出号码、认出商品；拒绝相机只提示、不上报。

## 截图（本机，不进仓库）

`/Users/skin/pearnly-devtools/device-1104/`：c03 列表、c04 盘点单、c06 授权弹窗、c07 拒绝后提示、c09 / c10 扫到并认出商品、c13 关闭后汇总未变（各有 `-s` 缩小版）。
