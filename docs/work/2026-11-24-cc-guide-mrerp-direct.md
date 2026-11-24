# CC 直写 · MR.ERP 写不进的两处修复（cc-guide-mrerp-direct，11-24）

主会话 11-24 批准；线上缺陷（ERP 直写 11-23 已上线）。评估口径见 docs/work/2026-11-24-cc-guided-documents-评估.md 同日消息。

## 现象与根因
- ERP：`express/precheck.py` 未接 Express 的公司上，收单记录只有 LINE 来源走 MR.ERP 判定（`operator.intake_mrerp_view`，11-19 加），
  CC 对话放进来的一律 `express.disconnected` → ERP 直写 · MR.ERP 写不进；代录对话同样报这个错原因。
- Cowork：`assistant/guided/push_setup.py`（11-16）没有调用方，判定卡在 MR.ERP 账套上不出导入菜单 / 默认值题；账套有几个已关联菜单
  或缺默认值时，每张都停在「先选导入菜单 / 默认值」，CC 里没处选（本机实验栈 + MR.ERP 专属测试账套实测：两次停下；用网页同一个函数记好
  选择后写成 PG691124-380119、SI691124-703585——这两张不算修好后的证据）。

## 修法
- ERP：按「公司接的账本 + 对话方式」判，不按来源；模式先看记录自己放进来的那段对话（session_id）。直写 → MR.ERP 判定；代录 → MR.ERP
  公司报 `unsupported`（`guide.reason.unsupported` 四语），没账本报 disconnected；记答案同判据。
- Cowork：判定卡推送目标是 MR.ERP 时出 `push_setup.items`（只在直写对话；代录是 CC 桌面版在网页里录，不问导入题），选项「代码 · 名称」；
  作答按账套记一次交 `push_setup.remember`；push_setup 的 Express 存货科目组段删掉（与推送判据 new_item_group 不一致，主会话定）。
- 守门：push_setup 出题 ⇔ `needs.needed`（推送同一视图）；代录对话不出导入题；ERP 两种来源同判据、代录报 unsupported。

## 共用件连带回归

`python3 scripts/check-impact.py` 出的入口（说明见 `scripts/impact/shared-parts.toml` 同名入口）：

| 入口 | 结果 |
|---|---|
| `cowork.line.precheck-questions` | 没测到（页面流程与 MR.ERP 测试账套真写排在本分支下一段，由接手窗口做；本段只有判定 / 记答案的服务端改动，离线单测守住） |
| `erp.cc.write` | 没测到（页面流程与 MR.ERP 测试账套真写排在本分支下一段，由接手窗口做；本段只有判定 / 记答案的服务端改动，离线单测守住） |
| `cc.operator.page` | 通过（离线：本分支全闸门里同名离线检查）——以三步结果为准 |
| `cowork.express.push` | 没测到（页面流程与 MR.ERP 测试账套真写排在本分支下一段，由接手窗口做；本段只有判定 / 记答案的服务端改动，离线单测守住） |
| `erp.cc.collection` | 通过（离线：本分支全闸门里同名离线检查）——以三步结果为准 |
| `cc.operator.start-card` | 通过（离线：本分支全闸门里同名离线检查）——以三步结果为准 |
| `cowork.cc.mrerp-setup` | 没测到（页面流程与 MR.ERP 测试账套真写排在本分支下一段，由接手窗口做；本段只有判定 / 记答案的服务端改动，离线单测守住） |
