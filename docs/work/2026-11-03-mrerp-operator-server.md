# 施工记录：MR.ERP 代录第 3 步不依赖真站点的部分（服务端）

- 分支 `pearnly/mrerp-operator-server`，工作树 `.claude/worktrees/mrerp-operator-server`，基线 `790dc639`（= origin/main，11-03）。
- 依据：[评估](2026-11-02-mrerp-operator-and-erp-company-评估.md) §12.1、§13 第 3 步、§14.3、§14.4；[ADR-026](../adr/0026-operator-targets.md) 第 5–8 条；
  [第 10 步记录](2026-11-02-mrerp-cc-credentials.md) §5、§7、§12；第 2 步记录（分支 `pearnly/cc-mrerp-operator` 的 `docs/work/2026-11-02-cc-mrerp-operator.md`，不合入）「要主会话定的事」第 1–3 条。
- 不连任何真实 MR.ERP；mrerp4sme.com 只读也不碰（用户 11-02 定）。

## 1. 服务端拒收网页交来的 MR.ERP 密码（第 10 步 §12 第 3 条）

- `connections/service.configure`：`adapter == "mrerp"` 时 `credentials` 只要不空（用户名、密码、令牌任一项），建连接、改连接都按
  `connection.config_invalid` 拒、什么都不写；不带凭据照常改。Express 连接的 `credentials` 不变。网页早已不传（第 10 步 `d516082b`），
  所以对现有页面没有可见变化；旧页面缓存若还交密码，改为报配置无效。
- 唯一写 MR.ERP 密码的路 = CC 桌面版授权（`connections.mrerp_login.store`）。
- 实验栈与流程改走测试专用的路：`scripts/cowork-mrerp-login-lab-verify.py` 的 `seed_old_login`、`scripts/flows/cowork_mrerp_login.py` 的准备段，
  建好连接后在站点里直接写 Password 字段（与老版本 `configure` 写的同两格），模拟第 10 步以前存下的老连接。产品里没有对应接口。
- 其余经 `configure` 交 `credentials` 的调用方逐个核过：`scripts/cowork-connections-lab-verify.py` 只对 Express 连接交凭据（不受影响）；
  其余 `tests/` 命中的是 `companion.credentials` 模块替身，不是这个参数。
- 测试：`tests/test_cowork_connection_read_locks.py::test_configure_refuses_an_mrerp_login_from_the_page`（四种交法 × 建 / 改都拒、不落库，
  不带凭据照常升版本）；实验栈脚本新增 `test_the_web_configure_refuses_an_mrerp_password`（管理员令牌、真 WSGI 调白名单接口）。

## 2. 新原因代码（第 2 步记录「要主会话定的事」第 1、3 条）

| 代码 | 级别 | 整批停 | 中文 | English | ไทย |
|---|---|---|---|---|---|
| `account_in_use` | 必须人工 | 是 | 账号已在别处登录 | Account signed in elsewhere | บัญชีเข้าสู่ระบบที่อื่นอยู่ |
| `account_set_unavailable` | 必须人工 | 是 | 账套无法打开 | Account set unavailable | เปิดชุดบัญชีไม่ได้ |

- 三处同值：共用层 `pearnly_core/express_operator/contracts.py`、CC `companion/src/companion/express_operator/contract.py`、前端
  `frontend/assistant/src/components/operator/types.ts`（`tests/test_express_operator_shared.py`、`tests/test_cc_operator.py` 守住，原因数 29 → 31）。
- 新增文案三语（CC 网页没有日文），`check-copy`：新增 2、改动 0、删除 0、拦截 0。
- 第 2 步执行层（分支，不合入）现在被踢回 `login_failed`、账套库不存在回 `company_mismatch`；合入前变基到本提交，改用这两个代码。

## 3. 续录口径（Express 与 MR.ERP 同一套）

子任务在分支 `pearnly/operator-resume-after-stop` 施工，已接到本分支（`b0013a1c`…`3f55483d`）；口径、写账件号核实结论、测试数字见
[续录记录](2026-11-03-operator-resume-after-stop.md)。要点：

- 现状缺陷（已修）：批次一旦停下，站点按原批次号交回来的单（作答、重试、整批继续）在 CC 上都被当「停止跳过」，永远录不进去；
  CC 宿主没有「重试」控制，页面点重试报错；整批「继续」对已停批次无效。
- 口径（Express 与 MR.ERP 同一套，都在共用 `Runner` / `Host`）：换了写账件号 = 人的新指令，已停的批次重新打开，已存的回原单号、
  等人 / 跳过的换新草稿重录、停在半路的先读账套查存没存；同一件写账再派来照上次结果回执。单张重试改走站点 `task_answer`（空答案），
  整批继续走新加的 `task_resume`（只重交整批停原因与停止跳过的行）。
- 本窗口 11-03 的审查：逻辑与旧批次文件兼容性看过，无改动意见。

## 4. 账号租约与 Cowork MR.ERP 代录接线

主会话 11-03 定：**合入 main、默认不启用**。开关不用站点配置（用户 10-07 定功能不做按租户的开关），改成共用层一处全局常量
`MRERP_OPERATOR_ENABLED = False`，两个产品都读；第 2 步执行层核过线上配方、合入的同一个提交里改成 True，不在任何站点单独开。
测试锁两种状态：False 时 `target()` 对 MR.ERP 公司照旧回 express、生产零变化；测试里打补丁设 True 时链路通。契约测试：main 锁服务端
这一侧的黄金样本，第 2 步分支锁 `draft.py` 那一侧。施工在分支 `pearnly/cowork-mrerp-operator-wiring`（子任务），结果回来后补。
ERP 第 6 步直写的入口同一口径：代码常量挡住，不用站点配置。

主会话 11-03 又定（选 A）：只有 MR.ERP、没有 Express 连接的工作空间也要能代录——MR.ERP 连接也能登记 CC 通道（电脑行、心跳、领单都按这条连接走），
放在常量关着的同一批里。三条约束：已有 Express 连接的工作空间，登记与通道行为一个字节不变（测试锁住）；同一台电脑同时登记 Express 与
MR.ERP 两条连接时选一种不会互相顶掉的做法（共用一套通道凭据或各一套），写进 ADR-026；先核对 ERP（ADR-025，CC 用 ERP 账号直连）有没有
同样的问题，有就一并处理。排在接线分支补完测试之后，同一工作树一个写者。

## 5. 检查

本机真实站点层（11-03，代码 `a2b79422` 之上只加了验证脚本）：生产镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:ea24fcb9ce7238414bf166bbe439339a06b9f601cd46b8a64dfdd0f4d7c1803d`
挂本工作树源码；真 MariaDB / Redis / WSGI；不连任何 MR.ERP，直写导入一律换成假的。本任务自己的栈（`pearnly-mrerpop-backend-lab`、
`pearnly-mrerpop-flows-lab`），跑完连卷拆掉；别的窗口的栈没动。全局常量 `MRERP_OPERATOR_ENABLED`、ERP 的 `MRERP_LEDGER_ENABLED` 在产品
代码里仍是 False，「开着」只在验证进程里打补丁。

| 项 | 结果 |
|---|---|
| `scripts/cowork-mrerp-login-lab-verify.py`（cowork-backend 实验栈） | **7 通过**。第一次 6 过 1 不过：新加的 `test_the_web_configure_refuses_an_mrerp_password` 走的是人的 API 令牌调网页接口，Frappe 照常回 Guest 会话 Cookie，而脚本的通用 `post` 按机器入口断言「不发 Cookie」——脚本问题，不是产品缺陷。改为只对机器入口断言 Cookie，并把「拒收」收紧成 417 ValidationError（核过回的是配置无效，不是认证失败）。改走 `seed_old_login` 的老连接用例通过 |
| `scripts/cowork-mrerp-operator-lab-verify.py`（新，同一实验栈） | **8 通过**，连跑 3 遍都过：①开录：`application.submit` 带批次与 `mrerp` 块、选定电脑 → `_prepare_mrerp` → `ledger.reserve` → `ledger.confirm`（`ledger.confirm` 对带 `route_device` 的 MR.ERP 记录没有 Express 才有的判据，走得通），不排服务端执行任务；直写同连接照旧排任务、不带选定电脑；`mrerp_jobs.heads` 真 SQL：`own=True` 只取本连接派给这台的、Express 密钥（`own=False`）取得到同一张、方向反了取不到、别的电脑的排掉 ②MR.ERP 连接自己的密钥经真机器认证：心跳记电脑行（目标含 `mrerp`、`mrerp_users`、`mrerp_logins`）→ 领单拿到 MR.ERP 代录载荷并租上账号 → 手上有件又来领 = 重发同一件（同 `task_key`）→ 回执记 `external_docnum`、这批最后一张放开账号 ③Express 密钥领同一张（投递挂 Express 连接）并回执 ④同一台电脑两把密钥同时领：两轮、线程先后对调，赢家在领取事务里多停 1 秒，输家确实同时走到账本领取、被挡下（MariaDB 判死锁 → 共用的整笔重来，重来时这台已忙 → 不领），只一条投递，输家那把交不了回执 ⑤真 Redis 账号互斥：一批两张，第一张回执后账号不放；这期间直写不导入、照旧排队；Redis 剩余时间缩到 1 秒等同过期后直写恢复导入；电脑回来领第二张时重新租上、回执后放开 ⑥直写短锁占着时代录领不到、单子照旧排着、没有投递，放开后领到 ⑦电脑没报用户名时按站点存的用户名租 ⑧常量关着：MR.ERP 密钥心跳 / 领单 403、电脑行不变；开录前准备拒收、不建记录；开着时排上的单关了之后 Express 密钥领不到、MR.ERP 密钥 403；开着时领走的一张关了之后回执 403、记录还在电脑手上 |
| ERP：`scripts/erp-mrerp-direct-lab-verify.py`（新，回归实验室的 `erp.regression.localhost`） | **2 通过**：关着时管理员经 WSGI 调 `configure` / `choose_ledger` / `read_choices` 一律 403，公司就算标了 MR.ERP 账本提交的单也不进 MR.ERP；打补丁开着时经 WSGI 建连接、选账套当账本，销售单（现销）提交即排上（`ledger_target = mrerp`、排队中），代录占着这个账号（真 Redis）时不导、`mrerp.account_busy` 照旧排队，放开后后台任务导入一张（假 MR.ERP）→ 账本栏 `written` + MR.ERP 单号，再排不再导入 |
| 流程 `run.py --changed --base origin/main`（分叉点 `790dc639`，本机专用回归实验室，生产镜像） | 命中 4 条，**10/10 格通过**：`cc.chat` 4/4、`cc.operator` 2/2、`erp.express.new-item-stock` 2/2、`cowork.operator.unknown` 2/2 |
| 流程 `run.py --flow cowork.mrerp-login`（`--changed` 没选中，单独跑） | **2/2 格通过** |
| 全闸门 `gate.sh`（`SKIP_NPM_CI=1`，`1b6bbfd8`：三份验证脚本都已提交） | **174 步全过**（exit 0） |

没测到的与原因：

- `cc.operator` 流程里「续录改了页面的重试 / 继续」：**没测到**。这条流程在网页上跑，没有 CC 桌面版的桥、不开录，流程里不会出现停下的批次，
  页面上的「重试」「继续」按钮点不到；离线由 `tests/test_cc_operator.py`（全闸门内）与续录记录里的测试锁住。要实测得补一条能在站点上
  造出停下批次的流程，或在测试机上用 CC 桌面版实录。
- 真实 MR.ERP 登录、导入、网页代录：没测到（不连任何真实 MR.ERP，等岳父线上测试账号）。
- CC 桌面版端（托盘、写账线）与 Windows 测试机：没测到（本任务不动测试机）。
- 观察（不是缺陷）：同一台电脑两把密钥同时领单时，输家在 MariaDB 里撞死锁、由共用的整笔重来消化（`transaction.conflicts`，最多 4 次带退避），
  结果正确；两把密钥的领单在同一工作空间里本就会互相等锁。

## 6. 要主会话定的事

1. ~~MR.ERP 代录服务端接线怎么合~~：主会话 11-03 定合入、全局常量关着（见 §4）。
2. 已有文案「Express 登录失败」「Express 登录未设置」（`login_failed` / `login_missing`）在 MR.ERP 代录里也会出现，字面写着 Express。
   要不要改成不带账本名的写法（改已有文案，需用户确认）。本步没改。

## 等岳父线上测试账号

- 被踢下线时页面是否稳定出现「"User" ดังกล่าวมีผู้ Login ซ้ำ…」弹框，`account_in_use` 判定以它为准（第 2 步配方）。
- 账套库不存在 / 期间不可用时 MR.ERP 的实际提示文字，`account_set_unavailable` 判定以它为准。
