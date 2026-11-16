# ERP 在 CC 对话里直写采购 / 销售的引导整链（11-16）

分支 `pearnly/cc-guide-documents`（工作树 `.claude/worktrees/focused-lalande-7a051c`）。基线：`origin/pearnly/cc-guided-collection`（e590f7fb4）
合并 `cc-guided-phase1`（6e7de94ed）、`erp-operator-intake`（bedff7642）、`cc-review-card`（8aec2404f）；这几条进 main 后变基到 main。
开工方案、接口约定在主会话共享笔记：「ERP直写采购销售引导-开工方案-11-16.md」「引导式-documents源-约定-11-16.md」。

## 目标

点「采购 / 销售」或说一句 → CC 一句「请上传…票据」→ 上传、识别 → 题卡一次问完要人定的事 → 复核卡（就地改）→ 「确定」才写 → 结果卡。
同一条路在「确定」之后分叉：直写 = 提交 ERP 单、由它排直写；代录 = 交给 CC 桌面版。两产品、直写 / 代录、点菜单 / 打字同一套（主会话 11-16）。

## 已做（提交）

| 提交 | 内容 |
|---|---|
| 901097822 | ERP 产品函数 `express/guide_documents.py`（交识别按业务定方向、识别状态、复核票面读改、直写确定、结果）；排直写带上对话号（`conversation.for_session`，后台 `push.send(session=…)`），计划门认 `I:<收单记录>` |
| 6d664a92b | 付款「点了才算」（直写、代录同改，主会话 11-16 批）；**顺带修的缺陷**见下 |
| dac2df497 | 共用层 documents 出题源 `assistant/guide/documents.py`；`run.py` 按 source 分派、新 `edit`、`confirm(seen)`；`write_plan.from_page`（凭证存计划 `consent` 列）；第 1 期 `guide_tool` 改调同一份；声明加 `intake / uploads / edit` |
| 1aa4c1c0a | Cowork 产品函数 `assistant/guide_documents.py`，Cowork 声明点名；ERP 产品函数改为一次取回收单记录 |

## 缺陷（单列，6d664a92b 修）

ERP 收单记录（`I:`）在写账判定里：Express 那边判为可写时，判定卡**不带收单记录自己的题**（付款、行物料、仓）。付款只有票面推出的预选、
用户在卡上根本看不到付款题，点不到；开录 / 提交时再按预选默认写（采购按到期日、否则「已付、现金」）。修后：记录自己的题没定就出题，
预选点了才算；对这个对方点过一次记住的算已定；没点的这一张留下，结果行用现成词条「付款还没定」（Payment not chosen yet，四语已有）。
Cowork 第 1 期本来就是这口径（共用写账规则 `_payment_question`：预选来源 rule、引导卡 writable 不收没答的）。

## 改前 / 改后（付款，主会话 11-16 确认）

- 改前：卡上付款没点，开录时照样按预选写——采购按票面（晚于单据日期的到期日记「未付」，否则「已付、现金」），销售没记住过就停下。
- 改后：卡上付款没点，这张这次留下不写（结果行「付款还没定」）；预选照旧排第一、标推荐，点了才算；对这个对方点过一次的记住、下次不问。

## 验证（本地离线）

- 新：`test_cc_guide_documents`（12）、`test_erp_guide_documents`（8）、`test_cowork_guide_documents`（6），已接进 Development checks。
- 受影响老测试照过：`test_cc_guide_tool`（第 1 期 22 条不改断言）、`test_cc_guide_run`、`test_erp_collection_guide`、`test_write_plan`、
  `test_cc_conversation_ledger_erp`（+3）、`test_erp_write_precheck`（+1）、`test_write_precheck_guide`、`test_express_rules_guided` 等。
- 检查：architecture、check-impact（新模块登记 not_entries）、check-copy、unbounded / loop queries、check-cc-guides 通过。
- 未跑：全闸门 `gate.sh`、候选离线（`express/test_operator_intake.py` 在 Frappe 运行时跑，本机不能直接跑）、实验栈流程、真模型、真机。

## 待做

1. 全闸门 + 候选离线（本机负载降到 8 以下再跑）。
2. 接线（引导式窗口分支 3）进 main 后：页面带回 `review.seen` 调 `confirm`、`edit` 白名单接口、复核卡 / 结果卡组件；实验栈流程
   `erp.cc.guide.purchase / .sales`（放票 → 题卡 → 复核改一个值 → 写入 → 结果），付款三条（没点不写且结果行可见、点了写、点过的对方下一张不问）；
   代录那条加在 `erp_cc_operator_intake`。
3. 打字路径 `pearnly_guide` 输入加 `files`（从交文件开运行）。
4. 真模型一段（费用先报主会话）、真机（主会话排）。
5. 最后单独一个提交：ERP 声明 `modes` 加 `chat`（放出直写菜单），先发主会话给用户看。

## 接缝

- 引导式窗口 local_38ec22a7：接线、页面组件；documents 源接口约定由它事后审。
- guided-cowork local_434fc6be：引擎通用能力 N1–N9 也改 `declaration.py`，谁先进 main 谁先、后者变基。
- H3 local_3709c091：`I:` 分派建在其上不重写；付款改动影响代录开录（`operator._submit_intake` 改用 `settle_payment`）。
- CC 桌面版接手窗口：确定后的代录 `hand_in` 不变；收单记录的付款不再默认、复核改的值写回记录（`build_payload` 读，代录自动带上）。
