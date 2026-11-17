# CC 桌面版 1.12.23 · 代录中途带回对话、被打断判定、叫人判据（11-16 ～ 11-17）

分支 `pearnly/cc-1123-desktop`（叠在 `pearnly/cc-stop-exits-h5` 上）。方案：共享笔记「CC桌面版1.12.23-剩余四件-更新做法-11-16.md」（主会话 11-16 回「可以做」，并入 A5 服务端一半）。
1.12.23 原有四项（uid 认同一账套、人选的存货科目组照写、代录回执顶层带码、凭证稽核栏）的记录在 `2026-11-16-cc-stop-exits-h5.md`。

## 做了什么
| 项 | CC 桌面版 | 服务端 |
|---|---|---|
| ③ 中途配方外的业务选择框 | 不点、单据窗口不关、Express 留着；回执原因 `dialog_choice`（保存途中已留半截单的仍 `partial_saved`）带 `meta.dialog`（指纹、标题、正文、按钮、单种、阶段）；载荷 `operator.dialog_answers` 里有同一指纹、按钮还在就照点 | `clean_operator_meta` 放行 `dialog`；`for_operator` 下发 `dialog_answers`；停下出口登记 |
| ④ 对话里选了按钮 | 领单回复 `operator_controls`（answer）在取任务线程里执行，核框没变才点；保存途中的由「留着等人」检查补报原回执号；录入中途的点完按放弃收拾、等带答案重交 | `cc_desktop/operator_controls.py` 信箱（10 分钟、只发 ≥1.12.23）；ERP / Cowork 判定把框列成一题（选项 = 框按钮），记住（ERP 按公司 + 指纹，Cowork 按连接 + 账套 + 指纹，`operator_dialog`）、给留着框的电脑发指令 |
| ⑥ 被打断 | 只认点到 / 按在本批 Express 上的（移动、滚轮、切前后台、别的程序里打字不算）；停手 3 秒接着录，满 60 秒还在点 → `interrupted`；人动过后核对不上不交 AI、转人工 | 原因登记、四语报告词条；卡片按钮写「继续」（= 重交、先读账套），停止用对话里的停止 |
| ⑨ 叫人判据 | 页面经桥 `attention_seen{session, visible}` 报开着哪段对话；CC 窗口在最前面、页面看得见且就是这段对话 → 这条对话提醒不弹；有没录成的批次通知点开待处理页 | 页面一半归 frontend-backlog（C-4） |

没做 / 后置：单位框按答案点（要先在测试机录原生单位框）；站点下发 `muted` 静音（等 `attention.call` 进 main）；流程 `cc.operator.dialog-choice`（随全套流程一起跑时补）。

## 文案改动（用户 11-16 同意，主会话转达原话「文案同意」）
出现在哪：CC 桌面版每批写账结束弹的 Windows 通知（`gui_tray._on_batch_done`）。条件：与「点通知打开 CC 窗口的待处理页」同一个提交上线（本提交）。

| 键 | 语言 | 现文案 | 改后 |
|---|---|---|---|
| notif_partial_body | 中文 | 本批 {ok} 张已录入，{fail} 张录入失败，请在网页中处理。 | 本批 {ok} 张已录入，{fail} 张录入失败。 |
| | 泰文 | ชุดนี้บันทึกแล้ว {ok} ใบ ไม่สำเร็จ {fail} ใบ กรุณาดำเนินการบนเว็บ | ชุดนี้บันทึกแล้ว {ok} ใบ ไม่สำเร็จ {fail} ใบ |
| | 英文 | {ok} entered and {fail} failed in this batch. Handle them on the website. | {ok} entered and {fail} failed in this batch. |
| | 日文 | 今回 {ok} 件を入力し、{fail} 件は入力できませんでした。ウェブで処理してください。 | 今回 {ok} 件を入力し、{fail} 件は入力できませんでした。 |
| notif_failed_body | 中文 | 本批 {fail} 张录入失败，请在网页日志中查看原因。 | 本批 {fail} 张录入失败。 |
| | 泰文 | ชุดนี้บันทึกไม่สำเร็จ {fail} ใบ กรุณาดูสาเหตุในบันทึกการทำงานบนเว็บ | ชุดนี้บันทึกไม่สำเร็จ {fail} ใบ |
| | 英文 | {fail} in this batch failed. See the reason in the website log. | {fail} in this batch failed. |
| | 日文 | 今回 {fail} 件を入力できませんでした。理由はウェブのログで確認してください。 | 今回 {fail} 件を入力できませんでした。 |

新增文案（直接写，四语）：代录原因「Express 有提示框待选择」「Express 被人操作过」；题类名「Express 提示框」。

## 真机
- 1.12.23 原有四项：件一（Express 原生不拦 ST01）、件二（101 本回放只差五个稽核栏）、件三（uid 三行）都通过，见共享笔记「CC桌面版1.12.23-真机结论-11-16.md」。
- 本记录这几项还没上真机：测试机2 + 70EXP\test，配方外的框、答框、被打断、托盘「正看着」各一行，待 h5 合入、1.12.23 变基后安排。

## 检查
- 提交前本地：CC 全量单测、`test_companion_contract.Release1123Edges`、ERP / Cowork 相关测试、停下出口（146 个全归类）、架构、文案闸门。全闸门 / 候选 / 并集流程在 h5 合入 main、本分支变基后整套跑。
