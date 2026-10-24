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
- 验收问题 24（高，写错账）：同一供应商记住了「费用科目」（电费 5330-02）后，再开一张维修费单（明细认出 Express 费用类物料 5320-02，
  Express 里这类条目 STKTYP=3、编码就是科目号），载荷仍按记住的电费科目记，STKCOD 与总账都成了 5330-02。修法（按类，采购与销售同）：
  - ERP `express/prepare._own_accounts`：服务行认出的 Express 物料编码在这份账套的科目表里时，这一行带上自己的科目 `account`；服务行都有自己的科目时
    不再问费用科目、也不套用按供应商记住的（`account_resolved`）。销项服务行同样带收入科目。
  - 共用写账规则：`common.extract_line_items` 带下行的 `account`（OCR 的票没有这一键，Cowork 不变）；`common.split_by_account` 把费用（借）/ 收入（贷）
    按行科目拆、同科目合并；`mapper` / `sales_mapper` 用它重排分录，行改直接科目行（STKCOD = 它自己的科目），分录标 `role`。
  - 分录带 `role` 的载荷版本升到 5（`ROLE_PAYLOAD_VERSION`）：旧版 CC 按位置认分录会把第二条费用当进项税，用版本号让它诚实拒收；CC 1.12.0
    `payload_contract` 认 5，`purchase_lines.journal_rows` / `dbf_sales._gl_rows` 有 role 时按角色写（费用 / 收入可以有几条），没有 role 照旧按位置。
  - 用例：`tests/test_erp_express_direct.py`（记住电费后开维修 + 运费：维修记维修、运费记记住的；只有维修时不问；销项按收入科目拆）、
    `companion/tests/test_payload_contract.py`（按角色写、旧载荷按位置）。实验栈对 PUR-2026-167878 只准备不写：借 5320-02 250、明细 5320-02、版本 5。
  - 验收问的货物采购那对同科目借贷（1140-02 借 / 贷 1000）：不是 Express 原生记法，是 CC 存货重分类在「购货科目 = 存货科目」时写出的空对冲，余额不受影响；
    这处会改到 Cowork 现有库存采购的写法，这批不动，并进出入库方案第一期配 62 套离线回归一起改。
- LINE 选择统一规则（用户 10-24「若12个以下的是不是就不需要关键词这一步了？」，主会话列七条，ERP 与 Cowork 同）逐条核对后补齐：
  - 匹配结果上限从 11 改成 12（按钮 12 个另加「取消」，正好是 LINE 的 13 个上限）；匹配超过 12 个只请多打几个字、不再列前几个。
  - Cowork 选账套：原来只要 2 份以上就一律打字找，改为不超过 12 份直接列序号按钮（沿用「选择公司」文案），超过 12 份才打字找、上次用的给按钮；
    打了字没找到时照原样再列 / 再问。存货科目组正好 12 个也列按钮，超过 12 个才打字找。
  - ERP 选供应商 / 客户：本公司能选的都已在按钮里时不再带「输入…名称关键字查找」（`_more_parties`）。公司、仓库原本就按 ≤12 列按钮、>12 打字找，当前公司 / 上次用的仓给按钮。
  - 每档用例：`tests/test_line_search_and_loading.py::ErpChoiceTiers`、`tests/test_cowork_line_intake.py`（12 / 13 份账套、12 / 14 个科目组、找到 0 / ≤12 / >12、整串编号）。
- 收尾核对（10-24 晚，验收第 3 项「自动建本地商品同步后会不会重复」测的那张单碰巧认到了 Express 现成物料，没走到要查的路径）：
  认不到的货物行提交时建本地档 → CC 在 Express 里按品名找不到就新建存货（新 STKCOD）→ 几家公司的租户主档同步不认领本地档（本批改的）、
  服务端也没存 CC 回执里逐行用的存货码 → 同步按新码另建一条同名档（同问题 13 的客户 / 供应商漏口）。修法同 `link_party`：
  - 共用 `cc_desktop.jobs.acknowledge` 把回执 `line_modes` 里的 `seq / name / stkcod` 存进 meta 的 `line_codes`，`view` 交给产品；CC 不用改（1.12.0 本来就报）。
  - ERP `express/push.link_products`：第一次写进去时，按序号且品名一致对上行，只被这张单用过的本地商品挂上这家公司和这个码；这家公司已有挂这个码的档时不动
    （库存流水已记在本地档上，不改指）。用例 `tests/test_erp_express_direct.py::LinkProducts`、`tests/test_cc_desktop_channel.py`（逐行存货码）。
  - 生产镜像 `29404c6a` 完整候选离线检查（`c4ad23d9`）：通过（CAND EXIT 0）。这一遍先发现 4ee7eac4 留下的用例缺口：`integrations/test_line_intake.py`
    的假数据库没答对方档条数（`_more_parties` 新查的），2 处报错；补上用例数据后通过，产品代码未改。本地全闸门（`4baaeffe`）通过。
  - 验收复测（84d8ff9b）：选「新建」的 PUR-2026-167900 → HP691024-010（CC 新建 PN00002），本地档 PRD-167899 回执后挂上 CO-42069 / PN00002，同步后只有 1 条；
    选现成物料的 PUR-2026-167904 → HP691024-011，本地档不动，与修前同。本轮回归（ERP / Cowork / DMS 三段 + 切回 ERP 补测）全部闭环。

## 交付状态（10-24 晚）

- 集成分支最终 `84d8ff9b`（代码最后一次改动 `c4ad23d9`）；GitHub Development checks 通过；Companion checks 最后一次在 `000a26d8`（之后没改 `companion/`）通过。
- 本地全闸门（`4baaeffe`）、架构检查、生产镜像完整候选离线检查（`c4ad23d9`）通过；文案闸门之后没有新增 / 改动文案。
- CC 桌面版 1.12.0 测试包（`000a26d8` 构建）已在测试机；正式版要在合入 main 后发布（载荷版本 5，旧版 CC 会诚实拒收按行科目拆的单）。
- 未合入 main、未部署。测试机实验栈仍在 ERP（testerp / testcowork / testdms 三个域名都还指向测试机），收尾时 `lab-stop erp cowork dms` 切回线上。
- 观察（不是本批引入，交主会话定）：ERP 的 bench 没设 `scheduler_tick_interval`，Frappe 默认 240 秒一拍，`masters.advance` / `push.poll` 写的「每分钟」实际约 4 分钟一次
  （实验栈 15:32 / 15:36 / 15:40 / 15:44 才跑；写单后自动同步要等一到两拍，验收见到 4 分钟）。DMS 已按 ADR-014 设成 60 秒（`configure-dms-runtime.py`）；
  ERP 照做要改生产运行配置，随部署一起做。

## ERP 定时节拍改 60 秒（主会话 10-24 批准，随本次部署）

- 改法同 DMS（ADR-013 / ADR-014）：新增 `scripts/production/configure-erp-runtime.py`（只写 `scheduler_tick_interval=60`），
  `activate-product.sh` 激活 ERP 时、`site-step.py` 开 ERP 户时、测试机 `finish-start.py`（erp 组）都调用；Cowork 不变（仍 240 秒）。
  用例 `scripts/production/test-erp-runtime.py`（进 CI）、`test-activate-product.py`（只有 ERP 激活调用、在迁移之前）。
- 单拍负载（ERP bench 装 frappe / pearnly_core / pearnly_erp_portal；实验栈 3 小时 worker 日志，中位 / 最大）：

| 每拍都跑的 | 耗时 |
|---|---|
| `express.masters.advance`（每分钟 cron，41 家公司） | 0.18 / 0.20 秒 |
| `express.push.poll`（每分钟 cron） | 0.14 / 0.17 秒 |
| `execution.work.sweep`（core，每分钟 cron） | 0.05 / 0.07 秒 |
| Frappe「All」4 项：`monitor.flush`、`google_calendar.sync`、`email.queue.flush`、`retry_sending_emails` | 各 ≤ 0.06 秒 |

  每站点每拍约 0.6 秒后台时间；生产 ERP bench 两个站点（mrerp、testerp）每分钟约 1.2 秒，可以接受。其余 cron（5 / 10 / 15 分钟）按自己的表达式跑，频率不变。
- 实验栈验证（15:51 UTC 改，重启 scheduler）：`advance` / `poll` / `sweep` 在 15:52:00、15:53:43、15:54:43、15:55:43、15:56:00 各跑一次（约 1 分钟一拍）；
  之后 Error Log 0、失败的 Scheduled Job Log 0、worker 日志无报错。

## 主会话 10-24 决定（用户授权主会话定夺）

- 批准合入 main 并部署；ERP 节拍 60 秒随这次部署（见上节）。回退目标：生产镜像 `29404c6a`（main `3205521e`）。
- 位置外 6 本账套本批不加代码：ccdemo、testcowork 是内部演示 / 测试租户，升级后按操作清单重新处理（部署后另报）；testcowork 的 S 盘 / 212 两本按「只用 C 盘」不恢复。
  「同一本账套换了路径写法（自己的 C$ 共享 ↔ 本地路径、映射盘 ↔ UNC）自动认回、不用人重选」记为通用改进，并进出入库那批。
- 已知低项去向：CC `request_worker` 遇 502 无退避 → 下一批（和上一条一起改 CC）；1140-02 空对冲、负库存后补货再卖的成本 → 出入库方案第一期；
  账套打税号找不到（服务端没存 CC 报的 tax_id）→ 下一批。
