# 需求影响评估：CC 只写对话选中的公司与账套（A 线第 4 棒 P0）

> 状态：待主会话审定。基线 main a810621e1。分支 `pearnly/cc-write-scope`（未开）。

## 0. 需求原文与理解

- 原话（用户 11-10，总表二）：「CC 严格只写对话里选中的公司与账套；同一台电脑开着多本账套（含真账套）是正常用法；CC 或模型录进别的账套就是产品缺陷，按最高级别修，不靠改测试配置回避。」
- 证据：线上压测 S7（11-10，testcowork 选「บริษัท Pearnly จำกัด」，放 SINCERE 作废票，日文问录到哪本），CC 说要录进卖方的真账套 69SINCER。代码结论见共享笔记「推送目标账套由什么决定-代码结论-11-10.md」。
- 我的理解：凡是 CC（模型）在对话里发起的**账本写入**，服务端在写之前核「这份账套属于本对话挂的那家公司」，不属于就拒，原因照实交回模型（模型据此用话说明）。不靠提示词。
- 不在范围：网页待处理 / 复核页由人亲手选账套推送（人点的，不是 CC）；LINE 收票、文件夹定时任务**运行时**（不经对话）；只读查询（查别家单据、报表照旧可以）。

## 1. 现有同类与复用

- Cowork：`assistant/company_tools.default_scope`（本对话公司 → `service.work_sets`，含分店范围）已是「本对话这家有哪些账套」的唯一判据，读工具（列单据、文件夹任务）都用它；写工具没用。`tools._selectable` 只核「在连接的可选清单里」。
- ERP：`express/cc_tools._other_company` 已拦「单据不是本对话公司的」，但模型**点名别家**（`company=B`）就放行；ERP 写哪份账套由单据所属公司 + 日期在服务端选（`express/target.pick`），模型改不了。
- 结论：扩展现有。新增一个纯判定 `assistant/write_scope.py`（Cowork），ERP 在 `_other_company` 上收紧，不新建一套。

## 2. Frappe 原生能力

- 不涉及界面；服务端校验，照 Frappe 权限之外的业务校验写在自有 App。

## 3. 按类通用（Cowork CC 里所有「会把东西写进账本」的入口，一次覆盖）

| 入口 | 现状 | 改后 |
|---|---|---|
| `cowork_recognize_attachments` 带推送目标（`target=express/mrerp` + 账套） | 只核在清单里 | 再核属于本对话公司；不属于 → `not_started`，原因 `not_this_company_book` |
| `cowork_push_documents` 普通路（`_push_one`） | 模型传的或单据上记的账套，服务端照推 | 在 `_submit_one` 算出**实际要推的那份**（含 `_choices_view` 解析出的）之后、写停下原因 / 交推送之前核；不属于 → `failed`，原因 `not_this_company_book`，**单据上不留别家去向** |
| `cowork_push_documents` 待选账套路（`_push_listed` → `pending_api.push_to_account_set`） | 候选按单据归属（税号）算，可能是别家的 | 候选只给本对话公司的；模型传别家的键 → 同上拒 |
| `cowork_choose_document_policy` 选现用账套（`_choose_current_book`） | 同上 | 同上 |
| `cowork_pos_import` / `cowork_pos_layout` | 只核在清单里 | 再核属于本公司 |
| `cowork_intake_options` | 列这条连接上全部账套，`suggested` = 本人上次用的（可能是别家） | 公司对话里只列本公司的账套；`suggested` 不在其中就不给（`last_used_unavailable` 照实） |
| 文件夹任务存 / 改（`TASK_KEYS` 带账套） | 不核 | 公司对话里只能指向本公司账套（任务以后自己写账，等于写入） |
| 代录交单（`operator_source.submit` → `_push_one`） | 资料按本公司账套筛过（`list_documents(company=…)`），交单不再核 | 交单时同一判定再核一次（资料与交单之间单据可能被改目标）；不属于 → 该张 `failed`、原因照实，不写 |

- ERP CC：`erp_push_documents`、`erp_resolve_push_result` 在**公司对话里**只写本对话公司的单，去掉「点名别家就放行」；ERP 代录（`express/operator`）资料、预检、交单都已按任务公司筛（`_intake` / `_target` 核 `company`），账套由公司算，**已满足**，只补测试锁住。
- MR.ERP：Cowork 的 MR.ERP 账套在 `books()` 里同样是（连接, `comidyear:seldb`）一对，同一判定直接覆盖；D 线按同一函数接（见第 7 节接口）。ERP 的 MR.ERP 推送按公司走，同 ERP 规则。

## 4. 上下游数据

- 判定只读：本对话公司（`projects.of_session`）→ `service.work_sets(公司, 分店范围)` → {(连接, 账套键小写)}。不改单据结构、不改推送记录。
- 拒的时候**不**往单据上写停下原因与去向（否则待处理页会把别家账套当这张单的去向）。
- Express / MR.ERP：不涉及载荷变化；CC 桌面版不变。
- 分店：对话指定了分店时只算该分店的账套（`work_sets` 已是这个口径；该分店没有自己的账套则算全部）。

## 5. 隔离与权限

- 由服务端拦，提示词只补一句「只写本对话公司的账套；被拒照实说」。
- 跨公司：拒的原因里带「这份账套属于哪家」（只给模型看，用于说明），不泄露别的租户（同一租户内的公司主档本人可见）。

## 6. 入口

- 只涉及 CC 对话（电脑 / 手机同一后端）。LINE、文件夹定时运行、网页人工推送不变。

## 7. 共用件与连带回归

- 改 `pearnly_cowork/assistant/tools.py`（登记的写账共用件）、`operator_source.py`、ERP `express/cc_tools.py`。施工后跑 `scripts/check-impact.py --files …` 并贴结果；动不到 `pearnly_core`。
- 给 D 线的接口（Cowork）：`write_scope.check(connection, account_set_key, *, company=None) -> None | dict`，`company` 不给 = 取本对话公司；回 None 放行，回 `{"reason": "not_this_company_book" | "no_company", "company": …, "allowed": [{connection, key, label}]}` 拒。MR.ERP 直写（`push/mrerp/runner` 经 `_push_one`）自然覆盖，D 只在不经 `_push_one` 的写入点自己调。ERP 那边按公司判，`cc_tools._other_company` 收紧后 MR.ERP 推送同样覆盖。

## 8. 存储与成本

- 每次写前多一次 `work_sets`（同请求内缓存）；无外部调用、无费用。

## 9. 删除、作废、追溯

- 不涉及。拒绝不落库；原因在工具结果里（对话记录可追）。

## 10. 文案

- 不加界面文案。给模型的原因码与一句说明在工具结果（英文，只给模型）。

## 11. 测试（先写，旧代码上红）

1. 票上卖方税号属于别家公司（S7 原样）：公司 A 对话，模型推 B 的账套 → 拒，单据上不留 B 去向。
2. `suggested` 是别家账套：A 对话里 `intake_options` 不推荐、不列 B 的；按它识别 → `not_started`。
3. 待选账套候选是别家：单据按税号归 B、停在待选账套，A 对话里候选只给 A 的，传 B 的键 → 拒。
4. 选现用账套传别家键 → 拒；POS 导出传别家 → 拒；文件夹任务指向别家 → 拒。
5. 代录：资料列出后单据目标被改成别家 → 交单该张拒、其余照交。
6. ERP：A 对话 `erp_push_documents` 带 `company=B` 推 B 的单 → 拒（旧代码放行）；ERP 代录 B 公司单据不进 A 任务（锁现状）。
7. 本公司两本同期（9.Pearnly / TEST2）都放行（同公司不拦，哪一本由第 2 项的复述 + 用户同意来定）。

## 12. 要主会话定

1. **对话没挂公司（「最近」里的对话）时 CC 要写账**：建议一律拒，原因 `no_company`，CC 用话请用户先在这段对话选公司。不拒就等于这条底线有个口子。—— 同意吗？
2. 文件夹任务的存 / 改纳入（建议纳入）。
3. 待选账套的候选改成「本对话公司覆盖单据日期的账套」，票上双方都不是本公司时候选为空、CC 说明这张票不是这家的 —— 同意吗？
4. 本公司有两本同期时，P0 不替用户选哪一本（仍由「待选账套」停下 + 第 2 项一句话复述后用户同意来定）。
