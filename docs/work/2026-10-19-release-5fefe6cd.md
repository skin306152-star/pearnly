# 生产部署 5fefe6cd（CC 前端标准化 B2–B7、数字会计员第 3 期、CC 回复与标题按界面语言；CC 桌面版 1.9.0）

- 依据：用户 10-06 起的按批次自主部署授权（记忆 autonomous-batch-deploy）；10-18 晚用户「做好某一大批次统一部署」；10-17 起「没客户、进维护立即部署」。
- 窗口协调：「消息含义理解」10-19 确认这段时间不用测试机、不用生产；部署期间不停 `pearnly-binlog-ship.timer`、不动 ERP 组 common_site_config 的 `pearnly_cowork_ocr_remote` / `pearnly_cowork_originals_bucket`，部署后回读六站引擎都是 claude、mrerp / erpcowork / ris 原件在 oracle；可用内存低于约 4 GiB 或 swap 持续超过约 4 GiB 时告诉它（本次没碰到）。
- 上一版生产：`4090d01f`（镜像 1d1e0366，10-18 20:30）。本批 `4090d01f..5fefe6cd` 150 个提交。

## 内容

- **CC 前端标准化 B2–B7**（`docs/work/2026-10-17-cc-frontend-standards.md`）：组件库、外壳、弹窗与列表页、会话栏 / 结果 / 文档 / 工具卡、侧栏列表、对话区换成 globals.css 的标准写法；纯替换各批全矩阵 0 样式差、0 像素差，画面变化逐项只差第 7 节预期的项；闸门范围外写死值 1300 处左右 → 3 处（`ui/button.tsx`，留 B8）。B2 用户手机真手指通过（10-18 晚）；B3–B7 测试窗口 CC 桌面版深浅色通过（10-19 凌晨），用户 10-19 上午「大概看了下，没啥问题」。
- **数字会计员第 3 期**（`docs/work/2026-10-16-da-phase3-design.md`）：自动推送档位（只提议 / 按规则 / 整月，新老公司都从只提议起）、推后核对、两份同期账套按公司问一次并记住现用账套、选定一次同组停着的单跟着走、商品单入账方式（走库存 / 记费用）、月结台账档位与「已录 N/总数（自动 M）」、提议行。10-19 验收中按用户与测试窗口改的：提议行点开才有「推送」并显示 CC 备好的内容、不设「全部推送」（用户定）；现用账套写年度；停在多份账套的单 CC 报候选不报默认账套；CC 推完照实报同组跟着走的张数；推送后不闪原始原因码。
- **CC 回复与标题按界面语言**（`pearnly/cc-title-language`，并入第 3 期后一起验收）：每条用户消息附界面语言标记，CC 按它回复、侧栏标题按它起；消息里夹的外文专名不改变语言，整条换语言或明确要求时才跟用户。
- **CC 桌面版 1.9.0**：账套历史 v3（带入账方式，读 STMAS / STCRD），自动推只派给 1.8.0 以上；发版顺序先服务端后桌面版。

## 验证

- 测试机 lab（testcowork 切测试机，实时源码，镜像为生产 1d1e0366）：B3–B7 集成 `pearnly/batch-1019` 8caf84b7 验收；第 3 期 b475de61 → 0c795a1d → 8cd19261 → 29890a1b → f73bf44a → 90486924 → 5fefe6cd 逐轮复测，最后 5fefe6cd 全部通过（测试窗口记录 `~/pearnly-devtools/test-window/round-p3/第3期-测试窗口结果.md`）。识别花费今天约 0.75 美元（累计 11.87，额度 50）。
- 没测到、上线后或单测覆盖：档位放开后的自动推送；提议行「各项做法」那组（复制的测试单没有备好的选择）；主档没读过的账套显示码；CC 改选现用账套；网页端与手机 / 平板真手指的 B3–B7 各项（用户已粗看）。
- 本地全闸门 105 步、分支 Development checks 过；共用层改动（标题与回复语言）在生产镜像上跑完整候选离线检查过。
- 构建 run 39218445596（offline / DMS flow / DMS snapshot 三车道全过）→ `sha256:bcb5e240bb75b88dfaa584911fc0d6456f37cba0ade0421c59e770a38ad63b8e`；stage run 39219242587 成功。

## 部署（服务器 UTC 07:57–08:08，泰国时间 14:57–15:08）

- 脚本 `/opt/pearnly/state/deploy-5fefe6cd.sh`（照 deploy-4090d01f.sh），日志 `deploy-5fefe6cd.log`，内存采样 `mem-5fefe6cd.log`。
- 部署工具同步后「Deploy tooling matches this image」；ERP 07:59:59、Cowork 08:03:22、DMS 08:05:56 各「activated with verified image」，基础层「Foundation activated」，三产品 clear-cache，08:07:59 完成。
- 可用内存 28.1 GB → 最低 25.8 GB → 26.0 GB；swap 4.2 GB → 0.5 GB；非生产容器前后都是 0。

## 回读

- 24 个应用容器全是新镜像；三个 ocr-worker `FRAPPE_BACKGROUND_WORKERS_NOFORK=1`。
- 六站 maintenance_mode=0，`pearnly_ocr_engine` 都是 claude，原件都在 oracle；两个 Cowork 站点第 3 期新表 Cowork Express Choice Change 在。
- 部署后 Error Log：erpcowork 6 条，都是 15:00（泰国）Cowork 激活维护期间 `Scheduled jobs can't run in maintenance mode`，属预期；其余站点 0。
- ERP 组两项原件配置仍在；`pearnly-binlog-ship.timer` active、最近一次 success；mariadb 1.0 GiB / 8 GiB。
- 六站 `/api/method/ping` 与 fatcc.com 200；生产对话页入口 `index.BzCdGuxW.js` / `index.CzOYH3AS.css`（与测试机验收同一构建），样式含 B7 的 `--cc-press-scale`。

## CC 桌面版 1.9.0

- 测试通道 run 39221998111（1.9.0 / 5fefe6cd，sha256 49c4dafa…）装到测试机（skin3 普通权限静默安装，任务与安装包已删），连生产 testcowork：账套历史交回 v3。
- E8 大账套读 STCRD 耗时：测试机能读的近期账套 STCRD 都很小（NIPA 0 MB），138 MB 的 T8NLT 是 2565 年旧账套、近 12 个月没单据会直接跳过扫描。改在本机用 1.9.0 同一函数、dbf 0.99.11 对它的 STCRD（34.7 万行）逼全表扫描：两遍各约 1.5 秒（Apple M5 Pro）；按测试机 i5-1135G7 慢 3–4 倍估 5–8 秒，加局域网复制 138 MB 约 2–12 秒，远低于请求时限（900 秒减 120 秒保留）。测试机上请求读取时 CC 进程 CPU 约 0%、内存 33 MB。本机副本已删。
- 正式发布：推标签 `companion-v1.9.0`，run 39224707590 成功；`latest.json` 回读 1.9.0、source 5fefe6cd、sha256 31fe0b6f…、size 152895635、published_at 08:49:04Z。测试机装的是同版本号的测试包，不会再自动更新，留着。

## 清理

- lab：本轮测试单 20 张经删除服务删掉（连原件），测试定时任务「第3期测试凭条（可删）」删掉；测试机 NIPA 语料下 `_p3-intake`（11 张测试凭条与结果工作簿）删掉；testcowork lab-stop 切回生产、引擎键与甲骨文变量已删，生产 testcowork 引擎回读 claude；测试机 CC 重启后连生产。
