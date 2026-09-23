# 主数据第二块：客户档案的字段规则（第四十四批）

第四十四批从新平台 `05192c1` 开工，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`。线上来源仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。不接前端、不部署、不改 DMS、不写旧库、不调模型。

主数据族按"先交确定性规则、再交存储与路由"推进。上一批交的是**买家 → 客户**的匹配与归属决策（决定每张票算在谁头上）；本批交**客户档案本身的字段规则**（决定档案里存进去的是什么）：长度上限、"空串即未填"、可写字段白名单、颜色默认值、建档与更新两路的差别。

## 一、本批交付

| 新落点 | 来源（sha256 固定） | 内容 |
| --- | --- | --- |
| `masters/client_records.py` | `services/clients/store.py`（`24407c4e…`，500 行）的 `create_client` / `update_client` 里不碰数据库的那部分 | `FIELD_LIMITS`、`CREATE_FIELDS`、`UPDATE_FIELDS`、`DEFAULT_COLOR`、`clean_text`、`create_values`、`update_values` |

保留的部署版口径（逐条）：

- 字段长度上限：`name` 200 / `short_name` 80 / `tax_id` 20 / `address` 500 / `contact_person` 100 / `contact_phone` 50 / `contact_email` 200 / `notes` 1000 / `color` 20 / `party_type` 20 / `branch` 120 / `promptpay_id` 40；
- 字符串先去空白、再截上限，**截完为空就是未填**（存 None，不是空串）；
- **建档与更新的清洗不一样**（部署版如此）：建档那一路的 `color` 原样收下（真值即用，不 strip 不截断），清洗与截断只发生在更新那一路 —— 这一条是差分测试逼出来的（我第一版把两路写成一样，差分立刻报差异），照部署版保留；
- `name` 是唯一必填：空名不建档（旧侧直接返回 None，连语句都不发）；
- 更新只写**给了值**的字段（`None` 表示"这次不动它"），一个可写字段都没给就什么都不写（旧侧返回 False）；更新白名单含 `is_active`（当布尔），**不含** `workspace`/`tenant_id` 这类归属字段；
- 建档字段里没有 `is_active`（旧侧建档不认它）。

### 有意差异

| 旧 | 新 | 理由 |
| --- | --- | --- |
| 文本字段上非字符串（如 `notes=123`）：旧侧对字符串调 `.strip()`，遇到数字直接抛异常被吞掉 —— **整条建档静默失败**（只写日志） | 本侧按字符串处理（`str(value)`） | 旧行为是"静默不建档"，比"收下一个数字"更危险；本侧更宽也更可预期。差分夹具因此只放字符串，差异登记在此 |
| 旧侧返回的是数据库自增 id / rowcount | 本侧只返回清洗后的字段；写入与返回值由存储层（下一批）决定 | 这一层是纯规则；`rowcount > 0` 那种"有没有写到"的语义由 DocType 层表达 |

## 二、验证与证据

- 离线用例（隔离 bench）**15** 个：去空白与空即未填、按字段上限截断（含"超长但未到上限不动"）、非文本按字符串处理、空名不建档、颜色默认值与显式颜色、**建档颜色原样收下而更新才清洗**、买方目录字段（party_type/branch/promptpay_id）、未知字段被丢、建档不认 is_active；更新只写给了值的字段、None 即不动、没有可写字段就什么都不写、空串变未填、is_active 当布尔、白名单不含归属字段。
- 固定源差分（**把游标换成记录器**：捕获旧侧那条 INSERT/UPDATE 的语句与参数，再按部署版列序还原成 `{列: 值}`）：**200 个建档场景（149 建档 + 51 因空名被拒）+ 149 个更新场景，差异 0**。覆盖：截断 122、未填 862、默认颜色 74、`is_active` 33、"没有可写字段" 15。
- 同族回归：买家归属决策 + 文档档位 + 银行入口一起 **95** 个通过（确认没碰坏）。
- 静态检查：架构 **0** 违规（新文件在既有 `masters` 域内，无新增跨域方向）；`i18n-build --check` PASS；`check-i18n` REPORT-ONLY；`git diff --check` 干净。
- 线上来源：交付 `evidence/baseline-forty-fourth-delivery.json`（`ca98ef87…`，采集 18:02:04Z）与第四十三批交付快照 `compare` **PASS**；运行投影 `push-runtime-forty-fourth-delivery.json`（`1040c20f…`）与上一批**逐项相同**。

### 本批仍未做开工采集

同前几批：`before` 用上一批交付快照，它证明"上一批交付到现在线上没变"，不等于"本窗口开工那一刻的身份"。

## 三、下一批（客户名册的存储与 RPC，主数据第三块）

本批只有规则，**没有存储层**。下一批要接上：

1. 把 `Cowork Client` 的字段补全到与旧侧档案一致（`short_name`/`address`/`contact_person`/`contact_phone`/`contact_email`/`notes`/`color`/`party_type`/`branch`/`promptpay_id`，`display_name` 对应旧侧 `name`），并按本批规则清洗后写入；
2. `masters/clients.py` 服务层：列表（按 `is_active DESC, name ASC`，带 `invoice_count`/`total_amount`/`last_invoice_at` 三个聚合）、详情、建档、更新、停用/删除（**默认级联解绑**：把识别记录的客户字段置空，不删记录）、把发票归属到客户（先校验客户属于当前作用域）、以及**买家记忆**的存放（旧 `buyer_to_client_memory`）与 `create_by_tax_id`（按税号查或建，去重）；
3. RPC：列表/详情/保存/停用/归属/解析买家（把上一批的决策核心接上真实数据）；
4. 隔离 Site 端到端：建档 → 列表聚合 → 归属 → 级联解绑 → 权限边界 → 买家记忆学习与解析命中。

再之后：商品与单位（`units.py` 244 行、`names.py`）、供应商与科目/税率档、设置与分类。

## 四、未做

- 未写任何存储层、未改任何 DocType、未加 RPC；未接前端、未部署、未推送、未合回主线；
- 未动共享底座（`features.py`、`pearnly_core/**`、各产品术语表）；未跑真实客户数据、未做用户验收。
