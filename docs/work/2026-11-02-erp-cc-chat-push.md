# ERP 版 CC 对话直写工具（评估第 9 步，只做 Express）

- 分支 `pearnly/erp-cc-chat-push`，工作树 `.claude/worktrees/erp-cc-chat-push`，基线 `9be471c5`（= origin/main，11-02）。
- 依据：评估 [2026-11-02-mrerp-operator-and-erp-company-评估.md](2026-11-02-mrerp-operator-and-erp-company-评估.md) §0「四格口径」、
  §13 第 9 步、§12.1 定案；[ERP 公司](2026-11-02-erp-cc-companies.md)；ADR-026；Cowork 对 MR.ERP 的对话口径 [cc-mrerp](2026-10-19-cc-mrerp.md)。
- 目标：ERP 版 CC 的对话里像 Cowork 一样直写——按公司列 ERP 单据、看一张单要人选的项、记下选择、交直写、看推送结果、核对结果不明的单。
  对话直写按公司的账本目标自动走对应那条（现在 ERP 只有 Express），用户不用选。

## 1. 提交顺序

| 提交 | 内容 |
|---|---|
| `test:` 黄金样本 | **改动之前**在原代码上生成 Cowork 工具黄金样本并单独提交：`tests/fixtures/assistant/cowork-push-tools-golden.json`（`assistant/tools.py` 全部 63 个工具的声明（公司工具在 `company_tools.py`，本次没动）、`GUIDANCE`、进行中的说法，以及列单 / 推送结果 / 推送 / 要选的项 / 记选择 / 结果不明核对共 16 份调用结果与 JSON 时间换算），`tests/test_cowork_push_tools_golden.py` 逐项逐字节比对 |
| `refactor:` 下沉 | 共用部分搬到 `pearnly_core.assistant.direct_push`，Cowork 只改成引用；黄金样本逐字节一致 |
| `feat:` ERP 工具 | `pearnly_erp_portal.express.cc_tools` 六个工具、hooks 声明、术语表、登记表 |
| `docs:` | 本记录、architecture.md 一节 |

## 2. 下沉了什么（`pearnly_core.assistant.direct_push`，文件头写明多产品共用）

| 共用件 | 原来在 Cowork 的 | 现在两边怎么用 |
|---|---|---|
| 给模型的时间 `local_time` / `local_times` / `dumps`、`LOCAL_TIME` | `_local_time` / `_local_times` / `_json` | Cowork 保留原名作别名；ERP 工具与 `erp_company_profile` 用 `dumps` |
| 上限与等结论 `PUSH_MAX` / `LIST_MAX` / `PUSH_WAIT_SECONDS` / `PUSH_WAIT_STEP` / `may_wait()` | 同名常量、`_may_wait` | 同上 |
| 结果状态码 `ENTERED` `QUEUED` `FAILED` `UNCONFIRMED` `NEEDS_CHOICE` `WAITING_FOR_CC` `ALREADY_SUCCESS` `ALREADY_PENDING` `NOT_FOUND` `SAVED` `NOT_SAVED`、`RESULT_STATES` | 字面量 | ERP 全用常量；Cowork 核对与推送结果筛选用 |
| 结果形状 `not_found` / `not_saved` / `resolved` / `filled`、`NO_PUSH_TO_CONFIRM` | `resolve_push_result` 里的字面量、`_filled` | 两边核对工具的结果同一个函数出 |
| 参数形状 `scope_props(…)`（company / all_companies）、`page_props(…)`（offset / limit）、`documents_prop(…)`、`CHOICE_ITEMS`、`RESOLVE_SCHEMA` | `SCOPE_PROPS` 与各工具里的字面量 | Cowork 以原参数调用生成同一份 |
| 结果不明的说法 `unconfirmed_rule(programs, resolve_tool)`、`resolve_description(programs, then)` | 推送工具说明里的那一项、核对工具的说明 | Cowork 传 `"Express or MR.ERP"` 与原尾句；ERP 传 `"Express"` 与 ERP 的尾句 |
| 说话规矩通用段 `GUIDANCE_TIMES` / `GUIDANCE_THIS_MONTH` / `GUIDANCE_BAHT` / `GUIDANCE_TABLE_ROWS`、`cc_desktop(does)` | `GUIDANCE` 第 2、3、5、9 条与 `CC_DESKTOP` | Cowork 原位引用；ERP 的 `GUIDANCE` 引用同几段 |

留在 Cowork（产品特有）：识别记录、单据详情 / 改单 / 处理口径、文件夹与定时任务、回读、对账、报税、盘点、看板、公司规则与记住的选择、MR.ERP 设置、
`WRITTEN_NOTE` / `PLANNED_NOTE`（Cowork 推送记录的写法事实）、`ENTERED_NOTE`（引用 Cowork 的公司选择与回读工具、复核页）、所有公司那条
（引用 by_account_set、连接与账套键）、按单号找单那条（引用待处理预填）。`scripts/i18n/keep-cjk.tsv` 里随文字搬到共用层的六条登记改到新文件。

**逐字节证据**：黄金样本在下沉之前的代码上生成并单独提交（`17e4a408`），下沉提交后 `python3 tests/test_cowork_push_tools_golden.py` 通过；
`tests/test_cowork_assistant_tools.py`（Cowork 工具原有全部用例）照常通过。

## 3. ERP 工具与 Cowork 对照

ERP 工具（`express/cc_tools.py`，hooks `pearnly_assistant_tools` / `guidance` / `step_names` 声明，ADR-009）：

| ERP 工具 | Cowork 同名 | 做什么 | 走 ERP 哪条路 |
|---|---|---|---|
| `erp_list_documents` | `cowork_list_documents` | 按公司列写 Express 的 ERP 单据（采购、销售、出入库 / 调拨、收银日汇总、盘点），按推送状态 / 种类 / 日期 / 搜索筛，服务端分页 | 只读：各类单据 `get_all`（带上限）+ 计数 |
| `erp_push_choices` | `cowork_push_choices` | 一张单要人选一次的项与可选值（每项最多 30 个、可搜） | 单据页 `api._view` |
| `erp_save_push_choices` | `cowork_save_push_choices` | 记下选择（记在这家公司名下），当场再准备、交直写 | 单据页「选一次」`api.answer` |
| `erp_push_documents` | `cowork_push_documents` | 交直写：转人工的「再写一次」，排队 / 停在会自己好的原因上的按提交时那个函数再准备；等结论（入账带 Express 单号） | `api.retry` / `push.send` |
| `erp_push_results` | `cowork_push_results` | 推送结果（在路上 / 入账 / 没成） | 同列单 |
| `erp_resolve_push_result` | `cowork_resolve_push_result` | 结果不明的单交回原来那台电脑核对：Express 里有就报回单号，没有才写一次 | 单据页对这种情形的 `api.retry`（钉回原电脑） |

单据编号给模型的是 `<类>:<单号>`（`P` 采购、`S` 销售、`M` 出入库、`D` 收银日汇总、`C` 盘点），同代录的 `doc_id`。

**种类**：五种写 Express 的单都列、都看结果；交直写也五种都开——单据页的「Express」弹窗（`p.express`，选一次、再写一次）在采购、销售、
出入库、盘点、收银日汇总五处都有（`purchase-form.js` / `sales-form.js` / `stock-entry.js` / `stocktake.js` / `transactions.js`），用的是同一组
`api` 函数，工具照单据页的 `can_retry` / `needed` 判，不多开。

Cowork 工具逐个对照（Cowork 69 个；ERP 本步新增 6 个，第 8 步已有公司 4 个）：

| Cowork 工具 | ERP | 原因 |
|---|---|---|
| cowork_list_documents | 有：erp_list_documents | 本步 |
| cowork_push_choices | 有：erp_push_choices | 本步 |
| cowork_save_push_choices | 有：erp_save_push_choices | 本步；ERP 选完当场再交（单据页口径），Cowork 选完再推 |
| cowork_push_documents | 有：erp_push_documents | 本步 |
| cowork_push_results | 有：erp_push_results | 本步 |
| cowork_resolve_push_result | 有：erp_resolve_push_result | 本步；ERP 的结果不明由原电脑核对（见 §5） |
| cowork_companies / cowork_company_profile / cowork_company_note_save / cowork_company_note_remove | 有：erp_companies / erp_company_profile / erp_company_note_save / erp_company_note_remove | 第 8 步 |
| cowork_company_choice_save / cowork_company_choice_remove | 不适用 | ERP 没有「公司规则」；要人选的项在单据上选一次、按公司记住（`express.choices`），即本步 erp_save_push_choices |
| cowork_recognize_attachments / cowork_recognition_results | 不适用 | 识别在 ERP 走采购 / 销售页与 LINE 收单，识别完是 ERP 单据而不是识别记录；对话里识别不在本步 |
| cowork_intake_options | 不适用 | 选账套：ERP 单据按税号 + 分店 + 日期自动选账套（`target.pick`），同期两份在单据上选一次，即 erp_push_choices 的 account_set 项 |
| cowork_stock_group | 不适用 | 存货科目组在 ERP 单据上要选时出现在 erp_push_choices（stock_group 项） |
| cowork_mrerp_setup | 不适用（第 6 步） | ERP 现在没有 MR.ERP；第 6 步接 MR.ERP 时 ERP 自己的菜单 / 默认值设置另加 |
| cowork_document_detail / cowork_edit_document / cowork_choose_document_policy / cowork_document_original | 不适用 | 识别记录的明细、改识别字段、转账凭条 / 预扣税凭证口径、原件；ERP 单据在 ERP 页面改 |
| cowork_pos_import / cowork_pos_layout | 不适用 | ERP 有自己的收银台，收银日汇总由 ERP 生成 |
| cowork_companion_status / cowork_refresh_account_sets | 不适用 | Cowork 连接（Express / MR.ERP）状态与重读账套；ERP 用共用机器通道，账套由 CC 桌面版心跳报来，公司页（erp_company_profile）给账套；推送结果里 `cc_desktop_online` 说电脑在不在 |
| cowork_delete_documents / cowork_export_review_workbook | 不适用 | 识别记录的删除与复核工作簿；ERP 单据删除 / 作废在 ERP 页面 |
| cowork_folder_task_options / cowork_folder_files / cowork_folder_fetch / cowork_save_folder_task / cowork_save_folder_tasks / cowork_set_folder_task_enabled / cowork_set_folder_tasks_enabled / cowork_delete_folder_tasks / cowork_folder_tasks / cowork_run_folder_task / cowork_folder_task_runs | 不适用 | 文件夹定时录入是 Cowork 能力，ERP 没有（评估 §0 不在范围） |
| cowork_express_readback / cowork_express_readback_status / cowork_express_readback_delete | 不适用 | 月结回读是 Cowork 能力；ERP 的 Express 主档与库存由主档同步对齐 |
| cowork_recon_start / cowork_recon_status / cowork_recon_list / cowork_recon_result / cowork_recon_answer / cowork_recon_presets / cowork_recon_export / cowork_recon_delete | 不适用 | 对账中心是 Cowork 能力 |
| cowork_vat_draft / cowork_wht_draft / cowork_wht_answer / cowork_tax_draft_delete | 不适用 | 报税草稿是 Cowork 能力 |
| cowork_stocktake_list / cowork_stocktake_template / cowork_stocktake_create / cowork_stocktake_report / cowork_stocktake_count / cowork_stocktake_close / cowork_stocktake_export / cowork_stocktake_delete | 不适用 | Cowork 的盘点工作台；ERP 盘点在 ERP 页面，盘点写 Express 的结果在 erp_list_documents（种类 count） |
| cowork_work_boards / cowork_work_board / cowork_work_card / cowork_work_create_card / cowork_work_update_card / cowork_work_move_card / cowork_work_comment / cowork_work_search | 不适用 | ERP 任务管理有自己的页面（`work` 域）；对话工具不在本步 |

**「允许」卡片**：任务书第 6 条写「写操作走『允许』卡片（同 Cowork 推送的确认方式，看 Cowork 工具里哪些要确认就照做）」。核了 Cowork：
`cowork_push_documents` / `cowork_save_push_choices` / `cowork_resolve_push_result` **都没有** `approval`（Cowork 里要「允许」的是改全公司设置与
定时安排：公司规则、知识库、定时任务、存货科目组、MR.ERP 设置、单据处理口径）。照「Cowork 哪些要确认就照做」，ERP 这三个也不出卡片，
用 `tests/test_erp_cc_chat_push.py` 锁住与 Cowork 同名工具一致。**请主会话确认**：如果要这三个出「允许」卡片，只需在声明里加
`"approval": True` 与 `ask`（词条要新增四语），Cowork 是否同改也一并定。

## 4. 权限（全在服务端，与 ERP 单据页同一判据）

- 读一张单：`express.api._document`（这类单据的查看权、公司在本人可见公司里 `masters.company.allowed_companies`）＋员工只看本人录入的
  （`guard.scope_filters`）。看不到的回 `not_found`（不说有没有这张）。
- 推 / 选 / 核对：另要这类单据的开单权（`api.CAPABILITIES[...][1]`），没有回 `reason_code = not_permitted`；`api.retry` / `api.answer` 自己再核一遍。
- 列单：公司 = 本人可见公司里的（给了 company 只看那家，`all_companies` 看全部，都没给看本对话挂的那家）；每类单据要查看权才列；员工按本人录入收窄
  （出入库 / 盘点 / 收银日汇总在单据页没按本人收窄，这里统一收窄，只严不松）。
- 默认公司按对话挂的公司（`masters.cc_tools.filed()`，共用层 `projects.of_session`）；本对话挂了公司、单据是别家的，写操作回
  `reason_code = other_company`（带那家的编号与名称），模型问过用户后显式给 `company` 才动，同样过上面的权限。
- 公司断开 Express（`push.enabled` 为假）不再写，回原因 `express.disconnected`。

## 5. 行为口径

- **交直写不另开路**：按单据页的判断——要人选的不推（`needs_choice`）；转人工的走「再写一次」`api.retry`；排着还没交进队列、停在会自己好的
  原因上（没有账套、没有电脑、主档在读）的按提交时同一个 `push.send` 再准备（每分钟巡检也是它）；已入账 `already_success`、在路上
  `already_pending`；不写 Express 的单回 `failed / not_for_express`。推送不查重、不拦。
- **代录中的单**（`push.operator_owned`）直写不接，回 `with_operator`（原因 `express.operator_owned`），说明写明由代录去录。
- **结果不明**（写账件转人工、原因 `cc_desktop.result_unknown`）：推送不再推，回 `unconfirmed`；`erp_resolve_push_result` 照单据页对这种情形的
  「再写一次」钉回原来那台电脑——那台电脑按单据号核对 Express，有就报回单号、没有才写一次（`jobs.retry` 的既有口径）。与 Cowork 不同：
  Cowork 由人去 Express 看了告诉 CC，记成已入账 / 未入账；ERP 没有「人报单号记成已写入」这条现成的路，本步不新开（会绕过电脑读回、
  也影响库存对齐），所以人答「已入账 / 未入账」都交原电脑核对。参数与结果形状与 Cowork 相同。**如要让人报的单号直接记成已写入，需另定**。
- **记选择**：`api.answer` 只认这一项能选的值、记在这家公司名下、当场再准备并交直写（单据页口径）；结果里 `push` 是交出去之后的结论。
- **等结论**：同 Cowork，只在推送线程里等（`direct_push.may_wait`），最多 25 秒、每 2 秒读一次；电脑那头没人写（原因是离线 / 没连 / 没有能写的电脑）
  的不等，`cc_desktop_online: false`。
- **按公司账本目标分路**：`cc_tools._route` 一处；现在 ERP 公司只有 Express。

### MR.ERP 在第 6 步怎么接

1. ERP 公司选账本（Express / MR.ERP，评估 §12.1 #6 的通用「账本状态 / 账本单号」字段上线后），`cc_tools` 读单据状态改读通用字段（与
   `express.pending._ledger_rows` 同一处改）。
2. `_route`：公司账本是 MR.ERP 时交 ERP 的 MR.ERP 导入直写（第 6 步在 `pearnly_erp_portal.mrerp` 里做的入口，调 `pearnly_core.mrerp.push`），
   Express 分支不动。
3. `_outcome`：MR.ERP 的结论同一组状态码（入账带 MR.ERP 单号，菜单 / 默认值没选回 `needs_choice` + 设置项）。
4. 说明里 `PROGRAM` 改成 `"Express or MR.ERP"`（同 Cowork），`unconfirmed_rule` / `resolve_description` 照共用层同一份出；ERP 的 MR.ERP 设置工具
   照 Cowork `cowork_mrerp_setup` 另加（共用规则 `pearnly_core.mrerp.needs` 已在共用层）。
5. MR.ERP 的结果不明照 Cowork：由人去 MR.ERP 核对后记下（MR.ERP 没有电脑端的本机记录，不能钉回电脑核对）。

## 6. 新增文案

界面文案只有对话里步骤行的 12 条（6 个工具的做完 / 进行中），全部照抄 Cowork 已有词条与译文（`check-copy --report`：新增 12、改动 0）：

| 英文 | 中文 | ไทย | 日本語 |
|---|---|---|---|
| Listed documents | 查看了单据 | ดูรายการเอกสารแล้ว | 書類を一覧しました |
| Listing documents | 正在查看单据 | กำลังดูรายการเอกสาร | 書類を一覧しています |
| Pushed documents | 推送了单据 | ส่งเอกสารแล้ว | 書類を送信しました |
| Pushing documents | 正在推送单据 | กำลังส่งเอกสาร | 書類を送信しています |
| Checked push choices | 查看了推送前要选的项 | ตรวจสอบสิ่งที่ต้องเลือกก่อนส่งแล้ว | 送信前の選択項目を確認しました |
| Checking push choices | 正在查看推送前要选的项 | กำลังตรวจสอบสิ่งที่ต้องเลือกก่อนส่ง | 送信前の選択項目を確認しています |
| Saved push choices | 保存了推送选项 | บันทึกตัวเลือกการส่งแล้ว | 送信の選択を保存しました |
| Saving push choices | 正在保存推送选项 | กำลังบันทึกตัวเลือกการส่ง | 送信の選択を保存しています |
| Confirmed a push result | 确认了推送结果 | ยืนยันผลการส่งแล้ว | 送信結果を確定しました |
| Confirming a push result | 正在确认推送结果 | กำลังยืนยันผลการส่ง | 送信結果を確定しています |
| Checked push results | 查看了推送结果 | ตรวจสอบผลการส่งแล้ว | 送信結果を確認しました |
| Checking push results | 正在查看推送结果 | กำลังตรวจสอบผลการส่ง | 送信結果を確認しています |

工具说明、结果说法是给模型的英文（不进界面）。另：`erp_company_profile` 交给 CC 的时间改成用户当地时间（原来是 UTC，与共用说话规矩
「工具结果里的时间已是当地时间」相反），说明同步改；页面接口仍是 UTC。

## 7. 测试与闸门（11-02，本机）

| 检查 | 结果 |
|---|---|
| `tests/test_cowork_push_tools_golden.py`（新增，Cowork 逐字节锁；样本在下沉前提交 `17e4a408`） | 通过 |
| `tests/test_erp_cc_chat_push.py`（新增 17 条：声明与四语步骤名、与 Cowork 同词同「允许」口径、共用件同一份、ERP 工具汇总只有 ERP 声明的、只走 ERP 现有写账路；列单服务端分页 / 筛选 / 员工与公司范围；推送各状态走向、等结论、电脑离线不等、权限 / 公司 / 本人、断开；要选的项上限与搜索、记选择走 `api.answer`、不在可选值里不记；结果不明只对 `result_unknown` 交回原电脑） | 通过 |
| `test_cowork_assistant_tools` / `test_cowork_push_conflicts` / `test_cc_desktop_name` / `test_cowork_companies` / `test_cowork_da_verify` / `test_assistant` / `test_erp_cc_companies` / `test_erp_express_direct` / `test_express_operator_tasks` / `test_express_operator_shared` | 全部通过 |
| 全闸门 `~/pearnly-devtools/gate.sh`（照 development.yml，167 步） | 第一遍 166 过、1 不过：`i18n-build.py --check`（ERP 翻译包没重新生成）；重新生成并提交（`fix:` 提交）后该步通过，其余没有改动、不重跑 |
| `check-architecture.py` | PASS（0 violations） |
| `check-unbounded-queries.py` | 通过（没超基线；新代码的查询都带上限：每类 `offset + limit`、计数 `limit 1`） |
| `check-copy.py --report` | 新增 12、改动 0、拦截 0（清单见 §6） |
| `check-impact.py`（含 `--files`） | 登记表完整（新入口 `erp.cc.chat-push` 登记在 `assistant`、`cc-desktop` 两个共用件下）；入口实测见下表 |
| 流程覆盖闸 `scripts/flows/coverage.py` | 通过（改到入口文件 0 个） |
| 候选离线检查（生产镜像 `ea24fcb9…`，`cand-local.sh offline`） | 通过（72 组全部 OK，rc=0） |
| 实验栈流程（`pearnly-chatpush-lab`，挂本分支、生产镜像 `ea24fcb9…`）：`cc.companies` + `cc.chat` | 5/5 通过（cc.chat 新 / 旧 × 电脑 / 手机，cc.companies 旧 × 电脑） |

### 入口实测

实验栈里以用户身份直接调工具函数（假智能体不发工具调用），截图在共享笔记 `erp-cc-chat-push-截图/01…05`：

| 入口 | 结果 |
|---|---|
| `erp.cc.chat-push` | 通过：`erp-new` 站点（公司接 Express、电脑没连）管理员在挂公司的对话里列单（2 张，scope = 这家）、推送结果、看要选的项、交直写 → 停在「等电脑」（`waiting_for_cc`，原因「正在读 Express 账套」）、核对没有结果不明的单 → `not_saved`；单据改成停下要选后 → `needs_choice`、交直写被拦、记选择（`api.answer`）后当场再交 → 回到等电脑；员工只见本人录入的 1 张、推管理员的单 → `not_found`；没有采购权的销售账号 → `not_found`。跑完作废本次建的两张、撤写账件、删会话与记住的选择。别家公司（`other_company`）与代录中、结果不明的分支实验栈数据里没有，离线用例覆盖 |
| `erp.cc.companies` | 通过：流程 `cc.companies`；`erp_company_profile` 时间改当地时间由 `test_erp_cc_companies` 覆盖 |
| `cc.assistant` | 通过：流程 `cc.chat` 4/4 |
| `shared.assistant` | 通过：流程 `cc.chat`（ERP、Cowork 对话页打开、收发） |
| `cowork.assistant` / `cowork.cc-chat.push` | 通过（离线 + 实验栈）：黄金样本逐字节一致；实验栈 Cowork 两个站点调 `list_documents` / 推送结果 / 核对正常（实验数据里没有识别单，真推没跑）；工具汇总 Cowork 照旧 63 + 公司 6 |
| `cc.operator.thread` | 没测到实机：本次只在 ERP hooks 的工具清单里加一项，共用层代录工具没改；离线 `test_express_operator_tasks` 通过 |
| `cowork.express.push`、`cowork.cc.mrerp-setup`、`cowork.ocr`、`cowork.execution`、`cowork.tax-documents` | 没测到实机：Cowork `assistant/tools.py` 只把共用部分改成引用，定义与输出由黄金样本锁住逐字节一致；推送 / 识别 / 报税的代码没动；真推要测试机 Express 与付费识别，不在本步 |
| `erp.line.chat`、`erp.cc.connect`、`erp.execution`、`erp.provisioning`、`erp.masters.scope`、`shared.navigation`、`control.assistant` | 没测到：命中原因是 ERP `hooks.py` 被改（只加了对话工具的三行声明），这些入口用到的 hooks 项没动 |

## 8. 剩余项与要主会话定的

1. **「允许」卡片**（§3 末）：照 Cowork 现状三个写工具不出卡片；如要出，Cowork 是否同改一并定。
2. **结果不明**（§5）：ERP 没有「人报单号记成已写入」的现成路，本步交原电脑核对；如要支持人报单号直接记，需另做（会影响库存对齐口径）。
3. CC 对话里由真模型调这六个工具的实机对话没跑（实验栈假智能体不发工具调用；真模型在测试租户上，按部署批次验）。
4. 测试机 + 真 Express 的「交直写 → 写进 C 盘 T 账套 → 读回」没跑（直写路径本身没改，`erp.cc.write` 冒烟覆盖）。
5. MR.ERP 分支在第 6 步接（§5「MR.ERP 在第 6 步怎么接」）。
