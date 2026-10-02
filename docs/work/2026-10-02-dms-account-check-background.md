# DMS 连接页试账号改后台 + 推送（2026-10-02，本批）

分支 `codex/dms-connection-test-background`，基线 main c5092f48。来源：Cloudflare 评估查出（`2026-10-02-cloudflare-proxy-evaluation.md`），
压测窗口转用户口径排进本批：连接页「真试一次再存」在网页请求里同步做——Playwright 登录，账号锁最长等 180 秒，再加核对权限——
会撞 gunicorn 120 秒上限。

## 改动

- `connections/background.py`：`set_read_account` / `save_write_accounts` 只做权限与必填检查、排任务就返回 `{"queued": 号}`；任务在
  `long` 队列以发起人身份、发起时的语言跑原来那段逻辑（改名 `_set_read_account` / `_save_write_accounts`，内容不变：占用检查、按用途真试、
  存连接与系统账号、开始读缺的资料）。结果 / 失败原因写进不淘汰的状态存储（`pearnly_core.execution.state`，15 分钟过期），并
  `publish_realtime("pearnly_dms_account_check")` 只推给发起人；`account_check(check_key)` 查状态，任务已不在却没写结果按失败回。
- **密码不进队列**：任务参数会留在 Redis 的任务记录里，所以账号密码用站点密钥加密后单独存 15 分钟，任务读到就删，查状态不回它。
- 前端 `connect.js` `checkInBackground`：发起后先问一次，之后推送优先、轮询兜底（在线 20 秒 / 不在线 5 秒，同 Cowork 录入），读完照原来的
  回复处理（缺权限列出缺哪几项、失败话术按语言取）；后台失败弹服务端原因、对话框不关。
- 旧的 `test_credentials` / `test_endpoint`（页面已不调用、保留给旧客户端）不变，登记为开 Cloudflare 前的前置项。

## 验证

- `tests/test_dms_account_check_background.py`（候选离线道，生产镜像）：排进 long 队列、任务参数里没有密码、加密件读后即删、结果与推送只给发起人、
  人话原样转、意外错误记日志只给通用提示、任务没了按失败、状态不回密码——4 项通过。
- `tests/test-dms-connect-page.cjs` 6 项通过（新增：后台失败弹原因、查状态只带号不带密码）；`check-dms-offline.py`、架构检查通过。
- 还缺：testdms 上点一次「修改读档账号」——会真实登录外部 DMS 一次（只读，不写），在统一点页时做。
