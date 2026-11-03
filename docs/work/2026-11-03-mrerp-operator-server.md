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

（合入前补。）

## 6. 要主会话定的事

1. ~~MR.ERP 代录服务端接线怎么合~~：主会话 11-03 定合入、全局常量关着（见 §4）。
2. 已有文案「Express 登录失败」「Express 登录未设置」（`login_failed` / `login_missing`）在 MR.ERP 代录里也会出现，字面写着 Express。
   要不要改成不带账本名的写法（改已有文案，需用户确认）。本步没改。

## 等岳父线上测试账号

- 被踢下线时页面是否稳定出现「"User" ดังกล่าวมีผู้ Login ซ้ำ…」弹框，`account_in_use` 判定以它为准（第 2 步配方）。
- 账套库不存在 / 期间不可用时 MR.ERP 的实际提示文字，`account_set_unavailable` 判定以它为准。
