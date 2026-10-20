# CC 推送回复与收入对账修复（教程录制问题清单第 2、4、5 条）

- 状态：本次范围完成，待合入（不合并 main、不发布、不部署）。
- 分支 `pearnly/cc-push-reply-fixes`，基线 `origin/main` 241b9ef8；工作树 `.claude/worktrees/agent-a4f24bb176892724e`。
- 来源：`~/Downloads/pearnly-cc-video/功能分集/问题清单-给修复窗口.md` 第 2、4、5 条；录制环境生产 df2d4cdf、CC 桌面版 1.9.1、演示租户 ccdemo。
- 归属：Cowork `reconciliation`（收入对账配对）、`push`（推送记录、推前试算、按票付款方式）、`assistant`（CC 工具说明与说话规矩）；`companion/` 只改一处过时注释。

## 证据（ccdemo 生产库只读查询，2026-10-20 21:1x）

查询方法：一次 SSH，在 `pearnly-production-cowork-web-1` 里用 `frappe.db` 只读查 `tabPearnly Assistant Tool Run`、`tabCowork Push Record`、`tabCowork Express Choice(+Change)`，模型原文经 `engine.adapter().events(remote)` 读 Anthropic 事件记录；没有 exec 进 mariadb 容器，临时脚本已删（容器内 /tmp 回读 0 个）。

### 第 2 条
- 推送记录：`CWP-102dcc55…` ABB6909-00007 → `external_docnum` HS690912-001；`CWP-8b1ef9cd…` ABB6909-00015 → HS690926-001（`invoice_no` 与 `payload.ref_no` 都是票面号）。对应关系在服务端**本来就存着**（回执带回 `express_docnum`，存 `external_docnum`）；CC 桌面版只是本机日志记了「退回自编号」，回执没有标志。
- 收入对账输入（`cowork_recon_start`，20:03:30）：左 `test-GL-2026-09.xlsx`（CC 回读生成的总账），右 `Sales_Register_Sep2026.xlsx`。结果：`vat_only_positive_items` 有 ABB6909-00007 / -00015，`gl_only_credit_items` 有 HS690912-001 / HS690926-001，同一张票两边各一次。
- 配对键：总账侧取凭证号（GLJNLIT.VOUCHER = DOCNUM 自编号），登记簿侧取票号；`income_vat.reconcile_gl_vat` 只按规范化后的号比，三个补救规则都不覆盖。ISVAT.REFNUM / ARTRN.YOUREF 里是真实票号，但回读总账工作簿没有这一列。
- 同类：测试租户 testcowork 的推送记录里，50 张抽查的销项有 34 张票号没原样进 Express（如 INV2025090361 13 位 → IV680929-001），说明这是普遍情况。进项单号本来就由账套自排（票号在 REFNUM），票号超 15 字节时 CC 桌面版缩写（`fit_ref_no`）；进项核查第 2 轮按税号 + 金额配，不受影响；POS 日汇总本来就自编号、参考号是号段。

### 第 4 条（根因比清单描述更重）
- 用户原话：「ใบที่พิมพ์ว่าโอนเงินแล้วเป็นเงินสด ที่เหลือเป็นเงินเชื่อ」（印了已转账的是现金，其余赊账）——这是**按单据**的事实。
- CC 用 `cowork_save_push_choices` 为 8 张票各记现 / 赊，但账套记忆按供应商只有一个值（归类键 `purchase|税号`）。同一供应商两张票：KD（0105599220213）00318 现金、00402 赊账；NL（0105599220205）0924 现金、0907 赊账。8 次保存并行，后写的盖掉先写的（`Cowork Express Choice Change` 两条：NL credit→cash、KD cash→credit）。
- 结果推送记录：KD2609-00318 → **RR690903-001 赊购**（应现购），NL-0907-2569-04 → **HP690907-001 现购**（应赊购）；两张都 `payment_from: remembered`。
- CC 推后回复（11:35:30 与 11:37:29 两次）都把 KD2609-00318 写成「HP690903-001 เงินสด」，与工具结果 `RR690903-001 credit` 不符；只对 NL-0907 做了事后解释。**录制记录里「HP690903 现购」以 CC 的话为准是错的，测试机 Accserver TEST 账套里实际是 RR690903-001 赊购。**
- 推前预览：推前交给模型的数据里没有现 / 赊（`cowork_push_choices` 只回 needed / options，`choices_view` 算出的 applied 被丢掉），模型只能按说明里「税票 = 赊」或自己的说法讲。

### 第 5 条
- 模型存档原文（事件 `sevt_015kgRo8EonaNMbZk4PKRZej`，会话段 `sesn_016BD3yS3UxCv9AMfeFnePcP`）表格第一行就是 `| HP690901... | | | | |`，是**模型自己写出的残行**，不是前端渲染或流式预览。
- 同一轮三次 `cowork_push_results` 的结果完整：8 张单号与现赊齐全、没有截断或省略号（`e44qi244ev` 那一份解析出 8 组完整的 `(票号, 单号, 现赊)`）。
- 同一张表还把 RR690903-001 写成 HP690903-001 现购：模型沿用自己先前的说法而不是工具结果。HP690901 这个号在 8 张里根本不存在。

## 修法

### 第 2 条
- `reconciliation/income_vat.py`：新增 `pair_known_numbers`，`reconcile_gl_vat(..., aliases=)` 先按已知对应（票面号 → 凭证号）配：税报号在总账里没有、对应凭证在总账里有且没被别的税报行点名才配；一张凭证只配一行（与 `pair_prefixed_numbers` 同一做法，配上后该行按那张凭证计，重算 `revision.income_compute` 直接按凭证号配）。
- `reconciliation/income_recon_service.py`：`known_numbers` 只拿两边各自没配上的号，经钩子 `cowork_recon_known_numbers` 问推送域；同一票号对上多张没配上的凭证时不配。reconciliation 不导入 push（架构检查通过）。
- `push/application/service.py`：`known_numbers(workspace, 票号, 单号)` 只读本工作空间推送成功、`external_docnum` 在给定集合里的记录，回 `(票号, 单号)`；`hooks.py` 登记钩子。
- CC 推送结果：`push/express/pushed.py` 新增 `numbers()`，`service._entered` 对 Express 记录追加：销项（POS 除外）单号 ≠ 票面号时 `printed_number`（超 12 字节再给 `number_reason: too_long`）；进项票号被缩写时 `printed_number` + `reference_written`。`WRITTEN_NOTE` 要求 CC 在回复里说出两个号与原因。没有新增界面文案。
- `companion/src/companion/dbf_sales.py`：只改文件头过时注释（REFNUM 写的是票面号）。

### 第 4 条
- 按票付款方式：`service.save_choices(..., document_payment=True)`（新入口 `api.save_document_choices`，只给服务端调用，不开网页接口），CC 为某张进项票的供应商选的现 / 赊同时写成这张票自己的 `posting_payment_manual`（同复核页改付款方式，付款判定第一级）；供应商记忆照记（以后的票用）。已推出 / 推送中、销项、别家供应商的项、值没变的不动。`cowork_save_push_choices` 改用它，说明写明「现 / 赊按票，其他类别按供应商一个值，同一供应商要不同值就一张一张存、推」。
- 推前预览 = 实际写入：`choices_view` 在没有要选的项时用与准备推送同一套试算（`prepare_express`，同自动推判定 `assess`）得出载荷，经 `pushed.planned()`（内部就是入账后的 `written()`）回 `planned`；现 / 赊由记忆 / 公司规则 / 任务默认定、而票面本身（不带选择的付款判定）指向另一个值时带 `document_payment {payment, from}`。`cowork_push_choices` 把它交给模型（已入账的不给），说明 `PLANNED_NOTE`：推前说现赊 / 科目只照 planned，不按票种推；有 `document_payment` 时推前同一条消息里说明按记住的选择写，不提问、不等确认。网页复核页取同一接口多一个键，界面不变。

### 第 5 条
- 成因是模型输出，前端与工具数据都不改。每轮都带的说话规矩（`GUIDANCE`）加一条：表格 / 清单每行从这一轮工具结果里这张单的那一项抄（单号、现赊、金额、日期），不沿用先前说法；一张单一行写完整，不留半行、不以「...」截断；与先前说的不同要直说。第 4 条修好后推前说法与写入一致，也去掉了这次「先前说法 ↔ 工具结果」冲突的来源。

## 验证

| 层级 | 版本 / 环境 | 内容 | 结果 |
| --- | --- | --- | --- |
| 单元回归（修前失败、修后通过） | 本分支；同一批测试对 origin/main 源码运行 | `tests/test_cowork_reconciliation_income_vat.py`（KnownNumbers、KnownNumbersFromPushes 共 6 条）、`tests/test_cowork_push_fixed.py`（DocumentPayment 3 条、推前试算 1 条、KnownNumbers 1 条）、`tests/test_cowork_assistant_tools.py`（PrintedNumber 3、Planned 2、TableRows 1、保存 / 推前选择 2 条改动） | 新增 / 改动的用例在主线源码上全部报错或失败（表格规矩那条在主线上同样不成立）；本分支全过（30 / 24 / 229） |
| 受影响旧测试 | 本分支 | `test_cowork_account_suggest`、`test_cowork_push_fixed` 的替身补 `_express_context` / `_planned`（choices_view 改取完整上下文） | 通过 |
| 本地闸门 | 本分支，macOS Python 3.12 | `development.yml` 全部 Python 步骤（94 条，含 check-architecture、check-copy、i18n、companion 契约）；对话页前端未改，`npm ci` 后 `test_assistant_ui` 通过；node 步骤与前端构建未跑（未改前端） | 全过；架构 0 违规；文案闸门新增 / 改动 0 |
| 相关离线测试 | 本分支，临时 venv | readback、mapper、choices、DA 档位 / 自动推、companies、folder_intake、companion 契约、push_conflicts、tax_pp30 等 21 份 | 通过（需要真 Frappe 的 push_account / recon_presets / recon_bulk_rows 在候选车道跑，本次未改其覆盖的代码路径） |
| CC 桌面版测试 | 本分支 | `companion` pytest | 1015 passed, 42 skipped |
| 真实 Site（实验栈） | 测试机 Cowork 栈，生产镜像 df2d4cdf（`sha256:1d71f442…`，revision 核对一致）+ 本工作树实时源码，站点 testcowork 导入副本；没切域名 | 钩子解析到 `push.application.service.known_numbers`；真实推送记录 L20-250212-01 ↔ HS680212-001 经钩子配上，对账前两边各 1 → 配后 0；`written` 带 `printed_number`（34 张）；`choices_view` 对真实票给出 planned；进项票 SD-6910-B711 记忆设为赊账时 planned 为 credit 并带 `document_payment {cash, document_type}`；`save_choices(document_payment=True)` 写入 `posting_payment_manual` 后 planned 变 `chosen` | 全部符合；写入在同一事务里回滚，回读记忆与票面均无残留 |

## 未验证 / 限制

- 没有在真实 CC 对话里跑模型（会花模型钱，且测试机 CC 桌面版此刻连着 ccdemo 录制环境）：模型是否在推前照 planned 说明、表格是否不再出残行，属于说话规矩层面，要测试窗口在 testcowork 用一组「同一供应商一张现金一张赊账」的进项单和一次 `ส่งครบหรือยัง` 实测。
- Express 写入未端到端跑：服务端只改了票面付款方式与给模型的事实，写入载荷的组装与 CC 桌面版写入代码没变。
- 收入对账只对经 Pearnly 推送的单据有对应关系；会计在 Express 手录、单号与票号不同的单仍按号配（与以前一样）。配上的行在结果里显示总账凭证号（与已有的前缀补配一致）。
- 回读生成的总账工作簿仍没有参考号列；用户自己从 Express 导出的总账同样靠推送记录配。

## 上线

- 只改服务端：不需要新版 CC 桌面版（`companion/` 只改注释，不发版）；`companion/src/companion/version.py` 未动。
- 无 DocType、补丁、依赖变化；部署按常规统一构建激活即可。
- 生产已发生的数据不会自动改：测试机 Accserver TEST 账套里 KD2609-00318（RR690903-001）、NL-0907-2569-04（HP690907-001）的现赊与用户指示相反，是否在 Express 里改由录制 / 测试窗口决定。

## 留下的东西

- 生产：只读查询，未写；容器与主机临时脚本已删。
- 测试机实验栈：Cowork 栈起栈核对后已 `live-off` 并 `down`（容器删除、卷保留，与开工前一样没有栈在跑）；`/opt/pearnly-testenv/live/cowork` 留有一份本分支源码副本（下次 `live` 覆盖，不再挂载）；testcowork 副本里没有留下写入。锁 `~/pearnly-devtools/locks/testenv.lock` 已释放；没有用测试机 PC 与 C:\PearnlyExpress。
- 本机：`frontend/assistant/node_modules`（被 git 忽略，为跑 `test_assistant_ui` 装的）。
