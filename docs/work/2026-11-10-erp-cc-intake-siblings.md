# ERP 代录多票 PDF 只录第 1 张（拆出的收单记录来源落成 LINE）

A 线第 3 棒，2026-11-10。第 1 批身份真模型场景 ERP 遍（实验栈 pearnly-aline3-lab，6QAA）撞出，main 上原有缺陷。

## 现象与根因
- CC 代录里放一份 24 张销售税票的 PDF：识别成功、拆成 24 条 `ERP LINE Intake`，对话资料卡只显示「已识别 1 张」，开始后只交了第 1 张。
- `integrations.intake._sibling`（同一文件里另一张票建新记录）复制字段时漏了 `source` 与 `session_id`：拆出的 23 条落成 DocType 默认来源 `line`；
  对话资料列表 `chat_records` 只认 `source=cc`，所以只列第 1 张，另外 23 张进了 LINE 收单。
- 影响：ERP 的 CC 对话 / 代录里放多票文件（多页 PDF、一张图几张票）都只录第 1 张。LINE 收单不受影响（本来就是 line）。

## 修法（按类，主会话 11-10 要求核全字段）
- 收单记录每一栏归两类写明在 `intake.py`：`SIBLING_SHARED`（随第一条：方向、是否猜的方向、发送人、来源、对话编号、文件指纹、公司、
  对方、付款、整批入库方式、仓库、收到时间……）与 `SIBLING_OWN`（每条自己的：状态、请求键、源文件、识别结果、逐行选择、提交出的单据、
  作废原因、备注、错误）。核下来漏复制的是 `source`、`session_id`、`direction_guessed` 三栏。
- 请求键 `request_key` 只归第一条（同一附件再交回原来那条）。

## 测试
- `integrations/test_cc_intake.py::Siblings`（旧代码红：拆出的那条没有 `source`）：CC 来的拆出仍是 CC、同一段对话、不带请求键、
  方向是否猜的随第一条；LINE 来的仍是 LINE。另一条按 DocType 定义核：每一栏都归了类，新加栏没归类就红。
- 候选离线车道：`test_cc_intake`、`test_line_intake`、`express.test_operator_intake` 128 条过（生产同版镜像）。

## 不在本次
- 线上已经被拆成 LINE 来源的旧记录要不要改回：待主会话定（只读核一下线上有没有这种记录再说）。
