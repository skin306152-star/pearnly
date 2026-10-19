# MR.ERP 推送方向按所选公司的税号判（T2，2026-10-19）

分支 `pearnly/mrerp-direction`，基线 `4277e95c`（= origin/main，多电脑与数字会计员第 3 期都已在内）。
来源：开发交接 2026-10-18 F1；Express 同类修复 `2b825a00`（`contracts.push.resolve_account_direction`）。

## 现状（改之前）

- 推送准备 `push/application/service.py::_prepare_mrerp` 只取 `Cowork Workspace.tax_id`，写进 `mappings["_own_tax_id"]`；
  方向在执行时判（`push/mrerp/routing.py::choose_doc_type`，只用 `resolve_direction` = 明确方向 > 工作空间税号）。
- 匹配闸 `routing.confirmed_account_set_mismatch` 与 `transport._filter_by_account_set` 也只认 `_own_tax_id`。
- 事务所替客户记账时，工作空间税号不是票上的买方 / 卖方：没有明确方向的票判不出方向，按默认赊销模块走，匹配闸看两边税号都不是
  工作空间的，拦成 `ERR_ACCOUNT_SET_MISMATCH`。快递公司开给客户的无买方简易税票同样被拦（不是费用类的那部分）。
- 目录里**没有**目标公司税号：MR.ERP 选公司页只有简称 / 全称 / 备注 / 年度库名（`connections/mrerp_catalog.py`），
  公司主档的 MR.ERP 身份是 `mrerp:{连接}:{公司代码}`、`tax_id` 恒为空（`companies/identity.py`）。所以不能照 Express 从目录行
  `attributes.tax_id` 取。

## 税号从哪来（MR.ERP 源码只读 `/Users/skin/mrerp-source/src` 核实）

- 公司自己的税号登记在本公司分店档案 `tbl_othcombrh.taxid`；总店那一行（`statusmain`）是 MR.ERP 打印税票表头用的同一个值
  （`component/php/showreportfile.php:27`）。
- 读法：`combrh/allform.php?id=<总店 id>&status=vie`（查看页，`<input id="txttaxid" value="…">`）。不带 `idmenu`，
  `sessionmenu.php` 不改会话里的当前菜单；`checkuseruse.php` 只查登录，不查菜单权限。
- 总店 id：推送本来就读的分店列表（`fabillrev/component/bshlistboxdata.php` 的 `txtothcombrh`，`[id, 代码, 名称]`）按
  推送本来就读的总店代码（`artran/allform.php`）对出来；读不到总店代码时只有一行才用。
- 税号是人填的自由文本（样板库 `stderp2020` 总店填 `8-888-888-88888-8`、分店填 `1`）：只认 13 位、校验位对的，
  其余当没登记。

**未核实**：这页在线上 MR.ERP（mrerp4sme.com）的实际页面没有连上去看过（本任务不连外部系统），只按源码与假站点测试。
读不到 / 页面不同 / 没登记 → 不放锚点，行为与改之前完全一样。

## 改法

| 位置 | 内容 |
|---|---|
| `targets/mrerp_masters.py` | 纯函数 `head_branch_id`（分店列表里总店那行的 id）、`parse_company_tax`（查看页的税号，只留数字）。 |
| `connections/mrerp_master_reader.py` | `MasterReader.company_tax()`：同一会话读一次；只在导入页打开之前读。 |
| `push/mrerp/transport.py` | `upload_routed_batch`：有票没有明确方向时，登录选好账套后先读公司税号，有效就放进 `mappings["_account_tax_id"]`；都有明确方向时不读。匹配闸 `_filter_by_account_set` 两个锚点有一个就启用。主档读取器在会话内共用（`_reader`）。 |
| `push/mrerp/routing.py` | `account_tax`（13 位且校验位对才算）；`choose_doc_type` 改用 `resolve_account_direction`（明确方向 > 工作空间税号 > 公司税号，同 Express）；`confirmed_account_set_mismatch` 加 `account_tax_id`，方向同一套判定，判不出时票面税号两个锚点都不是才拦。 |
| `push/application/service.py` | `_prepare_mrerp` 只补注释：`_own_tax_id` 照旧冻结工作空间税号，公司税号执行时读（目录里没有）。 |

## 有意变化

- 没有明确方向、工作空间税号判不出的 MR.ERP 票，按这家公司在 MR.ERP 登记的税号判：买方是这家 → 进项（货 / 费照旧按判据分）；
  卖方是这家 → 销项；票上没有买方、卖方是别家（校验位对、与公司税号至少差 3 位）→ 进项。对自己记账的工作空间也生效
  （无买方的别家简易税票以前被拦，现在按进项推，与 Express 一致）。
- 匹配闸认工作空间税号与公司税号两个锚点：替客户记账的票不再被误拦；买方是别家、卖方也不是这家的票照旧拦；判出方向却推进相反模块照旧拦。
- 推送会话里多一次只读 GET（仅当有票没有明确方向时）。
- 不变：明确方向优先；公司没登记 / 登记的不是有效税号 → 与改之前一样；卖方税号与公司只差 1–2 位的无买方票判不出，照旧转人工。
- 已知限制（改之前就有，未改）：判出进项、入口选了「现金」的票，MR.ERP 只有赊购模块，照赊购推（准备时只挡明确声明进项 + 现金的）。

## 测试

- `tests/test_cowork_push_direction.py` 新增 `MrErpDirection`（纯规则，CI 跑）：进项、销项、替客户记账（工作空间税号≠票面买卖方，
  公司税号 = 买方 / 卖方 / 快递无买方票，对照改之前被拦）、公司无税号或无效税号回落、税号相近不判、明确方向保留、别家票照旧拦。
- `tests/test_cowork_mrerp_masters.py`：总店 id 与税号解析。
- `tests/test_cowork_mrerp_push.py`（要 Frappe 环境，本机镜像容器里跑，CI 不跑）：假 MR.ERP 站点上事务所工作空间推客户的票落到
  采购导入、读的是总店 id 1；公司没登记税号照旧被拦；全部有明确方向时不读公司税号。另在同一镜像跑过 mrerp_menus / readback /
  push_fixed / folder_intake / assistant_tools / folder_tasks_mrerp / xlsx_source_parity，均通过。
- 未改 CC 桌面版与写入契约（MR.ERP 推送在服务端），`tests/test_companion_contract.py` 随全闸门跑。

## 外部写入授权

- MR.ERP 推送挂在 Cowork 租户上（testcowork 有一条 Express、一条 MR.ERP 连接），与新平台 ERP 产品的测试租户 testerp 无关；原稿把实测写在 testerp 是错的，已改正。
- 2026-10-19 用户在开发窗口同意（原话「同意」）实测时对 MR.ERP 测试公司 TEST2019 的两样外部写入：推 2–3 张测试单；总店没登记有效税号时先在「ข้อมูลบริษัท / สาขา」总店登记一个有效税号。其余 MR.ERP 数据与设置照旧不动；测完列出写了哪几张、改了什么。

## 实测（在 testcowork 的 MR.ERP 连接上做；本任务不上测试机）

前提：testcowork 的 MR.ERP 连接（`CWEC-283edc03…`，MR.ERP 测试公司 test01 · TEST2019）连的 MR.ERP 公司在「ข้อมูลบริษัท / สาขา」总店登记了真实有效税号；工作空间税号与该公司不同（模拟事务所）。

1. 票面无买方的进项票（快递 / 便利店简易税票，卖方是别家），入口**不选**进 / 销项，推往这家 MR.ERP 公司：应进采购（或费用）菜单、
   不报 `ERR_ACCOUNT_SET_MISMATCH`；MR.ERP 里能查到单据。
2. 替客户记账的票：票面买方 = 这家公司税号、卖方是别家，不选方向：应进采购菜单；再推一张卖方 = 这家公司的销项票：应进赊销 / 现销。
3. 对照：把总店税号清空（或换一家没登记税号的公司）重推一张同类票：应照旧被拦（`ERR_ACCOUNT_SET_MISMATCH`），说明回落正确。
4. 核对推送日志里 `[mrerp-route] groups=` 的分组与 MR.ERP 实际落的菜单一致。
