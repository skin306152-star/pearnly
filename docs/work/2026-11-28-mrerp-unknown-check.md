# MR.ERP 结果不明「核对」出口（2026-11-28，cc-coverage-5）

分支 `pearnly/mrerp-unknown-check`，基 `pearnly/web-result-unknown`。评估 `~/pearnly-devtools/handoff-1128/cc-coverage-5-mrerp-核对-评估.md`，
用户 11-28 批准「做」：a 按钮「已在 MR.ERP 核对」+ 四句字段换成 MR.ERP；b 收款核对成已入账后发票余额等下次读回，不当场逐张填；c LINE 不加按钮。

## 口径（同 Cowork 复核页核对 `push.native.service.resolve_unknown`）
- 只收「账本是 MR.ERP + 停在结果不明」（单据 `held` + `mrerp.result_unknown…`；收款 `failed` + 同前缀），别的状态 / 账本拒；`entered` 只收布尔。
- 已入账 → 单据已写入 + 人填的 MR.ERP 单号（排一次主档整读，同写成）；收款已写入 + 单号、按客户再读一次未收清发票。
- 没有入账 → 单据停下、原因 `mrerp.confirmed_not_entered`（弹窗换成「重试」，`mrerp.push.send` 照常再推）；收款失败、同原因（可撤回重录）。
- 规则一处：`mrerp.contracts.checked`（单据与收款共用）；谁何时判了什么记一条评论（Comment Info「MR.ERP check by …」）。
- 权限：单据同「重试」（`express.api._document(write=True)`）；收款同撤回（`service._own`）。

## 入口
| 入口 | 位置 |
|---|---|
| 单据 MR.ERP 弹窗 | `express.api.resolve_unknown` + `transactions.js`（`can_check`：结果 Select + MR.ERP 单号） |
| 收款页 | `collection.api.api_collection_check` → `service.check_unknown` → `collection.mrerp.resolve_unknown`；`collections.js`（`checkable`，不给撤回） |
| CC 单据 | `erp_resolve_push_result` 对 MR.ERP 单照用户答的记（只在同意了的写入计划里） |
| CC 收款 | 新工具 `erp_collection_resolve_result`（先出「允许」卡片） |

## 新增文案（四语）
| 键 | 中文 | English | ไทย | 日本語 |
|---|---|---|---|---|
| mrerp.unknown.check | 已在 MR.ERP 核对 | Checked in MR.ERP | ตรวจใน MR.ERP แล้ว | MR.ERP で確認済み |
| mrerp.unknown.outcome | MR.ERP 里的结果 | Result in MR.ERP | ผลใน MR.ERP | MR.ERP での結果 |
| mrerp.unknown.entered | 已入账 | It was entered | ลงบัญชีแล้ว | 計上されている |
| mrerp.unknown.not_entered | 未入账 | It was not entered | ยังไม่ลงบัญชี | 計上されていない |
| mrerp.reason.checkedNotEntered | 已核对，未入账 | Checked, not entered | ตรวจแล้ว ยังไม่ลงบัญชี | 確認済み・未計上 |
| （工具名）| 确认了收款结果 / 正在确认收款结果 | Confirmed / Confirming a collection result | … | … |

## 验证
- 离线：test_erp_mrerp_direct（单据已入账 / 没有入账 / 非结果不明不收 / 只收布尔）、test_erp_mrerp_collection（收款两条）、
  test_erp_collection_cc（工具声明与四语）、test_erp_mrerp_unknown_check（入口、权限、CC 顺序、四语）。
- 页面流程：见下方「流程结果」。

## 流程结果（本机实验栈 pearnly-cov1124-lab，--migrate）
- erp.mrerp.unknown-check：采购单弹窗核对「已入账」→ 成功 + 填的单号；销售单「未入账」→ 失败 · 已核对，未入账、按钮换成「重试」；库里两张与核对评论（电脑 / 手机 2/2）。
- erp.mrerp.unknown-check.collection：收款页点开结果不明的一笔，只有「已在 MR.ERP 核对」、没有撤回；已入账后再点开成功 + 单号（2/2）。
- erp.cc.mrerp-unknown-check：CC 收款核对出「允许」卡（问句「已在 MR.ERP 核对」、客户 · 发票 · 金额 · 结果）、允许后记「已核对，未入账」；
  CC 单据核对不在同意了的写入计划里不收、单子仍结果不明（2/2）。CC 单据「已入账」那条要用户同意过的写入计划，页面上没走（工具里与网页同一个
  `express.api.resolve_unknown`，网页那条已覆盖）。
