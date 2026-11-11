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
