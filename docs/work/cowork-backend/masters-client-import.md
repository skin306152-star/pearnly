# 主数据第六块：客户名录导入的归一（第四十八批）

第四十八批从新平台 `ca8d800` 开工，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`。线上来源仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。不接前端、不部署、不改 DMS、不写旧库、不调模型。

主数据只剩导入与供应商/设置两块。本批交**客户名录导入**里决定"哪些列是客户名、哪些行要导、导出来长什么样"的那一段（旧侧 `services/workspace/client_import.py` 与它用到的两个清洗函数）。**表格解析（xlsx/csv → 表头+行）由调用方给**，所以这一层可以完全离线差分。

## 一、本批交付

| 新落点 | 来源（sha256 固定） | 内容 |
| --- | --- | --- |
| `masters/client_import.py` | `services/workspace/client_import.py`（`b2b2da81…`，160 行）+ `services/purchase/field_clean.py`（`43912e65…`）的 `clean_tax_id`/`clean_branch_no` | 表头同义词与匹配、`cell_at`、`clean_tax_id`、`clean_branch_no`、`structural_error`、`import_rows` |

保留的部署版口径：

- 表头同义词**泰/英/中三语**，**子串**匹配，同一字段多列命中取**靠前**那列；
- **认不出客户名列就不导**（`name_column_found=False`，诚实报"表头认不出来"，不瞎猜）；其余列缺了整批留空，不算失败；
- 汇总行跳过；超过 `MAX_ROWS`（500）截断并置 `truncated`；单文件上限 `MAX_BYTES`（8 MiB）与本仓既有导入口径一致；
- 泰国税号 = 剥掉非数字后**恰好 13 位**，否则非法（空串）；分店号 = 最多 5 位数字左补零，**空或超 5 位返回空串**（未提供必须留空 —— 归一成总店 `00000` 会把分店票硬配到总店头上）；
- 行级结构校验：缺名 → `client_import.err_missing_name`；税号给了但不合法 → `client_import.err_bad_tax_id`（税号可选，空值不算错）。

### 有意差异

| 旧 | 新 | 理由 |
| --- | --- | --- |
| `parse_table`（xlsx/csv 解析）在本模块内调用 | 表头与行由调用方传入 | 解析是器件；原生已有自己的读取器，本层只要判定 |
| 路由层那一段（上传 → 结构校验 → 共享建档校验体 → dry_run 落库） | 不迁 | 建档校验复用本仓客户名册的服务层（第四十五批），导入端点属 RPC 编排，等前端接入时再定 |
| `field_clean.clean_seller`（卖家名清洗：金额/标签/日期/过短判空） | 本批不迁 | 它服务的是识别字段的呈现，属 OCR 那一家，不属客户导入 |

## 二、验证与证据

- 离线用例（隔离 bench）**14** 个：三语表头与子串命中/未命中、同一字段取靠前列；税号 13 位才算（含带文字与带分隔符）、分店号补零与"空/超 5 位留空"；结构校验三态；列映射到行（含去空白与缺列留空）、没有客户名列就不导、没有表头是空预览、汇总行跳过、超限截断、短行读到底。
- 固定源差分：两个模块都是纯函数（解析换成桩），**差异 0**。计数：表头格 30、表头组 7、单元格 600、结构校验 200、税号/分店清洗各 30、导入预览 120（行合计 273、缺客户名列 37、截断 1 组显式超大文件）。
- 主数据族回归：导入 + 商品规则 + 客户字段规则 + 买家决策一起 **70** 个通过。
- 静态检查：架构 **0** 违规；`i18n-build --check` PASS；`check-i18n` REPORT-ONLY；`git diff --check` 干净。
- 线上来源：交付 `evidence/baseline-forty-eighth-delivery.json`（`8e9bec08…`）与第四十七批交付快照 `compare` **PASS**；运行投影 `push-runtime-forty-eighth-delivery.json`（`25f77f47…`）与上一批**逐项相同**。

### 仍未做开工采集

同前几批：`before` 用上一批交付快照。

## 三、主数据剩下的与一处待决策

- 剩下：**供应商与科目/税率档**、**设置与分类**。开工前要先核实归属 —— 旧侧的 `supplier_posting_routes.py` 走的是采购域权限（`services/purchase/**`），`tax_profile_routes.py` 是税务申报档（`tax.filing.view`），而功能清单里 `purchase`/`tax`/`accounting` 属"不在 Cowork 范围"的产品线。下一批先做一次只读归属核对：确属 Cowork（供应商过账档案本仓已有 `Cowork Supplier Posting Profile` 与 `push_inputs.save_profile`）就接着补；属 ERP/申报侧就按"不迁"登记，不硬搬。
- **跨域待决策**仍挂着：识别记录归属的改挂/解绑要不要在 history 复核契约开口（现在删带票客户是拒绝的）。

## 四、未做

- 未写存储层、未改 DocType、未加 RPC；未接前端、未部署、未推送、未合回主线；未动共享底座；未做用户验收。
