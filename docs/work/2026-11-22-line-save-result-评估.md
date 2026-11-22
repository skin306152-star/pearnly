# 需求影响评估：LINE 保存后的状态与回执（三张卡一套）

## 0. 需求原文与理解
- 原话（11-22，用户对主会话）：「录入中」后没回执怎么知道状态？状态的目的是什么？LINE 有自带状态。主会话方案用户回「按这个做」。
- 理解：LINE 里 Cowork 单据卡、ERP 单据卡、ERP 收款卡点「保存」后：先显示 LINE 自带的三个点（loading），后台等电脑写的结果；等到了用这次的 reply token 回「成功 · 单号」或「失败 · 原因 +【编辑】」；等不到回「录入中」+ 快捷按钮【查看结果】，点了用新的 reply token 回此刻状态。全程只 reply、不 push。状态词统一成 录入中 / 成功 / 失败 / 已取消（ERP 网页列表、详情、LINE）。
- 不在范围：LINE 别的回话（识别中、出卡前提问）；CC 对话里的状态说法；Cowork 网页（已是标准词）；MR.ERP 开关。

## 1. 现有同类与复用
- ERP 收款卡已有大半：`integrations/line_collection.py` 放行后 `start_loading(30)` + 后台 `await_result`（等 `WAIT_SECONDS=20`）→ 写成回复，没写成回「已记录 · 待写入」+【查看结果】（`_show_result`），失败另 `notify` push 一条。
- Cowork `line/save.py`、ERP `integrations/handlers.py::_save_card`：在 webhook 里同步交单，立刻回「录入中 / Saved / 已入账 · 单号」，不等结果。
- 结论：把收款卡的「后台等 + 回复 + 查看结果」抽成共用件（`pearnly_core` 里一个小模块：等待循环、去重「只告诉一次」、回执格式），三张卡各自只给「读状态」函数；收款卡去掉 `notify` 的 push。

## 2. Frappe 原生能力
- 后台等待用 `frappe.enqueue`（short 队列，同收款卡现状），不另起线程；状态读取用各单据现有字段。不涉及界面组件新增（LINE 消息与快捷回复是 LINE 协议）。

## 3. 按类通用
- 类：LINE 里「保存后要等电脑写」的卡。覆盖 Cowork 单据卡、ERP 单据卡、ERP 收款卡三处；DMS 的 LINE 没有写电脑这一步，不涉及。
- 状态词同一来源：ERP 用单据 `ledger_state`（同网页列表 / 详情）、收款用 `ERP Collection.status`、Cowork 用推送账本 `last_push_status`（同网页列表）。LINE 回执与网页同一个映射函数，不另算。

## 4. 上下游数据
- 不改写账逻辑、载荷、Express 映射；只改「保存之后告诉人」与状态显示词。CC 桌面版不涉及。
- 状态映射（建议，见第 12 栏待定）：
  - ERP 单据：queued → 录入中；written、handled → 成功（带 Express 单号）；held、failed → 失败（原因）；mismatch → 失败（写了但核对不一致，要人看）；detached → 失败（已断开）。没接 Express 的公司提交即「成功 · 单号」。
  - ERP 收款：queued / writing → 录入中；written → 成功 · RE 号；failed → 失败；退回草稿（写前被拒）→ 失败 + 原卡还回（现状保留）；withdrawn → 已取消。
  - Cowork：pending → 录入中；success → 成功；其余 → 失败（现状）。

## 5. 隔离与权限
- 【查看结果】的 postback 只认本人（发单人 = 绑定的账号）的那张，非本人回「无权限」；读状态走现有按单据权限的函数，服务端拦。

## 6. 入口
- LINE 三张卡；网页 ERP 列表 / 详情 / Express 弹窗的状态词。CC 对话、手机网页列表随网页列表同一份词条。拍照入口不涉及。

## 7. 共用件与连带回归
- 新增共用件 `pearnly_core` LINE 结果回执（登记进 `scripts/impact/shared-parts.toml`，三个调用方）；改 `transactions.js` 状态词（共用件「单据 Express 窗口」）。施工时贴 `check-impact.py` 输出。
- 流程 / 冒烟：现有 `erp.line.postback.*`、`cowork.line.postback.*` 离线测试改断言（先回 loading、后回结果；超时回录入中 +【查看结果】）；新增【查看结果】的离线测试。

## 8. 存储与成本
- loading：官方文档未写计费（价格页只列 push / multicast / broadcast / narrowcast 计数，reply 不计数）。三张卡全改 reply 后 **不增加 push 计数**，收款卡去掉失败 push 还少一条。
- 后台等待：每次保存占 short 队列一个工作进程最多 N 秒（见第 10 栏）。按 3 租户 × 10 人估：同时保存 ≤ 几张，short 队列现有进程数够用；施工时核实生产 short 队列进程数，必要时等待改成「每秒查一次、不占锁」。

## 9. 删除、作废、追溯
- 不涉及数据删除；「已取消」沿用卡片取消现状。「只告诉一次」用缓存键（同收款卡 7 天），不落库。

## 10. LINE 平台限制（官方文档核实，2026-11-22）
- loading animation：`loadingSeconds` 5–60、步长 5，默认 20；只能一对一聊天（群组 400）；新消息到达或时间到就消失，用户不在聊天画面时不显示、之后也不补（文档 use-loading-indicator）。
- reply token：官方 reference 的错误表写明「用过的 reply token」会被拒（只能用一次）；**有效时长没在原文里核实到**（只在摘要里见到「一分钟」），施工时真机实测：同一 token 在 20 / 40 / 55 秒回复各一次，按实测定等待上限（先按 20 秒，同收款卡现状）。
- postback 事件自带 replyToken（【查看结果】可用它回复）。
- webhook：官方建议异步处理事件，没写多快要回 200；本方案交单留在 webhook（现状，几百毫秒），等待放后台任务，不卡 webhook。

## 11. 文案
- 新增：【查看结果】（已有词条 `View result` 收款卡在用，沿用，四语齐）。
- 改已有：ERP「排队中 / 待处理 / 不一致 / 已写入 / 已入账 / 已记录 · 待写入」等改为 录入中 / 成功 / 失败；施工前把「原句 → 新句」四语清单贴进本记录，用户 11-22 已同意统一到标准词，按 interface-copy 流程登记 copy-approved.tsv。

## 12. 待定（请主会话 / 用户定）
1. ERP `mismatch`（写进 Express 但回读核对不一致）显示「失败」还是「成功」？建议「失败」（要人看），原因悬浮写「核对不一致」。
2. ERP `handled`（已在 Express 手工处理）显示「成功」？建议是。
3. ERP 详情 Express 弹窗里作废进度（「作废」那一行的 排队中 / 已取消 / 待处理）是否也统一？建议统一（录入中 / 已取消 / 失败）。
4. 等待上限：先 20 秒，真机实测 reply token 时长后再定（不超过 55 秒，也不超过 loading 60 秒）。
