# 代录开始前卡片：排在第一句之后、不带未来时刻、最多先列 5 行（11-09 发布前阻塞）

分支 `pearnly/cc-start-card-short`（基线 ef47c3b92）。由 CC 线窗口负责，主会话 11-09 认可修法。只修事故，不扩展卡片。
复核方案（设计窗口 e8cb6af00）批下来后，开始前卡片会另行改成复核卡。

## 现象

发布实验栈上，ff7db152b 的 new 数据集跑 cc.operator「选公司说一句」不通过：
- 电脑宽度：收不到回复。
- 手机宽度：用户气泡为空。

那套数据积了 45 张上个月的待录单。新起的实验栈只有 1 张，所以流程能过。
在本窗口实验栈补到 45 张后，ff7db152b 和 ef47c3b92 都能复现。

## 根因

1. **卡片锚得太早、列得太长。** 开始前卡片锚在建任务那一刻，排在用户第一句之前，而且逐张列出 45 行，高约 2300px。
   这样用户的话和 CC 的回复都在视口外。消息用了 content-visibility，屏幕外的气泡读不出字。
   这就是 11-08 SincereIce 事故的同一类问题。
2. **不能用未来时刻垫后。** 把锚点改成「建任务 + 2 秒」会让卡片和「等你点开始」带着未来时刻先推到页面。
   页面按服务端时间取舍会话状态，后到的引擎「本轮结束」时刻更早，会被丢掉，状态行就停在「正在执行」。
   规矩：代录事件源不出未来时刻。

## 改动

- **记下第一句的时刻。** 任务表 `Pearnly Express Operator Task` 新增 `message_at`（Datetime，只读，标签 Sent On）。
  `sessions.send` 发出成功后调 `tasks.message_sent` 记下，只记第一句。
- **卡片锚在第一句之后。** `feed.project` 把资料和开始前卡片锚在「建任务、第一句、最后一份上传」中最晚的时刻之后。
  第一句发出前不出这两张卡。整段重放（`sessions._operator_events`）时，再垫到引擎记下的第一句之后。
- **旧任务补齐时刻。** 补丁 `pearnly_core.patches.v0_0_12_operator_task_message_at` 把已有任务的 `message_at` 补成建任务时刻，原来的排法不变。补丁是幂等的。
- **卡片最多先列 5 行。** 开始前卡片里逐张的行（结果不明、要人处理）最多先列 5 行，其余用原有「显示更多」展开，没有新文案。

## 部署

**部署要迁移，迁移前后核 DocType。**
- 这次带一个 DocType 字段和一个补丁，`activate-product.sh` 的 migrate 会跑。
- 迁移前后各数一次 DocType，数量不能变少。
- 迁移后 `message_at` 不应有空值：`SELECT count(*) FROM tabPearnly Express Operator Task WHERE message_at IS NULL` 应为 0，迁移后新建、还没发出第一句的任务除外。

## 验证

- 离线：
  - `tests/test_express_operator_feed.py` 新增断言：旧代码红，修复后 19 项过。
  - `tests/test_express_operator_tasks.py` 17 项过。
  - `tests/test_cc_operator.py` 10 项过。
  - `tests/test_assistant.py` 过。
  - `tests/test_assistant_ui.py` 253 项过。
- 流程 cc.operator：准备阶段标 8 张待录单，新增断言「用户的话在卡片之前、卡片最多 5 行、有显示更多」。
  - main（ef47c3b92 之前的代码）在 45 张数据上红：卡片在前，列 45 行，看不到回复。
  - 修复后 new / old × 电脑 / 手机 4/4 过，new 数据集 45 张。
- 代码提交 0e3e31524 上的检查：
  - 全闸门 211 项过。
  - 候选检查（生产镜像，offline）106 组过。
  - 受影响流程在本窗口实验栈（new 数据集 45 张）上：
    - `--changed`：cc.operator、cc.operator.replay、cc.chat、cc.sidebar-modes、cowork.cc.sidebar-modes、cowork.operator.unknown，18/18 格过。
    - 补跑：cowork.pending.operator、cowork.operator.send-while-waiting、cowork.cc.ask、cowork.cc.send-while-waiting、cc.attach-delete、cowork.cc.attach-delete、cc / cowork.machine.operator-ai、cc.companies，17/17 格过。
- 全量流程由发布窗口在发布实验栈上用同一个 SHA 重跑（那边积着 45 张单）。
