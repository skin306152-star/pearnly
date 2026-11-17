# ERP「CC 桌面版未连接」改成「未连接电脑」（欠账 B60）

- 依据：user-decisions-2026-10-18「未连接电脑文案」；Cowork 已用共用层同一说法（`intake.agent.state.missing`）。ERP 一直写「CC 桌面版未连接」。
- 清单 11-16 晚经主会话转用户，用户回「文案没问题」（清单原件：分支 `pearnly/cc-frontend-backlog-status` 的 `docs/work/2026-11-16-cc-frontend-backlog-status.md` 第 7 节）。
- 分支 `pearnly/erp-no-computer-words`，基线 origin/main 847fc020c。

| 出现处 | 现在（中 / 泰 / 日 / 英） | 改成 |
|---|---|---|
| ERP 待处理与单据页原因 `express/pending.py`、`public/desk/transactions.js` | CC 桌面版未连接 / CC เดสก์ท็อปไม่ได้เชื่อมต่อ / CC デスクトップが接続されていません / CC Desktop not connected | 未连接电脑 / ยังไม่ได้เชื่อมต่อคอมพิวเตอร์ / パソコン未接続 / No computer connected |
| ERP 公司页「同步 Express」没电脑 `public/desk/company.js`、`public/desk/company-connect.js`（词条 `company.syncExpress.offline`） | 同上 | 同上 |
| ERP 收款引导读账套时 `collection/guide.py` | 同上 | 同上 |
| ERP 收款 LINE 回复 `integrations/line_collection.py` | 同上 | 同上 |

做法：ERP 术语表 `i18n/messages.tsv` 那一行四语改掉（英文原句同时改，代码里四处英文原句跟着改），`scripts/i18n-build.py` 重出翻译包；`scripts/i18n/copy-approved.tsv` 登记。
