# 对账结果行批量写入（2026-10-02）

压测窗口 m4 抓到的 60–100 多秒长事务（识别窗口核对）：`reconciliation/bank_recon_service._persist_rows` 每一行走一次完整的
Frappe 文档插入，一份对账几百行，和任务头在同一个事务里，事务开着的时间随行数线性变长，拖住 purge、占着连接。

## 范围（查了全部对账结果写入路径）

| 路径 | 原来 | 现在 |
| --- | --- | --- |
| 银行对账：新建、复核确认、`revision` 重算 | 逐行 `persist(get_doc)` | `bank_recon_service._persist_rows` → 批量 |
| 收入对账：新建（`income_recon_service.create`）、`revision` 重算 | 逐行 `persist(get_doc)` | `income_recon_service._persist_rows` → 批量 |
| 销项税报告核查（`vat_check_service`） | 只写一条任务头，没有结果行表 | 不用改 |
| `bank_evidence` | 一次一条凭证，不是批量行 | 不用改 |

## 改法

- 共用 `recon_documents.bulk_insert_rows(doctype, access, task, values)`：一条 `frappe.db.bulk_insert`（每 500 行一句），仍在调用方（任务头）的事务里，由调用方提交；银行、收入的 `_persist_rows` 都走它。
- 行控制器的把关原样保留：只有本服务写这些行（不经白名单入口，`persist` 不再被调用）；工作空间权限
  `bank_recon_access.require` 查一次（一份对账的行同属一个工作空间）；名称照旧取 `record_id`。
- Frappe 逐行插入时的校验照同一口径先做（`_checked_row`）：选项必须在列表里、文字不超过字段长度，不合的整份报错、一行也不写；
  空日期存 NULL；`owner` / `modified_by` 为当前用户，`docstatus` / `idx` 为 0。
- 三个调用方（新建、复核确认、`revision` 重算）都是先写任务头再写行；`revision` 先删旧行，不变。

## 验证

- `tests/test_cowork_recon_bulk_rows.py`（新增，进候选离线道）：银行、收入两张行表各一次批量写入、名称与字段同逐行插入、权限一次、坏值一行不写、空日期 NULL；三处调用方源码里不再有逐行 `persist(get_doc(ROW…))`。
- 收入对账离线测试（contracts、export、parse_info、income_vat）与对账相关离线测试 `test_cowork_recon_scanned_docs`（121）、`bank_warnings`（19）、`recon_presets`（26）、`reconciliation_gl_sheets`（32）全过。
- 待补：真实库上写入回读（测试租户跑一套银行对账，核对行数、字段、事务时长），随下一批的闸②一起看。
