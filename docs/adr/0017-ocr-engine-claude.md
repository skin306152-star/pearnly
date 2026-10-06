# ADR-017 识别引擎可切换：新增 Claude（Anthropic）供应商与「只读一遍」读法

- 状态：已采纳（2026-10-07）。用户：「开工吧……这是一个新的供应商和 ocr 识别结构层，做好，做标准」「理想状态，开户管理后台加一个
  切换模型的选项，默认就是生产现状，claude 就是走现在这个……都只读一遍，只用提示词加强，或者靠代码纠正，不另增加复读的成本」
  「密钥测试期间暂时用聚合平台的，上生产以后再用正式密钥」。
- 依据：两轮 Sonnet 5.5 实时小样本（2026-10-07，90 页，见工作包）；生产读法见 [ADR-013](0013-ocr-read-strategy.md)，
  执行位置见 [ADR-015](0015-ocr-on-cloud-run.md)。
- 关联工作包：[Claude 识别引擎](../work/2026-10-07-claude-ocr-engine.md)

## 问题与约束

- 现行 Cowork 识别只有谷歌一家：看图直读 Gemini（3.8 / flash-lite），没过闸就复核、再叫 Vision 原文再读（ADR-013）。
- 小样本里 Sonnet 5.5 只读一遍：约 51 张真票字段错约 1%（3.8 在金标照片 / 扫描件上 6.2%，样本不同不可直接比）；
  每页约 ฿0.39（实时）。用户要一个按租户可切换的「Claude 引擎」，默认保持现状。
- 不能动现行读法：谷歌路径的提示词、检查闸、复核链、计费证据一字不改；在途工作单在发布前后都要能续跑。
- 付费防重（检查点）、额度与限流、权限复核、结果不明不重复付费这些保护对新供应商同样适用（ADR-015 的表）。
- 密钥不进冻结配置（冻结配置会随请求发往 Cloud Run、写进工作单）。

## 选项

1. **在现有 `ProviderClient` 里加分支**：它从头到尾按 Vertex 写（令牌、入口、备用区域、Gemini 回复格式），加分支会把两家的
   规则搅在一起，改一处要回归两家。
2. **新增独立供应商适配层 + 按模型路由**（采用）：Claude 一个适配器，只讲 Anthropic Messages 协议；用同一套 Core 能力
   （`post` / 检查点 / 额度 / 权限复核）；工厂按请求里的模型把调用路由过去，读字 / Document AI 仍走谷歌那一个。
3. **把 Claude 接到共用层 `pearnly_core.ocr`**：ERP / DMS 将来也能用，但 Cloud Run 镜像只打包 Cowork 识别域，
   且共用层这套读法与 Cowork 不同；第一步不扩到 ERP / DMS，见「范围」。

## 决定

1. **供应商适配层**：`pearnly_cowork.ocr.providers.anthropic.AnthropicClient`。只做一次调用：
   - 请求：Messages API；提示词放 `system` 并打缓存标记（同一份提示词每页都发，读缓存价约十分之一）；图在前、一句读取指令在后；
     `output_config.effort = "low"`（实测思考 token 为 0）；不发 `temperature`（新一代模型非默认值会被拒）。
   - 回复：拼接 `text` 块；`stop_reason=max_tokens` 映射为引擎认得的 `MAX_TOKENS`；用量含缓存读写。
   - 错误：与谷歌适配层同一套语义——401 / 403 冻结为认证拒绝；429 / 503 / 529 暂缓并回报额度；500 / 502 / 504 暂缓；
     其余 4xx 冻结为终局拒绝（续跑不再付费）；传输结果不明按调用方给的次数换尝试号重发一次，续跑直接取回。
   - 检查点身份 `["cowork", "anthropic", 入口主机, "global", 模型, 阶段, 页, 尝试]`：测试入口（聚合平台）与正式入口互不复用。
2. **路由**：`providers.route.ModelRouter` 按模型族分派 `model_call`（`claude-` → Claude，其余 → 原客户端）；
   `vision_call`、Document AI 等原样交给谷歌客户端。站点本机（`providers.core.build_client`）与 Cloud Run
   （`remote.runner.build_client`）同一个路由。
3. **引擎档位**：站点配置 `pearnly_cowork_ocr_environment.OCR_ENGINE`：空 / `gemini` = 现状；`claude` = 看图直读用
   `claude-sonnet-5-5`，并打开 `EngineConfig.single_read`：
   - 直读一次 → 现有检查闸（金额勾稽、折扣 / 预扣税推算、合理性）照常跑；**没过闸不复核、不叫 Vision、不升 3.8**，
     原因写进提示并转人工复核。年份差约 10 年、税号校验位不过同样只标红。
   - 过了闸也不做 Vision 单号 / 税号核对（`check_tax_invoice`）。
   - 输出写满上限不原样重读；格式坏不重试（`retries=0`）。读不出的页记成读不出（多页文件不拖垮整份）。
   - `claude` 时忽略 `OCR_DIRECT_MODEL`（切回 `gemini` 自动恢复原来选的 3.8 / flash-lite）。
   - 提示词与 3.8 同一份（`IMAGE_SYSTEM` + 「水电费账单算采购凭证」），即小样本实测用的那份；谷歌路径不变。
   - PDF 文字层路线、Word / Excel 表格、超过直读页数上限的文件仍走原读法（模型是谷歌，路由不受影响）。
4. **连接与密钥**：站点配置 `pearnly_anthropic` =
   `{"api_key", "base_url", "auth": "x-api-key" | "bearer", "models": {正式模型名: 入口上的模型名}}`，
   与 LINE / 计费凭据同一套做法：开户步骤从服务器 `runtime.env` 的 `PEARNLY_ANTHROPIC_CONFIG` 写进 Cowork 组的
   `common_site_config`（没填就去掉），不新增挂载。受理时只把非密部分（入口、认证方式、模型名映射）冻结进工作单，执行前与当前配置
   核对，变了就拒（同 Vertex 的做法）。Cloud Run 从环境变量 `ANTHROPIC_CONFIG`（Secret Manager，同一格式）读，并核对与请求
   带来的非密部分一致。
   测试期：入口 `https://openrouter.ai/api`、`bearer`、`claude-sonnet-5-5 → anthropic/claude-sonnet-5.5`；
   上生产：`https://api.anthropic.com`、`x-api-key`，换 `runtime.env` 与密钥版本即可，代码不变。
5. **开户后台**：`Pearnly Deployment.ocr_engine`（Cowork 才有，默认「现状」）。改了就把该部署的 `set_ocr` 步骤带上
   `engine` 重新排队（与改 LINE 渠道同一做法，带 `restart`）；站点步骤把该站的识别环境写成「组默认 + 本站覆盖 + OCR_ENGINE」，
   选 Claude 而站点没有 Claude 连接时步骤失败并提示原因，不写半套配置。
6. **计量**：`engine.cost` 增加 Sonnet 5.5 价目（估算口径，不参与计费）；Cloud Run 按模型带回的调用 / 限流计数认得
   `claude-`；Cloud Tasks 给 Claude 单独一个在途派发池（谷歌的 429 不拖累它，反之亦然）。

## 扩展点（用户 2026-10-07：「批处理我后期会按功能接入……希望架构可扩展好维护」）

- **一家供应商一个适配器**，都只做「组请求 → 发出 → 解析」，对引擎只暴露 `model_call(ModelRequest) -> ModelResponse`；
  付费防重、额度、权限复核由调用方注入（Core / Cloud Run 各一套），适配器不碰。再加一家（或换别家的模型）= 新适配器 +
  在 `ModelRouter` 登记模型族，引擎、检查闸、入库都不动。
- **批处理（Message Batches，同价减半）**：`anthropic.build_payload` 与 `anthropic.parse_response` 是纯函数，与实时调用同一份
  请求体与解析。以后的批处理只新增「提交一批 / 取回结果」的执行方式：用同一份请求体提交（`custom_id` 用检查点键），结果用同一个
  解析器还原成 `ModelResponse` 交给同一套引擎后半段（检查闸、标红、入库、计费）。读法、提示词与结果格式不因执行方式改变。
- **按功能选执行方式**：入口（录入、文件夹自动录入规则、对账核查）在受理时冻结「引擎 + 执行方式」；实时 / 批处理只影响何时拿到
  结果，不影响结果本身。文件夹自动录入（另一窗口）这类不急的入口可以选批处理，由它的规则声明，引擎层不需要知道调用者是谁。
- 没采用（不省钱或加成本）：顾问工具（多一次调用）、文件 API（图片仍按 token 计）、快速模式（加价）。图片缩小能明显省钱，
  但会影响读数，要先用小样本对比再定（现有 `OCR_IMG_MAX_LONG_EDGE` 可按站点调）。

## 范围

- 本 ADR 第一步：Cowork 发票录入（`cowork.invoice`）与对账的销项税核查（`cowork.vat_check`，同一引擎）。
- 第二步（同一工作包，验证后再接）：对账文档抽取（`enterprise.pipeline`，银行 / 总账 / 税报）在 Document AI 本地拼不平、
  要调模型的那一步换 Claude；它带 Document AI 原文与严格结果格式，读法与发票不同，先用语料小样本验证。
- 不做：ERP 采购 / 销售、DMS 身份证（走共用层 `pearnly_core.ocr`，另立工作包）；客户计费单价（另定）。

## 后果与验证

- 谷歌路径：代码路径与提示词不变；现有引擎、对照、远程执行测试全部照过。在途工作单冻结配置不含 `OCR_ENGINE`，按原读法续跑。
- 回退：开户后台把引擎改回「现状」（或站点配置删掉 `OCR_ENGINE`），下一份起回到谷歌；已受理的按受理时冻结的继续。
- 成本：Claude 每页约 ฿0.39（实时、只读一遍），比现行看图直读贵；切换按租户做，由用户决定哪些租户用。
- 上线前按 [识别改动上线闸门](../development/verification.md#识别改动上线闸门) 过金标与 100 人压测；
  正式密钥、Cloud Run 密钥与挂载在发布记录里登记。
