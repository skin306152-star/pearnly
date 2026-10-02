# 对账结果行批量写入（2026-10-02）

压测窗口 m4 抓到的 60–100 多秒长事务（识别窗口核对）：`reconciliation/bank_recon_service._persist_rows` 每一行走一次完整的
Frappe 文档插入，一份对账几百行，和任务头在同一个事务里，事务开着的时间随行数线性变长，拖住 purge、占着连接。

## 改法

- `_persist_rows`：一条 `frappe.db.bulk_insert`（每 500 行一句），仍在调用方（任务头）的事务里，由调用方提交。
- 行控制器的把关原样保留：只有本服务写这些行（不经白名单入口，`persist` 不再被调用）；工作空间权限
  `bank_recon_access.require` 查一次（一份对账的行同属一个工作空间）；名称照旧取 `record_id`。
- Frappe 逐行插入时的校验照同一口径先做（`_checked_row`）：选项必须在列表里、文字不超过字段长度，不合的整份报错、一行也不写；
  空日期存 NULL；`owner` / `modified_by` 为当前用户，`docstatus` / `idx` 为 0。
- 三个调用方（新建、复核确认、`revision` 重算）都是先写任务头再写行；`revision` 先删旧行，不变。

## 验证

- `tests/test_cowork_recon_bulk_rows.py`（新增，进候选离线道）：一次批量写入、名称与字段同逐行插入、权限一次、坏值一行不写、空日期 NULL。
- 对账相关离线测试 `test_cowork_recon_scanned_docs`（121）、`bank_warnings`（19）、`recon_presets`（26）、`reconciliation_gl_sheets`（32）全过。
- 待补：真实库上写入回读（测试租户跑一套银行对账，核对行数、字段、事务时长），随下一批的闸②一起看。
