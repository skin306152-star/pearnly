# 主数据第七块：供应商过账档案的 Cowork 半 + 客户名录导入端点（第四十九批）

第四十九批从新平台 `22b18d7`（第四十八批交接提交）开工，工作树
`/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支
`codex/cowork-backend-migration`。开工采集线上来源 PASS：`ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`
（`evidence/baseline-forty-ninth-start.json`，sha256 `5d830313…`），与固定只读副本一致。
不接前端、不部署、不改 DMS、不写旧库、不调模型、不动另一窗口的 ERP 侧文件。

本批先把主数据最后两条旧路由的**归属**逐条核对清楚（只读，见
[masters-attribution](masters-attribution.md)），再把其中确属 Cowork 的部分补完：
**供应商过账档案**的规则层与三个接口，以及**客户名录导入端点**的三态编排。

## 一、本批交付

| 新落点 | 来源（sha256 固定） | 内容 |
| --- | --- | --- |
| `masters/field_clean.py`（新） | `services/purchase/field_clean.py`（`43912e65…`）的 `clean_tax_id`/`clean_branch_no` | 旧侧这两个清洗函数由**供应商过账档案路由**与**客户名录导入**共用；本仓同样只留一份，两边 import 这里（`masters/client_import.py` 改为从这里取，导出名不变） |
| `masters/supplier_posting.py`（新） | `routes/supplier_posting_routes.py`（`210dfe27…`，98 行）+ `services/purchase/supplier_posting.py`（`a121f3ca…`）+ 值域同源 `services/ocr_history/posting_manual.py`（`35883a49…`） | 值域（cash/credit、goods/expense）、税号清洗、用户入口与内部回流的两种空值口径、CASE/COALESCE 的部分更新语义、`user_rule` 不被 `correction` 覆盖、删除幂等、"有没有效果"的判定 |
| `masters/push_inputs.py`（改） | 同上 | `save_profile` 改为走纯模块（值域不再另抄一份）；新增 `list_profiles`（按修改时间倒序、limit ≤ 500、只列**有效**档案）与 `clear_profile`（清掉效果、幂等） |
| `masters/clients.py`（改） | `services/workspace/store.py` 的 `tax_id_in_use`（`exclude_id` 语义）+ `routes/client_import_routes.py`（三态） | 同公司税号查重 `tax_id_taken` / `normalized_tax_ids`；建档与更新加同一道闸；`preview_import` / `commit_import` 逐行三态（valid/skip/error → created/skip/error） |
| `masters/api.py`（改） | 同上 | 五个新 RPC：`supplier_profiles`、`delete_supplier_profile`、`preview_client_import`、`commit_client_import`；`save_supplier_profile` 补"用户入口不接空串" |

保留的部署版口径（逐条）：

- **值域与空值**：付款方式 `cash|credit`、品项类型 `goods|expense`；给 `None` = 这一次不动该字段。
  旧侧路由（用户入口）连**空串**都不收（422 `purchase.line_invalid` + detail
  `default_payment_invalid`/`default_item_type_invalid`），而内部纠错回流可以写空串 —— 本仓用
  `input_error(..., allow_empty=)` 把这两种入口分开，RPC 走严的一路。
- **档案锚是卖方税号**：剥非数字后**恰好 13 位**才算合法；用户入口非法即 422
  （`purchase.tax_id_invalid`），删除路径**不校验**（旧侧一样，幂等 no-op）。
- **部分更新**：只有本次给了值的字段才写；首次建档未给的字段落**空串**。
- **来源优先级**：已有 `user_rule` 行不被 `correction` 覆盖 —— 整行不动，**连 source 都不改**
  （旧 SQL 的 `WHERE source <> 'user_rule' OR EXCLUDED.source = 'user_rule'`）；`user_rule`
  可覆盖任意来源。
- **删除幂等**：没有这一行时返回 `deleted=False`，不是失败。
- **名录导入三态**：缺名 → `client_import.err_missing_name`；税号给了但不合法 →
  `client_import.err_bad_tax_id`；税号已被同公司在用客户占用 → `skip` +
  `workspace.tax_id_duplicate`；其余在预览里 `valid`、落库后 `created`。结果始终回显
  `name`/`tax_id`，报错行也能看出是哪一行（旧侧同款 payload 形状）。
- **行字段映射**：行里的 `phone` 落到原生 `contact_phone`（旧侧 `workspace_clients.phone` 的对应物），
  `branch`/`address`/`tax_id` 同名直落。

### 有意差异

| 旧 | 新 | 理由 |
| --- | --- | --- |
| `DELETE /supplier-profiles/{tax}` **删掉这一行** | 清空两个默认值，行保留（`deleted` 表示"之前有没有效果"） | 本仓的推送输入行是可审计的原生记录、`on_trash` 明确拒绝删除（第十三方批定的契约："空值去掉效果、保留审计行"）。读侧结果与旧侧一致：清掉后不再出现在档案列表，推送第四级判据也拿不到档案 |
| 列表每行带 `supplier_name`（`LEFT JOIN suppliers`） | 列表只给税号，不带名字 | 旧侧借的是**采购域** `suppliers` 主档；本仓还没有供应商主档。按用户边界"供应商各产品自己维护一份"，Cowork 自己那份是新产品功能，不在本批重建旧路由的范围内 —— 登记在 [masters-attribution](masters-attribution.md) 第三节 |
| 税号查重拿**原样字符串**比（`tax_id = %s`） | 双方都剥非数字后比（同号不同写法也算重复） | 客户身份就靠这 13 位：前端查税号先剥非数字、买家匹配与 ERP 对账都按数字认人。留两条同号档案会让匹配指错人。**这条同时作用于单建与更新**（旧侧单建也有这闸；更新带"排除自身"，照搬） |
| 单建/更新只在 `workspace_clients` 上查重 | 同一道闸加在客户名册的建档与更新 | 第四十八批已定：名录导入的建档校验复用本仓客户名册服务；查重必须跟着同一段校验走 |
| 导入的 `parse` 端点（xlsx/csv → 表头 + 行） | 仍不迁 | 解析是器件；本批只交"收行 → 三态 → 复用名册服务"的编排层（第四十八批的纯模块负责列映射与结构校验） |
| 预览里"同一批内重复税号"的第二行仍算 `valid` | 照旧 | 旧侧预览逐行查库（同批第一行还没落库），落库时才 `skip`；本仓保留这个真实行为，lab 用例写明 |

## 二、验证与证据

| 层 | 结果 |
| --- | --- |
| 离线用例（隔离 bench） | 主数据族 **79** 个通过（客户导入 14 + 客户字段规则 + 买家决策 + 商品单位 + 本批供应商档案 14） |
| 固定源差分（容器内 `--source-stdin`） | 供应商档案：**差异 0** —— 值域 20、税号 10、upsert 决策 80 例（其中 `user_rule` 保留 16、首次建档 16）、空锚 3。旧侧 `upsert_profile` 的语句被**捕获后在 SQLite 里真跑**（`%s`→`?`、`now()`→`CURRENT_TIMESTAMP`），比对最终写入的（付款方式、品项类型、来源）三元组；客户导入/客户字段规则/买家决策三条差分同时复跑，仍为 **0 差异**（顺带证明 `field_clean` 抽分子模块没有漂移） |
| 隔离 Site 端到端 | `scripts/cowork-masters-lab-verify.py` **22** 条通过：档案分两次写（只给一个字段时另一个不动）→ 用户入口拒空串/未知值/坏税号 → 纠错不许覆盖 `user_rule`（而它自己那份可以继续写）→ 清效果幂等且审计行仍在 → 预览三态且**不落库** → 落库建 2 条、批内重复按 `skip`、重导全部 `skip`、行字段映射（`phone`→`contact_phone`）→ 单建/更新的同税号拒绝与"自己改自己放行" → 权限（只读能列不能写、跨公司被拒）→ `rows`/权限三态。新 RPC 与既有 RPC 一起做匿名 HTTP：**14/14 = 403** |
| 静态检查 | 架构 **0** 违规；`i18n-build --check` PASS；`check-i18n` REPORT-ONLY；`git diff --check` 干净 |
| 线上来源 | 开工 `baseline-forty-ninth-start.json`（`5d830313…`）与固定副本一致；交付 `baseline-forty-ninth-delivery.json`（`09378898…`）与第四十八批交付快照 `compare` **PASS**；运行投影 `push-runtime-forty-ninth-delivery.json`（`49783bcf…`）与上一批**逐项相同**（`settings_unchanged=true`） |

lab 容器本批启动用于 bench 用例、差分与隔离 Site 验证，收尾已停止；卷、Site 保留给下包。

## 三、未做

- 未建供应商主档（旧侧读侧的 `supplier_name` 因此暂缺）；
- 未做设置与分类（重复发票检测开关、费用科目树 + 供应商→分类记忆）—— 归属已核对完，见
  [masters-attribution](masters-attribution.md)，两项都需要用户点头才能定方向；
- 未接前端、未部署、未推送、未合回主线；未动共享底座；未做用户验收；
- 服务层新增的英文文案（本批 3 条 + 第四十五/四十七批遗留）**尚未补进** `pearnly_cowork` 的
  术语表 `i18n/messages.tsv`（`i18n-build --check` 当前 PASS 只证明术语表自身一致，不证明这些
  文案有四语译文）。这是一条跨批的词库欠账，登记在归属文档里，留待一次性的四语补录。
- `docs/work/cowork-backend/backend-api.md` 仍是第四/十六批时期的接口清单：第四十五、四十七批与
  本批的 masters RPC 都没写进去。前端接入前需要一次统一的接口清单刷新（本批的五个 RPC 见上表），
  本次没有顺手改它，避免只补一半。
