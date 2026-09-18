# DMS 地基：要用到的东西，以及它们现在到哪了

日期：2026-09-18。

用户的要求：**"先把要用到的东西全部铺好，缺什么就去老产品里找，那边是完整跑通的产品。"**

所以先把老产品里 DMS 这条线的**全部依赖**盘出来（下面第一节），再逐个搬。
清单本身就是这次要交付的东西之一 —— 没有它，"还缺什么"只能靠记忆。

## 一、老产品 DMS 用了什么（逐项对照）

老产品是 FastAPI + Postgres；新平台是 Frappe + MariaDB。**逻辑照搬，实现重写**。

| # | 能力 | 老产品在哪 | 代码量 | 新平台状态 |
|---|---|---|---|---|
| 1 | 外部连接（地址+凭据，Fernet 加密，SSRF 校验） | `erp_endpoints` 表 + `services/erp/endpoint_config.py` + `routes/erp_endpoints_routes.py` | ~300 | ✅ **已铺**（`Pearnly ERP Endpoint`） |
| 2 | 推送日志（状态唯一来源、租约、重试） | `erp_push_logs` 表 + `services/erp/push_store.py` | ~500 | ✅ **已铺**（`Pearnly Push Log`） |
| 3 | 身份证 OCR（Gemini 视觉 + 缺号升"高精档"） | `services/ocr/id_card_extract.py` + `handlers/id_card.py` | 264+ | ❌ 待搬 |
| 4 | MR.ERP DMS 客户端（真实浏览器登录 + 表单提交） | `services/erp/mrerp_dms_client*.py`、`mrerp_dms_adapter.py`、`mrerp_http/` | ~2500 | ❌ 待搬 |
| 5 | 客户查重/建档编排 | `services/erp/erp_dms_intake.py` | 318 | ❌ 待搬 |
| 6 | 推送编排（建档/改写 + 写日志） | `services/erp/erp_dms_push.py` | 405 | ❌ 待搬 |
| 7 | DMS 主数据缓存（地址级联/称谓/顾问） | `services/erp/dms_masters_cache.py`、`dms_advisor.py` | ~390 | ❌ 待搬 |
| 8 | 操作员花名册 | `services/dms_roster/` | 786 | ✅ **已做**（`DMS Operator`，含每行发绑定码） |
| 9 | LINE 业务流（录入/查询/预约/审批） | `services/line_dms/`（33 个文件） | ~7000 | ⚠️ 只搬了绑定（webhook/绑定码/解绑） |
| 10 | LINE 传输层 | `pearnly_core/line.py`（新平台已写） | — | ✅ 已做 |
| 11 | 凭据加密 | `core/kms_helper.py` `encrypt_str` | — | ✅ 用 Frappe 原生 `Password` 字段（落库即加密） |

### 关于"真实调用"要说清楚的两点

1. **DMS 那条连接是真实浏览器**：老产品 `mrerp_dms_adapter.py` 把
   **Playwright 登录后的浏览器上下文**当 transport，不是裸 HTTP
   （老产品注释："the DMS session is owned by a real Playwright browser login,
   not raw external HTTP"）。所以新平台要真连 DMS，容器里得有浏览器与登录流程，
   这是一笔实打实的工程量，不能当成"加个 requests 调用"。
2. **OCR 有两个档**：老产品身份证路径先用 `flash_lite` 读，**13 位身份证号读不出来
   时升到高精档再读一次**（`_gemini_vision_extract` 里的 `OCR_FALLBACK_MODEL`）。
   用户说的"A 档 / B 档"就是这个；搬的时候两条都要搬，只搬一档等于丢掉糊图的救场。

## 二、这次铺了什么（已落地并验证）

### `Pearnly ERP Endpoint`（外部连接）

照老产品的 `erp_endpoints` 搬，字段含义一致：`adapter` 枚举沿用
（`mrerp_dms | mrerp | webhook | express`），凭据分**两组** ——
操作员自己的（`username`/`password`）和"有修改权限的"（`admin_*`）。

老产品那句话是设计依据：**"销售人员仍用自己的账号匹配提成归属，需要改写客户档时
由服务端继承老板的这组凭据。"** 所以服务层 `credentials(name, as_admin=...)`
按需取一组，**取不到就报错、不回退到另一组**（静默换账号会把权限边界搞糊）。

### `Pearnly Push Log`（推送日志）

**推送状态的唯一来源。** 状态取值照老产品：
`success | failed | skipped_dup | pending | retrying | manual`。

带**租约**：`claim_for_push(endpoint, owner=...)` 靠
`lease_owner` + `lease_expires_at` 保证"同一条只被一个 worker 推"——
外网推送不是原子的，两个 worker 同时推同一条，对端会建两份档。

`status_of(单据类型, 单据名)` 给界面和验证脚本用：**只看这个才是结论**，
业务表上的 `push_status` 只是引用位。

## 三、这一轮踩到的坑（都会复发）

1. **DocType 的类名必须精确等于文档名去空格**。Frappe 用
   `doctype.replace(" ", "")` 推类名（`base_document.py`）。我写成
   `PearnlyERPendpoint`（少一个大写 E），migrate 直接把它当**孤儿 DocType 删掉**，
   而报错只说 `ImportError: Pearnly ERP Endpoint` —— 不提类名，完全看不出是大小写。

2. **`frappe.db.sql` 跑 UPDATE 返回的是空元组，不是影响行数**。
   拿它判成败（`if updated:`）永远为假 → 租约**一个都抢不到**，而且不报错，
   表现只是"没有任务可推"。改成"改完回读 `lease_owner` 是不是我"。

3. **`bench console` 的 exec 命名空间与函数 globals 不是同一个**。
   脚本里模块级的 import / 函数 / 列表，在函数体里全都看不见，报 `NameError`；
   更坑的是外层 `except` 会把它变成一句 `RESULT: SKIP`，看起来像"条件不足"。
   所以检查脚本一律：import 放函数里、状态放闭包里、模块级只留直白语句；
   而且**异常必须计入失败**，不能让它退化成 SKIP。

4. **造数据的检查脚本要 `finally` 清理**。第一版没有，脚本中途报错就把
   测试连接留在站点上了（实际留了 3 条）。

## 四、怎么验

```bash
scripts/sample-verify-foundation.sh dms qd65956.localhost
```

17 项，真跑在真站点上，**自己造数据、无论成败都清干净**。覆盖：

- 连接建得出来；列表**不返回密码**；能看出"配过凭据 / 可用"；
- 普通凭据与管理凭据各取各的、`as_admin` 标记正确；
- **停用的连接取不到凭据**（"同批不误投"的闸）；
- 测试连接**不谎报连通**（现在只检查配置，说明里写明"没有真的连"）；
- 推送日志：worker-1 抢到、**worker-2 被租约挡住**、收口后租约释放；
- `status_of` 以**最新一条**为准（还有 pending 就不算成功）；
- 重试到上限收成终态 `failed`，不无限重试。

## 五、下一步（按依赖顺序）

1. **OCR 身份证**（第 3 项）：自包含、不需要外部连接，先做能最快看到"真实调用"。
2. **DMS 客户端 + 查重/推送编排**（第 4/5/6 项）：要真实浏览器登录，量最大。
3. **DMS 主数据缓存**（第 7 项）：地址级联/称谓，OCR 后确认资料要用。
4. **LINE 业务流**（第 9 项）：录入/查询走 LINE —— 但先要有 1-3 才能接。

每一步都会：从老产品搬逻辑 → 在新平台重写 → 用真站点验证 → 记进本文档。
