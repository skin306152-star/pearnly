# CC 输入框两处「发不出去」（11-11 发布核查、压测 S8a）

分支 `pearnly/cc-composer-send-ae62d2`，基线 origin/main 32695c276（开工时 6275e4999，施工中变基一次）。主会话 11-12 派、方案审定后施工。
范围状态：本次范围完成，待主会话回复后由拼车窗口合入；未部署。

## 现象与真因

1. 输入框里留着一张超限失败的附件卡时，再发消息发不出去。
   发送时页面把没传上的附件重传一次，仍失败就整条不发、字和附件放回输入框（10-17「不静默丢附件」）。超限的文件永远传不上，不删卡就永远发不出。
   旁带：失败卡只有红色感叹号，文件名和原因要悬停才看得到；每发一次往站点 Error Log 写一条（`SiteError` 没写名字，回传脚本认不出是站点拒收）。
2. 提问卡（`ask_user`）挂着时，输入框里打的话发不出去。
   提问卡、「允许 / 拒绝」卡挂着时页面把对话算「在忙」，发送进排队、等这一轮结束才发；这一轮正等用户答卡，永远等不到。
   服务端也没接：引擎停在等卡片结果时不收用户消息，`sessions.send` 只会先交回「识别挂着等」的调用。

## 同类清单

| 挂着的东西 | 以前 | 现在 | 证据 |
|---|---|---|---|
| 提问卡 `ask_user` | 排队、永远发不出 | 当场发出，那一轮结束、卡片作废 | 流程 `cc.send-unblocked` |
| 「允许 / 拒绝」卡 | 同上 | 当场发出，卡片作废、工具不执行 | 同上 + `AskUserTests` |
| 超限 / 空文件 / 不收的类型 | 留失败卡，挡住发送 | 选中时拒收，不进输入框 | 同上 + `test_upload_limits.py` |
| 一时传不上的附件（断网、5xx） | 整条不发、写原因 | 不变；失败卡上有文件名与原因，移除后发得出 | 流程第 5、6 步 |
| 识别挂在服务端等 | 发得出 | 不变 | 流程 `cowork.cc.send-while-waiting`、`cowork.operator.send-while-waiting` |
| 代录开始前 / 中途选 / 继续卡 | 发得出 | 不变 | 流程 `cc.operator` |
| 「录吧」等点头 | 发得出 | 不变 | 流程 `cowork.cc.record-reply` |
| CC 回复中 | 排队、可撤回、本轮完了自动发 | 不变（设计如此） | 流程 `cc.chat` |
| 代录新对话没选公司 / 月份 | 发送键禁用 | 不变（已有设计） | 没单独测 |

## 改法（主会话 11-12 审定：A、B、C3）

- **A / B 提问卡、允许卡挂着时打字**：页面不排队（`reducer.holdsSends`：只有模型这边有活在走才排）。`sessions.send` 送消息前
  查本人这段对话有没有卡片在等（`toolrun.waiting`，只读）；有就对引擎结束等卡片的那一轮（`user.interrupt`）、卡片作废
  （`toolrun.cancel_waiting`，没点「允许」的调用不执行），再发这条消息，随消息带一句给模型的说明（`toolrun.TYPED_INSTEAD_NOTE`：
  卡片没答 / 没允许，这句就是回答）。卡片显示「已取消」。这条路没有作答、没有执行工具的分支，测试钉住。
  - 最初两版（先把卡片结果交回、再发消息）被真模型实测否掉，见下文「真模型实测」。
  - **不带「停止」的副作用**（主会话 11-12 要求先核清）：线上停止 `sessions.interrupt` 除了打断引擎还做两件事——`_mark_stop`
    记下用户喊停的时刻（写入计划逐段写时核它，变了就停）、代录会话给任务发停止控制（`_operator_stop` → `tasks.push_control`，
    电脑上在录的任务会停）。这里不调 `sessions.interrupt`，只发引擎的 `user.interrupt` + 作废等着的卡片：不记用户喊停、不给代录任务
    发停止；等用户点头的写入计划（`Pearnly Assistant Write Plan` 的 awaiting）是另一张表，不受影响；已经在执行的写账工具
    （执行记录 running）不在作废范围内（`cancel_waiting` 只认 awaiting_approval）。引擎事件里的 `user.interrupt` 另有一处读者
    `stopped.Previews`（停下时没写完的半段正文记下来），等卡片时没有半段正文，没有可记的。
- **C3 永远传不上的文件选中时拒收**：超限、空文件、服务端不收的类型在选中 / 拖入 / 粘贴那一刻拒收（`attachments.refusalOf`），不进输入框、
  不生成附件卡、不建会话、不上传，原因写在输入框上方那一行；一次选几份时没问题的照常加入。上限与类型清单都由服务端写进页面
  （`window.pearnly_upload_max_bytes`、新增 `window.pearnly_upload_extensions` = `contracts.UPLOAD_EXTENSIONS`），前端不另存。
  类型判定收成一条路：适配器 `accept` 改 `"*"`，「+」按钮自己开选文件框（仍只列 `ACCEPT`），不再由组件库按 accept 抛错（那条路页面没反应，还留一条未接住的错误进 Error Log）。
- 一时失败照旧整条不发；失败卡用标准组件 `Attachment`（出错态：文件名 + 原因 + 移除）。
- `SiteError` 写明名字：站点已拒收、页面已提示的不再记进 Error Log。
- 标准组件（用户 11-12）：出错提示三处（发送没成功 / 文件拒收、回复出错、通知没打开）换成 `Alert`，失败附件卡换成 `Attachment`；
  `check-frontend-kit` 基线 338 → 337。
- 文案：新增 2 条（四语），不改已有文案——`fileTypeUnsupported`「不支持此文件类型」、`fileEmpty`「文件为空」（主会话 11-12 批；Frappe 自带词条没有同义的短句）。
- 实验室假智能体：提问卡挂着时也照真实引擎拒收用户消息（以前对提问卡放行，量不出这个顺序）。

改到的服务端函数：`assistant/sessions.py` 的 `send`（发消息前加一段）、`assistant/toolrun.py` 新增 `waiting` 与常量 `TYPED_INSTEAD_NOTE`
（`decide`、`cancel_waiting`、`ask.py` 没改）、`www/assistant.py` 与 `www/assistant.html`（多写一个页面变量）。没有碰 `_segment_events`、`segment_for_turn`、写账目标相关函数。

## 验证（8b3f0fd6d 之后的工作树，本机实验栈 pearnly-csend-lab，假智能体，不调付费模型）

- 旧代码（6275e4999 的产品代码 + 新测试）：流程 `cowork.cc.send-unblocked` 电脑宽度 9 项全红；单元测试红。
- 新代码：流程 `cowork.cc.send-unblocked`、`cc.send-unblocked`（ERP）电脑 / 手机四格全过；截图 `~/pearnly-devtools/composer-send/shots/final/`。
- 回归流程全过：`cc.chat`、`cc.operator`（new / old）、`cc.attach-delete`、`cowork.cc.attach-delete`、`cowork.cc.ask`、`cowork.cc.send-while-waiting`、
  `cowork.operator.send-while-waiting`、`cowork.cc.record-reply`、`cc.machine.operator-ai`、`cc.sidebar-modes`，电脑 / 手机宽度。
  中途 `cc.operator` 红过一次：发消息前那一步的回滚丢了代录第一句开远端会话的写入，已修并加测试（查卡片只读；作废前先提交）。
- 与 CI 同名的闸门、候选检查：见提交说明与完成消息。

### 共用件影响清单（`check-impact.py`，15 个入口）

| 入口 | 结果 |
|---|---|
| `cc.assistant` | 通过（实验栈 Cowork、ERP，电脑 / 手机：新建对话、发消息、附件、删除对话、侧栏模式；流程见上） |
| `cc.operator.page` | 通过（流程 `cc.operator` new / old × 电脑 / 手机、`cowork.operator.send-while-waiting`；代录会话里提问卡挂着打字用真模型点过一次） |
| `shared.page-errors` | 通过（Cowork、ERP 的 CC 对话页：控制台抛错进 Error Log 一条；站点拒收的请求不进）；LINE 票据页、手机盘点页没测到（没改到） |
| `shared.assistant` | 通过（两个产品打开对话页无报错，流程里的页面报错检查） |
| `control.assistant` | 没测到（fatcc.com 入口要真实登录，实验栈没有） |
| `cowork.assistant`、`cowork.cc-chat.push`、`cowork.cc.troubleshooting` | 没测到（要真模型调工具、写 Express；本次只改发送与附件选取） |
| `erp.cc.companies`、`erp.cc.chat-push`、`erp.cc.stocktake` | 没测到（同上；「允许」卡在 ERP 用 `erp_company_note_save` 点过拒绝这一条路） |
| `cc.operator.thread` | 没测到（实操任务页底部对话要执行电脑） |
| `shared.login`、`shared.team.members`、`shared.execution` | 没测到（只因 `lib/frappe.ts`、`sessions.py` 是调用方被列入；登录、团队、识别代码没改） |

## 真模型实测（主会话 11-12 要求；本机实验栈接 6QAA 评测密钥，只有对话模型费，累计 1.48 美元，上限 1.5）

脚本与结果在 `~/pearnly-devtools/composer-send/`（`real_cards.py`、`real-*.json`），截图 `shots/real*/`。真引擎用完已断开，容器里没有密钥。

| 轮 | 做法 | 现象 | 结论 |
|---|---|---|---|
| 1 | 先把卡片结果交回（各题跳过 + 「回答在下一条消息里」），再发用户消息 | 引擎收到结果就让模型先说一轮：「你还没在提问卡上选，请直接回复我：周报用表格还是列表？」，随后用户消息到了才答对 | 等于又问一遍，不行 |
| 2 | 卡片结果和用户消息同一批发 | 引擎不收这一批，用户那句话没送到（页面气泡撤回） | 不行 |
| 3 | 卡片结果里带上用户原话，再发用户消息 | 第一段答对；用户消息到达后模型又补一句（提问卡那次带字面的 `<br>`）；允许卡那次页面上第一段回复没显示（事件里有） | 重复、丢段，不行 |
| 4（现做法） | 结束等卡片的那一轮、卡片作废、再发用户消息并带说明 | 见下 | 符合 |

第 4 轮（会话 `sesn_01MWPyZQ2KkKyRxW5dWXYRGV` 直写、`op_c0rl7qvkah` 代录），三条都是页面只有一段回复、没有重复提问、没有丢段、没有 `<br>`：

1. 直写，提问卡「周报用哪种格式？（表格 / 列表）」挂着，打「都不要，用一段话写就行。」：模型没有收到卡片结果（那一轮被结束），收到用户消息 + 说明，
   回「好的，你定的周报格式是：表格和列表都不用，用一段话写。」。卡片「已取消」。
2. 直写，「允许 / 拒绝」卡（存公司备注）挂着，打「先别存，我还没想好。你只要告诉我刚才那条存进去了没有。」：执行记录 `cancelled`，
   `Cowork Company Note` 里 0 条，工具没执行；模型回「没存进去。你没有点允许，这一步已经取消了，所以……没有加这条」。
3. 代录会话，提问卡挂着打同一句：模型回「好的，周报格式定为用一段话来写，不用表格，也不用列表。」；会话 `last_stop_at` 为空（没记成用户喊停），
   代录任务状态 `preparing`、没有停止控制。实验栈没有 CC 桌面版，「任务正在电脑上录」这一状态没法造，按代码与测试核（不调 `_operator_stop`）。

## 附带：快捷回复按钮「录吧」改「写入」（用户 11-12，主会话转）

用户 11-12 看到按钮「录吧」不满，同日定「文案统一用『写入』」。改已有文案清单（改前 → 改后），键 `assistant:MESSAGES.recordNow`：

| 语言 | 改前 | 改后 | 依据 |
|---|---|---|---|
| 中文 | 录吧 | 写入 | 用户原话 |
| English | Record it | Write | 同界面「Write to … in Express」 |
| ไทย | บันทึกเลย | บันทึก | 同界面「บันทึกลง Express ของ …」 |
| 日本語 | 記帳して | 書き込む | 同界面「… の Express に書き込む」 |

- 点了按钮发给 CC 的文字跟着变成这个词。服务端放行开始只看「复述之后用户发过消息」（`write_plan.py` 按 `user_turn` 序号），不比对文字；
  页面出不出按钮只看写入计划状态。加测试钉住（`tests/test_cc_record_reply.py`）。
- `scripts/i18n/copy-exempt.tsv` 删掉这条 R-TALK 例外；`scripts/i18n/copy-approved.tsv` 登记 2026-11-12。
- 流程、压测脚本都按 `data-slot="cc-quick-replies"` 找按钮，不按文字；流程 `cowork.cc.record-reply` 重跑。代码注释与文档里指代这个按钮的「录吧」没有全改。

## 没验证到的

- 真模型只测了上面三条（一题的提问卡、存备注的允许卡）；多题卡、写账类允许卡（推送）在真模型下没测，走的是同一条路。
- 代录任务正在电脑上录、中途出提问卡时打字：实验栈没有 CC 桌面版造不出这个状态，只核了代码与测试（不发停止控制），建议上线后在测试机补一次。
- 真机（iPhone / 安卓）选文件框与相机入口：`ACCEPT` 没变，但「+」按钮改成自己开选文件框，只在无头 Chrome 手机宽度点过。
- 一时失败后仍可能留下没发过消息的会话（「选中就上传」在选文件时就建会话，既有设计，本次没动）；超限这一路不再建。
