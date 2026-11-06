# 真机记录：ERP LINE 盘点 LIFF 相机（2026-11-06，部分完成）

- 行 id：`erp.scan.line.stocktake.liff-camera`（产品地图 `scripts/impact/product-map/erp.toml`，device_only）
- 为什么命中：分支 `pearnly/erp-stock-adjust-df0d3d` 改了盘点页 `public/desk/stocktake.js`，只加了页头按钮（「按盘点结果调整物料」、打开物料调整单）、状态文字；
  扫码与相机的代码（`scan/reader.js`、`stocktake-entry.js` 的扫码入口）没有改。
- 日期：2026-11-06 19:02–19:08（曼谷）
- 被测代码：本分支 32b286fd0（工作树无未提交改动），挂在本机实验栈 `pearnly-adjust-lab`（生产同一镜像底座），站点 erp-new.regression.localhost
- 环境：没有动测试机、没有切任何域名（主会话 11-06：测试机正被用户使用）。三星经 USB `adb reverse` 把手机上的 18791 / 8000 端口转到本机实验栈；
  登录用实验栈的一次性登录码（只对本机实验栈有效、用过即失效）。
- 设备：三星 SM-A146P（adb R92W60HEQPF），LINE 官方号 ERP-TEST 对话；三星自带 Chrome。

## 步骤与结果

| # | 步骤 | 结果 |
|---|---|---|
| 1 | LINE 打开 ERP-TEST 对话，发实验栈登录链接与盘点页链接，点开 | LINE 内置浏览器打开本分支的盘点页「真机 LIFF 相机 11-06」（STC-00492）与「连续盘点」弹窗，页面正常 |
| 2 | 弹窗里点扫码图标 | LINE 不弹授权框，直接提示「LINE 的相机权限已关闭……」。核对：LINE 应用的相机权限是开的（`appops CAMERA: allow`）；LINE 内置浏览器只对 https 网址开相机，本机实验栈是 http。11-04 那次走的是 testerp 的 https LIFF，才弹出 LINE 原生授权 |
| 3 | 改用三星 Chrome 打开同一盘点页 → 开始盘点 → 扫码图标 | Chrome 弹原生摄像头授权「想使用您的摄像头」→「仅此一次」→ 相机有画面（本分支代码）→ 点「关闭」相机关掉 |
| 4 | 读码 | **没做**：三星平放在桌上、对着椅子，视野里没有条码（用户正忙，没请人摆手机，主会话 11-06 定） |
| 5 | 清理 | ERP-TEST 对话里发的 3 条链接消息已删除（官方号对话的长按菜单没有「撤回」，只有「仅从此设备中删除」）；`adb reverse` 已撤掉 |

结论：**部分完成**。LINE 打开页面、真机相机启动通过；**LINE 原生相机授权（LIFF）与真机读码没测到**，原因如上。

## 上线后补全（随 Cowork 下一批部署后，测试机空闲时由主会话排）

1. 三星 LINE 打开 ERP-TEST → 富菜单「盘点」→ 经 LIFF 打开 testerp 的盘点页（`pearnly_liff=scan`）。
2. 打开一份进行中的盘点 →「开始盘点」→ 扫码图标 → LINE 弹原生授权「是否允许此网站访问您的相机?」→「仅允许本次」。
3. 测试机屏幕全屏放 EAN-13 码图，三星对准 → 读出号码、认出物料，光标停在「本次实盘数量」。
4. 不录数量，关闭弹窗；盘点记录不变、Error Log 不多出条目。补一份真机记录，写行 id、日期、SHA、结果。

## 截图

本机 `/Users/skin/pearnly-devtools/device-1106-adjust/`（`-s` 为缩小版）：p15 盘点页、p16 连续盘点弹窗、p17 LINE 相机权限提示、p21 Chrome 摄像头授权、p22 相机画面、
u2 长按菜单（无撤回）、u6 链接删除后。同一批缩小版也在主会话共享笔记「ERP物料调整-截图-11-06」（`device-` 开头）。
