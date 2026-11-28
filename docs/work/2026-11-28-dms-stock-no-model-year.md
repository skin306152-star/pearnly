# DMS 库存停放报表：表头不写年款

分支 `pearnly/dms-stock-no-year`，基线 `origin/main` 568f4b7f6（= 线上）。改已有输出文案，不改入口、权限、判据。

## 用户决定

用户 2026-11-27 晚原话：「26，my 可以在标题里取消掉，不写，交接进去，明天并进主线一起做」。

## 改动

- `query/stock_report.py`：`summarize` 不再算、不再返回 `year`（删 `_year_label`）。
- `query/stock_render.py`：`header_text(scope)` 不再拼「YY MY」；汇总图、PDF、Excel 同一处取表头。
- `i18n/messages.tsv`：两条表头词条去掉 `{my}` 占位。输出与此前「年款不一致 / 没填」时完全相同（真实经销商 11-26 那份原件就是这种情况），只是年份全一致时也不再出年款。
- 查同类：卡片副标题（`{count} 台 · {date} {time}`）、下载页标题、汇总文件名（`库存停放报表 [日期_时间]`）、原件文件名（MR.ERP 原名）都没有年款，不用改。车型快照里的 ปีที่ผลิต 字段保留（快照结构不动）。

## 文案清单（check-copy --report 第 1 节）

| 来源·键 | 中文 旧 → 新 | English | ไทย | 日本語 |
|---|---|---|---|---|
| pearnly_dms·dms.card.report.HEAD_AVAILABLE | {my} 空车 → 空车 | {my} Available → Available | รถรุ่น {my} ว่าง → รถรุ่น ว่าง | {my} 空き車 → 空き車 |
| pearnly_dms·dms.card.report.HEAD_OTHER | {my} 车辆 → 车辆 | {my} Vehicles → Vehicles | รถรุ่น {my} → รถรุ่น | {my} 車両 → 車両 |

## 验证

见文末（闸门、候选离线、离线重出图）。

### 离线重出图（2026-11-28，线上镜像 4cf9c586 + 本分支源码 / main 源码对照）

用正式租户 11-26 那份原件（92 台）+ 线上车型主档快照，四语 × 空车 / 全部各出一遍：

- 真实主档（年份混合）：新旧汇总图逐字节相同，表头本来就不写年款；只有 Excel 的文件元数据不同。
- 把主档年份全改成 2025 再出：旧代码表头「รถรุ่น 25 MY ว่าง」/「25 MY 空车」…，新代码四语都是「รถรุ่น ว่าง」「空车」「Available」「空き車」；两图差异只在表头左格（像素框 106–271 × 10–39），其余版式不变。
- 对照图与脚本：`~/pearnly-devtools/handoff-1128/dms-no-year/`（`表头-旧上-新下.png`、`旧-年份一致时-空车.jpg`、`新-年份一致时-空车.jpg`、`新-11-26原件-空车.jpg`）。

注意：词条改在 `messages.tsv` 后要跑 `python3 scripts/i18n-build.py` 重新生成 `translations/*.csv`，否则运行时仍读旧词条（第一次重出图时表头出现字面量 `{my}`，就是这个原因）。
