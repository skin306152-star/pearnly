# 需求影响评估：CC 对话用量与费用记账

> 状态：主会话 11-11 审定通过，已施工（见文末「审定与施工」）。基线 `9ba528f45`，分支 `pearnly/cc-usage-cost-39ef88`。

## 0. 需求与范围

- 用户 11-11 同意主会话的建议：「每段 CC 对话把用了多少、花了多少记下来，以后能按功能、按客户算清楚成本。改动小，不加任何费用」。
- 范围：站点里所有调 Anthropic 的地方各记一笔进同一本账——CC 对话（Managed Agents 会话）、侧栏标题概括、代录 AI 接手、Claude 识别。
- 不做：用户可见的菜单 / 页面 / 报表入口；预算拦截；改模型、改提示词、改计价。

## 1. 现状（实测代码）

- CC 对话走 Managed Agents（`assistant/engines/anthropic_managed_agents.py`），只有转发线程看到 `span.model_request_end` 时记一个「上下文大小」（`context.note`），不记费用；转发线程不在时（进程重启、页面关着由引擎回调跑工具、超 15 分钟）事件根本看不到——**按事件逐次累加会漏**。
- 官方会话对象自带 `usage`：累计输入 / 输出 / 缓存读写 token、`active_seconds`、`list_cost`（Anthropic 按牌价算好的美元，含会话运行费 $0.08/小时 和 Haiku 超 10 万的分档价）。这是权威数，读一次就有，不怕漏事件。
- 标题概括：`summarize` 单次 `messages.create`（Haiku），返回里有 `usage`，现在丢掉了。
- 代录 AI 接手：`express_operator/ai.py` 已算每次美元，只进日志和 Redis 预算。
- 识别：`claude_ocr/client.py` 拿到每次 `usd`，Cowork 存在识别结果的 `estimated_cost_thb`、ERP 存在结果的 `claude.usd`，各存各的，没有统一账。
- 价目表：`claude_ocr/engine.PRICE`（Haiku/Opus 两行），`express_operator/ai.py` 抄了一份并有测试比对。

## 2. 方案（推荐）

- **新 DocType `Pearnly AI Usage`**（pearnly_core，原生 DocType + 索引，不加工作区 / 菜单，只给 System Manager 读）。每行：
  日期（站点时区，按日汇总）、用途（`cc_chat` / `cc_title` / `operator` / `ocr`）、产品（cowork / erp，取站点产品）、租户（`pearnly_tenant`，缺省站点名）、
  用户、对话编号、远端会话编号（分段）、模型、输入 / 输出 / 缓存写 / 缓存读 token、运行秒数、美元（Decimal，8 位小数）、费用来源（`list_cost` 官方 / `price_table` 本地价目）。
- **CC 对话：每段远端会话每天一行，存「当天增量」**。每轮结束（转发线程收线、引擎回调 `webhook` 跑完、读事件补拉时）排一个**后台任务**读一次 `sessions.retrieve().usage`，
  用「累计快照 − 这段会话已记各行之和」写进当天那行（同一行反复覆盖，幂等；漏了某一轮下一轮自然补齐）。不在转发线程里写库、不拖慢回复。
- **单次调用（标题、代录、识别）：每次调用一行**，用本地价目算美元。
- **价目表只留一份**：新建纯数据模块 `pearnly_core/ai_prices.py`（四个模型 + Haiku 分档 + 缓存写 1.25 倍），识别引擎、代录、账本都从这里读；
  删掉 `ai.py` 里那份抄件。识别引擎也部署在甲骨文，这个模块零依赖，随 `claude_ocr` 一起打包（施工时核对甲骨文打包清单）。
- **识别和评测能分开**：评测 / 回放直接打甲骨文服务，不经过站点，不进这本账。于是「Console 总额 − 全部租户账本合计」= 评测与测试脚本花费。
- **怎么查**：`bench --site <站点> execute pearnly_core.ai_usage.report --kwargs '{"days":30,"by":"day,purpose"}'`，按日 / 用途 / 产品 / 用户 / 模型汇总，美元与泰铢两列；
  加一个 `scripts/` 小脚本对所有站点跑一遍再合计（租户之间是分库的）。不加任何页面。
- **保留**：先不清理（每段对话每天 1 行，3 租户×10 人估每月几千行，可忽略）；以后要清再按月归档。

## 3. 影响与风险

- 隔离：账本在各租户自己的库里，只有 System Manager 能读，报表函数要求 System Manager。
- 性能：CC 回复路径只多「排一个后台任务」（Redis 入队，毫秒级）；后台任务里一次 `sessions.retrieve`（同 `status` 已在用的接口）。识别 / 标题 / 代录多一次插入，失败只记日志不影响业务。
- 成本：零新增外部费用、零固定月费。
- 共用件：改到 `assistant/relay.py`、`assistant/webhook.py`、`assistant/titles.py`、`engines/anthropic_managed_agents.py`、`claude_ocr/client.py`、`express_operator/ai.py`。
  A 线正在改 assistant 的写入计划和工具（`tools.py` / `toolrun.py` 一带），本方案不碰这两个文件；`relay.py` / `webhook.py` 只加一行排任务，若 A 线也在改需先协调。
- 删除：对话删除时账本**不删**（成本账要留）；只存数字和编号，不存对话内容。
- 测试：用假模型返回固定 usage 的单测（快照增量、幂等、跨天、Decimal 金额、价目分档），不跑真模型。

## 4. 要主会话定的事

1. 范围：只做 CC 对话 + 标题（最小），还是按上面四个用途一次接齐（推荐，同一本账才能按功能比）？
2. CC 对话的美元用官方 `list_cost`（含运行费、自动跟价，推荐）还是本地价目表？我两者都存 token，报表默认用官方数。
3. 价目表抽成公共模块会动识别引擎的导入（零行为变化），可以吗？还是先只给账本单独用、不动识别？

## 审定与施工（2026-11-11）

主会话定：四个用途一次接齐；CC 对话用官方 `list_cost`，token 两边都存，`usd_source` 写明来源；识别直接引用结果里已记的美元，不重算；
价目新模块只给账本和代录用，识别引擎与甲骨文 claude-ocr 不动；不加页面。

落点（与评估的一处偏差：价目放进域包 `ai_usage/prices.py`，不放根目录——架构闸门 `root_modules` 基线不许新增根模块，主会话写的
`pearnly_core/ai_prices.py` 改到这里）：

| 文件 | 做什么 |
| --- | --- |
| `pearnly_core/ai_usage/prices.py` | 四个模型价目（Decimal），Haiku 单次超 10 万的高档，1 小时缓存写 × 2 |
| `pearnly_core/ai_usage/contracts.py` | 纯函数：单次调用 → 行；识别结果 → 每模型一行（美元用结果的、合计一分不差）；会话累计 → 快照；当天增量 |
| `pearnly_core/ai_usage/ledger.py` | `record` 只排任务（`enqueue_after_commit=False`，不跟调用方事务回滚）；`write_session` 覆盖写 `cc-<远端会话>-<日期>` |
| `pearnly_core/ai_usage/report.py` | `summary(days, by, since, until)`，数据库里按组求和，美元 + 泰铢 |
| `scripts/ai-usage-total.py` | 读 `bench --site all execute ...summary` 的输出，跨站点合计 |
| `pearnly_core/assistant/usage.py` | 每轮结束排任务读 `sessions.retrieve().usage`，交 `write_session` |
| `assistant/relay.py`、`assistant/webhook.py` | 一轮结束 / 引擎回调叫醒时 `usage.request`（只排任务） |
| `assistant/titles.py`、`engines/base.py`、`engines/anthropic_managed_agents.py` | 概括调用交回 `(模型, 用量)`，记 `cc_title`；适配器加 `session_usage` |
| `express_operator/ai.py` | 每次回了结果的接手调用记 `operator`（`ref` = 批次:调用） |
| `claude_ocr/client.py` | `_charge`：真付了费的读（甲骨文 / 本机直调）记 `ocr`，检查点复用不记 |
| DocType `Pearnly AI Usage` | 只给 System Manager 读，索引 `(day, purpose)`、`(segment, day)`，无菜单 / 工作区 |

技术债（登记，下次动识别时处理）：价目有三份——`claude_ocr.engine.PRICE`（识别引擎，甲骨文也在用）、`express_operator.ai.PRICE`（代录预算）、
`ai_usage.prices.PRICES`（账本）。`tests/test_ai_usage.py::Prices` 锁住三份一致；动识别、重发甲骨文并跑回放时并成 `ai_usage.prices` 一份。

已知限制：
- CC 对话的最后一轮若既没有推送线程（进程重启、超 15 分钟）又没配引擎回调，要等这段会话下一轮才补记；累计数不丢，只是晚记。
- `list_cost` 是官方按牌价算、四舍五入到分的累计数，是牌价不是合同价（目前无折扣，两者相同）。
- 谷歌识别（Document AI / Gemini）不经 Anthropic，不在这本账里。
