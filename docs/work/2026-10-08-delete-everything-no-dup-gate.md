# 删除就删干净、写入不查重（2026-10-08）

分支 `pearnly/folder-rule-bulk-delete`，基线 `65bfa717`（= origin/main）。用户 2026-10-08：

- 「规则列表勾选后右上角没有按钮：加删除就好了，其它不变。」
- 「那个已录入的列表也可以删」→「删就删除所有记录和储存吧，不要给服务器带来负担，本来我们产品也只是一个辅助工具而已，
  不要设计得这么复杂」。
- 「那个查重复，我都不知道说了几遍了，为什么要拦截啊，exp 别人会拦截手工录入时会检查重复吗？」

文案（用户已批）：录入工作台删除确认「确定要删除选中的 {0} 条记录吗？」、部分删除「已删除 {0} 条，{1} 条正在推送，未删除」、
一条没删「未删除任何记录：选中的 {0} 条记录正在推送。」（泰 / 日 / 英同义）。规则列表的删除确认用框架官方列表批量删除那句
「永久删除 {0} 项？」，删完只放 delete 音、不弹提示（同官方 `list_view.js`），没有新增词条。

## 改动

| 项 | 做法 | 位置 |
|---|---|---|
| 规则列表批量删除 | 勾选后右上角「删除」；删规则连同运行记录、文件进度，网页直传的暂存文件提交后清；没有连接使用权的不删，已不存在的跳过 | `intake-folders.js`；`folder_intake/api.delete` → `rules.delete` |
| 规则删了，后台推进不中断 | 推进逐份读文件进度时，已删掉的跳过（原来会让这一轮整个停下） | `folder_intake/pump._each` |
| 录入记录删干净 | 已推送的也删：推送记录、尝试、通知、纠错样本一起删；不在 Deleted Document 留备份。只有推送还没出结论的（准备好 / 排队 / 小助手在写 / 结果不明 / 占着写入锁）不删 | `history/removal.py` |
| 原件一起删 | 没有别的票再用、也没有识别在跑的原件：原件记录、识别批次、服务器文件（同内容别的文件记录还指着就留）、原件桶里的原件与预览（所有版本，后台任务） | `files/service.purge` / `purge_objects`；`files/cloud.delete_prefix` |
| 自动录入结果表格 | 票被删了记 `deleted`，与成功一样算有结论，不再等它、不再出它的表格 | `folder_intake/contracts.DELETED`、`pump._signature`、`workbook.status` |
| 小助手写入不查重（1.3.20） | 删掉写入前两道闸：同发票号 + 同对方「内容一样就跳过 / 不一样要确认」、「上一版还在要确认」。账套里已有同号单照写，与会计手录一样。只防同一个推送任务在回执丢失后被重发：本机 `%APPDATA%\Pearnly\written_tasks.json` 按任务号记最近 1000 个，重发照上次回执回报、不写第二张 | 删 `duplicate_gate.py` / `write_gates.py`；`dbf_writer` / `dbf_sales`；新 `task_journal.py`；`worker._handle_dbf_task` |

不变：扣费流水在控制面账本，删票不改余额；服务器识别时的「疑似重复」只是记录里的提示，不拦推送，没动；
新供应商 / 客户「名字像但税号不同」的转人工没动（主档，不是单据查重）。
服务器上「仍然推送」按钮与 `confirm_duplicate` 只对还没升级的旧小助手有用，新小助手不会再报这两种退回，没删。

## 验证

- 本地闸门（development.yml 同名步骤）0 失败；`check-architecture`、`i18n-build --check` 通过，`check-i18n --strict` 与 main 相同。
- 新测试 `tests/test_cowork_delete_everything.py`（假表，不连库）：已推送记录连同推送记录 / 尝试 / 通知 / 纠错删掉、
  正在推送与结果不明 / 占锁的留下、原件只在没人用且识别结束时删、同内容磁盘文件留着、桶前缀只认单份原件且删所有版本、
  规则连同运行记录与文件进度删掉、暂存文件提交后清。
- Cowork 全量 pytest 与 main 失败集合相同。小助手测试 591 通过（删了两份只测查重闸的测试，同号再推改为断言照写第二张；
  新增 `test_task_journal.py`）。
- 真实 Frappe（测试机生产镜像，临时容器挂本分支代码，只用测试机本地站点 `cowork.test.localhost`，测试行用完即删）：
  同一原件两张已推送票 + 一张推送中的票。先删一张已推送 + 推送中：删 1 跳 1，推送记录 / 尝试 / 通知没了，原件、批次、
  文件记录、磁盘文件都还在（另一张还用着）；再删另一张：原件、批次、文件记录全没了，磁盘文件提交后删掉；
  Deleted Document 里 0 份备份。
- 测试机生产镜像（testenv 的 PEARNLY_IMAGE）挂本分支提交快照：`ci-candidate-checks.sh offline` 退出 0（改了 pearnly_core 词条）；
  `delete_everything` 7、`folder_intake` 21、`express_choices` 10、`express_preflight`、`push_native_contract`、`push_purity`、
  `mrerp_push`、`push_lifecycle` 全过。
- 未做：原件桶里的删除只测了假接口（测试机没配桶，不去动生产桶）；真 Express 账套里同号重推（9-30 生产已有同号照写的实例）。
