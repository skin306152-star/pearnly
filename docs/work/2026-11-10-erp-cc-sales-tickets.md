# ERP 销售票小批（CC 对话入口：直写 / 代录）

A 线第 3 棒，2026-11-10。评估与审定见主会话共享笔记「A线-ERP销售票小批-短评估-待审-11-10」（主会话 11-10 已审）。

| 件 | 做法 | 提交 | 测试（旧代码红） |
|---|---|---|---|
| ① 代录销售票打通 | 开始前预检按编号规则**预看**下一个销售单号（`numbering.preview`，不占号）填进判定用的表单；预看号不落记录、不进交单入参，建单照常现编，写入与回执以实际编号为准 | 5f8c684eb | `express/test_operator_intake.py::SalesNumber` |
| ③ 写不了的不交 | 交单前再判一次（`_intake_precheck`），判「写不了」的不提交、不交电脑，原因照预检写 | 119af943d | `Submit::test_a_record_the_check_finds_unwritable_is_not_submitted_and_says_why` |
| ② 本公司税号问一次 + 卖方票重读 | 公司没税号、方向是猜的 → 开始卡出「本公司是票上的哪一方」（共用 `write_precheck.own_party`，一批合成一题、每张都有的预选，`own_party.unify` 两产品共用）；点开始先记成公司税号（`masters.company.set_own_tax`：没税号时有开单权即可补、改已有税号要管理公司权限，走公司页同一校验，ERP Company 开着 track_changes 留修改记录）；猜成采购、卖方是本公司（`checks.issued_by`）的：整份文件都是本公司开出的就整份按销售重读（`intake.reread_as_sales`：拆出的其余几条连源文件删掉，第一条回排队改销售，同名工作单、同一计费编号，识别费不再扣），这次不交、原因「本公司开出的票」；同一文件混有别家开来的票不重读，只把本公司开出的那几张留下不交 | 见分支 | `OwnParty`、`CompanyOwnTax`、`integrations/test_cc_intake.py::ReadAgainAsSales` |

依赖：叠在 `pearnly/cc-ticket-identity-2`、`pearnly/erp-cc-intake-siblings`、`pearnly/cc-own-tax-once` 之上（临时合并提交），三者合入后变基到 main。
待做：gate + 候选离线 + 受影响流程；真模型补跑 ERP 场景（公司先不给税号，≤0.5 美元）。

## 主会话 11-10 核两条路（ERP 第 2 件留不留）
- 补跑时发现：ERP 公司没 13 位税号就连不上 Express（`push.connected`、`cc_desktop.books.for_company` 都要税号，`target.pick` 判 `NO_TAX`）。
- 但两条路走得到：①代录对没接 Express 的公司也开放（`operator.allowed` 不看 Express），没税号时旧代码点开始照样建出 ERP 单据——销售票成了采购单（ERP 自己的账错，与 Express 无关）；②接 MR.ERP（`mrerp_push.handles`）不要求税号，D 线开关打开后同样会到。
- 所以留着，并补：预检停在「公司没税号」（`NO_TAX`）且这一题出得来时，照样问（卡片与原回包 needed 都有），答了记下税号再判（`OwnParty::test_a_record_held_for_the_missing_company_tax_id_still_asks_which_side`）。
- 场景：「公司先不给税号」那一遍要在没接 Express（或 MR.ERP）的公司上跑；没接 Express 的公司答完后仍写不了（没有账本可写），断言到「问一次 → 记税号留修改记录 → 整份按销售重读、没建采购单」为止。还没跑，交下一棒。
