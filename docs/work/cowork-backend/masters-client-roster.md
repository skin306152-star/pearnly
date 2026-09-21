# 主数据第三块：客户名册的存储与 RPC（第四十五批）

第四十五批从新平台 `ebef648` 开工，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`。线上来源仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。不接前端、不部署、不改 DMS、不写旧库、不调模型。

前两块交的是"每张票算在谁头上"（买家→客户决策）与"档案里存什么"（字段规则），本批把**存储与接口**接上：客户档案 CRUD、买家记忆、以及把上一批的决策核心接到真实数据上。

## 一、本批交付

| 新落点 | 来源 | 内容 |
| --- | --- | --- |
| `Cowork Client` DocType 字段补全 | `services/clients/store.py` 的 arch 形状（列名与长度见 `client_records`） | 补 `short_name`/`party_type`/`branch`/`promptpay_id`/`address`/`contact_person`/`contact_phone`/`contact_email`/`notes`/`color`（`display_name` 对应旧侧 `name`）；`Cowork Admin` 增删除权限 |
| 新 DocType `Cowork Buyer Memory` | 旧 `buyer_to_client_memory` 表 | 公司 + 买家名（**小写存**）+ 税号 + 客户 + 次数 + 最近使用时间 |
| `masters/clients.py`（新） | `store.py` 的 CRUD 与 `list_clients` 的三个聚合、`buyer_resolve.py` 的写路径 | 列表（部署版排序 + `invoice_count`/`total_amount`/`last_invoice_at`）、详情、建档、更新、删除、买家记忆的学与查、`find_or_create_client_by_tax_id`、`resolve_buyer` |
| `masters/api.py` 追加 RPC | — | `roster`、`client_detail`、`save_client`、`delete_client`、`resolve_buyer`（原有 `clients`/`create_client` 保留不动） |

写路径的判定全部复用前两批的纯模块：字段清洗用 `client_records`，归属决策用 `buyer_resolve`；本模块只负责读数据、写文档、过权限。

## 二、三处结构性差异（都是有意为之，写清楚）

1. **作用域从"用户 + 租户"换成公司**（`Cowork Workspace`）：本仓权限模型是 User Permission 绑公司/客户（`access.scope`），所以每张档案属于一个公司，读写都先过 `require_scope`；`Cowork Admin` 按既有设计对任何公司都放行（这也是 lab 里"局外人"要用普通角色而不是 Admin 的原因）。
2. **买家记忆的唯一键仍是那三段**（公司 + `LOWER(buyer_name)` + 税号）：原生把买家名小写后存、按这三段查 —— 与旧数据库唯一索引同一语义，且不依赖文档名的生成方式。
3. **"把发票改挂到别的客户"与"删客户时级联解绑"没有搬**：原生 history 域把记录的 `client` 定为**不可变**（`history/storage.py` 的 `IMMUTABLE`），只允许复核服务带令牌改记录 —— 那是本平台有意加的保护。绕过它去 `set_value` 等于拆掉这道闸，所以：
   - 本模块的删除改为**还有发票就拒绝并报数**（`This client still has N invoice record(s)`），比旧侧"静默解绑后删掉"更安全；
   - 改挂归属要等 history 域的复核契约开口 —— **属跨域决定**，登记为待决策项，不在主数据里自己定。
   同时 `Cowork Client` 的 ORM 权限查询里有 `enabled=1`（禁用客户默认不可见），所以名册的"含停用"那一路在读前已过本模块自己的功能闸与公司闸之后直读数据库 —— 这条也写在代码注释里。

还有一处小差异：RPC 接受 `display_name`（原生字段名）与 `name`（旧侧字段名）两种写法但**不允许同时给**，方便调用方按任一套命名提交。

## 三、验证与证据

- **隔离 Site 端到端（7 条，两个 Site 各跑一遍）**：建档（清洗与截断生效、未知字段被丢、颜色默认）→ 名册排序与三个聚合（2 张发票合计 200.5、未归属的不计入）→ 更新只动给了值的字段（`display_name`/`notes`/`branch` 各验一次）→ 空串清字段、无可写字段返回 `updated=False` → 停用后默认列表隐藏、`include_inactive` 能列出、再启用恢复 → **有发票的客户删除被拒**（并确认记录本身与归属都没动）、没发票的能删 → 新建 RPC 全部匿名 403 → 权限（只读角色能读不能建；非 Admin 且无公司授权者读写都被拒）→ 新税号建档、第二次经**记忆**命中（`use_count` 1→2）→ 名字近似只给建议（不写记忆、不建客户）、候选冲突只转人工（仍只有 1 张档案）。记录用**受认可的创建入口**（`history.api.create`）建立，因为 history 域拒绝直接写。
- 前两批纯模块的用例与差分随本批一起回归（客户字段规则 15 + 买家决策 26 + 文档档位 30 = **71** 个通过；两份固定源差分此前各自 200+149 与 260 批差异 0，本批未改那些模块的判定，只把 `int()` 强制转换去掉以适配原生字符串标识 —— 决策差分重跑仍差异 0）。
- 静态检查：架构 **0** 违规（新文件在既有 `masters` 域内；DocType 变更不新增跨域方向）；`i18n-build --check` PASS；`check-i18n` REPORT-ONLY；`git diff --check` 干净。
- 线上来源：交付 `evidence/baseline-forty-fifth-delivery.json`（`838ced1d…`，采集 18:07:39Z）与第四十四批交付快照 `compare` **PASS**；运行投影 `push-runtime-forty-fifth-delivery.json`（`e626a05f…`）与上一批**逐项相同**。

### 仍然没做开工采集

同前几批：`before` 用上一批交付快照，它证明"上一批交付到现在线上没变"，不等于"本窗口开工那一刻的身份"。

## 四、待决策项与下一批

- **待决策（跨域）**：识别记录的归属改挂/解绑要不要开一条受认可的路径（history 复核契约）。开了之后，本模块的删除可以再提供"级联解绑"选项，与旧侧一致；现在删带票客户是拒绝的。
- **下一批（主数据第三块收尾 + 第四块起步）**：客户导入归一（`client_import_routes.py`）、商品与单位（`products/units.py` 244 行、`names.py`）、供应商与科目/税率档（`supplier_posting_routes.py`、`tax_profile_routes.py` 的确定性部分）、设置与分类（`settings_routes.py`、`categories_routes.py`）。

## 五、未做

- 未接前端、未部署、未推送、未合回主线；未跑真实客户数据；未动共享底座（`features.py`、`pearnly_core/**`、各产品术语表）；未做用户验收。
