# MR.ERP 单据页与 CC 推送的四处修正（11-16，写账窗口）

来源：11-16 测试环境（testerp 实验栈）MR.ERP 写账 T9–T11 真浏览器 / CC 对话实测发现的 F1–F4（结果记录在本机，不入库）。主会话 11-16 定「F1–F4 修，并成一个小分支、开关之前进」；F1 / F2 用字用户 11-16 同意（原话「都按建议」）。

## F3：可再推重试的停住原因，CC 照旧原因拒推
- 现象：MR.ERP 单停在「连接还没授权登录」，登录补回后在 ERP CC 对话里让 CC 推：第 1 轮 CC 照单据上存的旧原因说推不了；第 2 轮建了写入计划又因状态仍是那句多问一次确认；第 3 轮才推成。三轮同因：列单 / 单据详情 / 推送结论三处把这类原因原样当失败给 CC，没有说「再推即重试」。
- 修：`express/cc_tools.push_again`——MR.ERP 账本、停住、原因不是结果不明 / 要人选 / 作废后写进了的，三处输出带 `push_again: true`；列单说明加一句「见到 push_again 就照常建计划推送，不因旧原因再问或拒」。测试 `tests/test_erp_cc_chat_push.py::PushAgainTests`。

## F4：写入计划对 MR.ERP 单说「没有指定账套」
- 修：`express/precheck.mrerp_view` 带上按单据日期选出的那本年度账套（`target`，同 Express 单的 `operator.book_target` 形状）；选不出时不编。测试 `tests/test_erp_write_precheck.py::MrErp::test_the_card_names_the_account_set_the_document_goes_into`。

## F1：MR.ERP 公司的单据页按钮仍写「Express」
- 修：采购 / 销售单据页按钮按单据记的账本取名：`ledger_target == "mrerp"` 用现成词条「MR.ERP」，其余照旧「Express」；采购 / 销售取数字段加只读的 `ledger_target`（导出照旧排除）。

| 位置 | 改前（th / zh / en / ja） | 改后（th / zh / en / ja） |
|---|---|---|
| MR.ERP 公司的采购 / 销售单据页按钮 | Express / Express / Express / Express | MR.ERP / MR.ERP / MR.ERP / MR.ERP（现成词条 `MR.ERP`） |
| Express 公司 | Express（不变） | Express（不变） |

## F2：MR.ERP 要选的几项下拉标题相同
- 修：`transactions.js` 的 `needed_fields`：MR.ERP 的导入菜单 / 主档默认值，标题用这一项自己的名字（服务端已给的现成词条 `mrerp.menu.*` / `mrerp.default.*`），同类总称挪进 ⓘ 说明；Express 各项不变。

| 项 | 改前标题（四语） | 改后标题（四语） | 改后 ⓘ |
|---|---|---|---|
| 采购货品菜单 | เมนูนำเข้า MR.ERP / MR.ERP 导入菜单 / MR.ERP import menu / MR.ERP 取込メニュー | ซื้อ (สินค้า) / 采购（货品） / Purchase (goods) / 仕入（商品） | 原总称「MR.ERP 导入菜单」四语 |
| 采购费用菜单 | 同上 | ซื้อ (ค่าใช้จ่าย) / 采购（费用） / Purchase (expense) / 仕入（経費） | 同上 |
| 赊销 / 现销菜单 | 同上 | ขายเชื่อ / 赊销 / Credit sales / 掛売上；ขายเงินสด / 现金销售 / Cash sales / 現金売上 | 同上 |
| 主档默认值（例） | การตั้งค่า MR.ERP / MR.ERP 设置 / MR.ERP settings / MR.ERP 設定 | บัญชีเจ้าหนี้ / 应付科目 / Payable account / 買掛金勘定；บัญชีเงินฝากธนาคาร / 银行收款科目 / Bank account / 銀行勘定 | 原总称「MR.ERP 设置」四语 |

文案：只复用现成词条，不新增、不改词条文本（`check-copy.py`：新增 0 · 改动 0）。
