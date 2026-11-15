# 验收体系第二期第 0 批：收款链路（11-15）

分支 `pearnly/acceptance-collection`，基线 `origin/main` 0c87836b7。只加验收场景、站点脚本、登记与单测，**不改产品代码**。
主会话 11-15 晚定：第二期开工，收款链路排第一；今晚只写不跑（本机负载高、accserver 已关、Mac 实验栈的测试机一半 11-16 白天才验）。

## 状态

| 层 | 状态 |
|---|---|
| 实现（场景 8 条、站点脚本 3 段、共用件 1 个） | 已写 |
| 本地测试（站点与账套换成替身，`tests/test_acceptance_erp_chain.py`） | 通过；只证明场景自己的判定对，不证明产品 |
| 真环境首跑 | **没跑**，证据 0；排在 11-16 白天 Mac 实验栈测试机一半验过之后 |
| 上线验收闸 | 仍只报告（`rows.toml` 的 `gate_blocks = false`） |

## 评估（按需求影响评估模板，只列有内容的项）

- 同类复用：骨架照示范 1（`scenarios/erp_purchase_express.py`）；基线、调站点、读回断言收进 `scripts/acceptance/erp_chain.py`，示范 1 不动（它的证据指纹不变）。
- 上下游：只调产品现成的服务端函数（`masters.cc_service`、`sales.entry`、`collection.service`），不新增产品入口，产品地图、共用件登记不变。
- 隔离权限：站点脚本只在测试租户跑；这家公司由 CC 报上来的账套必须全部就是验收账套（`69EXP\TEST2`），多报一本就拒绝、什么都不建不写。
- 成本：模型 0、识别 0。机器时间见「怎么跑」。
- 作废删除：每条跑完把账套恢复到基线并核对；站点侧留下测试单据与收款记录（实验栈数据，不清）。切换公司用的陪跑公司 `ACC switch probe` 只在只有一家公司时建一次。
- 文案：无界面文案。

## 场景

都从服务端函数走（`via = "api"`）：证明「同意之后写进账本并且写对、三处口径一致」。界面、对话、真 LINE 不在这里，见下面真机清单与流程用例
（`erp.company.connect`、`erp.web.collection`、`erp.line.collection`、`erp.collections.refused`）。

| id | 走什么 | 终点断言 |
|---|---|---|
| `acc.erp.company.connect` | 列表 → 刷新重报 → 添加 → 再添加一次 → 主档同步 → 停用后重新启用 → 切换当前公司 | 公司数不乱加、列表状态、主档落库、账套资料带收款方式、三处默认值一致、账套与基线逐文件一致 |
| `acc.erp.company.connect.fresh` | 同上，只在刚重置的站点上跑 | 首次添加接管空的默认公司，不另建 |
| `acc.erp.sales.credit.express` | 新建「ร้าน…」客户的未付款销售单 → IV | ARTRN 余额 = 合计、ARMAS 称谓栏 / 核心名、ISVAT、凭证；ERP 侧未付款不带收款方式 |
| `acc.erp.collection.three` | 同一张刚开的发票：网页现金 300、LINE 现金 200 + 转账 100（带凭证）、CC 现金 150 | 三张 RE：客户编码、对方单号 = 记录号、现金 / 其他方式栏、挂的发票、方式行、银行流水、凭证；发票已收 750、余额 320 |
| `acc.erp.collection.nbsp` | 编码带不换行空格的客户，复述成普通空格交进去 | RE 的客户编码逐字符原样 |
| `acc.erp.collection.web.twice` | 网页同一个请求号提交两次 | 一笔记录、一张 RE |
| `acc.erp.collection.line.edit` | 出卡后改金额，旧版本确认、新版本确认 | 旧的被挡回、写成的是改后金额 |
| `acc.erp.collection.line.refused` | LINE 草稿没确认时网页先收清同一张，再确认 | 被拒、草稿作废、账本只有网页那一张 RE |

每条收款场景自己先开一张赊销单再收，所以收的都是「刚开的发票」。预期以外的表逐字节不变，允许变化的表取 CC 桌面版自己的写入清单
（`dbf_schema.BACKUP_TABLES` 去掉应付三张、`dbf_receipt.WRITE_TABLES` 加客户主档 / 科目余额标记 / 银行流水）。

## 今天这条路上修掉的缺陷 → 守它的断言

| 缺陷（11-15 发布车的行 / 提交） | 断言在哪 | 这里守 / 别处守 |
|---|---|---|
| 首次添加另建一家、当前公司停在空默认公司（#5 dc86d2d26） | `company.connect.fresh`「首次添加接管了空的默认公司」 | 守 |
| 已停用公司重新添加被当成新建（#7 7b25f84d6） | `company.connect`「再添加：重新启用、不新建、公司数不变」 | 守（服务端）；提示文字「已更新」是前端，真机行 |
| 切换当前公司间歇不生效（#7） | `company.connect`「每次三处一致」 | 守（单进程顺序切；并发回填靠 `tests/test_user_defaults_cache.py`） |
| 刷新 = 在线电脑立即重报（#5） | `company.connect`「电脑都回执了重报」「30 秒内回来」 | 守 |
| CC 退出后刷新干等 60 秒（#7，20 秒无人领单判未连接） | — | 真机清单 1（要停 CC）；单测 `test_cc_desktop_channel.py` |
| 账套资料缺收款方式报「CC 桌面版需更新」（8281e1f40） | `company.connect`「账套资料已存、带收款方式」；收款站点脚本等重读好再继续 | 守 |
| 「ร้าน…」客户写进 Express 丢称谓（#9 72516d120） | `sales.credit.express`「新客户的称谓栏 / 核心名」 | 守 |
| 未付款单显示现金、未付草稿提交被当成已付（#8b 6321901ab） | `sales.credit.express`「未付款、已收 0、不带收款方式」+ 账本余额 = 合计 | 守（服务端值）；详情页显示是前端，真机行 |
| CC 把带不换行空格的编码换成普通空格、写入被拒（#6 75d4a80bb） | `collection.nbsp`、`collection.three` 的 CC 一笔「客户编码逐字符原样」 | 守（服务函数层）；真对话在真机清单 3 |
| CC 对刚开的发票说「已收清」（#6 199315fdb） | 每条收款场景「发票没列出来的那几次都不是已读好」 | 守读回状态；模型怎么说在真机清单 3 |
| 选定公司后预热读未收发票（#8c） | — | 流程用例 + 真机；这里只守「读得到」 |
| 收款旧卡编辑地址（#4a）、新建弹窗勾选与金额保留（#4b）、加载态 / 下拉不展开 / 状态实时刷新（#8c） | — | 前端，真机清单 2、流程 `erp.line.collection` / `erp.web.collection` |
| LINE 选公司 / 客户 / 供应商搜索统一（#8a ccce676a0） | — | 单测 `test_master_search_rules.py`、`test_line_search_and_loading.py`；真机清单 2 |
| 「未收」文案（#8d） | — | 文案闸 + 真机 |
| 卡片改过后拿旧卡确认（收款闭环） | `collection.line.edit`「拿旧卡的版本确认被挡回」 | 守 |
| 同一张被别处收清后再确认（写前拒） | `collection.line.refused` | 守（服务端写前拒）；CC 桌面版拒收退回草稿那一支仍没测到 |
| 连点两次 | `collection.web.twice` | 守 |

## 依赖的在途分支（场景按「合入后的行为」写）

- `pearnly/collection-date`（收款日期可改）：场景不传日期、不断言 DOCDAT，合入前后都成立；合入后要加一条「改了日期，RE 的日期是改后的」。
- 引导式（`pearnly/cc-guided-core` 等）：这里不走对话，不受影响；真对话那条（真机清单 3）要按引导卡重写点法。
- 按类缺陷修复：选择框账本物料排前、收款预热边角——不在这批断言里；后者合入后可把「发票没列出来的那几次都不是已读好」收紧成「首次就列出」。
- `pearnly/maclab`（Mac 实验栈）：真跑的前置。合入后把登记里示范 1 的 `resources` 去掉 `testpc1`（用 Mac 栈时不占测试机1）。

## 怎么跑（11-16 白天，Mac 栈的测试机一半验过之后）

前置：`maclab.sh reset erp --yes` → `up erp` → `maclab_pc.py attach --host testpc2 --app erp`（测试机2 的 CC 只报 `69EXP\TEST2`）。
环境变量同示范 1，另加 `PEARNLY_ACC_SITE_TARGET=maclab`。顺序（一次起栈连着跑，约 1–1.5 小时，费用 0）：

1. `acc.erp.company.connect.fresh`（刚重置的站点只有一次机会）→ `acc.erp.company.connect`
2. `acc.erp.sales.credit.express`
3. `acc.erp.collection.three` → `.nbsp` → `.web.twice` → `.line.edit` → `.line.refused`

首跑要校准的（预计会先不通过的地方）：只记账的一行写成 IV 时实际动的表；验收账套有没有转账方式、有没有编码带不换行空格的客户
（没有就记前置条件不满足，要在基线里补后重取基线，先问主会话）；重报回执 30 秒、主档同步 300 秒两个时限。

## 真机清单（交主会话排；这里做不了的）

| # | 行 | 设备 | 看什么 |
|---|---|---|---|
| 1 | `erp.desk.pearnly-company.action.company.connect.add` | 本机浏览器 + 测试机2 | 弹窗排布（待添加 / 已停用 / 已添加）、重新启用提示「已更新」、CC 退出后刷新约 20 秒显示「CC 桌面版未连接」、左上公司下拉入口 |
| 2 | `erp.line.postback.col`、`erp.line.collection-edit`、`erp.desk.pearnly-collections.action.New` | 三星 + 本机浏览器 1440 宽 + 测试机 | 真 LINE 逐步对话（选公司按税号 / 账套名、选客户按编码 / 税号）、现金 + 转账 + 凭证照片、编辑经 LIFF、旧卡地址、网页弹窗加载态与勾选保留、列表自己变状态 |
| 3 | `erp.cc.tool.erp_collection_draft` / `_push` | 本机浏览器 + 测试机 | CC 对话真模型：编码带不换行空格的客户写成、刚开的发票不说「已收清」（真模型 ≤1 美元，主会话 11-15 已同意；Mac 栈没接模型，要在测试机1 实验栈或线上测试租户跑） |
| 4 | `express.report.bank-deposit` | 测试机1 Express 原生界面 | 银行流水报表（屏幕装不下，仍没测到） |
| 5 | CC 桌面版拒收退回草稿 | 测试机 | Express 与服务端不一致时电脑拒收、草稿退回带原因（真机造不出，仍没测到） |

## 要主会话定的

1. 收款 5 行（网页新建、LINE 按钮、CC 草稿 / 写入两个工具、从连接添加）还没在 `rows.toml` 标验收属性：一标就按规则多算约 40 项欠账
   （检查器允许存量行第一次标时进基线，但你的口径是基线只减不加）。这次没标，基线净减 4 项（销售两行的真环境主线与链）。
2. 验收账套缺转账方式 / 带不换行空格客户时，是否在基线里补（要重取基线）。
