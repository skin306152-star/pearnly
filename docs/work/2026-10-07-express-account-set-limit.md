# Express 账套上报只存前 50 个（2026-10-07）

## 现象
文件夹自动录入测试中 41 份有 4 份推送失败，提示「准备推送时小助手不在线，或还没报上这份账套」，而小助手实际在线。
测试机 `C:\PearnlyExpress\70EXP` 有 105 个目录，小助手认出 100 个有效账套全部上报；服务器 `report_json.reported_account_sets` 只有 50 个，T8BAKEL 不在其中。

## 原因
`connections/reports.py` `_MAX_ACCOUNT_SETS = 50`（照搬旧仓 `services/erp/express_push/agent_account_sets.py`，固定源 ec2e5c95 同为 50，旧生产同样有此问题）。
网页可选的账套来自目录投影（`targets/projection.py` `MAX_ACCOUNT_SETS = 5000`），推送前的核对读的是被截的 50 个：

| 读这份清单的地方 | 用途 | 截断后果 |
|---|---|---|
| `connections/preparation.py` `collect` → `reported_writable` | 准备推送时判小助手是否报过这份账套可写，决定有无确认凭据 | 非默认账套排 50 名外 → `push.companion_account_unreported` |
| `push/express/account_rules.py` `authorized_account_sets` / `account_set_allowed`（`push/express/preflight.py` 两处） | 推送预检白名单与数据目录配对 | `account_set_not_allowed` |
| `targets/selection.py`、`targets/freeze.py` `reported_account_set_roots` | 选目标 / 冻结目标时核数据目录与程序目录配对 | 只剩投影快照一条证据 |
| `targets/choices.py` | 投影为空时的账套选项兜底 | 选项只到 50 个 |

小助手默认账套不经这份清单，不受影响。影响所有入口：网页「提交」、LINE、文件夹自动录入。

## 生产实况（10-07 只读查询一次）
- erpcowork：连接 `S:\2569\EXP69`（程序目录 `P:\69EXP`）上报正好 50 个 → 已被截；历史推送原因里暂无这条失败。
- testcowork：办公室测试机连接同样 50 个。

## 修法
上报缓存与目录投影用同一个上限（`_MAX_ACCOUNT_SETS = MAX_ACCOUNT_SETS`）：能在目录里选到的账套，推送核对也看得到。
旧仓对照测试（`tests/test_cowork_companion_reports_source_parity.py`）登记为有意改动，其余 1262 个场景逐值一致。

## 影响评估
- 体积：每个账套约 560 字节。100 个约 56KB、500 个约 280KB；上限 5000 个约 2.8MB，仍在单条上报 16MB 闸内。
- 写入：小助手每分钟上报一次，`report_json` 整份重写（生产约 600KB/条，测试机因按账套缓存约 3.1MB）。本改动只增加账套多于 50 个的连接的那一部分。
- 小助手不用改、不用发版；只动服务端一个常量，无表结构变化。
- 其它上限（科目表 3000、商品 20000、存货科目组 100）不变。

## 验证
- 本机：报告规则 21 项、对照测试 1262 场景 PASS；生产同款镜像里 targets / account_caches / express_preflight / preparation_dynamic / companion_reports / connections 全部 OK。
- 测试机：见下方进度。

## 进度
- 10-07：定位、修复、本机与镜像验证。
