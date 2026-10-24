# 共用件连带回归闸门（2026-10-25）

- 用户原话（主会话转）：「帮我把这个问题的解决方案纳入后续的开发流程，必要检测环节」——改共用件导致别处连带坏掉（修盘点的安卓扫码后其它入口扫码全部黑屏，没有窗口发现）。
- 分支 / 工作树：`pearnly/regression-safety-net` / `.claude/worktrees/regression-safety-net`，基线 `origin/main` = `d5b2c3c9`。不合入 main、不部署；交验收窗口独立审登记表、复跑演练，通过后报主会话定合入。

## 一、流程规则

- `docs/development/verification.md`：新增「共用件连带回归闸门」一节，选择矩阵加一行（命中登记的共用件 → 出影响清单 → 每个入口实测并记「通过 / 不通过 / 没测到（原因）」→ 才能合入；上线后线上测试租户跑只读冒烟子集；开发与验收各自出清单互相补漏）。
- `docs/development/workflow.md` 第 4、5 节接上；`AGENTS.md`「验证与审查」加一条指向该节。

## 二、工具

| 项 | 位置 | 进 CI |
|---|---|---|
| 入口登记表 | `scripts/impact/shared-parts.toml`：37 个共用件、117 个入口（ERP / Cowork / DMS / 控制面 / CC），以 `2026-10-24-erp-batch-regression-entries.md` 为底，三路只读摸底补全 | 由下一行校验 |
| 影响清单与漏登记检查 | `scripts/check-impact.py`（`--markdown` 出贴记录的表；按特征串反查全仓调用方，没登记就失败；登记表格式 / 入口引用 / 路径不对也失败；auto 入口必须有探针） | `tests/test_check_impact.py` + 每次推送跑一遍（`IMPACT_BASE` 取 PR 基线 / main 的上一提交） |
| 自动冒烟 | `scripts/smoke/`（`run.py` 按登记表驱动；`drive.mjs` 无头 Chrome + 假摄像头；探针 page / scan；容器内 `line_flow.py` 模拟 webhook、`express_flow.py` 写 C 盘读回；`session.py` 建删测试会话；`--target production --readonly` 线上测试租户只读子集） | 只在实验栈 / 线上测试租户跑 |
| 扫码共用件契约 | `scripts/smoke/selftest.py`：真 Chrome + 假摄像头测试条码，当前读码器必须有画面并读出 `2000000000008` | 是 |
| 页面报错回传 | `public/desk/errors.js`（Desk 第一个加载）→ `pearnly_core.desk.client_errors.report` → 本站 Error Log「Browser error」；同一条一小时一次、每人 30 / 每站 300 条每小时、去查询串 / 邮箱 / 长数字；只收已登录用户；零月费 | `tests/test_client_errors.py`、`tests/test-desk-errors.cjs` |

本机工具没收进仓库的：`~/pearnly-devtools/cam/`（旧探针、117 MB 的 y4m，已被 `make_barcode_video.py` 与 `probes/scan.js` 取代）、`~/pearnly-devtools/erp-sync-bg/lab4.py`（写 C 盘联调，已由 `express_flow.py` 取代其冒烟用途）。

## 三、证明安全网有效（演练）

一次性分支 `pearnly/drill-break-shared`（不推送，演练完删）：

| 演练 | check-impact | 自动检查 | 改回后 |
|---|---|---|---|
| 读码器不挂画面（`reader.js` 去掉 `video.srcObject = stream`） | 只命中 `desk-scan-core`，列出 15 个入口（14 个扫码入口 + 上传框拍照） | `selftest.py` 约 20 秒失败：画面 0×0、没读出码 | 通过（画面 640×480、读出 2000000000008） |
| `find_product` 永远找不到 | 命中 `erp-products-lookup`；**同时查出登记表漏了出库明细扫码、物料整页新建 / 详情条码三个入口**（它们也走 `api_find_product`），已补 | 实验栈冒烟待做（见下） | — |
| 新页面接了共用扫码但没登记 | 失败：「共用件 desk-scan-core 的调用方没登记：…/drill-new-scan.js」 | — | — |

实验栈部分（坏的源码 live 上去，`run.py --part desk-scan-core --part erp-products-lookup` 在这些入口报失败；改回后通过）：**测试机现在归验收窗口做线上补测，等主会话通知再做**。

已知限制：漏登记检查按「文件」粒度——调用方文件只要落在这个共用件的任一入口里就算登记，同一文件服务几个入口时漏列某个入口查不出来（上面 `find_product` 那次就是靠人看出来的）。所以验收窗口要按代码独立出清单对比，这一步不能省。

## 四、没做 / 后续

- Cowork 推送写 Express 还没有自动冒烟（入口标 manual）；LINE 真手机、付费识别、CC 桌面版原生界面按清单人工。
- 前端 SPA（`frontend/` 的 LIFF 页、CC 对话页）的报错回传没接：这些页面有游客 / 票据会话，接入要另定限频与防滥用；Desk 三个产品已覆盖。
- 未登记的共用件（调用方多、这一版没逐个登记）：`execution`（识别队列，75 个调用方）、`assistant`（CC 对话）、`tax_documents`、`transaction`、`provisioning`、`browser_text`、计费；后续改到它们时先补登记再改。
