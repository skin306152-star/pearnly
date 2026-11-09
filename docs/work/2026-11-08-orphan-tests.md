# 没人跑的测试清点（2026-11-08）

- 主会话 local_e395dace 派；分支 `pearnly/orphan-tests`，基线 `b29b11c84`。
- 起因：期初报表测试 `test_opening_report.py` 在 main 上 20 个错误却没人发现（不在任何 CI 步骤里，11-01 接口改了没跟）。

## 1. 口径与分类（第 1 步，只读）

「被 CI 执行」= `.github/workflows/*.yml` 的 run 行、`scripts/ci-candidate-checks.sh` 点名的（文件名或模块名），加上这些 run 行执行的脚本里按路径
`tests/<文件>` 列出的（`scripts/check-dms-offline.py` 的清单）。CI 里没有按目录或 discover 收测试的写法。只被别的测试 `import`
借样例（`*_source_parity` 借基础测试的数据）不算：被借的那个测试自己的用例没有跑。

| 类 | 个数 | 处理 |
|---|---|---|
| 工作流 / 候选检查直接点名 | 249 | 不动 |
| 经 `check-dms-offline.py` 清单执行（CI 跑它） | 28 | 不动 |
| 迁移对照工具（要 `--source` 传入旧仓 pearnly-app 固定提交的源码） | 44 | 写进 `scripts/tests/not-in-ci.tsv`，原因逐条写；迁移复核时手动跑 |
| 普通单测，从没被 CI 跑过 | 110 | 进 `scripts/tests/orphan-baseline.txt`（只许减少），第 2 步在本机与生产镜像各跑一遍再分：能过 / 过期 / 作废 / 真缺陷 |

合计 431 个（本分支另加闸门自测 1 个）。

## 2. 防再犯（第 4 步，随第 1 批合）

`scripts/check-test-coverage.py`（Development checks）：`tests/` 下每个 `test_*.py` 要么被 CI 执行，要么登记在 `not-in-ci.tsv` 并写原因；
清点中的存量在 `orphan-baseline.txt`，相对 merge-base 新加条目判失败，已被 CI 跑到 / 已删 / 已豁免的条目要同一提交删掉。
自测 `tests/test_check_test_coverage.py`：新加没人跑的测试被拦、只被 import 不算、豁免没写原因被拦、过期登记被拦、基线新加被拦、本仓通过。

## 3. 第 2 步：110 个逐个实跑（11-09）

跑了三遍，结果绑在基线 `fac13c1fa` 的工作树上：
- 本机裸 python3，等同 Development checks：runner 不装第三方包；
- 生产镜像 `26a2b0e8`；
- 开发镜像 `fb1037ad`（回归实验栈默认镜像）。

镜像里都是 `--network none`、只读挂工作树、逐个独立进程跑。

| 类 | 个数 | 处理 | 批 |
|---|---|---|---|
| 裸 python3 就过 | 64 | `scripts/run-offline-tests.py --plain`（Development checks，合计约 2 秒） | 1 |
| 过期，已修，裸 python3 可跑 | 1 | `test_opening_batch`：仓位替身缺 11-07 加的 `positions.resolve` | 1 |
| 过期，已修，要镜像 | 3 | `test_cowork_ocr_contracts`：`ocr.inputs` 已并进 `pearnly_core.originals.rules`（ADR-024）；`test_dms_ocr_timing`：LINE 加载动画改成 `start_loading_background`；`test_cowork_invoice_checks`：识别回放 `replay.active()` 没打替身 | 测试修复随 1，接 CI 随 2 |
| 只在镜像里过（要 frappe / pydantic / openpyxl / PIL / docx / werkzeug / PDF 库） | 40 | 候选检查 offline 道 `--image`（生产镜像合计约 35 秒） | 2 |
| 过期，另一分支已修 | 1 | `test_opening_report`：`pearnly/opening-report-test` | 合入后删基线行 |
| 要真实发布实验室（MariaDB + Frappe + Keycloak） | 1 | `test_release_member_identity`：离线跑不了，等主会话定豁免还是删 | 3 |
| 作废 | 0 | 静态预查疑似的 `test_cowork_companion_release` 是看错了路径（文件在 `apps/pearnly_cowork/.../companion/heartbeat.py`），通过 | — |
| 真缺陷（业务断言失败） | 0 | — | — |

分类清单另发主会话（共享笔记「孤儿测试-第2步分类-2026-11-09.md」）。

## 4. 暂停交接（11-08 20:2x，本机 21:00 睡眠）

已做（本地提交，未推送）：第 1 步分类、第 4 步闸门 + 两张表 + 自测 + CI 一步、本记录。本地 `python3 scripts/check-test-coverage.py` 通过、自测 6 个通过；还没跑 gate.sh。
静态预查：110 个导入的本仓模块都还在；只有 `test_cowork_companion_release.py` 引用的 `companion/heartbeat.py` 已不存在，疑似作废，第 2 步实跑确认。

下一步（11-09 白天，按主会话）：
1. 先看 `pearnly/opening-report-test`（bd144b92b，已推送未合）合了没有。它合入后 `test_opening_report.py` 已由 CI 执行，本分支变基时要从 `orphan-baseline.txt` 和上面清单里删掉这一行（闸门会提示）。
2. 第 2 步：110 个在本机 `python3` 和生产镜像（`ghcr.io/skin306152-star/pearnly/frappe@sha256:26a2b0e8dfeec93a10560e2be8b6ea15aea27b9725b68547a5dc2b362b829add`，`docker run --rm --network none`，只读挂工作树）各跑一遍，按「能过 / 过期 / 作废 / 真缺陷」分类；先确认本机没有别的窗口在跑全套流程。分类清单先发主会话，真缺陷只列不改业务代码。
3. 分 3 批合：第 1 批 = 闸门 + 能过的直接接进 CI（同一提交从基线删掉）；第 2 批 = 修好的过期测试；第 3 批 = 删作废测试（写原因）+ 剩余登记。每批 gate.sh、推分支等 CI（推完 15 分钟以上再查）、按排号快进合入。
