# 工作包：Claude 识别引擎（可按租户切换）

## 目标和范围

- 目标：Cowork 识别新增 Claude（Anthropic）供应商与「只读一遍」引擎档位，开户后台按部署切换，默认保持现状。设计见 [ADR-017](../adr/0017-ocr-engine-claude.md)。
- 完成条件：
  1. 谷歌路径行为与提示词不变，现有识别测试全过。
  2. Claude 档位在站点本机与 Cloud Run 两种执行位置都能读、检查点防重付费、错误语义与谷歌一致，有单元测试。
  3. 开户后台能切换，站点步骤写对配置；缺 Claude 连接时拒绝。
  4. 测试环境（testcowork）用聚合平台密钥真读通过，交用户试用。
- 本次不做：ERP / DMS；客户计费单价；生产正式密钥与部署（另行按发布流程）。
- 本次范围状态：第一、二、三步实现完成（选 Claude 就全部走 Claude），等用户压测。收尾状态：施工中。
- 分支 / 基线 / 工作树 / 集成目标：`pearnly/claude-ocr-engine` / `63e2c712`（10-07 变基到 `a2443248`）/ `/Users/skin/pearnly-wt-claude-ocr` / `main`。
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

## 变更与决定（第二步：对账文档抽取）

- `enterprise.request.build_request(model=)`、`pipeline.run(model=, max_rereads=)` / `first_pass(model=)`：不给照旧 3.8、最多再读 10 页。
- `engine.config.document_reading(environment)`：Claude 档位 → `(claude-sonnet-5-5, 0)`；`recon_reading.read_document` /
  `warm_document` 按受理冻结的识别环境取；调用方 `recon_center/document_ocr` 与 Cloud Run `remote.runner._document` / 预热传入环境与 `claude_target`。
- 适配层：请求带结果格式时用官方结构化输出（`output_config.format = json_schema`）。
- 远程：`contract.document_request(anthropic=)` 校验非密连接形状；`forward.send_document` 带上；`tasks.pool_of` 对文档同样分到 Claude 池。

## 变更与决定（第三步：选 Claude 就全部走 Claude，用户 2026-10-07）

- 发票：`config_from_environment` 在 Claude 档位把 `layer2_model` 设成 Claude、直读打开、直读上限 50 页；`run_invoice`
  只读一遍时不管有没有文字层都看图直读。表格（Word / Excel）文字交给 Claude。
- 对账文档：`document_reading` 回 `DocumentReading(model, max_rereads, transcribe)`；Claude = `(claude, 0, False)`：
  `pipeline.run(transcribe=False)` 不调 Document AI、不本地拼行、每页只看图；`first_pass` 读字阶段不发调用。
  电子账单的文字层本地拼行（`jobs.py` → `text_layer.read`，不花钱）保留。
- 看板：不读字时抽完一页也算读到这页（`document_ocr` 两处）。
- 成本：`cost.response_cost_thb` / `claude_usage_usd` 按 Claude 回复的分项用量（未缓存、读缓存 0.1 倍、写缓存 1.25 倍、输出）算实价；
  `calls_cost_thb` 与对账管线都用它。谷歌照旧按 token 估。

## 验证

| 时间 | 代码 | 环境 | 场景 | 预期 / 实际 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 10-07 | 未提交差异（基线 63e2c712） | 本机 Docker，生产镜像 4e80514d（10-02），`--network none`，源码以 PYTHONPATH 覆盖 | 现有识别测试：engine 56、invoice_policy 7、remote_ocr 69、ocr_tasks 44、provider_runtime 33、remote_forward 17、provider_work_guard 5、recon_reread 21 | 谷歌路径不变 / 全部 OK | 通过 |
| 10-07 | 同上 | 同上 | 新增 `test_cowork_claude_engine.py` 30、`test_control_ocr_engine.py` 7；`test_line_channel_setup.py` 16、`test_cloud_onboarding.py` 17 | 全部 OK | 通过 |
| 10-07 | 同上 | 本机 python3.14 / 系统 python3 | `development.yml` 全部 python 闸门（含 `check-architecture.py` 0 违规、`i18n-build.py --check`、site-step / provision-agent 测试） | 全部通过（4 个在系统 3.9 上因类型语法报错，用 3.14 重跑通过，与改动无关） | 通过 |
| 10-07 | 同上 | 本机与 main 对比 | `check-i18n.py --strict` | 与 main 同样失败、各 App 未译条数一致（存量，非本次引入） | 存量 |
| 10-07 | 3356ee71 | 测试机一次性容器（生产镜像 03151bec，不连站点 / 测试栈），OpenRouter 测试密钥 | 真实渲染 + 完整引擎 + Claude 适配层读 5 份语料（照片、扫描、电子 PDF、电费单、糊照片） | 每份 1 次 Claude 调用、0 次谷歌；4 份全对；糊照片税号错一位被校验位拦下 → 需复核（`single_read_unchecked`） | 通过 |
| 10-07 | 2582fb3e（变基到 a2443248） | 测试机 Cowork 测试栈实时源码，testcowork（另一窗口的文件夹录入也在这份上）；站点配置改：OCR_ENGINE=claude、pearnly_anthropic（OpenRouter）、回放关；转发 Cloud Run 保持关 | 真实入口 `files.create` → `ocr.submit` → 后台识别 → 入库，2 份（照片税票、扫描税票） | 两份 saved / confirmed、字段全对、各 1 次 `direct` claude-sonnet-5-5；提交到入库 8.1 / 12.0 秒 | 通过 |
| 10-07 | 第二步未提交差异 | 本机 Docker 生产镜像 4e80514d，`--network none` | `test_cowork_claude_engine` 34（含对账文档、站点步骤）、recon_reread 21、remote_ocr 69、ocr_tasks 44、bank_page_checks 17、recon_scanned_docs 122、recon_progress 13、document_tier 30、opening_batch 18、recon_waiting 21、recon_resume 13、recon_slots 6、recon_speed 6、bank_warnings 19、bank_day_month 7、engine 56；`check-architecture.py` | 全部 OK / 0 违规 | 通过 |
| 10-07 | 同上 | 测试机一次性容器（生产镜像 03151bec），OpenRouter 测试密钥，只看图（不调谷歌） | 银行对账单 2 页（KBank 照片、BAY 电子 PDF）经 `pipeline._extract` + 结构化输出 | 都 `END_TURN`、合格式；43 行 0 断、27 行 1 断（交人确认）；$0.063 / $0.037，32 / 17 秒 | 通过（断点按只读一遍口径标出） |

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 第一步、第二步完成 | 批处理执行方式未做（扩展点已留） |
| 本地验证 | 通过 | 引擎来源对照（`*_source_parity.py`，需旧线上源码）未跑 |
| 测试环境 | 进行中 | 真实入口已跑通；等用户在 testcowork 试用；开户后台切换在本机控制面（测试机没有控制面），只有单元测试 |
| 部署 / 业务验收 | 未执行 | |

## 压测（2026-10-07，OpenRouter 测试密钥，共花 $7.2：用户先定 $5、后同意把余额 $3.86 用完）

- 并发：30 页发票同时发（一次性容器直连）全部 200、无限流，单页中位 7.1 秒、最慢 11.1 秒；测试用 Cloud Run 服务端单次识别请求中位 7.1 秒、
  p90 31 秒（30 秒以上是整份多页对账单）。同一批发票谷歌（生产 Cloud Run）上传到出结果中位 20.3 秒，Claude 走 Cloud Run 中位 12.4 秒。
- 质量（测试站里谷歌 10-05 读过的 50 份真实件，两边不一致的逐张看原件判）：普通字段 Claude 错约 5 格、谷歌约 10 格；Claude 更好：单号、
  海关进口单据、原件 / 副本标记、水电「通知 + 收据」不重复入账。Claude 更差：销项 / 进项税报表 3 份与预扣税证明 1 份录成票（谷歌 0，
  3 份 Claude 自己标了复核）、同页「通知 + 税票」3 张里 2 张取了通知那半、个人税号两位对调 2 张（校验位拦下转复核）。
  Claude 同一份读两次：两次都读出的 162 格 0 差异。
- 对账单（测试用 Cloud Run，一套一套跑）：B07 BAY-CA 12 页 490 笔、B08 56 笔、B04 48 笔（含 Claude 读的电子总账 49 行）全部
  「期初 + 存入 − 支出 = 印出的期末」；B01 18 张照片 17 张读出（41–45 笔 / 张），1 张因测试账户余额不足被 402 拒；B05-02 与总账期初一致。
- 每页实价（含 Cloud Run 约 ฿0.01）：发票 Claude ฿0.39–0.43、谷歌今年 ฿0.156–0.17；对账单 Claude 约 ฿1.1–1.3、谷歌平均 ฿0.356。
- 402：OpenRouter 按每次请求的输出上限先冻结余额（对账页 16384 token ≈ $0.17、发票 8192 ≈ $0.08），余额低、并发高时被拒，被拒不扣费。
- **测试机网络**：测试站跑在 WSL（networkingMode=mirrored）里的 docker 容器中，容器对外请求约 1% 卡 15–30 秒或被断（20 分钟 7600 次中 72 次），
  同时段 Windows 本机 1304 次 0 失败；限定 WSL 端口段（52810–56905）不改善；办公室 IPv6 不通（120/120 失败），只影响先试 IPv6 的程序。
  所以测试机本机读时的「结果不明」/ 读超时多是这一层造成，不是产品或 OpenRouter；生产（普通 Linux + Cloud Run）没有这一层。
  测试机直连 OpenRouter 的大量卡死与早先「Wi-Fi 断流」的判断已被这组对照推翻。

## 测试环境占用与恢复（收尾必须做）

testcowork 测试副本与另一窗口（文件夹自动录入）共用，本包改过的站点配置收尾时恢复：
- `pearnly_ocr_replay` 恢复为 `{"enabled": 1, "profile": "normal"}`（另一窗口的临时设置）；
- 删 `pearnly_cowork_ocr_environment.OCR_ENGINE` 与 `pearnly_anthropic`；
- 通知「Cowork单据自动化识别推送」窗口；`pearnly_cowork_ocr_remote`、`pearnly_cowork_folder_intake`、lab-stop 由它恢复。
