# 生产容量可调（2026-09-30）

- 分支：`codex/prod-capacity-knobs`，基线 `16373bff`。
- 目的：用户要求压测服务器并发极限后"定一个数值、留安全空间"。先把能调的量从写死改成 runtime.env 可调，
  **默认值全部等于原值，上线不改行为**；数值由压测窗口测完再定。
- 分工（与「旧项目迁移后的用户体验问题」窗口 2026-09-30 约定）：它改共用层 `pearnly_core/execution`、
  各 OCR 客户端和 Document AI 三区域切换；本任务只动 compose、容量检查和开户配置下发，不碰那些文件。两批一起上线。

## 改了什么

| 位置 | 变化 |
|---|---|
| `infrastructure/production/compose.yml` | 识别进程（三产品）、Cowork 对账进程、网页进程（三产品）的进程数与内存上限读 runtime.env，变量见文件头「可调容量」。计费后台不变。 |
| `infrastructure/production/capacity.py` | 启动前按每个进程的最低内存把关（识别 512 MiB、对账 768 MiB、网页 256 MiB，按原值折算）；只加进程不加内存会被拒绝。总量仍须装得下宿主。 |
| `infrastructure/production/site-step.py` | `pearnly_ocr_budget` 读 `PEARNLY_OCR_BUDGET`（JSON，只覆盖给出的键，默认 `{"concurrency": 8, "tenant_concurrency": 3, "rpm": 120}`）；`PEARNLY_DOCUMENT_AI_REGIONS`（JSON 列表 `[{location, processor, version, rpm}]`，顺序即优先顺序）有值才写 `pearnly_document_ai_regions`，没值不写、用代码默认。 |

## 压测后怎么调（每步仍需用户同意）

1. 在 `/opt/pearnly/secrets/runtime.env` 写对应变量（进程数和内存成对改）。
2. `python3 /opt/pearnly/deploy/infrastructure/production/capacity.py --product <p>`（需 `PEARNLY_IMAGE`）先过容量检查。
3. 按 `scripts/production/activate-product.sh` 的维护流程重建该产品容器。
4. `PEARNLY_OCR_BUDGET` / `PEARNLY_DOCUMENT_AI_REGIONS` 只在跑开户步骤时写进 common_site_config；
   不开新户时要让三个产品同时生效，需对三组各跑一次写配置（`bench set-config -g ... --parse`），
   三组必须一字不差，否则 budget 报 policy_mismatch。去掉 `PEARNLY_DOCUMENT_AI_REGIONS` 不会自动删除已写的键。

## 验证

- `scripts/check-production-capacity.py`：默认值等于原值；调大（进程+内存）放行；只加进程、进程为 0 拒绝；超出宿主拒绝。
- `scripts/production/test-site-step-capacity.py`：默认限额不变、只覆盖给出的键、非法值拒绝；区域有值才下发、缺字段/重复拒绝。
- 未部署；未在生产跑。
