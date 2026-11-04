# Code 版 CC 施工块 2：会话组件（W2）

- 评估与审定：[2026-11-04-cc-code-style.md](2026-11-04-cc-code-style.md)（第 5、11、13 节）。分支 `pearnly/cc-code-run-ui`，从 `ca850183c` 开出，变基到 `origin/main` `4ffe8aea6`。
- 范围以 11-04 用户更正为准（主会话转达）：Code 左侧只有对话简要，执行中、结束、出错、停止都在对话框里，**直写现有的那套就是标准**。
  因此任务清单、结束框、侧栏「等你处理」新标记、另一套出错 / 停止显示都**不做**（第一版类型 `82e8b8a7d` 里写过，已删）。

## 交付

| 项 | 落点 | 说明 |
|---|---|---|
| 接口类型 | `frontend/assistant/src/components/run/types.ts` | `UploadState`、`UploadCardProps`、`TaskUpload*`（评估 B1/B2 形状，块 1 接口做出来有出入时在这里对齐）、`ComposerStop` |
| 上传状态卡片 | `components/run/upload-card.tsx`、`upload-state.ts` | 上传中 N%、排队中、识别中 n/m 页、已识别 N 张（· N 张不在某月）、失败。样子 = 附件卡片 `AttachmentCard` + 一行步骤行的字（在动的点同进行中的步骤，失败同步骤行尾的「失败」），上传失败的「重试」是 `.cc-card-btn`；识别失败不另做重试。同一份再传就是又一张卡片，不加「已传过」提示 |
| 停止键给代录用 | `thread.tsx` 的 `Composer` 加可选 `stop` | 代录传自己的在跑状态与停止动作，发送键换成直写同一个 ■，Esc 同效；不传时行为不变 |
| 对话最后的内容 | `ThreadComponents` 加可选 `Tail` | 画在消息组里、发送中的气泡之后，新对话也画（W3 放「需要处理」框）；不传不占位 |
| 每条消息前的内容 | `ThreadMessages` 加可选 `before(message)` | W3 按时间把上传卡片插进对话；不传时同以前 |
| 文案 | `i18n.ts` 新增 `RUN` 一块（三语） | 只有上传卡片的 5 条：排队中、识别中、识别中 n/m 页、已识别 N 张、N 张不在某月；「正在上传」「重试」「失败」沿用已有词条 |

没有改：`activity.ts`、`turn.ts`、`tool-uis/*`、侧栏标记、`globals.css`。组件这一阶段没有挂到页面上，现有界面行为不变；产品地图没有新入口（`check-product-map.py` 通过）。

## 推出来、待块 0 截图校对的样子

- 上传卡片下面那一行（字号 caption、淡色、在动的点）：Code 没有对应的上传状态显示，按直写的附件卡片与步骤行拼出来。
- 失败行「失败 · 原因」的排法：照步骤行尾的「失败」。

## 新增文案（`check-copy.py --report`）

| 来源·键 | 中文 | English | ไทย |
|---|---|---|---|
| RUN.queued | 排队中 | Queued | อยู่ในคิว |
| RUN.reading | 识别中 | Reading | กำลังอ่าน |
| RUN.readingPages | 识别中 {n}/{m} 页 | Reading {n}/{m} pages | กำลังอ่าน {n}/{m} หน้า |
| RUN.read | 已识别 {n} 张 | Read {n} document(s) | อ่านแล้ว {n} ใบ |
| RUN.outside | {n} 张不在 {月} | {n} not in {month} | {n} ใบไม่อยู่ใน {month} |

改动已有文案：无。

## 共用件连带回归（`check-impact.py`：命中 `cc-composer`）

| 入口 | 结果 |
|---|---|
| `cc.operator.page`（离线） | 通过：`tests/test_cc_operator.py`（本地全闸门） |
| `cc.assistant`（手动） | 没测到：回归实验室被主会话占用，本块不动它。改动只加可选参数，不传时 DOM 与行为不变；`tests/test_assistant_ui.py`、`tests/test_cc_run_indicator.py`（停止键与 Esc）、`tests/test_cc_run_ui.py`（`ThreadMessages` 真渲染、不传 `before` 时同以前）离线通过。W3 接上后在块 6 一起实测 |
| `shared.team.members`（手动） | 没测到：本块没改团队页，命中来自 `cc-composer` 登记表里的入口；块 6 一起实测 |

## 测试

`tests/test_cc_run_ui.py`：三语假数据渲染每种上传状态（字、在动的点、失败与重试、同一份两张卡片）、B2 一行到状态的换算、`ThreadMessages` 的 `before`、`Tail` 与 `stop` 的接法、没有第二套运行界面。`tests/test_cc_idle_motion.py` 登记上传卡片的在动的点只在上传 / 排队 / 识别时挂。
