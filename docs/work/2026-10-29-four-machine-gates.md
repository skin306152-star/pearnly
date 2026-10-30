# 四道机器闸（主会话 10-28 任务书，10-29 恢复施工）

> 验收窗口施工。分支：① `pearnly/swallowed-errors-gate`（已合入 main 956482ab、已删）；②③④ `pearnly/regression-baseline`。
> 主会话 10-29 定：流程套件不上 free-arm-2（生产镜像只有 amd64，ARM 上要 qemu，又慢又不一致还要 root 权限），在本机回归实验室跑、结果回写 GitHub 提交状态 `pearnly/flows`，部署闸认这个状态，没有绕过。

## ① 吞错闸门（已合入 main 956482ab）

- 规则：`scripts/check-swallowed-errors.py`。产品前端（apps 下 public / page / doctype JS、frontend/src、frontend/assistant/src）每个 catch 必须重抛或交统一出错回传（`pearnlyCaught` / `pearnlyReportError` / `reportCaught` / `withReport`）；空 catch、只 console、只给提示一律失败。预期的吞错登记在 `tests/baseline/swallowed_errors.toml`（文件 + 那一行特征 + 原因），登记不唯一、过期、没写原因都失败。
- 存量：298 个 catch，改 218 处接入统一回传，登记 89 条预期（浏览器存储、坏 JSON、相机能力探测、推送 / SW 能力、失败响应正文非 JSON、提示音、errors.js 自身）。请求失败（服务端已报错的）`pearnlyCaught` 不重复回传。
- 拦住了什么：线上 fcb65cca 的 ERP / Cowork `stocktake-entry.js`（10-28 手机盘点事故那段）分别在 258、211 行被拦；新加空 catch、只 console 的 catch、复制一条已登记的行都被拦。
- 全闸门 144 项通过；分支 CI（free-arm-2）success；快进合入 main 并回读远端 956482ab。

## ② 两套固定数据（`scripts/regression/datasets.py`）

| 数据集 | 站点 | 内容 |
| --- | --- | --- |
| `new`（照 polymer） | `erp-new.regression.localhost` | 一家公司、按正式方式接上 Express（`books.replace`）；Express 主档同步 7 个仓（01–07）；零库存；商品分别靠编码、条码（EAN-13）、二维码内容、内部编码、Express 同步认，再加一个按台管理（车） |
| `old`（照 mrerp） | `erp-old.regression.localhost` | 两家公司（A 两仓 + 库位清单，B 一仓）；5 件商品（合法 EAN-13）；采购入库有存量 585、销售（含作废）、收银班次销售 / 退货 / 交班、盘点（进行中、已过账） |

全部走正式接口建，重跑幂等；定义改了收敛到定义上（例：老数据早先的条码不是合法 EAN-13，假摄像头画不出来，重跑时按正式改商品接口收敛）。

## ③ 入口 → 流程用例

- 登记表 `tests/flows/registry.toml`；跑法 `scripts/flows/run.py`；覆盖闸 `scripts/flows/coverage.py`（CI 跑，改到产品入口文件没有流程登记 → 失败）；说明 `tests/flows/README.md`。
- 第一条流程 `erp.stocktake.scan`（10-28 polymer 手机盘点事故）：两套数据 × 电脑 / 手机宽度；手机宽度另用假摄像头放条码画面读码。每个码断言录入区出没出来、品名对不对、提示，保存后查 `ERP Stock Count Entry` 商品与数量，没有多余记录；页面无报错；站点 Error Log 没多出前端回传。
- 10-29 结果（实验室：线上同版镜像 fb1037ad，源码 main 8fd0fa4d）：

| 数据集 / 宽度 | 结果 | 耗时 |
| --- | --- | --- |
| new / 电脑 | 通过（6 种认法 + 按台商品被拒 + 未知码） | 97 秒 |
| new / 手机 + 假摄像头（8850001000019） | 通过 | 195 秒 |
| old / 电脑 | 通过 | 93 秒 |
| old / 手机 + 假摄像头（8850002000025） | 通过（首跑因老条码不是合法 EAN-13 没画出来，修数据集后重跑） | 269 秒 |

- 覆盖面：全仓产品入口文件 339 个，有流程的 4 个。覆盖闸只看「这次改到的」，所以今后改到哪个入口就要给它补流程；存量清单 `python3 scripts/flows/coverage.py --list`。
- 观察项（产品，未改）：盘点录入框查到下一件商品后，上一件的提示（如「此仓库未登记该商品的台」）仍留在框里。
- work-followups 的流程用例（`scripts/regression/flows/`，JSON 用例 + 自己的种子数据与实验室）还在它的工作树里未提交，等它提交后按本登记表适配并入，不另跑一套。

## ④ 部署闸（`scripts/production/deploy.sh`）

- 构建前（演练也查）核对要部署的 main 提交的 `pearnly/flows`：没有、不是 success、不是全套（说明不以 `all flows` 开头）都停下，打印补跑命令，生产没动；没有绕过开关。
- 状态说明格式：`<范围>; <流程数> flows; datasets <数据集>; <通过>/<格数> runs passed; <秒>s`。`run.py --post-status` 回写前核对实验室挂的就是本工作树源码、产品代码无未提交改动。
- 部署后回读（`deploy-readback.sh`）每个租户多列 `browser_errors_since`（部署后的前端回传，算进 Error Log、非 0 即问题）和 `browser_errors_24h_before`（对照）。
- 离线测试 `scripts/production/test-deploy-run.py` 23 项通过（新增：没有结果 / 没通过 / 只跑了受影响流程三种拦下且不触发构建；演练同样拦；回读按租户列出前端回传）。

## 限制

- 依赖 / Dockerfile / 底座版本变了时实时源码盖不到，要用候选镜像起实验室再跑（README 写了）。
- 流程只有一条；339 个入口里 335 个还没有流程，靠覆盖闸逐步补。
- 实验室 bench 3 GB 内存，跑大批角色时 OOM 过一次（10-28），重启后重跑受影响部分。

## 覆盖现状与补流程顺序（10-29）

生成：`python3 scripts/flows/coverage.py --status --days 30`（main a7d15486；改动次数按 main 历史数提交，不含合并提交；仓库历史从 09-17 起，所以「全部」约是 6 周）。
入口文件 340 个：有流程 5 个，登记不需要 1 个（控制面开户单 `issues.py`），没有流程 334 个。

| 产品 | 入口文件 | 有流程 | 不需要 | 没有流程 |
|---|---|---|---|---|
| ERP | 72 | 2 | 0 | 70 |
| Cowork | 52 | 0 | 0 | 52 |
| DMS | 16 | 0 | 0 | 16 |
| 控制面 | 21 | 0 | 1 | 20 |
| 共用层 | 50 | 3 | 0 | 47 |
| CC 对话页 | 99 | 0 | 0 | 99 |
| 共用单页 | 30 | 0 | 0 | 30 |

### 最该先补流程的 20 个入口

排序：以近 30 天改动次数为主；两处调整——① 手机盘点页 `www/stocktake.html` 因 10-29 空仓保存 404 事故提前进前 20；
② 纯词条表与通用小组件（CC 的 `i18n.ts` 69 次、`ui/drag.tsx` 26 次、`tool-uis/steps.tsx` 22 次、`attachment.tsx` 20 次）不单列，
由它们所在页面的流程一起登记进 `covers`（它们出错只会在那些页面上表现出来）。「建议流程」一列把 20 个入口归成 10 条流程，一条流程补上就覆盖一组。

| # | 入口 | 近 30 天 | 全部 | 建议流程 | 说明 |
|---|---|---|---|---|---|
| 1 | `frontend/assistant/src/assistant.tsx` | 78 | 78 | `cc.chat` | CC 对话页主体：新建对话、发消息、切会话、刷新后还在 |
| 2 | `frontend/assistant/src/components/assistant-ui/thread.tsx` | 55 | 55 | `cc.chat` | 同上（消息流、附件、工具步骤） |
| 3 | `frontend/assistant/src/components/assistant-ui/thread-list.tsx` | 54 | 54 | `cc.chat` | 会话列表、置顶、拖动排序 |
| 4 | `frontend/assistant/src/components/settings.tsx` | 50 | 50 | `cc.settings` | 设置改一项、刷新后保留 |
| 5 | `frontend/assistant/src/components/scheduled-tasks.tsx` | 38 | 38 | `cc.scheduled-tasks` | 建定时任务、改、删，落库对上 |
| 6 | `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/transactions.js` | 36 | 47 | `erp.express.popup` | 单据页 Express 弹窗（10-29 改 POST；C 盘窗口正把复核脚本改成登记用例） |
| 7 | `frontend/assistant/src/components/pending.tsx` | 35 | 35 | `cc.pending` | 待选账套：列出、选定、记住 |
| 8 | `frontend/assistant/src/components/assistant-ui/projects.tsx` | 32 | 32 | `cc.projects` | 项目建、改名、归入对话 |
| 9 | `apps/pearnly_cowork/pearnly_cowork/public/desk/recon.js` | 30 | 40 | `cowork.recon.bank` | 银行对账：上传对账单、出结果、改一笔、导出 |
| 10 | `frontend/assistant/src/components/company-page.tsx` | 28 | 28 | `cc.companies` | 公司页：资料、账套、月份 |
| 11 | `frontend/assistant/src/lib/desktop.ts` | 28 | 28 | `cc.companies` | CC 桌面版连接状态的读取与显示（实验室里按「未连接」断言） |
| 12 | `apps/pearnly_cowork/pearnly_cowork/public/desk/intake-folders.js` | 26 | 26 | `cowork.intake.folders` | 文件夹录入规则：建、改、立即跑一次 |
| 13 | `frontend/assistant/src/components/company-cards.tsx` | 25 | 25 | `cc.companies` | 公司卡片、记住的选择 |
| 14 | `apps/pearnly_cowork/pearnly_cowork/public/desk/intake-upload.js` | 24 | 39 | `cowork.intake.upload` | 上传录入（识别要打桩，不调付费识别） |
| 15 | `frontend/assistant/src/lib/companies.ts` | 23 | 23 | `cc.companies` | 公司数据读写 |
| 16 | `apps/pearnly_erp_portal/pearnly_erp_portal/express/api.py` | 22 | 22 | `erp.express.popup` | Express 写账状态、转人工、标已处理 |
| 17 | `apps/pearnly_core/pearnly_core/assistant/api.py` | 21 | 21 | `cc.chat` | CC 对话接口（会话、消息、推送） |
| 18 | `apps/pearnly_core/pearnly_core/public/desk/product.js` | 20 | 52 | `erp.stocktake.scan` 起 | 共用外壳 `p.call` / 提示 / 对话框：任一 Desk 流程都经过，先登记进现有盘点流程 |
| 19 | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` | 20 | 20 | `cc.pending` | 待选账套接口（10-29 改只读） |
| 20 | `apps/pearnly_erp_portal/pearnly_erp_portal/www/stocktake.html` | 15 | 20 | `erp.line.stocktake.phone` | LINE 手机盘点页：空仓保存、抬头计数（10-29 事故） |

紧接着的下一批（近 30 天 ≥ 13 次）：控制面开户 `provisioning.py`（19，对外写身份服务，先评估能否在实验室跑，不能就登记不需要并写原因）、
`www/ai.html`（18，fatcc.com 智能体入口登录）、Cowork `folder_intake/machine.py`（18，并进 `cowork.intake.folders`）、ERP 采购 / 销售单页
`purchase-form.js`（17）/ `sales-form.js`（14）、对账 `sheets.py`（15，并进 `cowork.recon.bank`）、ERP `company.js`（14）、`products.js`（13）。

限制：

- CC 对话页的 6 条流程要在实验室里给智能体后端打桩（不调付费模型、不连 CC 桌面版），`cc.chat` 断言的是会话与消息落库、列表与排序，不是模型回答内容。
- 改动次数只说明「常改、容易回归」，不等于风险；事故相关的入口（如第 20 位）按事故提前，以后有新事故同样处理。
- 这份清单是 10-29 的快照；补流程时以 `coverage.py --status` 现跑的结果为准。

### 现状清单（按产品，近 30 天改动次数倒序）

<details><summary>ERP（72 个，近 30 天改动次数倒序）</summary>

| 入口 | 近 30 天 | 全部 | 流程 |
|---|---|---|---|
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/transactions.js` | 36 | 47 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/express/api.py` | 22 | 22 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/purchase-form.js` | 17 | 33 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/www/stocktake.html` | 15 | 20 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/company.js` | 14 | 28 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/sales-form.js` | 14 | 27 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/stocktake-entry.js` | 14 | 24 | erp.stocktake.scan |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/products.js` | 13 | 43 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/stocktake.js` | 11 | 49 | erp.stocktake.scan |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/stock-entry.js` | 10 | 24 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/warehouses.js` | 10 | 21 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/products/service.py` | 9 | 27 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/scan.js` | 9 | 17 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/masters/selection.py` | 9 | 11 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/api.py` | 8 | 28 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/sales.js` | 7 | 22 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/team.js` | 7 | 22 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos-receipts.js` | 7 | 14 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/line_mobile.py` | 7 | 11 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/serials.py` | 7 | 7 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/purchase.js` | 6 | 26 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` | 6 | 15 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/purchase-fields.js` | 5 | 18 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/inventory.js` | 5 | 16 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/clients.js` | 5 | 14 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/suppliers.js` | 5 | 14 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/sales/api.py` | 5 | 13 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/sales-fields.js` | 4 | 14 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/entry-choice.js` | 4 | 9 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos-shifts.js` | 4 | 9 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/pearnly-pos-adapter.js` | 3 | 25 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/stock-card.js` | 3 | 18 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/warehouses.py` | 3 | 12 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/cashier/api.py` | 3 | 10 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/purchase-entry.js` | 3 | 7 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/sales-entry.js` | 3 | 6 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/erp-originals.js` | 3 | 3 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/work.js` | 3 | 3 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/company-menu.js` | 2 | 5 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/pearnly-pos-receipt.js` | 2 | 4 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/stock-opening.js` | 2 | 3 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/locations.py` | 2 | 2 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/serials.js` | 2 | 2 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/work/api.py` | 2 | 2 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/masters/api.py` | 1 | 8 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/line-binding.js` | 1 | 6 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/page/pearnly_tasks/pearnly_tasks.js` | 1 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/cashier.js` | 0 | 30 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/team/api.py` | 0 | 6 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` | 0 | 5 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/pearnly-pos-shift.js` | 0 | 5 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/purchase-viewer.js` | 0 | 3 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/official-pos_item_selector.js` | 0 | 2 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/purchase-layout.js` | 0 | 2 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/sales-viewer.js` | 0 | 2 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/user-line.js` | 0 | 2 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/page/pearnly_clients/pearnly_clients.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/page/pearnly_pos_receipts/pearnly_pos_receipts.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/page/pearnly_pos_shifts/pearnly_pos_shifts.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/page/pearnly_purchase/pearnly_purchase.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/page/pearnly_sales/pearnly_sales.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/page/pearnly_suppliers/pearnly_suppliers.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/official-point_of_sale.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/official-pos_controller.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/official-pos_item_cart.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/official-pos_item_details.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/official-pos_number_pad.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/official-pos_past_order_list.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/official-pos_past_order_summary.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/official-pos_payment.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/onscan.js` | 0 | 1 | 没有 |
| `apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/sales-layout.js` | 0 | 1 | 没有 |

</details>

<details><summary>Cowork（52 个，近 30 天改动次数倒序）</summary>

| 入口 | 近 30 天 | 全部 | 流程 |
|---|---|---|---|
| `apps/pearnly_cowork/pearnly_cowork/public/desk/recon.js` | 30 | 40 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/public/desk/intake-folders.js` | 26 | 26 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/public/desk/intake-upload.js` | 24 | 39 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` | 20 | 20 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/machine.py` | 18 | 18 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/recon_center/sheets.py` | 15 | 25 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` | 14 | 18 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/public/desk/targets.js` | 11 | 19 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/public/desk/intake.js` | 11 | 17 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/companion/api.py` | 11 | 15 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/history/api.py` | 10 | 16 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/public/desk/stocktake-entry.js` | 9 | 13 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/public/desk/stocktake.js` | 9 | 12 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/companion/heartbeat.py` | 9 | 10 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/connections/api.py` | 6 | 11 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/recon_center/api.py` | 6 | 9 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/api.py` | 6 | 6 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/public/desk/work.js` | 5 | 19 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/push/application/api.py` | 5 | 12 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/public/desk/team.js` | 5 | 6 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/companies/api.py` | 5 | 5 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/companion/device_api.py` | 5 | 5 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/ocr/remote/callback.py` | 5 | 5 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/files/api.py` | 4 | 6 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/companion/request_api.py` | 4 | 4 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/public/desk/intake-choices.js` | 3 | 7 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/companion/queue_api.py` | 3 | 4 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/stocktake/desk_api.py` | 3 | 4 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/recon_center/presets.py` | 3 | 3 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/work/api.py` | 2 | 8 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/companion/submission.py` | 2 | 4 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/public/desk/post-file.js` | 2 | 4 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/masters/api.py` | 1 | 7 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/reconciliation/bank_recon_api.py` | 1 | 7 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/reconciliation/income_recon_api.py` | 1 | 7 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/stocktake/api.py` | 1 | 5 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/public/desk/line-binding.js` | 1 | 4 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/files/guard.py` | 1 | 2 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` | 0 | 5 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/intake.py` | 0 | 2 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/history/category_api.py` | 0 | 1 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/line/api.py` | 0 | 1 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/masters/workspace_api.py` | 0 | 1 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/page/pearnly_intake/pearnly_intake.js` | 0 | 1 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/page/pearnly_recon_bank/pearnly_recon_bank.js` | 0 | 1 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/page/pearnly_recon_income/pearnly_recon_income.js` | 0 | 1 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/page/pearnly_recon_vat/pearnly_recon_vat.js` | 0 | 1 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/page/pearnly_work/pearnly_work.js` | 0 | 1 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/public/desk/choice.js` | 0 | 1 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/public/desk/user-line.js` | 0 | 1 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/roundtrip/api.py` | 0 | 1 | 没有 |
| `apps/pearnly_cowork/pearnly_cowork/team/api.py` | 0 | 1 | 没有 |

</details>

<details><summary>DMS（16 个，近 30 天改动次数倒序）</summary>

| 入口 | 近 30 天 | 全部 | 流程 |
|---|---|---|---|
| `apps/pearnly_dms/pearnly_dms/www/liff/dms-booking.html` | 5 | 9 | 没有 |
| `apps/pearnly_dms/pearnly_dms/public/desk/connect.js` | 4 | 21 | 没有 |
| `apps/pearnly_dms/pearnly_dms/connections/api.py` | 3 | 12 | 没有 |
| `apps/pearnly_dms/pearnly_dms/operators/roster.py` | 3 | 9 | 没有 |
| `apps/pearnly_dms/pearnly_dms/account/api.py` | 3 | 8 | 没有 |
| `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` | 3 | 7 | 没有 |
| `apps/pearnly_dms/pearnly_dms/public/desk/roster.js` | 3 | 7 | 没有 |
| `apps/pearnly_dms/pearnly_dms/public/desk/records.js` | 3 | 6 | 没有 |
| `apps/pearnly_dms/pearnly_dms/ocr/ocr_id_card.py` | 3 | 5 | 没有 |
| `apps/pearnly_dms/pearnly_dms/query/records.py` | 2 | 4 | 没有 |
| `apps/pearnly_dms/pearnly_dms/connections/snapshots.py` | 0 | 7 | 没有 |
| `apps/pearnly_dms/pearnly_dms/intake/id_card.py` | 0 | 1 | 没有 |
| `apps/pearnly_dms/pearnly_dms/pearnly_dms/page/pearnly_connect/pearnly_connect.js` | 0 | 1 | 没有 |
| `apps/pearnly_dms/pearnly_dms/pearnly_dms/page/pearnly_records/pearnly_records.js` | 0 | 1 | 没有 |
| `apps/pearnly_dms/pearnly_dms/pearnly_dms/page/pearnly_roster/pearnly_roster.js` | 0 | 1 | 没有 |
| `apps/pearnly_dms/pearnly_dms/records/manage.py` | 0 | 1 | 没有 |

</details>

<details><summary>控制面（21 个，近 30 天改动次数倒序）</summary>

| 入口 | 近 30 天 | 全部 | 流程 |
|---|---|---|---|
| `apps/pearnly_control/pearnly_control/provisioning.py` | 19 | 43 | 没有 |
| `apps/pearnly_control/pearnly_control/www/ai.html` | 18 | 18 | 没有 |
| `apps/pearnly_control/pearnly_control/issues.py` | 8 | 21 | 不需要（已登记） |
| `apps/pearnly_control/pearnly_control/billing_control.py` | 4 | 9 | 没有 |
| `apps/pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_issue/pearnly_issue.js` | 3 | 16 | 没有 |
| `apps/pearnly_control/pearnly_control/billing_api.py` | 2 | 11 | 没有 |
| `apps/pearnly_control/pearnly_control/teardown.py` | 2 | 8 | 没有 |
| `apps/pearnly_control/pearnly_control/line_admin.py` | 2 | 4 | 没有 |
| `apps/pearnly_control/pearnly_control/assistant_entry/billing.py` | 2 | 2 | 没有 |
| `apps/pearnly_control/pearnly_control/pearnly_control/page/pearnly_line_guide/pearnly_line_guide.js` | 2 | 2 | 没有 |
| `apps/pearnly_control/pearnly_control/www/pearnly-issue.html` | 1 | 7 | 没有 |
| `apps/pearnly_control/pearnly_control/account/member_api.py` | 1 | 4 | 没有 |
| `apps/pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_topup_request/pearnly_topup_request.js` | 1 | 4 | 没有 |
| `apps/pearnly_control/pearnly_control/assistant_entry/api.py` | 1 | 1 | 没有 |
| `apps/pearnly_control/pearnly_control/assistant_entry/webhook.py` | 1 | 1 | 没有 |
| `apps/pearnly_control/pearnly_control/onboarding/line_guide.py` | 1 | 1 | 没有 |
| `apps/pearnly_control/pearnly_control/onboarding/welcome.py` | 0 | 3 | 没有 |
| `apps/pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_tenant/pearnly_tenant.js` | 0 | 2 | 没有 |
| `apps/pearnly_control/pearnly_control/onboarding/targets.py` | 0 | 1 | 没有 |
| `apps/pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_deployment/pearnly_deployment.js` | 0 | 1 | 没有 |
| `apps/pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_feature/pearnly_feature_list.js` | 0 | 1 | 没有 |

</details>

<details><summary>共用层（50 个，近 30 天改动次数倒序）</summary>

| 入口 | 近 30 天 | 全部 | 流程 |
|---|---|---|---|
| `apps/pearnly_core/pearnly_core/assistant/api.py` | 21 | 21 | 没有 |
| `apps/pearnly_core/pearnly_core/public/desk/product.js` | 20 | 52 | 没有 |
| `apps/pearnly_core/pearnly_core/public/scan/reader.js` | 12 | 12 | erp.stocktake.scan |
| `apps/pearnly_core/pearnly_core/public/desk/scan.js` | 11 | 13 | erp.stocktake.scan |
| `apps/pearnly_core/pearnly_core/www/assistant.html` | 11 | 11 | 没有 |
| `apps/pearnly_core/pearnly_core/identity.py` | 10 | 18 | 没有 |
| `apps/pearnly_core/pearnly_core/public/desk/list.js` | 7 | 21 | 没有 |
| `apps/pearnly_core/pearnly_core/ocr.py` | 7 | 19 | 没有 |
| `apps/pearnly_core/pearnly_core/public/desk/home.js` | 7 | 13 | 没有 |
| `apps/pearnly_core/pearnly_core/line.py` | 6 | 22 | 没有 |
| `apps/pearnly_core/pearnly_core/desk/navigation.py` | 6 | 20 | 没有 |
| `apps/pearnly_core/pearnly_core/public/desk/actions.js` | 6 | 6 | 没有 |
| `apps/pearnly_core/pearnly_core/public/desk/errors.js` | 5 | 5 | 没有 |
| `apps/pearnly_core/pearnly_core/www/pearnly-app.html` | 3 | 8 | 没有 |
| `apps/pearnly_core/pearnly_core/public/desk/user.js` | 2 | 10 | 没有 |
| `apps/pearnly_core/pearnly_core/public/desk/select.js` | 2 | 6 | 没有 |
| `apps/pearnly_core/pearnly_core/account/line_login.py` | 2 | 5 | 没有 |
| `apps/pearnly_core/pearnly_core/shell.py` | 2 | 5 | erp.stocktake.scan |
| `apps/pearnly_core/pearnly_core/account/preferences.py` | 2 | 4 | 没有 |
| `apps/pearnly_core/pearnly_core/public/desk/viewer.js` | 2 | 3 | 没有 |
| `apps/pearnly_core/pearnly_core/assistant/webhook.py` | 2 | 2 | 没有 |
| `apps/pearnly_core/pearnly_core/cc_desktop/machine.py` | 2 | 2 | 没有 |
| `apps/pearnly_core/pearnly_core/desk/client_errors.py` | 2 | 2 | 没有 |
| `apps/pearnly_core/pearnly_core/browser_text.py` | 1 | 12 | 没有 |
| `apps/pearnly_core/pearnly_core/billing_www.py` | 1 | 10 | 没有 |
| `apps/pearnly_core/pearnly_core/public/desk/link.js` | 1 | 5 | 没有 |
| `apps/pearnly_core/pearnly_core/www/pearnly-password.html` | 1 | 3 | 没有 |
| `apps/pearnly_core/pearnly_core/cc_desktop/api.py` | 1 | 1 | 没有 |
| `apps/pearnly_core/pearnly_core/exports/api.py` | 1 | 1 | 没有 |
| `apps/pearnly_core/pearnly_core/password.py` | 0 | 5 | 没有 |
| `apps/pearnly_core/pearnly_core/sessions.py` | 0 | 4 | 没有 |
| `apps/pearnly_core/pearnly_core/manifest.py` | 0 | 3 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_stock_in/pearnly_stock_in.js` | 0 | 3 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_stock_out/pearnly_stock_out.js` | 0 | 3 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_company/pearnly_company.js` | 0 | 2 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_inventory/pearnly_inventory.js` | 0 | 2 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_products/pearnly_products.js` | 0 | 2 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_stock_card/pearnly_stock_card.js` | 0 | 2 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_stocktake/pearnly_stocktake.js` | 0 | 2 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_team/pearnly_team.js` | 0 | 2 | 没有 |
| `apps/pearnly_core/pearnly_core/public/desk/sidebar.js` | 0 | 2 | 没有 |
| `apps/pearnly_core/pearnly_core/payments/proof.py` | 0 | 1 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_cashier/pearnly_cashier.js` | 0 | 1 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_guide/pearnly_guide.js` | 0 | 1 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_history/pearnly_history.js` | 0 | 1 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_home/pearnly_home.js` | 0 | 1 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_push_logs/pearnly_push_logs.js` | 0 | 1 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_reconcile/pearnly_reconcile.js` | 0 | 1 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_stock_count/pearnly_stock_count.js` | 0 | 1 | 没有 |
| `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_warehouses/pearnly_warehouses.js` | 0 | 1 | 没有 |

</details>

<details><summary>CC 对话页（99 个，近 30 天改动次数倒序）</summary>

| 入口 | 近 30 天 | 全部 | 流程 |
|---|---|---|---|
| `frontend/assistant/src/assistant.tsx` | 78 | 78 | 没有 |
| `frontend/assistant/src/i18n.ts` | 69 | 69 | 没有 |
| `frontend/assistant/src/components/assistant-ui/thread.tsx` | 55 | 55 | 没有 |
| `frontend/assistant/src/components/assistant-ui/thread-list.tsx` | 54 | 54 | 没有 |
| `frontend/assistant/src/components/settings.tsx` | 50 | 50 | 没有 |
| `frontend/assistant/src/components/scheduled-tasks.tsx` | 38 | 38 | 没有 |
| `frontend/assistant/src/components/pending.tsx` | 35 | 35 | 没有 |
| `frontend/assistant/src/components/assistant-ui/projects.tsx` | 32 | 32 | 没有 |
| `frontend/assistant/src/components/company-page.tsx` | 28 | 28 | 没有 |
| `frontend/assistant/src/lib/desktop.ts` | 28 | 28 | 没有 |
| `frontend/assistant/src/components/ui/drag.tsx` | 26 | 26 | 没有 |
| `frontend/assistant/src/components/company-cards.tsx` | 25 | 25 | 没有 |
| `frontend/assistant/src/lib/companies.ts` | 23 | 23 | 没有 |
| `frontend/assistant/src/components/tool-uis/steps.tsx` | 22 | 22 | 没有 |
| `frontend/assistant/src/components/assistant-ui/attachment.tsx` | 20 | 20 | 没有 |
| `frontend/assistant/src/components/results-panel.tsx` | 19 | 19 | 没有 |
| `frontend/assistant/src/lib/managed-agents/runtime-provider.tsx` | 19 | 19 | 没有 |
| `frontend/assistant/src/components/add-company.tsx` | 17 | 17 | 没有 |
| `frontend/assistant/src/lib/managed-agents/api.ts` | 17 | 17 | 没有 |
| `frontend/assistant/src/lib/managed-agents/session-controller.ts` | 17 | 17 | 没有 |
| `frontend/assistant/src/lib/managed-agents/reducer.ts` | 16 | 16 | 没有 |
| `frontend/assistant/src/components/month-ledger.tsx` | 15 | 15 | 没有 |
| `frontend/assistant/src/lib/managed-agents/session-list-adapter.ts` | 15 | 15 | 没有 |
| `frontend/assistant/src/components/session-panel.tsx` | 14 | 14 | 没有 |
| `frontend/assistant/src/lib/managed-agents/attachments.ts` | 14 | 14 | 没有 |
| `frontend/assistant/src/components/assistant-ui/sidebar-group.tsx` | 13 | 13 | 没有 |
| `frontend/assistant/src/lib/company-panel.ts` | 13 | 13 | 没有 |
| `frontend/assistant/src/main.tsx` | 13 | 13 | 没有 |
| `frontend/assistant/src/components/companies.tsx` | 12 | 12 | 没有 |
| `frontend/assistant/src/components/period-chip.tsx` | 12 | 12 | 没有 |
| `frontend/assistant/src/components/ui/sidebar.tsx` | 12 | 12 | 没有 |
| `frontend/assistant/src/lib/managed-agents/poll-source.ts` | 12 | 12 | 没有 |
| `frontend/assistant/src/lib/managed-agents/sidebar-groups.ts` | 12 | 12 | 没有 |
| `frontend/assistant/src/lib/managed-agents/sidebar-order.ts` | 12 | 12 | 没有 |
| `frontend/assistant/src/lib/profile.ts` | 12 | 12 | 没有 |
| `frontend/assistant/src/components/assistant-ui/markdown-text.tsx` | 11 | 11 | 没有 |
| `frontend/assistant/src/components/tool-uis/approval-bar.tsx` | 11 | 11 | 没有 |
| `frontend/assistant/src/lib/frappe.ts` | 11 | 11 | 没有 |
| `frontend/assistant/src/lib/layers.ts` | 11 | 11 | 没有 |
| `frontend/assistant/src/lib/managed-agents/activity.ts` | 11 | 11 | 没有 |
| `frontend/assistant/src/components/delete-chats.tsx` | 10 | 10 | 没有 |
| `frontend/assistant/src/components/ui/dialog.tsx` | 10 | 10 | 没有 |
| `frontend/assistant/src/lib/managed-agents/realtime.ts` | 10 | 10 | 没有 |
| `frontend/assistant/src/components/mobile-shell.tsx` | 9 | 9 | 没有 |
| `frontend/assistant/src/lib/managed-agents/events.ts` | 9 | 9 | 没有 |
| `frontend/assistant/src/lib/managed-agents/list-sync.ts` | 9 | 9 | 没有 |
| `frontend/assistant/src/components/install-prompt.tsx` | 8 | 8 | 没有 |
| `frontend/assistant/src/components/ui/menu.tsx` | 8 | 8 | 没有 |
| `frontend/assistant/src/hooks/use-long-press.ts` | 8 | 8 | 没有 |
| `frontend/assistant/src/lib/managed-agents/projects.ts` | 8 | 8 | 没有 |
| `frontend/assistant/src/lib/managed-agents/sidebar-view.ts` | 8 | 8 | 没有 |
| `frontend/assistant/src/lib/month.ts` | 8 | 8 | 没有 |
| `frontend/assistant/src/lib/sidebar-keys.ts` | 8 | 8 | 没有 |
| `frontend/assistant/src/lib/follow-scroll.ts` | 7 | 7 | 没有 |
| `frontend/assistant/src/lib/managed-agents/send-failure.ts` | 7 | 7 | 没有 |
| `frontend/assistant/src/lib/prefs-sync.ts` | 7 | 7 | 没有 |
| `frontend/assistant/src/lib/step-summary.ts` | 7 | 7 | 没有 |
| `frontend/assistant/src/components/ui/tooltip.tsx` | 6 | 6 | 没有 |
| `frontend/assistant/src/lib/managed-agents/sidebar-drag.ts` | 6 | 6 | 没有 |
| `frontend/assistant/src/lib/tool-labels.ts` | 6 | 6 | 没有 |
| `frontend/assistant/src/components/error-boundary.tsx` | 5 | 5 | 没有 |
| `frontend/assistant/src/lib/approval-card.ts` | 5 | 5 | 没有 |
| `frontend/assistant/src/lib/hover-scroll.ts` | 5 | 5 | 没有 |
| `frontend/assistant/src/lib/nav.ts` | 5 | 5 | 没有 |
| `frontend/assistant/src/lib/push.ts` | 5 | 5 | 没有 |
| `frontend/assistant/src/components/document-view.tsx` | 4 | 4 | 没有 |
| `frontend/assistant/src/components/shortcut.tsx` | 4 | 4 | 没有 |
| `frontend/assistant/src/hooks/use-mobile.ts` | 4 | 4 | 没有 |
| `frontend/assistant/src/lib/install.ts` | 4 | 4 | 没有 |
| `frontend/assistant/src/lib/managed-agents/pending-send.ts` | 4 | 4 | 没有 |
| `frontend/assistant/src/lib/managed-agents/turn.ts` | 4 | 4 | 没有 |
| `frontend/assistant/src/lib/step-detail.ts` | 4 | 4 | 没有 |
| `frontend/assistant/src/components/assistant-ui/tooltip-icon-button.tsx` | 3 | 3 | 没有 |
| `frontend/assistant/src/components/mascot.tsx` | 3 | 3 | 没有 |
| `frontend/assistant/src/components/split-handle.tsx` | 3 | 3 | 没有 |
| `frontend/assistant/src/components/typing-dots.tsx` | 3 | 3 | 没有 |
| `frontend/assistant/src/components/ui/marquee.tsx` | 3 | 3 | 没有 |
| `frontend/assistant/src/components/ui/skeleton.tsx` | 3 | 3 | 没有 |
| `frontend/assistant/src/lib/notify.ts` | 3 | 3 | 没有 |
| `frontend/assistant/src/lib/overflow-fade.ts` | 3 | 3 | 没有 |
| `frontend/assistant/src/lib/step-subject.ts` | 3 | 3 | 没有 |
| `frontend/assistant/src/lib/utils.ts` | 3 | 3 | 没有 |
| `frontend/assistant/src/lib/variant.ts` | 3 | 3 | 没有 |
| `frontend/assistant/src/lib/viewport.ts` | 3 | 3 | 没有 |
| `frontend/assistant/src/components/tool-uis/approval-sheet.tsx` | 2 | 2 | 没有 |
| `frontend/assistant/src/components/tool-uis/index.tsx` | 2 | 2 | 没有 |
| `frontend/assistant/src/lib/appearance.ts` | 2 | 2 | 没有 |
| `frontend/assistant/src/lib/back-stack.ts` | 2 | 2 | 没有 |
| `frontend/assistant/src/lib/css-var.ts` | 2 | 2 | 没有 |
| `frontend/assistant/src/lib/focus-return.ts` | 2 | 2 | 没有 |
| `frontend/assistant/src/lib/live.ts` | 2 | 2 | 没有 |
| `frontend/assistant/src/lib/managed-agents/notice-reload.ts` | 2 | 2 | 没有 |
| `frontend/assistant/src/lib/managed-agents/sidebar-fold.ts` | 2 | 2 | 没有 |
| `frontend/assistant/src/lib/touch.ts` | 2 | 2 | 没有 |
| `frontend/assistant/src/lib/caught.ts` | 1 | 1 | 没有 |
| `frontend/assistant/src/lib/ime.ts` | 1 | 1 | 没有 |
| `frontend/assistant/src/lib/managed-agents/new-chat-send.ts` | 1 | 1 | 没有 |
| `frontend/assistant/src/lib/managed-agents/stop-run.ts` | 1 | 1 | 没有 |
| `frontend/assistant/src/lib/sidebar-focus.ts` | 1 | 1 | 没有 |

</details>

<details><summary>共用单页（30 个，近 30 天改动次数倒序）</summary>

| 入口 | 近 30 天 | 全部 | 流程 |
|---|---|---|---|
| `frontend/src/pages/dms/ScanPage.vue` | 4 | 4 | 没有 |
| `frontend/src/pages/dms/EditorPage.vue` | 3 | 14 | 没有 |
| `frontend/src/lib/dms-editor.ts` | 3 | 7 | 没有 |
| `frontend/src/pages/dms/AccountPage.vue` | 2 | 8 | 没有 |
| `frontend/src/lib/id-scan.ts` | 2 | 2 | 没有 |
| `frontend/src/pages/dms/ReviewPage.vue` | 2 | 2 | 没有 |
| `frontend/src/router.ts` | 1 | 9 | 没有 |
| `frontend/src/pages/dms/RosterPage.vue` | 1 | 8 | 没有 |
| `frontend/src/lib/nav.ts` | 1 | 7 | 没有 |
| `frontend/src/pages/HomePage.vue` | 1 | 7 | 没有 |
| `frontend/src/components/AccountMenu.vue` | 1 | 5 | 没有 |
| `frontend/src/lib/icons.ts` | 1 | 5 | 没有 |
| `frontend/src/main.ts` | 1 | 5 | 没有 |
| `frontend/src/pages/NotBuiltPage.vue` | 1 | 5 | 没有 |
| `frontend/src/pages/dms/ConnectPage.vue` | 1 | 5 | 没有 |
| `frontend/src/pages/purchase/PurchasePage.vue` | 1 | 5 | 没有 |
| `frontend/src/components/ChangePasswordDialog.vue` | 1 | 4 | 没有 |
| `frontend/src/components/ShellBootCard.vue` | 1 | 4 | 没有 |
| `frontend/src/pages/cowork/IntakePage.vue` | 1 | 4 | 没有 |
| `frontend/src/lib/liff.ts` | 1 | 2 | 没有 |
| `frontend/src/lib/caught.ts` | 1 | 1 | 没有 |
| `frontend/src/App.vue` | 0 | 9 | 没有 |
| `frontend/src/lib/i18n.ts` | 0 | 4 | 没有 |
| `frontend/src/components/LineLayout.vue` | 0 | 2 | 没有 |
| `frontend/src/lib/api.ts` | 0 | 2 | 没有 |
| `frontend/src/lib/shell.ts` | 0 | 2 | 没有 |
| `frontend/src/types/frappe-ui.ts` | 0 | 2 | 没有 |
| `frontend/src/composables/useBoot.ts` | 0 | 1 | 没有 |
| `frontend/src/lib/boot-context.ts` | 0 | 1 | 没有 |
| `frontend/src/types/frappe-ui-vite.ts` | 0 | 1 | 没有 |

</details>

## 覆盖闸扩到「入口背后的业务模块」（10-29，先出清单、未开拦截）

起因：get-writes-scanner-callables 只改了 Cowork `work/boards.py`、`work/service.py`（不带 `@frappe.whitelist`，不算入口），
覆盖闸说「没改到入口文件」——按旧规则没错，但改的正是任务管理的行为。主会话定：入口模块在同一 App 里 import 的模块，追两层，改到了也要有流程。

做法（`scripts/flows/coverage.py`）：用 `ast` 建 apps 下的 import 图（只算同一 App 内、不含测试和补丁），从每个入口往下追两层；
改到这类模块时，流程直接 `covers` 它、或 `covers` 了两层之内导入它的入口，都算覆盖。`--behind` 出下面的清单。
现在 `BEHIND_ENFORCED = False`：改到没有流程的这类模块时 CI 只打印提醒、不判失败；主会话定了基线处理方式再打开。

近 60 个 main 提交里改到入口背后模块的 6 个，按新规则会被拦 5 个（按原入口规则只拦 1 个），被拦的模块 Cowork 6、DMS 7、ERP 3、控制面 1 次。

入口背后的模块 515 个：有流程 143 个，没有 372 个

| 产品 | 入口背后的模块 | 有流程 | 没有流程 |
|---|---|---|---|
| ERP | 98 | 41 | 57 |
| Cowork | 250 | 72 | 178 |
| DMS | 72 | 0 | 72 |
| 控制面 | 26 | 0 | 26 |
| 共用层 | 69 | 30 | 39 |

<details><summary>ERP（没有流程 57 个）</summary>

| 模块 | 经哪个入口导入（举一个） |
|---|---|
| `apps/pearnly_erp_portal/pearnly_erp_portal/cashier/access.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/cashier/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/cashier/counter.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/cashier/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/cashier/sales.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/cashier/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/cashier/shifts.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/cashier/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/__init__.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/binding.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/intake.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/line_cards.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/line_flow.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/line_menu.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/access.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/attachments.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/opening_batch.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/opening_workbook.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/openings.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/report.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/workbook.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/masters/customers.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/masters/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/numbering/__init__.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/cashier/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/numbering/contracts.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/numbering/service.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/cashier/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/products/purchase.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/products/sales.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/products/workbook.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/products/service.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/access.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/category_memory.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/contracts.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/files.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/output.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/preview.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/recognition.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/records.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/service.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/settings.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/suppliers.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/recognition/__init__.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/recognition/background.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/recognition/billing.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/recognition/dates.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/recognition/originals.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/inventory/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/recognition/policy.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/recognition/progress.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/sales/access.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/sales/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/sales/contracts.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/sales/files.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/sales/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/sales/preview.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/sales/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/sales/recognition.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/integrations/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/sales/records.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/sales/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/team/__init__.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/cashier/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/team/service.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/cashier/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/work/__init__.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/work/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/work/access.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/work/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/work/boards.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/work/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/work/contracts.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/work/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/work/documents.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/work/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/work/notify.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/work/api.py` |
| `apps/pearnly_erp_portal/pearnly_erp_portal/work/service.py` | `apps/pearnly_erp_portal/pearnly_erp_portal/work/api.py` |

</details>

<details><summary>Cowork（没有流程 178 个）</summary>

| 模块 | 经哪个入口导入（举一个） |
|---|---|
| `apps/pearnly_cowork/pearnly_cowork/access/menu.py` | `apps/pearnly_cowork/pearnly_cowork/line/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/assistant/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/assistant/company_tools.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/assistant/tools.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/access.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/automation.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/choices.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/context.py` | `apps/pearnly_cowork/pearnly_cowork/companies/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/current_book.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/exceptions.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/identity.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/ledger.py` | `apps/pearnly_cowork/pearnly_cowork/companies/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/months.py` | `apps/pearnly_cowork/pearnly_cowork/companies/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/notes.py` | `apps/pearnly_cowork/pearnly_cowork/companies/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/pending.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/rules.py` | `apps/pearnly_cowork/pearnly_cowork/companies/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/service.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companies/tasks.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companion/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/companion/request_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companion/credentials.py` | `apps/pearnly_cowork/pearnly_cowork/companion/request_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companion/devices.py` | `apps/pearnly_cowork/pearnly_cowork/folder_intake/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companion/http_auth.py` | `apps/pearnly_cowork/pearnly_cowork/companion/request_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companion/request_access.py` | `apps/pearnly_cowork/pearnly_cowork/companion/request_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companion/request_service.py` | `apps/pearnly_cowork/pearnly_cowork/companion/request_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/companion/request_store.py` | `apps/pearnly_cowork/pearnly_cowork/companion/request_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/connections/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/connections/access.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/connections/account_search.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/connections/collector.py` | `apps/pearnly_cowork/pearnly_cowork/companion/heartbeat.py` |
| `apps/pearnly_cowork/pearnly_cowork/connections/desktop_update.py` | `apps/pearnly_cowork/pearnly_cowork/companion/heartbeat.py` |
| `apps/pearnly_cowork/pearnly_cowork/connections/model.py` | `apps/pearnly_cowork/pearnly_cowork/companion/heartbeat.py` |
| `apps/pearnly_cowork/pearnly_cowork/connections/mrerp_needs.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/connections/pool.py` | `apps/pearnly_cowork/pearnly_cowork/companion/heartbeat.py` |
| `apps/pearnly_cowork/pearnly_cowork/connections/preparation.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/connections/reports.py` | `apps/pearnly_cowork/pearnly_cowork/companion/heartbeat.py` |
| `apps/pearnly_cowork/pearnly_cowork/connections/service.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/contracts/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/contracts/document_policy.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/contracts/duplicates.py` | `apps/pearnly_cowork/pearnly_cowork/history/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/files/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/files/access.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/files/service.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/access.py` | `apps/pearnly_cowork/pearnly_cowork/companies/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/agent.py` | `apps/pearnly_cowork/pearnly_cowork/companies/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/company_folders.py` | `apps/pearnly_cowork/pearnly_cowork/companies/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/contracts.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/destinations.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/fetch.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/follow.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/inbox.py` | `apps/pearnly_cowork/pearnly_cowork/folder_intake/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/live.py` | `apps/pearnly_cowork/pearnly_cowork/folder_intake/machine.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/notify.py` | `apps/pearnly_cowork/pearnly_cowork/folder_intake/machine.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/pump.py` | `apps/pearnly_cowork/pearnly_cowork/folder_intake/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/push.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/readers.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/rules.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/settings.py` | `apps/pearnly_cowork/pearnly_cowork/folder_intake/machine.py` |
| `apps/pearnly_cowork/pearnly_cowork/folder_intake/workbook.py` | `apps/pearnly_cowork/pearnly_cowork/folder_intake/machine.py` |
| `apps/pearnly_cowork/pearnly_cowork/history/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/history/certificates.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/history/document_policy.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/history/duplicates.py` | `apps/pearnly_cowork/pearnly_cowork/history/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/history/push_state.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/history/removal.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/history/storage.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/line/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/line/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/line/binding.py` | `apps/pearnly_cowork/pearnly_cowork/line/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/masters/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/masters/buyer_resolve.py` | `apps/pearnly_cowork/pearnly_cowork/masters/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/masters/client_import.py` | `apps/pearnly_cowork/pearnly_cowork/masters/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/masters/client_records.py` | `apps/pearnly_cowork/pearnly_cowork/masters/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/masters/clients.py` | `apps/pearnly_cowork/pearnly_cowork/masters/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/masters/product_names.py` | `apps/pearnly_cowork/pearnly_cowork/masters/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/masters/product_units.py` | `apps/pearnly_cowork/pearnly_cowork/masters/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/masters/products.py` | `apps/pearnly_cowork/pearnly_cowork/masters/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/masters/push_inputs.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/masters/workspaces.py` | `apps/pearnly_cowork/pearnly_cowork/companion/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/engine/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/engine/config.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/remote/callback.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/engine/contracts.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/engine/document_prompts.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/engine/document_tier.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/engine/errors.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/engine/parsing.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/engine/runner.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/enterprise/header_bank.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/enterprise/local/bank_chain.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/enterprise/local/common.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/enterprise/local/geometry.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/enterprise/local/result.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/enterprise/pages.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/remote/callback.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/execution/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/execution/service.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/execution/settings.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/execution/store.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/execution/work_guard.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/feedback/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/feedback/service.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/policy.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/remote/callback.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/providers/client.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/remote/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/files/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/remote/contract.py` | `apps/pearnly_cowork/pearnly_cowork/files/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/remote/forward.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/remote/callback.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/remote/tasks.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/remote/callback.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/schema/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/schema/documents.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/schema/evidence.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/table_engine/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/table_engine/roundtrip.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/table_inputs/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/validation/chains.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/ocr/validation/sources.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/pos_import/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/pos_import/layouts.py` | `apps/pearnly_cowork/pearnly_cowork/companies/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/pos_import/service.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/pos_import/tables.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/application/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/application/assess.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/application/conflicts.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/application/goods_posting.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/application/provenance.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/application/service.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/automation/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/automation/rules.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/automation/service.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/choices.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/export_actions.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/export_facts.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/express/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/express/account_suggest.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/express/company_choices.py` | `apps/pearnly_cowork/pearnly_cowork/companies/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/express/decide.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/express/goods_posting.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/express/pushed.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/mrerp/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/mrerp/runner.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/native/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/native/contracts.py` | `apps/pearnly_cowork/pearnly_cowork/companion/heartbeat.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/native/documents.py` | `apps/pearnly_cowork/pearnly_cowork/companion/heartbeat.py` |
| `apps/pearnly_cowork/pearnly_cowork/push/native/service.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/readback/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/readback/service.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/reconciliation/export/theme.py` | `apps/pearnly_cowork/pearnly_cowork/recon_center/sheets.py` |
| `apps/pearnly_cowork/pearnly_cowork/reconciliation/recon_access.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/roundtrip/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/roundtrip/access.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/roundtrip/contracts.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/roundtrip/export.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/roundtrip/export_mapping.py` | `apps/pearnly_cowork/pearnly_cowork/roundtrip/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/roundtrip/export_writer.py` | `apps/pearnly_cowork/pearnly_cowork/roundtrip/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/roundtrip/intake.py` | `apps/pearnly_cowork/pearnly_cowork/ocr/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/roundtrip/service.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/tabular/summary.py` | `apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` |
| `apps/pearnly_cowork/pearnly_cowork/targets/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/targets/catalog_groups.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/targets/choice.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/targets/contracts.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/targets/freeze.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/targets/mrerp_masters.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/targets/mrerp_menus.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/targets/projection.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/targets/selection.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/tax/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/tax/access.py` | `apps/pearnly_cowork/pearnly_cowork/companies/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/tax/service.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/tax/wht.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/team/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/team/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/team/service.py` | `apps/pearnly_cowork/pearnly_cowork/team/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/work/__init__.py` | `apps/pearnly_cowork/pearnly_cowork/assistant/pending_api.py` |
| `apps/pearnly_cowork/pearnly_cowork/work/access.py` | `apps/pearnly_cowork/pearnly_cowork/work/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/work/boards.py` | `apps/pearnly_cowork/pearnly_cowork/work/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/work/contracts.py` | `apps/pearnly_cowork/pearnly_cowork/work/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/work/documents.py` | `apps/pearnly_cowork/pearnly_cowork/work/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/work/notify.py` | `apps/pearnly_cowork/pearnly_cowork/work/api.py` |
| `apps/pearnly_cowork/pearnly_cowork/work/service.py` | `apps/pearnly_cowork/pearnly_cowork/work/api.py` |

</details>

<details><summary>DMS（没有流程 72 个）</summary>

| 模块 | 经哪个入口导入（举一个） |
|---|---|
| `apps/pearnly_dms/pearnly_dms/__init__.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/account/__init__.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/account/portal.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/adapter/__init__.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/adapter/account_probe.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/adapter/admin_pool.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| `apps/pearnly_dms/pearnly_dms/adapter/change_sync.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| `apps/pearnly_dms/pearnly_dms/adapter/live_sync.py` | `apps/pearnly_dms/pearnly_dms/connections/snapshots.py` |
| `apps/pearnly_dms/pearnly_dms/adapter/operations.py` | `apps/pearnly_dms/pearnly_dms/connections/snapshots.py` |
| `apps/pearnly_dms/pearnly_dms/adapter/push.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/adapter/read_priority.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| `apps/pearnly_dms/pearnly_dms/adapter/session.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/adapter/session_cache.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/adapter/snapshot_writeback.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/booking/__init__.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/booking/booking_amend.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/booking/booking_ledger.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/booking/booking_payments.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/booking/booking_review.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/booking/conversation/__init__.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/booking/conversation/qa_cards.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/booking/conversation/qa_util.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/booking/conversation/review_fix.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/booking/delivery.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/client/__init__.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/client/eventlog_reader.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/client/gateway.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| `apps/pearnly_dms/pearnly_dms/client/snapshot_reader.py` | `apps/pearnly_dms/pearnly_dms/connections/snapshots.py` |
| `apps/pearnly_dms/pearnly_dms/client/transport.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| `apps/pearnly_dms/pearnly_dms/config/__init__.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| `apps/pearnly_dms/pearnly_dms/config/pacing.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| `apps/pearnly_dms/pearnly_dms/connections/__init__.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| `apps/pearnly_dms/pearnly_dms/connections/background.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| `apps/pearnly_dms/pearnly_dms/contracts/client_base.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| `apps/pearnly_dms/pearnly_dms/contracts/customer_merge.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/contracts/endpoint.py` | `apps/pearnly_dms/pearnly_dms/connections/snapshots.py` |
| `apps/pearnly_dms/pearnly_dms/contracts/geo_matching.py` | `apps/pearnly_dms/pearnly_dms/connections/snapshots.py` |
| `apps/pearnly_dms/pearnly_dms/contracts/id_validate.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/contracts/models.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| `apps/pearnly_dms/pearnly_dms/editor/__init__.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/editor/booking_edit.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/editor/browser_ticket.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/editor/customer_edit.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/editor/edit_link.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/editor/edit_snapshot.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/i18n.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/intake/__init__.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/intake/customer_diff.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/intake/draft.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/intake/flow.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/intake/review.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/intake/scan.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/intake/service.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/line/__init__.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/line/cards.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/line/errors.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/line/identity.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/line/out.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/line/page_reply.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/masters/company_banks.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/masters/master_contract.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/menu/__init__.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/menu/menu_flow.py` | `apps/pearnly_dms/pearnly_dms/editor/browser_api.py` |
| `apps/pearnly_dms/pearnly_dms/ocr/__init__.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/ocr/ocr_service.py` | `apps/pearnly_dms/pearnly_dms/account/api.py` |
| `apps/pearnly_dms/pearnly_dms/operators/__init__.py` | `apps/pearnly_dms/pearnly_dms/operators/roster.py` |
| `apps/pearnly_dms/pearnly_dms/operators/advisors.py` | `apps/pearnly_dms/pearnly_dms/operators/roster.py` |
| `apps/pearnly_dms/pearnly_dms/operators/line_account.py` | `apps/pearnly_dms/pearnly_dms/operators/roster.py` |
| `apps/pearnly_dms/pearnly_dms/records/access.py` | `apps/pearnly_dms/pearnly_dms/query/records.py` |
| `apps/pearnly_dms/pearnly_dms/snapshots/__init__.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| `apps/pearnly_dms/pearnly_dms/snapshots/manual.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| `apps/pearnly_dms/pearnly_dms/snapshots/store.py` | `apps/pearnly_dms/pearnly_dms/connections/api.py` |

</details>

<details><summary>控制面（没有流程 26 个）</summary>

| 模块 | 经哪个入口导入（举一个） |
|---|---|
| `apps/pearnly_control/pearnly_control/__init__.py` | `apps/pearnly_control/pearnly_control/assistant_entry/billing.py` |
| `apps/pearnly_control/pearnly_control/account/__init__.py` | `apps/pearnly_control/pearnly_control/account/member_api.py` |
| `apps/pearnly_control/pearnly_control/account/identity_pools.py` | `apps/pearnly_control/pearnly_control/issues.py` |
| `apps/pearnly_control/pearnly_control/account/member_provider.py` | `apps/pearnly_control/pearnly_control/account/member_api.py` |
| `apps/pearnly_control/pearnly_control/account/member_service.py` | `apps/pearnly_control/pearnly_control/account/member_api.py` |
| `apps/pearnly_control/pearnly_control/assistant_entry/__init__.py` | `apps/pearnly_control/pearnly_control/assistant_entry/api.py` |
| `apps/pearnly_control/pearnly_control/assistant_entry/contracts.py` | `apps/pearnly_control/pearnly_control/assistant_entry/api.py` |
| `apps/pearnly_control/pearnly_control/assistant_entry/entry.py` | `apps/pearnly_control/pearnly_control/assistant_entry/api.py` |
| `apps/pearnly_control/pearnly_control/assistant_entry/registry.py` | `apps/pearnly_control/pearnly_control/assistant_entry/billing.py` |
| `apps/pearnly_control/pearnly_control/assistant_entry/routes.py` | `apps/pearnly_control/pearnly_control/assistant_entry/billing.py` |
| `apps/pearnly_control/pearnly_control/billing.py` | `apps/pearnly_control/pearnly_control/assistant_entry/billing.py` |
| `apps/pearnly_control/pearnly_control/billing_auth.py` | `apps/pearnly_control/pearnly_control/account/member_api.py` |
| `apps/pearnly_control/pearnly_control/billing_remote.py` | `apps/pearnly_control/pearnly_control/assistant_entry/billing.py` |
| `apps/pearnly_control/pearnly_control/features.py` | `apps/pearnly_control/pearnly_control/issues.py` |
| `apps/pearnly_control/pearnly_control/infra.py` | `apps/pearnly_control/pearnly_control/issues.py` |
| `apps/pearnly_control/pearnly_control/onboarding/__init__.py` | `apps/pearnly_control/pearnly_control/provisioning.py` |
| `apps/pearnly_control/pearnly_control/onboarding/assistant.py` | `apps/pearnly_control/pearnly_control/provisioning.py` |
| `apps/pearnly_control/pearnly_control/onboarding/line_ids.py` | `apps/pearnly_control/pearnly_control/provisioning.py` |
| `apps/pearnly_control/pearnly_control/onboarding/line_liff.py` | `apps/pearnly_control/pearnly_control/provisioning.py` |
| `apps/pearnly_control/pearnly_control/onboarding/teardown_members.py` | `apps/pearnly_control/pearnly_control/provisioning.py` |
| `apps/pearnly_control/pearnly_control/payments/history.py` | `apps/pearnly_control/pearnly_control/assistant_entry/billing.py` |
| `apps/pearnly_control/pearnly_control/payments/pricing.py` | `apps/pearnly_control/pearnly_control/assistant_entry/billing.py` |
| `apps/pearnly_control/pearnly_control/payments/topup.py` | `apps/pearnly_control/pearnly_control/assistant_entry/billing.py` |
| `apps/pearnly_control/pearnly_control/payments/transactions.py` | `apps/pearnly_control/pearnly_control/assistant_entry/billing.py` |
| `apps/pearnly_control/pearnly_control/payments/usage.py` | `apps/pearnly_control/pearnly_control/billing_api.py` |
| `apps/pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_deployment/pearnly_deployment.py` | `apps/pearnly_control/pearnly_control/provisioning.py` |

</details>

<details><summary>共用层（没有流程 39 个）</summary>

| 模块 | 经哪个入口导入（举一个） |
|---|---|
| `apps/pearnly_core/pearnly_core/account/line_login_contract.py` | `apps/pearnly_core/pearnly_core/account/line_login.py` |
| `apps/pearnly_core/pearnly_core/account/member_client.py` | `apps/pearnly_core/pearnly_core/account/line_login.py` |
| `apps/pearnly_core/pearnly_core/account/profile.py` | `apps/pearnly_core/pearnly_core/account/line_login.py` |
| `apps/pearnly_core/pearnly_core/assistant/definition.py` | `apps/pearnly_core/pearnly_core/assistant/webhook.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/__init__.py` | `apps/pearnly_core/pearnly_core/cc_desktop/api.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/books.py` | `apps/pearnly_core/pearnly_core/cc_desktop/api.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/contracts.py` | `apps/pearnly_core/pearnly_core/cc_desktop/api.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/devices.py` | `apps/pearnly_core/pearnly_core/cc_desktop/api.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/documents.py` | `apps/pearnly_core/pearnly_core/cc_desktop/api.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/http_auth.py` | `apps/pearnly_core/pearnly_core/cc_desktop/machine.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/jobs.py` | `apps/pearnly_core/pearnly_core/cc_desktop/machine.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/products.py` | `apps/pearnly_core/pearnly_core/cc_desktop/api.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/release.py` | `apps/pearnly_core/pearnly_core/cc_desktop/machine.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/requests.py` | `apps/pearnly_core/pearnly_core/cc_desktop/machine.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/store.py` | `apps/pearnly_core/pearnly_core/cc_desktop/machine.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/wire/__init__.py` | `apps/pearnly_core/pearnly_core/cc_desktop/api.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/wire/ack_rules.py` | `apps/pearnly_core/pearnly_core/cc_desktop/machine.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/wire/queue_contracts.py` | `apps/pearnly_core/pearnly_core/cc_desktop/machine.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/wire/request_contracts.py` | `apps/pearnly_core/pearnly_core/cc_desktop/machine.py` |
| `apps/pearnly_core/pearnly_core/cc_desktop/wire/route_contracts.py` | `apps/pearnly_core/pearnly_core/cc_desktop/api.py` |
| `apps/pearnly_core/pearnly_core/claude_ocr/__init__.py` | `apps/pearnly_core/pearnly_core/ocr.py` |
| `apps/pearnly_core/pearnly_core/claude_ocr/client.py` | `apps/pearnly_core/pearnly_core/ocr.py` |
| `apps/pearnly_core/pearnly_core/execution/budget.py` | `apps/pearnly_core/pearnly_core/ocr.py` |
| `apps/pearnly_core/pearnly_core/execution/errors.py` | `apps/pearnly_core/pearnly_core/ocr.py` |
| `apps/pearnly_core/pearnly_core/execution/provider.py` | `apps/pearnly_core/pearnly_core/ocr.py` |
| `apps/pearnly_core/pearnly_core/execution/work.py` | `apps/pearnly_core/pearnly_core/ocr.py` |
| `apps/pearnly_core/pearnly_core/exports/__init__.py` | `apps/pearnly_core/pearnly_core/billing_www.py` |
| `apps/pearnly_core/pearnly_core/exports/disposition.py` | `apps/pearnly_core/pearnly_core/billing_www.py` |
| `apps/pearnly_core/pearnly_core/exports/service.py` | `apps/pearnly_core/pearnly_core/billing_www.py` |
| `apps/pearnly_core/pearnly_core/express_rules/push/__init__.py` | `apps/pearnly_core/pearnly_core/cc_desktop/machine.py` |
| `apps/pearnly_core/pearnly_core/express_rules/push/wire_location.py` | `apps/pearnly_core/pearnly_core/cc_desktop/machine.py` |
| `apps/pearnly_core/pearnly_core/express_rules/targets/__init__.py` | `apps/pearnly_core/pearnly_core/cc_desktop/machine.py` |
| `apps/pearnly_core/pearnly_core/express_rules/targets/account_match.py` | `apps/pearnly_core/pearnly_core/cc_desktop/machine.py` |
| `apps/pearnly_core/pearnly_core/ocr_models/__init__.py` | `apps/pearnly_core/pearnly_core/ocr.py` |
| `apps/pearnly_core/pearnly_core/ocr_models/orient.py` | `apps/pearnly_core/pearnly_core/ocr.py` |
| `apps/pearnly_core/pearnly_core/payments/history.py` | `apps/pearnly_core/pearnly_core/billing_www.py` |
| `apps/pearnly_core/pearnly_core/transaction/__init__.py` | `apps/pearnly_core/pearnly_core/cc_desktop/api.py` |
| `apps/pearnly_core/pearnly_core/transaction/conflicts.py` | `apps/pearnly_core/pearnly_core/cc_desktop/api.py` |
| `apps/pearnly_core/pearnly_core/workspace.py` | `apps/pearnly_core/pearnly_core/account/line_login.py` |

</details>

## 暂停点（10-29 晚，用户下班）

已做（分支 `pearnly/flows-batch-1`）：
- 新流程 3 条，都在两套数据 × 电脑 / 手机宽度跑通：`erp.line.stocktake.phone`（含假摄像头、撤销、GET 403，并入 work-followups 的断言）、
  `cc.chat`、`cc.settings`。
- 实验室：`agents` 假 Claude Managed Agents 服务（`scripts/flows/lab_agents.py`，用真实适配器验过 31 项）、bench 加后台 worker、
  `lab_init` 给产品站点写 CC 引擎配置（假密钥）；`run.py` 加 `--migrate`（回写状态时必做）、前端产物新鲜度检查、模块可声明 `READY`。
- 覆盖闸「入口背后的模块」：判定和 `--behind` 清单已做，`BEHIND_ENFORCED = False`（只提醒）。

这一批的全部流程（8 条 × 4 格，带站点迁移）跑到第 1 格时，回归实验栈被别的窗口收尾停掉，这一轮作废、没有回写状态；全量闸门 149 项已过。第一批**没有合入 main**，停在已推送的分支上。

明天第一步（主会话 10-29 已定 B 方案）：
0. 起回归实验栈，变基到最新 main，重跑全量闸门和全部流程（`--all --migrate`），都过再按主线集成合入第一批。
1. 存量 372 个登记成「存量没有流程」（基线文件，只许减少）；新增模块、入口已有流程的模块一律拦；`BEHIND_ENFORCED` 打开。
2. 提醒做显眼：CI 摘要（`GITHUB_STEP_SUMMARY`）列出改到的存量模块和经哪个入口导入；`deploy.sh` 构建前列出本批改到的存量模块。
3. 按「最该先补的 20 个入口」顺序补流程；每补一条刷新本记录里的清单数字。
4. 等着的：任务管理流程在 C 盘窗口的 `erp.work.board` / `cowork.work.board` 合入后补建卡、改、移、归档、通知；Cowork 上 4 条 CC 流程
   （定时任务、待选账套、公司、项目）与 `cowork.recon.bank`、`cowork.intake.*`（数据已在 main 的 cowork-new / cowork-old）。
5. 产品观察（已报主会话）：手机盘点页按台商品出数量框、保存才被拒，查码还登记一行快照。

## 入口背后的模块：B 方案开拦截（10-30）

- 存量清单 `tests/flows/behind_baseline.txt`：开拦截时（main 724d374a + 本批流程）没有流程的入口背后模块 **344 个**
  （10-29 统计 372，主线新进的任务管理、盘点菜单等流程已覆盖了一部分）。
- 规则：不在清单里又没有流程的，改到就拦；清单里的改到只提醒——CI 摘要列出「改到的存量模块（还没有流程兜底）」及经哪个入口导入，
  `deploy.sh` 在流程闸放行后列出上次放上生产以来改到的存量模块。清单只许减少：新加条目拦；已有流程覆盖、已不是入口背后或文件已删的条目必须删掉（拦）。
- 入口补上流程后，它背后那一片自动转为拦截（同时要求把清单里对应条目删掉）。每次合入后在这里更新数字。
- 测试：`tests/test_flows_coverage.py`（新增 5 项：清单与现状一致、清单外没流程拦、清单内只提醒、已覆盖条目留在清单里拦、部署提醒不判失败）；
  `scripts/production/test-deploy-run.py`（存量提醒显示、不挡部署）。

