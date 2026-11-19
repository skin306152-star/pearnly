# MR.ERP 收款：结果句按账本出字（11-19）

分支 `pearnly/mrerp-collection`（收款直写第三轮）。只改账本是 MR.ERP 的公司看到的字；账本是 Express 的公司一字不变。
没有新增词条：全部用现成的 MR.ERP 词条或中性的状态词拼出。

## 待用户过目：原句 → 新句（只对 MR.ERP 公司）

| 入口 | 场景 | 原句（中 / 英 / 泰 / 日） | 新句（中 / 英 / 泰 / 日） | 用的现成词条 |
|---|---|---|---|---|
| LINE | 收款写成 | 已写入 Express：FR… / Written to Express: FR… / บันทึกลง Express แล้ว: FR… / Express に書き込みました：FR… | 已写入 · FR… / Written · FR… / บันทึกแล้ว · FR… / 書き込み済み · FR… | `erp.collection.status.written` |
| LINE | 收款没写成 | 未写入 Express：〈原因〉 / Not written to Express: 〈reason〉 / ไม่ได้บันทึกลง Express: 〈…〉 / Express に書き込めませんでした：〈…〉 | 失败 · 〈原因〉 / Failed · 〈reason〉 / ไม่สำเร็จ · 〈…〉 / 失敗 · 〈…〉 | `erp.collection.status.failed` |
| LINE | 没有覆盖日期的账套 | 未写入 Express：〈Express 账套期间〉 | 失败 · 〈MR.ERP 账套期间〉（同网页 / CC 的 MR.ERP 说法） | `erp.collection.status.failed` + 现有 MR.ERP 期间句 |
| 网页「收款」列表 | 写入结果那一列的列头 | Express | MR.ERP | `MR.ERP` |
| 网页「收款」导出 | 单号那一列 | Express 单号 / Express No. / เลขที่ Express / Express 番号 | MR.ERP 单号 / MR.ERP No. / เลขที่ MR.ERP / MR.ERP 番号 | `MR.ERP No.`（点开一笔的弹窗已在用） |
| CC 引导卡 | 收款方式那一步题目下 | 只选一种收款方式（说明行） | 删除（不加说明文案）；选了两种时作答被拒，拒收那句「只选一种收款方式」保留 | — |

排队句：LINE「已记录 · 待写入」本来就不说 Express；「Express 正开着这本账套」那句只在 Express 公司出现（MR.ERP 收款没有电脑排队，判断恒为否），不用改。

## CC 给模型的说明（不是界面文案，不进文案闸门）

- 收款结果卡（引导式）带 `ledger: "mrerp"`，模型按 MR.ERP 说。
- 对话式收款工具：账本是 MR.ERP 时，排队 / 被拒 / 正在读取这三种定式换成 MR.ERP 版（服务端导入，不提 CC 桌面版）；「刚读到」那句把 Express 换成 MR.ERP；工具描述改成「公司的账本（Express 或 MR.ERP）」。

## 验证

- `tests/test_erp_collection_line.py`（`MrErpResultLines`）、`tests/test_erp_collection_cc.py`（`MrErpNotes`）新增，收款五个测试文件全过。
- `python3 scripts/check-copy.py`：新增 5（上一轮已列）· 改动 0 · 拦截 0。

## 登记

- 2026-11-19 用户原话：「同意」（经主会话转达，针对上表「原句 → 新句」）。
- 已在 `scripts/i18n/copy-approved.tsv` 登记日期与本记录路径：MR.ERP 公司改用的现成词条 `erp.collection.status.written`、
  `erp.collection.status.failed`、`MR.ERP No.`（`MR.ERP` 那条 11-16 已由 MR.ERP 界面修正登记过）。本轮没有改动任何已有词条的文字或译文。
