# 主数据第一块：买家 → 客户 的匹配与归属决策（第四十三批）

第四十三批从新平台 `312c29b` 开工，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`。线上来源仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。不接前端、不部署、不改 DMS、不写旧库、不调模型。

功能清单里唯一"明显未完成"的是**主数据**（`master_data`）。用户此前已定的方向：**客户按 ERP 为准，商品/供应商/科目各产品自己维护**；本仓这边要把 Cowork 自己的那份做出来。主数据族很大（旧侧 `services/clients` 938 行 + `services/products` 279 行 + 七条路由约 1,500 行），所以按"先交确定性核心、再交存储与路由"分批推进。本批是第一块：**把发票买方（名字/税号）解析到一个 Cowork 客户**的决策核心 —— 它决定每张票算在谁头上，是主数据里最不能出错的一段。

## 一、本批交付

| 新落点 | 来源（sha256 固定） | 内容 |
| --- | --- | --- |
| `masters/buyer_resolve.py` | `services/clients/buyer_resolve.py`（`eff506ba…`，438 行）里不碰数据库的那部分 | `buyer_candidates_conflict`、`normalize_tax`、`is_valid_tax_id`、`match_client`（六层匹配）、`resolve_buyer_client`（决策阶梯） |

**数据访问是注入的**：旧侧直接开游标查 `clients` / `buyer_to_client_memory`、调 `find_or_create_client_by_tax_id`；原生把这些换成调用方给的值与回调（`memory`、`clients`、`create_by_tax_id`、`learn`）。这样这一层可以完全离线差分，存储与 RPC 归下一批的客户名册。

保留的判定口径（都是拍过板的，别按直觉改）：

- **税号 = 泰国法定唯一身份**：合法 13 位税号、且没命中已存在客户 → 按税号建/取（去重），这才是"新买方自动建"的闭环；**只靠名字模糊匹配不建**（防同名异主体错并）；
- **多页买方候选冲突**（两个互不包含的名字，缩写/全称不算冲突）→ 绝不自动建、转人工（qa_4 那种静默高信心错建）；
- **阈值**：≥0.95 自动绑 + 学习；0.80–0.95 只给"建议归属"（不绑、不学习）；<0.80 待归属；
- **六层匹配与置信度**：学习记忆 1.0 → 税号精确 0.98（税号归一后 ≥10 位才比）→ 名字精确 0.95 → 简称精确 0.90 → 名字子串 `0.80 + 0.10×(短/长)` → 简称子串 `0.78 + 0.08×(短/长)`；前两类的"精确"命中即返，后两类取最优并**四舍五入到三位**；
- 五条 `action`（`assigned` / `created` / `suggest` / `review` / `none`）与各自的 `reason` 文案逐字保留。

### 有意差异

| 旧 | 新 | 理由 |
| --- | --- | --- |
| 打开游标自己查库（含 RLS 作用域） | 作用域内的记忆命中与客户列表由调用方给出 | 本仓的存储是 DocType；作用域本来就在 `access`/`masters` 那边解析 |
| `find_or_create_client_by_tax_id`、`learn_buyer_to_client`、`update_history_client_id` 三个写路径 | 前两个注入为回调（`create_by_tax_id` / `learn`）；`update_history_client_id` 不迁 | 写路径属存储层；把 `client_id` 写回识别记录是 history 域的事（那边已有归属字段） |
| `ensure_*_table()`、`list_clients`/`get_client`/`create_client`/`update_client`/`delete_client`、供应商分类记忆 | 本批不迁 | 客户名册 CRUD 与供应商分类是主数据的下一批（存储 + RPC + 列表/详情） |
| 税号比较时客户端一侧只去连字符与空格（不去首尾空格） | 两侧都用同一个 `normalize_tax`（会 strip） | 只在"客户档案里的税号带首尾空格"时才有差别，那是脏数据；差分夹具不含这种值，差异已登记 |

## 二、验证与证据

- 离线用例（隔离 bench）**26** 个：候选冲突（一个/重复/缩写/无关两名/空串）、税号归一与合法性（13 位才合法）、六层匹配各自命中（记忆/税号带标点/短税号不比/名字精确/简称精确/精确优先于近似/子串按比例算置信度/最相近者胜/无相似返 None）、决策五条路径（无买方名、候选冲突、税号精确 → assigned 且已学习、记忆命中 → assigned、新税号 → created 且已学习、建号失败 → review、近似无税号 → suggest 且**不**学习、无税号无匹配 → review、短税号不算合法因而不建号、简称精确 0.90 落在建议带而非自动绑带、不传学习回调也能用）。
- 固定源差分（**把数据库换成替身**：假游标按固定源码的三条查询分别回答记忆命中/税号行/启用客户列表；两个写路径换成记录型桩）：**260 批、差异 0**。计数：记忆 64、税号 51、名字精确 40、简称精确 21、子串 32、无匹配 52、`assigned` 108、`created` 6、`suggest` 26、`review` 95、`none` 25、学习 114 次、冲突网格 24 组。每批比三样：决策六个字段、学习回调的调用（次数与参数）、建号调用的参数；冲突规则另在 24 组名字对上逐一比对。
- 同族回归：文档档位 + 银行入口 + 读取器 + 模型路径 + 换眼重读一起 **174** 个用例通过（本批新模块与它们无耦合，跑一遍确认没碰坏）。
- 静态检查：架构 **0** 违规（新文件在既有 `masters` 域内，无新增跨域方向）；`i18n-build --check` PASS；`check-i18n` REPORT-ONLY；`git diff --check` 干净。
- 线上来源：交付 `evidence/baseline-forty-third-delivery.json`（`2e21cbb0…`，采集 17:58:21Z）与第四十二批交付快照 `compare` **PASS**，来源仍 `ec2e5c95…`；运行投影 `push-runtime-forty-third-delivery.json`（`b4980c93…`）与上一批**逐项相同**。

### 本批仍未做开工采集

同前几批：`before` 用上一批交付快照，它证明"上一批交付到现在线上没变"，不等于"本窗口开工那一刻的身份"。

## 三、主数据族的下一批（按依赖排）

1. **客户名册（存储 + RPC）**：`Cowork Client` 已有（`workspace`/`display_name`/`tax_id`/`enabled`），要补的是列表/详情/编辑/停用、`client_import_routes.py` 的导入归一，以及"买家记忆"的存放处（旧 `buyer_to_client_memory`）—— 本批的 `memory`/`clients`/`create_by_tax_id` 就由它喂；
2. **商品与单位**：`services/products/units.py`（244 行，单位换算）与 `names.py`（产品名清洗）；
3. **供应商与科目/税率档**：`supplier_posting_routes.py`、`tax_profile_routes.py` 的确定性部分（供应商 → 科目分类记忆、税率档）；
4. **设置与分类**：`settings_routes.py`、`categories_routes.py` 里属主数据的那部分。
按用户已定的边界：客户以 ERP 为准（Cowork 这边保留建档与映射所需的最小字段），商品/供应商/科目是本产品自己维护的一份。

## 四、未做

- 未接前端、未部署、未推送、未合回主线；未写任何存储层（本批全是纯决策）；未动共享底座（`features.py`、`pearnly_core/**`、各产品术语表）；
- 未跑真实发票数据、未做用户验收。
