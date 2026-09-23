# 团队协作（WeKan 看板）移除记录

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，基线 `3be521a`（第二十一批交付）。

用户指示：团队协作功能不要搬；如果搬了就删了；该功能后期自行开发。2026-09-17 那次"协作页 `/work`（WeKan）留"的确认由本次覆盖，`docs/p0/04-scope-and-drop-list.md` 已在该文件顶部登记覆盖说明。

## 删除前的审计（只读）

先查清这个仓库里到底搬进来什么，再动手，避免把只读来源、历史证据和真正要删的功能混在一起：

| 类别 | 位置 | 处置 |
| --- | --- | --- |
| 产品功能键 | `apps/pearnly_cowork/pearnly_cowork/product_manifest.py` 的 `"boards"` | 删除 |
| Cowork 侧边栏入口 | `apps/pearnly_core/pearnly_core/desk/navigation.py` 的 `nav.work` | 删除 |
| 前端导航与图标 | `frontend/src/lib/nav.ts` 的 work 项、`frontend/src/lib/icons.ts` 的 `/work` 映射与 `IconBoard` 导入 | 删除 |
| 原生占位页 | `apps/pearnly_core/pearnly_core/pearnly_core/page/pearnly_work/`（js/json/`__init__`） | 删除 |
| 旧壳页表 | `apps/pearnly_core/pearnly_core/www/pearnly-app.html` 里的 `"work": "pearnly-work"` | 删除 |
| 控制台功能目录 | `apps/pearnly_control/pearnly_control/features.py` 的 `boards` 条目 | 删除 |
| 样板基础设施 | `infrastructure/compose/03-wekan.yml`（两租户各一套 WeKan + MongoDB） | 删除 |
| 样板引用 | 46 个脚本/文档（`stack.sh`、`dev-reload.sh`、`production/control-up.sh`、各 `sample-verify-*`、`sample-verify-tenant-home.mjs` 等）中的该 compose 文件 | 摘掉引用（45 处整行删除，2 处同一行里只摘该文件） |
| 文案 | `nav.work`、`Collaboration Boards (WeKan)`、`A board instance is created only when this is on` 三条术语与四个语言包；`pearnly_feature.json` 里把"协作看板"当例子的说明 | 删除或改写，并重新生成语言包 |
| 描述性提及 | 根 `README.md`、`apps/README.md`、`apps/pearnly_cowork/{README.md,hooks.py,pyproject.toml}`、`docs/adr/{README.md,0006-language-management.md}`、`docs/frontend/{01-page-inventory.md,02-first-slice.md}`、`docs/p1/{03-sample-t0.md,05-control-api-and-events.md}`、`docs/p0/01-source-baseline.md`、根 `AGENTS.md` | 改写为已移除，或标注历史 |
| 验证断言 | `tests/test_native_desk_contract.py` 的 Cowork 页数（12→11）、`scripts/sample-verify-frontend-slice.mjs` 里专门验未购买看板的断言（随功能一起移除，其余按功能过滤的断言保留） | 更新 |
| 保留不动 | `docs/p0/02-live-inventory.md`、`docs/adr/0002-deployment-target.md` 里旧 VM/旧盘的运行与退役记录 | 那是旧系统的历史证据，不是搬进新平台的实现 |

审计口径：`git grep -i 'wekan'`、`\bboards?\b`（排除 dashboard/keyboard/onboard）、`nav.work`、`pearnly-work` 四组关键字，覆盖 `apps/`、`frontend/src`、`infrastructure/`、`scripts/`、`tests/`、`docs/`。旧仓 `pearnly-app` 不在本仓，未做改动。

## 删除后核对

```sh
git grep -in 'wekan\|nav\.work\|pearnly-work\|协作看板'   # 只应剩历史证据与本次说明
python3 scripts/i18n-build.py && python3 scripts/i18n-build.py --check
python3 scripts/check-i18n.py --list                      # 本次改动文件 0 条发现
bash -n <被改的 46 个脚本>                                 # 语法未破
python3 scripts/check-architecture.py
PYTHONPATH=apps/pearnly_core python3.14 -m unittest tests.test_native_desk_contract
```

结果：`i18n-build --check` PASS；`check-i18n.py --list` 中本次改动文件（功能目录、导航、DocType 说明、前端导航）**0 条**发现，`--strict` 仍按既有基线失败（本批之前就是 177 条 / 47 文件 / 161 文案，本批未增减该口径）；架构检查 0 违规；原生导航契约用例通过（Cowork 页数 11）。

## 影响与未做

- 已构建/已安装的 Site 上，旧 `Workspace Sidebar` 文档里可能仍留着 `pearnly-work` 项；`ensure_navigation` 重跑时会按新清单重写侧边栏，但**本批没有在 Site 上执行重跑**，也没有删除历史 DocShare/页面记录。旧站点若已存在该 Page 文档，需要一次 `bench migrate` 才会随标准 Page 一起消失。
- 前端依赖未安装（`frontend/node_modules` 不存在），**没有运行 `npm run build`**；本次前端改动是删除一条导航项与一条图标映射，已用 `node --check` 保证语法，但没有类型检查产物。
- 旧系统（`pearnly-app` 与线上 WeKan VM）**未做任何改动**，本仓不发起对旧系统的删除或退役；那属于后续独立授权。
