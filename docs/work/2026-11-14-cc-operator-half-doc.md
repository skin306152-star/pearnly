# CC 桌面版：代录半截单按类修、位置只选几本、窗口超屏（11-14）

分支 `pearnly/cc-desktop-half-doc-1ee3e3`，基线 main a838abe54。新增文案只有原因码 partial_saved 的四语术语（新文案，未改已有文案）。

## 第一件：代录保存中途认不出的框（半截单）

### 根因（查清的）
1. 保存后转人工的单，批次收尾 `finish` / 闲置 `idle` / CC 重启收拾孤儿 都会 `session.close`（WM_CLOSE）→ Express 被关时存档停在框上 → 半截单。
2. 「回报不带单号」：CC 其实在 meta.operator.placeholders 带了半截单号，但**服务端 `push/common.clean_operator_meta` 只放行 doc_type、menu_options**，其余全丢；两产品 status() 对 not_done 也不回单号。
3. 判「存了没有」只看单头含税合计，不看凭证 / 明细 / 税。

### 半截单可靠判定（原生证据）
- 测试机2 70EXP\test IV0000045（11-13 原生）：ARTRN 单头在、NETAMT 0、SRV_VATTYP '-'；STCRD 1 行；GLJNL / GLJNLIT / ISVAT 都无。
- 本机 69EXP\TEST 副本（~/pearnly-devtools/collection-lab/run1113/re_t1、re_t5）：有效 IV 28 张、HS 13 张、RE 12–17 张**每张都有 GLJNL.VOUCHER = DOCNUM 与 GLJNLIT**；作废的（DOCSTAT C）NETAMT 0、凭证还在。
- 判定（`book.gaps`，四种单同一口径）：开录后新出现、对方+日期+对方单号对上、没作废的单头 → 缺 total（含税合计不符）/ lines（STCRD 行数不够）/ journal（无同号 GLJNL）/ vat（有税且非服务品收付实现却无 ISVAT）任一 = 半截；都不缺 = 已完整。GLJNL 只在出事 / 再交时读（表大）。
- 代录只有 RR / HP / IV / HS 四种单（收款 RE 不走代录，直写半途失败整单回滚，已有测试 test_a_failure_half_way_rolls_everything_back），四种都覆盖。

### 修法（CC 侧，已写）
- `engine._after_save_trouble`：先读账套（含凭证）→ 已完整回单号；半截 → 原因 `partial_saved` + `partial {docnum, missing}`；都标 `recheck` + `held`，**不碰 Express**。
- 放弃（保存前）时认不出的框留在编辑中的单上 → 也 `held`。
- 批次：`held` 落盘；`_close` 在 held 时不关（finish / idle / run 收尾全覆盖）；同批后面的单、同一本账套别的批次 → 回「等账套」(account_busy，waiting_lock)；建主档要关 Express 时抛 Busy；CC 重启收拾孤儿跳过 held 的 Express。
- 放手条件（只读不点）：held 那张在账套里已完整（人答完存好）→ 记已写入回原单号；或 Express 上认不出的框没了 / Express 没了 → 放手，那张仍等人。
- 再交：带 recheck / 半截 / 空单头的单再交一律先读账套：完整回原号、半截带单号交人、确认一行都没有才从头录。
- 配方外业务选择框（两个以上非 ตกลง/ยกเลิก/OK/Cancel 的钮，`engine.is_choice`；win32_desk.windows 新增 buttons 列）：`_recover` 不交 AI 直接转人工；AI 工具层（点、键、打字、拖、菜单）发现开着就抛 ChoiceBox → 转人工（helper RAISE_THROUGH）。
- 回执 meta.operator 新增 `partial`、`held {computer, account_set}`，`docnum` 填半截单号。
- 顺带：服务品收付实现（srv_vattyp 2）读回不再要求 ISVAT（原生不写）。

### 服务端（同分支，已写，等主会话定是否这样）
- core contracts 加原因 `partial_saved`（HUMAN）；四语术语（新文案）：zh「Express 里有一张未存完的单」/ en「Document partly saved in Express」/ th「มีเอกสารบันทึกไม่ครบใน Express」/ ja「Express に保存が完了していない伝票があります」；messages.tsv、translations、assistant 前端同步。
- `clean_operator_meta` 放行 partial（docnum≤20、missing∈{total,lines,journal,vat}）与 held（computer、account_set ≤60）。
- 两产品 status() 对 not_done 显示半截单号：已做（接缝 c）。
- 跨边测试：tests/test_companion_contract.py `test_the_half_document_receipt_keys_are_the_ones_the_server_keeps`。

### 离线测试
- 新 `companion/tests/test_operator_half_doc.py` 12 条（四种单 × 保存中途业务选择框；工具层拦；收尾 / 闲置 / 别批次不关；人答完再交回原号且不出第二张；关了 Express 再交不重录；判定单元；宿主回执；重启不收拾 held）。
- 假 Express：GLJNL、保存途中问业务选择（先写单头 0 + STCRD，同 IV0000045 原生形状）、buttons。
- 改了 2 条旧测试的期望（原因由 placeholder_left/unknown_screen 变 partial_saved 的场景）。

## 接缝（唯一一份约定，负责人：本窗口；跨边测试 tests/test_companion_contract.py）
a. 原因码 `partial_saved`（HUMAN）：保存途中停住，账套里留了这张的半截单。core `express_operator/contracts.py` 与 companion `express_operator/contract.py` 同值（tests/test_express_operator_shared.py 比对）；四语术语（新文案）zh「Express 里有一张未存完的单」/ en「Document partly saved in Express」/ th「มีเอกสารบันทึกไม่ครบใน Express」/ ja「Express に保存が完了していない伝票があります」，messages.tsv + 翻译包 + assistant 前端。
b. 回执 `meta.operator`（CC worker 把宿主 meta 放进这里）新增两项，服务端 `push/common.clean_operator_meta` 只放行这两项的这些键：
   - `partial = {docnum: ≤20 字, missing: ⊆ ["total", "lines", "journal", "vat"]}`（`OPERATOR_PARTIAL_GAPS` ≡ CC `book.GAP_*`）；
   - `held = {computer: ≤60, account_set: ≤60}`：Express 原样留着等人答框的电脑与账套。
   CC 另把半截单号放进回执 `meta.operator.docnum`（原有键）。
c. 两产品对未完成（not_done）的单只多显示这个单号：`common.operator_partial_docnum(meta)`；Cowork `operator_source.status` 读推送记录 `response_json.meta`，ERP `express/operator.status` 读写账件 `Pearnly CC Job.meta_json`。不加说明句、按钮、确认步骤。
d. 等待：Express 留着等人期间，同一本账套的代录单 / 要先建主档的单回 `waiting_lock` + `error_code ACCOUNT_BUSY_LOCKED`（现成的「Express 正在使用此账套」，`ack_rules.EXPRESS_OPEN`：这本账套歇 30 秒再派、别的账套照常领），不另起状态、不加文案。
上线顺序：服务端提交先部署（旧 CC 不发这些键，净化与显示都是空，行为不变）；CC 后发。服务端遇到旧 CC 照旧；新 CC 遇到旧服务端：原因显示「未知原因」、单号被净化丢掉，不坏事。

## held（Express 留着等人）的三点
1. 排在后面的单：只影响同一本账套，见接缝 d；直写遇到 Express 开着这本账套本来就等，口径一致。CC 重启后内存里没有那一批，也凭本机 Express 记录（进程还在 + 批次标 held + 同账套）照样等。
2. 人一直不来：CC 永远不关、不点那个 Express。收场——人在 Express 里答完、单存完：账套读到「已完整」即放手、记已写入；Express 被人关掉 / accserver 关机后进程没了：放手，那张保持半截 / 没写（再交按账本实际状态判）；框和 Express 都还在：一直算占着这本账套（同人自己开着 Express），只挡这一本，不占整条队列。不加超时自动收场（超时只能关 Express 留半截或替人点框，都违反管钱底线）。
3. 结果回报：转人工当时回 partial_saved + 单号 + 缺什么；人答完框后用任务里现成的「重试 / 整批继续」再交，CC 只读账套：已完整回「已写入 + 原单号」（服务端从未完成变已写入），半截带单号再回未完成，一行都没有才从头录。CC 不主动补报、不加按钮。

## 第三件：窗口超出屏幕
- `desk.fit_rect`（纯函数）+ `win32_desk.fit`：主窗口没最大化又超出 → 铺满工作区；别的窗口（报表、弹窗、单据）比工作区大就缩、再整个挪进所在显示器工作区；SetWindowPos 不激活、异步（后台做法照样能用）。
- 引擎在开单据窗口前、AI 接手看画面前、转人工截图前各调一次。测试 `tests/test_operator_fit.py`。

## 第二件：位置只选其中几本
- 位置可以直接选年度里的某本账套目录（如 P:\69EXP\TEST）：`express_places.reaches`（年度在位置里或位置在年度里才读）+ `row_in`（上报只留数据根在位置里、或账套目录在位置里的行）。
- 改到的点：worker.scan_roots / places_scope / 补数据根两处、express_machine.places_roots（选的是账套就取所在年度）/ rescan_roots、desktop_settings 添加 / 移除 / 设置页年度列表。
- 启动缓存（account_sets_<档案>.json 旧 39 本）：上报前过 places_scope，只剩所选（测试覆盖）。
- 不涉及服务端接口（上报形状不变）。测试 `tests/test_place_pick_books.py`。
- 界面：沿用「添加文件夹…」，选账套目录即可，一本一本加；没加新文案。

## 证据分层
- 实现 + 本地离线测试：完成（CC 全量通过；仓库全闸门见提交时记录）。
- 测试机真跑：**没做**（测试机 1 / 2 开机后排队做：70EXP\test 造配方外选择框、位置只选 69EXP\TEST、150% 下报表窗口）。
- 部署 / 发版：没做。
