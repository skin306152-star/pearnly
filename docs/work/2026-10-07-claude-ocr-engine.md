# 工作包：Claude 识别引擎（可按租户切换）

## 目标和范围

- 目标：Cowork 识别新增 Claude（Anthropic）供应商与「只读一遍」引擎档位，开户后台按部署切换，默认保持现状。设计见 [ADR-017](../adr/0017-ocr-engine-claude.md)。
- 完成条件：
  1. 谷歌路径行为与提示词不变，现有识别测试全过。
  2. Claude 档位在站点本机与 Cloud Run 两种执行位置都能读、检查点防重付费、错误语义与谷歌一致，有单元测试。
  3. 开户后台能切换，站点步骤写对配置；缺 Claude 连接时拒绝。
  4. 测试环境（testcowork）用聚合平台密钥真读通过，交用户试用。
- 本次不做：ERP / DMS；客户计费单价；生产正式密钥与部署（另行按发布流程）。
- 本次范围状态：进行中。收尾状态：施工中。
- 分支 / 基线 / 工作树 / 集成目标：`pearnly/claude-ocr-engine` / `63e2c712` / `/Users/skin/pearnly-wt-claude-ocr` / `main`。
- 并行窗口：Cowork「文件夹自动录入」（`pearnly/cowork-folder-intake`，改 `folder_intake/`、companion、`hooks.py`、翻译、架构说明）。
  本包只动 `ocr/`（providers、engine、execution/settings、remote）、`pearnly_control` 的部署与开户步骤、站点步骤脚本、Cloud Run 部署脚本、
  测试与本记录 / ADR；共用文件（`hooks.py`、`messages.tsv`、翻译、`architecture.md`）尽量不碰，碰了合并前变基复核。
- 外部操作授权：测试期用旧项目聚合平台（OpenRouter）密钥（用户 2026-10-07 同意，付费小样本已花 $1.43，剩 $8.01）；
  生产正式密钥、Cloud Run 密钥、生产部署未授权，按发布流程另报。

## 依据（2026-10-07 小样本，测试机 `C:\PearnlyStress\_work\cl\`）

- 发票类 76 份（真票约 51 张）只读一遍：字段错 3 个（一张 680px 糊照片税号错一位 + 买方名；一张多税基货代票未税额）；
  非票据 13 份全判对；多行税报 / 销项汇总按生产第 11 条标成副本（与 3.8 同口径）。对账单 14 页约 390 行余额链 0 断。
- 每页：发票 $0.0119（≈฿0.39）、中位 5.9 秒；对账单 $0.0325（≈฿1.07）。现行对账单平均 ฿0.356（m8 实算）。
- OpenRouter 支持 Anthropic Messages 原生格式（`/api/v1/messages`），测试期与生产同一套代码，只换入口、认证方式、模型名与密钥。

## 变更与决定（第一步：发票录入与销项税核查）

- 供应商适配层 `ocr/providers/anthropic.py`：组请求 `build_payload` / 解析 `parse_response` 为纯函数（以后批处理复用），
  `AnthropicClient.model_call` 发出；错误语义、检查点、结果不明重发与 `ProviderClient` 一致；检查点身份带入口主机。
- 路由 `ocr/providers/route.py`：`claude-` 模型交给 Claude，其余与读字 / Document AI 照旧谷歌。站点本机
  `providers/core.build_client(..., claude_target=)` 与 Cloud Run `remote/runner.build_client(..., claude_target=)` 同一路由。
- 引擎：`EngineConfig.single_read`；`config.OCR_ENGINE`（gemini / claude，`engine_of` 校验）；`runner.single_page`
  （直读一次 → 检查闸 → 没过只标红 `single_read_unchecked:`、不复核 / 不叫 Vision / 写满上限不重读）；提示词 Claude 与 3.8 同一份。
- 受理冻结：`execution/settings` 允许 `OCR_ENGINE`；Claude 档位冻结 `anthropic`（非密），`revalidate` 核对；没配连接不受理。
  调用方 `execution/workflow`、`recon_center/invoice_ocr` 传 `claude_target`；`remote/forward` → `contract.request(anthropic=)`。
- Cloud Run：环境变量 `ANTHROPIC_CONFIG`（Secret Manager），与请求带来的非密连接核对；`model_name` 认 `claude-`；
  Cloud Tasks 新增 `claude-sonnet-5-5` 派发池（下限 24）；部署脚本 `PEARNLY_ANTHROPIC_SECRET` 设了才挂。
- 连接与密钥：站点配置 `pearnly_anthropic`，开户步骤从服务器 `runtime.env` 的 `PEARNLY_ANTHROPIC_CONFIG` 写进 Cowork 组
  `common_site_config`（与 LINE / 计费凭据同一做法，不新增挂载）。
- 开户后台：`Pearnly Deployment.ocr_engine`（Cowork 才显示，默认 gemini）；改了就 `queue_ocr_engine` 带 `engine` + `restart`
  重排 `set_ocr`；开通计划也带上；站点步骤 `ocr_environment` 写「组默认 + 本站覆盖 + OCR_ENGINE」，选 Claude 没连接就停下提示。
- 计量：`engine.cost` 增加 Sonnet 5.5 价目（估算口径）。
- 文档：[ADR-017](../adr/0017-ocr-engine-claude.md)（含扩展点：一家供应商一个适配器、批处理复用组请求 / 解析、按功能选执行方式）、
  ADR 索引、`architecture.md` OCR 段一句。
- 第二步（对账文档抽取换 Claude）未做：读法带 Document AI 原文与严格结果格式，先小样本验证再接。

## 验证

| 时间 | 代码 | 环境 | 场景 | 预期 / 实际 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 10-07 | 未提交差异（基线 63e2c712） | 本机 Docker，生产镜像 4e80514d（10-02），`--network none`，源码以 PYTHONPATH 覆盖 | 现有识别测试：engine 56、invoice_policy 7、remote_ocr 69、ocr_tasks 44、provider_runtime 33、remote_forward 17、provider_work_guard 5、recon_reread 21 | 谷歌路径不变 / 全部 OK | 通过 |
| 10-07 | 同上 | 同上 | 新增 `test_cowork_claude_engine.py` 30、`test_control_ocr_engine.py` 7；`test_line_channel_setup.py` 16、`test_cloud_onboarding.py` 17 | 全部 OK | 通过 |
| 10-07 | 同上 | 本机 python3.14 / 系统 python3 | `development.yml` 全部 python 闸门（含 `check-architecture.py` 0 违规、`i18n-build.py --check`、site-step / provision-agent 测试） | 全部通过（4 个在系统 3.9 上因类型语法报错，用 3.14 重跑通过，与改动无关） | 通过 |
| 10-07 | 同上 | 本机与 main 对比 | `check-i18n.py --strict` | 与 main 同样失败、各 App 未译条数一致（存量，非本次引入） | 存量 |

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 第一步完成 | 对账文档（第二步）未做 |
| 本地验证 | 通过 | 引擎来源对照（`*_source_parity.py`，需旧线上源码）未跑 |
| 测试环境 | 未执行 | |
| 部署 / 业务验收 | 未执行 | |
