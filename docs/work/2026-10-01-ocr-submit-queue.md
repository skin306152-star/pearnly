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
