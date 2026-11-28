# CC 页面上的服务端时间带秒和六位小数

分支 `pearnly/cc-when-naive-datetime`，基线 `origin/main` 568f4b7f6（= 线上）。缺陷修复，不改入口、文案、权限。

## 现象

用户 11-28 在测试机（CC 正式版 1.12.25，测试租户）设置 → 连接看到 MR.ERP 一行「已授权：<账号> · <电脑名> · 2026-11-28 11:08:35.095638」，问后面那串数字是什么。

## 查证：两条通道各怎么存、怎么给

| 通道 | 存 | 给页面 | 结果 |
| --- | --- | --- | --- |
| ERP `mrerp/connection.py`（records / login_store） | `now_datetime()`，站点时区裸时间 | `str()` 原样 | 页面 `formatWhen` 认不出无时区的，原样显示（本次现象） |
| Cowork `connections/mrerp_login.py` | `datetime.now(UTC)` 去掉时区，即 UTC 裸时间 | `_when` 补 UTC → RFC3339 | 页面正常 |
| ERP `masters/cc_service.when`、Cowork `companies/service.when` | 站点时区裸时间 | `contracts.utc_iso` → RFC3339 UTC | 正常（公司卡、记住的选择、草稿） |
| Cowork `folder_intake/rules.runs`（定时任务运行记录） | 站点时区裸时间 | 原样（JSON 里是「YYYY-MM-DD HH:MM:SS.ffffff」） | 页面 `new Date(...).toLocaleString()`：带秒、按浏览器语言不是页面语言 |

记一笔不在本次范围的：Cowork 把 MR.ERP 授权时刻按 UTC 存进 Frappe 的 Datetime 字段（Frappe 约定是站点时区），在 Desk 里直接看这条连接会早 7 小时；CC 页面经 `_when` 显示正确。改它要迁移存量，没动。

## 修法

服务端给带时区的时间，页面再兜底：

- ERP `mrerp/connection.py`：`credential.at` 经 `_when` → RFC3339 UTC（records 两处、login_store 一处）。
- Cowork `folder_intake/rules.runs`：`started_at`、`scanned_at` → RFC3339 UTC；交给模型的 `folder_task_runs` 点名这两个字段按 UTC 换回当地时间（模型看到的仍是「2026-10-13 09:00」）。
- 前端 `lib/when.ts` 新增 `serverTime`：只有日期的按日期；带时区的按设备时区；**不带时区的 Frappe 时间照它的钟点**（不猜时区，同 10 月「补 Z 晚 7 小时」的教训），一律写到「日期 + 时:分」，不出秒和小数。`formatWhen`、月结卡 `dayShort` / `dayTime` 共用它；定时任务运行记录改用 `formatWhen`。四语同一套（泰文佛历）：en「Nov 28, 2026, 11:08 AM」、zh「2026年11月28日 11:08」、th「28 พ.ย. 2569 11:08」、ja「2026/11/28 11:08」。

全仓查同类（直接把服务端时间字符串丢到界面上）：前端只有定时任务运行记录一处 `toLocaleString`，已改；其余 `new Date(...)` 都是排序比较，不显示。

## 测试

- `tests/test_assistant_ui.py` `ServerTimeTests`：四语 × 四种裸时间写法都是「日期 + 时:分」；页面里不许再有 `toLocaleString` 类直出。原有「不带时区原样」断言改为照钟点到时分。
- `tests/test_cc_month_ledger.py`：`dayTime` / `dayShort` 吃裸时间。
- `tests/test_erp_mrerp_direct.py`：ERP 授权时刻（datetime 或字符串）→ `2026-11-28T04:08:35.095638Z`（站点 Asia/Bangkok）。
- `tests/test_cowork_folder_intake.py` `RunTimes`、`tests/test_cowork_assistant_tools.py`：运行记录给 UTC、模型那份换回当地时间。

## 没测到

真机 / 测试机上的 CC 设置页（改动在服务端与前端构建，测试机1 跑的是正式版 1.12.25 + 线上站点，要等上线后看）。
