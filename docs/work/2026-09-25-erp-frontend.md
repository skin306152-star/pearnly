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

## 线上每次打开/刷新都慢（`7f68801`、`d156e39`，未部署）

- 现象：线上每次打开或刷新都像强制刷新，每页都慢。
- 证据（09-25 只读）：线上 `/assets` 响应没有 `Cache-Control`/`Expires`，只有 ETag；一次整页 96 个请求（脚本 58）；
  主机 nginx 只有 HTTP/1.1。同一静态文件：服务器本机直连容器 0.002–0.01s、本机经主机 https 约 0.1s、从用户 Mac 1.3–5.9s；
  服务器负载 0.5、内存 5G/32G。左侧菜单切换是页面内路由（不整页重载），每次约 6 个数据请求，正常。
- 根因：官方 bench nginx 模板给 `/assets` 加 `Cache-Control "max-age=31536000"`；线上前端用的 frappe_docker 镜像模板没有这行。
- 修改：
  - `infrastructure/production/frappe.conf.template` = 线上镜像模板原文 + `/assets` 一年缓存；`/assets/**.html` 用 `no-cache`
    （DMS LINE 预约页以不带版本的 `pearnly-app/index.html` 进入编辑器）；两处都重新 `include` 安全头。三个前端在 compose 里挂载它。
    本地样板模板同步同样规则。
  - 主机 nginx：`auth-tls.conf`、`default-deny.conf`、开户脚本生成的租户配置全部 `listen 443 ssl http2`（服务器 nginx 1.24 的写法）。
- 发新版后用户怎么拿到新的：`/desk` 页面本身 `no-store`，每次取最新；站点实际加载的 60 个脚本、8 个样式全部带内容版本
  （`?v=` 或打包哈希），改了就换地址。未带版本的只有：LINE 编辑器入口 html（已 no-cache）与原样照搬的扫码库
  `vendor/barcode-detector/`（升级时须换目录名）。
- 本地验证：用本仓线上模板起临时前端连本地 ERP：`nginx -t` 通过；静态文件 `max-age=31536000` 且 5 个安全头在；
  `pearnly-app/index.html` 为 `no-cache`；`/desk` 为 `no-store`；404 不带缓存头。临时容器已删。
- **部署时额外要做**（需授权）：按产品激活时前端会重建，模板随 `infrastructure/production/` 投递；但主机 nginx 已存在的
  站点文件（`/etc/nginx/conf.d/pearnly-tenant-*.conf`、`pearnly-auth.conf`、`pearnly-default.conf` 等）不会自动重写，
  要逐个改成 `listen 443 ssl http2`，`nginx -t` 通过后 `reload`，再从外网回读 `http_version=2` 与缓存头。

## 接 LINE：按人开通（`e9836e7` → `0c620c0` → 本提交，未部署）

- 现状（09-25 只读）：后端绑定码 / 状态 / 解绑、LINE 收发与收件箱早已有；ERP 的「集成」页只有菜单声明、没有页面，
  所以入口被隐藏。线上 testerp 已配 LINE 渠道（官方号 ERP-TEST `@063eadty`），但 **LINE 后台回调地址仍是已退役的
  `https://pearnly.com/api/line/erp/webhook`**，新系统 72 小时内一次回调都没收到、绑定 0、收件箱 0。
  应改为 `https://testerp.pearnly.com/api/method/pearnly_core.line.webhook` —— 外部写入，**待用户授权**，宜在 ERP 部署后改。
- 用户决定（09-25）：
  - LINE 在团队管理里**逐人勾选**（添加 / 编辑用户），实现为原生角色 `ERP LINE`；租户管理员本人默认可用。
    业务岗位不再自动带 LINE；绑上之后能发什么单据仍按采购 / 销售岗位判。
  - **绑定码只给本人**：团队列表里打开**自己**、或本人的「个人信息」页，才出现二维码 / Bot ID / 绑定码；打开别人只看绑定状态。
  - 取消勾选 = **立即解绑**；LINE 通道对没开 LINE 的账号拒绝绑定、拒绝收单。
  - 撤掉独立「集成」页；界面只留字段标题，不写说明文字。
- 迁移来源：旧线上 `src/home/page-integrations.ts`（ec2e5c957e6b，源码取自本机归档 bundle）。有意变化：二维码服务端本地生成
  （pyqrcode，与 DMS 同库），不再交给 `api.qrserver.com`；提示文案不照搬。
- 验证：CI 同名闸门全过；`tests/test_erp_portal_access.py` 103 项通过（集成能力改为按 `ERP LINE`）。本地 qerp：
  团队列表出现「LINE」列；添加用户弹窗有「LINE」勾选；打开自己出现绑定块（本地无渠道，二维码与码用页面内模拟返回验证）；
  服务端实测勾选 / 取消 / 取消即解绑 / 只改岗位不动 LINE 均符合预期（测试账号 `linetest@qerp.local` 已停用，仅本地样板）。
  修复：团队弹窗里的格子晚于调用才挂到页面，绑定块原先直接不画 —— 组件改为先等挂上再开始。
- 未验：真实 LINE 加好友 / 发码 / 发单据（本地无渠道，线上待部署 + 改回调）；个人信息页那一块只做了加载检查；
  本地新增用户走身份服务受限接口，本地样板调不通（原有限制，与本次无关）。

## 暂不处理

- 窄布局（约 800px 宽、两列）下物料框悬停时右侧图标占位导致长物料名被截：用户表示先不管。
