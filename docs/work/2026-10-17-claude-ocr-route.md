# Claude 识别路线（ADR-022，2026-10-17）

- 授权：用户 10-17「可以上线，这个项目只是 Claude，不要和谷歌牵扯；后台管理有自由调配模型识别 Google 或者 Claude 的选项，每个租户
  可以自定义，开户也可以自定义选择，选 Claude 后就走今天这套规则逻辑，要适用于三个产品」；「dms 到时候去生产拉真实语料用」；
  「api 就用我发给你那一把」；甲骨文安全列表「你做好我点确定」。
- 分支 `pearnly/claude-ocr`，基线 `c150612c`。并行窗口「工作清单窗口交接」的分支不碰识别、ERP 识别、DMS 识别、控制面开户（10-17 核对）。

## 评测（甲骨文 /opt/pearnly-claude-eval，评测工具在 ~/pearnly-devtools/claude-eval/，累计约 24.3 美元）

语料：金标 215 份（开发集 47 调提示词、测试集 168 只跑一次）；SM 热敏小票 86 张（MR.ERP 登记簿当答案，账票不一致以票为准，抽 6 张核对
6 张票对）；06-录入推送有答案抽 250 份（Express 账，能对上 49 份 69 张票）；银行账单 9 份 263 行（余额链）。

| 阶段 | 金标测试集 | SM 热敏 | Express | 每页 | 识别段 p50 / p90 |
|---|---|---|---|---|---|
| 10-07 Claude（照搬谷歌提示词） | 5.7% | — | — | ฿0.30 | — |
| 只读关键字段 + 硬性自检分级（评测脚本） | 2.02% | 1.26% | 0.90% | ฿0.09–0.19 | 5–5.5s / 9–10s |
| **正式引擎（含明细行、付款等全部字段）** | **1.73%** | **2.09%** | **2.03%** | **฿0.14–0.24** | **7–9s / 13–16s** |

合计 31/1665 格 1.86%；非票据被录 0（谷歌同一金标测试集 13）。加明细行后每页贵约 30–50%、慢 3–6 秒（输出变长），ERP 采购入账与 Cowork
存货推送要用明细，保留。银行账单 Haiku 263 行余额链 0 断。Sonnet 5.5 比 Haiku 差且贵，淘汰。缓存：系统提示词约 3000 token 每次读缓存。

## 实现

- `pearnly_core/claude_ocr/`：`spec`（Claude 专用提示词与结果格式：发票类 voucher、身份证 id_card；结构化输出全字符串，可为空参数有上限）、
  `rules`（硬性自检、本公司税号更正）、`engine`（Haiku→Opus，effort low，提示缓存）、`server`（甲骨文服务）、`client`（站点侧：
  站点配置 `pearnly_ocr_engine`、先甲骨文再本机直调、Anthropic 出错抛 `Deferred`、识别工作单里按内容存断点不重复付费）。
- DMS：`ocr/ocr_id_card.extract` 选 Claude 时读 id_card，字段名与原 `normalize` 一致。
- ERP：`recognition/policy.read_all` 选 Claude 时交 `recognition/claude.read_all`（整份一次，标准格式转采购 / 销售原字段，照旧 structured /
  review / finish）；采购不再调 Vision 取原文核对；采购把 ERP 公司税号交给更正。
- Cowork：`ocr/execution/workflow._recognize` 选 Claude 且是图片 / PDF 时交 `ocr/execution/claude.recognize`，产出与 Cloud Run 同形的
  `(pipeline_json, evidence)`：每个实体页一条，同页多张进 additional_invoices，续页按非发票留作证据，第 9 页起按读不出标红；含税小票的
  subtotal 按 Cowork 口径扣出税前；转账凭条 / 50 ทวิ 交给租户的单据口径。（10-18 起表格文件也接 Claude，见下。）
- 控制面：租户表单与开户单加「Recognition Engine」（Google / Claude，默认 Google）；开户的 `set_ocr` 带上引擎，租户改了重排 `set_ocr`
  （`provisioning.queue_ocr_engine`），执行器写站点配置 `pearnly_ocr_engine`。
- 运行：甲骨文 `infrastructure/claude-ocr/` + `scripts/claude-ocr/deploy.sh`（容器 `pearnly-claude-ocr`，8443，自签证书、令牌、
  内存 1g；安全列表与 iptables 只放行生产 107.155.65.74）。生产 compose 透传 `PEARNLY_CLAUDE_OCR_URL/_TOKEN/_CERT_SHA256`、
  `PEARNLY_ANTHROPIC_API_KEY`（runtime.env）。

## 测试

- `tests/test_claude_ocr_rules.py`（Development checks）：自检、分级、空串转 None、页数上限、结构化输出限制、提示词关键口径。
- `tests/test_claude_ocr_products.py`（候选检查，生产镜像）：站点侧客户端（远端 / 断点 / 本机兜底 / 502 推迟 / 未配置推迟）、ERP 转换、
  Cowork 结果经 `prepare_history_batch` 出 3 张票、DMS 身份证分支不调谷歌。
- 甲骨文服务端到端：SM IMG_2575 小票单号、日期、税号、税额、合计、11 行明细正确，Haiku 单读 10.5 秒 $0.0023。

## 10-18 补齐全部入口、闸门改走生产代码（main 5edbd5e4 之后，分支 `pearnly/claude-ocr-all`）

用户 10-18：「claude 正式上线后还覆盖完所有入口啊，这个架构你要搭建要呀，测试的数据要和生产正式跑的时候对得上呀」。

- 入口盘点（三个产品调识别模型的全部 8 处）：原来漏 4 处，都已接上 —— Cowork 表格文件（Excel / Word，逻辑页文字交 Claude，
  `claude.recognize_table`）、销项税核查的发票（`recon_center/invoice_ocr`，与录入同一个转换）、对账三类文档扫描件 / 照片
  （`recon_center/document_ocr._read_claude` → `ocr/execution/claude_documents`：一页一读，按企业档格式拼页，对账出行器
  `enterprise_rows` 与税报解码同样认 `claude_v1`）。LINE 入口都落在这些入口上。
- 硬闸：`provider.post` 与 `forward._call` 遇选了 Claude 的站点抛 `GoogleRefused`；源码守卫要求每个调谷歌识别的模块都先看路线。
- 闸门实跑发现并修好的问题（单元测试都 mock 掉了，只有走生产路径才看得出）：
  1. 甲骨文自签证书被 requests 默认 CA 校验拒掉，每次都退回本机直调 → 会话关 CA 校验、只认钉死指纹（指纹不对照样拒绝，已实测）；
  2. 识别前没转正（谷歌路线有本地方向模型）→ 照片与 PDF 页先过 `ocr_models.orient`；
  3. ERP：转账凭条、预扣税凭证、对账单、报表按 ERP 口径回「不是单据」（报价单 / 订单 / 送货单仍算）；
  4. 对账：总账没印期末行时期末取最后一行印的余额；第 1 页漏读「ยอดยกมา」时按第一笔印的余额倒推期初；倒序网银账单不再被当成断链
     白交 Opus。
- 闸门工具：`scripts/claude-ocr/gate.py` + `run-gate.sh`。原件（金标 215、Express 250 从测试机直传甲骨文，不落 Mac）→ 上传检查
  `originals.inspect` → Cowork `claude.recognize` → `prepare_history_batch`（存档前最后一步，租户口径按金标：凭条 / 凭证不录）；
  同一次读法交 ERP 采购 `read_all` → `_structured`；对账走 `read_statement_file` / `read_ledger_file` / `report_source` → 配对
  `bank_matching.reconcile`。容器 python 3.14 + 生产镜像同版本依赖，读法经甲骨文 claude-ocr 服务。

| 闸门（生产代码，Claude） | 读错 | 非票据被录 | 多出 / 漏 | 每页 | 识别 p50 / p90 |
|---|---|---|---|---|---|
| 金标 215 份 · 谷歌**生产记录**（gold-gemini-rec） | 2.10% | 22 | 多 24 / 漏 0 | ฿0.155 | — |
| 金标 215 份 · Claude 生产记录 | 2.13% | 2 | 多 0 / 漏 3 | ฿0.252（升级 13%） | 9.7s / 17.6s |
| SM 热敏 86 张 | 0/238 格 | 对不上答案的 22 张录 2 | — | ฿0.187 | 8.7s / 13.7s |
| Express 248 份（能对上账 49 份 86 张票） | 0.52%（3/572） | — | 漏 0 | ฿0.085 | 8.6s / 17.7s |

读错格多是口径：电费单印的应付按 0.25 舍入（477.75），金标取未税 + 税额（477.76）；单号前缀（RCPT#）。ERP 换算与 Cowork 同一次读法，
金标 185 份字段错数相同（另 30 份闸门假 Frappe 出错，已修，不是产品问题）。

对账（账单 + 总账配对，与生产同一套）：GIZE BAY-CA 1 月扫描 10 页 416 行、链 0 断、配上 402（总账 544 行）、公式差 0，3 分钟 $0.03；
冰厂 KBank / BBL 存折扫描、CamScanner 12 页、照片与 PDF 同一张结果一致；MrsFlowers SCB3061 35 行配上 31、BBL6764（倒序透支户）71 行
配上 70、公式差 0；KBank 往来户扫描版与电子版结果相同。所有扫描账单余额链 0 断。SM 销项税报告照片 4 张 72 行全部对上登记簿，68 行金额
全对（1 张有 4 行相邻行金额错位）。WZSHOP「税报扫描版」实为门店销售清单（电子版生产即拒为销售清单），不作税报样本。

累计花费（甲骨文评测 + 本轮闸门）约 29.5 美元（预算 50）。

## 上线闸门（未完成项）

1. DMS 身份证：从生产取真实件小批量测（用户同意，有多少用多少）。
2. 测试环境：选 Claude 的测试租户实测从上传到出结果的速度（三个产品各一份）。
3. 生产：runtime.env 四行 10-17 已加（备份 runtime.env.bak-claude-ocr-20261017）；随「工作清单窗口交接」的外壳批部署；先开一个内部租户。
