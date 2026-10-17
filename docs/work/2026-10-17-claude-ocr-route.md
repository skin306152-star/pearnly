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
  subtotal 按 Cowork 口径扣出税前；转账凭条 / 50 ทวิ 交给租户的单据口径。Excel / Word 表格仍走原表格读法（未接 Claude）。
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

## 上线闸门（未完成项）

1. DMS 身份证：从生产取真实件小批量测（用户同意，有多少用多少）。
2. 测试环境：选 Claude 的测试租户实测从上传到出结果的速度（三个产品各一份）。
3. 生产：runtime.env 加四行（`~/.config/pearnly-claude-ocr/runtime.env`）、随批部署；先开一个内部租户。
