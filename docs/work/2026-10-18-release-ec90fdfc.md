# 生产部署 ec90fdfc（CC 外壳第 10、11 轮 + Claude 识别三批）

- 依据：用户 10-06 起的按批次自主部署授权（部署不等用户同意、按批次上；见记忆 autonomous-batch-deploy）；10-17 起「没客户、进维护立即部署」。
  窗口协调：「消息含义理解」窗口 10-18 00:00 前回复没有在动生产或甲骨文，Claude 识别三批（ADR-022 / 023）随这一批部署、生产 runtime.env 由它追加的
  6 个键（备份 `runtime.env.bak-claude-ocr-20261017`、`.bak-claude-originals-20261018`），本窗口未动；测试窗口复核与抽查完后不占 lab。
- 上一版生产：产品激活 `b9da6b58`（`372dd6c4`，10-16）；10-17 「消息含义理解」窗口只同步过部署工具（`d4697672`，未激活产品）。

## 内容

- `pearnly/cc-shell-rebuild`（任务记录 [2026-10-16-cc-shell-rebuild.md](2026-10-16-cc-shell-rebuild.md)，设计 [2026-10-16-cc-shell-design.md](2026-10-16-cc-shell-design.md)）：
  CC 外壳重构第 4–11 轮（侧栏按公司分组与拖动、置顶与组内手排、手机端照 Claude、对话卡片照 Claude、记住的选择、三端按钮尺寸照 Claude、
  对话滚动照 Claude、删除对话先确认、「记费用」等）；第 11 轮重测与复核查出的修复：卡片以本站执行记录为准（`pearnly.tool_run`、停止后卡片作废、
  旧卡片回答不再 500）、停止不再走组件库 cancelRun、断网 / 重连后不重复消息、补拉不完整页（partial）不收口、允许后马上停止不再 1020 冲突、
  推送来的卡片当场取、MariaDB 快照隔离下推送线程不断流、拖动长帧（行与组头不随拖动上下文重画、挡板）、Esc 取消后原行松开不当点击、
  键盘拖动按列表先后、收菜单整页样式失效（手机连接提示那条兄弟选择器）、删除后焦点与收起侧栏 inert 等。
- main 上已合入、未部署的：Claude 识别三批（`5edbd5e4`、`df52c9ea`、`213743d0`，ADR-022 / 023：所有识别入口可按租户选 Claude、
  选 Claude 的租户原件存甲骨文；所有租户默认仍 Google）、服务器架构（`b50a724f` 等：swap、容量检查、改动记录异地、甲骨文守望）。

## 验证

- 本地闸门（`~/pearnly-devtools/gate.sh`）86 项通过（`ec90fdfc`）；候选离线检查在本机 Docker 用生产镜像 `20a07692` 跑 72 / 72 通过
  （外壳最终版与并入 `213743d0` 后各一次）；分支 Development checks run 39050529700 通过。
- 测试机 lab（实时源码）：第 11 轮清单 38 条 → 重测问题修复 → 复核清单 17 条（15 过；剩右侧栏卡片头 Esc 后原处松开仍开合，留第 12 轮；
  只带附件断在路上缩略图晚约 97ms 放回，记已知限制）→ 上线候选 `ec90fdfc` 抽查 5 项全过（焦点补修、快速回归；未做会调付费识别的上传）。
- A14 真机拖动帧（不开 trace 的帧间隔）：拿起 126 → 58–60ms、出菜单 105 → 72ms、收菜单 76–98 → 35ms、落定 48 → 35–39ms。

## 构建与激活

- main 快进到 `ec90fdfc` 并推送（回读远端一致）。构建 run 39050848252：offline、flow、snapshot 三条候选车道全过 →
  `sha256:ebba5a407bdc8e8d1a878bad05755e1b3443e7e57b05c7ef12090384f744d448`；stage run 39051352858 成功。
- 服务器脚本 `/opt/pearnly/state/deploy-ec90fdfc.sh`（日志 `deploy-ec90fdfc.log`，每 10 秒内存采样 `mem-ec90fdfc.log`；产品之间等可用内存 ≥ 8 GB）：
  部署工具与镜像一致（revision `ec90fdfc`）；泰国时间 10-17 23:53:56 ERP、23:55:57 Cowork、23:57:47 DMS 各 "activated with verified image"；
  23:59:26 基础层 "Foundation activated"；10-18 00:01:11 完成。
- 内存：开始可用 22.2 GB，全程最低 22.1 GB，结束 26.6 GB，swap 用 35 MB。这是 swap 与容量检查（服务器架构那批）第一次在产品激活里实际执行，没有骤降。
- 部署前服务器上已有一个非生产容器 `pearnly-maintenance-cowork`（不是这次起的，Cowork 对外一直 200，没被容量检查拦），已问「消息含义理解」窗口来历。

## 回读

- 生产 frappe 容器全部为 `ebba5a40`（含 billing-web）；三个识别进程 NOFORK=1；六站 maintenance=0。
- Error Log（23:53 起）：mrerp、testerp、testcowork、ris、testdms 0；erpcowork 10 条都在 Cowork 激活窗口内（23:56）：9 条
  `InReadOnlyMode: Scheduled jobs can't run in maintenance mode`、1 条 `SessionStopped`，属维护期间的正常现象；10-18 00:00 后 0 条。
- 新结构：两个 Cowork 站公司三表、会话五列、patch18 在；`Pearnly Assistant Tool Run.status` 选项含 `cancelled`（erpcowork、testcowork）。
- mrerp、erpcowork、ris `/api/method/ping` 200，fatcc.com 首页 200（其 ping 403 为既有放行规则）；mariadb 403 MiB / 8 GiB（刚重建）。
- 本机控制面 `control.localhost`：`tabPearnly Tenant` / `tabPearnly Issue` 都有 `ocr_engine`，7 个租户都是 Google（「消息含义理解」窗口 10-17 已迁移，本次只回读）。

## 未完成 / 后续

- 真机业务验收（用户在正式租户使用）：未执行。手机深色导航条（A4）请用户复验，必要时重装主屏应用。
- 第 12 轮：自写拖动替换 dnd-kit（`pearnly/cc-drag-engine`，进行中；含右侧栏卡片头 Esc 后原处松开不开合）。
- CC 前端标准化（`pearnly/cc-standards`，B0 补修中，之后 B1–B8，见 [2026-10-17-cc-frontend-standards.md](2026-10-17-cc-frontend-standards.md)）。
- 多电脑（`pearnly/multi-desktop`）、数字会计员第 3 期（`pearnly/da-phase3`，证据窗口改取法已回放验证）、文案闸门（`pearnly/copy-gate`）未合入，
  各自按任务记录推进；CC 桌面版 1.9.0 等第 3 期服务端部署后再发。
