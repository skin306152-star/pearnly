# 发票修正反馈合同与本地原生例库

第三批本地后端迁移，起始新仓 SHA `392d9d917cb62c1a00d0bb43f72dc7b806082a86`。父任务负责首尾生产身份与运行配置核验；本工作包固定参考 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，快照在 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。工作树为 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`。

第三批实现 `pearnly_cowork.ocr.feedback.contracts`，仅依赖 Python 标准库，不读取数据库、环境、ContextVar、时钟或 Frappe。第四批在重新确认同一生产来源及 OCR 策略后，增加 Frappe 原生例库与队列受理快照；仍不导入真实样例、不调用付费模型。以下纯合同保持不变，原生接线与验证分列在文末。

## 接口与来源

| 新入口 | 固定来源 | 保留合同 |
| --- | --- | --- |
| `compute_field_corrections(ai_pages, corrected_pages)` | `services/ocr/feedback/diff.py:40` | 首次 AI 与当前编辑的主页字段差异，按固定字段顺序输出 |
| `primary_seller(pages)` | `services/ocr/feedback/diff.py:53` | 同一主页的卖方税号和名称，只 trim，不查主档或校验身份 |
| `tax_ids_for_text(text)` | `services/ocr/feedback/fewshot.py:33` | 按出现顺序取前 4 个不同的连续 13 位数字匹配 |
| `select_authorized_examples(text, authorized_examples_by_tax)` | `fewshot.py:36-44` 与 `store.py:78-98` | 每税号最多 20 行；依税号/行顺序按 `(field_name, ai_value)` 去重，保留首条 |
| `build_prompt_block(examples)` | `fewshot.py:50` | 最多 12 条，原中文提示、空值标记、换行和输出值完全保留 |
| `prompt_for_text(text, authorized_examples_by_tax, enabled=...)` | `fewshot.py:21` 的纯组合部分 | 显式布尔开关；关闭、空文本、无匹配或样例处理异常返回空串 |

`authorized_examples_by_tax` 必须由调用方先按服务器确定的操作者、租户及权限范围取得。映射的每个值为已经按 `use_count DESC, updated_at DESC` 排序的样例序列；本模块不重新排序、不证明授权，也不接受用户传来的样例作为可信资料。SQL 同分同时间的次序在旧版没有额外排序键，本纯合同只保留传入次序。

### 修正计算

主页是首个 `is_copy` 和 `is_duplicate` 都不为真的条目；若所有条目都是副本/重复项，退回第一条。找到的主页即使字段为空也不寻找下一条。AI 与编辑结果各自按此规则选择主页。

学习字段严格保留 `invoice_number/invoice_date/date/seller_name/seller_tax/buyer_name/buyer_tax/total_amount/subtotal/vat/wht/category`。不新增 `discount/wht_amount/items` 或内部字段。值按原版 `None → ""`、其他值 `str(value).strip()` 比较；编辑后为空的清除动作不学习，数值零则成为字符串 `"0"`。没有金额运算；`"1,000.00"` 改成 `"1000.00"` 仍是一条文字修正，不合并成同金额。

### 税号、去重和提示块

税号沿用 Python 正则 `\d{13}`，包括 Unicode 数字；没有校验位或数字边界检查。26 位连续数字可能匹配成两段；带分隔符的号码不会自动去分隔符。这里只决定修正例查找候选，不能拿来证明合法税号、用户身份或访问权限。

去重键不 trim、不归一金额、不包含 corrected_value 或 tax ID，因此同字段同 AI 原值在多个税号间只留先出现的一条；`None`、空串和带空格的值保持区别。每组先限 20 行再去重，提示块再限 12 条，不进行全局 use_count 排序。

提示文案属于模型输入合同，不是产品 UI 翻译。样例里的换行、引号、文字和数字保持原样，不能在本次机械迁移中静默改写或另加模板。所有样例内容应按业务敏感数据处理，不写进公开日志或运行配置证据。

## 有意边界与待接线

- 新函数接收显式、已解析的布尔开关；字符串 `"false"`、数字 `0/1` 不可冒充该参数。旧环境字符串的解析仍归运行策略层。
- 去重结果深拷贝样例，后续改动不会污染调用方保存的首次证据；不改变提示块内容和次序。
- 纯组合函数保留旧版失败时不注入提示的行为，但不记录日志。数据查询异常、授权拒绝和内部诊断由未来适配层处理，不能把“无样例”记录成“已完成真实样例迁移”。
- 旧读取依赖 `tenant_or_user` RLS，查询只按 seller_tax 过滤；是否实际强制 RLS 取决于连接角色等运行状态。新调用方必须明确执行租户和当前权限限制，不能复制一条只带税号的宽查询，也不能把旧潜在权限绕过迁入新平台。
- 第三批交付时尚未实现原生例库与保存接线；第四批的进展见文末。真实修正例导入仍未进行，纯合同本身不能单独交付“反馈闭环”。
- 后续保存链应先用编辑后的主页卖方；缺税号时按旧 `store.record_correction_from_edit` 回落到首次 AI 主页卖方。原 `ai_raw` 必须保持不可变，不用前一轮人工编辑替代首存基线。
- 旧注入点是 `layer2_gemini.py:242` 普通 invoice L2 且 `system_prompt_override is None`。电子 PDF 文本路径和 Vision 回落 L2 都可使用；成功 image-direct 不注入。`page_runner.py:95-100` 已用 `submit_ctx` 传播多页请求上下文，不能依 `feedback/context.py` 过时注释遗漏多页。
- 未来队列需固化本次选中的授权样例/提示输入及其受限审计依据，重试不得因例库继续编辑而静默换提示重做付费识别。此处只接受数据，不创建检查点或提供访问权限。

本模块不证明旧线上例库为空、例库已迁入、新旧提示输入已完全一致或真实识别结果相同。缺真实样例迁移及运行链验证时保留这些缺口。

## 差分与验证

`tests/test_cowork_invoice_feedback.py` 的普通 unittest 路径不需要旧仓。带 `--source` 或 `--source-stdin` 时，先校验以下源码 SHA-256，任何差异立即拒绝，再执行已验证的纯 AST；不 import 旧应用。`store.py` 只解析 `fetch_examples` 的默认 limit 常量，不执行数据库代码。旧 `maybe_block_for_text` 仅去掉本地服务 import，使用显式合成 context/store 替身，其税号、去重、异常及拼接算法原样执行。

| 来源文件 | SHA-256 |
| --- | --- |
| `services/ocr/feedback/diff.py` | `51c6f00096610e47f3a4564e7c81b0bbc29ab6a9d9cd6033f04162a9406b98ff` |
| `services/ocr/feedback/fewshot.py` | `32008315e86ad6b14739c22b459501a41dd6c2139ee4fcf37ac302018d03a7d5` |
| `services/ocr/feedback/store.py` | `9077fede0f1d5f9d15fd280f73309d5bce9648b9bc8e216aeca0a3ccfd6749c5` |

2026-09-21：14 项合成测试通过；3,000 组编辑差分、6,000 次卖方选择、3,000 组提示块、3,000 组关闭提示与固定源完全一致。覆盖全部复制页、空主页、字段清除/真实零、文字金额、原始 key 去重、跨税号冲突、20/12 限额、Unicode/长数字、禁用/畸形输入、输入不变和篡改源码拒绝。

在本工作树使用本机 Python 3.14，未初始化 Site、未写数据库、未访问云端或真实样例：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=apps/pearnly_cowork /opt/homebrew/bin/python3.14 tests/test_cowork_invoice_feedback.py --source /tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b
python3 scripts/check-architecture.py
git diff --check
```

第三批架构检查 PASS（0 违规），`git diff --check` 通过；父任务已将第三批纳入后续开发基线。提交、合回主线、推送和部署状态由主任务总记录负责，不能由这里的纯合同测试推出。

## 第四批原生例库

第四批起始新仓 SHA `ffdcd477632c8cb435d54ab855c721d4b67779cc`，父任务再次核验生产来源仍为上述固定 SHA。新增域文件 `ocr/feedback/model.py`、`access.py`、`service.py`，原生 DocType 位于 `pearnly_cowork/doctype/cowork_ocr_correction/`；这些是 App 内路径，不复制旧部署或旧 PostgreSQL RLS 结构。

`Cowork OCR Correction` 每行代表一次已保存历史版本的一个字段修正。事件唯一键绑定 Site、公司、客户、历史名称、历史版本和字段。同一版本重试保留原事件及原操作人，不重复累加；同版本不同内容拒绝并留下脱敏诊断。不同保存版本的相同卖方税号、字段及首次 AI 原值共用样例组，读取时统计事件数，选择最近的修正值。每个税号再按次数、修改时间、事件名称排序取前 20 条。最后一项是旧 SQL 同分同时间没有规定时的稳定次序。

保存入口是内部 `capture_saved_edit(doc)`，由 `history.storage.update_pages` 在 `doc.save()` 成功后、返回及事务提交前调用。此前保存文档只用于判定本次页数据是否改变；首次 AI 和当前版本从加锁的已保存 History 重读，并核验版本、页内容、首次 AI 和当前操作人。完成状态修改、未改变页数据、手工记录无首次 AI 时不学习；实际修改仍相对首次 AI 计算，不能用上轮人工编辑替代它。旧版每次请求可能累加的行为，在这里明确收紧为已保存版本幂等。

捕获自有唯一保存点，不隐式提交、也不主动回滚整个调用方事务。普通例库异常只回滚本次新增例库，票据保存继续；外层事务失败则票据和例库一起回滚。MariaDB `QueryDeadlockError` 可能已撤销整笔事务，因此继续向请求边界抛出，由父任务的保存 RPC 完整重试，不能吞掉后向用户声称保存成功。保存点回滚本身失败同样向上抛出。诊断只写服务端 logger，含状态、历史编号、版本和异常类名，排除异常文字、堆栈、SQL、票据及修正值；不创建 Error Log/Version/Comment 内容。

### 授权、原生入口和快照

- Site 来自服务器，actor 来自当前 session；必须有已购买 `intake`、对应角色/启用账号及即时公司、客户授权。客户范围精确匹配，不回落到同公司其他客户或只按税号跨范围查询。管理员角色也必须满足购买功能。历史已删除或归属不匹配的事件不会参与查询。
- 例库不开放产品原生 CRUD。Frappe 16.33 的 `database/query.py:1575-1582` 在没有角色读取权限时直接走旧分享，跳过 query hook。因此元数据保留 `All/Guest` 的基础 read 仅保证进入随后永远拒绝的列表 hook，并不构成可用读取授权；详情权限 hook 同样直接抛拒绝，DocShare 的 `before_validate` 和 `validate` 拒绝新分享。Controller 只接受 Python 真对象 create capability，拒绝通用更新、删除、重命名、`ignore_validate` 和 JSON 伪造 flags；native `check_permission` 也拒绝 Administrator 原生详情短路。运维数据库及服务器代码权限仍属于受信边界，不声称能阻止运维直接 SQL。
- `track_changes/track_seen/track_views=0`，内部插入关闭 `notify_update` 和 version，避免原生全站 list_update 广播例库名称与操作人。内容不进入全局搜索；没有新增前端入口。
- `snapshot_for_scope(workspace, client=None)` 返回 `{body, sha256}`，在受理时冻结当前精确范围的候选例、次数、事件/历史依据。先查询名称和字节数，超过 2,000 条、每税号 20 条或完整 UTF-8 JSON 1 MiB 即拒绝受理；不悄悄截断税号，也不把几十万例塞进工作参数。快照由受保护的 `Cowork OCR Batch` 存储，Core work arguments 只带批次名称。
- worker 从受保护 Batch 恢复原 actor 后调用 `validate_snapshot(snapshot)`，重新核验 Site、actor、购买功能、角色、账号、公司/客户授权、持久事件内容和来源历史归属，再使用返回 body 的 `examples_by_tax`。快照建立后新增或修改票据不会替换已接受的例、次数和提示输入；撤权、来源丢失或内容不符则拒绝执行。
- SHA-256 是完整性检查，不是客户端授权凭据。快照 API 均不 whitelist，调用方只能读取受保护且不可变的 Batch 字段，不能接受外部提交的快照作为信任来源。

父任务负责 `hooks.py` 的 Correction 权限与 DocShare 接线、History 保存 hook、Batch/worker 接线和统一 Site 迁移。本工作包不修改 Core、不启动或迁移环境，不搬真实生产例库。旧版依赖租户 RLS 的例库范围与新平台显式公司/客户范围存在有意收紧，真实迁入前必须确定旧例到新公司及客户的映射，不能把无法证明归属的历史例直接全租户开放。

### 第四批验证状态

2026-09-21 本机 Python 3.14：`tests/test_cowork_feedback_store.py` 13 项通过；覆盖同版本事件身份、跨 Site/公司/客户不碰撞、首次卖方回落、相同金额的文字差异、清除与零、非法版本/非发票、快照篡改/结构/数量/字节限制、深拷贝及空例库。新增文件语法检查和架构检查通过。

`scripts/cowork-feedback-lab-verify.py` 有 16 项合成真实 Site 场景，使用既有专用 lab marker、Site 白名单和本地数据库校验；首次建立带标识的合成用户/公司后逐例回滚。覆盖真实保存 hook、重复版本、窗口 SQL 聚合、范围和即时撤权、冻结快照、缺失/归属变更、部分失败与外层事务回滚、deadlock 传播、原生 CRUD/flags/everyone 与 Guest 分享及不产生日志内容。首次现场执行暴露了无角色权限的旧分享列表绕过（已按上述方式修复），以及 History 的 `client=None` 被 Frappe 用户默认客户替换（父任务已修复）；测试夹具同时修正为真实 OCR 的已规范化首次日期，保留原断言。父任务重新迁移后统一运行，`/tmp/cowork-feedback-tests.log` 显示 16 项全部通过（2026-09-21，1.927 秒）；这是专用本地 Site 的合成场景证据，不是生产例库或用户验收。

共享 Core 通知的一行兼容另由 `tests/test_cowork_core_notifications.py` 保留 8 项回归：源 AST 原样执行，所有 Site/DB/Redis/LINE 依赖替身；无 hook 的 `[]/{}/None`、现有 DMS handler 路径及参数、错误隔离、failed/uncertain 仍释放两级名额均通过。`check-dms-offline.py` 的 207 条离线断言通过；不发生外部建单或消息发送。

本批新增批次/纠错字段和公开服务端消息已补 Cowork 英、中、泰、日术语表及生成 CSV。翻译完成不代表用户语言验收，也不代表新前端已接入。这些检查不证明真实例库已迁入、提示内容与生产历史数据完全相同、付费识别结果相同或生产已部署。

语言定向检查：新增 56 条术语，execution/feedback 的公开消息及 Batch/Correction 字段合计 70 项四语完整，术语表校验通过。全仓 `check-i18n.py --missing-all --strict` 仍报告每语 22 项 Core/DMS/control 既有缺项，本包没有修改其他 App 翻译，也不把该全仓检查记为通过。
