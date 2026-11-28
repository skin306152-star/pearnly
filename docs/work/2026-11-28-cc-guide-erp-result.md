# C2：ERP 表单引导补结果步骤（2026-11-28，cc-coverage-5）

分支 `pearnly/cc-guide-erp-result`，基 `pearnly/cc-form-result`（C1）。总表 C 堆第 2 步（主会话 11-25 批）：ERP 五份表单引导接共用
`pearnly_core.assistant.guide.form_result.result`，结果卡只照确定回包画，名字不露内部编号。

| 引导 | 结果卡名字 | 另外 |
|---|---|---|
| 盘点 stocktake | 盘点名称（新建用填的名称；其余取盘点选项同一个标题，没有标题用盘点单号）；调整单用调整单号 | 删除写「已删除 1 条」 |
| 公司资料 company | 备注正文（删除取删之前那条，截 80 字） | 删除写条数 |
| 添加公司 company_add | 加的几家公司名连写（中 / 日「、」，泰 / 英逗号） | 主档同步还在排队 / 读取 / 落库写「同步中」（公司页同一个词，`cc_service.syncing`） |
| 收发存 stock_card | — | 结果卡带文件 |
| 待处理 pending | 交出那张的「单号 · 对方」 | 交给单据引导（next 原样带） |

「单据」业务（documents）仍用 `guide_pending.result`，C3 并进 form_result。

## 验证
- 离线：test_erp_stocktake_guide / test_erp_company_guide / test_erp_stock_card_guide / test_erp_pending_guide（结果卡名字、删除条数、同步中）、test_cc_coverage（31 done）。
- 页面流程（本机实验栈 pearnly-cov1124-lab）：erp.cc.guide.stocktake、erp.cc.guide.company、erp.cc.guide.stock-card、erp.cc.guide.pending 各电脑 / 手机两格通过。
- 没测：添加公司没有页面流程（实验栈连接目录要有一家未接进 ERP 的公司），格子留 half。

## 总表
C1 + C2：✅ 31 · ◐ 16（范围内 90 格）。
