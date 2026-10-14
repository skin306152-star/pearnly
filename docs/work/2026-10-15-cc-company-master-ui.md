# CC 公司主档：前端包（B）

- 分支 `pearnly/cc-company-ui2`，基线 `9f434323`（`pearnly/cc-night-batch`）。只在本地提交，未推送、未合入、未部署。
- 规格：`~/pearnly-devtools/night-2026-10-15/company-master-spec.md` §10–§12、§14 B 节，补充 `company-master-amend.md` 甲（页面）、乙（页面）、丙。
- 服务端（A，`pearnly/cc-company-core`）与 CC 桌面版（C，`be797d1c`）不在本分支；本包按 §4.2 / §12.2 与补充的字段名用假站点测。
- 总任务记录由 A 包在 `docs/work/2026-10-15-cc-company-master.md` 维护；本文件只记前端新增的入口与证据，集成时并入那份。

## 新增与改动的产品入口（只在 Cowork：共用层 `projects()` 的 `kind = "company"`；ERP / DMS 不出现）

| 入口 | 用途 | 实现 | 需要的配置 / 角色 | 点击去向 | 本地证据 |
|---|---|---|---|---|---|
| 侧栏「待处理 N」（定时任务下面，N=0 不显示） | 要人处理的单统一入口 | `assistant.tsx` `CompanyNav`、`lib/companies.ts` `watchPendingCounts` | A 的 `companies.api.pending_counts`；Cowork 录入权限 | 待处理视图 `?view=pending` | 源码检查；计数形状 `parseCounts` 行为测试 |
| 侧栏「公司」 | 全所公司主档 | `components/companies.tsx` | A 的 `companies.api.companies`；`READ_FEATURES` 任一 | 公司页 `?view=companies` | 源码检查 |
| 侧栏公司组（组头名、⋯、＋、待处理数） | 我有对话的公司 | `components/assistant-ui/projects.tsx`、`lib/managed-agents/{projects,sidebar-groups}.ts` | 共用层 `projects` / `listing grouped=1` / `create` / `move` | 名字 → 主档页；＋ → 这家的新对话（上个月）；数 → 这家的待处理 | `CompanySidebarBehaviourTests`、`test_sidebar_sections_put_pinned_companies_then_recent` |
| 添加公司对话框 | 从账套目录建主档 | `components/add-company.tsx` | `companies.api.candidates/create`；目录空时 `connections.api.request_refresh` | 建好 → 主档页（或回填到公司条 / 移到） | 源码检查 |
| 公司主档页 `?company=` | 按公司按月开对话、知识库、文件夹、账套、任务、草稿、记住的选择 | `components/company-page.tsx`、`components/period-chip.tsx` | `companies.api.detail` 及写接口；文件夹只在 CC 桌面版 1.6.0+ | 对话 / 草稿 → 对话页；任务 → 定时任务页 | `CompanyPageBehaviourTests` |
| 待处理视图 `?view=pending[&company=]` | 当场选 / 交给 CC | `components/pending.tsx` | `companies.api.pending`；选项写入复用 `push.application.api.save_choices` | 交给 CC → 新对话（预填、不发送） | `test_pending_choices_…`、`test_choosing_in_place_…` |
| 对话菜单「移到 ▸」、顶栏「公司 ›」与月份 / 分店胶囊、手机胶囊「移到…」「处理月份」 | 对话换公司、换月份 | `projects.tsx`、`assistant.tsx` | 共用层 `move` | — | `test_moves_renames_and_deletes_merge_at_once` |
| 新对话输入框上方公司条 | 新对话挂在哪家、哪个月 | `projects.tsx` `ComposerCompany`（放在 `Welcome` 里，不改 `thread.tsx`） | 至少有一家公司 | — | 源码检查 |
| 定时任务页 `?view=tasks&company=` | 只看这家的任务、预填新建 | `components/scheduled-tasks.tsx` | `detail.tasks` | — | 源码检查 |

去掉的入口：设置「此电脑 → 网页可读取的文件夹」（桌面版声明 `company_folders` 时）、网页「连接」每台电脑下的文件夹清单。旧桌面版保留旧块过渡；`legacy_roots` 不为空才出「以前授权的文件夹」。

## 待集成者在测试机 / 真机验证（本包只做了离线检查）

见交付说明（结构化结果的 live_verification）。HTTP 200、页面能打开不算验收。
