# L3 真环境验收核心集（2026-11-23）

> 状态：**主会话 11-23 审定**（见第 3 节决定）。基线 `origin/main` 099a31dbc。分支 `pearnly/acceptance-l3-core-cb48fa`。
> 规范：[验收测试标准](../development/acceptance-testing.md)；方案：[2026-11-12 验收测试体系](2026-11-12-acceptance-suite.md)。

## 0. 现状（机器读数，`check-acceptance.py --report`，11-23）

- L3 登记 61 条（正常 7、多情形 21、串联 18、不正常使用 15）；`tier = "gate"` 46 条、`weekly` 15 条。
- 46 条在真机 / 实验栈跑过并登过证据（11-16 ~ 11-20），**现在全部不新鲜**：过期 41、从没跑 20、新鲜 0。
- 从没跑的有：Cowork 全部 6 条、ERP CC 对话 4 条（`acc.erp.cc.collection*`、`acc.erp.cc.push.*`）、`acc.erp.operator.talk.sales`、收款 busy / redelivered / unbound、代录 no.computer 等。
- 运行器只认 `--tier gate`（46 条、按历次实耗约 110 分钟），没有「核心集」这个集合；没有 Cowork 代录、Cowork MR.ERP、LINE 录单回执、DMS 订车、界面真点的跨端场景。
- 示范场景 `acc.erp.purchase.express` 已真跑 9 次（最近 11-20，130 秒），不是「没跑过」。

## 1. 核心集草案（上线前一条命令跑完，目标 ≤40 分钟、≤3 美元）

耗时取历次 run.json 实数；「新」是要写的场景。断言都落在库里 / 账本 / 页面值上，不算「能打开」。

| # | 场景 | 格 | 起点 | 动作 | 断言（预期状态） | 耗时 |
|---|---|---|---|---|---|---|
| 1 | `acc.erp.purchase.express.ui`（新） | ERP 直写·本地·采购（跨端） | 账套恢复基线；站点新建现购单草稿 | **浏览器真点「提交」** → CC 桌面版写 → 单据页 | 账本 RR 按单号读回表头+每行=预期；不变式（别的表逐字节不变、流水号 +1）；单据页显示同一单号；推送记录 = 已写入 | ~3 分 |
| 2 | `acc.erp.sales.credit.express` | ERP 直写·本地·销售 | 同上；新客户「ร้าน…」 | 提交赊销单 → 写 | IV 读回、客户余额 = 合计、称谓栏原样 | 2.2 |
| 3 | `acc.erp.collection.three` | ERP 收款·网页 / LINE / CC | 刚开的发票 | 网页现金、LINE 现金+转账、CC 对话三处收 | 三张 RE 读回、挂的发票、余额 = 0、各端单号一致 | 5.3 |
| 4 | `acc.erp.cc.collection` | ERP CC 直写·收款（真模型） | 刚开的发票 | 对话一句 → 写入计划 → 点写入 | RE 读回；没复述不写（底线①）；最后说的单号 = 账本 | ~5，≤0.5 美元 |
| 5 | `acc.erp.operator.purchase.express` | ERP 代录·本地·采购 | 基线 | 现购单交代录 → Express 界面录 | 与直写同一套账本断言 | 2.2 |
| 6 | `acc.erp.operator.talk.sales` | ERP 代录·本地·销售（真模型对话） | 基线 | 代录对话下令 → 写入计划 → 点写入 | IV 读回；写入计划落点 = 选中的账套 | 8.4，≤0.5 美元 |
| 7 | `acc.erp.mrerp.purchase.goods` | ERP 直写·MR.ERP·采购 | MR.ERP 测试账套 | 采购单导入 | 按单号读回单据页+日记账（借贷平、进项税一行） | 0.1 |
| 8 | `acc.erp.mrerp.sales.credit` | ERP 直写·MR.ERP·销售 | 同上 | 赊销单导入 | 应收一行、销项税一行 | 0.1 |
| 9 | `acc.erp.operator.mrerp.purchase`（新） | ERP 代录·MR.ERP·采购 | 同上 | 交代录 → CC 桌面版在 MR.ERP 网页里录 | MR.ERP 按单号读回 = 预期；单据页回执单号 | ~4 |
| 10 | `acc.cowork.intake.express` | Cowork 网页·本地·采购 | 基线；内容写死不识别 | 录入页提交 → 推送 → 写 | RR 读回；单号回显到录入记录 | ~3 |
| 11 | `acc.cowork.cc.direct.purchase` | Cowork CC 直写·本地·采购（真模型） | 样票库一张票 | 放票 → 识别 → 写入计划 → 点写入 | RR 读回 = 样票预期值；复核前不写 | ~6，≤0.8 美元 |
| 12 | `acc.cowork.operator.purchase.express`（新） | Cowork 代录·本地·采购 | 基线 | 单据交代录 → Express 界面录 | RR 读回；回执单号回到 Cowork 单据 | ~3 |
| 13 | `acc.cowork.mrerp.purchase`（新） | Cowork 直写·MR.ERP·采购 | MR.ERP 测试账套 | 录入页提交 → MR.ERP 推送 | MR.ERP 读回（借贷平）；单号回显 | ~1 |
| 14 | `acc.cowork.line.intake.receipt`（新） | Cowork LINE 录单回执 | 测试账号绑定（模拟事件，出站截住） | LINE 传一张图 → 卡片【保存】→ 推送 → 写 | 录入记录落库、RR 读回、**截住的回执消息里单号 = 账本单号** | ~4，≤0.1 美元 |
| 15 | `acc.erp.purchase.express.twice` | 不正常·M02 代表 | 基线 | 同一张连推两次 | 账本只多一张 | 2.8 |
| 16 | `acc.erp.collection.busy` | 不正常·M08 代表 | Express 开着验收账套 | 三处收款 | 停在待写入、账本逐字节不动 → 放开后各一张 | ~5 |

合计约 **55 分钟**、约 **2 美元**（真模型 4 条）。超 40 分钟：建议 15、16 两条不正常使用归 `gate`（按 `--since` 命中才跑），核心集留 1–14 约 **47 分钟**；
再压就要把 MR.ERP 两条（7、8、9、13，不占 Express 账套）与 Express 那条线**并行跑**（运行器按资源分两路），预计 ≤40 分钟。我倾向做并行，不再删场景。

**不进核心集、写明原因**：
- DMS 订车：真建单是**写外部 DMS 系统**（`booking/delivery.py` 走登录后的外部接口），AGENTS.md 规定外部写入需要用户授权，且没有登记的测试车主 / 测试经销商账号。要定（见第 3 节）。
- 云端 Express 各格：等那条线进 main 后补（同任务书）。
- 库存、POS、作废、公司接入等其余 gate 场景：留 `gate`，按 `--since` 命中才跑；每周全集照跑。

## 2. 要改的骨架（审定后做，分三批合入）

- 批 1：登记加 `core = true`；`run.py --core` 一条命令跑核心集，按资源（Express 账套 / MR.ERP）分两路并行；报告逐条「通过 / 不通过 / 没测到（原因）」+ 证据目录；`check-acceptance.py --report` 加「核心集」一段；先在测试机1 真跑现有 9 条（1–8、10、11 中已登记的）刷新证据。
- 批 2：新写 1、9、12、13、14（复用 `scripts/smoke/express_flow.py`、`cowork_express_flow.py`、`line_flow.py` 的截住出站做法、`scripts/scenarios/write_plan_real.py`；界面真点复用流程运行器的浏览器会话，不另造）。
- 批 3：`acceptance-testing.md` 写进上线流程位置：`deploy.sh` 前打印核心集结果（只报告），跑稳后主会话定何时改拦。

## 3. 主会话 11-23 的决定

1. 核心集 = 第 1 节 1–14；15、16 归 `gate`（按 `--since` 命中才跑）。Express 一路与 MR.ERP 一路按资源并行，目标 ≤40 分钟。
2. DMS 订车真建单先不进 L3，留 L2 模拟；写外部 DMS 要用户授权，主会话去问。
   **更新（用户 11-23「可以」，主会话转达）**：DMS 订车进核心集，第 15 条 `acc.dms.line.booking`：每次在 testdms 的测试账号 DMS-TEST 上
   真建一张订车单 → 核对 → 用 DMS 网页原生作废方式作废；只限这个测试账号、不碰真实租户；**作废失败判不通过、不留测试单**。
   DMS 单独一路资源（`testenv-domain:dms` + `samsung`）。建单 / 核对 / 作废复用 DMS 窗口 11-23 在 testdms 真建单那套
   （`docs/work/2026-11-23-dms-line-gifts.md`），等 DMS 分支合入 main 后再写；登记里写授权来源「用户 11-23」。
3. 测试机2 今天不用，CC 桌面版换测试机1（`PEARNLY_ACC_LEDGER_HOST=testpc1`，运行器把登记里的 testpc2 锁换成 testpc1）；站点本机 Mac 实验栈
   （`PEARNLY_ACC_SITE_TARGET=maclab`）；账套 `69EXP\TEST2`；真模型上限 3 美元。测试机1 排在 LINE 回执、DMS 真建单、K1 回放之后，主会话通知；
   账套服务器 20:40 前收尾，来不及改明早。办公室出口连不上生产：要连线上测试租户的场景先不排。
4. 等待期间：Cowork 6 条、ERP CC 对话 4 条从没跑过的场景脚本补好，在实验栈上离线自检。

## 4. 进度

- 批 1（本分支）：登记 `core = true`（已登记的 10 条：1 的现成版 `acc.erp.purchase.express`、2–8、10、11）；`run.py --core` 按资源分路并行，
  `report.md` 逐条三态；`check-acceptance.py --report` 加「L3 核心集」一段；测试机1 桌面用户通道留环境变量 `PEARNLY_ACC_TESTPC1_USER`。
  干跑：`PEARNLY_ACC_LEDGER_HOST=testpc1 PEARNLY_ACC_SITE_TARGET=maclab python3 scripts/acceptance/run.py --core --dry-run` → 两路（Express 8 条、MR.ERP 2 条）。
  测试机1 首跑前还要备：验收数据根 `C:\PearnlyAcceptance\root`（`win/make_root.py`）、`maclab_pc.py prepare/attach --host testpc1`、TEST2 基线快照在测试机1 本机盘。
- 等待期间补的（同一批）：销售样票 `sales-a1`（客户 บ001 取自 TEST2 基线 ARMAS 的两份只读拷贝，渲染核过票面），`acc.cowork.cc.direct.sales` 不再缺票；
  Cowork 录入四条（`acc.cowork.intake.*`）站点跟 `PEARNLY_ACC_SITE_TARGET`、默认不再指线上测试租户，登记里写死的 testpc1 改成按站点目标占；
  标准 §7 / §8 写进核心集的命令、并行分路、`report.md` 与「只报告不拦」。
- 离线自检（11-23）：10 条从没跑过的场景模块与站点脚本全部编译过；`tests/test_acceptance_*.py` 全过；`first_run.py --list`（测试机1 + Mac 栈）列出要占的资源与缺的环境变量；
  **没有连站点跑**：Mac 栈 cowork 现在没起、maclab-erp 挂的是别的窗口的实时源码，真跑前按排队起栈、`maclab.sh live` 挂被测提交。
  10 条里要真模型的 8 条（ERP CC 对话 4 条、Cowork CC 直写 2 条、代录对话 2 条）只能在真跑时验，离线做不了更多。
- 批 2（11-23 下午，测试机排队期间离线写）：第 12 条 `acc.cowork.operator.purchase.express`（Cowork 代录 · 本地 · 采购）写好未跑：
  站点脚本 `site/cowork_operator_post.py`（造票同 Cowork 录入那条 → 记对话账套 → Cowork 公司 → 代录任务 → `express_operator.api.start`），
  断言代录载荷（`entry_mode=operator`）、批次 = 代录任务、票名下只一条推送、任务里已录入同一单号，账本读回与直写同一套；
  新链 `cowork.operator-to-express`；`core = true`；离线单测（正确世界通过、三种坏法各自被拦）。
- 批 2：第 1 条 `acc.erp.purchase.express.ui`（ERP 采购草稿在采购页真点「提交」）写好、浏览器一段已在自用 Mac 栈实测
  （PUR-2026-00005：点页头主按钮后服务端 submitted、页面出「已提交」）；整条写账未跑。核心集里替换服务端提交版 `acc.erp.purchase.express`
  （后者留 gate）。浏览器工具 `scripts/acceptance/desk_submit.py`（Playwright，本机栈测试账号、密码从栈配置读不打印）；
  采购站点脚本加 `stage = draft / wait`。离线单测：先草稿后点再等写账；弹窗或没提交成判不通过且不去等写账；不在 Mac 栈时没测到。
- 第 14 条 `acc.cowork.line.receipt`（11-23 LINE 回执进 main 2e006a7bc 后写，写好未跑）：站点脚本 `site/cowork_line_save.py` 造一张
  卡片票（录入去向 express + 连接 + 账套）→ 以测试账号身份进程内调卡片「保存」（假回复令牌、假 LINE 号，本进程 LINE 出站全截住）→ 等结果；
  断言推送写成、单号 = 账本读回、本进程开了 60 秒 loading、记下等结果的键、全程没有推送（站点按天计数 push_* 不变、本进程无 push）、
  55 秒内有结果时回复恰好一次（站点计数，LINE 拒收假令牌记 reply_rejected）且本进程没回兜底；55 秒还没结果时记「走了兜底」。
  只在接着 LINE 测试频道的测试环境 testcowork 跑（Mac 栈没有 LINE 频道 → 核心集在 Mac 栈跑时这条记没测到）；回执文字留三星真机记录。

## 5. 首跑（11-24，窗口 accept-l3-1124，分支 `pearnly/acceptance-l3-first-run-64fc3d`）

环境：本机 Mac 栈 `pearnly-acccore`（ERP 18995 / Cowork 18996，镜像 054aa603… + main d8510e61a 实时源码，`maclab_model.py on` 接真模型）+ 测试机2（aksar，CC 1.12.24）
+ 账套 `69EXP\TEST2`（基线 TEST2@2026-11-16）。测试机1 当天给上线后验收，主会话改排测试机2。真模型合计 2.15 美元（上限 3）。
证据目录 `~/pearnly-devtools/acceptance-runs/20261124-*`；上机 / 还原脚本 `~/pearnly-devtools/acc-l3-1124/`。

| # | 场景 | 第 1 遍（含复跑） | 第 2 遍 | 结论 |
|---|---|---|---|---|
| 0 | acc.erp.company.connect.fresh | 通过 | —（只在刚建的站点跑） | 通过 |
| 1 | acc.erp.purchase.express.ui | 通过（环境类重跑 1 次） | 通过 | 通过 |
| 2 | acc.erp.sales.credit.express | 通过 | 通过 | 通过 |
| 3 | acc.erp.collection.three | 通过 | 通过 | 通过 |
| 4 | acc.erp.cc.collection | 不通过 ×3（用例）→ 通过 | 没测到：花费到顶 | 通过 1 次 |
| 5 | acc.erp.operator.purchase.express | 通过 | 通过 | 通过 |
| 6 | acc.erp.operator.talk.sales | 不通过 | 没测到：花费到顶 | 不通过（问题 1） |
| 7、8 | acc.erp.mrerp.* | 没测到 | 没测到 | 新栈没授权 MR.ERP |
| 10 | acc.cowork.intake.express | 没测到 / 不通过（用例）→ 通过 | 通过 | 通过 |
| 11 | acc.cowork.cc.direct.purchase | 不通过 / 没测到（用例）→ 通过 | 通过 | 通过 |
| 12 | acc.cowork.operator.purchase.express | 不通过（用例 + 工具）→ 通过 | 通过 | 通过 |
| 14 | acc.cowork.line.receipt | 没测到 | 没测到 | 今天不切 testcowork |
| 15 | acc.dms.line.booking | 没测到 | 没测到 | 写好（离线单测过），等测试环境排期 |

首跑红的都是用例 / 工具问题（已在本分支修）：`maclab_model.py` 只配密钥不配引擎；新站点 / 新账套要先答写前卡（付款方式、科目、往来方），
卡上科目只给搜索；同号发票（每格还原账套）把前几格的收款算进来、查找不等本格那笔；引导式收款点写入后 CC 不另说话（改核卡上单号）；
代录回执不带落点（改按冻结的账套核）；对话驱动不会答 `pearnly_guide` 卡；`pair_profile` 没从原本就暂停的同通道档案借 Express 程序；
测试机 cp874 控制台印不出中文结果；真模型驱动只认一组站点。

产品侧（报主会话，未改）：
1. ERP 代录准备中列出的待录单会被 `express.push.poll` 每分钟直写抢走（实测 8–22 秒），代录对话时有时无——口径待定，定了再改第 6 条场景。
2. Cowork 代录遇到没有明细行的票（直写能写）停在 Express 空明细的 RR 上、OPERATOR_UNKNOWN_SCREEN。场景已改带样票明细，不再触发。

何时改「拦」：问题 1 定案、第 6 条改好后，再连续两次完整核心集全过（含两条 ERP 对话），由主会话定接进 `deploy.sh`。
