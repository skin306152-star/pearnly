# 直写 / 代录：没有明细的买卖单补一行「编码 = 科目码」的服务品（11-24）

分支 `pearnly/direct-amount-only-line`，基线 origin/main `c6fd4d177`。窗口 local_ac1aafb6（代录缺陷）。主会话 11-24 批准立项，随 CC 1.12.25。
前情：`docs/work/2026-11-24-cc-operator-no-items-评估.md`（代录无明细票、统一收尾；收尾已拆到 `pearnly/operator-clean-exit`）。

## 缺陷
直写的「只有单头」单——RR / HP / IV / HS，OCR 没读到明细、明细对不上、人选了「只记金额」——人在 Express 里「แก้ไข → 不改 → 保存」，这张单就被清零：
- 单头 AMOUNT / NETAMT / VATAMT 等全部清空，DOCSTAT 仍是 N；
- 凭证 GLJNL 与分录 GLJNLIT 全部打删除标记；
- ISVAT 的 AMT01 / VAT01 清空；
- 全程没有任何提示。

测试机2 69EXP\TEST 实测（11-24 15:01–15:04）：
- 进货 RR691124-001、销货 IV0000034 都被清零；
- 带明细的单修改再存不变：代录的科目行单 RR0000310、直写的有明细单 RR691124-002。

原生：Express 进货 / 销货单的「จำนวนเงิน」框只读，界面上录不出只有单头的单。S 盘 2568 年 16 本，当年人录的单没有只有单头的；服务 / 费用单是明细一行「编码 = 科目码」的服务品（RR 4361、HP 2311 张）。

## 改法（只动 CC，载荷与服务端不变）
- 新共用模块 `companion/amount_only.py`：
  - `line()`：没有明细行、`items_status` ≠ ok、有 `items_account`、税前 > 0、不是 POS 日汇总、单种为 RR / HP / IV / HS，就补一行直接科目行：科目 = items_account，数量 1，单价 = 税前（代录按含税价录时含税），品名 = 科目名；
  - `fill()`：直写用，把这一行放进 entry。
- 调用方：直写进货 `dbf_writer.write_purchase`、直写销货 `dbf_sales.write_sale`（都在三道闸之后、写之前），代录 `express_operator.masters.amount_only_line`。三处同一个函数。
- 补出的行照原有的直接科目行写（`dbf_detail` → `product_master.ensure_account_item`）：
  - 编码 = 科目码，有就沿用、不改；
  - 同码是库存品就整单交人（`ACCOUNT_CODE_IS_STOCK_ITEM`，主会话定：不保留只写单头）。
- `ensure_account_item` 新建时，科目组和单位优先照抄本账套原生的「编码 = 科目码」品（4 / 5 型、编码在科目表里，`_pick_account_template`）。没有样本时照旧照抄随便一件非库存品（待评估）。类型仍按 11-02 的决定固定为 3。

## 验证
- 单元测试：`companion/tests/test_amount_only.py` 18 条，涵盖：
  - 进货 / 销货新旧写法比：单头、凭证、分录、税记录、应付 / 应收逐字段相同，只多 STCRD 1 行与服务品；
  - 同码是库存品交人、账套不变；已有同码品照用；新建照抄原生科目码品的组和单位；
  - 不补的六种情形；
  - 代录与直写同一个函数；
  - 代录建主档补行（原代录分支的测试移过来）。
- 101 本 T 账套离线回放（测试机1 后台，11-24 15:23–16:05，`scripts/express-amount-only-regression.py`）：
  - 做法：每本复制两份，旧写法、新写法各写一张无明细 RR 与 IV，逐字段断言，并核 CDX 与 DBF 一致；
  - 结果：**98 通过 / 0 失败 / 3 跳过**。跳过的是 T7PROPHA、T8PROPHA（没有默认科目或对方）和 thai（没有会计期间）；
  - 补行服务品：RR 新建 95、沿用 3，IV 新建 83、沿用 12；
  - 结果文件：`~/pearnly-devtools/operator-empty-items-1124/evidence/replay101/`；
  - 回放跑的是 code-review 修复之前的提交 c14c901a2。审查后新加的两处（不可信明细也换成补行、同类科目样本优先）只影响有部分 OCR 行的票和新建品的科目组 / 单位，由单元测试覆盖，单头 / 凭证 / 税记录的对照结论不受影响。
- 实机回归（固定用例，以后改直写写法都要过）：`scripts/express-operator/edit_save_regression.py`。直写无明细 RR / IV → Express 修改再存 → 断言金额、分录、税额、明细不变 → 原生作废 → 断言 DOCSTAT C。
  - 测试机2 69EXP\TEST，用账套里原本没有服务品的科目 52-01-00-00、42-07-00-00：
  - 新写法：RR691124-004、IV0000036 **通过**；
  - 同一脚本用 origin/main 源码：RR691124-005、IV0000037 **不通过**（金额、凭证、税额清零），证明这条用例抓得到。
  - 测试单都已原生作废。
- 实机顺带看到、与本缺陷无关（直写和原生原有的差别，归入「直写只有单头」那份事实评估）：Express 存档时把单头 CMPLAPP 由 Y 改成 N、清掉 CMPLDAT；税记录补上 DESCRP / TAXID / PRENAM / SELF_ADDED / HAD_MODIFY，销货税记录的 REFNUM 改成单号。

## 共用件连带回归（check-impact）
| 入口 | 结果 |
|---|---|
| cc.desktop.write（直写） | 通过：单元测试 + 101 本回放 + 测试机2 实机 |
| cc.desktop.operator（代录建主档补行） | 通过：单元测试；实机 11-24 代录录成 RR0000310 / IV0000033 |
| cowork / erp 的写 Express 入口 | 没测到实机：载荷不变，只有无明细票多一行明细、新建科目码品的组和单位变了；有明细的单、库存单写法不变，CC 全量离线测试覆盖 |

## 剩余
- 存量：已有多少张只有单头的直写单、有没有已经被清零的——今晚 S 盘只读统计，结果给主会话问用户；不动真实账套。
- 类型 3 还是照原生用 5、没有原生样本时的组和单位：并进「直写只有单头」那份事实评估。
