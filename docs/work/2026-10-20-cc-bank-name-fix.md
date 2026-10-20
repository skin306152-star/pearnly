# 银行对账把银行名说错（CC 教程录制问题清单第 3 条）

- 目标：银行对账的银行名取账单上印着的；读不出就不写，不按账号格式或流水里的对方银行猜；内部代号（`generic` / `GENERIC`）
  不出现在回复、界面或导出里。按类修，不针对这家虚构银行。
- 不做：识别读法（提示词、模型）不动；银行码的识别口径（`bank_parse.detect_bank` 按全文关键词认银行、文件名优先）不动；
  对账预设的键（`bank|GENERIC|<尾号>|…`）不动；不新增、不改界面文案。
- 状态：本次范围完成（实现 + 本地验证 + 分支 CI）；未合入 main、未部署（任务书明确不合并、不发布）。
- 分支 `pearnly/cc-bank-name-fix`，基线 `origin/main` 241b9ef8；工作树 `.claude/worktrees/agent-a4dcf3e38b8f77353`。

## 核实（生产只读，2026-10-20 晚）

ccdemo.pearnly.com，生产 df2d4cdf，从 `pearnly-production-cowork-web-1` 用 `frappe.db` 只读查（没进 mariadb 容器，两次 SSH）：

- 对账任务 `CBR-6d1383436edccc61…`（2026-10-20 19:59，标题「กระทบยอดธนาคาร ก.ย. 2569」）：`bank_code = generic`，
  `parse_info.stmt_files = [{file: Statement_1187305542_202609.pdf, bank_code: generic, account: 5542, account_read: true, rows: 26}]`，
  总账科目 `11-01-01-00`。
- 同一对话 `sesn_01VDRvQyP3N683JdB4MKUusG` 的 25 次工具调用结果里**没有一处**出现「กสิกร / KBANK / KBank / Kasikorn」；
  `cowork_recon_result` 给模型的只有 `summary.bank_code: "generic"`，没有任何银行名。
- 回读出的总账（`test-GL-2026-09.xlsx`）里 `11-01-01-00` 的科目名是「เงินสด」，也不含银行名。
- 离线用同一份 PDF 跑解析（`bank_intake.read_statement_file`）：`bank_code = generic`、`source = text_lines`、26 行；
  文字层第一页第一行就是「ธนาคารสาธิต」，但没有任何代码读它。

## 根因

1. **银行名从来没被读出来**：PDF 文字层路径只认 7 家银行的关键词得到银行码，认不出就是 `generic`；表头上印着的银行名不进结果。
2. **内部银行码直接当银行名给人看**：导出标题与「ธนาคาร」一栏写 `summary.bank_code.upper()`（→「GENERIC」），文件信息写每份的
   `bank_code`（→「generic」）；结果列表的银行列、给 CC 的结果要点和列表也原样给 `generic`。
3. **「กสิกรไทย」是模型自己猜的**：工具结果只有 `generic`，没有银行名；模型手里只有文件名里的账号 1187305542
   （格式 xxx-x-xxxxx-x，与 KBank 账号的写法一样），就补了一个银行名。服务器上没有任何来源给出「กสิกรไทย」，
   也不是记住的选择或预设（这份对账没有银行预设）。

## 修法

- `reconciliation/bank_text.printed_bank_name`：PDF 文字层第一页、流水表之前（与 `labelled_account` 同一块表头）第一个像银行名的栏：
  「ธนาคาร…」开头的（「ธนาคารผู้รับ / ปลายทาง」这类栏名不算，带「สาขา…」的截掉分行）、「ธนาคาร / ชื่อธนาคาร / Bank (Name):」标签后面的、
  带「bank」一词的英文名（账单标题、栏名不算）。流水行里的对方银行、页脚、第二页都不看。
- `bank_intake.read_statement_file` 结果多一个 `bank_name`（有才给）；识别读出的账单用已有的 `identity.bank`（模型按「印着的」读）。
- `parse_info.stmt_files` 多存一个 `bank_name`（契约 `bank_recon_contracts` 放行，旧任务没有照旧）。
- 新的纯函数模块 `reconciliation/bank_names`：给人看的银行名 = 读成功的几份账单上印着的（不同取多数）→ 没有就是认得出的银行码大写（`KBANK`，与原来一样）
  → 都没有为空。`generic` 永远不给人看。
- 用到的地方：
  - 结果页：它就是由同一本复核工作簿画出来的（`recon_center.sheets._view` → `revision.bank_workbook_bytes`），一起传 `bank_label`；
    结果页存档的指纹含 `reconciliation/` 全部源码，部署后旧存档自动重建。
  - 导出：`export.workbook(..., bank_label=)`；标题读不出时不带「· 银行」，「ธนาคาร」一栏写「—」（同旁边总账科目一栏的写法），
    文件信息写每份的银行名；导出文件名的兜底（没有标题时）不再拼 `GENERIC`。不传 `bank_label` 时照旧（旧对照测试的口径不变）。
  - 列表与结果要点：`bank_recon_service.BankTasks`——列表的银行列给银行名（排序照旧按存的银行码）；`detail` 另给 `bank`、汇总里的
    内部银行码去掉。
  - CC 工具 `cowork_recon_result`：银行对账给 `bank`（读不出就不给）；工具说明加一句：`bank` 是账单上印着的银行名，没有就是读不出，
    不要说银行名，也不要按账号或流水推断。`cowork_recon_list` 经由列表同样不再给 `generic`。
- 有意变化：导出的银行一栏、标题、文件信息从银行码改成账单上印着的名字（认得出、没印名字的照旧是 `KBANK` 这类码）。
  对照旧线上的 `tests/test_cowork_bank_recon_export_source_parity.py` 不传 `bank_label`，口径不变（它要 `--source`，不在 CI 里）。
- 文案：没有新增、没有改动界面文案（`check-copy.py --report` 新增 / 改动 / 删除都是「无」）。工具说明是给模型的，不是界面文案。

## 验证

| 层级 | 代码 | 环境 | 场景 / 命令 | 结果 |
| --- | --- | --- | --- | --- |
| 回归测试（修前） | origin/main 241b9ef8 的 `apps/` | 本机 colima，`pearnly-ci:runtime-067596c27f44-df29939b831c`（arm64，含 frappe / pdfminer / openpyxl），`--network none` | `tests/test_cowork_recon_bank_name.py` | 16 项全部不过：导出里出现「งบกระทบยอดเงินฝากธนาคาร · GENERIC」「GENERIC」「… · 26 รายการ · generic」（与录制现象一致）；结果要点没有 `bank`；没有表头银行名 |
| 回归测试（修后） | 本分支 | 同上 | 同上 | 17 项通过（另加结果页工作簿 `revision.bank_workbook_bytes` 一项）：表头读法（pdfminer / pypdf 两种抽法、泰文 / 英文 / 标签写法、只看表头）、已知银行仍认对（`BANGKOK BANK` → bbl，文件名 SCB → scb）、未知银行取原文、读不出为空、导出 / 列表 / 结果要点不出现 generic、契约存取、真 PDF（`tests/fixtures/bank-statements/Statement_1187305542_202609.pdf`）读出「ธนาคารสาธิต」 |
| CC 工具 | 本分支 | 本机 python3 | `tests/test_cowork_assistant_tools.py`（新增：给 `bank`、读不出不给、不出现 generic、说明里有「do not name a bank」） | 223 项通过 |
| 受影响的对账测试 | 本分支 | 同上 CI 运行镜像 | `tests/` 下 45 个 recon / bank 测试文件（去掉要 `--source` 的旧对照） | 38 个通过；7 个因这个旧运行镜像缺 `pymupdf` 导入失败（与改动无关），改在生产镜像 `dd36053868b9`（amd64 模拟）里补跑，见下行 |
| 受影响的对账测试（生产镜像） | 本分支 | `ghcr.io/skin306152-star/pearnly/frappe@sha256:dd36053868b9…` | presets / progress / scanned_docs / speed / waiting / originals / bank_name / pdf_account | 8 个文件全部通过（41 + 13 + 122 + 6 + 21 + 9 + 16 + 10 项） |
| 本地闸门 | 本分支 | 本机 | `.github/workflows/development.yml` 里全部单行 python3 / node 步骤（110 步），含 `check-architecture.py`（0 违规）、`check-copy.py`（新增 / 改动 0）、`check-dms-offline.py`；`npm ci` + `frontend/assistant` 构建后的 `test_assistant_ui.py`（248 项）；`git diff --check` | 全部通过（`test_assistant_ui.py` 第一次因本工作树没装 `frontend/assistant` 依赖失败，`npm ci` 后通过，与改动无关）；`frontend/` 的 `npm ci` + DMS 编辑器快照两步没跑（未改前端，交给分支 CI） |
| 分支 CI | 推送后的提交 | GitHub Development checks | `gh run list --branch pearnly/cc-bank-name-fix` | 见交付说明 |
| 端到端（testcowork） | — | — | — | 未执行：服务端整条路径（真 PDF → 结果 → 存储契约 → 导出 / 列表 / 结果要点）已离线覆盖；CC 在真模型下是否不再自己补银行名没有实测（会花模型费、要占共用测试环境），留给教程重录或测试窗口 |
| 部署 / 用户验收 | — | — | — | 未执行（任务书不合并、不部署） |

## 未覆盖与遗留

- 模型行为：工具结果现在给出印着的银行名、读不出时不给，说明里明确不许推断；但模型是否完全不再凭账号格式猜，只能在真对话里看。
- 表格账单（xlsx / csv 直读）不读银行名：没有印着的名字时照旧是认得出的银行码，认不出为空。
- 内容识别认银行仍按全文关键词（流水里提到别的银行会被认成那家，会影响走哪个文字解析器与预设键）；本次只保证给人看的名字取印着的，
  识别口径未动。
- 对账预设列表给模型的 `scope_key` 里仍有内部键 `bank|GENERIC|…`（它是要原样传回的键）；给人看的 `label` 早已不写 GENERIC。
- 已存的旧任务没有 `bank_name`：导出 / 列表对它们写认得出的银行码，认不出的为空（不再写 GENERIC），不补读旧文件。
