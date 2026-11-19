# 原生 SQL 的时间改用框架口径（2026-11-19）

分支 `pearnly/sql-clock-framework-now`，基线 origin/main `1a91b178b`。来源：流程工具窗口 11-17——GitHub 全套 43868347334 的
cowork.recon.exports [old/desktop] 新增 2 行 Cowork Recon View，按站点时钟「creation >= 本格开始」查不到（实验室库是 UTC、站点是
Asia/Bangkok，差 7 小时）。主会话 11-19 派（低优先）。

## 盘点（产品代码 9 处）
- Cowork `recon_center/view_store.put`：INSERT creation / modified = NOW(6)，ON DUPLICATE modified = NOW(6)（按数据库时区）。
- DMS `snapshots/store.py` 6 处（`_ensure` 建行的 creation + modified，以及 staging / overlays ×2 / request_json 的 modified）、
  `snapshots/manual.py` 2 处（refresh_request 的 modified）：UTC_TIMESTAMP(6)。
这些列只作记录：DMS 快照的过期与请求判断用 `source_started_at` / `completed_at` / `requested_at` 纪元秒；对账缓存靠 `fingerprint`
判新旧。改成框架口径不改业务行为。

## 改动
- 9 处都改成从 Python 传 `frappe.utils.now()`（站点时区，与 Frappe 自己写的 creation / modified 一致）；DMS 用 `store._now()` 一处取。
- 新闸门 `scripts/check-sql-clock.py` + `tests/test_check_sql_clock.py`（CI 一步）：`apps/` 下产品 Python 的 SQL 串里出现
  NOW( / UTC_TIMESTAMP / CURRENT_TIMESTAMP / SYSDATE( / CURDATE( / UTC_DATE / CURRENT_DATE 就失败；文档字符串、非 SQL 的说明文字不算
  （本仓 SQL 一律大写，只认大写）。改前 DMS store.py 命中 5 行（共 6 处）。

## 验证
DMS 快照相关单测 8 组通过；Cowork 对账缓存单测要 Frappe 环境，在候选离线检查里跑。全闸门、候选、流程见主会话汇报。
