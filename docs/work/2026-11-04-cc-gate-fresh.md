# 访客打开 CC 对话页一律落 CC 登录页（2026-11-04）

分支 `pearnly/cc-gate-fresh`，基线 origin/main 590626e98。主会话 11-04 定修法并派本窗口改；先不合入 main，随 Code 版 CC 这批一起合。

## 现象与原因

用户在测试机 testcowork 上没登录、连点几次 `/assistant`：第 1 次落 CC 登录页（fatcc.com），第 2–4 次落 Pearnly 的 Keycloak 登录页。
测试机前端日志：10:59:39 `/assistant` → 302 去 `fatcc.com/?site=…` 并种 `pearnly_cc_entry` 记号（120 秒）；之后每次 `/assistant` → 302 → 本站 `begin_login?redirect_to=/assistant` → `auth.pearnly.com/realms/pearnly-cowork` 登录页。
这是 `login_redirect.assistant_gate` 的记号分支（10-10 提交 03d2bf9ac 引入），生产 c9057f6f 一样，不是这批新带进来的。

记号原本防的循环：入口用 `prompt=none` 静默确认账号池登录状态，过了就交给站点 `begin_login`；站点那边万一仍没登上，访客回到 `/assistant` 又被送去入口、静默又过、又被送回，两边来回跳。记号让 2 分钟内第二次以访客身份回来时改走站点登录口，循环断了，但没登录、只是再点一次的人也被送去了 Pearnly 登录页。

## 修法（只改站点这半边）

`assistant_gate`：记号还在时仍送 CC 入口，地址加 `fresh=1`（`entry_url/?site=<本站>&fresh=1`），不再走本站 `begin_login`。
入口 `www/ai.py` 收到 `fresh=1` 不做静默确认、只显示登录页（生产已有，退出登录 `identity.py` 也是送 `/?fresh=1`），这一跳不会自动回到站点，所以不会来回跳：最多每提交一次登录走一圈。生产控制面 fatcc.com 不动。

## 测试

- `tests/test_assistant.py::test_assistant_guests_go_to_the_cc_entry_once`：记号分支断言改成入口登录页地址、不含 `begin_login`；新增访客连开三次都落入口（第二次起带 `fresh=1`）。
- 生产 fatcc.com 只读 GET：`/?site=testcowork.pearnly.com&fresh=1` → 200，页面有登录表单，不跳转。

## 共用件连带回归（`check-impact.py`：命中 `login-session`，5 个入口）

| 入口 | 结果 |
|---|---|
| `shared.login` | 访客打开对话页：通过（单测 + 测试机集成环境从外部连开三次 `/assistant`，结果见下）。站点 OIDC 登录、退出、改密码：没测到（这次只改访客打开 `/assistant` 的分支，要输密码的步骤不由本窗口做；测试机上 CC 桌面版 10:46 走同一条 `begin_login` → `callback` 登录成功，那是改动前的代码） |
| `control.issue-and-entry` | 没测到（控制面代码未改；入口对 `fresh=1` 的处理只读核过：200、显示登录表单） |
| `shared.shell-gate` | 没测到（开通闸代码未改，本次改动不经过它） |
| `shared.team.members` | 没测到（团队页建成员未改，本次改动不经过它） |
| `shared.line.login` | 没测到（LINE 登录未改，本次改动不经过它） |

产品地图：命中 `cowork/erp.cc.action.switch-account`、`cowork/erp.cc.action.logout`，都是电脑绿、手机绿（流程测试已有）；没在回归实验室重跑。
测试机集成环境外部读回结果、本地全闸门、生产镜像候选离线检查、分支 CI 结果补在下面。

## 验证记录

- 本地全闸门：按 `.github/workflows/development.yml` 的 184 步全跑，0 失败（提交 052505f0f）。
- 生产镜像候选离线检查：`scripts/ci-candidate-checks.sh offline`，镜像 `frappe@sha256:29ae8ca65e5cb183694da3ee53631be690507bbd942f5158c463b728cda07bf0`（生产 c9057f6f），候选代码只读挂进五个自有 App 目录，本机 colima 跑，LANE EXIT 0。
- 测试机集成环境（testcowork 切到测试机，集成提交 dc5a562f4 = 590626e98 + scan-expected-no-report + cc-unified 5c28d1900 + 本分支，`testenv.sh live cowork`），2026-11-04 从外部用同一个 cookie 罐连开三次 `https://testcowork.pearnly.com/assistant`：
  第 1 次 302 → `https://fatcc.com/?site=testcowork.pearnly.com`；第 2、3 次 302 → `https://fatcc.com/?site=testcowork.pearnly.com&fresh=1`。
- 用户在测试机环境上亲手试：待主会话通知。
