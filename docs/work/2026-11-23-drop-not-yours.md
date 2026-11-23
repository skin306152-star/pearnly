# 删「您的电脑离线」(not_yours) 旧分支与词条（2026-11-23）

「谁的电脑写单」11-17 定稿（同事能写的电脑在线就替他写，领单时判）上线后，两产品的电脑状态不再回 ``not_yours``。
`docs/work/2026-11-17-cowork-office-writer-评估.md`「下一批」列的旧分支与词条，主会话 11-23 按核数结果定一起清。

## 依据（生产只读，主会话 11-23 经甲骨文中转跑，`~/pearnly-devtools/prod-ro-1123/`）

- 库里存着这个原因码的：ERP 各站 CC 写账件 0、单据 ``ledger_reason`` 0；Cowork 三站推送记录 0、票上 ``last_push_reason`` 0。
- CC 桌面版（`companion/`）从不读 ``not_yours``，只有服务端与网页读，电脑上 CC 的版本新旧不影响（当时在用：1.12.23 ×9、
  1.12.21 ×4、1.9.2 ×1、1.7.0 ×1、未报 ×2）。
- 生产者早已没有：`cc_desktop.jobs.writers` 与 `companion.pool.state` 只回 ``ok`` / ``offline`` / ``unreachable`` / ``missing``，
  读回服务也不再回 ``not_yours``。

## 删了什么

- ERP：契约常量 ``NOT_YOURS``（`express.cc_desktop_not_yours`）及它在 `cc_tools.OFFLINE`、`operator.COMPUTER_REASONS / ORDER`、
  `push._queued_note`、`pending.REASONS`、`public/desk/transactions.js` 的映射；共用层 `assistant/stop_exits.py` 的同一键。
- Cowork：`assistant/operator_source.py` 的 ``…notYours`` 分支、`assistant/tools.py` 回包的 ``cc_desktop_not_yours`` 与给模型的
  两段说明（其中一句「同事的电脑不会替他写 / 读」与 11-17 定稿相反）、`guided/readback.py` 的 ``WAITING["not_yours"]``、
  `folder_intake/destinations.py` 取 ``not_yours`` 的两处、`public/desk/targets.js` 的分支。
- CC 对话页：`frontend/assistant/src/components/scheduled-tasks.tsx` 的分支与类型字段。
- 词条：`intake.agent.state.notYours`（Cowork）、`express.reason.notYours`（ERP），四语；翻译包里这两条键与随它生成的英文原文行
  一并删（`i18n-build.py` 合并时不删已有行，要手删），`i18n-build.py --check` 通过。
- 单测改成断言这些都不在了；Cowork 旧形状里多带 ``not_yours`` 的也只按电脑离线说。

`assistant/stop_exits.py` 里 ``"not_found": act("not_yours")`` 是另一回事（找不到 / 不是本人能写的单的退出动作），不动。

## 界面文案（`scripts/check-copy.py --report`）

新增 0 · 改动 0 · 删除 2 · 拦截 0：

| 来源·键 | 原中文 |
|---|---|
| pearnly_cowork·intake.agent.state.notYours | 您的电脑离线 |
| pearnly_erp_portal·express.reason.notYours | 您的电脑离线 |

两条都已触发不了（上面依据），删后用户看得到的文字不变。
