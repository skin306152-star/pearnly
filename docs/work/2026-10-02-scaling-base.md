# 2026-10-02 扩容基础（零花费）

分支 `codex/scaling-base`，基线 `b078e796`。来源：压测窗口转述的用户要求“我不想未来扩张又要重构”。

## 做了什么

1. 数据库 / Redis 地址可配置（`a4a25b02`）：`PEARNLY_DB_HOST`、`PEARNLY_DB_PORT`、`PEARNLY_REDIS_CACHE_URL`、
   `PEARNLY_REDIS_QUEUE_URL`，默认值与现状相同；compose、Keycloak、`site-step.py`、`bootstrap-billing.py` 都读它们。
2. 配置对照表的公式版 `infrastructure/production/sizing.py`：按核数与内存算进程、线程、内存上限、缓冲池、连接上限、
   MariaDB 内存；部署时写 `/opt/pearnly/state/sizing.env`，compose 先读它再读 `runtime.env`（写死的仍以 `runtime.env` 为准）；
   算不出就写空文件，行为与之前相同。接入点：`activate-product.sh`、`activate-foundation.sh`（7 处 compose）、
   `capacity.py check()`、`provision-agent.py compose()`。
3. 对照表、最小支持机器、扩容后 100 / 300 人压测规则、可拆分自检两级清单：`docs/development/production-capacity.md`。

## 不改变现值

生产 `runtime.env` 把这些参数都写死了；公式在 8 核 / 32095 MiB 上与之逐项相同（`check-production-capacity.py` 守住）。

## 验证（本地）

- `check-production-capacity.py`：30 项通过（含公式 = 生产、8/32 · 12/48 · 16/64 · 32/128 过容量检查、4/16 被拒绝、数据库 / Redis 地址断言）
- `test-activate-product.py`、`test-provision-agent.py`、`test-site-step-capacity.py`、`test-site-step-webhook.py`：OK
- `check-architecture.py`：PASS

## 未做 / 下一步

- 随下一批（redis-state、offsite-backup、识别窗口索引补丁）一起集成、构建、激活；激活后回读 `/opt/pearnly/state/sizing.env` 与容器上限未变。
- “扩容前必须改”的四项（sites 共享文件系统、多机部署工具、Redis 认证、数据库跨机访问）等实际扩容时做，届时跑 100 / 300 人压测。
