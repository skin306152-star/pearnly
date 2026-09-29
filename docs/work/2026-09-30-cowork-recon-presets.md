# Cowork 对账预设：规矩类的不确定问一次、记住、以后照办（2026-09-30）

来源：用户在修复窗口问「OCR 或规则遇到瓶颈时，哪些情况人工提前兜底能大幅降错或 100% 不错」，随后定：「这套框架希望三个口都能用，
是不是类似于检测出某类问题，提问一次，然后有个保证？交互东西要做好」。方案（先做共用底座 + 银行 ①② + 收入 ⑥；预设只在当前工作空间内
按客户公司记；能建对账的人都能回答和修改、记录谁何时改）用户回复「可以」。

- 分支 `codex/cowork-recon-presets`，工作树 `.claude/worktrees/cowork-recon-presets`，**叠在 `codex/cowork-recon-scanned-docs`
  （422a933b 起，现含 920903c5）之上**：用到了那边的合计组（`bank_groups`）、识别任务复用等。扫描件分支合入 main 后本分支变基再走合入流程。
- 只改 `pearnly_cowork`（共用层未动）。未同步测试机（压测窗口最终回归期间不同步），未部署。

## 口径

| 环节 | 做法 |
| --- | --- |
| 检测 | 对账时遇到系统自己定不了、但定一次规矩就能定的地方，不猜，列成一个问题（`reconciliation/presets.py`） |
| 提问 | 结果页顶部原生按钮「需要定一次（N）」→ 原生对话框一次一个问题：标题是问题，附 1–3 行证据，选项是整行按钮，点了即生效（无确认步骤）；答完自动接下一个 |
| 记住 | 原生 DocType `Cowork Recon Preset`（工作区 + 类型 + 范围 + 规则唯一），记值、来源任务、谁、何时；只经 `preset_store` 读写，通用读写删分享一律拒绝 |
| 生效 | 用已识别的数据当场重算（不再识别、不扣费）；已完成的任务连同存档行与导出一起更新。例外见 ①：要用原文件重跑 |
| 复用 | 以后同一范围同类情况直接按预设处理，标题旁说明「按预设 …」，工作簿里合计组显示「L4-合计匹配（按预设） #n」 |
| 不硬套 | 预设应用自带校验：合计组只收唯一解；记住的科目在这份总账里没有就不用、改为重新问 |
| 管理 | 列表页原生按钮「对账预设」→ 对话框列出本类全部预设（范围 · 规则、值、谁何时定），固定选项可直接改，科目号类只显示、可删（删了下次重新问） |
| 边界 | 读错字（像素级）不在这里：那类靠余额链 / 合计核对指出位置，交人改数 |

## 范围（按什么记）——与方案的差异

方案写的是「按客户公司税号」。读代码发现银行对账与收入的总账一侧都读不出本公司税号（只有销项税报表头印着），所以：

- **银行按账户记**：`银行 + 账号末四位 + 总账科目`（`presets.bank_scope`）。一个工作区有多家客户公司，同一银行同一科目号可能是
  别家的，所以至少要有尾号或科目号之一，否则不问（照旧只提示）。账号末四位新记在任务诊断 `stmt_files.account`（识别读出的账户身份，
  或表格账单每行的账号）。
- **「这个账户对哪个科目」按 `银行 + 尾号` 记**（科目号正是要问的）；没有尾号不问。
- **收入按税报表头的本公司税号 + 科目记**（`presets.income_scope`）；税号新记在任务诊断 `vat[].tax_id`，读不出不问。

## 已做的三类

1. **银行·多笔对一笔是不是汇总入账**（`grouped`，是 / 不是）。检测：`bank_groups.suspected_groups`（窗口内全部单边行合计正好相等、只有一种凑法）。
   定了「是」：`bank_groups.accept` 把这些组当作匹配（第 4 层，组号），汇总公式重算（`bank_matching.summarize` 从 `reconcile` 原样拆出）；
   建任务、复核确认、页面重算三处同一口径（`bank_recon_service._match(rules)`）。定了「不是」：不再提示疑似合计。
   用户 2026-09-30 定的「多笔对一笔只提示疑似」不变：没定过的账户照旧只提示，并问一次。
2. **银行·这个账户对总账哪个科目**（`gl_account`，答案是科目号）。检测：全科目总账、没指定科目、`likely_account` 分不出（并列或都不够）、
   且至少两个科目有对得上的流水。此时照旧全部科目一起对，**原文件留着**（`Cowork Recon Rerun`，7 天，每日 `rerun.expire` 清理；
   任务删除时一起删），结果页问一次；答了记住并**用原文件重跑**：新后台任务按原对账号取识别结果（`document_ocr.reader` 先找已有识别任务，
   不再提交、不再扣费），同一事务里先删原结果再建新结果（建失败原结果还在、原文件也还在）。以后同一账户按记住的科目读总账
   （这份总账里没有这个科目就不用，照旧自己挑，挑不出再问）。
3. **收入·税报里一笔都对不上的收入科目参不参与对账**（`excluded`，按科目一问）。检测：核对结果里对上了的科目之外、有贷方行的科目；
   一个科目都没对上（多半是别的问题）不问。定了「不参与」：只在核对时去掉，总账行照样存进 `inputs_json`，改了预设重算能拿回来。

## 改动位置

| 位置 | 改动 |
| --- | --- |
| `reconciliation/presets.py`（新） | 规则、答案校验、范围与标签（纯逻辑） |
| `reconciliation/preset_store.py`（新）、`doctype/cowork_recon_preset` | 预设存取与写门；`hooks.py` 拒绝通用访问与分享 |
| `recon_center/rerun.py`（新）、`doctype/cowork_recon_rerun` | 留原文件、重跑参数、过期清理；`hooks.py` 每日清理、拒绝通用访问 |
| `recon_center/presets.py`（新） | 页面接口：`answer`（重跑类返回后台任务号）、`listing`、`change`、`remove` |
| `reconciliation/bank_groups.py`、`bank_matching.py` | `accept`；`summarize`（从 `reconcile` 原样拆出）；行加 `group_no`（Row DocType 加字段） |
| `reconciliation/bank_recon_service.py` | `_match(rules)`、范围、账号尾号入诊断、科目预设与问题、`create(replace_task)`、`account_scores`（`likely_account` 行为不变） |
| `reconciliation/revision.py`、`recon_center/sheets.py` | 页面重算带预设；问题、按预设说明 |
| `reconciliation/income_recon_service.py`、`recon_center/vat_check.py`、`jobs.py` | 税号入诊断、排除科目只在核对时去掉；多份税报返回税号；重跑参数（`ocr_job` / `replace_task`）与留文件 |
| `reconciliation/export/*` | 第 4 层标签与底色、合计组排在普通匹配之后、总账明细不收组里银行那一侧 |
| `public/desk/recon.js`、`recon.css` | 问题对话框、预设清单、重跑跟随（与新建对账同一遮罩） |
| 词条 | `reconciliation.preset.*`、`cowork.recon.questions` / `presets*` / `rule.*` 等四语 |

有意差异（对照测试已登记）：银行导出标签表 103 → 104（`layer_4`）。

## 验证

- 离线（一次性容器 `pearnly/frappe-sample:16.34.1`，只读挂载、无网络）：`tests/test_cowork_recon_presets.py` 26 条；
  受影响的银行 / 收入 / 扫描件 / 契约 / 导出测试全部通过；`check-architecture.py` 通过；词条 `--check` 通过。
- **未做**：测试机真实站点（DocType 迁移、对话框与重跑实际点击、手机宽度）；真实语料上的问答效果。压测窗口最终回归结束后同步测试机再验。

## 待办

- 刷卡净额（B02 TTB：银行净额、总账毛额、错一天、没有手续费行）作为银行第三类规则：需先在测试站点看 B02 的比率分布再定检测口径。
- 销项核查暂无问题类型（③本公司信息 / ④单号规则 / ⑤日期写法 / ⑦名称别名是第二批）。
- 合入：等扫描件分支合入 main 后变基、跑同名闸门，按用户同意合入与部署。
