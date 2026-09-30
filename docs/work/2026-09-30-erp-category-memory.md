# ERP 采购明细按「供应商 + 品名」记分类（2026-09-30）

- 分支：`codex/erp-item-category-memory`，基线 `16373bff`。
- 起因：用户问 Cowork 的分类记忆（2026-09-30 改成「卖方 + 品名」）能否在 ERP 通用。ERP 采购明细有分类 / 子分类，但没有记忆，
  LINE 录单也没有选分类的地方，入账的明细全是空分类。与「旧项目迁移后的用户体验问题」窗口约定：它先合入 ERP 识别改动，本任务再做。

## 做法（与 Cowork 同一规则）

| 时机 | 入口 | 行为 |
|---|---|---|
| 识别 | `purchasing/recognition.recognise`（网页 + LINE 共用） | 认得出唯一的已有供应商时，给空着分类的行预填；网页上人可以改。 |
| 入账 | `purchasing/entry._post`，仅 `source == "line"` | LINE 录单在确认供应商后补空着的行（网页不补，人清空的分类不会被悄悄填回）。 |
| 学习 | `purchasing/entry._post` 保存后 | 每行最终分类按「供应商 + 品名」记下，后一次覆盖前一次。 |

- 套用顺序：同一供应商同一品名 → 这个供应商记过的品名全是同一个分类时按供应商兜底（卖多种东西的供应商、「一般供应商」不猜）。
- 已停用 / 不再是顶层的大类不套；子分类不再挂在该大类下时只套大类。只填空的，不改人选的。
- 记忆表 `ERP Purchase Category Memory`（仅 System Manager 可见，服务端读写），品名比对忽略大小写、空白和标点（与 Cowork `_item_key` 同一口径）。

## 验证

- 本机：`tests/test_erp_category_memory.py` 7 条（品名优先、供应商兜底只在单一分类时、不改已选、后者覆盖、停用分类不套、歧义供应商不认）；`check-architecture.py` PASS。
- 生产 ERP 镜像 `4c2c12ed`（五个 App 源码只读覆盖，`--network none --memory 2g`）：`pearnly_erp_portal.integrations.test_line_intake` 82 条 OK；改动模块与新 DocType 控制器可导入。
- 未部署；新 DocType 随部署时 `migrate` 建表。未在真实站点入账验证，部署后需在 ERP 录一张采购单选分类，再识别同供应商同品名看是否预填。
