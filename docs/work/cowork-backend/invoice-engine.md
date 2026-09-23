# Cowork 发票执行纯编排（第四批）

本批落点为 `pearnly_cowork.ocr.engine`，不依赖 Frappe、网络、文件、线程、环境变量或系统时钟。公开 Python 入口是 `run_invoice`，公开 RPC/工作单/收费归 `ocr.execution`；帧渲染和 Core 供应商检查点归 `ocr.inputs_runtime` / `ocr.providers`。依赖方向只有运行适配器 → engine 合同 → 已迁 schema/validation/money/policy。

## 来源与运行边界

来源仍为实际生产 SHA `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。第四批开工由主任务重新核对 `baseline-fourth-start.json`、`ocr-runtime-fourth-start.json`：invoice 为 economy + Vertex，fastpath=true，fewshot=1，direct 上限默认 20，双读关闭。固定源码在 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`；交付前线上复核由主任务统一完成，本文件不代替交付读回证据。

`test_cowork_engine_source_parity.py:HASHES` 绑定 direct_read、L2 prompts/gemini/structure、L3 fallback/gemini、page_runner、totals_rescue、fewshot、pattern_memory 的完整 SHA-256；并复用第三批模型/校验源哈希。测试只编译指定 AST 定义，不导入旧 App，不替换 sys.modules，不访问旧数据。实际 Vertex 在全部阶段使用 **L2 的 JSON parser**，不是 L3 文件中未被实际传输调用的严格 parser；MAX_TOKENS 即使 JSON 完整仍是内容失败。

| 来源链 | 迁入模块 | 保留行为 |
| --- | --- | --- |
| direct_read | runner | invoice/auto、每页原图直读、20 页阈值、缺总额/数学/sanity 失败整件回落；缺票号软标注，日期/贷项/折扣推导强制复核；0.95 起步，不能自动确认 |
| pipeline / page_runner | runner | 整件有效 text_pages 跳过 Vision；否则 L1 → L2 → 有触发时 L3；同页漏票最终提示、字段置信、来源警告、折扣修复与人工分桶 |
| layer2_prompts / layer3_fallback | prompts | 仅发票提示原文、原重试提示、L3 全字段独立看图与原 JSON/触发原因；不带 ERP prompt 扩展 |
| layer2_gemini / Vertex 实际调用 | parsing / runner | markdown 或散文中首个完整 JSON object；解析失败一次重试；L2 schema 失败直接终止，L3 schema 失败可走窄金额救援 |
| totals_rescue | runner | L3 内容失败后四个金额重读一次，512 token/8 秒；数学+VAT 闸通过才替换，并始终人工复核与差异留痕 |
| feedback/fewshot | fewshot | 从每页文本取前四个不同 13 位税号，按 (field_name, ai_value) 稳定去重，最多 12 条；直读成功不注入 |
| pattern_memory | patterns | 可选纯跨页票号模式对象，生产调用仍为 None；不存在自动 DB 学习/查询 |

## 稳定接口

- `PageSource.page_count` 为完整物理页数；`text_pages` 是整件 Page 列表或 None；`frame(n)` 以 1 起的物理页码惰性取帧，必须支持回落时重读同页。text 页数不匹配则整件丢弃 override；错页号拒绝。图像解码/PDF 200 DPI/大小页数限制在 IO 层。
- `Frame(image_bytes, mime_type, vision_bytes=None, vision_mime_type=None)`：直读和 L3 用原图，L1 可消费源定义的图片缩放版。直读保留旧 PNG/JPEG/WEBP/GIF sniff、未知默认 PNG；L3 用 PIL 真 MIME。
- `model_call(ModelRequest)` 只执行一次可检查点调用。request 明确 `stage/page_number/attempt/model/prompt/images/max_output_tokens/timeout_s/temperature/response_mime`，attempt 从 0 开始。stage 为 direct/layer1/layer2/layer3/totals_rescue。JSON 内容重试由 engine 发出新的 attempt；provider 不自行重试。
- `ModelResponse` 保留 `raw/text/finish_reason/input_tokens/output_tokens/model/elapsed_ms/checkpoint_key`；`VisionResponse` 保留完整 Page/raw/elapsed/key。engine 深拷贝收到的原证据，模型归一化或折扣补写不能改它。
- `run_invoice(..., config, anchor_year, today, fewshot_selector=None, pattern_memory=None)` 返回 `EngineResult(pipeline, calls)`。config、日期、授权 fewshot 快照必须在工作受理时固定；不能接受普通调用人任意指定日期或跨账套 fewshot。日期软闸与 schema 年份规则分别注入，不用人工编辑日期处理替代。
- `calls` 是全部已完成结果（含被弃直读、JSON 重试、L3 救援）的凭据，以非空 checkpoint_key 去重。兄弟票展开不生成第二次调用；计费不能对展开票面 token 求和。实际 provider 原文持久化由 Core；工作单可存摘要和 key。直接回落原因另进每页 `extraction_audit.direct_fallback`。

## 配置与确定性

`config_from_environment(environment, selected:InvoicePolicy)` 消费冻结快照，无 os/env 读取。支持 economy/enterprise + Vertex 的 invoice/auto 同一源链；enterprise 只换源策略指定模型，当前生产实证仍是 economy。qwen/selfhost/aistudio、银行/GL/VAT、双读和 image-first 明确拒绝，不静默换模式。

| 环境键 | 实际消费者 |
| --- | --- |
| IMAGE_DIRECT / IMAGE_DIRECT_MAX_PAGES / DIRECT_TIMEOUT | policy → EngineConfig；直读启用、整件上限、单次超时 |
| PIPELINE_CONF_THRESHOLD / PIPELINE_AMOUNT_TOL | engine 分别传入 soft flags / Decimal 数学闸；direct、Vision 触发和救援同一金额容差 |
| CONF_AUTO / CONF_REVIEW | engine 置信分桶 |
| L2_TIMEOUT / L3_TIMEOUT / TOTALS_RESCUE_TIMEOUT | engine 各 stage ModelRequest，默认 60/10/8 秒 |
| L1_TIMEOUT / IMG_MAX_LONG_EDGE | admission 校验后由 provider/input 消费；默认 60 秒/2400，L1 超时 1..120、边长 >=0，0 关闭缩小 |
| FAST_PATH / FEWSHOT | policy + IO/调用方决定整件文本/授权 snapshot；engine 不查询 DB |
| TRANSIENT_RETRY | 只允许缺省或旧默认 1；旧整页网络重试改由 Core 定时续跑/检查点，不照搬 sleep 或重付费；非默认值明确拒绝 |

表中环境键都带 `OCR_` 前缀，超时带 `_SECONDS` 后缀；代码为精确名字。关闭的 double-read/image-first 子参数与旧源一样不生效，但启用父开关会在受理前拒绝。金额容差有限非负、置信 0..1、review<=auto、超时最多 120 秒为新平台明确受理范围。

## 错误及有意差异

1. **顺序与恢复**：旧同件多页并发，现顺序运行，避免同 work 并发和 50 页常驻图。Core 检查点按 stage/page/attempt 区分，JSON 重试不能重放第一次坏缓存。Deferred / Uncertain 原样抛出，任何阶段（包括 totals_rescue）都不得把未知结果当作看不清而升档。
2. **明确拒绝合同**：只有 adapter 的 `ProviderFailure` 表示已收到明确拒绝。非认证拒绝：direct 整件回落；L3 按 quota/transient/failed 保留 L2 并人工复核，不追加金额救援；rescue 保留 L2 人工；L2 失败向上抛。认证 code/401/403/7/16 始终抛出。旧 direct/rescue 会吞全部异常，新平台不吞认证或不确定结果。
3. **首存与用量**：保留所有完成调用的 raw，旧只在最终 token 计数处留下最后一次或漏掉失败救援；新页统计可含该阶段全部已完成尝试，账单事实仍取单独 calls/Core key。流水不能按展开兄弟票复制后的用量收费。耗时改为已完成回调 elapsed_ms 的和，仅作本轮诊断；provider 从检查点回放时回调耗时接近 0，不是原 HTTP 耗时的持久回放，也不作为费用证据。`estimated_cost_thb` 当前保持模型默认 0，未接估价器，不能当实际供应商成本或用户费用。
4. **救援类型**：旧 rescue 的 model_copy(update=...) 可绕过 schema 注入 float/对象。新仅接字符串金额；非字符串结果保留证据而不应用，走原 L2 人工，不以结构破坏换表面成功。金额算术继续第三批 Decimal 的已登记差异。
5. **Vision 日期/来源警告**：保留旧真实行为：这些 warning 本身未独立强制人工，最终仍由原触发、sanity、贷项/折扣和分桶共同决定；直读日期异常会强制人工。没有为“更安全”而私自改写旧分桶规则，后续收紧须独立设计验收。
6. **空识别不收费**：即使 L2/L3 返回 schema 合法的 `{}`，只要不是明确非发票、也没有票号/总额/卖方名称或有效同页附加票，就按未识别物理页拒绝整件封存，不生空 History、不收费。旧源可能把这种空对象当可完成结果，本批按“不漏页、不把空成功收费”明确收紧。显式非发票页仍保留，整件全非发票仍免费；有效 L2 的 L3 复核回退仍可保存人工草稿。
7. **纯错误码**：EngineError/ContentError 对调用方提供稳定 code/stage/page/detail；raw、detail 不应直接进入产品提示或日志。content schema 和解析失败是已完成内容错误，不授权隐藏网络/权限错误。root 服务决定友好错误、重试和工作单状态。

## 当前验证与边界

专用本地隔离 lab：Python 3.14.7 / Pydantic 2.12.5；本子任务未启动/停止容器、未 init/connect/migrate Site，未调用外部 OCR。

- **31 个纯执行单测通过**，覆盖 direct/text/Vision/L3/四金额救援、整件回落证据、same-page siblings、配置映射及实际阈值效果、fewshot 去重、全部阶段 Deferred/Uncertain、确定拒绝与认证硬失败、页关联、文本空页、预算拒绝和未支持模式。
- 固定源差分：7 项提示常量、1200 JSON 解析、7 MIME、10 L3 提示、25 fewshot 提示、600 直读决策、64 Vision 决策、6 L3 内容失败/救援/明确拒绝分支。字段与决策逐项对照；只排除明确改变的耗时/重试用量和错误详情文字，金额告警数字显示沿第三批登记规则比较，未放宽金额闸。
- 架构检查通过（0 violations），engine 静态无 IO / 无系统时钟 / 无反向运行层依赖检查通过。
- 本证据证明纯编排，不代表供应商真实识别质量、计费落账、Site/RQ 续跑、外部 ERP 推送、前端或用户业务验收。集成、总回归和生产交付读回由主任务报告。


## Core Work 历史分享的保护性升级

`patches/v0_0_1_revoke_cowork_work_shares.py` 登记在 Cowork 原生 `patches.txt` 的 post_model_sync。只对实际存在且 `product=cowork` 或 `kind LIKE cowork.%` 的 `Pearnly OCR Work`，把其 DocShare 的 read/write/share/submit 四权限位置 0；保留原 share 行、用户/everyone、时间和通知字段。不触碰 DMS、ERP、相邻名字 coworker.*、其他 DocType 或悬空目标的分享，不给共享 Core Work 添加任何 read/select 角色权限。重复运行不再改变状态，由原生迁移负责提交；本子任务未执行 Site 迁移。

原因是锁定 Frappe 16 的 `database/query.py:get_permission_conditions` 在用户没有角色 read/select、但存在分享时，直接返回 shared 名单，绕过自定义 query hook。这些 Core 工作单包含付费调用证据，本来只能从产品服务按原件范围访问，既有单独分享不属于合法功能。此 patch 是明确的保护性权限撤销，保留审计行，不通过隐式删除掩盖历史。

新写的 DocShare 仍由产品 hook 拒绝；原件受理/Work 创建时还应拒绝稳定 Work name 上已有的有效预置分享（四权限位任一非零），不偷偷清理。受理 guard 由 IO/主任务负责。四权限都为 0 的升级保留行不应阻塞正常工作；管理员直接 SQL 强行篡改不属于客户 API 的权限承诺。

定向测试 `test_cowork_engine_work_shares_patch.py` **2 项通过**，运行 patch 同一参数化 SQL 于内存关系夹具，逐列验证只有两类 Cowork 匹配项撤销，DMS/ERP/其他行完全不变、无删行且幂等。真实 MariaDB Site 升级和合成违规 share 回读由主任务执行。另 `test_cowork_invoice_results.py` 在空票收费收紧后 **16 项通过**。
