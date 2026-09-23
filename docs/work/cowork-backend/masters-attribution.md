# 主数据最后两条路由的归属核对（第四十九批 · 只读）

> 后续结论（第五十一批）：用户要求“只用于展示的就不要了”。分类标签、补全、统计与供应商名称富化不迁；
> 有业务依赖的分类规则/公司树/供应商记忆已补齐，重复提醒与个人开关也已交付。
> M1 名称闸只在账套主体，不在普通买方；其工单分拣消费者不迁。
> **第五十二批已补独立账套导入**：旧 `/api/workspace/clients/import/*` 对应新 `masters.workspace_api`，
> 现有买方 RPC 不改义。见 [账套导入合同](workspace-import.md)；分类依据仍见 [第五十一批合同](invoice-classification.md)。
> 下文保留第四十九批调查时点，不作为最新完整性结论。

主数据只剩"供应商/科目档"与"设置与分类"。开工前先把旧侧四条路由逐条核清归属，**再决定动不动手**：
是 Cowork 的就接着迁，属别的产品线就按"不迁"登记，不硬搬。本文只读，不改代码、不写库。

固定源 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`（只读副本
`/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`）。核对口径：读路由的权限码与
feature flag → 读它调用的 DAL/服务 → 读这些服务的**消费者**（谁写、谁读、在哪条产品线上），
再对照 `feature-status.md` 第二节"不属于 Cowork"的产品线清单。

## 一、四条路由的结论

| 路由（旧侧行数） | 权限 / 闸 | 数据与消费者 | 结论 |
| --- | --- | --- | --- |
| `routes/supplier_posting_routes.py`（98） | 采购域 `purchase.doc.view` / `purchase.supplier.manage` | 表 `supplier_posting_profiles`；值域从 **Cowork 自己的** `services/ocr_history/posting_manual.py` 取；喂 `services/erp/express_push/common.py` 的 `payment_verdict` 第四级判据（**Cowork 推送**） | **属 Cowork**：档案本体是本仓既有的 `Cowork Supplier Posting Profile` 与 `masters/push_inputs.save_profile`。旧侧只是把它挂在采购域权限下。本批补完（值域、部分更新、来源优先级、幂等删除、读/删接口） |
| `routes/categories_routes.py`（35） | 成员即可（无细码） | 读 `services/clients/store.py` 的 `supplier_categories`（供应商→费用分类记忆）：**写**在 `routes/history_routes.py` 保存/复核识别记录时，**读**在 `services/ocr/recognize/persist.py` 识别落库时（同卖家学过的分类优先于模型猜测），本路由给前端 datalist + "已记住 N 个供应商" | **属 Cowork 的识别/录入线**（不是主数据）：记忆表与两个消费者都在识别记录这条线上。**但它依赖采购域的"费用科目树"做净化**（见第二节第 2 问），本仓两者都没有 |
| `routes/settings_routes.py`（132） | 归档：`_plan_permissions` 的 `can_archive`/`can_customize_archive`（Plus/Pro）；重复检测：成员即可 | 归档 3 端点读写 `services/archive`（`DEFAULT_TEMPLATE`/`preview_name`）+ `archive_settings` 表；重复检测 2 端点读写 `users.dup_check_enabled`，**消费者是 `services/ocr/recognize/persist.py` 的"入库前重复检测"**（检测到只记警告、不阻断，让前端决定） | **一半一半**：归档 3 端点属**归档线**（`feature-status` 已把 `archive` 登记为不在 Cowork 范围）→ **不迁**；重复检测 2 端点属 **Cowork 的识别线** → 见第二节第 1 问（本仓既无偏好存储也无警告位，属缺口，不硬搬） |
| `routes/tax_profile_routes.py`（411） | `settings.workspace.manage`（画像/别名）+ `tax.filing.view`（矩阵），整组挂 feature flag `pearnly_ai_m1`（关 → 404 fail-closed） | `services/workspace/tax_profile_store`（客户税务画像）、`client_alias_store`（客户别名）、`services/workorder/{obligation_engine,matrix,profile_inference,wht_signals}`（义务清单/事务所矩阵/推断） | **不迁**：税务申报 + 工单 + 账套主体资料，属 `tax`/`accounting`/`workorder` 产品线；`client_name_aliases` 只被这一族使用（全仓搜索确认）。**登记为"不迁"，不算欠账** |

## 二、需要用户点头的三件事（都不是本批能自己定的）

1. **重复发票检测要不要在新平台保留？** 旧侧：识别落库前先比票号，未命中再比日期/卖家/金额，
   命中就**只记警告、不阻断**，把处理交给前端；开关存在用户偏好里（默认开）。
   本仓现状：`history` 域**既没有**这个偏好存储，**也没有**记录上的警告位（域内现有的
   "duplicate" 校验只管"同一次请求里名字不能重复"和"页级副本"）。要迁就得新增偏好存储 +
   记录上的警告位 + 落库路径的一次查库，属识别记录写入契约的扩展。
2. **费用科目树 + 供应商→分类记忆归谁？** 旧侧树在 `services/purchase/categories.py`
   （两级费用科目、按 (租户, 套账) 懒种子泰国标准科目），记忆表 `supplier_categories` 由识别保存写、
   识别落库读。用户边界是"商品/供应商/科目**各产品自己维护一份**"——若照此，Cowork 需要自己的
   一份两级费用科目树（含泰国预设）+ 记忆表；若"科目以 ERP 为准"，则这两块登记为不迁，
   代价是识别时的分类回填（学到过就优先）在新平台没有对应物。
3. **供应商主档要不要给 Cowork 建一份？** 旧侧供应商过账档案的列表读侧富化借的是**采购域**
   `suppliers` 主档（`LEFT JOIN suppliers` 取名）；本仓没有供应商主档，所以本批的列表只给税号。
   按"供应商各产品自己维护一份"，Cowork 自己那份是新产品功能（不是旧路由的重建）。

顺带核出的两条**已存在的缺口**（不新增，只是这次一并记下）：

- **M1 泰文注册名闸**：旧侧建/改账套主体时，若 `pearnly_ai_m1` 开着，要求名称含泰文字符
  （`workspace.thai_name_required`），已登记的泰文名还不许清空（`workspace.thai_name_locked`）——
  缘由是税号被读花时靠它兜底认方向。本仓客户名册没有这道闸，也没有对应的产品决定记录。
- **服务层文案未补术语表**：第四十五/四十七/四十九批新增的英文 `_()` 文案还没进
  `pearnly_cowork/i18n/messages.tsv`。`i18n-build --check` 只看术语表自身一致，不代表这些
  文案有四语译文 —— 建议做一次性的四语补录（属词库工作，不属主数据）。

## 三、本批据此做的动作

- 供应商过账档案：**接着迁**，交付见 [masters-supplier-posting](masters-supplier-posting.md)；
- 客户名录导入端点：**接着迁**（旧侧 `client_import_routes.py` 的三态编排），同上；
- 归档设置 3 端点、税务画像族：**登记不迁**，已在 `feature-status.md` 第二节核对口径下；
- 设置与分类、供应商主档、M1 泰文名闸：**停在归属结论上等用户决定**，没有动手。
