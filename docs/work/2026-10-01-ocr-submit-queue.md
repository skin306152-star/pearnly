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

## 3.1-flash-lite 被谷歌限流时改用 3.8-flash（压测窗口转用户定，2026-10-01）

- 生产压测：3.1-flash-lite 在 global 约 32 并发开始 429（52 次）；3.8-flash 在 global 实测 850 次每分钟才有 4% 的 429。
  用户目标是几百人同时用、识别不排队不失败，定为不降并发（budget 保持 32 / 500），改成限流时换模型。
- 改法：用 3.1-flash-lite 的调用（ERP 采购 / 销售、DMS 经 `pearnly_core.ocr.json_from_images`；Cowork 录入经
  `ocr/providers/client.model_call`）先不原地等地发一次；谷歌回 429 / 503（`provider_*`）→ 这一页立刻改用 3.8-flash
  （思考档按 3.8 默认 LOW，调用方要求的更高档照旧），3.8 也被限流才走原有退让；自家额度一时拿不到照旧原地等 3.1。
  续跑时先查 3.8 的检查点，已转 3.8 读完的直接取回，不再调 3.1。
- 成本：只在限流时多花，每页按 3.8 价（抽取约是 3.1-lite 的 3 倍）。平时仍用 3.1-lite。
- ERP 记录上的 `ocr_model` 仍写 3.1-lite（按调用方传入的模型记），实际换用 3.8 的次数看服务器日志「rate limited; reading with」。
- 验证：`pearnly_core.execution.test_lite_fallback` 4 条、`tests/test_cowork_lite_fallback.py` 4 条；ERP `test_line_intake` 82 条、
  Cowork 离线测试全过（除两条镜像缺 `docx`）。
