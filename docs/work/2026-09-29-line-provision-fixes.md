# 2026-09-29 开户 LINE 凭据与 ERP 手动录入入口

状态：本次范围完成（代码 + 本地/真实控制面库验证）；待用户真机验收；未部署。

- 分支 `codex/line-provision-fixes`，基线 `26e852e2`（= 当时 `origin/main`），工作树 `.claude/worktrees/peaceful-gauss-545f01`。
- 起因：用户在本机管理后台（control.localhost:8104）给 MRERP 开户时遇到三件事；租户已手工修好
  （`LINE-DEP-2026-0297` 已 active，mrerp.pearnly.com 已开 LINE 登录），本次只做通用修复，不改已有数据。

## 1. 开户单上的访问令牌没存下、开通时 LINE 被静默跳过

### 证据（本机控制面库，只读核对）

| 事实 | 说明 |
| --- | --- |
| `ISS-2026-0294` 行 `til4jo34j3`：密钥列为 32 个 `*`，令牌列为 NULL；`__Auth` 只有 `line_channel_secret` | 20:01:49 首次保存（新单一次「发号并开通」）时，保存请求里就没有令牌 |
| 该单 20:01:50 的发号版本记录没有子表变化 | 之后没有任何保存改过这一行 |
| `PR-2026-0296` 创建于 20:01:50.34，但它的**第一条**版本记录在 20:02:45，内容是一次性"added" 全部 14 个步骤 | 第一次 `api_plan` 建了请求和部署，之后抛错；步骤是 55 秒后第二次 `api_plan` 经 `_sync_steps` 补上的 |

### 根因

1. **前端**：令牌没进 20:01:49 的保存请求。Desk 子表格子只在失焦（`change`）时把值写进 `frm.doc`。
   在浏览器里用样板账号复现（新草稿、拦住保存、不落库）：Tab、点按钮、Esc 三种常见操作都能落进去，
   未能复现当时的具体手势；已知会丢值的是"格子里有值但 change 没触发"的情况。
2. **后端（静默跳过的真正原因）**：`build_plan` 先 `request.insert()`，建部署时 `_ensure_oidc_client`
   **提交了事务**，然后 `_ensure_line_channel` 才因"只有密钥"抛错 —— 留下一张无步骤的请求。
   再点一次时走"已有请求"分支 `_sync_steps`，它不建渠道，于是 `set_line` 载荷 `configured: False`、记 skipped。
   这正是事后单独 dry-run 会报错、实际开通却没报错的原因。
3. 开户单保存时没有任何"只填一半"的检查，问题一路带到开通之后才暴露。

### 修复

- `pearnly_issue.py`：保存时 LINE 渠道 ID / 密钥 / 令牌去首尾空白；**未发号**的单子只填了一半直接拒绝
  （沿用已有词条"第 {0} 行：LINE 渠道要同时填 channel secret 与 access token"）。已发号历史单只去空白，不挡别的修改。
- `pearnly_issue.js`：`save_first` 先让正在输入的格子失焦、等 100ms 再存（同 Frappe 主按钮 `trigger_primary_action` 的做法）。
- `provisioning.py`：`build_plan` 在建任何记录**之前**先核对全部行的 LINE 凭据；`_sync_steps` 遇到部署还没有渠道、
  开户单上带了凭据时补建渠道，已跑过（done/skipped）的 `set_line` 经 `queue_cloud_line_config` 重排。已有渠道不动。
- 共用纯函数 `onboarding/line_ids.py`：`clean_credential`（保留已存的 `*` 占位）、`half_filled`。

## 2. 手建 LINE 渠道默认 pending，保存后下发的是 configured=False

选择：`pending` 只表示"凭据还没齐"；**凭据齐了保存即自动转 active**，要停用用 `suspended`。不加新文案。
`pearnly_line_channel.py` 的 `validate` 同时去空白。开户单建渠道的路径（显式 `active` + `flags.pearnly_from_issue`）不受影响，
测试覆盖"仍 active、保存不排队"。

## 3. ERP LINE「手动录入」不走 LINE 免密登录

`pearnly_erp_portal/integrations/line_flow.py` 快捷回复「手动录入」改用 `pearnly_core.account.line_login.entry_url`，
与卡片「编辑」、菜单「盘点」同一入口；`test_line_intake.py` 覆盖采购与销售两条链接。

## 可选项：凭据首尾空白

新保存的开户单行与渠道都会去空白。`LINE-DEP-2026-0297`、`LINE-DEP-2026-0285` 存量密钥前的空格**没动**：
站点 `pearnly_core.line._conf` 会 `.strip()`，不影响使用；重存渠道会触发云端 `set_line` 下发，需用户另行决定。

## 验证

| 版本 | 环境 | 场景 | 结果 |
| --- | --- | --- | --- |
| 工作树 | 锁定镜像 `pearnly/frappe-sample:16.34.1`，`--network none` | `tests/test_line_channel_setup.py`（10 项）；旧代码上 9 项失败 | PASS |
| 工作树 | 同上 | `tests/test_cloud_onboarding.py`（17 项）、`pearnly_erp_portal.integrations.test_line_intake`（79 项） | PASS |
| 工作树 | 本机 node | `scripts/production/test-issue-actions.cjs`（11 个场景，新增"输入中的格子先落再存"；旧 JS 上失败） | PASS |
| 工作树 | 本机 | `development.yml` 里 22 条 python/node 闸门（含 `check-architecture.py` 0 违规、`test_liff_ids.py`） | PASS |
| 工作树 | 一次性容器挂工作树源码 + 本机控制面库，**禁止提交、结束回滚** | 草稿只填密钥被拒（中文报错）；两项都填带空白 → `__Auth` 存 32 / 172 位；手建渠道 pending→active、缺项仍 pending、suspended 不动；已发号 `ISS-2026-0294` 仍可保存；事后回读无残留 | PASS |
| 本机控制面 Desk | 浏览器（样板账号，拦住保存，不落库） | 子表内 Tab / 点「发号并开通」/ 行编辑 Esc 均把令牌写进 `frm.doc` | 未复现丢值 |

未覆盖：真实 LINE 里点「手动录入」的免密登录（需真机）；控制面 Desk 上用新 JS 的实际点击（控制面挂的是主目录源码，
合并后才生效）；生产部署。

## CI 登记

`scripts/ci-candidate-checks.sh offline` 增加 `tests/test_line_channel_setup.py`（需要 Frappe，走候选镜像）。
