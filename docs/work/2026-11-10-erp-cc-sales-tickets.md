# ERP 销售票小批（CC 对话入口：直写 / 代录）

A 线第 3 棒，2026-11-10。评估与审定见主会话共享笔记「A线-ERP销售票小批-短评估-待审-11-10」（主会话 11-10 已审）。

| 件 | 做法 | 提交 | 测试（旧代码红） |
|---|---|---|---|
| ① 代录销售票打通 | 开始前预检按编号规则**预看**下一个销售单号（`numbering.preview`，不占号）填进判定用的表单；预看号不落记录、不进交单入参，建单照常现编，写入与回执以实际编号为准 | 5f8c684eb | `express/test_operator_intake.py::SalesNumber` |
| ③ 写不了的不交 | 交单前再判一次（`_intake_precheck`），判「写不了」的不提交、不交电脑，原因照预检写 | 119af943d | `Submit::test_a_record_the_check_finds_unwritable_is_not_submitted_and_says_why` |
| ② 本公司税号问一次 + 卖方票重读 | 公司没税号、方向是猜的 → 开始卡出「本公司是票上的哪一方」（共用 `write_precheck.own_party`，一批合成一题、每张都有的预选，`own_party.unify` 两产品共用）；点开始先记成公司税号（`masters.company.set_own_tax`：没税号时有开单权即可补、改已有税号要管理公司权限，走公司页同一校验，ERP Company 开着 track_changes 留修改记录）；猜成采购、卖方是本公司（`checks.issued_by`）的：整份文件都是本公司开出的就整份按销售重读（`intake.reread_as_sales`：拆出的其余几条连源文件删掉，第一条回排队改销售，同名工作单、同一计费编号，识别费不再扣），这次不交、原因「本公司开出的票」；同一文件混有别家开来的票不重读，只把本公司开出的那几张留下不交 | 见分支 | `OwnParty`、`CompanyOwnTax`、`integrations/test_cc_intake.py::ReadAgainAsSales` |

依赖：叠在 `pearnly/cc-ticket-identity-2`、`pearnly/erp-cc-intake-siblings`、`pearnly/cc-own-tax-once` 之上（临时合并提交），三者合入后变基到 main。
待做：gate + 候选离线 + 受影响流程；真模型补跑 ERP 场景（公司先不给税号，≤0.5 美元）。
