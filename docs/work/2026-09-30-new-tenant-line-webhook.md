# 2026-09-30 新租户 LINE 收不到消息、Express 连接没有权限

状态：开户修复已提交到任务分支；线上两个租户已手工补齐（见下），剩"Use webhook"开关要用户在 LINE 后台打开。

- 分支 `codex/line-webhook-auto`，基线 `c97eac21`（= 当时 `origin/main`），工作树 `.claude/worktrees/line-webhook-auto`。
- 起因（用户）：erpcowork 在 LINE 发绑定码毫无反应；随后录入工作台点 Express「连接」报"没有权限"。

## 证据（生产只读核对，2026-09-30 11:14–11:40 UTC）

| 租户 | 事实 |
| --- | --- |
| erpcowork（Cowork） | 渠道凭据齐全、官方号 `@107shbpp`；LINE 回读 Webhook 地址 **404 未设置**；站点 LINE 事件 0 条、错误日志 0 条；绑定码 179330 未用 |
| erpcowork | 前端日志 5 次 `POST pearnly_cowork.companion.api.issue` → 403；站点配置缺 `pearnly_cowork_erp_push_enabled`（`credentials._enabled` 第一道拒绝）。testcowork 有，是 09-27 手工打开的 |
| erpcowork / mrerp | 站点配置都没有菜单表（`pearnly_cowork_line_menus` / `pearnly_erp_line_menus`）；mrerp 官方号 richmenu 列表 0 条、无默认菜单。testerp 有，是手工发布的 |
| mrerp（ERP 正式） | Webhook 地址是手工填的，但 `active: false`（"Use webhook"没开），同样收不到消息 |

## 根因

开户流程只把渠道凭据写进站点：
1. 从不去 LINE 设置 Webhook 地址（`_line_payload` 早就算好了 `webhook_url`，云端 `set_line` 没用它）；
2. ERP / Cowork 的按权限富菜单要手工 `publish()`（DMS 在 `set_line` 里自动发布）；
3. Cowork 的 ERP 推送总闸从没写过（只有测试环境脚本 `cowork-lab.py` 会写）。

之前遇到的都是在出问题的那个站点上手工补，没改开户流程，所以每开一个新租户都会再遇到。

## 修复（`infrastructure/production/site-step.py`）

- `set_line`：写完凭据后把 Webhook 地址设为本站 `https://<站点>/api/method/pearnly_core.line.webhook` 并回读（此时站点还没发布，不发测试推送）；
  ERP / Cowork 同时发布自己的富菜单（幂等，按内容散列复用）。
- `finalize_site`：Cowork 打开 `pearnly_cowork_erp_push_enabled=1`。
- `verify_site`（开通最后一步）：Cowork 必须已开总闸；ERP / Cowork 配了 LINE 的必须已发布菜单；配了 LINE 的 Webhook 必须是本站地址、
  "Use webhook"已打开、LINE 测试推送回 200。"Use webhook"开关 LINE 没有开放接口，只能手动打开，所以没开时这一步失败并写明原因
  （提示写在服务器受限日志里，控制面只显示通用失败）。
- 逻辑放在 `site-step.py` 里、不进共用层：维护容器用的是各产品当前镜像，而 `site-step.py` 来自部署目录，
  新增共用层函数会让还没换镜像的产品组在开户时导入失败。

## 线上手工补齐（用户授权："直接帮我设好 Webhook"，以及本次排查）

- erpcowork：LINE Webhook 地址已设（测试推送 200）；`set-config pearnly_cowork_erp_push_enabled 1`；Cowork 菜单发布 28 套。
- mrerp：ERP 菜单发布 28 套（不改默认菜单，当时没有已绑定用户）。
- 回读：新增的 verify 检查在两个租户上都通过；erpcowork 的 Webhook 检查按预期停在"Use webhook 未打开"。
- **待用户**：LINE Developers → 渠道 `2011793651`（erpcowork）与 mrerp 渠道 → Messaging API → 打开 Use webhook（并开 Webhook redelivery）。

## 验证

| 版本 | 环境 | 场景 | 结果 |
| --- | --- | --- | --- |
| 工作树 | 本机 | `scripts/production/test-site-step-webhook.py`（6 项，新增并接入 development.yml） | PASS |
| 工作树 | 本机（python 3.14，默认 3.9 过老） | development.yml 23 条 python / node 闸门，含 `check-architecture.py` 0 违规 | PASS |
| 工作树 `set_webhook` / `check_webhook` | 生产 Cowork web 容器，erpcowork 真实渠道 | 重设同一地址 + 回读 | 设置 OK；检查停在 Use webhook 未开（符合预期） |
| 线上代码的 `publish()` / `_published()` | 生产 erpcowork、mrerp | 发布与回读 | PASS |

未覆盖：完整跑一遍新租户开通（需要真实新单）；`site-step.py` 需随下一次产品激活同步到服务器部署目录后才生效。
