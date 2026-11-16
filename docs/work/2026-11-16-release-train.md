# 上线车 11-16（集成分支 pearnly/integration-1116）

拼车窗口维护。部署不由本窗口发起：主会话外部核对后合入 main、打标记，部署前先告诉用户。
线上现为 `72516d120`（标记 `release-20261115-1126`，11-15 19:06 部署完成）。
这条集成分支装「命中只能真机的入口、要同一提交带真机记录才过产品地图闸」的改动；不命中的由合入队列直接合 main。
基线：`origin/main` 0c87836b7（11-15 21:3x）。

## 这一车装什么

| # | 内容 | 来源分支 → 头 | 并入状态 | 真机要求 |
|---|---|---|---|---|
| 1 | 物料 / 客户 / 供应商选择框账本档优先；搜索统一（采购 / 销售对方与物料框、网页收款客户框、LINE 收款同一条查询，电话带不带分隔符都认，税号命中不被过滤，LINE 回显选中的名称；LINE 选公司提示句已批文案） | link-ledger-code-first f971c8579 + master-search-unify → a15f8c1cf（作者 11-15 晚确认这两个 SHA，顺序 1 → 2；作者本机已过受影响流程 8 条、单测 6 组） | 已并（快进） | `erp.scan.line.stocktake.liff-camera`、`erp.line.postback.erp_item`（产品地图闸要求） |
| 2 | 收款日期可改（当天不在任何一本期间时改问日期；LINE day 步、网页弹窗日期、CC 工具 date / date_needed；隐藏字段 date_chosen） | collection-date 465b5fc7a → 变基后 1fe229338（基于 a15f8c1cf；作者解了 `_after_company` 冲突，认对日期后不再带「No match」并补测试） | 已并（52105b5cc） | 合入前测试机1 NLTTEST 原生对照；LINE / 网页改日期 |
| 2b | 收款公司列表不列「已连接账套一本都没报会计期间」的公司（collection.service.companies_for） | collection-date-no-period 9d9f0c109（叠在 1fe229338 上，1 个提交） | 已并（79e38eccc） | 不命中新的真机入口；并进收款日期那趟核「有期间的公司照常可选」 |
| 2c | LINE 卡片【编辑】按卡片的公司打开那一页、不改用户当前公司（页面级公司头，线上 6）；卡片指向已不存在的单据时回列表（intake-edit-missing 两个提交，队列已合，变基去重） | page-company d9207ed4c（基于 main 9c1e783ad） | 已并（7a2e73226）；**可摘除**（主会话 11-15 定：真机排各组最后，不过或来不及就重建集成分支跳过它） | 三星一行（两家公司账号 B 卡编辑、A 不变）；共用件 erp-originals / erp-company-menu / cowork-intake-upload 的手工入口 |
| 3 | 验收体系第二期第 0 批：收款链路场景 8 条、站点脚本、首次登记 5 行验收属性（基线 903 → 939） | acceptance-collection → 892df7a4e | 已并 | 真环境首跑（Mac 栈测试机一半）；5 行真机 |
| 4 | MR.ERP 这一车之后的界面行 T9–T11、真实 testerp 数据副本升级演练 | 待三条 MR.ERP 分支进 main | 未到 | T9–T11 真浏览器 / 真对话 |
| 5 | CC 桌面版 1.12.22 服务端 | cc-1122-server e97f3b3b2（是否并入本分支待主会话定） | 未定 | 12 行（共享笔记「CC桌面版1.12.22-真机待测-11-15.md」） |
| 6 | 引导式后续分支 | 待引导式 P0 窗口给 | 未到 | 到时附清单 |

## 放行检查表（集成头随分支更新）

| 项 | 结果 | 证据 |
|---|---|---|
| 全闸门（gate.sh = development.yml） | 9f2119f64：251 项过，1 项不过 = 产品地图只差两行真机记录（liff-camera、erp_item，预期，真机后补） | ~/pearnly-devtools/integ1116-gate-1.log |
| 候选离线（线上镜像 02eebaa4…） | 9f2119f64 与合并头 1f2b2de62 均 111 组 LANE EXIT 0 | ~/pearnly-devtools/integ1116-cand-1.log、-cand-2.log |
| Development checks | 未推 | — |
| 产品地图 | 8d415ba14：只差两行真机记录（liff-camera、erp_item），其余通过 | 本地 |
| 共用件连带回归 | 7a2e73226：命中 erp-masters-selection、erp-originals、erp-company-menu、cowork-intake-upload，40 个入口（下表是 8d415ba14 时的 14 个，白天并 main 后重出全表） | `check-impact.py --markdown` |
| 受影响流程 | 1f2b2de62：交叠的 4 条收款流程（erp.line.collection、erp.web.collection、erp.collection.date、erp.collections.refused）5/5 格通过，404 秒，自建小栈 pearnly-integ1116-lab（跑完已 stop）；其余 33 条中的另外 29 条等白天并 main 后 `--changed` 跑（主会话 11-15 晚定） | ~/pearnly-devtools/integ1116-flows-1.log |
| 全闸门（合并头） | 1f2b2de62：251 项过，同上只差两行真机记录 | ~/pearnly-devtools/integ1116-gate-2.log |
| **最终被测头 f7d403580**（并 origin/main b30469eda，11-16 12:0x） | 全闸门 257 项过，只差两行真机记录（预期）；候选离线 111 组 LANE EXIT 0；`run.py --changed --base origin/main --jobs 3`（自建小栈清卷重起）19 条流程 60/61 格过，唯一不过 erp.exports new/mobile 为无头浏览器断开（「debug connection closed」），`--rerun-failed` 立刻补跑仍是浏览器断开（栈刚起 20 秒），栈起稳后单跑该格**通过**（39 秒）；new/desktop、old/mobile 同流程首轮即过 | ~/pearnly-devtools/integ1116-gate-3.log、-cand-3.log、-flows-3.log / -3b / -3c |
| 产品地图连带的其余 22 条流程（`check-product-map.py` 的「要跑的流程」35 条减去 `--changed` 已跑的；主要是搜索统一改 selection 连带的盘点 / 物料流程） | f7d403580：73 格首轮过 72；唯一不过 erp.stocktake.scan new/desktop 为浏览器侧（「Inspected target navigated or closed」、驱动脚本 cur_dialog 未定义；同流程另 3 格过），栈起稳后单跑两次均**通过**（46 / 45 秒）| ~/pearnly-devtools/integ1116-flows-4.log、-4b-1 / -4b-2 |
| 产品地图（真机记录贴入后） | `check-product-map.py` exit 0 | 本地 |
| 7e3409cf1（再并 main 290bc368b） | 全闸门 GATE EXIT 0（260 项）；候选离线 111 组 LANE EXIT 0；`--changed` 20 条 65/65；地图连带 22 条 72/73（erp.stocktake.gs1 new/mobile 假摄像头 Data Matrix 未认出，下一轮复跑通过） | ~/pearnly-devtools/integ1116-gate-5、-cand-5、-flows-5 / -5x |
| **55b1652da**（再并 main baa9c709e，第三列车 26 个文件） | 全闸门 GATE EXIT 0（261 项）；候选离线 111 组 LANE EXIT 0；26 个文件影响的流程并集 24 条（含 gs1 复跑、9 个前端文件先重建）**76/76**；GitHub architecture-and-offline success；新 main 段命中只能真机的行 0 个（第三列车命中的 erp-transactions、cc-company-picker 12 个手工入口属第三列车自己的连带回归，不是本分支进 main 的条件） | ~/pearnly-devtools/integ1116-gate-6、-cand-6、-flows-6 |

### 共用件连带回归（8d415ba14，`erp-masters-selection`）

| 入口 | 方式 | 结果 |
|---|---|---|
| `erp.entry.choices` | manual | |
| `erp.cc.write` | auto：express | |
| `erp.line.intake.submit` | manual | |
| `erp.purchase.original-upload` | manual | |
| `erp.sales.original-upload` | manual | |
| `erp.line.item-questions` | manual | |
| `erp.trade.line-stock` | manual | |
| `erp.line.webhook-sim` | auto：line | |
| `erp.line.chat` | manual | |
| `erp.line.collection` | offline | |
| `erp.collection.server` | offline | |
| `erp.cc.collection` | offline | |
| `erp.masters.scope` | manual | |
| `shared.team.members` | manual | |

## 真机

总表（按机器分组、估时、依赖）：共享笔记「真机总表-11-16.md」。记录按六栏格式贴在下面，「通过」才放行。

执行：备用 D（local_50267266），被测 `f7d403580`，实验栈 testerp（REFRESH 重导线上 testerp、LOCAL_SPA、镜像 02eebaa48ab5…）。原始记录与读回细节：共享笔记「真机-记录-f7d403580-11-16.md」；截图 `~/pearnly-devtools/device-1115/shots/`。

```
真机记录 | LIFF 采购单供应商框按电话搜（11-15 不通过项） | 2026-11-16 | f7d403580 | 三星 SM-A146P（LINE 内 LIFF，aksarapak） | 通过 | LINE 采购→手动录入→LIFF 新建采购（PE2569-000019，未提交）→供应商框：`0-2328-1245`、`023281245`、税号 `0107536000587` 都出 พัฒน์กล จำกัด (มหาชน)；泰文名「พัฒน์กล」出 2 个（a3–a6）
真机记录 | LINE 采购选公司打税号 / 账套名回显 + 新提示语 | 2026-11-16 | f7d403580 | 三星 | 通过（提示语：没测到） | 打 `NLTTEST` → 下一问首行「บริษัท นิว ลิงค์ (ไทยแลนด์) จำกัด」；打税号 `1234567890123` → 首行「บริษัท Pearnly จำกัด」。新提示语「输入公司名称、税号或账套名查找」只在公司超过 12 家时出（line_flow.py / line_collection.py 同一条件），本账号 8 家（收款 5 家）→ 按设计不出，没测到（a7）
真机记录 | LINE 收款选公司 / 选客户回显 + 纯数字电话 | 2026-11-16 | f7d403580 | 三星 | 通过 | 收款→打税号 `0135547004463` → 首行回显 นิว ลิงค์ 并问收款日期；客户打 `0812345678`「没有找到」——NLTTEST / 69EXP\TEST 都没有这个电话的客户（DBF 只读核过），改用 NLT0148（ARMAS 存 `089-7906072`）打 `0897906072` → 找到 2 个（同电话两位客户）→ 选后发票卡副标题回显客户名；客户编码 `NLT6302008` 直接选中；Pearnly 下客户打税号 `2222222222222` 找到 นาย ทดสอบ（a10–a15）
真机记录 | 网页收款客户框按税号搜（11-15 瑕疵） | 2026-11-16 | f7d403580 | 三星 LIFF 内 Desk 收款页（手机宽度；电脑宽度没测） | 通过 | 新建→Pearnly→客户框打 `0105599240028` → 下拉「บ001 บ้านดีโฮมบิลเดอร์ จำกัด / 0105599240028」。附带观察：不点下拉直接失焦弹「未找到客户」（Link 框原生校验）（a35–a36）
真机记录 | erp.line.postback.col + 收款日期（day 步） | 2026-11-16 | f7d403580 | 三星 + 测试机1 CC（skin3，BED3D4E3）+ accserver NLTTEST | 通过 | นิว ลิงค์ → 问收款日期，正文「会计期间：01/01/2567–31/12/2568 NLTTEST」，快捷回复「31/12/2567 NLTTEST」（本年段最后一天）→ 点它 → NLT0148 → IVP62400272 → 现金 1,000 → 下次再收 → 确认卡有「收款日期 31/12/2567」（a10–a18）
真机记录 | collection-date-no-period | 2026-11-16 | f7d403580 | 三星 + 实验栈 | 部分：有期间照常可选 通过；无期间不出现 没测到 | 收款公司列表 5 家 = 接了 Express 且有期间的全部（实验栈逐家核：linked+periods 5 家；mr.erp / ซิสเตอร์ / CO-00120 / 213 未接 Express）。本租户没有「接了但一本都没报期间」的公司，无法造；Pearnly（今天在期间）选后直接问客户、不多问日期 ✔
真机记录 | erp.line.collection-edit（改日期） | 2026-11-16 | f7d403580 | 三星 + 测试机1 CC + NLTTEST | 通过 | 卡片【编辑】→ LIFF 弹窗 COL261116-89B4，日期 2024-12-31，日历可翻到 2025-01（期间至 2025-12-31）→ 改 2024-12-30 → 保存「已保存」→ 回 LINE 点旧卡【确认】→「收款内容已变更，请重新确认」+ 新卡「收款日期 30/12/2567」→ 确认 →「已记录 · 待写入」→ CC 12:31:47 写成 RE2403085（98.6 s）→ 下次进收款首条「已写入 Express：RE2403085」。读回 NLTTEST：ARTRN RE2403085 DOCDAT/DUEDAT/CMPLDAT 2024-12-30、YOUREF COL261116-89B4、CSHRCV 1000；ARRCPIT → IVP62400272 1000；GLJNL VOUDAT 2024-12-30；IVP62400272 余 31,952。附带观察：写成后再点卡【编辑】弹窗显示「此卡片已失效，请使用 LINE 中最新的卡片。」（该卡就是最新那张，只是已写入；只读显示 RE 号）——文案可商榷，不挡（a21–a33）
真机记录 | erp.desk.pearnly-collections.action.New（收款日期） | 2026-11-16 | f7d403580 | 三星 LIFF 内 Desk（手机宽度） + 测试机1 CC + NLTTEST | 通过 | 新建→公司换 นิว ลิงค์ → 日期清空，ⓘ「会计期间：2024-01-01–2025-12-31 NLTTEST」（手机上在 ⓘ 里，不在框下）；日历打开在 2025-12 且无向后箭头 → 输入 2024-12-30 → 客户 NLT0235 → IV002401394 列出 → 500 / 现金 500 → 提交「已记录 · 待写入」→ CC 12:39:55 写成 RE2403086；读回 DOCDAT 2024-12-30、ARRCPIT → IV002401394 500、GLJNL VOUDAT 2024-12-30（a34–a47）
真机记录 | erp.scan.line.stocktake.liff-camera | 2026-11-16 | f7d403580 | 三星 SM-A146P + 测试机1 屏幕码图（Edge 全屏 code-erp-qr-ice.png） | 通过 | ERP-TEST 富菜单「盘点」→ LIFF 盘点列表 → live-1115-test（Pearnly，คลังสำนักงานใหญ่）→ 开始盘点 → 扫码图标 → LINE 原生「是否允许此网站访问您的相机？」→ 仅允许本次 → 真相机对测试机屏幕 → 读出并认出「น้ำแข็งหลอด」，账面「คลังสำนักงานใหญ่ / 未填 · 10,774 กิโลกรัม」→ 关闭不保存，实盘仍「—」（a50–a55）
真机记录 | erp.line.postback.erp_item | 2026-11-16 | f7d403580 | 三星 + 实验栈 | 没测到：这张票 3 行全被预选定了，没出逐项提问 | LINE 采购→上传识别→打 NLTTEST 选 นิว ลิงค์ →按票面识别→未付→发 invoice-1114.jpg（付费识别 1 次，Claude；实验栈原无 Claude 变量，按 test-environment.md 追加 6 个甲骨文变量 + live erp 后才识别，之前 deferred:claude_not_configured 不扣费）→ 卡片 3 行：两行轮胎「入库」（预选到本地物料 PRD-00046 P-000005、PRD-186783 P-000019，均 express_company 空）、运费「只记账」→ 问仓库（>12）打编码 `36` 直接选中「Ship to ทางลัดพาณิชย์」→ 复核卡。line_choices 三行 decided=true，所以没有「全部新建 / 逐项选择」，erp_item 回传触发不了；要验得换一张品名全新的票（再识别 1 次，约 ฿0.2–0.35，待主会话批）（a56–a64）
真机记录 | page-company 三星行（LINE 选 B 公司 →【编辑】） | 2026-11-16 | f7d403580 | 三星 LIFF | 通过（「网页别的标签页」改为服务端核） | 网页当前公司 A = Pearnly（CO-140489）；LINE 选 B = นิว ลิงค์ 发票 → 卡片【编辑】→ LIFF 采购页：仓库 = B 的「Ship to ทางลัดพาณิชย์」，侧栏左上仍「บริษัท Pearnly จำกัด」→ 提交 → 查看页 PE2569-000019 能开（PUR-2026-198698，company CO-186790，供应商 SUP-170147 ห้างหุ้นส่วนจำกัด ทดสอบยางยนต์）→ 侧栏「采购记录」列表为空（Pearnly 采购 0 张；没有 B 的单）→ LIFF「刷新」后左上仍 Pearnly、列表仍空；服务端 aksarapak 默认 company = CO-140489（没被改成 B）。电脑浏览器另一标签页：没测到（本机浏览器没登 aksarapak）。入库读回：ERP Stock Movement purchase_in ×2（PRD-00046 ×1、PRD-186783 ×4，WH-198095，ref PUR-2026-198698），运费行无库存流水 ✔。Express 未写（待录状态，不在本行）（a65–a73）
真机记录 | collection-date 原生对照（B 组） | 2026-11-16 | f7d403580 | 测试机1 skin3 一次性任务只读复制 \\accserver\d$\ACCOUNT\69EXP\NLTTEST（ARTRN/ARRCPIT/ARRCPCQ/GLJNL/GLJNLIT/ARBAL/ISRUN） | 部分通过：日期 / 期间 / 号段 / 余额桶与原生一致；GLJNLIT 的 CHGDAT/CHGTIM 我们为空（原生有）；原生现场录入与关账期间拒绝没测到 | 对照物 = 既有原生单，非现场录（主会话 13:2x 定：数据层对照够）。没在 Express 界面现场原生录（CC 代录引擎没有收款单配方，只能盲按键逐格录泰文界面，风险大，先问主会话）。改用账套里已有的原生老期间收款 RE2403079（USERID BIT9，2026-11-10 用 Express 录进 2024-11-10，转账 BBRE2403079）对照我们写的 RE2403085（LINE，2024-12-30）/ RE2403086（网页，2024-12-30）：
真机记录 | erp.entry.choices | 2026-11-16 | f7d403580 | 三星 LIFF（aksarapak，当前公司 Pearnly） | 通过 | 物料格打 `P`：前 20 条全是 Pearnly 账本档（PN000xx、53-05-07-00），本地档在后、别家公司 0 条；`P-0000` 出本地档（P-000010…）；`101TOP`（นิว ลิงค์ 的 101TOP045）0 条。供应商格打税号前缀 `01055`：前 20 条抽查 168 ทองเจริญการช่าง、Acer、LAZADA、คริสตัล 都是 CO-140489 账本档，无别家（该前缀库里 CO-00078 359 / CO-186790 126 / 本地 2 条都没混进来）（a80–a83）
真机记录 | erp.masters.scope | 2026-11-16 | f7d403580 | 三星 LIFF | 通过 | 侧栏公司下拉切到 นิว ลิงค์（左上随之变）→ 物料格 `101TOP` 出「ข้าวหอมมะลิเก่าตราต้นโพธิ์ 45กก. 101TOP045」，`PN000` 只剩「新建物料」（Pearnly 档不出），`P-0000` 仍出本地档 → 切回 Pearnly（左上回读 บริษัท Pearnly จำกัด，原状）（a84–a88）。新建页未保存
真机记录 | erp.cc.tool.erp_collection_draft / _push（收款 CC 真模型） | 2026-11-16 | f7d403580 | 本机无头 Chrome（e2eerp，1280 宽）+ 测试机1 CC + 69EXP\TEST | 通过（附 1 处文案瑕疵） | ①编码带 \xa0：Pearnly → 「请帮客户 นาย ทดสอบ 收 IV0000006 100 现金，写进 69EXP\TEST」→ 计划列公司 / 账套 69EXP\TEST / 客户 / 日期 / 发票余额 101,800 / 现金 100 →「确定」→「已写入 Express，收款单号 RE0000031 … 余额 101,700」；读回 ARTRN RE0000031 CUSCOD 'นาย\xa0ทดสอบ'、YOUREF COL261116-CB6B、CSHRCV 100、ARRCPIT → IV0000006 100、IV0000006 REMAMT 101,700 ✔。②刚开的发票：LIFF 新建赊销 SI2569-000008（นาย ทดสอบ，ค่าบริการ 100+7%）→ 13:09 写成 IV691116-001 → 1 分钟内 CC 新对话「收 IV691116-001 全额现金」→ CC 回「列表里没有 IV691116-001，最近一次读取还在进行中，结果还不能确定」，**没说已收清** ✔，给三个选项（纯文字列出，不是提问卡）→「请再读取一次」→ 出现 107.00 → 计划 →「确定」→ RE0000032，余额 0 ✔。**瑕疵**：第二段回复里夹了一句英文「IV691116-001 now shows in the list, with 107.00 บาท still open. I'll make the collection draft…」（用户全程中文）。模型费 0.18 + 0.30 = 0.48 美元（c-col-1…4）
真机记录 | guided-core.erp-cc | 2026-11-16 | f7d403580 | 本机无头 Chrome（e2eerp） | 通过 | 判据：本行要验的就是「新对话照常回复、控制台 0 报错」（本分支不加界面）。代码在 f7d403580 里，是合入队列变基后的 15ee69cc6 一串，与 909971dbb 相同（拼车比对 30 个文件）。实测：新对话发「你好」照常回复、控制台 0 报错（0.16 美元，c-guided-erp-hello）
真机记录 | erp.cc.tool.pearnly_operator_month（H10 月份外） | 2026-11-16 | f7d403580 | — | 没测到：分支未进 main（cc-operator-other-months 在列车 B，拼车核过）
真机记录 | erp.cc.approval-withdraw | 2026-11-16 | f7d403580 | 本机无头 Chrome（e2eerp）+ 实验栈 | 通过（附 1 处观察） | CC 建草稿 COL261116-A3D3（นาย ทดสอบ / IV0000006 / 现金 50，不写入）→「把这张草稿撤回」→ 允许卡「ถอนรายการรับชำระนี้หรือไม่」列 客户 / 发票 IV0000006 / 金额 50.00，按钮只有「ปฏิเสธ / อนุญาต」（c-ap-2）→ 拒绝 → 工具 denied、CC 说草稿仍保留（c-ap-3）→ 再请求 → 允许 → 工具 done、「已撤回」（c-ap-4）。实验栈读回：A3D3 = withdrawn，同时段另 4 笔（89B4 / 2942 / CB6B / C57F）仍 written，没被动。**观察**：卡上没有「公司 / 账套」那一行（Cowork 那条验收写的是「列出单据名和一行公司 / 账套」，ERP 撤回卡要不要有请作者判定）；拒绝后 CC 说「系统要求确认，操作被拒绝了」措辞偏机器。模型费 0.19 美元
真机记录 | erp.line.postback.erp_item | 2026-11-16 | f7d403580 | 三星 LINE + LIFF，Pearnly（69EXP\TEST） | 通过 | 新票 inv-1116.jpg（品名全新：D1116 กล่องเหล็กทดสอบ รุ่น KX-77 / D1116 สายไฟทดสอบ VCT 3x2.5 ม้วน 30 เมตร / ค่าขนส่ง，付费识别第 2 次）→ 卡片两件标「新建 · 入库」+ 提问「新物料：1. … 2. …」按钮 全部新建并入库 / 全部只记账 / 逐项选择 / 取消 → 逐项选择 → 第 1 件 [新建并入库][只记账][选已有物料][取消] → 选已有物料 →「请输入物料名称或编码」→ 打 `PN00035` 直接选中 → 第 2 件 → 选已有物料 → 打 `LOGO` →「找到 4 个」全是 Pearnly 账本档 PN00024… → 取消 →「已取消」（按设计未定行 = 新建并入库；取消后没另发更新卡片）→ 卡片【编辑】经 LIFF 打开采购页 → 仓库空（取消在问仓库之前），选 คลังสำนักงานใหญ่ WH-140490 → 提交弹「Express / 物料：新建、费用科目」框 → 新建 + 51-03-01-00 ซื้อ → 保存 → PE2569-000020（PUR-2026-198704，CO-140489）。读回：行 1 product PRD-186789（PN00035，service）、行 2 新建 PRD-198703（P-000020，本地档）、行 3 运费 PRD-169978 service；ERP Stock Movement 只有行 2 purchase_in ×4 到 WH-140490 ✔（a100–a112）
```

## 一、集成分支进 main 的条件（拼车负责）

只看这条集成分支装的东西（主会话 11-15 23:0x 定）：

| 必须 | 内容 | 状态 |
|---|---|---|
| 必须 | 搜索统一：`erp.scan.line.stocktake.liff-camera`、`erp.line.postback.erp_item` 两行真机记录「通过」，绑最终集成头 | **两行都通过**（f7d403580；erp_item 13:26 补测 PE2569-000020） |
| 必须 | 共用件连带回归：每个 manual 入口记「通过 / 不通过 / 没测到（原因）」 | 待 11-16（A、C 组）；白天并 main 后重出全表 |
| 必须 | 收款日期：NLTTEST 原生对照通过（作者判定）；LINE / 网页改日期；no-period 核点 | **原生对照 11-16 作者判定通过**（唯一差异 GLJNLIT 的 CHGDAT / CHGTIM 为空，是四类单据共用写法的存量差异，另案；关账期间拒绝没测到，判可接受）；LINE / 网页改日期、no-period 待 A 组真机 |
| 必须 | 验收第 0 批首跑（验收窗口，E 组） | 待 11-16 |
| 必须 | 白天并最新 origin/main 后：全闸门（只许差已附记录的真机行）、候选离线 LANE EXIT 0、`run.py --changed` 受影响流程全过；Development checks 绿 | 并 main 前的结果见上表 |
| 可摘除 | page-company：三星一行 + 它的共用件入口；不过或来不及就重建集成分支跳过它（不用 revert） | 各组最后做 |

CC 桌面版 1.12.22 那 12 行、A 线两行（月份代录、Cowork 撤回）**不是**本分支进 main 的条件（主会话确认），它们的分支走合入队列。

## 二、下一次部署的条件（主会话定是否部署；按届时 main 上的全部内容算）

全套流程在发布提交上只跑一次 `run.py --all --post-status`；`deploy.sh --dry-run` 全过（流程闸、租户配置、LIFF、站外设置、容量检查）。
下表按 11-15 23:1x 的分支状态列，**缺的如实标**；进 main 的顺序与时间由合入队列与主会话定。共用件命中按 `check-impact.py --files <分支相对 main 的文件>` 算。

| 分支（头） | 进 main | 要过的（真机行 / 演练 / 只读核对） | 现状 |
|---|---|---|---|
| 本集成分支（搜索统一、收款日期、no-period、验收第 0 批、page-company 可摘除） | 拼车 | 见上一节 | 待 11-16 |
| `pearnly/cc-1122-server`（e97f3b3b2，共用件 cc-desktop，手工入口 10 个） | 合入队列 | 手工入口并进 D 组（备用 D）；CC 1.12.22 真机 12 行；**⑫「旧 CC 1.12.21 不受影响」部署前必须** | 待 11-16（D 组） |
| `pearnly/cc-1122-desktop`（1c65aff15） | 不随服务端部署 | 服务端上线后另发 staging → prod（prod 等主会话点头） | 不挡部署 |
| MR.ERP 三条：第 3 批 `company-onboarding-3-307a0a`（共用件 mrerp-rules，手工 14）→ `mrerp-ledger-dialog`（a1b09740d）→ `mrerp-catalog-pick`（d58919b6c）（后两条共用件 erp-transactions / express-rules / mrerp-rules，手工合计 30 个，含第 3 批的） | 合入队列（今晚） | ① 升级演练、② T9–T11、③ 这 30 个手工入口：都归线 3 写账窗口 local_3e3bec90，15:30 起 T9–T11 → 演练 → 接着点手工入口，不够顺延次日；④ 开关前生产只读核对：收款负责窗口 local_292cabb6，部署前主会话单独叫；开关 `MRERP_LEDGER_ENABLED` 不随部署打开 | ①② 待 11-16；③ 可能顺延；④ 部署前 |
| `pearnly/admin-all-features`（7ec1d4600，未命中共用件，调用方手工入口 2 个） | 合入队列 | 部署后逐个正式租户只读核：每个已开通功能管理员都为真（收款负责窗口，照 collection-admin-check 的做法扩） | 部署后 |
| `pearnly/cc-guided-core`（909971dbb，共用件 assistant，手工 16） | 合入队列 | 作者给了 3 行（0 美元，新代码运行时无调用方，assistant 手工入口被调代码改动 0 处，见 `docs/work/2026-11-15-cc-guided-p0-p1.md`）：guided-core.erp-cc（C 组末尾）、guided-core.cowork-cc（D 组 ⑫）、guided-core.migrate（部署时：**新增 DocType `Pearnly Assistant Guide Run`**，DocType 守卫只拦减少、不拦新增；看各 ERP / Cowork 租户迁移无报错） | 前两行待 11-16；第三行部署时 |
| `pearnly/capacity-maintenance-transient`（7c47c996e） | 合入队列 | `deploy.sh --dry-run` 容量检查通过；生产内存余量约 300 MiB，本批不能新增 ≥300 MiB 常驻进程 | 部署预演时核 |
| `pearnly/control-frontend-source`（b5b0d6393） | 合入队列 | 控制面页面打开冒烟 | 部署后核 |
| `pearnly/cowork-withdraw-push`（0be7bcb07，共用件 cowork-intake-upload，手工 6） | 合入队列 | Cowork 撤回 1 行 + 手工入口（D 组 ⑫）；第 ① 步重启测试机1 上用户的 CC：用户 11-15 夜已批（skin3 普通权限起、哈希 BED3D4E3 不变） | 待 11-16 |
| `pearnly/cc-operator-other-months`（86d0240c9，共用件 assistant，手工 16） | 合入队列 | 月份代录 ①–④（C 组，真模型约 0.5 美元）；⑤ 没批记没测到；手工入口由作者备用 A 写成待测行，并进 C 组末尾 | 待 11-16；待作者给行 |
| `cc-approval-cards`（9ac8a8f5f / ce6c56482 / f09af0041，**已在 main 未上线**；按这三个提交的 32 个文件算：共用件 0、上线必验 0、真机单 1 项） | 已在 main | ① cowork.scan.line.stocktake.liff-camera（三星，0 费用；只读摘要不经过相机路径，可记没测到并写原因）；② cowork.cc.approval-cards（约 ฿5）、③ erp.cc.approval-withdraw（约 ฿3）真模型实机，费用主会话已批（约 ฿8）；实验栈流程两条已验 | 待 11-16（D 组 ⑫ / C 组末尾） |

产品地图本批真机单（`check-product-map.py --batch --files <分支相对 main 的文件>`，同 `deploy.sh` 的调用）：搜索统一 2 项（erp_item、liff-camera，已在本车）；cc-approval-cards 按它三个提交本身算真机单 1 项（cowork liff-camera，来自 stocktake/upload.py）；capacity-maintenance-transient、control-frontend-source 也各报这 1 项，149 个入口是按旧分叉点带出来的（多半同一原因），进 main 后以 `deploy.sh --dry-run` 打印的为准。其余为 0。
手工入口数按 `check-impact.py --files` 算（同上），命中 assistant 的几条分支（月份代录、引导式、允许卡等）的手工入口大部分重合，统一点一次即可。
（11-15 23:1x 一度记「--batch 全报 0 项」：是拼车在 zsh 里没把文件名拆开，工具无误。）

## 部署预演与回滚

部署由主会话执行，步骤同 [11-15 上线车](2026-11-14-release-train.md)「部署预演步骤」。本车是否有新增 DocType / 补丁：collection-date 给 ERP Collection 加一个隐藏字段 `date_chosen`（只增不删，回旧镜像不需要反向迁移）；其余待分支到齐后核。
回滚：`scripts/production/deploy.sh --tag release-20261115-1126`。

## 集成记录

| 时间 | 动作 | 头 |
|---|---|---|
| 11-15 21:4x | 从 origin/main 0c87836b7 开分支；快进到 master-search-unify a15f8c1cf；合 acceptance-collection | 8d415ba14 |
| 11-15 21:5x | 合 collection-date 1fe229338（无冲突） | 52105b5cc |
| 11-15 22:4x | 合 collection-date-no-period 9d9f0c109（无冲突；收款三组离线测试 OK） | 79e38eccc |
| 11-15 22:5x | 合 page-company d9207ed4c（无冲突；带进 main 到 9c1e783ad；作者已过全闸门、候选离线、受影响流程 17 条 56/56） | 7a2e73226 |
| 11-16 12:0x | 并 origin/main b30469eda：冲突 6 个文件（registry.toml / messages.tsv / copy-approved.tsv 两边都留；cc_tools.py 取 main 的「its label」并保留 DAY_NEEDED_NOTE；purchase.js / sales.js 保留 page-company 的 page_company.enter，main 的 switched_company 整页重载已被取代） | f7d403580 |
| 11-16 14:5x | 再并 origin/main baa9c709e（第三列车）：tests/acceptance/rows.toml、owed_baseline.txt 两边都留（rerere 自动；check-acceptance 通过，无重复行） | 55b1652da |
| 11-16 13:3x | 再并 origin/main 290bc368b（+39 提交：MR.ERP 三条、CC 1.12.22 服务端、月份代录等）：冲突 2 个文件——tests/test_master_search_rules.py 两边都留但删掉 main 那条测 `search_rules.or_filters` 的过时测试（该函数已被搜索统一 0672c530e 换成 link_search 删除，全分支无调用方）；collection/cc_tools.py `_fresh_invoices(company, customer, day=None, docnums=())` 合两边参数，并给 draft 里 INVOICE_NOT_FOUND 的按单号即时读补上 `tool_input.get("date")`（收款负责窗口核对：必须补，否则老账期按今天选账套）；新 main 段命中只能真机的行 0 个，f7d403580 上的真机记录沿用。待补：draft 按单号重读带日期的离线测试（收款负责窗口下一条小分支，不进本车） | 7e3409cf1 |
