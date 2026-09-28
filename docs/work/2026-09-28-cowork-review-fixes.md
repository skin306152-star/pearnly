# Cowork 识别复核与 Express 推送修复（2026-09-28）

来源：[Express 压测交接](2026-09-28-express-stress-handoff.md)「五、接手核实」与 [过程记录](2026-09-28-express-intake-stress.md)。
用户 2026-09-28：「你这边修复 Cowork，按计划执行，测试机已开，做完测试没问题，全部统一到最后部署」；
MR.ERP 假成功另开窗口（[交接](2026-09-28-mrerp-push-verify-handoff.md)，分支 `codex/mrerp-push-verify`）。

- 分支 `codex/cowork-review-reasons`，工作树 `.claude/worktrees/gifted-lehmann-d677ce`，基线 main `0a4d4427`（= 生产 Cowork / ERP 镜像 `1386e61e` 的源码）。
- 口径：按类通用；拿不准的让人选一次并记住，选项失效停下问人；不改 DMS；生产部署等全部完成、测试环境验证后统一做。

## 计划与进度

| 批 | 内容（交接编号） | 状态 |
|---|---|---|
| 1 | 单号不再按原文替换、两种读法交人选（#1）；税号找回只接受唯一且差一位（#1）；按票汇总「需复核原因」，列表与复核页显示（#2 #4 #12，#3 标红部分） | 本地完成 |
| 2 | 推送失败 / 退回：原因码 → 人话（列表、复核页、手机），按原因修改后重推，已成功过的再点有反馈（#16 #17） | 本地完成（选对方档的入口在第 3 批） |
| 3 | 账套决策表：对方档 / 存货 / 费用科目 / 记账方式 / 付款 / 抵进项 / 部门，选一次记住，推送带码，小助手按码落账（#6 #7 #8 #9 与回读新增项） | 本地完成，待测试环境 |
| 4 | 识别侧：印章号提示词、重复识别、单页失控不拖垮整份、排队过期（#3 #5 #10 #11） | 本地完成，待测试环境 |
| 5 | 推送吞吐：分步计时、按单据类型备份（#13）；结果不明的人工解除入口（接 MR.ERP 窗口）；ERP 网页进程内存（#14，ERP 侧，未做） | 本地完成，待测试环境 |

## 第 1 批

- `ocr/validation/accounting.py`：`repair_invoice_number` → `invoice_number_alternative`，不改票面，记
  `invoice_number_alternative: 模型|原文`；`runner.direct_result` 见到它就转复核。税号原文找回：两位以内唯一、且只差一位才换。
  有意变化（相对 09-27 版本，旧线上 `ec2e5c957e6b` 没有这一步）；旧线上源码对照 `test_cowork_engine_source_parity` 仍 PASS。
- `contracts/line_items.py`：明细合计判定抽成识别与推送共用（`push/express/common.extract_line_items` 改为调用它，推送结果不变）。
- `history/review.py`：需复核原因。按当前票面重算：税号位数 / 校验位（12 位补 0 能过校验位的给建议）、本方税号与公司资料不符、
  金额算不平、明细对不上 / 不完整、进项单号像会计盖的凭证号（年月与票面日期相符）、单号写法与这个对方以前人核过的票不同；
  识别时留下的：单号两种读法、可能漏识别、有页没读出、识别拿不准、疑似重复——人点选 / 点掉后记进票面 `review_resolved`。
  推送成功过的不再提。只提示，不拦保存和提交。
- `Cowork OCR History.review_reasons`（隐藏只读 JSON）随每次写入重算；`v0_0_7_backfill_review_reasons` 回填旧行。
- 复核页：顶部逐条提示；单号两种读法、税号补 0 点一下填入；识别类提醒可点掉。列表：没推送过、有原因的显示橙色「待复核」，悬浮看原因。
- 文案 `cowork.review.*` 四语，在 Cowork 术语表。

验证（本机，Frappe 16.34.1 样板镜像，`--network none`）：新增 `test_cowork_review_reasons` 9；`test_cowork_engine` 42、`test_cowork_accounting` 8；
Cowork 全部非对照测试 99 个文件通过；对照 `test_cowork_engine_source_parity`、`test_cowork_push_source_parity` PASS；架构、前端 i18n、i18n 生成检查通过。
站点内写入路径与页面待测试环境验证。

## 第 2 批

- 原因码 → 人话：`intake.error.<码>` 四语词条（小助手拒绝码 17 个、服务端预检 / 明细闸 / 结果不明等 30 个），
  `p.intake_push_reason` 认 `[码] 原文`、`码:细节`、`EXPRESS_MANUAL: 码`；认不出的原样显示（服务端已翻译的句子）。
  列表状态悬浮、复核页顶部红色一行都用它（复核页手机上也看得到）。
- 小助手回报的「重试也没用」的拒绝（同税号多档、疑似重复对方、字段超宽、载荷无效、账套不符、内容不同 / 上一版还在）
  服务端按码直接转人工，不再先重试 3 次（`push/lifecycle.NEEDS_PERSON_CODES`，有意变化）；小助手 `_outcome_for` 同步登记（随下一版发）。
- 「仍然推送」：退回原因是 Express 已有同号不同内容 / 上一版还在时，复核页可点，提交带 `confirm_duplicate`，
  服务端写进载荷 `duplicate_confirmed`（小助手查重闸认它）；意图里只在点了时才有这个键，老请求键回放不受影响。
- 提交时服务端回的是别处已成功的那条：提示「这张票已经推送成功过」，不再没反应；退回原因仍只记在票上、不弹窗。
- 明细能增删行（补漏读的行、删多读的行），保存时只写改过的格子。
- MR.ERP 税务库查重两句泰文进 `USER_DATA_ERROR_THAI_PATTERNS`（与 MR.ERP 窗口约定由这边加）。

验证：`test_cowork_push_lifecycle` 30（新增转人工 1 条）、旧线上对照 `test_cowork_push_source_parity` PASS；小助手本机 520 通过（36 个需 Windows 跳过）。

## 第 3 批：账套选择（选一次、记住、同类自动套用）

- 规则 `push/express/choices.py`（纯计算）：按「连接 + 账套」记会计的选择——
  对方档（同税号几个档、或同税号只有一个档但分店号对不上 → 问；可选「新建（照票面）」）、
  存货（库存行票面品名对不上 Express 存货名 → 问；可选「新建」）、服务类进项费用科目（科目映射没定时 → 问，按供应商）、
  进项现购 / 赊购（票面没写、没人工裁决 → 问，按供应商；票上会计改过的自动记下）、小票 / 简易税票税额抵不抵进项（按供应商）、
  部门（小助手报了部门表就问，整份账套一个）。选过的值已不在 Express 主档里 → 重新问，不静默换。主档没报上来（旧版小助手）的那一类不问。
- 存取 `push/choices.py` + 新 DocType `Cowork Express Choice`（只经推送服务写，原生接口一律拒绝，记选择人与时间）。
- 推送准备：缺选择 → 不发小助手，推送记录 manual、原因 `choice_needed:<类>`；有选择 → 放进映射器配置：
  费用科目改用选定科目并写直接科目行（不建服务存货）、抵进项按选、付款按选（低于本票人工裁决）、
  对方档码 / 新建确认、存货码、部门码写进载荷。带存货码或部门码的载荷版本 2，小助手报的版本低就在准备时拦下。
- 页面：提交前按服务端判定弹一次原生对话框问齐；退回原因是 `choice_needed` 时复核页可直接点开再选。
- 小助手：上报供应商（APMAS）与部门（ISTAB 50）；存货按码落账、码不在就报错（不按名找 / 新建）；
  部门码取载荷 `depcod`；对方「新建」确认后不再按名找旧档、不按疑似重复拦；
  库存卡 `POSOPR` 进货写 `0`、销货 `9`（69SINCER 真账 RR 2247/2247 为 0、IV 2645/2645 为 9；期初行未核实照旧）。载荷版本支持 1、2。
- 服务端接收：`reported_suppliers` / `reported_departments` 进小助手上报缓存（含按账套刷新的缓存）。

验证（本机）：新增 `test_cowork_express_choices` 9；Cowork 非对照测试 100 个文件通过；对照 `cowork-express-source-parity`、
`test_cowork_push_source_parity`、`test_cowork_companion_reports_source_parity`、`test_cowork_companion_source_parity`、
`test_cowork_supplier_posting_source_parity` PASS（没选择时推送结果不变）；小助手 524 通过（36 个需 Windows 跳过），新增按码落账 / 码不在报错 / 进货 POSOPR / 供应商与部门上报。
站点内数据表、准备、页面对话框待测试环境验证。

## 第 4 批：识别侧

- #3 看图读票提示词加第 14 条：票号只取开票方印的号，不取会计事后盖章 / 手写的凭证号（`IMAGE_SYSTEM`，L2 旧线上提示词不动）；
  另由第 1 批的需复核原因「单号像凭证号」兜底标红。提示词长度测试上限 4500 → 4800。
- #5 查重加第三道「同一对方有效税号 + 同日期 + 同金额」（`contracts/duplicates.MATCH_FIELDS["party"]`，有意新增），
  在当前用户看得见的本公司记录里查（不只本人上传）；原两道与旧线上一致（对照 1120 例 0 差异）。`duplicate_level` 增 `party`。
- #10 多页文件里一页读不出（直读、复核、带原文再读都没结果）不再整份失败：记成 `PAGE_UNREAD`，其余页照常分组入库，
  读不出的页挂到前一张单据上（从第 1 页起读不出的挂第一张）并标红「有一页没读出」；一页的文件、或全部页读不出仍失败。
  不进分组算法（与 ERP `read_all` 同一做法）。
- #11 共用层 `pearnly_core/execution/work.py`：已付过费（有完成的检查点）的任务续跑时不按提交时间判排队超时，
  没付费的照旧按时限过期，付过费的也有绝对上限（时限后 3 小时）。DMS 窗口确认无冲突并建议加绝对上限（已采纳）。
  页面等识别结果的上限 20 → 60 分钟。合入前须在生产镜像跑完整 `ci-candidate-checks.sh offline`。

验证（本机）：`test_cowork_engine` 43（新增一页读不出）、`test_cowork_invoice_results` 17（新增挂页 / 第 1 页 / 全部读不出）、
`test_cowork_duplicates` 7；对照 engine / invoice_group / ocr / invoice_validation / duplicates PASS；Cowork 非对照测试 101 个文件通过。

## 第 5 批

- #13 小助手：每张单写盘日志一行 `[timing] <方向> <单号> <结果> total=… checks= backup= write= reindex= verify= index_check= cleanup=`
  （失败时 `failed_step= rollback=`）；备份按方向只备这张单会写的表（进项不备 ARTRN/ARBAL/ARMAS，销项不备 APTRN/APBAL/APMAS）。
  备份改放本机、批量写：等分步耗时数据出来再定（同一共享盘内复制可能由服务器端完成，改本机未必更快）。
- 结果不明的人工解除（与 MR.ERP 窗口约定由这边做，Express / MR.ERP 共用）：`ledger.resolve_unknown`，只接受「转人工 + 结果不明」；
  已入账 → 成功（可填 ERP 单号），走正常成功收尾；未入账 → 原因 `confirmed_not_entered`，可重推；这条记录占着的写者锁一并放掉。
  复核页「结果不明」那一行有「已在 ERP 核对」。
- #14 ERP 网页进程内存属 ERP 产品，本任务不改。

## 测试环境验证（2026-09-28，Windows 测试机，线上同一镜像 `1386e61e` + 本分支实时源码）

- 起环境：`lab-start cowork`，再 `PEARNLY_TESTENV_REFRESH=1` 从线上重新导入 testcowork（220 张票，含压测 207 张），testcowork 域名切到测试机。
  新 DocType、`review_reasons` 列、`duplicate_level=party` 迁移成功，回填补丁 `v0_0_8` 已执行。
  测试机重启后 WSL 没有保活进程，空闲会整体关掉（容器跟着重启一次）；已用 WMI 起独立的 `wsl … sleep infinity` 保活。
- 需复核原因（按识别原始结果 + 新规则重算，对照 69SINCER 真账，207 张）：错的 101 → 77（35 张不再被原文改错单号）；
  错了没标出 85 → 30（其中 21 张是 Details 汇总表行，真正漏标 9 张：单号读错且开票方没有可比的历史票号）；
  对的被标出 3 → 20（多为真重复照片、可能漏识别）。历史票号来自压测本身，全新供应商第一批没有历史可比。
  过程中改了两处：单号两种读法按开票方（进项供应商 / 销项本公司）历史写法裁决；历史取近 500 张（本公司同时用两套票号）。
- 账套选择：Big C 销项要选对方档（3 个同税号档）；服务类进项要选费用科目（263 个科目）；旧版小助手不报供应商，进项不问对方档（符合设计）。
- 端到端：压测里被小助手退回（同税号两个客户档）的 IV69/06-019、IV69/06-026，在测试环境选定客户档（照 69SINCER 原账 `คาโบชะ`、`ชาญ ฟู้ด`）
  → 提交 → 测试机小助手（1.2.4）写入 SINCTEST 成功；回读 ARTRN 客户码、金额、ISVAT 税号与原账一致，ARMAS 没有新建客户（267 → 267）。
- 页面：测试环境登录走线上 Keycloak 真实账号，未代为登录；「原因码 → 文案」「需复核原因 → 文案」两段逻辑在本机 Node 跑通（各种原因格式都认得）。
  列表橙色「待复核」、复核页提示条 / 单号两种读法按钮 / 选择对话框 / 「已在 ERP 核对」的外观待用户在测试环境看。
- 未验证：新版小助手（按码落账、部门码、POSOPR、分步计时、上报供应商 / 部门）只有从 main 才能发测试通道，合入后再装到测试机验证。

## 临时资源

- `~/.cache/pearnly-legacy-ec2e5c957e6b`：旧线上 `ec2e5c957e6b` 源码（`git archive`，只读对照用），任务结束删除。
- 会话临时目录下 `cvenv/`：跑小助手测试的 Python 3.12 虚拟环境。
