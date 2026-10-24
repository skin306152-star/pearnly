# ERP 本批集成分支（2026-10-24）

- 用户口径（主会话转）：「全局由你指挥定夺……所有功能必须验证合格且不会影响其它功能的正常使用才能上线，之前发生过改库存盘点的扫码器，
  把其它功能的扫码器改坏了，没任何窗口发现」。本分支只做集成与检查；**不合入 main、不部署**，合入与上线由主会话在回归全部通过后定。
- 分支 / 工作树：`pearnly/erp-batch-integration` / `.claude/worktrees/erp-integration`，基线 `origin/main` = `cecc475e`。

## 合进来的三个分支

| 分支 | 远端 SHA | 内容 | 记录 |
|---|---|---|---|
| `pearnly/erp-express-sync-background` | `3485dd15` | ERP 按 CC 报来的 Express 账套自动建公司、后台主档同步、主档按公司隔离、年度与关账、提交时就问、LINE 超 12 项打字找、等结果三个点、CC「Express 位置」、问题 1–22 | `docs/work/2026-10-23-erp-express-auto-companies.md`、`2026-10-23-erp-express-sync-background.md` |
| `pearnly/cool-bouman-26647e` | `ae175a46` | ERP 任务管理（`work/` 域、15 张 `ERP Work *` 表、`pearnly-tasks` 页）、共用侧栏平铺项、下载文件名 RFC 5987 | `docs/work/2026-10-23-erp-tasks.md` |
| `pearnly/work-followups-d3d5bf` | `bd78c5cb`（修复 `ca079f1d`） | 任务管理遗留四处：Cowork 卡片关注 / 删封面后保存、默认列表跟站点语言、共用页无权限显示原生页、中文 / 泰文下载名 | `docs/work/2026-10-24-tasks-followups.md` |

- 第一个快进；第二个合并时冲突按任务管理记录的办法解：`hooks.py` 的 `scheduler_events["cron"]` 两边都留（本批的 `push.poll` +
  `masters.advance` 每分钟、`masters.check_marks` 每 10 分钟，加任务管理的 `work.reminders.run` 每 5 分钟）；`patches.txt` 两条都留
  （`v1_12_product_code_key`、`v1_12_tasks_feature`）；ERP 术语表两段都留（键不重复），翻译包 `scripts/i18n-build.py` 重新生成；
  其余（CI、架构文档、权限、菜单）自动合并。第三个无冲突。集成提交 `403dcc07`。

## 检查（都对 `403dcc07`）

| 检查 | 结果 |
|---|---|
| 本地全闸门（`development.yml` 全部 117 步，含 `frontend/assistant` 安装与构建、多行步骤） | 通过 |
| `scripts/check-architecture.py` | 通过（0 违规） |
| `scripts/check-copy.py` | 0 拦截；新增 302、改动 0（已登记的 5 句在分支内）、标黄 232；清单见 `2026-10-24-erp-batch-integration-copy-report.md` |
| 生产镜像 `29404c6a` 上完整 `ci-candidate-checks offline`（候选源码只读挂进镜像） | 通过（CAND EXIT 0） |
| GitHub Development checks / Companion checks（`2bba1236`） | 都通过 |

## 集成核对中发现并修的（集成分支上的提交）

- 本批取消了商品编码、条码的全租户唯一，但两处按码认商品没分公司：盘点扫码 / LINE 手机盘点 / 盘点导入用的 `inventory/count.product_of_code`
  （按编码取第一条，可能取到别家公司的同码商品）；库存期初模板与导入 `inventory/opening_batch`（模板列全部公司的商品——实验栈 4.8 万件会超过
  模板行数上限报错；导入时同码判「有歧义」）。改为按盘点 / 期初仓库所属公司只认这家 Express 的档和本地档（这家优先），模板只列这家 + 本地。
  新增 `tests/test_erp_codes_per_company.py`（进候选离线检查）。改后本地全闸门与生产镜像完整候选离线检查再跑一遍都通过。
- 受影响入口回归清单：`2026-10-24-erp-batch-regression-entries.md`（交验收窗口）。
- 验收回归（10-24 晚）报的三处原有问题，这批一起修（都和扫码 / 盘点相关）：
  - A（高）采购 / 销售明细物料格扫码枪会悄悄填错物料：回车到达时下拉还是获得焦点时的默认列表，选了它高亮的另一件。`entry-choice.js` 改为：回车时下拉不是按这串字查出来的，就按这串字查（物料按编码 / 条码准确查），只有一个才填，否则只刷新下拉不替人选。
  - B（中）入库 / 出库物料格扫码枪丢码：`transactions.js` 的 `scan_by_code` 看到旧列表的高亮项就放行。改为回车一律先按码查，查不到才照原生回车选此刻高亮项。
  - C（中）同一浏览器网页登录着、打开手机盘点页后网页变成未登录：`line_mobile.as_ticket_user` 在已登录请求里 `frappe.set_user` 就地改了会话，请求结束框架写回缓存。改为票据上的人就是当前登录的人时不换；不是时换新会话对象、不写回（同 `cc_desktop.http_auth`）。
  - 实验栈内置浏览器真实按键复核（只填表不提交）：A 扫 8850187700031 填 ฉัตรอุบลม่วง 1กก.、打名称等下拉刷新后回车仍选高亮项；B 11020 + 回车填入编码 11020 的物料；C 打开 /stocktake 后 `get_logged_user` 仍是原账号。
- 验收问题 23（中高，本批「服务端拒绝跨公司」的漏口）：门户自己的单条读写接口（商品打开 / 修改 / 停用 / 加单位、客户打开 / 修改、供应商修改）
  自己 `frappe.get_doc`，不经 `has_permission`，只开了 CO-00001 的员工读到了别家公司的商品。新增 `masters.access.require_master`，
  `products.service._get`、`masters.customers._get`、`purchasing.suppliers.update_supplier` 都过它；用例进 `tests/test_erp_codes_per_company.py`。
  实验栈以该员工只读直调：别家的两件商品被拒、本地商品照常。
