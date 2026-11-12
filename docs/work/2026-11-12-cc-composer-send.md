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
| 提问卡 `ask_user` | 排队、永远发不出 | 当场发出，卡先交回 | 流程 `cc.send-unblocked` |
| 「允许 / 拒绝」卡 | 同上 | 当场发出，卡按拒绝交回、工具不执行 | 同上 + `AskUserTests` |
| 超限 / 空文件 / 不收的类型 | 留失败卡，挡住发送 | 选中时拒收，不进输入框 | 同上 + `test_upload_limits.py` |
| 一时传不上的附件（断网、5xx） | 整条不发、写原因 | 不变；失败卡上有文件名与原因，移除后发得出 | 流程第 5、6 步 |
| 识别挂在服务端等 | 发得出 | 不变 | 流程 `cowork.cc.send-while-waiting`、`cowork.operator.send-while-waiting` |
| 代录开始前 / 中途选 / 继续卡 | 发得出 | 不变 | 流程 `cc.operator` |
| 「录吧」等点头 | 发得出 | 不变 | 流程 `cowork.cc.record-reply` |
| CC 回复中 | 排队、可撤回、本轮完了自动发 | 不变（设计如此） | 流程 `cc.chat` |
| 代录新对话没选公司 / 月份 | 发送键禁用 | 不变（已有设计） | 没单独测 |

## 改法（主会话 11-12 审定：A、B、C3）

- **A 提问卡挂着时打字**：页面不排队（`reducer.holdsSends`：只有模型这边有活在走才排）；`sessions.send` 送消息前调 `toolrun.replied`，
  提问卡按「每题没选 + 回答在下一条用户消息里」交回（`ask.replied`），卡片显示成答过、各题跳过。
- **B 允许卡挂着时打字**：同一条路，一律按拒绝交回。`toolrun.replied` 只调 `decide(..., False, replied=True)`，没有执行工具的分支；
  测试钉住「工具没有执行」。没有卡片在等时只读一次、不提交不回滚（代录会话第一句此前有没提交的写入）。
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

改到的服务端函数：`assistant/sessions.py` 的 `send`（加一行）、`assistant/toolrun.py` 的 `decide`（加参数）与新增 `replied`、`assistant/ask.py` 新增 `replied`、
`www/assistant.py` 与 `www/assistant.html`（多写一个页面变量）。没有碰 `_segment_events`、`segment_for_turn`、写账目标相关函数。

## 验证（8b3f0fd6d 之后的工作树，本机实验栈 pearnly-csend-lab，假智能体，不调付费模型）

- 旧代码（6275e4999 的产品代码 + 新测试）：流程 `cowork.cc.send-unblocked` 电脑宽度 9 项全红；单元测试红。
- 新代码：流程 `cowork.cc.send-unblocked`、`cc.send-unblocked`（ERP）电脑 / 手机四格全过；截图 `~/pearnly-devtools/composer-send/shots/final/`。
- 回归流程全过：`cc.chat`、`cc.operator`（new / old）、`cc.attach-delete`、`cowork.cc.attach-delete`、`cowork.cc.ask`、`cowork.cc.send-while-waiting`、
  `cowork.operator.send-while-waiting`、`cowork.cc.record-reply`、`cc.machine.operator-ai`、`cc.sidebar-modes`，电脑 / 手机宽度。
  中途 `cc.operator` 红过一次：`replied` 开头的回滚丢了代录第一句开远端会话的写入，已修并加测试。
- 与 CI 同名的闸门、候选检查：见提交说明与完成消息。

### 共用件影响清单（`check-impact.py`，15 个入口）

| 入口 | 结果 |
|---|---|
| `cc.assistant` | 通过（实验栈 Cowork、ERP，电脑 / 手机：新建对话、发消息、附件、删除对话、侧栏模式；流程见上） |
| `cc.operator.page` | 通过（流程 `cc.operator` new / old × 电脑 / 手机、`cowork.operator.send-while-waiting`；提问卡 / 允许卡挂着打字只在直写会话里点过，代录会话没单独点） |
| `shared.page-errors` | 通过（Cowork、ERP 的 CC 对话页：控制台抛错进 Error Log 一条；站点拒收的请求不进）；LINE 票据页、手机盘点页没测到（没改到） |
| `shared.assistant` | 通过（两个产品打开对话页无报错，流程里的页面报错检查） |
| `control.assistant` | 没测到（fatcc.com 入口要真实登录，实验栈没有） |
| `cowork.assistant`、`cowork.cc-chat.push`、`cowork.cc.troubleshooting` | 没测到（要真模型调工具、写 Express；本次只改发送与附件选取） |
| `erp.cc.companies`、`erp.cc.chat-push`、`erp.cc.stocktake` | 没测到（同上；「允许」卡在 ERP 用 `erp_company_note_save` 点过拒绝这一条路） |
| `cc.operator.thread` | 没测到（实操任务页底部对话要执行电脑） |
| `shared.login`、`shared.team.members`、`shared.execution` | 没测到（只因 `lib/frappe.ts`、`sessions.py` 是调用方被列入；登录、团队、识别代码没改） |

## 没验证到的

- 真模型：提问卡被交回后，CC 是否把用户那句话当作回答接着做（实验栈是假智能体，只核到模型收到的结果与顺序）。建议上线后在测试租户用压测 S8a 那一格复测。
- 真机（iPhone / 安卓）选文件框与相机入口：`ACCEPT` 没变，但「+」按钮改成自己开选文件框，只在无头 Chrome 手机宽度点过。
- 一时失败后仍可能留下没发过消息的会话（「选中就上传」在选文件时就建会话，既有设计，本次没动）；超限这一路不再建。
