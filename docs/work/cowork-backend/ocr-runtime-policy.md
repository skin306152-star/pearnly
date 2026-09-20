# OCR 实际运行策略与纯选择合同

本工作包属于 `pearnly_cowork.ocr`，从第三批起点 `392d9d917cb62c1a00d0bb43f72dc7b806082a86` 开发。公开产品入口未变；新 `policy.py` 只接收调用方提供的配置快照，不读环境、数据库或网络，不登记 provider，也不修改 Core/DMS 的共享默认档。依赖方向为未来 OCR 工作流 → 此纯策略；本模块不反向依赖框架或工作流。

## 取证方法

先用既有 `cowork-migration-baseline.py` 证明 Web/Worker 实际流量、固定镜像、源码与 Secret 引用。另用 `scripts/cowork-ocr-runtime-baseline.py`：

1. 校验来源凭据内容哈希，首尾分别回读两服务实际流量。
2. 回读相同 revision，并要求投影与来源凭据一致。
3. 只在内存读取其固定版本 `runtime/runtime.env`；显式 revision 环境覆盖文件值，与旧 entrypoint 一致。拒绝未支持的配置来源和任何 `${...}` 插值，避免用本机环境猜出错误结果。
4. 只投影白名单 OCR 参数；秘密、DSN、用户记录及 provider 错误原文不输出、不写盘。保留参数原始空白，因为部分旧开关不 trim。
5. 使用上述配置的数据库连接，在只读事务及 5 秒 statement timeout 下，仅查 `platform_settings` 的 `ocr_engine_policy` 一行必要列；回滚并断开。没有导入旧应用或执行建表、修复、OCR。
6. 两组件动态策略不同则拒绝统一结论；输出采用排他创建，已有证据文件不可覆盖。比较时校验完整字段、两组件、固定引用、白名单、凭据布尔值、SHA和首尾时序，不能让空对象自算哈希后冒充完整证据。独立Secret环境引用涉及OCR、凭据或DB配置时明确拒绝，不误报凭据不存在。

脚本依赖现有 `gcloud/python-dotenv/psycopg2`。本次仅利用旧仓已存在的 Python venv 运行脚本，不修改旧仓或它的环境。Secret Manager 访问为必要的只读取证，不把密钥装入新 Site、测试容器或源码。数据库连接本身仍使用已有生产身份；只读事务约束本脚本，不等于给该身份降权。

## 本次查到的事实

来源仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。Web 与 Worker 分别引用 `pearnly-web-env:3` 和 `pearnly-worker-env:3`，本次白名单与动态策略一致；不同 Secret 的版本号相同本身从不当作内容相同的证据。首尾凭据位于本目录 `evidence/`。

| 配置 / 路线 | 本次证据及解析 |
| --- | --- |
| 数据库 invoice 覆盖、全局 mode、各套餐默认 | 均为 `economy`；本场景无需用新计费接口猜旧套餐上下文 |
| 后端 | `vertex`，项目 `pearnly` |
| 首读 / 兜底升级模型 | `gemini-3.1-flash-lite` / `gemini-3.8-flash` |
| 配置文件四个模型字段 | 虽为 `gemini-3.5-flash`，但请求 mode 的模型表优先，不能据此搬成 3.5 |
| 实际两模型区域 | `global`；3.8 固定 global，3.1 按旧 global-only 规则 |
| `OCR_FAST_PATH_ENABLED` | `true`；PDF 平均每页至少200字符可进入文本 L2，优先于 image-direct；文字页数必须与渲染页数相符 |
| image-direct / 页数上限 | 未显式设置，固定源码默认启用、最多20物理页；不命中文字快路的图片/PDF才按此门槛判断 |
| 双读 / image-first | 未显式设置，固定源码默认关闭 |
| `OCR_FEWSHOT_ENABLED` | `1`；普通 invoice L2 会消费授权范围内历史纠错例，成功 image-direct 不注入 |
| `GOOGLE_APPLICATION_CREDENTIALS` | 在显式 revision 与 runtime.env 中均未配置；这里只记录存在性，没有凭据内容 |

旧 `layer1_vision.py` 在调用 SDK/ADC 前强制检查 `GOOGLE_APPLICATION_CREDENTIALS`，当前取证范围下这条回落会因配置缺失失败。Vertex 自身的 ADC 与这个检查不同，不能据此称 Vision 回落可用。本次没有发真实 OCR 请求，也没有修生产配置。

fewshot 源码使用 `ocr_correction_examples`，按税号取样；多页已通过 `submit_ctx` 传递请求上下文。当前没有读取或导入真实历史修正例，也没有验证旧数据库 RLS 的实际执行角色。因此这份证据证明配置/选择规则，**不证明新旧提示词输入、真实供应商结果或识别精度相同**。

数据库配置是瞬时读，旧进程可能保留最长30秒缓存；本脚本不读取进程内缓存。服务账户、ADC实际可调用性、用户自带密钥、配额和外部业务都未验证。后续接线须保存 actor、服务端锚定日期、策略与提示快照，避免任务重试时漂移。

## 纯策略保持项与检查

`resolve_mode` 保留 env concrete mode > invoice 覆盖 > 全局 > auto套餐的顺序，未知值回落 economy，配置按已知顶层类型浅合并；旧 `enabled` 字段不参与这个选择。`model_for` 保留mode模型优先；空fallback仅使该档为空，**不代表关闭L3**。`InvoicePolicy`分别给出 `fallback_model` 与实际 `l3_model=fallback or flash`，空fallback也不给它伪造区域；3.8固定global与其它旧型号的区域覆盖区别由 `vertex_location` 保留。二读/image-first等其它调用点仍须按各自tier接线，不能用一个模糊“升级模型”替代全部路径。

`InvoicePolicy.route` 需要权威物理页数和调用方已核验的 `text_path_accepted`。它只描述路径，不能用来跳过实际文本提取/页数核对，也不代表 qwen/selfhost 已有新 provider。非 Gemini 后端不误标为 Gemini 模型。

本地检查：7项选择单测、14项取证投影/拒绝/首尾比较测试；哈希绑定固定源码差分5,120组 mode选择、120组模型档位、45组区域选择通过。来源不匹配立即失败；只执行经固定哈希核验的纯 AST，不 import 旧应用。

后续续接时重新采集，不覆盖本批证据：

```sh
# 使用已具备 dotenv/psycopg2 的本机 Python；先重新生成来源身份 baseline。
python scripts/cowork-ocr-runtime-baseline.py collect-readonly --baseline /path/to/new-baseline.json --output /path/to/new-runtime-evidence.json
python3 scripts/cowork-ocr-runtime-baseline.py compare docs/work/cowork-backend/evidence/ocr-runtime-third-start.json docs/work/cowork-backend/evidence/ocr-runtime-third-delivery.json
```

本批不改共享 Core 的 enterprise 默认值。未来 Cowork 流程必须显式选择其已核验策略，不能直接复用默认值而让发票静默升到企业档。计费、检查点、缓存、供应商调用、原生例库、输入渲染与外部真实结果仍单独接线和验收。
