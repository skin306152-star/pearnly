# 录入识别提交不再「暂时满就拒收」（生产压测，2026-10-01 凌晨）

压测窗口在生产 testcowork 同时提交合成发票（每份 1 页；每产品 6 个识别进程、budget 32 / 500 / 12）：
同时 16 张有 2 张、32 张有 7 张、64 张有 10 张被 `capacity_unavailable` 拒收，64 张另有 4 张 `queue_full`。

- 原因：`capacity_unavailable` 不是额度用完，是 `pearnly_core.execution.work._submit` 抢准入锁只等 3 秒；持锁时要查库、写库、提交，
  数据库忙（约 90%）时排不上锁。`queue_full` 是每租户待处理上限 `tenant_pending`（默认 50）的硬上限。
- 改法：
  - 准入锁最多等 15 秒（`blocking_timeout` 3 → 15），持锁内容不变（仍是一次查重 + 计数 + 插入）。
  - 录入页遇到 `capacity_unavailable` / `queue_full` / `ocr_paused`：该行显示「排队中，稍后自动提交」，隔 5 秒起、逐次加长到 30 秒
    自动重交，最多 60 分钟；服务端拒收时整笔回滚，同一请求键重交安全。余额不足等其他拒收照旧显示原因。
- 不在本批：识别进程 CPU 满（需采样定位）、谷歌 3.1-flash-lite 在 32 并发附近 429（配置 / 模型区域另议）。
- 验证：Cowork 离线测试全过（除两条镜像缺 `docx`）、原生界面契约、前端 i18n 检查通过；真实并发待压测窗口复测。

## 识别进程 CPU 跑满：原因与处理（2026-10-01 凌晨，测试机实测）

- 生产压测：识别进程 6 个 CPU 556%、主机负载 15.9。测试机复现：积压任务被自家额度反复推迟、重派（每个任务 10 分钟被重跑约 20 次），
  4 个识别进程 CPU 跑满，却一张没读完。
- 单次推迟重跑实测约 1.8 秒 CPU（cProfile）：重新导入识别模块约 1.1 秒（RQ 每个任务 fork 子进程，跑完就丢）、重新向谷歌换令牌约
  0.6 秒；渲染、方向识别只占零头。刚 fork 1 秒的子进程 CPU 就有 69%–74%。
- 处理：三个识别容器（dms / erp / cowork-ocr-worker）加 Frappe 官方开关 `FRAPPE_BACKGROUND_WORKERS_NOFORK=1`，worker-pool 里的进程
  直接执行任务、不 fork（Frappe 每 5000 个任务自动重启进程；崩溃由 pool 拉起）。同一批反复推迟的任务各测 2 分钟：
  CPU 平均 46% → 3%、最高 217% → 20%，每个任务约 0.84 → 0.09 秒 CPU。`check-production-capacity.py` 断言只有这三个容器带此开关。
  「LINE 绑定无反应」窗口同意由本分支改 compose；容量不用调（压测时 cowork-ocr-worker 6 进程峰值 921MiB / 3GiB）。
- 「续跑不重新渲染」不做：实测大头不在渲染，关掉 fork 后每次重跑只剩约 0.09 秒 CPU。
- 页面轮询改推送：识别任务有结论时共用层 `work._finish` 推 `pearnly_ocr_work` 给提交人；录入页收到立刻读结果，轮询 1.5 → 5 秒兜底
  （按页进度 5 秒一刷）；对账进行中看板 1.5 → 5 秒，对账结束的推送（`cowork_recon_finished`）立刻叫醒它。
