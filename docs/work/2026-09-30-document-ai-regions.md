# Document AI 三区域切换与限流计数（2026-09-30）

用户（经压测窗口转达）：「谷歌限流基本解决了，但代码要灵活：遇到限流马上转区域，不要原地重试，三个区域灵活调用，所有 OCR 入口都要。」
分支 `codex/ocr-region-failover`，基线 main `16373bff`。共用层与识别调度由本窗口改；compose / runtime.env / 容量检查 / site-step
下发由「LINE 绑定无反应」窗口在 `codex/prod-capacity-knobs` 改（两边文件不重叠），一起上线。

## 范围

- Document AI 只在 Cowork 企业档读字里用（对账账单 / 总账 / 税报扫描件，`ocr/providers/client.document_ai_call`）；ERP、DMS、
  Cowork 录入走 Vertex（已是 global）与 Vision（global），没有 Document AI。Vertex / Vision 遇 429 照旧按共用层退让。
- 三个区域（同一项目、同一份凭据），按顺序：新加坡 asia-southeast1 / 6c7dfffac937fcd9 / v2.1.1 / 每分钟 10（已申请 60）；
  孟买 asia-south1 / 51ea584b4184e9d7 / v2.1.1 / 10（已申请 30）；美国 us / 2a5bdf2779fcc187 / pretrained-ocr-v2.1-2024-08-07
  （没有 v2.1.1）/ 120。
- 站点配置 `pearnly_document_ai_regions`（common_site_config，列表 [{location, processor, version, rpm}]，顺序即优先顺序）可整体替换；
  格式不对整体回到默认。没有列表、但旧的 `ENTERPRISE_OCR_LOCATION` / `_PROCESSOR_ID` 指定了处理器时只用那一个（兼容）。

## 做法

- 每个区域一个额度桶：`budget.permit(bucket, timeout, rpm=…)` 让桶带自己的每分钟上限；并发上限照旧全局。
- `provider.post(..., wait=False)`：拿不到额度 / 被 429 不原地等，Deferred 直接交回。`document_ai_call` 按顺序试区域：对方 429 / 503 /
  额度用完 / 5xx（`provider_*`）或该区域桶还没轮到（`capacity`）→ 立刻换下一个；三个都不行，识别任务里按最短冷却原地等一次
  （单次 ≤20 秒、累计 ≤60 秒，与共用层同口径）再从头试，网页请求里直接交回。额度服务连不上 / 配置不一致不换区域。
- 换区域后续跑：先查各区域的检查点，已在别的区域读完的这一块直接取回，不再付费。结果不明（读超时）照旧在原区域按原规则重发。
- 限流看得见：`budget.note` 按分钟计数（`deferred:<原因>|<标签>`、`http_429|…`、`waited_seconds|…`），Cowork 的标签如
  `documentai:asia-southeast1`、`vertex:global`，ERP / DMS 的调用记为 `other`；
  `bench --site <站点> execute pearnly_core.execution.budget.stats --kwargs '{"minutes": 60}'` 查看。只作观察，写不进不影响调用。

## 验证

- 离线：`tests/test_cowork_document_ai_regions.py` 9 条、`pearnly_core.execution.test_region_budget` 3 条；原有 `test_provider_wait` /
  `test_permit_lease`、Cowork 离线测试全过（除两条镜像缺 `docx` 的导入错误）；`test_a_server_error_on_a_reconciliation_read_is_retried_later`
  改为三个区域各试一次。
- 未验：真实三区域调用（美国 v2.1 与 v2.1.1 读字差异由压测窗口用银行扫描件对照真账验收）、生产并发压测。
