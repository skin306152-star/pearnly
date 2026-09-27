# Express 存货科目组：每个账套选一次（2026-09-28）

分支 `codex/express-stock-acc-group`，基线 `100c4f7f`（= origin/main）。交接见 [交接](2026-09-28-express-stock-acc-group-handoff.md)。

用户 2026-09-28 口径：科目规则由会计定一次、日常自动套用、复核时看；存货科目组不是每张票选，而是**每个账套选一次**，
以后在这个账套新建的库存品都挂这个组。0 个合格组仍拦（要会计先在 Express 建组）。

## 改了什么

| 位置 | 内容 |
|---|---|
| 存储 `Cowork ERP Connection.stock_acc_groups`（新隐藏字段） | `{账套键: {acccod, chosen_by, chosen_at}}`。与连接配置分开：不升连接版本、不作废账套目录、管理员保存连接设置不会冲掉；换账套不用清。 |
| 规则 `targets/stock_groups.py`（纯函数） | 合格组（小助手判 fit）、按账套取所选、选择只收这份账套上报的合格组、给入口的视图（`known` = 小助手读过这份账套没有）。推送判据 `push/express/stock_acc_group.py` 的合格名单与标签改为用它（同一份）。 |
| 候选来源 `connections/preparation.py::reported_stock_groups` | 与推送准备同一来源：默认账套用记在它名下的公共缓存，其他账套用按账套刷新带回的缓存；须出自当前凭据，否则算「不知道」。 |
| 接口 `connections/api.py` | `stock_acc_group(name, account_set_key)`（GET）、`choose_stock_acc_group(name, account_set_key, acccod)`（POST）；权限同推送（可用这条连接的人）。 |
| 推送 `push/application/service.py::_apply_stock_group` | 推哪份账套就把那份账套选过的组放进 `config.stock_acccod`。映射器不变：有组就带给小助手；零存货主档账套没选过仍按候选数（1 个自动用 / 0 个拦 / 多个拦）。 |
| 网页新建录入 `public/desk/intake-choices.js` | Express + 存货 + 选定账套：合格组 ≥2 且没选过 → 出一个原生下拉「存货科目组」，选了才能「继续」，继续时存回账套。 |
| 网页提交 `public/desk/intake-upload.js` | 兜底：新建时小助手还没读过这份账套（候选未知）、LINE 来的票、复核里改成存货的票，提交前同样原生弹窗选一次；关掉弹窗 = 这份账套的票这次不推。 |
| LINE `line/flow.py` | 选了 Express + 存货后同一规则：点序号选一次存回账套；候选未知或多于 11 个不问（网页提交前再问）。 |
| 小助手 `stock_template.pick`（1.2.4） | 收到 `stock_acccod` 时新建库存品挂所选组（结构抄同组真品；该组下还没品就抄众数那类结构、换成所选组），照样过存货科目守门；没收到照旧按众数克隆。 |

有意变化（相对旧线上）：旧版存货科目组是端点级单值、只在零存货主档的失败卡里选；现在按账套存、在录入入口选，
有存货主档的账套也按所选组建新品（旧版按众数克隆）。旧版端点级 `stock_acccod` 仍认（仅当前默认账套，换账套清掉）。

## 验证

- 本机：`tests/test_cowork_stock_acc_group_choice.py` 8、`test_cowork_push_account` 6、`test_cowork_line_intake` 26、
  `test_cowork_account_caches` 5、`test_cowork_targets` 39、`test_cowork_push_purity` 2、Express 映射器 / 预检 85 通过
  （Frappe 16.34.1 样板镜像，`--network none`）；Express 规则与旧线上源码对照 963 例 PASS（含存货科目组 15 例）；
  小助手 `test_stock_template` 等 48 通过（本机 Python 3.9，缺 httpx 的 1 条与需 Windows 的 11 个文件以 Windows CI 为准）。
- 架构检查、i18n 生成检查、前端 i18n、Desk 原生契约通过；`check-i18n --strict` 的失败是存量（主线同为 541 条）。
- 待补：分支 CI、生产镜像候选离线检查、真机（测试账套 70EXP\TEST）、部署。
