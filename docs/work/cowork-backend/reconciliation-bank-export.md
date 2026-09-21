# Cowork 后端迁移：银行对账复核工作簿导出（第二十四批）

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `a65cdd4`（第二十三批交付）。

本包把旧项目银行对账的**四表 XLSX 复核工作簿**（会计拿到手做人工复核的那份产物）按部署版逐格迁入，并给原生任务加一个受权下载入口。没有新增 RPC 之外的界面、没有接前端、没有部署、没有写 ERP；工作簿只读任务已存下来的行与汇总。

## 来源清点与独立性

旧侧导出入口是 `routes/recon_routes_bankv2.py:bank_v2_export`（`/bank-v2/{task_id}/export`，GET，`recon.export` 权限）：按 task 读 `detail_json`/`summary_json`，再用 `export_bank_recon_excel(...)` 生成字节。生成链共 5 个文件，自述"纯表现层：i18n 标签 + openpyxl 组表，无匹配/判定逻辑"：

| 来源模块 | 职责 | 本批处置 |
| --- | --- | --- |
| `services/recon/bank_recon_excel.py` | 四个 sheet 的组装与入口 `export_bank_recon_excel` | **迁入**（`export/workbook.py`，入口改名 `workbook`） |
| `services/recon/bank_recon_excel_summary.py` | Sheet 1（สรุป）：汇总、文件信息、使用说明三段，含全部内层闭包 | **迁入**（`export/summary.py`） |
| `services/recon/bank_recon_excel_i18n.py` | 四语标签表 + `t`/`layer_label`/`status_label` | **迁入**（`export/labels.py`） |
| `services/recon/bank_recon_excel_usage.py` | Sheet 1 使用说明文案块（4 语） | **迁入**（`export/usage.py`） |
| `services/recon/bank_recon_excel_styles.py` | 共享调色板与表头/边框/日期 helper | **迁入**（`export/styles.py`） |
| `routes/recon_routes_bankv2.py` 的下载外壳、`bank_gl_excel.parse_gl_excel`、`gl_vat_excel`、`bank_recon_v2_store` | HTTP 响应组装、GL 解析器、VAT 工作簿、旧任务存储 | 未迁（前者是旧路由；后三者是解析/其它报表/旧存储） |

固定来源只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`；差分脚本先校验 SHA-256，再执行固定源码里的真实 writer。

| 文件 | SHA-256 |
| --- | --- |
| `services/recon/bank_recon_excel.py` | `2735766f0b1f59e8fd9b81854f9f0997f7d3987897b137ac64b33fed3a63a807` |
| `services/recon/bank_recon_excel_i18n.py` | `d4a0071b860c201ec05c7ede48b24f403c96337a865e051dba1e621d381f4fde` |
| `services/recon/bank_recon_excel_styles.py` | `038c2d4eb2590918f3988d61c943b76ab2b9f95f6732343713a2ff2fbe1d7011` |
| `services/recon/bank_recon_excel_summary.py` | `cd818cd8ed2f573ab299ee6d0a55ade95fb655dc7ed08e0de6b3e409916495a7` |
| `services/recon/bank_recon_excel_usage.py` | `aeb4705687f5b96f6fbf51079f5fcbb423962e06db177b845e28ca0397664497` |
| `services/recon/bank_recon_types.py`（已有） | `df0508cccb9891a5ea628cbce104ee09001b976ade15afd4d599c5d766de149e` |

## 落点与调用合同

实现：`reconciliation/export/`（`styles.py`、`labels.py`、`usage.py`、`summary.py`、`workbook.py`）。只依赖本域已迁的 `bank_matching.BankReconRow`/`BankReconSummary`、openpyxl（产品镜像已装 `openpyxl==3.1.5`）与标准库。

```python
from pearnly_cowork.reconciliation.export import workbook

content: bytes = workbook(recon_rows, summary, lang="th", task_info=None, parse_info=None,
                          anchor_overrides=None, anchor_ocr=None, warnings=None)
```

签名与部署版 `export_bank_recon_excel` 完全一致（含只被接受、未被使用的 `anchor_ocr`）。函数体是机械搬移：只改模块头、导入路径与私有名公开（`_t`→`t`、`_layer_label`→`layer_label`、`_status_label`→`status_label`、`_hdr_style`→`hdr_style`、`_border_range`→`border_range`、`_fmt_date`→`fmt_date`、`_USAGE_BLOCKS`→`USAGE_BLOCKS`、`_I18N_EXPORT`→`I18N_EXPORT`、`_build_*_sheet`→`build_*_sheet`、`export_bank_recon_excel`→`workbook`）。

原生消费端：`bank_recon_service.export(task_id, lang="th")` 读任务、权限沿用 `bank_recon_access.require(workspace)`（`recon` 购买功能 + 角色 + 工作区公司范围），把存储的结果行反解回 `BankReconRow`、把 `summary_json` 反解回 `BankReconSummary`，再用任务存的每文件解析诊断渲染工作簿，返回 `{"filename": "BankRecon_v2_<任务>_<银行码>.xlsx", "content": bytes}`（与部署版下载名一致）。RPC `bank_recon_api.export` 是 **GET**，写一条 Access Log（`file_type=bank_recon_workbook`）并按私有下载返回（`attachment` + `Cache-Control: no-store` + `X-Content-Type-Options: nosniff`），与盘点导出的下载外壳同一套。

新增存储字段 `parse_info_json`（Long Text，隐藏只读）：契约 `bank_recon_contracts.parse_info` 只接受部署版的两段与记录键（`stmt_files`: file/rows/ok/error/error_code/bank_code，`gl_files`: file/rows/ok/error/error_code/accounts），未知段/键、越界行数、非字符串科目或超量记录一律拒绝。`create` 与 `create_from_parsed` 都接受可选 `parse_info`；`create_from_parsed` 另加 `gl_filename` 并把本次解析的每文件信息按部署形状落库（`ok=True`、`rows=该侧行数`、`accounts` 取解析结果）。重放判定仍只看表头与行（`request_digest`），文件诊断不参与摘要，重放时保留首存诊断。

## 保持的布局与行为

- 固定 4 个 sheet，sheet 名随 `lang` 取部署标签表：ฐรุป/汇总/…；未知语言回落 `th`（部署版同规则）。
- Sheet 1 三段：汇总（标题 + 银行/科目 + 期末锚点 + 期初差异 + 四类未匹配展开 + 计算期末 + 差额 + 统计）、文件信息（优先 `parse_info`，否则 `task_info` 的文件串重建，都没有则只留小节标题）、使用说明（4 语块，标注加粗行）。零匹配或匹配率 <10% 且项数 ≥10 时的红字横幅、以及 `anchor_overrides` 非空时的手动录入痕迹小节与黄底对照 + 单元格批注，都按部署条件出现。
- Sheet 2 合并结果：匹配行按层着色（L1/L2/L3），其后单侧行按紫/蓝着色；`Status` 首列用 `status_matched`/`status_label` 文案，`Match Layer` 对未匹配写 `—`；金额 `#,##0.00` 且右对齐。
- Sheet 5/6：对账单明细与 GL 明细，置信度列（高/中/低三色）与余额校验列（`ok`/`warn`/`fixed`/`na`，自动修正优先显示 `fixed`）、来源文件列、按日期排序、行内红色标记不平衡行；表头 `freeze_panes` 分别为 Sheet2 `C2`（冻结状态与层两列）、Sheet5/6 `A2`、Sheet1 `A6`。
- 标签表 103 条、四语齐全；月份/日期显示沿用 `%d/%m/%Y`。
- 机械搬移保留了部署版的中文注释与四语数据表（`labels.py`/`usage.py` 整文件、`summary.py` 的两条标题文案已登记在 `scripts/i18n/keep-cjk.tsv`，理由是"工作簿自身的翻译源，按导出语言取词，不进 Desk 界面"）。

## 登记的有意差异与缺口

- **手动覆盖痕迹与运行警告暂不出现**：部署版把 `_anchor_overrides`/`_anchor_ocr`/`_brv2_warnings` 存进 `summary_json`（来自未迁的运行编排与 OCR 锚点）。原生任务不存这三项，导出时不传，所以这两段/横幅不会出现；纯 builder 支持它们，且差分用例覆盖了传入时的逐格一致。
- **失败文件的诊断不落库**：原生入账一次只收一份成功解析（失败即拒），所以文件信息里不会出现部署版多文件运行中 `ok=false` 的行；`parse_info` 契约本身允许 `ok=false` 记录，供将来多文件编排使用。
- **未知余额渲染为 0.00**：结果行类型 `BankReconRow.stmt_balance` 是 float，部署版没有"未知余额"状态；存成未知的行在工作簿里与 0.00 相同（原生行仍有 `stmt_balance_known` 注记，只在详情接口体现）。
- **权限口径**：部署版另有 `recon.view`/`recon.create`/`recon.export` 细分权限位；新平台沿用既有"`recon` 功能 + 角色 + 工作区范围"的服务端门禁，不再新建独立导出权限位（与第二十一批的任务 API 同一口径）。
- **下载方式**：RPC 只接受 GET（与盘点导出、旧路由一致），其它方法 403；响应外壳、Access Log 与私有缓存头复用既有实现，未新建第二套。
- **工作簿不做业务判断**：不重算匹配、不写库、不读文件、不连 ERP；`export()` 不写任何任务字段。

## 验证

```sh
# 主机（无 openpyxl）
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest discover -s tests -p 'test_cowork_reconciliation*.py'
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest tests.test_cowork_bank_recon_contracts
# 隔离 bench（有 openpyxl）
cd /home/frappe/frappe-bench/sites && ../env/bin/python -m unittest discover -s /workspace/tests -p 'test_cowork_bank_recon_export*.py'
# 固定源差分（主机生成源码 JSON，管道送进 bench）
python3 - <<'PY' | ../env/bin/python /workspace/tests/test_cowork_bank_recon_export_source_parity.py --source-stdin
...
PY
# 隔离 Site
bench --site <lab Site> migrate
PEARNLY_COWORK_BACKEND_LAB=1 ../env/bin/python /workspace/scripts/cowork-bank-recon-lab-verify.py
PEARNLY_COWORK_BACKEND_LAB=1 ../env/bin/python /workspace/scripts/cowork-bank-recon-export-lab-verify.py
python3 scripts/check-architecture.py
git diff --check
```

- 新模块离线 XLSX 用例 **22** 个通过：四语 sheet 名与未知语言回落、工作簿归档结构（4 个 sheet part）、冻结窗格、匹配/单侧行的状态与层文案、金额格式与对齐、置信度与余额校验列（含"已修正优先"）、标题与身份行、零匹配横幅、OCR 小节只在有标记行时出现、文件信息优先 `parse_info` 且缺省时无状态格、手动录入痕迹只在有覆盖时出现、空任务仍出 4 表、标签表四语完整性与 helper 回落、日期与表头样式 helper。
- 固定源差分：**41** 个工作簿、**11728** 个单元格、**714** 组标签/helper 比对全部一致（含每格的值与类型、字体、填充、边框、对齐、数字格式、保护、合并单元格、列宽、行高、冻结窗格、筛选）；覆盖 186 行结果（5 种状态、L1/L2/L3 与未匹配全部命中）、四语各 ≥5 本、`parse_info`/`task_info`/`anchor_overrides`/`warnings` 各 ≥8 本、自动修正/余额不平衡/低置信度各 ≥5 行，低于下限即失败。
- 隔离 Site（`cowork-backend-ack.localhost`、`cowork-backend-preparation.localhost`）：`migrate` 通过并新增 `parse_info_json` 列与 DocField；导出脚本各 **3** 个用例通过（工作簿内容与文件诊断、权限与功能位拒绝、WSGI 真请求的私有附件响应 + 跨用户/匿名/非 GET 403 + Access Log 入队）；既有对账脚本各 **10** 个用例继续通过，五个入口匿名调用全部 403。两次 Site 迁移都是本批对 lab Site 的升级，不是部署。
- 主机侧既有检查：对账 **123** 个用例、`test_cowork_bank_recon_contracts` **15** 个用例、固定源差分（第二十一/二十三批口径）继续通过；架构 **0** 违规；`i18n-build --check` PASS；本批新增/改动文件在 `check-i18n.py --list` 的真实发现里 **0** 条（四语表按登记豁免，计入"有意保留"）。
- 顺带修了验证夹具：lab Site 长期存在、每个脚本每次运行都建合成用户，重跑时撞上 Frappe 的 User 创建节流（`Throttled`，默认 60/小时）。`scripts/cowork-backend-lab-verify.py:connect` 在标记过的 lab bench 内把 `throttle_user_limit` 提到 1000，**仅对本进程生效**，不写 Site 配置、不改产品行为；改后两个 Site 的既有对账脚本与导出脚本都再跑通过。
- 运行投影：交付前采 `push-runtime-twenty-fourth-delivery.json`（`3c340208…`），`verify` PASS；与第二十三批交付比较仍为 `push_runtime_identity_changed`，差异只在 `erp_endpoints.configuration_digest`（`c012939c…` → `1bdf7bd2…`，端点数 60、两个平台开关与缺失列不变）。**完整配置与旧状态迁入继续 HOLD**。

## 未做与后续

- 未迁：GL/VAT 工作簿（`gl_vat_excel`）、异步任务状态与轮询、`bank_stmt_reread` 换眼重读、真实文件解析与模型抽取、收入对账（`reconciliation_type=income`）、批量删除、前端、部署。
- 手动覆盖痕迹与运行警告要真正出现在工作簿里，需要先迁运行编排里的锚点/警告来源并落库；本批只保证"传进去就一致、没传就不出现"。
- 未合并、未推送、未部署；工作簿全部由任务内合成数据生成，没有真实文件、模型调用或外部结果。
