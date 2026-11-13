# Cowork 账套身份与电脑无关（A 线 11-12，主会话审过评估后开工）

- 分支 `pearnly/cc-book-identity`，叠在 `pearnly/cc-conversation-ledger`（5e5c2a08a）之上。
- 评估（主会话 11-12 审过）：共享笔记 `A线-账套身份跨电脑-评估-待审-11-12.md`。主会话定：① 选项编号改成与 ERP 一样的
  `book_key`，旧编号读时映射一次，不长期并存；② 同一用户两台都能写时按在线挑、不加设置，挑中那台领单前掉线怎么办写清并有测试，
  绝不派给不归这位用户的电脑；③ 无税号公司身份跟连接分家本支不改，另立并如实登记缺口。

## 勘察结论（与评估的差别）

- 同一条 Express 连接（池）里两台电脑以不同写法报同一本，池登记本来就按文件号 / 网络路径认成一份（ADR-021 第 2.4 条，
  `targets.identity.resolve`）。**真正分裂只发生在两台电脑各配一条连接时**：公司主档把各连接目录里的行按（连接，账套键）各算一本。
- 池登记上有文件号 `uid`，但目录（池并集快照）刻意不带它（「每台电脑自己的写法不进池目录」）。

## 改了什么

| 提交 | 内容 |
|---|---|
| df3d5a77d | 池目录每行带上登记的文件号，键名 `book_uid`（账套的物理身份，哪台报都一样；与每台自己的写法 `uid` 区分，原断言不变）。公司主档 `identity.book_id`：有 `book_uid` 用 `cc_desktop.contracts.book_key(book_identity(key, uid))`（与 ERP 同口径），没有用（连接，账套键）——不按路径猜合并；`service._found` 按它合并成一条、`places` 收齐各处；本对话账套选项编号 = 这个编号。共用层 `ledger.canonical / same` 经 provider 的 `canonical_ledger` 把 11-12 以前的「连接\|键」映射成新编号（读时一次并存下），写入计划记旧编号不判 `ledger_changed` |
| 6982273f4 | 写入闸认这本各处写法（`_pairs` / `is_pin` 按 `places`）；本对话账套编号新旧都认（`_book_of`）；写入没指定目标时 `place(pin)` → `_serving`：只看这位用户自己登录 CC 的电脑（`pool.owned`），能写且在线的那一处在前；都不在线取第一处能写的；一处都没有他的电脑回第一处（照今天）。识别缺省目标、按账套读 / 记的工具（`_book_defaults`）、待选账套候选都按 `place` 取那处写法 |
| 39cbb0b35 | ERP：`cc_desktop.jobs.writers` 新状态 `not_yours`（本人能写的电脑都关着、同事够得到这本的电脑在线）→ 原因 `express.cc_desktop_not_yours` →「您的电脑离线」（新词条 `express.reason.notYours` 四语，check-copy 过）；作业仍只归本人电脑 |
| bffa2cdd5 | 测试钉住：挑中那台领单前掉线，单子留在它那条连接上等，再推回 `already_pending`，不改派到另一处（另一条连接有自己的写者锁，改派会两处排队、可能写两次） |

## 测试（都先在旧代码上红）

- `test_cowork_companies.BookIdentityTests`：两连接同文件号 → 一条、默认就是它、`places` 两处；没文件号 → 照今天两条、要问一次；旧编号映射。
- `test_cowork_express_account_sets`：池目录带 `book_uid`。
- `test_cc_conversation_ledger`：旧编号读时映射并存下；`ledger.same`。`test_write_plan`：计划记旧编号、此刻同一本新编号 → 照推。
- `test_cc_conversation_ledger_cowork.CrossComputerTests / ServingTests`：两处写法都过闸、别家拒；缺省写进本人在线那处；同事在线不挑；
  掉线不改派。
- `test_cc_desktop_channel`：本人离线、同事在线 → `not_yours`；都离线 → `offline`。

## 还没做（下一步）

1. Cowork 同类说错原因：代录读科目表 `account_masters.view` 不按人看在线（同事在线时一直「读取中」，都离线说电脑离线），对话里「CC 电脑不在线」的实际出处待查清再改。
2. 排队显示在等哪台电脑（沿用「等待电脑」+ 电脑名，不新增词条）。
3. 记住的选择按（连接，账套）存：同一本经另一处写入时读不到，要按身份读（`push/choices.py`）。
4. 受影响流程、候选检查、共用件实测；两台测试机真机（排在四格复测之后）。
5. 已知缺口登记：无税号公司身份 `name:express:{连接}:{名称键}` 仍跟连接分家（本支不改）。

## 接缝约定：账套题改由引导卡承担（A 线 × 引导式第 1 期，11-13 对过）

A 线是这条接缝的负责人；引导式窗口（local_401d0285，分支 `pearnly/cc-guided-phase1`）同意以下各条，第 3 批实现引导卡入口 `pearnly_guide` 时照做：

1. **题目来源**：共用层 `pearnly_core.assistant.ledger` 的「这段对话可选几本」（`options_of` / `offered`，与 `pearnly_conversation_ledger` 同一份候选），引导卡不另算候选。
2. **选项编号**：本对话账套编号——ERP 是 `book_key`；Cowork 自本支起也是与电脑无关的 `book_key`（`identity.book_id`），11-12 以前的「连接|键」由 `ledger.canonical` 读时映射。
3. **答案写入**：只经 `ledger.choose` 存进会话 `project_ledger`，引导卡不另存；`write_precheck/guide.py` 把 account_set / current_book 归「做不做」类（不预选、不标推荐，只认人明选）。
4. **出不出**：本对话账套已定（新建页选了、右栏改了、答过一次）就不出账套题、不再问；未定才出一次。同期两份那种 current_book 题并进这一题，不单独问。
5. **之后**：写入闸（`write_scope`）、写入计划（`write_plan` 记的 `ledger`）只认会话的 `project_ledger`。
6. **跨边测试（第 3 批落地时必须带上）**：引导卡答的账套 = 会话 `project_ledger` = 写入计划 `ledger` = 推送 / 写账作业落点；已定时引导卡不出账套题。引导卡入口还不存在，这条测试由实现 `pearnly_guide` 的窗口在同一提交里加；现有的单边测试是 `test_cc_conversation_ledger`（`ledger.choose` 只认最近一张卡上选的）与 `test_write_plan`（计划记账套、变了判 `ledger_changed`）。
