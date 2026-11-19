# 打字说要收款 → 只打开收款卡（11-19）

用户 11-16 定「按建议做」（前后对比见共享笔记「收款-打字改走引导-前后对比-11-16.md」）。做法照 A1 交接第二节。
**单独一个提交，可整条撤回**：撤回后恢复成打字建草稿 → 写入计划 → 放行；卡片照样从输入框上方的「收款」打开。

## 改了什么
| 处 | 改动 |
|---|---|
| `Pearnly Assistant Guide Run` | 加字段 `hint`（Data，只读），原生迁移 |
| `assistant/guide/run.py` | `start(…, hint="")` 存前 140 字（空白合并）；出卡时只给此刻第一道带搜索的单选题用 hint 搜选项、回 `query`（搜索框初值）；用户答过任一道带搜索的单选题后不再预填；不当答案 |
| `collection/cc_tools.py` | 新工具 `erp_collection_guide`（入参只有 `hint`，开收款引导，回 opened 与给模型的一句：不问、不复述数值、不录）；`erp_collection_draft / voucher / push` 从 TOOLS / STEP_NAMES 拿掉（函数保留）；查询类工具不动 |
| `collection/cc_guide.py` | 声明的 `tool` 改成 `erp_collection_guide` |
| `express/cc_tools.py` | 写入计划里遇到 R: 收款单的提示改成「只在收款卡上录」 |
| 术语表（ERP） | 新增 2 句：Opened the collection card / Opening the collection card，四语 |
| 产品地图 / 共用件 / 验收登记 | 三个旧工具行换成 `erp.cc.tool.erp_collection_guide`；验收场景 `acc.erp.cc.collection(.nbsp)` 改成开卡 → 卡上选填 → 写入 → 读回（`cc_driver` 加 guide / guide_answer / guide_search / guide_confirm，引导卡点确定计入「用户点头」） |
| `collection/service.customers`（11-20） | 搜索字对不上任何客户时按发票号认：哪几个词（最多 5 个）是这家公司未收清发票的单号（快照，精确对、不分大小写），回这些发票的客户（只认账本里在用的；服务端分页）。网页卡、LINE 选客户、CC 查客户共用这一个函数 |
| `erp_collection_guide` 说明（11-20） | 改成写它做什么、不写命令句；hint 只传一样：说到客户就传客户，没说客户才传发票号；开卡时同 LINE 选公司预热一次未收发票（`service.warm`，没读过的账套才整本读） |
| 共用提示词 `assistant/definition.py`（11-20） | 工具调用前后可见的文字里不写给自己的计划、规则提醒和对工具说明的复述（真模型第 1 段两次里一次出现「Open collection card with hint; never pass amounts…」：复述了旧说明里的命令句） |
| 流程 | `erp.cc.typed-collection`（假智能体 `[lab-tool]` 调开卡工具：出卡、搜索初值、什么都没替答、库里不建草稿） |

## 其他收款入口不受影响（主会话 ②）
网页、LINE、MR.ERP 账套的收款都直接调 `collection.service`，不经过 `collection.cc_tools`。`test_erp_collection_cc.OtherEntriesDoNotUseTheCcTools`
断言除 CC 工具模块、收款引导（只借 `note_withdraw` 的问句拼法）与 hooks 外没有代码引用它；行为由 test_erp_collection、test_erp_collection_line、
test_erp_admin_collection、test_erp_collection_guide 守住（全闸门通过）。

## 顺序
- 前端读 `card.questions[].query` 预填搜索框（A2 local_1f7ac454），前端先进 main 或同一列车排在前面，不出现服务端发了 query、页面不显示的空档。
- 本分支在 wire 进 main 后从 main 重开、压成一个提交；真模型验（三种说法只开卡、查询照旧直接答，上限 1 美元）在交队列前做。
- 验收场景只改了脚本，由验收窗口 local_5097f6f5 在测试机真跑并登记证据。

## 验证（本地）
- 先红后绿：test_cc_guide_run 的 HintTests 3 条在旧 run.py 上失败、新代码过；test_erp_collection_cc 41 条、test_acceptance_erp_chain 20 条过。
- 11-20 第一轮真模型（28d05132f）：四段过三段；「客户 <码> 付了 <发票号>」不过（模型把发票号或「码 + 发票号」当 hint，客户题为空）；第 1 段一次回复开头多出英文自述。修法见上表 11-20 三行。
- 11-20 修后单测：test_erp_collection（按发票号认客户 1 条新）、test_erp_collection_cc、test_assistant（提示词断言）、test_cc_guide_run、test_erp_collection_line、test_erp_collection_guide、test_acceptance_erp_chain 全过。
- 11-20 修后真模型（本机实验栈 pearnly-cw3real-lab，real 模式，6QAA 测试密钥只进栈，跑完 down -v、卷与密钥已删；共约 0.48 美元）：
  | 段 | 说法 | 结果 |
  |---|---|---|
  | 1 | 帮我收 <客户名> 600 | 通过：只开卡，hint = 客户名，客户题只剩这位；无金额 / 发票号复述，无英文步骤句 |
  | 2 | 录一笔收款 | 通过：开卡，hint 为空 |
  | 3 | 客户 <码> 付了 <发票号> | 通过：hint 只传客户码（不再带发票号），客户题选中这位 |
  | 4 | <客户名> 还欠多少？ | 通过：没开卡，用查询工具直接答（两张、合计 1,500.00） |
  | 1b | 第 1 段复跑 | 通过，无英文步骤句 |
  | 5 | <发票号> 这张发票收到钱了 | 通过：hint = 发票号，客户题按未收发票认出这张发票的客户 |
  五段都没建 ERP Collection。英文自述两次第 1 段都没再出现（概率性问题，只能说本轮没复现）。

## 共用件连带回归（check-impact，基线 origin/main 6b641f42f）
命中 `assistant`（13 个入口）：本次对共用层只改了 `assistant/definition.py` 共用提示词一句（不写给自己的计划 / 规则提醒 / 工具说明复述）与 `assistant/guide/run.py`（hint 预填，28d05132f 已有）。
- 通过：erp.cc.collection、erp.collection.server、erp.cc.chat-push、erp.cc.stocktake、cc.operator.start-card（离线测试全闸门 284 步通过）；erp.cc.collection 另有真模型 6 段（见上）。
- 通过（自动流程）：erp.page.collections、erp.cc.typed-collection、erp.cc.guide.collection 由推送后的 GitHub Flows 跑，结果见队列交接。
- 没测到（原因：只多一句提示词文字，没有代码路径变化；真模型只在 ERP 收款对话里跑过）：control.assistant、shared.assistant、cowork.assistant、cc.assistant、cc.operator.thread、erp.cc.companies、cowork.cc-chat.push、cowork.cc.troubleshooting、erp.cc.troubleshooting。真机验收交 local_5097f6f5。
