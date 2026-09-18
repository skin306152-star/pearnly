# OCR 已搬（引擎在共用层，身份证抽取在 DMS 层）

日期：2026-09-18。承接 [14-dms-foundation.md](14-dms-foundation.md) 的清单第 3 项。

## 为什么分层

用户定过：**"OCR 是所有 app 的共享底座，不是可选项"**。所以：

| 层 | 文件 | 放什么 |
|---|---|---|
| 共用 | `pearnly_core/ocr.py` | 模型调用、两个档、密钥、失败升档 |
| DMS | `pearnly_dms/ocr_id_card.py` | 泰国身份证的提示词与字段归一 |

以后 Cowork 的票据 OCR 只需要在共用层之上再写一个"提示词 + 归一"，引擎不动。

## 两个档（用户说的 A 档 / B 档）

照抄老产品的 `MODE_MODEL_MAPS`，**数字一个没改**：

| 档 | 老产品叫法 | 读取臂 | 升级臂 | 什么时候用 |
|---|---|---|---|---|
| **A 档** | `enterprise`（默认） | `gemini-3.8-flash` | `gemini-3.8-flash` | 要准；两臂同模型，所以**只调一次** |
| **B 档** | `economy` | `gemini-3.1-flash-lite` | `gemini-3.8-flash` | 省钱；读不出合格号才升档花大钱 |

**"只调一次"不是省事，是照抄**：老产品 `models_with_fallback()` 会去重
（`if fb and fb not in out`），两臂同模型时"升档"没有意义，不该白发第二次请求。
这一条我一开始写测试时判反了，是读完老代码才确认的。

## 照搬的业务规则（这几条最不能改）

1. **13 位身份证号是刚需**：读取臂读不出合规的 13 位就升档再读一次
   （判据同老产品：去掉非数字后正好 13 位）。
2. **不合规的号一律置空**：`len != 13 → ""`。**绝不把不合规的号推给 DMS。**
3. **号/名/姓缺任一 → `needs_review`**，不许进推送流程。
4. **日期一律归一成佛历**：看着像公历（< 2200）就 +543。
5. **泰文称谓缩写归一**：身份证印 `น.ส.`，DMS 称谓下拉是全称 `นางสาว` ——
   不归一会匹配不到、回落错值（老产品踩过）。
6. **提示词一字未改**：那段提示词是拿真件调出来的（泰文月份表、`เม.ย.` 是 04
   不是 05、"不确定就返回空号，不要编一个 13 位"），改一个字都可能把准确率改坏。

## ⚠️ 两处必须记住的差距

### 1. 真实调用**现在跑不通**（卡在凭据）

实测两个凭据来源都不可用：

| 来源 | 结果 |
|---|---|
| Secret Manager 里的 `pearnly-erp-gemini-api-key` | **429 `RESOURCE_EXHAUSTED`** —— 原文："Your prepayment credits are depleted"，AI Studio 的预付额度用完了 |
| 生产用的 Vertex（`gen-lang-client-0165868422`，见老仓 `docs/refactor/HANDOFF-2026-06-29-ocr-llm-backend-gateway.md`） | 当前 gcloud 账号 `skinzihao@gmail.com` **无权访问**；服务账号 JSON 在服务器上，本机没有 |

所以"模型认不认一张真身份证"这件事，**现在无法在本机验证**。
需要用户二选一：

1. 给 AI Studio 那把 key 充值（最容易）；或
2. 给生产 Vertex 的服务账号 JSON（等于把生产的合规路径也一起接过来）。

### 2. 数据驻留这条路还没接

老产品的 `enterprise` 档不只是换模型，还把**后端切到 Vertex**
（`MODE_BACKENDS: {"enterprise": "vertex"}`），那是"数据驻留"的合规选择。
新平台现在走的是公开的 `generativelanguage.googleapis.com`。
**模型档位照搬了，合规属性没有** —— 将来要走 Vertex，在
`pearnly_core/ocr.py` 里换 `pearnly_ocr_endpoint`（已留成可配置）再加凭据即可。

## 怎么验（现在能验的部分）

```bash
scripts/sample-verify-ocr.sh dms qd65956.localhost
```

**25 项全过**。它用**本地桩服务器**代替真 API，验的是**代码这一层**：

- A 档只调一次、B 档升档一次且顺序正确（轻量臂 → 升级臂）；
- 请求形状：提示词含关键约束、图片走 `inline_data` + 正确 mime、
  `temperature=0`、要求直接吐 JSON、**密钥只在请求头、不在 body**；
- 归一化：13 位号去空格、非 13 位置空、公历 +543、称谓缩写归一、`LIFETIME` 透传；
- 没配密钥时状态诚实返回"没配"、自检返回 `no_key`。

它**不验**"模型认不认真卡" —— 那一层必须真凭据 + 真样本，脚本注释里写明了，
不冒充那个结论。

### 顺带记一条已知边界

终身证只有在模型**按提示词返回字面量 `LIFETIME`** 时才被认成终身；
如果它返回泰文 `ตลอดชีพ`，就会落成空串（因为那是"没有可解析的日期"）。
这是**照搬老产品的行为**，不是这次引入的。终身证在老年人里很常见，
以后值得单独定：两种写法都认，还是维持现状。检查脚本里有一条断言把这个行为钉住了。

## 站点配置（本机 DMS 站已设）

| 键 | 值 |
|---|---|
| `pearnly_ocr_backend` | `vertex`（走生产同一套认证，不用那把额度用尽的 AI Studio key） |
| `pearnly_ocr_mode` | `enterprise`（A 档） |
| `pearnly_ocr_credentials` | `/etc/pearnly/credentials/pearnly-ocr-sa-20260902.json`（**只挂载，不进镜像**） |
| `pearnly_ocr_project` / `pearnly_ocr_location` | `pearnly` / `asia-southeast1` |

实测 `ocr.api_status()` 回报：`backend=vertex`、`mode=enterprise`、
`gemini-3.8-flash` 两臂都落 **global**（`gemini-3.5-flash` 才落 `asia-southeast1`）。

## 真样本验证（2026-09-18 补，这一步原先是缺的）

**真图片 + 真 Vertex**，`scripts/sample-verify-ocr-real.sh dms qd65956.localhost` →
**6/6 全字段一致**：

| 场景 | 结果 |
|---|---|
| `id_card_clean_001` / `_002` | 全字段一致 |
| `id_card_skewed_001`（歪 8–20°） | 全字段一致 |
| `id_card_glare_001`（反光） | 全字段一致 |
| `id_card_expired_001`（过期证） | 全字段一致 |
| `id_card_photocopy_006`（复印件） | 全字段一致 |

比对字段：13 位身份证号、名、姓、出生日期、到期日、称谓、区/府/邮编 —— 全部对上，
没有一个字段靠猜。**没触发限流**（连续 6 张）。

样本从哪来：老产品的评测料 `tests/eval/vision_ablation_p3/id_card/`（合成泰国身份证，
非真人数据）。图被 gitignore，但有固定种子的生成器 `generate.cjs`，本机 5 秒重新生成：

```bash
cd <老产品> && node tests/eval/vision_ablation_p3/generate.cjs
```

已**复制进本仓库** `samples/ocr-id-card/`（40 图 + 40 真值，1.9MB），
所以验证不再依赖老仓库在不在本机。

⚠️ 真值里的日期是 ISO（`2540-11-11`，佛历年），我们的归一输出是 `dd/mm/yyyy`，
检查脚本负责两式归一后再比 —— 别把格式差当成识别错。

## 下一步

清单里还剩：**MR.ERP DMS 客户端**（真实浏览器登录 + 表单提交，约 2500 行）、
查重/建档编排、推送编排、主数据缓存、LINE 业务流。

**前置的 DMS 连接凭据已经解决了** —— 不用问用户，老产品里就有，
已解密并写进本站 `EP-00029`（测试账号 `dmstest`）：见
[16-legacy-credentials-found.md](16-legacy-credentials-found.md)。
