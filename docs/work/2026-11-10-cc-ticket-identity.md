# 复核 5 批之第 1 批：同一张票只写一次（统一身份与跳过）

设计 `docs/work/2026-11-09-cc-misuse-graceful-design.md` 第 1 层（1.1–1.4），影响评估见主会话共享笔记「A线-第1批身份-影响评估-11-09」（已审）。

## 做了什么
- 票面身份：纯契约 `express_rules.contracts.ticket_identity`（方向、双方税号与印的分店、单号去空格斜杠横线点、日期、合计；缺一方税号、单号差一两个字只算近似）。
- 共用判定 `pearnly_core.ledger_tickets`：产品用 hooks `pearnly_ledger_tickets` 声明「同一张票由别的记录写了 / 正在写」；直写与代录交单都在按身份的短锁里先问它，已写的默认跳过（带那张的 Express 单号），用户说「再写一次」才照写；只看我们自己的推送记录，Express 里会计手录的同号照写。
- Cowork：识别记录带身份栏（补丁回填、建索引）；代录「已入账」判定、重复提醒同一口径；账套键大小写写法不同也认得（FLOWOP / flowop）。
- ERP：采购单带身份栏（补丁回填），直写与代录交单同样跳过；销售单本批不判（第 4 批）。
- 流程 `cc.ticket-once`（ERP）、`cowork.ticket-once`（Cowork）：24 张里 4 张已由直写写入，开始卡标出 4 张、交 20，站点跳过同 4 张。
- 真模型场景 `scripts/scenarios/operator_party_once.py` 与通用真跑前置自检 `scripts/scenarios/real_run_preflight.py`（识别配置、识别进程与队列、计费、合成电脑、引擎指向，缺一项不开跑；假智能体 + 假识别先干跑）。

## 验证（11-10，变基到 main 81ec70ae3 之后）
- 本地闸门 215 项过；候选离线见合入前的结果。
- 干跑（假引擎）：两个产品配置齐、页面步骤走通、识别工作单经假识别跑完。干跑判据修了两处（Cowork 假智能体要调识别工具；判据改看识别工作单而不是收单记录数）。
- 真模型（本机实验栈 pearnly-aline3-lab，6QAA 评测密钥，两遍合计约 0.86 美元，台账 `~/pearnly-devtools/test-window/round12/ocr-spend.md`）：
  - **Cowork 电脑宽度：通过**（11 条）：直写 4 张截图识别、客户问一次并记住；代录开始卡标出 4 张「已入账 · RR-DIRECT-0n」默认不勾；交出 20 张全部排队，没有一张停在选客户；页面与回话没有内部词。
  - **ERP 电脑宽度：没过，原因不在本批**：①多票 PDF 拆出的收单记录来源落成 LINE（main 原有缺陷，`pearnly/erp-cc-intake-siblings` 修）；②ERP 代录销售票的开始前预检在建单前按缺单号判「写不了」（另开「ERP 销售票」小批）。
    **ERP 真跑待这两处修好、进了实验栈后重跑**（主会话 11-10 定：本批先合入）。

## 11-11 新口径：录过的票先核账本再问（A 线第 6 棒，分支 `pearnly/cc-ticket-identity-3`）

- 基线 main 56bf65d85：已含写入计划、核账本共用入口、「录吧」。identity-2（810275b9e）的 22 个提交压成一个合上来，解了 6 处冲突：
  - `ledger_tickets/__init__.py` 两边都新建，合并后同票判定和 `check_present` 两份都留；
  - ERP 交单保留 main 的「等电脑」和 `waiting_targets`，加上按身份的写者锁；
  - Cowork 推送保留写入计划的门；
  - 补丁清单、影响登记表取两边并集，工具金样重新生成。
- 口径（用户 11-10，主会话批复）：写过的票不再自动跳过，建写入计划时先核账本（`ledger_tickets.check_entered` → `check_present`）。
  - 删了 / 作废（gone）：当没录过照写，记成再录（交单时不再按「写过」跳过）；
  - 还在（present）或核不了（unknown）：这次不写，进跳过清单，带单号和 `in_ledger`（yes / could_not_check）；CC 在复述里问跳过还是再录，用户说再录，CC 带 `again` 重建计划；
  - 另一条正在写：不写（`being_written`）；
  - 会计在 Express 里手录的同号照旧不拦（只看我们自己的推送记录）。
- 两个产品的 twin 写成时另带 `ledger`、`connection`、`account_set`、`direction`：
  - Cowork 取那条推送记录（`external_docnum`、`account_key`、载荷里的 `direction`）；
  - ERP 取采购单（`ledger_docnum`、`ledger_book`）；
  - 单号用账本里的号，不用票号（D 线 11-11 要求）。
- 直写：Cowork、ERP 推送工具把已开始计划里标了再录的单并进 `enter_again`（`direct_push.with_plan_again`）。
- 测试：`tests/test_write_plan.py` 加 3 条：present / unknown 跳过并带核对结果、gone 照写并记再录、正在写不写；again 不核；直写按计划再录。
- 真模型：等测试机上的 CC 1.12.20（实验栈 CC 桌面版回不了 `express_doc_check`，核出来都是 unknown）。
