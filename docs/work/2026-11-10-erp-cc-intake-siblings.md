# ERP 代录多票 PDF 只录第 1 张（拆出的收单记录来源落成 LINE）

A 线第 3 棒，2026-11-10。第 1 批身份真模型场景 ERP 遍（实验栈 pearnly-aline3-lab，6QAA）撞出，main 上原有缺陷。

## 现象与根因
- CC 代录里放一份 24 张销售税票的 PDF：识别成功、拆成 24 条 `ERP LINE Intake`，对话资料卡只显示「已识别 1 张」，开始后只交了第 1 张。
- `integrations.intake._sibling`（同一文件里另一张票建新记录）复制字段时漏了 `source` 与 `session_id`：拆出的 23 条落成 DocType 默认来源 `line`；
  对话资料列表 `chat_records` 只认 `source=cc`，所以只列第 1 张，另外 23 张进了 LINE 收单。
- 影响：ERP 的 CC 对话 / 代录里放多票文件（多页 PDF、一张图几张票）都只录第 1 张。LINE 收单不受影响（本来就是 line）。

## 修法
- `_sibling` 连同 `source`、`session_id` 一起随第一条；请求键 `request_key` 不复制（只归第一条：同一附件再交回原来那条）。

## 测试
- `integrations/test_cc_intake.py::Siblings`（旧代码红：拆出的那条没有 `source`）：CC 来的拆出仍是 CC、同一段对话、不带请求键；LINE 来的仍是 LINE。
- 候选离线车道：`test_cc_intake`、`test_line_intake`、`express.test_operator_intake` 127 条过（生产同版镜像）。

## 不在本次
- 线上已经被拆成 LINE 来源的旧记录要不要改回：待主会话定（只读核一下线上有没有这种记录再说）。
