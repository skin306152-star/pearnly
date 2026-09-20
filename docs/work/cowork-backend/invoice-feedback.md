# 发票修正反馈纯合同

第三批本地后端迁移，起始新仓 SHA `392d9d917cb62c1a00d0bb43f72dc7b806082a86`。父任务负责首尾生产身份与运行配置核验；本工作包固定参考 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，快照在 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。工作树为 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`。

本包只实现 `pearnly_cowork.ocr.feedback.contracts`，仅依赖 Python 标准库。不读取数据库、环境、ContextVar、时钟或 Frappe；不调用模型、保存修正例或导入真实样例。测试全部使用合成输入。

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
- 原生 DocType、幂等样例写入、人工编辑保存 hook、首次 AI 基线读取、真实修正例导入及 OCR 队列接线均未实现。本模块不能单独交付“反馈闭环”。
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

本次架构检查 PASS（0 违规），`git diff --check` 通过。父任务另行记录整批检查及交付前生产复核；本包尚未提交、合回主线、推送或部署。
