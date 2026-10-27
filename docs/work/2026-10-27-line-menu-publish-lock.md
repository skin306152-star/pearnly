# LINE 菜单首次发布超过锁时长（ERP / Cowork / DMS）

> 基线 `origin/main` 61052381，分支 `pearnly/line-menu-publish-lock`。状态：代码与离线测试完成，**等这一批的验收和部署**（与「新需求理解」窗口协调，用户 2026-10-27：「修完和新需求理解那个窗口告诉他，协调好一起部署」）。

## 现象与原因

- 2026-10-27 新租户 polymer（ISS-2026-0312，PR-2026-0314）开通到「配置 LINE 频道」失败，控制面只显示「Server operation failed; inspect the restricted server log」。
- 生产 `/opt/pearnly/state/last-provision-error.log`（13:08 UTC）：`site-step.py set_line` → `pearnly_erp_portal.integrations.line_menu.publish()` → `cache.lock(..., timeout=120)` 退出时 `redis.exceptions.LockNotOwnedError`。
- 新频道没有任何菜单，ERP 要建 4 语言 × 7 组合 = 28 套，每套「建菜单 + 查图 + 传图」，合计约 85 次 LINE 请求，每次都新建连接（`line.request` 没传 session）。这一步实际用了 145 秒（18:36:12 → 18:38:37 IST），超过 120 秒的锁。锁先过期，退出时释放报错，整步记失败。
- 菜单本身已经建好（日志里只有释放锁这一处异常）。重试时按内容散列复用菜单，只剩查询，约 40 秒，所以「重试开通」能过（用户重试后这一步确实已完成）。
- Cowork（`pearnly_cowork/line/menu.py`）和 DMS（`pearnly_dms/menu/rich_menu.py`）是同一写法、同样 120 秒，按类一起修。

## 修法

- 三个产品各加 `PUBLISH_LOCK_SECONDS = 900`，锁要盖住最慢的首次发布。进程中途被杀时，残留的锁最多挡后续发布 15 分钟。
- 同一次 `publish()` 用一个 `requests.Session`，经 `line.request(session=...)` 传给每一次请求。`line.request` 本来就支持 `session` 参数，共用层不改。
- 发布结果不变：菜单名、内容、上传顺序都和原来一样，只是不再每次请求重新握手。

## 测试

- `tests/test_line_menu_publish_lock.py`（新，候选镜像离线跑，已加进 `scripts/ci-candidate-checks.sh` offline 车道）：模拟全新频道，三个产品都检查锁时长等于 `PUBLISH_LOCK_SECONDS` 且 ≥ 600、整次发布只用一个连接、每套菜单都传了图。用改动前的代码跑：3 个失败 + 3 个错误；改动后通过。
- 本地：development.yml 全部闸门、`check-architecture`、`check-dms-offline`；镜像 fb1037ad 里跑 `test_line_channel_setup`、`test_line_search_and_loading`、`test_dms_entrypoints`、`pearnly_erp_portal.integrations.test_line_intake`。结果见提交说明。

## 共用件连带回归（`scripts/check-impact.py`）

命中的共用件：无；只改到调用方的入口 12 个。这些入口都要真机 LINE 或实验栈，本窗口没有测，交给这一批的验收窗口：

| 入口 | 方式 | 结果 |
|---|---|---|
| `erp.line.stocktake.liff-camera` | manual | 没测到（需安卓真机 LINE，交本批验收） |
| `cowork.line.stocktake.liff-camera` | manual | 没测到（同上） |
| `erp.line.webhook-sim` | auto：line | 没测到（需实验栈，交本批验收跑 `scripts/smoke/line_flow.py`） |
| `cowork.line.webhook-sim` | auto：line | 没测到（同上） |
| `erp.line.chat` | manual | 没测到（需三星 adb 真机） |
| `cowork.line.chat` | manual | 没测到（同上） |
| `dms.line.chat` | manual | 没测到（同上） |
| `erp.line.binding` | manual | 没测到（同上） |
| `cowork.line.binding` | manual | 没测到（同上） |
| `dms.line.binding` | manual | 没测到（同上） |
| `shared.team.members` | manual | 没测到（同上） |
| `dms.execution` | manual | 没测到（同上） |

额外的真实场景：部署后 gize（ISS-2026-0316）是全新频道的第一次 ERP 菜单发布，它的「配置 LINE 频道」一次通过，就是本修复在生产上的直接证据。

## 同时发现：开户卡在「创建工作台」（不是本分支修的）

- polymer 重试后卡在 `set_workspace`：`LinkValidationError: Could not find Row #8: Link To: ERP Serial No, Row #10: Link To: ERP Serial Movement`。
- 本机 control 跑的是 main，工作台载荷带了 10-26 新加的序列号两张单据（c2be1899）；生产 ERP 镜像 revision 是 08bfb419（10-25），站点上没有这两张 DocType。
- 处理：把 main 部署到生产后，在 ISS-2026-0312 / ISS-2026-0316 点「重试开通」。已告知「新需求理解」窗口。
