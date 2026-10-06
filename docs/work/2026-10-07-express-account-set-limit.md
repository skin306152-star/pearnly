# Express 账套上报只存前 50 个 + 心跳每分钟整份重写（2026-10-07）

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

账套上限部分：报告规则 21 项、旧仓对照测试 1262 场景 PASS（见下方验证）。

## 二、心跳每分钟整份重写上报记录（用户 10-07：一起处理）

### 现象与量级
小助手每分钟心跳一次，`connections/collector.py` `ingest()` 每次都把连接这一行整行保存（`snapshots.publish` 里再存一次）。
上报记录里带着每次都会变的时间戳（`agent_last_seen_at`、`*_seen_at`），所以内容没变也整份重写。
生产只读核实：MariaDB `log_bin=ON`、`binlog_format=MIXED`、`binlog_row_image=FULL`、保留 7 天；
连接记录生产约 600KB、测试机约 3.1MB。一台小助手全天在线 ≈ 1440 次 × 2 次保存 × 600KB ≈ 1.7GB/天日志，7 天约 12GB；客户越多越大。

### 读写核对
| 字段 | 谁读 | 处理 |
|---|---|---|
| `report_json.agent_last_seen_at` | 只有 `companion/credentials.py` `_online`（在线判断：网页选目标、推送确认、文件夹自动录入） | 改记在当前密钥行 `last_seen_at` / `last_seen_generation`（小行，只改两列），`_online` 读那里 |
| `account_sets_seen_at` 等 `*_seen_at` | 无人读 | 不再写进上报记录 |
| `report_received_at` | 无人读 | 只在上报内容变化时更新（含义变为「内容最近一次变化」） |
| `_account_caches[..]._seen_at` | 淘汰最旧账套缓存 | 不变（只在按账套刷新时写） |
| `projection_heads` | 目录指针 | `snapshots.publish` 指针没变就不写连接行 |

### 改法
- 心跳入口 `companion/heartbeat.py` 调 `credentials.record_seen`：报到只改密钥行两列；小助手说下线就清空。
- `collector.ingest`：去掉易变时间戳后，上报内容、配置、上报凭据都没变就不保存连接行；变了只写一次。
- `snapshots.publish`：目录指针没变不保存连接行（快照本身原来就按内容指纹去重）。
- 凭据表加两列（原生 DocType 字段）；补丁 `v0_0_13_agent_last_seen` 把升级前最后一次报到搬过去，升级后不出现一分钟「离线」。
- 小助手不用改；连接层不反向依赖小助手层（报到在心跳入口记）。

### 没改的
- 每次请求（心跳、每 3 秒一次的取任务）鉴权时仍会读整条连接记录（只读，不进日志），另记。
- 旧版代码切回时：它读上报记录里的报到时间，下一次心跳就写回，最多一分钟显示离线。

## 验证
- 生产同款镜像：`test_cowork_report_writes`（新增：内容不变不写、变了写一次、下线写一次、目录指针不变不写）、
  `test_cowork_companion_liveness`（改读密钥行）、account_caches / companion_reports / connections / targets /
  preparation_dynamic / express_preflight / folder_intake / companion_http_auth 全部 OK；新增与在线判断测试接进生产构建离线检查。
- 测试机（10-07 04:42–04:46，借用「条件咨询」窗口 5 分钟，用完切回）：
  - 迁移后立刻判在线（补丁生效）；第一次心跳：账套清单 100 个全部存下、旧时间戳去掉，写一次；
  - 之后两次心跳：密钥行报到时间正常前进，连接行修改时间与内容指纹完全不变，在线判断一直为真；
  - 把一张 T8BAKEL（排 50 名外）的票按网页「提交」同样流程推送：成功写进 Express，单号 HS681219-001（修复前报「小助手没报上账套」）。

## 进度
- 10-07：两项定位、修复、镜像与测试机验证。
