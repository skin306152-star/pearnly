# 生产部署 df2d4cdf（CC 新建任务跟着公司走、添加公司按 ERP 分标签、设置电脑列表间距、电脑登记与心跳写者锁重试）

- 依据：用户 10-20「做了吧」（交接里的三项小活 + 新建任务流程重新设计）、「保存测试任务验完删，接着做并发复测和识别闸门，过了按正常流程上线」；按批次自主部署授权（记忆 autonomous-batch-deploy）。
- 窗口协调：「消息含义理解」同意顺序（甲骨文闸门 → 重发 → 生产部署），提醒对照样本、花费上限、重发前记旧镜像、回读五点与 swap；识别那项没过闸门，甲骨文没重发。
- 上一版生产：`23471f87`（镜像 dd360538，10-20 04:40）。本批 `1c98ffa0..df2d4cdf` 14 个提交（11 个非合并），集成分支 `pearnly/batch-1020b`。

## 内容

- **新建定时任务跟着公司走**（`pearnly/task-company-first`，用户 10-20：「选了公司又要选目标 ERP 再选公司账套，不合理」）：选完公司，表单只读摆出公司与「目标 ERP · 本月账套」，不再有 MR.ERP / Express 卡片与两级账套；ERP 没连上时写状态、给「连接」；几个分店各有账套时只多一个「分店」；已跟着公司走的旧任务编辑时同样。公司还没有账套、没记公司的旧任务照旧能选。服务端本来就按所选账套记下公司与分店、推送时按单据日期选这家公司的账套（`folder_intake.follow`），没改。
- **添加公司按 ERP 分标签**（同分支，用户 10-20：「添加公司没有按 ERP 分类」）：候选里有两种以上 ERP 时搜索框下摆 MR.ERP / Express 标签（`.cc-tabs`），只列选中的那种，默认停在候选第一行那种；分了标签时整窗定在上限高，切标签不跳（lab 点验时发现后修）。新建任务先选公司用的是同一个对话框。
- **设置「电脑」列表间距**（`pearnly/cc-device-spacing`，测试窗口 round-e2「每台一节，节间空 32」）：设置页各行统一 `.cc-settings-row`，电脑一台一行、行间细线；以前授权的文件夹行与其他行同高。记录 `docs/work/2026-10-17-cc-frontend-standards.md` 第 11 节。
- **电脑登记与心跳写者锁重试**（`pearnly/register-lock-retry`，记录 `docs/work/2026-10-15-multi-desktop-build.md` 末两节）：共用 `pearnly_cowork.transaction.conflicts`（从 push 搬出，push 保留入口），register / 移除电脑、心跳、领单 / 回执、认电脑、机器认证、按需请求六处统一整笔回滚 + 退避重来 4 次（原各自三次不等，按需请求原来没有）；register 锁连接行之前先结束读视图（撞 1020 的根因）；重来时一并还原 `error_message`（第一版漏了，会把配对码带进心跳认证的回复，实验室脚本查出后修）；两个队列实验室脚本的夹具补齐。

## 没进本批

- **识别阶段副本页不升 Opus**（`pearnly/ocr-copy-pages`）：甲骨文闸门（`GATE_LOCAL_ENGINE=1`，24 份，1.52 美元）没过——6901074（海外发票 + 银行汇款单，汇款单印着同一单号、金额、买方税号）被当成副本没升，读成转账凭条、不录入。结论与下次的改法写在 `docs/work/2026-10-16-da-phase3-design.md` 末尾；甲骨文 claude-ocr 没重发。

## 验证

- 测试机 lab（testcowork 切测试机，实时源码 + 本地构建前端，底座生产镜像 dd360538）：添加公司两种 ERP 分标签、切标签不跳；新建任务选公司后只读公司与目标、分店切换目标跟着换；保存一条测试任务，站点里记下公司 นิภา เอ็กเช้นจ์、分店 00012，界面删除；设置「连接」电脑一台一行。lab 前端入口哈希与本机构建一致。
- 并发复测（本机 `pearnly-multidesk-lab`，3 租户 × 10 台，240 秒，与改动前代码交替对照）：register 500 每轮 2 次 → 0，register 撞 1020 每轮 9–15 次 → 0–1 次；死锁与领单 p95 与对照同一量级（死锁来自推送账本领单时归属行共享锁升排它锁，改动前同样有，未改）；三家都没有重复交付、重复成功、被别人的电脑写；heartbeat / queue-http / queue-ack-http 实验室脚本通过（credentials 那 3 项是旧口径断言，对照同样）。
- 本地全闸门 110 步、分支 Development checks（run 39382345659）过；本批没改共用层 Python，未另跑候选离线（构建里的候选三车道照常全过）。
- 构建 run 39382762666 → `sha256:1d71f44233847452fa9bfc9703d2015b0bcaf0729484d780090ecdc2a90c06d6`；stage run 39383421192 成功。

## 部署（服务器 UTC 06:57–07:09，泰国时间 13:57–14:09）

- 脚本 `/opt/pearnly/state/deploy-df2d4cdf.sh`（照 deploy-23471f87.sh），日志 `deploy-df2d4cdf.log`。
- 部署工具同步一致；ERP、Cowork、DMS 各「activated with verified image」，基础层「Foundation activated」，07:09:32 完成。
- 可用内存 28.4 GB → 最低 26.9 GB → 26.1 GB；swap 3.9 GB → 0.58 GB；非生产容器前后都是 0。

## 回读

- 24 个应用容器都是新镜像 1d71f4423384；三个 ocr-worker `FRAPPE_BACKGROUND_WORKERS_NOFORK=1`。
- 六站 maintenance_mode=0、`pearnly_ocr_engine` 都是 claude、原件都在 oracle；部署后 Error Log 六站都是 0；没有维护容器残留。
- ERP 组两项原件配置仍在；`pearnly-binlog-ship.timer` active、最近一次 success（07:12:16）；mariadb 1.09 GiB / 8 GiB。
- 六站 `/api/method/ping` 与 fatcc.com 200；生产对话页入口 `index.NwjUt6-j.js` / `index.DzYRKMZs.css`，与测试机验收的构建一致。

## 清理

- lab：引擎键与甲骨文变量已删，lab-stop（域名回落生产通配记录、生产 testcowork 定时任务恢复、栈停、卷保留）；测试机 CC 换回生产那份配置，心跳成功（账套 679 个）。
- 本机并发实验栈 `pearnly-multidesk-lab` 连数据卷删掉；甲骨文闸门结果放在 `/opt/pearnly-claude-eval/runs-prod/copypages-1020/`，旧结果放回原处。
