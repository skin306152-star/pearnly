# 发票原件处理与供应商 IO 迁移

第四批，本地工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，开工基线 `ffdcd477632c8cb435d54ab855c721d4b67779cc`。父任务确认 `baseline-fourth-start.json` 和 `runtime-fourth-start.json` 为 PASS，旧生产仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。固定只读来源为 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`，末次线上与运行配置复核由父任务统一记录。

所属域为 `pearnly_cowork.ocr.inputs_runtime` 与 `ocr.providers`，另按审查结果补 `ocr.execution.work_guard` 原生工作单保护。本工作包不改共享 Core、原件服务、UI 或镜像；不装凭据、不调用真实 OCR、不发 LINE/ERP、不部署。实现可供后端执行流程接入，离线测试不能代替供应商或业务验收。

## 入口与依赖

```python
from pearnly_cowork.ocr.inputs_runtime import PageSource
from pearnly_cowork.ocr.providers.core import build_client

# content 必须由上层检查 actor/source 范围后，通过 files.service.read_bytes 取得。
with PageSource(content, filename, expected_page_count=stored_page_count,
                enable_text_path=frozen_text_flag, image_max_long_edge=2400) as pages:
    client = build_client(frozen_environment, core_identity=frozen_core_reference)
    # 上层在每次回调之前重新验证身份/原件范围和工作单剩余时限。
    # engine.run(..., model_call=client.model_call, vision_call=client.vision_call)
```

`PageSource` 对接 `ocr.engine.contracts.PageSource`：`page_count` 是实际物理页数；`text_pages` 为整件文本命中后的 `Page` 元组或 `None`；`frame(n)` 用 1-based 物理页号，按需返回 `Frame(image_bytes,mime_type,vision_bytes,vision_mime_type)`；不持有整件 PNG 列表。`close()` 丢弃原件与文本引用；文件原件仍由既有原件服务持久保存，不在此写磁盘或网络。

`build_client(environment, *, core_identity)` 接受受理时冻结的非敏感旧环境快照及 Core 引用四键：`backend/project/location/credentials_path`。本批仅 Vertex；其余后端明确拒绝。生产适配调用 Core `ocr.access_token`，不新造认证、SDK client 或 HTTP transport。每次调用先拒绝空 work context，并核对上述引用没有改变，再取 token；完整 actor/tenant/source 权限仍由执行域负责。

依赖方向：`inputs_runtime → engine.contracts + schema + inputs`；`providers.client → engine.contracts + schema + policy + Core.execution.errors`；`providers.core → Core.ocr + Core.execution.{provider,budget,work}`。engine 不反向依赖 providers，纯编排只消费自己的协议。没有新增跨产品数据读写、公开 RPC 或队列 hook。

## 来源与保持行为

| 固定来源 | 新落点 | 行为 |
| --- | --- | --- |
| `services/ocr/text_path.py:try_extract` | `inputs_runtime/source.py:_text_pages` | pypdf 提取；整件平均至少 200 字符/页；个别提取失败页记空但计入平均；页数不同则取消整件 override |
| `services/ocr/pipeline.py:run_on_pdf_bytes` | `PageSource.frame` | PyMuPDF、200 DPI、alpha=False、PNG；真实物理页数与受理记录必须一致 |
| `pipeline.py:downscale_image_bytes` | 同名函数 | 上传图片 L1 最长边默认 2400，只缩不放；JPEG q90、其他压 PNG；变大或压缩异常则保留原 bytes；PDF与L3不受该缩图影响 |
| `services/ocr/layer1_vision.py:_response_to_page/_extract_bbox` | `providers/client.py:vision_page` | REST 返回的首物理页转相同 Page/Block/Paragraph/Word；拼词/段落、均值置信度、缺坐标补零、少于四角补最后顶点 |
| `services/ai_gateway/providers/vertex.py:_location_for_model/_config/_usage/_safe_text` | `ProviderClient.model_call` | 已迁 policy 选择区域；3.8 固定 global；多模态图片在前、prompt在后；3.8 LOW、2.5 budget=0、其他多模态 MINIMAL；output tokens 含 thoughts |
| `layer1_vision.py:_call_vision_for_image` | `ProviderClient.vision_call` | DOCUMENT_TEXT_DETECTION、th/en、每页一次；空白页是合法完成结果，响应结构缺失不假冒空白 |

保留输入 bytes，不修改金额、提示词、业务模型或模型名。`ModelResponse` 返回完整 raw body、原文、finish reason、usage、实际请求模型、elapsed 和 checkpoint key；模型 JSON 解析/重试只归 engine。有效 JSON 遇 MAX_TOKENS 也交 engine 判定，适配器不自称成功票据。

图片 MIME 的旧差异保留在 engine：直读只嗅探 PNG/JPEG/WEBP/GIF，其他按旧规则 PNG；L3 用 Pillow 实际格式。原件可接收 TIFF/BMP 等格式不等于所有供应商都已验收这些格式，不能用本地解码测试代签外部能力。

## 付费请求和回放

唯一网络出口为 Core `execution.provider.post`，由它取得共享 budget permit，保存 started/done/rejected 检查点并处理明确 HTTP 429/503。provider 的 checkpoint 身份编码 `cowork/provider/project/location/model/stage/page/attempt`，预算 bucket 仍只用真实 provider/project/location/model；不往供应商 JSON 加私有 stage/attempt 参数。这样同一已完成请求可回放，另一页、模型、阶段或显式 JSON 重试不会误命中上一结果。

Core `Deferred` 和 `Uncertain` 原异常上抛；无 sleep、无本地自动网络重试、无“未知结果转另一个付费模型”。没有 work context 时在 token 与 HTTP 调用前失败。明确收到的 provider refusal 采用 `ProviderError`，继承纯 `engine.contracts.ProviderFailure(code,http_status,provider_code)`，与 engine 的解析失败分别处理；L3 的有效 L2 回退仍可进入人工审核，认证错误继续失败，未知付费状态不能走该回退。缺配置、空 token、目标改变和无效本地请求使用独立 `ProviderConfigurationError`，不继承该协议，不能被误判为供应商已明确拒绝而降级。

Core 只直接回放 done；因此 HTTP 非可重试拒绝及 HTTP 200 非 JSON 另外写入 `cowork.terminal:<原检查点key>`，保存固定错误码、状态与原响应 JSON/bytes。后页 Deferred 后续跑时，前页的已知拒绝在取 token/HTTP 之前重放，保持旧回退路径且不重复付费。Core rejected 已提交、产品 terminal 尚未写入的进程中断缝隙从原 rejected 记录保守重建拒绝；该分支没有原响应 bytes，明确记 `recovered_from_core_rejection`，不能冒称证据完整。429/503 与已标记 deferred 的拒绝保留重试语义。elapsed 记录本次回调耗时；检查点重放通常接近零，不代表原供应商处理耗时或计费量。

Vision HTTP 200 的内嵌 code 8/14 仅在**没有有用 annotation**时确认为拒绝：同一 Core 检查点改为 rejected，反馈 429/503 预算冷却，再 Deferred。若有用输出与 error 共存，保留 done 检查点并明确报 partial_response，不自动重付费。认证/无效图片等错误明确报失败，不当空白页。

额外审查发现锁定 Frappe 的 Administrator 会在权限 hook 前直接获准；只挂 `has_permission` 不能保护已付费 Work 检查点。应父任务要求，本包新增 `ocr.execution.work_guard`：`CoworkWorkGuard` 原生读/写权限 mixin，以及 `mutation/deny_delete` 生命周期钩子，核对新值和数据库旧 product/kind 归属；包括 Administrator、篡改 kind/product、伪造新记录状态都不能重设/删除 Cowork Work。仅 `work_creation()` 的私有真对象 capability 允许 Core.submit 新建，不能更新已有记录；创建前若相同稳定 Work name 已预置任何 read/write/share/submit 有效分享权限则拒绝，四权限皆零的保留审计行允许。此处不删除或重写 DocShare；旧分享撤权由原生 patch 负责。Core SQL 状态及检查点更新不变，非 Cowork Work 沿原生行为。hooks、Core.submit 外层接线和真实 Site 测试由父任务负责，本工作包不改 Core 本身。

## 明确的资源与失败处理变化

- 旧 PDF 会预先保存整件图片；新实现只按需渲染当前页。电子 PDF 的 L2 可直接吃文本，只有实际视觉步骤才渲染。像素、顺序、文字阈值保持一致，时序和内存驻留方式改变。
- 超过 50 页直接拒绝，不能截断后冒充整件完成；这也与已迁原件服务一致。每页渲染前核对 200 DPI 像素数，默认上限 40,000,000，超出报 `ocr.render_pixel_limit`，不私自降 DPI。上限是新增本地资源保护。
- 快路启用但缺 pypdf 报 `ocr.pypdf_not_configured`，不悄悄切付费 Vision。父任务已核定现有 lab 镜像实际 `pypdf==6.15.0`，与固定源锁一致，并负责把该已有版本显式固定到镜像定义。本包不安装/升级依赖。
- `OCR_L1_TIMEOUT_SECONDS` 默认 60，适配允许 1..120 秒，符合 Core 已有单请求上限；不按旧 whole-page transient retry 重新执行已付费步骤。长批次续跑、时限和权限复核由执行域承接。

## 验证证据

2026-09-21，在既有隔离 `colima-pearnly` / `pearnly-cowork-backend-lab` bench 内执行，没有 init/connect Site 或本地业务数据写入。已核挂载指向本工作树，bench image 为 `sha256:90c1d545d215d6081965740013dd8af84337ca23c5fde5c36acdf162cd4655c3`；父任务负责容器启停，本包未改变环境。

- `tests/test_cowork_provider_runtime.py`：15 项假 transport 通过，覆盖 Vertex payload/区域/thinking/usage、stage/page/attempt身份、完成响应及明确拒绝回放、Core rejected 提交缝隙、Deferred/Uncertain原样传播、本地配置异常不能降级、无work/目标改变前置拒绝、Vision坐标/空白/内嵌quota/混合错误。
- `tests/test_cowork_input_runtime.py`：9 项真实 synthetic PDF/图片处理通过，覆盖惰性200DPI、整件pypdf阈值与空页、文本页数不同、上传图缩图、原图保留、损坏/加密/超页/引用不符和分配前像素上限。
- `tests/test_cowork_provider_work_guard.py`：5 项无 Site 的原生协议测试通过，覆盖 Administrator、旧归属篡改、伪造/嵌套 capability、DMS 旁路、预置分享四权限拒绝及全零保留行允许。
- `tests/test_cowork_provider_source_parity.py`：绑定以下来源哈希，500 个 Vision 层级/坐标样本、36 个六种图片格式缩图样本、4 类 PDF 文本样本与旧函数严格相等。脚本只执行选定函数 AST；不 import 旧应用或 SDK。
- 架构检查 PASS（0 违规），适配与测试语法检查通过；后续执行域/真实 Site 故障注入由父任务另记。

| 来源文件 | SHA-256 |
| --- | --- |
| `services/ocr/pipeline.py` | `9e8fa8fb566a8b457c0ae6f64f6961069c757994269c9b65f6284a14a7843e52` |
| `services/ocr/text_path.py` | `d5829b61500c55bf0dfb5c31f73c850147f9fbf552f702374ba0425f15fbf49d` |
| `services/ocr/layer1_vision.py` | `631ab44df70acafda4209307caf8fa28e1a8c862c3028809c4bcdb94e561b150` |
| `services/ai_gateway/providers/vertex.py`（配置人工对照） | `78473a9ba7956e5f6ec3f9c76afd91eafeef2f779bf83f9a7d8d9b6f985f64d7` |

检查命令：在上述 compose 的 bench 内使用环境 Python 对上述三个 unittest 文件分别 `unittest discover -s /workspace/tests -p '<文件名>' -v`；source parity 支持 `--source <固定目录>` 或三个原文件 `{相对路径: 原文}` JSON 经 stdin 配合 `--source-stdin`。所有测试均无真实 OCR/凭据/API 调用，尚无外部供应商、生产运行或用户验收证据。
