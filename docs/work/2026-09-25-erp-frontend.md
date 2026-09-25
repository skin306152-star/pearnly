# ERP 前端：用户逐项反馈（2026-09-25 起）

- 状态：进行中（用户在本窗口边看边改）。**未部署**：线上 ERP 仍是 `f0f5a43`，用户明确"先不急部署"。
- 仓库 / 分支：`codex/erp-frontend`，工作树 `/Users/skin/.codex/worktrees/erp-frontend/pearnly`，
  基线为当日本地 main。本地预览 `http://qerp.localhost:8102`（`pearnly-sample` 的 erp 组，挂载主目录 `/Users/skin/pearnly`，
  所以预览前先快进合入本地 main，改 JS/Python 后 `bash scripts/stack.sh restart erp-web`）。
- 本地样板登录：`tenant-admin` / `KcLocal123!`。本次给 qerp 站点加了一条身份绑定（业务 realm 的 tenant-admin →
  `owner@qerp.local`），因为原绑定的 `qerpowner` 密码无记录；只在本机样板，删掉该 `Pearnly Identity` 行即恢复。

## 已完成

| 问题 | 原因 | 修改 | 提交 |
| --- | --- | --- | --- |
| 左上角「切换公司」上线后不见 | 公司清单在 boot 数据里，框架按人缓存 `bootinfo`；新建第二家公司（线上 `mr.erp`，09-25 17:45）不清缓存，页面一直当只有一家 | `ERP Company` 的 `on_change` / `on_trash` 清 `bootinfo` | `58c3de0` |
| 供应商 / 客户框有扫码按钮 | 采购/销售单的交易方与物料共用 `entry-choice.js`，无条件挂扫码 | 只按 `p.scan_doctypes`（仅 `ERP Product`）挂 | `5cc7d00` |
| 线上采购"扫码带不出物料" | 线上日志：10:51、10:52 两次扫码都是 `kind=supplier`（点的是供应商框那颗错误按钮）；两个码在线上分别是物料「沐浴露」「蛋白质」 | 随上一行一并解决，无独立改动 | — |

两条提交已在远端 main（`3781bfb`，由 DMS 窗口推送时一并带上）。

## 决定：公司隔离按官方原生（用户 2026-09-25 确认"保持现状，按官方的来"）

依据：ERPNext v16.34.1（`b1225b5`，`pearnly-erp/upstream/erpnext`）。

- **按公司分**：仓库（`Warehouse.company` 必填）、库存（记在仓库上）、单据（带公司）；员工限定用官方 `User Permission`。本仓已是如此。
- **不分公司、全集团共用**：物料（`Item` 无公司字段，只有按公司的 `Item Defaults` 子表）、供应商 / 客户（只有按公司的
  `Party Account` 科目子表；`Allowed To Transact With` 仅在集团内部往来时由 `validate_inter_company_party` 检查）。
- 所以 `ERP Product` / `ERP Supplier` / `ERP Customer` **不加公司字段、不做公司隔离**。以后若要"某公司只卖部分物料"，
  按官方思路做按公司默认设置或按分组的用户权限，不拆表。

## 验证

| 版本 | 环境 | 场景 | 结果 |
| --- | --- | --- | --- |
| `58c3de0` | 本地 qerp | 保存公司前后查 `bootinfo` 缓存 | 前有、后无 |
| `58c3de0` | 本地 qerp，浏览器 | 用户新建第二家公司「1231」后不点刷新：boot 拿到 2 家、下拉出现「公司 >」；切到 1231、再切回 qerp-demo | 通过 |
| `5cc7d00` | 本地 qerp，浏览器 | 采购单：供应商框无扫码、物料框有；模拟扫码 `8850000000017` 带出物料/单位 | 通过 |
| `3781bfb` | 本地 qerp，当前公司 1231 | 仓库页只列 1231 的仓；库存页 SKU 0（qerp-demo 的 2 条结存不显示）；采购 0；物料页照常 2 条 | 与官方口径一致 |
| `f848f91` | 本地 qerp，浏览器 | 共用原件预览框：显示、放大 144%、旋转 90°、复位、全屏 | 通过（该提交推送早于这次点验，已补测） |

未验：供应商已选中后跳转箭头位置（本地无供应商数据）；非管理员账号按公司受限的浏览器视角；线上部署与真机。

## 暂不处理

- 窄布局（约 800px 宽、两列）下物料框悬停时右侧图标占位导致长物料名被截：用户表示先不管。
