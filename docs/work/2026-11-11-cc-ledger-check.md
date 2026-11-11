# 录过的票先核账本：服务端共用入口（A 线第 6 棒，2026-11-11）

> 评估：共享笔记「A线-第4棒-录过的票先核账本-评估-待审-11-10.md」（主会话 11-10 批复）；接口定稿「A线-核账本接口定稿-给D线-11-10.md」（已发 D 线）。
> 分支 `pearnly/cc-ledger-check-2`，基线 origin/main 06bb2e600；CC 桌面版那半（第 5 棒 b58fbcde5）拣过来，与 C 线把 folder_fetch 放宽到 40MB 的改动合并。

## 做了什么

- 共用入口 `pearnly_core.ledger_tickets.check_present(connection, account_set_key, items, *, ledger="express", wait=60)`，签名同定稿：
  - 按产品 hooks `pearnly_ledger_check = {"express": …, "mrerp": …}` 分派；
  - 每张都补一项，产品没回、状态认不出、产品抛错都按 unknown 补上，抛错只记 Error Log；
  - 最多等 60 秒，一次最多 50 张。
- Cowork 通道 `readback/ledger_check.py`、ERP 通道 `express/ledger_check.py`：
  - 各走自己的 CC 桌面版请求通道发 `express_doc_check`；
  - 没有电脑能做时（不在线、够不到这份账套、CC 桌面版低于 1.12.20 不认这种请求）不建请求，整批 unknown；
  - 读好了按单号回 present / gone；等到上限没读好就回空，并删掉请求。
- 两个通道共用的纯函数：`parse_result`（解析 CC 回的 gzip JSON）、`request_params`（拼请求参数，规格同 CC 桌面版）。
- 其他改动：
  - Cowork 按需请求表的 kind 选项加上 `express_doc_check`；
  - ERP 的请求种类在第 5 棒已声明；
  - `cc_desktop.books.find(key)` 按账套键取那份账套。

## 测试

`tests/test_ledger_check.py` 18 条（新模块，旧代码上不存在，所以是红的）。CC 桌面版那半：`companion/tests/test_express_doc_check.py`、`tests/test_companion_contract.py`。

## 还没做（下一步）

- 调用方：「录过的票」判定改成先核账本再问。identity-2 分支暂停，`pearnly_core.ledger_tickets` 包它也要用，那支重做时要合并 `__init__.py`。
- 进 main 后在接口定稿笔记第 5 节补 main SHA，并通知 D 线接 MR.ERP。
- 真机：CC 1.12.20 发布后，在测试机 TEST 账套上核一张存在的、一张作废的（要先在占用表登记并问主会话）。
