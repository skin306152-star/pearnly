# 代录对话的附件「选中就上传」（2026-11-06，主会话 local_3d36d798 派）

## 现象（11-06 20:00 测试机实测，截图在共享笔记 `CC1.12.15-三条修复-11-06/代录发送实测-operator/`）
代录会话里「+」放图片和 PDF、打字、发送：两份都交了识别（intake 200、识别完成），但
- 消息气泡里没有附件，只有文字；
- 上传卡片排在这条消息上方，页面滚到底看不到；
- CC（代录的 AI）不知道这些文件（消息里不带），回「只找到一张待录单据」；
- 只放附件不打字点发送：根本不发消息（`if (text) sendMessage`）。

根因：代录模式用的是另一套附件（`heldAttachments`：文件留在本页，发送时逐份调 `express_operator.api.intake`，消息里不带附件）。

## 改法（主会话定：代录与直写同一套附件组件和上传路径，不做「发一条带文件名的消息」那种小改）
- 前端 `runtime-provider.tsx`：代录不再有自己的附件适配器和发送分支，与直写同用 `createAttachmentAdapter` 和同一条发送
  （`filesToSend` → `sendMessage(text, files)`）：选中 / 拖进 / 粘贴就传进会话，发出后附件在这条消息里、CC 读得到，只放附件也发得出。
- 服务端 `assistant/files.upload`：代录会话（`operator_task` 不为空）传进会话的同时交给会话挂的任务识别
  （`express_operator.uploads.take`，请求键 = 引擎文件编号），回包多带 `task` / `upload`；产品不收上传时照常只进对话；
  原件存放处一时连不上回 `upload.deferred`（同 B），不回 500。
- 输入框那一项：上传中转圈（原有），传完接着显示排队中 / 识别中 n/m 页 / 已识别 N 张 / 失败
  （`attachment-recognition.ts` 现读 `task_uploads` 只读这一份，3 秒一次，有结论或离开输入框就停；字沿用对话上传卡片的 `run.*`）。
- 上传卡片的位置：`take` 记的那一行先不记上传时刻（还没进对话，事件源不出卡片）；带着它的消息发出后 `sessions.send` 调
  `uploads.sent`（按消息里带的文件编号对上）记上此刻，卡片排在这条消息后面。

## 影响评估要点
- 同类复用：直写的附件组件、上传口、发送路径原样复用；识别仍走产品原来的 `intake`（扣费照常，不查重不拦）。
- 上下游：任务上传子表不改结构（`uploaded_at` 本来可空）；事件源只跳过没有时刻的行；旧的 `api.intake(task=…)` 入口保留（别的调用方照旧）。
- 隔离权限：`take` 先核本人的任务 + 产品权限 + 同公司（同 `intake`），挡下的不交识别。
- 入口：CC 网页 / CC 桌面版 / 手机网页同一份前端。
- 文案：无新增、无改动（沿用 `run.queued` / `run.reading` / `run.readingPages` / `run.read`、`steps.failed`）。
- 限制：输入框里放了又移除的文件已交识别（选中即识别，同直写选中即进会话）；它不进对话、不出卡片，但识别结果在这家公司的资料里。

## 验证
- 测试（还原修复即失败，逐条核过）：`tests/test_assistant.py`（代录会话上传口交任务、直写不交、任务不收照常只进对话、发出后才记）、
  `tests/test_express_operator_code_cc.py` ConversationUploadTests（选中即识别、发出前事件源无卡片、发出后排在消息后、Deferred / 权限）、
  `tests/test_cc_operator.py`（代录用直写附件与发送、输入框识别状态读到有结论就停）。
- 本地全闸门、生产镜像候选离线检查：见交付说明。
- 流程用例 cc.operator / cc.chat：本机已有三套别的窗口的实验栈在跑（上限三套），未跑。
- 测试机 CC 窗口实测（图片、PDF、拖拽、粘贴、只发附件）：见共享笔记 `代录选中就上传-11-06/`。
