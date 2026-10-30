# 批次 / 有效期彻底删除（2026-10-30）

用户 10-30：「批次管理 / 有效期管理」要彻底删掉。10-29（`5bcd373d`）只从页面去掉、列隐藏保留，用户在原生字段选择 / 筛选 / 报表视图里还看得到。
主会话口径：按类彻底删，DocType 定义、代码引用、翻译、库里的列一起删，存量值不迁移；小评估写这里，不必等审。

## 小评估

- **范围（全仓 grep `track_batch|track_expiry|batch_no|expiry_date`）**：
  | 对象 | 字段 | 处理 |
  | --- | --- | --- |
  | ERP Product | track_batch、track_expiry（Check，隐藏） | 删字段 + 删列 |
  | Cowork Product | track_batch、track_expiry（Check） | 删字段 + 删列；`masters/products.py` 的 `PRODUCT_FLAG_FIELDS` 只留 `is_weighed` |
  | Purchase Line | batch_no（Data）、expiry_date（Date），隐藏只读 | 删字段 + 删列 |
  | ERP Stock Entry Line | batch_no（Data），隐藏只读 | 删字段 + 删列 |
- **代码引用**：`products/service.py` 新建商品接口里略过旧键的那段（只留略过 `origin`）、`purchasing/output.py` 导出列排除名单里的两列；页面、打印、导入导出模板、CC 网页、CC 桌面版都已没有引用（10-29 删过）。
- **不改的同名词（不是这两个功能）**：
  - `public/desk/pos/official-pos_*.js`、`pearnly-pos-adapter.js` 里的 `batch_no` / `has_batch_no` 是官方收银台界面自带的概念，适配层恒回 0、不对应我们的字段；
  - DMS 身份证识别的 `expiry_date_be`（证件有效期）；
  - Cowork「识别批次」「汇总入账」等 batch 词条；
  - **采购识别提示词**（`purchasing/recognition.py:22`）里列着 `batch_no, expiry_date`：识别结果里这两项本来就不写入任何字段；改提示词按「识别改动上线闸门」要过金标评测，这次不动，下次动识别提示词时一并去掉。
- **翻译**：ERP 术语表删 4 行（Track batches / Track expiry 及其键、Batch No、Expiry Date），四种语言翻译包各删 6 行（含按英文原文补的别名行）；Cowork 术语表删 2 行（Track Batch / Track Expiry），翻译包各删 2 行。
  保留「Item is removed since no serial / batch no selected.」（官方收银台界面还在用）。`i18n-build.py --check` 通过。
- **数据库列**：Frappe 模型同步只加 / 改列不删列。补丁 `pearnly_erp_portal.patches.v1_21_drop_batch_expiry_columns`、`pearnly_cowork.patches.v0_0_27_drop_batch_expiry_columns`
  （post_model_sync）有列才删、删前打印每列有值的行数（发布回读用）；存量值不迁移（没有代码读）。顺手删掉提到这几列的原生个人视图设置（`__UserSettings`），
  免得存着的报表视图去查不存在的列（下次打开回到默认视图）。
- **上下游**：Express 不涉及（这几列从没写进 Express）；CC 桌面版载荷不涉及；Cowork 商品主档同一口径。
- **隔离 / 权限 / 成本 / 文案**：不涉及（只删）。
- **测试**：流程 `erp.products.create`、`erp.purchase.create` 改成断言库里列已不存在；`scripts/cowork-masters-lab-verify.py`、`scripts/erp-pos-lab-products-verify.py` 去掉对这两列的期望。
