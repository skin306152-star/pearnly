# DMS LIFF 编辑器打不开：票据存储自调用（2026-10-03 热修）

分支 `codex/dms-ticket-store`，基线 `origin/main` ed4fb8b0。只上 DMS。

## 现象与证据

- 用户 2026-10-02 19:10 UTC 在手机上打开 testdms 的「建档」编辑器（`/liff/dms-booking`，editor=customer），页面空白、只有一句
  「保存失败，请检查字段后重试。」；19:15 再开一次相同。用户没有提交任何内容。
- 计时日志：页面、词条、配置 200（0.1–1.5 秒）；换编辑票据的 `browser_api.browser_auth` 两次都是 500（1.9 秒）。
- 错误（在 Error Log 待写入队列）：`RecursionError: maximum recursion depth exceeded`，990 层调用栈里 981 层是
  `editor/browser_ticket.py` 第 44 行 `_state_store`。
- 页面拿不到票据就不读表单，直接显示通用失败文案，所以是空白页。

## 原因

上一批 batch-next（10-02，提交 03fdb608「锁、登录 state、票据挪到不淘汰的 Redis」）机械替换时，`browser_ticket._state_store()`
写成了 `return _state_store()`（自己调用自己）；另外 10 个同名函数都正确调用 `pearnly_core.execution.state.store()`。
当时的测试全部把 `_state_store` 替换掉了，没有一条真写真读。

影响：10-02 约 11:43 UTC 上线起，所有 DMS 站点的 LIFF 编辑器（建档、订车编辑、账号）都打不开，约 7.5 小时。访问日志里只有用户在
testdms 的两次调用，正式站 ris 没有客户碰到。与压测负载、本批 batch-1003 无关（本批没有改 DMS 编辑器）。

## 修法

- `browser_ticket._state_store` 改为调用共用层状态存储（与另外 10 处相同）。
- 新实验室测试 `scripts/test-dms-state-store-native.py`（流程实验室：真站点、真 Redis，不替换被测函数）：DMS 里每个 `_state_store`
  真写真读真删一个键（新增的也必须登记，否则失败），并把编辑票据走一遍发出 → 读回 → 作废。加进 `ci-candidate-checks.sh` 的 flow 道。
- 架构检查 `self_calls`：函数体只有一句 `return 自己(...)` 就报错（`tests/test_architecture_guard.py` 补正反两例）。全仓扫过只有这一处。
- 规矩：以后凡是「挪存储」这类改动，必须有一条不打桩、真写真读的测试。

## 验证

- `check-architecture.py` PASS（用旧版文件验证新规则能拦下）；`test_architecture_guard.py` 21 项通过；`check-dms-offline.py` 20 项通过。
- 构建流水线的 DMS 流程实验室跑新测试：见下。
- 构建流水线（bb6133cf，run 37054741714）：离线、DMS 流程实验室（新测试 2 项通过）、DMS 快照实验室都过。

## 同批：菜单 6「คู่มือ」子菜单改成 LINE 快捷按钮（用户 2026-10-03）

- `menu/manual.manual_card()` 由 Flex 卡片改为文字（只一句 `TXT_MANUAL_PICK`）+ `quickReply` 三个 postback（动作码不变），点按后照旧用回复令牌回视频。
- 按钮标签「编号 短标题」用新加的按钮词条 `dms.manual.quick.setup / customer / booking`（LINE 快捷按钮标签上限 20 字符）：
  泰文 ตั้งค่าครั้งแรก / ข้อมูลลูกค้า / ใบจองรถ，英文 First setup / Customer data / Car booking，中文、日文沿用原标题；点按后 `displayText` 为「编号 完整标题」。
  原有四个手册词条不变（`TXT_MANUAL_PICK_SUB` 保留未用）。
- `tests/test_dms_menu_manual.py`：断言文字 + 快捷按钮结构，并读术语表检查四种语言每个按钮标签 ≤ 20 字符；14 项通过，`check-dms-offline.py` 20 项通过。
