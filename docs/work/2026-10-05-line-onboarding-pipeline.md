# LINE 开户流水线：只填 Channel ID + 密钥，其余全自动（2026-10-05）

用户："我感觉最近这个 line 总是出问题，修修改改的，所以我想固化下来，做成一个流水线，每次都一样，我只需要去后台加新的 oa 和
A3 / B3 开关：Webhook、关问候语、关自动回复、Use webhook、补发这些。"
分支 `codex/line-onboarding-pipeline`，基线 `origin/main` ead80044，工作树 `../pearnly-line-pipeline`。

## 结论：哪些能自动、哪些只能人做

| 事项 | 以前 | 现在 | 依据 |
| --- | --- | --- | --- |
| 建官方号、开 Messaging API（选 Provider Pearnly） | 手工 | 手工 | LINE 没有建号 / 建频道接口 |
| Webhooks 开、问候语关、Manual chat、Use webhook、Webhook redelivery | 手工 | 手工；开户最后一步读回 Use webhook，没开就提示 | LINE 没有这些开关的接口（Use webhook 只能读） |
| 复制 Channel ID + Channel secret | 手工 | 手工（开户单只剩这两格） | 密钥只能从后台复制 |
| 发长期令牌、抄进开户单 | 手工（误点 Reissue 会让租户断线） | **自动**：站点用 ID + 密钥现取 15 分钟短时令牌，缓存 10 分钟 | 官方：短时令牌不限发放次数 |
| 在共用 LINE Login 频道建 LIFF、抄 LIFF ID | 手工（10-04 两个租户填错） | **自动**：点「发号并开通」时建好 / 校正 | LIFF 服务端接口 |
| Webhook 地址、菜单、账号池 LINE 登录入口、LIFF 核对、Webhook 自检 | 已自动 | 不变 | `set_line` / `verify_site` |
| 拆租户 | LIFF 留在共用频道里 | **自动删**它的 LIFF，腾名额 | 一个频道最多 30 个 LIFF |

免密登录和账号池不是每户手工项：账号池里的 LINE 登录入口（`line-<频道号>`）由 `set_line` 按开户目标上的 LINE Login 频道配置，
回调地址每个账号池只登记一次（2026-09-26 已登记 pearnly-business / pearnly-cowork）；用户与 LINE 的关联由站点每小时对账建立。
之前反复出问题的根源是 LIFF 手建填错；LIFF 自动建好后，这条链上已没有每户手填的值。

## 改动

| 层 | 改动 |
| --- | --- |
| 共用层 `pearnly_core/line.py` | `access_token()`：站点配置有长期令牌就用（10-05 之前的租户、本机样板）；否则用 `channel_id` + `channel_secret` 向 `oauth2/v3/token` 现取，按（频道, 密钥摘要）在站点缓存 10 分钟。`can_send()`：能拿到令牌；`is_configured()`：有密钥且能发。发送路径取令牌失败按原有失败处理（推播记 `push_failed`，后台回覆 / loading 动画降级为不发）。 |
| 产品 | ERP / Cowork / DMS 菜单与 DMS 花名册官方号信息改用 `line.can_send()` / `line.access_token()`，不再直接读站点配置里的令牌。 |
| 执行器 `site-step.py` | `set_line` 不再要求载荷带令牌：先用 ID + 密钥换令牌（换不到报"重新复制两个值"的提示），再写站点配置、设 Webhook；`verify_site` 用 `line.access_token()` 自检。 |
| 控制面 | 新 `onboarding/line_liff.py`：按规定建 / 校正 LIFF（Full；DMS `/liff/dms-booking`，ERP / Cowork 站点根；scope openid，DMS 加 chat_message.write；加好友提示关）。只改"用不了"的已有 LIFF（缺必需权限、地址不在本站、不是 Full、开了加好友提示），能用的手建 LIFF 原样保留。`build_plan` 在建任何记录前先建 LIFF；`_sync_steps` 补建渠道、`queue_cloud_line_config` 重排时也对齐一次；拆租户 `purge_control` 删 LIFF。满 30 个如实报错，不删别人的。 |
| 开户单 / 渠道 | 产品行隐藏令牌与 LIFF 两格，只填 Channel ID + 密钥（两个要么都填要么都空）；旧单带的长期令牌照旧认。渠道的 LIFF ID 只读，令牌格说明"留空"。 |
| 手册 | 「LINE 开通手册」删掉发令牌（原 B4）、建 LIFF（原 C）、手填 Webhook；只剩建号、开关、复制两个值、填单开通、自测。删 8 张不再用的截图。 |

## 验证

| 版本 | 环境 | 场景 | 结果 |
| --- | --- | --- | --- |
| 工作树 | 本机 Python 3.12 | development.yml 全部 26 条 Python 闸门（含新增 `tests/test_line_liff.py` 11 项、`test-site-step-webhook` 新增 3 项） | PASS |
| 工作树 | 生产镜像 `4e80514d` + 本分支五个 App 源码只读覆盖，断网 2G | `ci-candidate-checks.sh offline` 全量（含新增 `tests/test_core_line_token.py` 8 项、改过的 `test_line_channel_setup` 16 项）+ `pearnly_dms.operators.test_line_account` | PASS（55 组 1092 项；DMS 花名册 5 项另跑通过） |
| 工作树 `line_liff.py` | 本机控制面容器，真实 LINE 接口，**只读**（写请求被测试包装器拒绝） | testerp / testcowork / testdms 对齐 LIFF：三站都认出已有 LIFF，0 次写入 | PASS |
| — | 真实 LINE，testdms 的 Messaging 频道 | ID + 密钥现取令牌 200（900 秒）；用它读 bot/info、webhook endpoint、richmenu/list、quota 均 200 | PASS |
| — | 真实 LINE，共用 LINE Login 频道 2010411313 | 现有 LIFF 7 个（上限 30） | 记录 |

首次 offline 跑出 1 项失败：`test_dms_entrypoints` 的假站点只有令牌没有密钥，菜单发布被新加的"必须有密钥"判断挡住。
原判断（发菜单要求完整渠道）→ 发菜单只需要令牌 → 拆出 `can_send()`，菜单与 loading 等发送路径用它，行为与之前一致。

## 未覆盖 / 下一步

- 真正新建 LIFF（`POST /liff/v1/apps`）和不带长期令牌的站点收发消息，要在下一次真实开户时验收（用户说后天开户）；
  失败时开户单会当场报原因，不留半套配置。
- 已有租户继续用长期令牌，本次不迁移；需要统一时把渠道里的令牌清空、重排「配 LINE 渠道」即可。
- 控制面跑在本机（`pearnly-sample-control-*` 挂主目录源码）：合入 main 并在主目录快进后，控制面需要重启 + `migrate` 才生效
  （开户单字段隐藏、LIFF 自动建）；站点侧（共用层、执行器）随下一次生产部署生效。
