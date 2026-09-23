# Cowork 后端迁移：对账与盘点纯契约

日期：2026-09-20。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，初始基线 `1d7832e6c5c767b310542b9022750992ec9957b0`。

本工作包只迁入无框架、无 IO 的业务契约和确定等价的计算。没有新增 RPC、DocType、数据库迁移、队列、前端、语言包或部署，不能据此宣称对账、盘点功能已可用。

## 来源与边界

父任务现场核对旧 `pearnly-web`、`pearnly-worker` 各 100% 流量 revision 为 `*ec2e5c957e6b-s3`，镜像 digest 为 `178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`，GitHub run `35321281376` 对应完整源码 SHA `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。本子任务未独立查询线上，使用父任务固定的只读源码：`/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。交付前的线上再核对由父任务负责。

- 所属 App：`pearnly_cowork`；域：`reconciliation`、`stocktake`。
- 数据归属：将来的 Cowork 当前 Site 内、已授权公司范围；纯函数不能确立授权或数据库隔离。
- 依赖：各域计算只依赖标准库和本域纯契约。没有 Frappe、HTTP、数据库、文件读取、OCR、会计或工单依赖。
- 不新增产品 Web、LINE 或原生 API 入口；当前函数是持久化/运输适配层将来调用的内部入口。
- 不修改旧 Postgres 表，不直接连接旧数据库，不为盘点建立第二套库存账。盘点保持导入账面快照与实际数量比较；关闭任务不等于调整库存。

## 实际迁入

| 固定来源 | 新落点与内部接口 | 保持的规则 |
| --- | --- | --- |
| `services/stocktake/excel.py:quantity` | `stocktake/contracts.py:quantity` | Decimal、最多 6 位小数、绝对值严格小于 `100000000000000`；账面数量可负，实盘数量不可负；不把非法数变成 0 |
| `services/stocktake/excel.py:parse` 中解码后的逐行校验 | `stocktake/contracts.py:ImportCell`、`validate_import_rows` | 原有 6 种表头布局；编码必须为文本；前导零、QR 大小写保留；拒绝公式、重复商品、跨商品编码/条码歧义；原始行数上限含空行 |
| `services/stocktake/entries.py:write` 的请求标准化 | `stocktake/contracts.py:entry_request` | 库位修剪、仓库必填、数量固定 6 位文本；版本、操作人、作废标志和实际图片摘要参与幂等请求比较 |
| `services/stocktake/entries.py:_total` 的求和规则 | `stocktake/calculations.py:active_quantity` | 只合计未作废明细；没有有效明细为 `None`，明确盘到 0 为 `Decimal(0)`；合计仍检查数量范围 |
| `services/stocktake/reports.py:_comparison/workbook` 的比较与差异计算 | `stocktake/calculations.py:compare_location`、`summarize_item` | 数量差异为实盘减账面；仓库/库位只看本商品有效明细；不修改账面快照；报告继续使用同一锁定快照中的已存 `actual_qty`，不静默重算 |
| `services/recon/field_comparator.py` 的非金额函数 | `reconciliation/normalization.py` | 保留单号前缀与前导零，仅去视觉分隔符；泰文总部别名；税号、Mod-11；两位佛历日期等原逻辑 |
| `services/recon/reconciliation_matcher.py:run_matching` 的第一轮 | `reconciliation/matching.py:pair_by_invoice_number` | 只按归一化后单号配对；同一报告行只消费一次；同号按原始输入顺序；返回剩余索引，未把它们提前判为最终孤立项 |

接口注意：

- `validate_import_rows(header, rows)` 接收已解码的 `ImportCell(value, data_type)`。真正 XLSX 适配器仍必须先核对 5 MiB 压缩体、40 MiB 展开体与格式；本函数没有打开文件，不能单独充当上传接口。
- `entry_request` 只标准化幂等请求；UUID、类型与版本参数校验、图片校验、请求 ID 唯一约束、事务锁和保存回执仍是持久化层责任。图片摘要必须来自服务端已验证图片，不能信任浏览器提交摘要。
- `active_quantity` 输入必须已限定到同一授权商品；将来数据库聚合和任务锁不能用内存函数代替。
- `summarize_item` 输出展示中立的 `not_counted/not_specified/matched/mismatched` 与数量 `over/short` 代码。旧报表的对应泰文含义分别为未盘、账面未指定、一致、不一致、盘盈、盘亏；这里只分离展示文案，不修改判断顺序。语言包和导出模板尚未接入。
- `pair_by_invoice_number` 是第一轮配对，不是完整 `run_matching`。单号强配对允许金额/日期等字段不同，后续仍要逐字段对照；不代表整张票已核对一致。
- 来源 `parse_date("29/02/2568")` 在替换成西历 2025 年时抛 `ValueError`，本批保留并覆盖该限制，没有顺手改变历史行为。

## 金额差分与明确未迁的部分

对账来源实际使用 `float`、`round(..., 2)` 和 `abs(a-b) <= 0.01`。直接换成 Decimal 会改变旧线上配对结果，即使名义上仍是“两位小数、1 分容差”。本批没有安装这种改变。

下表已直接执行 hash 固定的旧 `_get_total`，与 `Decimal.quantize(.01, ROUND_HALF_EVEN)` 对比。两边日期和税号相同，仅展示第二轮的金额判据：

| 原始金额输入 | 旧金额结果 | 旧匹配 | Decimal 匹配 |
| --- | --- | --- | --- |
| `2.675 / 2.68` | `2.67 / 2.68` | 否 | 是 |
| `-2.675 / -2.68` | `-2.67 / -2.68` | 否 | 是 |
| `2.675 / 2.69` | `2.67 / 2.69` | 否 | 是 |
| `1.00 / 1.01` | `1.0 / 1.01` | 否 | 是 |
| `1000.00 / 1000.01` | `1000.0 / 1000.01` | 是 | 是 |
| `-1.00 / -1.01` | `-1.0 / -1.01` | 否 | 是 |
| `0.00 / 0.01` | `0.0 / 0.01` | 是 | 是 |
| `1.005 / 1.015` | `1.0 / 1.01` | 否 | 否（Decimal 为 `1.00 / 1.02`） |

因此第二轮日期+税号+金额、逐字段金额比较、GL/VAT 汇总和银行金额算法都标记**未迁**。继续施工前需明确数值兼容策略并补充差分证明，或在独立行为变更中明确更正精度规则。不能把新 Decimal 的结果算作旧功能等价通过。

**2026-09-21 更新**：数值策略已在独立工作包中确定为“保留已部署的二进制浮点判据”，第二轮配对与逐字段金额比较随之迁入 `reconciliation/amounts.py`、`reconciliation/fields.py` 和 `reconciliation/matching.py`；GL/VAT 汇总与银行金额算法仍未迁移。见[对账数值规则](reconciliation-amounts.md)。

**2026-09-21 再次更新**：银行侧的“余额链校验”已迁入 `reconciliation/bank_statement.py`（行契约、方向纠正、逐行核对、金额反推修复、定案序列、完整性审计）；同一批同时确认该段是 `bank_stmt_xlsx/extract/reread`、`bank_recon_pipeline`、`bank_recon_v2` 的共享实现。流水/GL 解析、匹配评分、任务存储与导出仍未迁移。见[银行流水行契约与余额链](reconciliation-bank-statement.md)。

## 来源业务面盘点与下一步

固定来源的路由盘点是定位索引，不是这些路由全部迁移完成或自动进入保留范围。应继续以当前 Cowork 消费者、功能清单和已确定的删除边界核对。

| 来源入口族 | 路由数 | 主要依赖与未迁内容 |
| --- | --- | --- |
| `routes/stocktake_routes.py` | 14 | Web/LINE 身份、公司授权、模板下载、创建/列表/详情、旧模式计数、扫描明细新增/修改/作废、照片、关闭、删除、导出 |
| `routes/recon_routes.py`、`recon_routes_v1_batch.py` | 9 + 3 | VAT 任务、批量识别、两轮配对、字段覆盖/动作、进度、结果、导出、单条/批量删除；依赖 OCR、计费、原始文件与任务存储 |
| `routes/recon_routes_glvat.py` | 6 | GL 文件与 VAT 文件解析、计算、任务历史与导出；GL 是导入文件，不因此迁会计账簿 |
| `routes/recon_routes_bankv2.py`、`recon_routes_bankv2_run.py` | 5 + 1 | 银行流水/GL 解析、余额链校验、两法分歧人工复核、匹配、历史、删除、导出 |
| `routes/recon_jobs_routes.py` | 6 | bank-v2/gl-vat/vat_excel 提交、任务轮询、模板、确认人工复核行；需逐项区分保留业务与已排除的 Excel 内测，不照搬旧后台 worker |
| `routes/bank_recon_routes.py` | 9 | 银行会话上传/详情/列表、候选、匹配、人工覆盖、公司范围；需和当前页面消费路径对齐，避免把两代任务系统同时搬入 |

盘点存储适配必须保持以下合同：

1. `stocktake/access.py` 原 Web 入口检查 Cowork 身份与 `recon.view/create/export`；LINE 先验证原渠道 ID token，再验证当前有效绑定，随后应用同样角色和公司授权。新原生权限映射不能只隐藏菜单。这里尚未迁鉴权。
2. `stocktake/store.py` 每次读写都限 tenant+workspace，计数、导出、关闭和删除串行锁定任务；导入同 request_id 仅在名称与文件摘要相同才重放成功。
3. `stocktake/entries.py` 先检查 scan 模式，再查操作回执，回执相同可重放；随后才检查 active。更新检查 version、保留 previous/current/actor 审计并在同事务更新总数。关闭后不能新盘，重复已成功操作仍可返回原回执。
4. 空值和 0 不混淆；作废保留审计且不计入合计。旧 legacy 模式的直接数量修改与新 scan 模式不可混用。
5. 照片、导出和删除的归属/清理需新原生文件及 DocType 合同，不复制旧 SQL 到 Frappe，不从新函数虚报已具备持久幂等。

建议后续先建盘点原生记录及公司权限、事务/唯一约束，再接本批合同和离线 XLSX 适配；对账先解数值规则，再迁持久任务及受控解析。前端接入仍依用户要求另行讨论。

## 验证与证据

本批零外部调用、未启动容器。使用本机 `/opt/homebrew/bin/python3.14`，符合 App 的 `requires-python >=3.14`。首次误用系统 `python3`（3.9）在类型注解导入阶段失败；改用锁定主版本运行，未修改代码迁就旧解释器。

```sh
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest discover -s tests -p 'test_cowork_stocktake*.py' -v
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest discover -s tests -p 'test_cowork_reconciliation*.py' -v
PYTHONPATH=apps/pearnly_cowork python3.14 tests/test_cowork_reconciliation_source_parity.py --source /tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b
python3 scripts/check-architecture.py
git diff --check
```

- 盘点 17 个单测、对账 10 个单测通过：覆盖源规则成功/拒绝、数量精度和溢出、六种表头、编码歧义、幂等载荷、作废/null/0、库位比较、原始数据保持、日期和两位佛历、同号消费顺序。
- 固定 seed `20260920`：原函数与新数量合同 **4020** 组输入差分、原第一轮与新第一轮 **1000** 组输入差分通过。金额 **8** 组调查复现上述差异。差分脚本校验源文件 SHA-256，且只执行选定纯函数 AST，不导入旧项目启动/数据库/OCR。
- 架构检查 **0 违规**，`git diff --check` 通过；另外直接检查本工作包 9 个 Python 文件（含未跟踪文件）的语法、行尾空白和域内纯依赖通过。交付前再次读取架构要求，未改变 App/域/数据/依赖边界。
- Site 安装/升级、真实事务并发、公司和角色权限、真实文件/照片、业务 API、队列、浏览器、LINE 真机、部署、外部结果、用户验收：**未执行**，本工作包没有声称通过。

固定源文件摘要：

| 文件 | SHA-256 |
| --- | --- |
| `services/stocktake/excel.py` | `c8027111a6f4c77fdb6fa329df6f9cf798c36440211e44bb2d2673939917f13d` |
| `services/stocktake/entries.py` | `0a62d0c5b234bed64c5bb0aac3efea63124a76c22e25f48db55b935d5c70c0d0` |
| `services/stocktake/reports.py` | `2051f30c8a073326724f5f2d506d6856d3d38f7058a035d8b15afef5da47db72` |
| `services/recon/field_comparator.py` | `109c0401587154d3f3860166b4f0c3812f568b03e2e452a880af866195eec037` |
| `services/recon/reconciliation_matcher.py` | `bc62255e03f1898a4eb6eb83e088d2af08f0cc93ab1d07d9dde2ee359cdeb4e6` |
