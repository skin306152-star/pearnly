# 控制面 Desk 无限重载（闪屏）—— 根因已定位并修复

日期：2026-09-17。状态：**已修复并自验证通过**（含反向对照）。

## 结论（一句话）

建站时 Frappe 把默认首页写成了 `setup-wizard`，而"设置向导"页面在设置已完成时又会跳回 `/desk`
——**两头互踢，整页无限重载**。改成真实存在的 `desktop` 后消失。

实测对比（同一入口、同一浏览器、同一 8 秒窗口）：

| `desktop:home_page` | 8 秒内整页导航次数 | 结果 |
|---|---|---|
| `setup-wizard`（坏值） | **259 次**（约每秒 32 次重载） | FAIL，界面根本没渲染出来 |
| `desktop`（修好后） | **0 次** | PASS |

## 因果链（逐条对上用户看到的现象）

1. `frappe/boot.py:254` 用 `frappe.db.get_default("desktop:home_page")` 决定 Desk 首页，
   写进 `boot.home_page`。
2. 建站时 `frappe/utils/install.py:48` 会把这个默认值写成 `setup-wizard`；本项目从不跑
   交互式设置向导，所以这个值一直留在库里（**7 个站点全都是**）。
3. `frappe/www/desk.py` 渲染出的 `setup-wizard` 页面脚本第一句就是：
   `if (frappe.boot.setup_complete) window.location.href = frappe.boot.apps_data.default_path || "/desk"`
   ——而本站 `apps_data.default_path` 是空串，于是**跳回 `/desk`**。
4. `/desk` 无路由时 `frappe/public/js/frappe/views/pageview.js:52` 又用 `boot.home_page`
   渲染 `setup-wizard` 页面 → 回到第 3 步。**闭环成立。**
5. `frappe/public/js/frappe/desk.js:383`：`home_page == "setup-wizard"` 时**故意不建工具栏**
   （防你从向导里跑掉）。这解释了对上截图的"只有左下角头像、没有菜单"的骨架样子。
6. 浏览器控制台干净（整页加载会清空报错列表）+ 页面一直变 → 正是"整页反复重载"的特征，
   与当初的观察一致。

## 修复内容

1. **7 个站点**（control / cowork-a,b / erp-a,b / dms-a,b）：
   - `desktop:home_page` → `desktop`（本 build 里真实存在的 desk 页面只有 `desktop` 和
     `setup-wizard`；上游向导写的 `workspace` 在这里不存在，会落回 `desktop`）。
   - `Installed Application.is_setup_complete` → 1（**这才是"设置已完成"的真正开关**，
     见下面"错判留档"第 2 条）。
2. **固化，避免重建回归**：新增 `scripts/sample-finalize-site.sh`（建站收尾，幂等，
   值不对会直接报错退出），并接进 `scripts/sample-init-group.sh` 的 `new-site` 之后。
3. **回归测试**：新增 `scripts/sample-verify-desk-stable.mjs`，用真实浏览器判定
   "是否还在整页重载"，并输出截图。

```
node scripts/sample-verify-desk-stable.mjs                      # 控制面（earn/earn）
PEARNLY_DESK_USER=Administrator PEARNLY_DESK_PASS=admin-local-only \
  node scripts/sample-verify-desk-stable.mjs http://erp-a.localhost:8102
```

## 验证证据

浏览器实测（Playwright + 真实 Chromium，每项观察 8 秒）：

| 入口 | 整页导航 | 界面渲染 | 结论 |
|---|---|---|---|
| A `http://control.localhost:8104/desk` | 0 | 是 | 稳定 |
| B `http://control.localhost:8104/app`（点首页等价路径） | 0 | 是 | 稳定 |
| D 表单页左侧点"主页" → `/desk/users` | 1（正常一次加载，非循环） | 是 | 稳定 |

截图：`docs/p4/evidence/desk-stable-a-direct-desk.png`、
`desk-stable-b-home-entry.png`、`desk-stable-d-home-click.png`。

服务端（带会话）：

- 7 个站点 `boot.home_page` 全为 `desktop`、`setup_complete` 全为 `True`、
  `setup_wizard_requires` 全为 `None`。
- 连续 20 次 `GET /desk`：全部 200，耗时 6–10 ms，字节数完全一致（139366）。
- 一轮干净回归中 nginx 错误 0 条，`/desk` 请求 3 次（每个探针 1 次）。
- ERP 站点（`erp-a.localhost:8102`）浏览器实测同样 PASS。

## 错判留档（避免重犯）

1. **"384m 内存太紧导致被 OOM 杀掉"——错。**
   我曾据此把 `control-web` 提到 `1g`（改动已撤回，文件回到 384m）。实测对照：
   12 路并发下 384m 与 1g 的失败数**完全一样（都是 6/24）**，错误也是同一个，
   与内存限制无关。当时看到的 `upstream prematurely closed connection` / 502，
   是重载风暴（每秒 30+ 请求）的**后果**，不是原因。
2. **`setup_complete` 改错了字段。**
   我第一版收尾脚本改的是 `System Settings.setup_complete` —— 那只是判断结果的**回写**
   （`setup_wizard.py:406`），单独改它没有任何效果。真正的开关是
   `Installed Application.is_setup_complete`（`frappe/__init__.py:1537`），
   对应 `setup_wizard.py:177` 的写法。第一版脚本因此**静默无效**，而且我还把错误输出丢了。
   现在的脚本用 `bench execute`（`bench console` 会吞多行脚本），并且值不对就退出非零。
3. 之前把"缺前置 nginx"当成根因也不完整：nginx 确实该加（`/socket.io` 从"永远挂住"
   变成 2 ms 返回 200），但它不是无限重载的原因。

## 附带发现（非本次问题，未修）

1. **同一会话被并发狂打会 500**：12 路并发请求 `/desk` 时 6/24 返回 500，错误是
   `MySQLdb.OperationalError: (1020, "Record has changed since last read in table 'tabSessions'")`
   —— MariaDB 会话行锁冲突。单浏览器正常操作不会触发（实测 3 次请求 0 错误）。
   与内存限制无关（1g 下同样 6/24）。
2. **未登录访问 `/desk`、`/app` 返回 301（永久重定向）**，浏览器会永久缓存，
   可能形成 `/desk → /login → /desk` 的另一种循环。建议改成 302，本轮未改。
3. **`cowork` / `erp` / `dms` 三组仍没有 nginx 前置层**，它们的实时服务没有被转发：
   `/socket.io/?EIO=4&transport=polling` 实测控制面 **200**，另三组 **404**。
   影响是 Desk 的实时通知/后台任务进度（不是重载）。本轮只改了控制面，
   如需补齐要新增 nginx 前置层并调整端口归属。

## 控制面可用入口

| 用途 | 地址 |
|---|---|
| 发号 | http://control.localhost:8104/pearnly-issue |
| 原生后台 | http://control.localhost:8104/desk |

控制面登录：本地账号 `earn` / `earn`（邮箱 `earn@pearnly.local`，仅本机使用）。
