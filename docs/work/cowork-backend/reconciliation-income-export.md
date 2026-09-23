# Cowork 后端迁移：收入对账的复核工作簿导出（第三十批）

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `b5c3e66`（第 29 批交付）。

本包把旧项目收入对账的复核工作簿（`gl_vat_excel`）按部署版逐格迁入，并给原生任务加了一个受权下载入口。会计拿到手核对的那份产物现在能由新平台从已存任务生成；不接前端、不部署、不改 DMS、不写旧库。

## 定形：单文件搬运 + 逐格差分

| 来源 | 性质 | 本批处置 |
| --- | --- | --- |
| `services/recon/gl_vat_excel.py` | 单一模块：四语 `_I18N` 表、`_t`、`export_gl_vat_excel`（3 个 sheet：明细+KPI 带 / 对账汇总 / 四语使用说明） | **迁入** `reconciliation/export/income_workbook.py`，只改类型导入与缩进 |
| `services/recon/gl_vat_types.py` | `GlVatSummary`/`ReconRow`（第 25 批已迁 `income_vat.py`） | 复用既有迁移件 |
| `routes/recon_routes_glvat.py: /gl-vat/{task_id}/export` | 取任务 → 反序列化明细/汇总 → 渲染 → 下载名 | **语义迁入**原生 `income_recon_service.export` + GET RPC |
| `gl_vat_serialize.py` 的 `detail_from_json`/`summary_from_json` | JSON → 数据类 | 由原生 `_export_row`/`_stored_summary` 承担（存储形态不同，见下） |

固定来源只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`；差分脚本先校验 SHA-256，再执行固定源码里的真实 writer（主机把固定源码文本经管道送进有 openpyxl 的 bench）。

| 文件 | SHA-256 |
| --- | --- |
| `services/recon/gl_vat_excel.py` | `8527af0e5ec1cb7115a1cc8af284903f8ebb156bdf72bfc6700f577ef799fd20` |
| `services/recon/gl_vat_types.py`（已有） | `ee24b7dd0a7a24e67ac338f7c7deae73c71c36aca5b731ddc18b6e6af27a300d` |

## 落点与保持的行为

- `reconciliation/export/income_workbook.py`：`_I18N`（四语，逐键与部署版一致）、`_t`、`export_gl_vat_excel(detail, summary, lang)`。
- Sheet 1：KPI 带（总笔数 / 完全匹配 / 有差异 / GL 未找到 / 差异金额合计，五行五值 + 各自字体、填充与数字格式）、空行、表头 8 列、每行状态文案与配色（匹配绿 / 差异红 / 未找到黄）、数字列右对齐与格式、列宽 `[14,20,14,32,18,18,14,22]`、冻结在表头下一行。
- Sheet 2：`总账合计` → `减 GL 贷方单边` → `加 GL 借方单边` → `加 VAT 单边正数` → `减 VAT 贷方单边` → `销项税报告合计`；两个合计行填金额并着色，四个调整标题只做分区（淡蓝底 + 蓝色分隔线，`r > 2` 才加线），每个调整项下展开 `单据号 · 日期 · 客户名` 明细行（名称超 55 字符截断加省略号、信用侧取负、灰色小字白底右对齐）。
- Sheet 3：参考卡（四语并排，标题/Arial 字号/自动换行与逐行间距照旧），与所选导出语言无关。
- 原生入口：`income_recon_service.export(task_id, lang)`（权限沿用任务 API 的 `recon` 功能 + 角色 + 工作区范围，未知语言回落 `th`）与 GET RPC `income_recon_api.export`（按私有附件返回、写 Access Log）。

## 有意差异

1. **存储形态**：部署版把明细与汇总存成 `detail_json`/`summary_json` 再由 `*_from_json` 复原；原生按行表 + 汇总列读取，`_export_row` 把 `has_gl_match` 还原成 `gl_amount=None/diff=None`，`_stored_summary` 从 `summary_json` 重建数据类（含四个调整项列表）。
2. **下载名**：部署版同时发 ASCII 安全名与 RFC 5987 UTF-8 名两个 `filename`；原生沿用银行那批的做法，发单个 `GL_VAT_recon_<任务>_<销项税文件名 ASCII 安全基名>.xlsx`（≤40 字符，非 ASCII 字符换成 `_`，空则该段回落 `vat`）。
3. **权限口径**：部署版用 `recon.view`（导出）/`recon.export` 权限位；原生与任务 API 一致，用 `recon` 功能 + 角色 + 工作区范围（第 24 批已登记同一差异）。
4. **未展示的存储字段**：第 29 批存的两个文件名与解析诊断在部署版版式里本来就没有位置，Sheet 上不出现（后续若要"文件信息"段，属新版式，需另行登记）。手动覆盖/运行警告在收入这条链上没有对应的部署段。

## 验证

```sh
python3 scripts/check-architecture.py
python3 scripts/i18n-build.py --check
python3 scripts/check-i18n.py          # 四语标签按登记豁免，RESULT: REPORT-ONLY
git diff --check

# 隔离 bench（有 openpyxl）
cd /workspace/tests && ../env/bin/python test_cowork_income_recon_export.py
# 固定源差分（主机生成源码 JSON，管道送进 bench）
python3 - <<'PY' | ../env/bin/python /workspace/tests/test_cowork_income_recon_export_source_parity.py --source-stdin
...
PY
# 隔离 Site
../env/bin/python /workspace/scripts/cowork-income-recon-export-lab-verify.py   # 两个 Site 各 3 个
../env/bin/python /workspace/scripts/cowork-income-recon-lab-verify.py          # 两个 Site 各 16 个
```

- 新模块离线 XLSX 用例 **14** 个通过（四语 sheet 名与未知语言回落、几何与冻结、KPI 带数值与配色、行状态文案与填充、`not_found` 四语、空任务、汇总梯子与明细行取负/截断/配色/分隔线、使用说明四语齐备、标签表完整性）。
- 固定源差分：**192** 个工作簿、**23268** 个单元格、**180** 组标签比对全部一致（值/类型/字体/填充/边框/对齐/数字格式/保护/合并/列宽/行高/冻结/归档结构），四语各 32 本 + 未知语言 64 本，覆盖率下限不达即失败。差分抓到一处真实笔误（日文说明第 2 行少一个空格），已按部署版改正——这正是逐格差分的价值。
- 隔离 Site：导出脚本两个 Site 各 **3** 个用例通过（工作簿内容与三行状态、权限与功能位拒绝、WSGI 真请求的私有附件响应 + 匿名/非 GET 403 + Access Log 入队）；收入存储脚本各 **16** 个继续通过。两次 Site 迁移非部署。
- 主机侧既有：对账 **137** 个用例、架构 **0** 违规、`i18n-build --check` PASS、`check-i18n` REPORT-ONLY（新增四语表按登记豁免）、`git diff --check` 干净。
- 未执行：真实文件读取、异步任务与轮询、批删、`reconciliation_type=income` 的统一入口、前端、部署、外部结果与用户验收。

## 来源核对与运行投影

- 交付 `baseline-thirtieth-delivery.json`（`3833e228…`，采集 2026-09-21T14:40:11Z）与第 29 批交付快照 `compare` **PASS**，`source` 仍 `ec2e5c957e6b…`。
- 运行投影 `push-runtime-thirtieth-delivery.json`（`af1a0b00…`）：端点 `configuration_digest` `ff8e39f4…` → `bf041a45…`（端点数 60、平台开关不变）。**完整配置与旧状态迁入继续 HOLD**。

## 未做与后续

- 收入族只剩：异步任务与轮询、批删。银行侧剩余项（异步状态与轮询、批删、手动覆盖痕迹/运行警告落库）仍挂着。
- 未接前端、未部署、未合并回 main、未推送；除只读的来源采集外没有外部调用，没有写任何旧库。
