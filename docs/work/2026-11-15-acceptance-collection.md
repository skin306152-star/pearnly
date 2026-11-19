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

## 欠账登记（主会话 11-15 晚定：标）

收款 5 行（网页新建、LINE 按钮、CC 草稿 / 写入两个工具、从连接添加）此前没有验收属性，等于没算欠账。这次**首次登记，基线 +40 项，
是把账记实，不是回退**；同一提交销售两行减 4 项（真环境主线与链已有场景）。基线 903 → 939。

- 登记时就不欠的（这批场景或现有流程已覆盖，没进基线）：三行写账本的「真环境正常主线」与「串联」（`collection.three`）；网页新建的
  连点两次 M02（`collection.web.twice`）；LINE 的过期卡片 M13（`collection.line.edit`）、两条路各走一遍 M14（`collection.line.refused`）。
  这几项要等首跑通过才有证据。
- 进基线、仍欠的 40 项：
  - 网页新建 6：换公司 / 账套 M04、电脑离线 M07、Express 开着 M08、权限不足 M09、手机切后台 M11、新开户与老数据两套。
  - LINE 按钮 11：重复事件 M02、中途喊停 M03、M04、卡片挂着打字 M05、M07、M08、M09、M11、刷新 / 换设备 M12、未绑定 M16、两套数据。
  - CC 草稿工具 9：M02、M03、M04、M05、M09、M11、M12、正常主线（要走真对话，不算 via = api 的）、两套数据。
  - CC 写入工具 11：M02、M03、M04、M05、M07、M08、M09、M11、M12、正常主线（同上）、两套数据。
  - 从连接添加 3：M02（场景里再添加了一次，但登记的是串联不是不正常使用，下一批拆出一条）、M09、M11。
- 下一步能直接划掉的：M07 / M08（示范 3 的桌面遥控接上后，收款与采购同一套做法）、M09（站点脚本换成没有收款权限的员工，零费用）、
  从连接添加的 M02。

## 已定（主会话 11-15 晚）

- 验收账套缺转账方式或带不换行空格的客户时：用正式途径（正式 CC 或服务器版 Express，不离线改 DBF）补上并重取基线，补了什么报主会话；
  `69EXP\TEST2` 仍只有验收窗口写。
- 5 行真机排在 11-16 白天，与 Mac 栈测试机一半的 40 分钟一起做。

## 第 1 批：库存单据写 Express（11-15 晚接着写，同一分支）

站点脚本 `scripts/acceptance/site/erp_stock_post.py`（按顺序做几步、每步等写完）+ 场景 `scenarios/erp_stock_express.py`，6 条，都是 `via = "api"`、费用 0、**写好未跑**：

| id | 做什么 | Express 单别 |
|---|---|---|
| `acc.erp.stock.in.express` | 入库 2 件 | 调整 JU |
| `acc.erp.stock.out.express` | 先入库 2 件再出库 1 件 | 领用 OU |
| `acc.erp.stock.adjust.express` | 物料调整 +3 | 盘点调整 TK |
| `acc.erp.stock.transfer.express` | 先入库再调拨到另一个仓 | 调拨 RL |
| `acc.erp.stock.void.express` | 入库写成后作废 | 原单改作废、明细打删除标记 |
| `acc.erp.stock.count.express` | 新建盘点 → 实盘多 1 → 结束 → 生成调整单（再点一次不生成第二张）→ 提交 | TK |

读回：STTRN 单头（仓库码、状态、调入仓）、STCRD 明细（物料、数量；调拨一出一入两行）、预期以外的表不变
（允许的表 = `dbf_stock.TABLES` + 科目余额标记、分仓、附加说明）。基线再减 8 项（四种库存单的真环境主线与链），939 → 931。

首跑要校准：明细数量的正负号（现按绝对值核）；验收账套有没有第二个 Express 仓、JU / OU / TK / RL 单据簿、是否允许负库存——缺了会停在等人处理，
场景原样报原因、记前置条件不满足。凭证张数没核（定期盘存或零金额不过账）。

这一批没做、留给下一批的：POS 日汇总（要先造收银台、开班、小票）、采购单 / 销售单作废（已写进 Express 的不写 Express，只能断言「账本不变 + 状态」）、
Cowork 网页上传 → 写、定时 / 文件夹录入。

### 第 1 批（续，只在本地提交，攒着一起推）

- `acc.erp.pos.day.express`：开班 → 两张现金小票 → 当天日汇总（再合一次没有第二张）→ 现销单 HS 读回（合计、已收清、明细数量合计）。
  站点脚本 `erp_pos_day.py`；公司没开收银时在实验栈里替它打开。地图里没有日汇总自己的行，挂在收银小票页那一行上。
- `acc.erp.purchase.cancel.express`、`acc.erp.sales.cancel.express`：写成后作废 → ERP 作废、写账栏照旧、不多排写账件、
  作废前后账套逐文件一致（已写进 Express 的采购 / 销售单没有原生作废，Express 那张由财务处理）。站点脚本 `erp_doc_cancel.py`。
- `acc.erp.company.connect.twice`：把「同一份再添加一次」单独登记成不正常使用 M02（同一个模块、同一组断言），基线减 1，931 → 930。
- 现在登记的真环境场景 20 条，全部写好未跑。

Cowork 网页上传 → 写、定时 / 文件夹录入还没写：识别回放只对产品认的测试站点名开，Mac 实验栈的站点名不在名单里（不付费就没有识别结果）。
要么这两条在测试机1 的 Cowork 实验栈跑（要切 testcowork 域名），要么走不经识别的手工录入入口；等主会话定。

- `acc.erp.denied`（不正常使用 M09）：没有任何岗位的员工碰收款（网页提交、LINE / CC 建草稿、读发票、读方式）与从连接添加
  （列表、刷新、添加）八处，都被服务端拒、收款记录 / 公司 / 写账件一条不多、账套与基线一致。站点脚本 `erp_denied.py`
  （没岗位的员工 `acc.denied@<站点>` 不存在就在实验栈里建）。基线再减 4，930 → 926；登记 21 条。CC 写入工具的 M09 仍欠
  （放行别人的草稿要先有一张草稿，下一批做）。

### Cowork 两条（主会话 11-15 定 A：按测试机实验栈写）

已写（本地提交）：`acc.cowork.intake.express`、`.twice`——录入页「提交」背后的函数链（建推送记录 → 确认 → CC 写 → 回执），票的内容写死
（样票库 purchase-a2），读回 APTRN 表头、进项税、凭证；写成后再提交一次账本仍只多一张。站点脚本 `cowork_intake_post.py`
（防护照冒烟三道，落点换成验收账套）。基线减 3，926 → 923；登记 23 条。运行器加了「按服务端在哪取资源」（`site_resources`）：
ERP 那 21 条用 Mac 栈时不再占测试机1。

没写、要主会话定的（只读勘察查到的三处实际挡路，不是写法问题）：

1. **上传 → 识别（回放）这一段现在跑不了**：样票库只有票面内容，没有识别结果；回放按「原件字节哈希 + 页数」找，找不到就判失败。
   要先留存一份字节固定的样票（现在每次现截图，换机器字节可能不同），在一个测试租户上真识别一次（付费一次，约 ฿0.2–0.35），
   再用 `scripts/ocr-replay-corpus.py` 导进共享回放库。
2. **站点识别引擎是 Claude 时不经回放、照样付费**（`workflow.py` 里 Claude 那条路在回放分支之前）。线上租户 10-18 起都是 Claude，
   测试机实验栈跟线上：开跑前要把实验栈那个站点的引擎临时换回谷歌并开回放，或接受每遍一次真识别的费用。
3. **定时 / 文件夹录入在验收账套上自动推不出去**：测试站点上自动推只认 `C:\PearnlyExpress\` 下的账套，服务器上的验收账套会被降成
   「提议」；图片票一律判要人看，只有带文字层的 PDF 才自动推。所以这条要么写到测试机本机 `C:\PearnlyExpress` 的 T 账套
   （另取一份基线），要么断言「停在待处理 → 作答 → 推出去」。

建议：1、2 合成一件事做——留存一张固定样票 + 付一次识别费录回放 + 实验栈跑前切引擎；3 用测试机本机 T 账套、带文字层的 PDF 样票。

## 第 4 批：MR.ERP 四种单（11-15 晚，本地提交）

把写账窗口 11-15 在实验栈真写过的 T1–T5 做成可重复的场景（站点脚本 `erp_mrerp_post.py`，场景 `scenarios/erp_mrerp.py`，5 条）：
货品赊购、费用、赊销、现销现金各一张，外加「写成后再推一次单号不变、列表里仍只有一张」。断言：账本栏已写入、写的是 MR.ERP、
单据上记着写进了哪本账套；按单号在所选菜单的列表里恰好一张、单据页单号一致；日记账借贷相等且等于单据合计，税额 / 应收 / 现金那一行对得上。

要知道的三点：
- **依赖三条没进 main 的分支**（公司接入第 3 批 → ledger-dialog → catalog-pick）；今天的 main 上「记着写进了哪本账套」那条会不过。
- MR.ERP 写账开关是产品代码里的常量、默认关：场景只在站点脚本那个进程里打开，同步跑一次导入（后台任务看到的仍是关的）。
- 读回没有产品函数：照写账窗口的实测脚本，经产品唯一的登录入口自己取列表页、单据页、日记账页。登录会踢掉别处的 test01，
  运行器占 `mrerp:test01` 的锁，跑前先问主会话。MR.ERP 里的单不删（产品没有删除函数），每次跑累积 4–5 张，对方档用新名字新税号。

先登记成每周跑（weekly）；三条分支合入、开关对客户打开后再改成上线前必跑。没做、留下一批的：现购（T7）、折扣与无税卖家（T8）、
要人选菜单 / 再写一次 / CC 推送 / 导入中作废（T9–T12）、没有账套 / 关开关（T15、T16）、税报告（T18）。

## 第 2 批：CC 对话直写（真模型；11-15 晚，本地提交，没花钱）

已写：`acc.erp.cc.collection`、`.nbsp`（场景 `scenarios/erp_cc_collection.py`，每条上限 1 美元）——自己先开一张赊销发票，在 ERP 的 CC 对话里
只说一句「客户 X 今天付了发票 Y 现金 400」，等到写入计划（引导卡按每题第一项代答并留证）→ 核点头之前账套逐文件没变 → 点「写入」→
按发票找到 CC 建的那笔收款（只读站点脚本 `erp_collection_lookup.py`）→ 账本读回同 `acc.erp.collection.*` → 核三条底线。
守 11-15 两条挡上线缺陷的对话那一半：带不换行空格的客户写成且编码原样；刚开的发票不用用户再催一次（这条是模型说法，记软断言）。
基线减 2（两个收款工具的「正常主线」），923 → 921；登记 30 条。对话驱动加了「建对话时带月份 / 账套」（`session=`）。

顺手修了一处场景自己的缺陷（替身单测撞出来的）：会写账本的场景原来要走到「账本读回」才登记收尾恢复，前面哪一步不通过就不恢复基线；
现在从核完基线那一刻起，任何一步不通过都恢复（只读的从连接添加 / 权限不足两组除外）。

没写、原因（只读勘察查到的产品现状）：
- **ERP 对话直写采购 / 销售**：ERP 的直写对话不识别附件（附件识别只有代录在用），单据一提交就自动排写账，对话里的「推送」只等于单据页的
  「再写一次」。所以它的场景是「先造一张停下的单，再在对话里推」，与 Cowork 的「放票 → 录」不是一回事——形状要主会话确认后再写。
- **Cowork 直写销售**：样票库只有两张采购票；销售票的卖方是验收公司自己、买方要是验收账套里真有的客户，要对着基线挑一位再生成样票
  （今晚不碰测试机，没法核基线里的客户）。场景本身照示范 2 改方向与读回表即可。
- **真模型驱动不连 Mac 实验栈**（那里没接模型）：这一批在接了模型的实验栈上跑。

## 第 3 批：代录（11-15 晚，本地提交）

已写：`acc.erp.operator.purchase.express`、`acc.erp.operator.sales.express`（站点脚本 `erp_operator_post.py`，场景 `scenarios/erp_operator_express.py`）。
做法：开一张单 → 建代录任务 → 交单开始（写入计划「开始」走的同一个函数）→ 等 CC 桌面版在那台测试机的 Express 界面里录成并回执 →
读回与直写同一张单的同一套断言，另核代录特有的三样（写账件是代录交的、这张单名下只有这一件、代录任务里是已录入并带同一个单号）。
基线减 2，921 → 919；登记 32 条。

要知道的：
- **ERP 网页提交的单会自动排直写**，代录只接「还没交进队列」的单。站点脚本只在自己那个进程里不排那一次后台直写、紧接着交代录；
  交成后这张单归代录、直写不再接。直写要是还是先领走了，场景记前置条件不满足，不硬抢。产品里代录的正路是「对话里放票 → 识别 → 开始时提交」，
  那条要识别回放（同 Cowork 上传那件事），等回放样票录好后补一条从放票开始的。
- 要占测试机桌面（`testpc2-desktop`），一次一条；那台电脑的 CC 上登录的员工要先做过 Express「授权登录」；20:40 前收尾。
- 对话里下令、开始前卡片、Express 中途弹框带回对话、被人打断后「继续 / 停止」、「同一张再录一次默认跳过」都不在这两条里：
  对话驱动还不接代录模式（要先让驱动能建代录会话、认代录的三种卡片），列在真机清单；下一批做驱动。
- 云端 Express、MR.ERP 代录：产品还没到，模块里留了桩（走到就记前置条件不满足），没登记。

### 第 2 批（续）：ERP 对话推送（主会话 11-15 晚点头按这个形状写）

`acc.erp.cc.push.purchase`、`acc.erp.cc.push.sales`（场景 `scenarios/erp_cc_push.py`，真模型，每条上限 1 美元）：站点脚本 `erp_push_held.py`
先造一张停下的单（提交后自己交一次写账、紧接着撤掉那一件，账本栏落成「失败」；被电脑先领走就记前置条件不满足）→ ERP 的 CC 对话里说
「把采购单 <单号> 写进 Express」→ 写入计划 → 核点头之前账套 = 基线 → 点「写入」→ 只读站点脚本 `erp_doc_state.py` 等写成 →
读回同网页提交直写的那套断言，另核这一件是直写不是代录、写账件只多一件 → 三条底线。基线减 2，919 → 917；登记 34 条。
产品现状三条已发给引导式施工窗口并抄主会话；引导式 P2 合入后这条的起点要按新步骤表重写。

### 第 3 批（续）：代录的对话那一半

对话驱动放开了代录模式（真模型驱动：`open_conversation("real", "erp", "operator", company=…, session={"period": "YYYY-MM"})`，站点建对话时带
`mode=operator`，同一事务里建代录任务；假智能体那层仍只接直写）。新场景 `acc.erp.operator.talk.purchase`（`scenarios/erp_operator_talk.py`，上限 1 美元）：
开一张已提交、没交进写账队列的现购单（`erp_push_held.py` 的 `leave = "queued"`）→ 代录对话里说「开始录这个月的单」→ 写入计划 →
核点头之前账套 = 基线 → 点「写入」→ 等 CC 桌面版在 Express 界面里录成 → 读回 + 代录特有两样（写账件是代录交的、只这一件）→ 底线。
登记 35 条。没做的对话用例（Express 弹框带回对话点选、被人打断后继续 / 停止、同一张再录一次默认跳过、定不了的行开始前卡片）：
驱动已能开代录对话，这些各要一种造得出来的现场（弹框、有人动键鼠），留给下一批对着真机设计。

### 第 4 批（续）：MR.ERP 再补四种

`acc.erp.mrerp.purchase.cash`（现购，T7）、`.sales.discount`（整单折扣的赊销，T8）、`.purchase.novat`（无税卖家，T8）、
`.purchase.nobook`（单据日期不在任何账套期间里，T15：停下「没有账套」、不导入、不去读 MR.ERP）。同一个站点脚本、同一个场景模块，
都登记成每周跑。登记 39 条。仍没做：要人选菜单 / 再写一次 / CC 对话推送 / 导入中作废（T9–T12）、关开关（T16）、税报告（T18）。
现购的贷方科目、称谓分栏没核（读回的单据页里对方档只有代码与名称；称谓要读对方档页，下一批加）。

## 跑单脚本（11-15 晚，主会话点头）

`scripts/acceptance/first_run.py`：按首跑顺序一条命令跑一组（`--group erp | operator | chat | mrerp | cowork`，可多给），失败即停，
每格记「通过 / 不通过 / 没测到（原因）」，最后出一屏结果（三种各几格、模型花费、每格一行），同时存进这次运行的证据目录。

- 开跑前把这一趟要占的资源一次列清并检查：被别人占着、要账套服务器而不在 08:00–20:40、缺环境变量、没给 `--holder`，有一样就一条都不跑、说清缺什么。
- 会花模型费的格默认跳过并记「没测到」，给 `--paid` 才跑；一屏结果列本次花费。
- `--list` 只列会跑什么、要占什么，不碰任何机器；`--from <场景>` 从某一格接着跑；`--record` 登记证据；`--stop-on-not-run` 没测到也停。
- 例（Mac 栈、ERP 那 21 格）：

```bash
PEARNLY_ACC_SITE_TARGET=maclab PEARNLY_ACC_ERP_USER=acc.erp@maclab.test PEARNLY_ACC_ERP_TAX=1234567890123 PEARNLY_ACC_BASELINE='C:\PearnlyAcceptance\baselines\TEST2@2026-11-13' python3 scripts/acceptance/first_run.py --group erp --holder <窗口名> --record
```

它只检查占用锁，不替人排时间：测试机、test01 的时间窗照旧先问主会话。

## 首跑结果（11-16，验收首跑窗口接手；环境：本机 Mac 实验栈 erp + 测试机2 CC 正式版 1.12.21，模型费 0）

两段证据，账套路径不同：
- **旧路径**：`\\accserver\d$\ACCOUNT\69EXP\TEST2`，基线 `TEST2@2026-11-13`（12:2x–14:2x）。
- **新路径**：`\\accserver\ACCOUNT\69EXP\TEST2`，基线 `TEST2@2026-11-16`（14:3x 起，主会话 11-16 选 A）。两份基线内容逐文件相同；验收数据根按新路径重建，旧的留在测试机2 `C:\PearnlyAcceptance\root-dshare-20261116`。
  换路径的原因：代录在 Express 程序（`P:\69EXP`，P: = `\\accserver\ACCOUNT`）的公司登记表里按路径找账套，d$ 写法认不出（产品侧已转 CC 桌面版窗口评估按 uid 认，见共享笔记「验收首跑-代录选公司-11-16.md」）。

| 场景 | 结果 | 路径 | 证据（`~/pearnly-devtools/acceptance-runs/`） |
|---|---|---|---|
| erp.company.connect.fresh / connect / twice | 通过 | 旧 | 20261116-123142-942b00243 |
| erp.denied | 通过 | 旧 | 同上 |
| erp.purchase.express | 通过 | 旧、新 | 同上；20261116-143948-011ae64eb |
| erp.purchase.express.twice | 通过 | 旧 | 20261116-124458-0309abf12 |
| erp.sales.credit.express | 不通过（产品缺陷 1） | 旧、新 | 20261116-124458-0309abf12；20261116-144225-011ae64eb |
| erp.collection.three / nbsp / web.twice / line.edit / line.refused | 不通过（产品缺陷 1，其余断言全过） | 旧（three 另有新） | 20261116-125905 / 130425 / 130742 / 131059-b3a07e794、131859-fd599332b；20261116-144636-011ae64eb |
| erp.stock.in / out / adjust / transfer / void / count | 通过 | 旧（in 另有新） | 20261116-132624 … 134427-4ae7502df；20261116-144443-011ae64eb |
| erp.pos.day.express | 通过 | 旧 | 20261116-135053-56938b89d |
| erp.pos.day.vat.remembered（新格） | 通过 | 旧 | 20261116-142209-8708c99ce |
| erp.purchase.cancel / sales.cancel | 通过 | 旧 | 20261116-140416 / 140925-d00f28a43 |
| erp.operator.purchase.express | 通过 | 新 | 20261116-143210-011ae64eb |
| erp.operator.sales.express | 不通过（产品缺陷 2） | 新 | 20261116-143426-011ae64eb |
| erp.operator.talk.purchase | 没测到（Mac 栈没接模型；驱动变量未配） | — | 20261116-143852-011ae64eb |

产品缺陷（报主会话，不在本分支修）：
1. 未付款 ERP 销售单的 `payment_method` 存成 `cash`（`sales/entry.py:317` 给 None，Select 无空选项被 Frappe 补成第一项；6321901ab 只修了前端）。修复在 `pearnly/select-blank-first`。
2. 代录写成的单不把新建的客户 / 商品码挂回 ERP 档：CC 的代录回执把码放在 `meta.operator.masters`，服务端 `cc_desktop/jobs.py:69` 只读顶层 `party_code`，`express/push.py:691` 的 `link_party` / `link_products` 不执行 → 主档同步回来会另建一条。

首跑校准改掉的场景 / 工具问题（只改验收脚本、maclab 测试机脚本与单测，不改产品代码）：
- 账套资料按 40 位账套键读（公司上的 express_book 只是显示名）；重报轮询清 Frappe 进程内缓存；release 还原 CC 配对改掉的全局 `backend_url`。
- 一组共用运行编号：`erp_chain.tag` 与采购单号带场景校验码（否则同一趟里撞单号 / 客户名）。站点脚本崩溃时把报错尾巴带回。
- 收款：银行流水按账本 ISRUN 的 SHORTNAM 是否指到 BKMAS 户头判（同 `dbf_receipt.plan_bank`）；收款 / 库存 / 调拨写账件不交 verify 元数据（写入时已逐表自核），按「成功且 verify_state 空或 ok」判。
- 每格还原基线后发票号 / 客户编码会重用：销售站点脚本开单前清测试站点上这本账套的未收快照与没取走的读回。
- 库存单只挑 Express 库存品（`express_stock = 1`）；收银第一次写日汇总照用户正路答一次「是不是增值税登记户」；`ledger diff --allow=` 写成一个参数。
- maclab 档案沿用被暂停的同通道档案的 Express 程序路径（验收数据根下没有 ExpressI.exe）。

缺口（主会话定，后面排）：
- 收款「转账方式经 SHORTNAM 指到户头 → 记银行流水」那一支：TEST2 的转账方式 SHORTNAM 都是空的，测不到；要测需在 Express 里给一种方式设 SHORTNAM 并重取基线。
- operator.talk.purchase：Mac 栈没接模型，改在测试机1 实验栈或 testerp 上跑。
- 旧路径证据的格（除抽查过的 4 格）明天按新路径补跑一遍。

## 第 5 批：真机线 11-16 问题补成场景（修复前红）

11-16，本地分支 `pearnly/acceptance-scenarios-1116`（基点 a1cf24198），只在本地提交。五条都按「修好后的正确行为」断言：对应修复在
别的窗口、还没进 main，现在跑应当不通过；修复进 main 并部署到被测站点后应转绿。都没跑过（不连测试机 / 实验栈），模型费 0、不跑识别。
组：`first_run.py --group fixes`（顺序：未连接 → 办公室电脑 → H3 → H5 → 挂码；后三条占测试机桌面）。替身单测
`tests/test_acceptance_erp_chain.py` 两条新用例：假世界「修好」时五条都通过，「没修」时各自不通过且原因指到那个缺陷；造不出现场时记没测到。
登记 45 条；欠账基线 986 → 984（`erp.cc.operator.submit|misuse:M07`、`erp.desk.pearnly-purchase.action.Submit|misuse:M07` 由未连接、
办公室电脑两条还上）。共用件 `erp_chain.py` 加了 `purchase_tables()` / `purchase_readback()`（现购单读回，同示范 1 与代录采购那套）。

| 场景 id | 怎么造现场 | 断言 | 依赖的修复 |
|---|---|---|---|
| `acc.erp.operator.intake.purchase`（H3） | 站点脚本 `site/erp_operator_card.py`（kind=intake）照识别结果的样子直接造一条「已识别」的收单记录 `I:<LI-…>`（写死：现购、验收公司里第一位 Express 供应商、一行已有库存品、票面合计），行物料 / 仓库已定；建代录任务 | 开始前卡片（`write_precheck.api.card`，写入计划与页面卡片同一判定）列得到这张、不是 `unknown_document`；按卡片作答经 `express_operator.api.remember` 记下不报没有权限；`hand_in` 后收单记录提交成 ERP 采购单、代录录成（代录交的、只一件、任务里已录入同单号）；账本读回同代录采购 | `pearnly/erp-operator-intake`（32ec86ccf，H3 窗口） |
| `acc.erp.operator.stock.group`（H5） | 同脚本 kind=stock_group：现购一件新建的 ERP 物料（账套里没有）交代录；开跑前清掉这家公司记过的存货科目组选择，跑完原样还原 | 开始前卡片问了物料组、合格的组都在选项里；答第一项（推荐）后再判不再问；写成，STMAS 新物料的 ACCCOD = 选的组、只多一行；万一停下，另核单据上带原因与可选项（S2） | `pearnly/cc-stop-exits-h5`（d818a1791，S1 + S2） |
| `acc.erp.operator.masters.link`（首跑缺陷 2） | 同脚本 kind=new_masters：新供应商（校验位正确的税号）+ 新物料的现购单交代录；写成后等主档同步做完（最多 7 分钟） | 供应商档 / 物料的 express_code = 账本 APTRN.SUPCOD / STCRD.STKCOD；同步后这一轮新建的档里同一个码只有单上那一条；APMAS、STMAS 各只多一行 | `pearnly/operator-masters-link`（e7d615d3b） |
| `acc.erp.operator.no.computer`（未连接） | `site/erp_operator_no_computer.py`：没有电脑的员工开现购单（不排直写）、建任务、`hand_in`；读完撤掉那一件、作废这张单、任务标停止。**不写任何账套**、不占测试机 | 写账件排着、归发单人；单据页原因（`push._queued_note`）、代录钩子 `computer_state`、模型的 `express_operator_status.no_computer`、对话投影（`feed.project`）都说「CC 桌面版未连接」 | `pearnly/erp-operator-intake` 的 341e08624 |
| `acc.erp.office.actor.purchase`（office_actor） | `site/erp_office_actor.py`：只用手机的员工（从没在电脑上登录过 CC）开现购单提交（直写正路） | 写账件不归发单人、归 `push.office_actor` 选的人（测试机那台 CC 上登录的员工）；排队时单据页不说未连接；那台电脑写成、单据仍是发单人的；账本读回。修复前写账件归发单人：脚本不空等，撤件作废后交回，场景判不通过 | 评估「ERP 写账人改走 office_actor」（流程 `erp.office-actor` 红绿证据在共享笔记，代码还没推到远端分支） |

没写成留桩的：无。五条都能从现有服务端入口走到；但两条要先备现场，没备好跑到时记前置条件不满足：
- **未连接**：要另配一家公司——CC 报上来了它的账套，但够得到这本账套的电脑上登录过 CC 的人里没有这家公司的员工（否则 office_actor 修好后
  会由那台电脑兜底，正确结果就不是「未连接」），以及这家公司一个从没在电脑上登录过 CC 的员工。环境变量 `PEARNLY_ACC_ERP_NOPC_USER` /
  `_TAX` / `_BRANCH`。验收公司本身造不出这个现场（测试机那台 CC 的员工就在这家公司里）。
- **办公室电脑**：验收公司里一个有开采购单权限、从没在电脑上登录过 CC 的员工（`PEARNLY_ACC_ERP_PHONE_USER`）；测试机那台 CC 此刻在线。
- **H5** 另要验收账套合格的存货科目组两个以上（站点脚本按账套资料判，不到两个记前置条件不满足；69EXP\TEST 有 DM、P002 两个，TEST2 没核）。

首跑要校准的：H3 收单记录里供应商按名称 + 税号认回（产品认不回会新建供应商，读回的供应商编码断言会报出来）；卡片作答规则（已定不动、
预选照预选、物料 / 对方「新建」、其余第一项）是否覆盖 TEST2 实际出的题（答不了的列在 `unanswered`，断言会报）；挂码那条主档同步的等待时长。

## 新路径补跑（11-16 17:08–18:20，主会话：只在旧路径跑过的格按新路径补）

环境：站点重置（首次添加那格可重跑）、`\\accserver\ACCOUNT\69EXP\TEST2`、基线 `TEST2@2026-11-16`，被测代码 a1cf24198（= main 9e0e102ab + 本分支）及 cea84e06b。

- 通过 18 格：company.connect.fresh / connect / twice、denied、purchase.express / .twice、stock.in / out / adjust / transfer / void / count、
  pos.day、pos.day.vat.remembered、purchase.cancel、sales.cancel、operator.purchase（复跑）。证据 `acceptance-runs/20261116-17*-a1cf24198`、`20261116-18*-cea84e06b`。
- 不通过 6 格：sales.credit、collection.three / nbsp / web.twice / line.edit / line.refused —— 只差产品缺陷 1（payment_method）。
- 于是旧路径跑过的格在新路径上全部补齐；operator.sales 新路径证据在 14:34（缺陷 2）。
- pos.day 第一次不通过（落点为空）：查出**可疑产品竞态**（报主会话）——`pos_day._summarize` 已 `push.queue` 排后台写账，站点脚本又同步 `push.send`，
  `push._send` 无行锁，答题前算出「要问」的那次晚落库，盖掉答题后写下的 `ledger_book` 并把旧题写回；回执只改状态 → 写成但字段不一致。
  产品里 `push.poll`（每分钟）与用户答题（`express.api.answer` 也 send）同样能撞上。站点脚本已改成等产品自己的后台写账（cea84e06b），复跑通过。

## 第 5 批首跑（修复前红，11-16 18:1x）

| 场景 | 结果 | 红在哪 | 转绿要等 |
|---|---|---|---|
| acc.erp.operator.intake.purchase（H3） | 不通过 | 开始前卡片认不出收单记录（not_found） | `pearnly/erp-operator-intake` |
| acc.erp.operator.stock.group（H5） | 不通过 | 没问物料组；写时 STOCK_ACC_GROUP_UNFIT（ST01 → 14-01-03-00 不像存货）停下且无可选项 | `pearnly/cc-stop-exits-h5` |
| acc.erp.operator.masters.link（缺陷 2） | 不通过 | 被 H5 先拦（新物料）走不到挂码 | H5 **和** `pearnly/operator-masters-link` 都进 main |
| acc.erp.operator.no.computer | 没跑 | 要另配一家「电脑上没有它的员工」的公司和一个没登录过 CC 的员工（环境变量见场景文件头） | 现场 + 341e08624 |
| acc.erp.office.actor.purchase | 没跑 | 要配一个只用手机的员工（`PEARNLY_ACC_ERP_PHONE_USER`） | 现场 + office_actor 修复 |

证据 `acceptance-runs/20261116-1814*`–`1817*-cea84e06b`。H5 现场在 TEST2 上原样复现，说明验收账套就能守这条。

## 验收总表（产品 × 入口 × 场景；11-16 18:3x，按这张表报数）

目标（用户）：覆盖整个产品、上线前必过。下表只算 **L3 真环境场景**（真账本 / 真 CC 桌面版）；L2 流程（实验栈）133 条另算。
「有证据」= 当前登记里有新鲜的真环境证据（全部在新路径 `\\accserver\ACCOUNT\69EXP\TEST2` 上，被测提交 a1cf24198 / cea84e06b）。证据只登记通过的格；修复前红的格只有运行目录。

| 产品 | 入口 | 登记场景 | 有证据（通过） | 跑过未过（产品缺陷 / 修复前红） | 没跑 |
|---|---|---|---|---|---|
| ERP | 网页 / 服务端（公司接入、权限、采购、销售、库存、收银、作废、收款网页） | 20 | 16 | 4（缺陷 1：sales.credit、collection.three / nbsp / web.twice） | 0 |
| ERP | LINE 收款 | 2 | 0 | 2（缺陷 1：line.edit / line.refused） | 0 |
| ERP | CC 代录 | 8 | 1（operator.purchase） | 4（缺陷 2：operator.sales；修复前红：intake H3、stock.group H5、masters.link） | 3（talk：Mac 栈没接模型；no.computer、office.actor：要另备现场） |
| ERP | CC 对话直写（真模型） | 4 | 0 | 0 | 4（真模型，费用待批；Mac 栈没接模型） |
| ERP | MR.ERP 直写 | 9 | 0 | 0 | 9（每周跑；要 test01 时段与三条依赖分支） |
| Cowork | 录入页 → Express | 2 | 0 | 0 | 2（只在测试机1 实验栈跑，主会话排） |
| **合计** | | **45** | **17** | **10** | **18** |

全产品入口盘点（`check-acceptance --report`，11-12 快照，动作级）：787 个动作，其中有断言到业务结果的自动测试 399、只到页面或只有单测 269、只有部署检查或手工 25、完全没有 94；
会写外部账本的产品地图行 57 条。L3 验收目前只覆盖 ERP 写 Express 这一条主链和收款；**没覆盖到的大块**：Cowork（除录入页 2 条）、DMS 与各 LINE 官方号、管理后台、登录与身份、订阅计费、CC 桌面版本体（只经代录 / 直写间接覆盖）。
下一步对齐：引导式全覆盖总表（窗口 guided-cowork 在出）出来后，按它的「格」给每条验收场景标格号，没有场景的格列成欠账。

### 报数口径
- 每次汇报给：登记 / 有证据 / 跑过未过 / 没跑 四个数，加上「会写外部账本的行有新鲜证据的条数」。
- 修复进 main 后由本窗口复跑对应的格并回填（缺陷 1 → sales.credit + 收款 6 格；缺陷 2 → operator.sales、masters.link；H3 / H5 → intake、stock.group；office_actor → no.computer、office.actor）。

### d$ 写法回归（11-16 19:0x 前，CC 桌面版接手窗口 local_27c53845 借验收设施跑）
测试机2 装 CC 1.12.23 测试包（按目录 uid 认同一账套），用 d$ 写法的数据根 `root-dshare-20261116` 登记 TEST2，跑 `acc.erp.operator.purchase.express`：
**通过**，代录写入 HP0000090，场景还原、逐文件与基线一致（该窗口记录）。即「两种共享写法认不出」修好后的真机证据；转正式版后本窗口用 d$ 写法再跑一次登记。

## 第 6 批：收款不正常使用、MR.ERP 剩余（11-16，本地分支 `pearnly/acceptance-batch6`，基点 batch5 29c4187a3）

只改验收脚本、登记、单测与本记录，不改产品代码。**都写好未跑**（不连测试机 / 实验栈 / 生产），模型费 0。登记 45 → 56 条；
欠账基线 984 → 976（还上 8 项：收款网页新建、LINE 按钮、CC 写入工具三行的 M07、M08，LINE 按钮的 M02、M16）。
替身单测（`tests/test_acceptance_erp_chain.py` 新增 4 个用例）：假世界正确时每条通过、产品出错时各自不通过且原因指到那一条、造不出现场时记没测到。

### A. 收款的不正常使用（模块 `scenarios/erp_collection_misuse.py`，站点脚本 `site/erp_collection_misuse.py`）

每条先开一张赊销发票（复用 `erp_collection_express.open_sale`），再在造出来的现场里收它；都从服务端函数走（via = "api"）、跑完恢复基线。

| 场景 id | 怎么造现场 | 断言 | 组 |
|---|---|---|---|
| `acc.erp.collection.busy`（M08） | 测试机上 `ledger.py hold`（新子命令）像 Express 开着窗口那样**共享**开着 ISRUN / ARTRN / ARRCPIT 300 秒，探测确认 ISRUN 独占打不开（CC 写前就是这样判 Express 开着，`companion/account_lock.py`） | 占着时网页 / LINE / CC 三笔都没写成、停在待写入、各只一件写账件、都判成「在等 Express 关闭」（`service.express_waiting`，收款员看到的那句）；占着时拷的现场里没有收款单；放开后三笔都补写成、三张 RE 各不相同、名下仍各一件写账件；账本读回同 `collection.three` | erp |
| `acc.erp.collection.offline`（M07） | 新脚本 `win/cc_power.py` 经桌面用户通道停掉测试机2 的 CC 桌面版；收完再起、等起来之后的心跳 | 停着时三笔都没写成、待写入、写账件没被领走、账本没有收款单；起来后都补写成、只各一张；半路不通过收尾先把 CC 起回来 | offline（新组） |
| `acc.erp.collection.line.redelivered`（M02） | 假 LINE 号（`Uacc…`）绑验收员工 → LINE 草稿 → 出确认卡 → 同一个【确认】按 webhook 那套函数投三次：首投、同一事件号重投（LINE 补发）、新事件号同一按钮（连点） | 首投领到并处理；补发的同一事件号不再处理；连点那次不回话；只一件写账件、同意记的是首投事件号；写成、账本只多一张 RE | erp |
| `acc.erp.collection.line.unbound`（M16） | 同上出卡 → 解绑 → 用旧卡点【确认】；另一个从没绑过的号点「收款」、打「ยืนยัน」 | 点按钮的两次只回「请先绑定」、打字那次当绑定码试回「码不对」（按产品原句比，zh）；草稿原样（还是草稿、版本没变）、没有写账件 / 单号 / 同意；收款记录一条没多；没绑过的号没留会话；最后作废草稿；账本只有那张发票 | erp |
| `acc.erp.collection.books`（同期两份账套） | **留桩**（模块里走到就记前置条件不满足，没登记）：验收公司只报一本账套，站点脚本的防护就是「报上来的必须全是验收账套」；要第二本同期测试账套（如 69EXP\TEST 与 TEST2 期间重叠）、它的整本基线、测试机 CC 对这家公司报两本，再把防护放开成「都在这两本里」 | — | — |

LINE 两条**不发任何 LINE 消息**：只在站点脚本进程里把共用层的回复 / 推送 / 加载动画换成记录器、把 `pearnly_core.line` 的 HTTP 挡掉、绑定变更钩子不跑、
LINE 收款的后台任务（等结果、通知）不排；验收员工已绑了别的 LINE 号就拒绝（不顶掉真绑定），假号跑完解绑。验签那一步不在这里（真 LINE 在真机清单 2）。

首跑要校准的：
- 占着 / 停着时看 90 秒够不够 CC 去试一次并回报「等账套」（`express_waiting` 只在 CC 回报过之后为真）；放开后服务端多久再派（settle 等 300 秒）。
- `ledger.py hold` 在 SMB 共享上由同一台电脑另一个进程共享打开，CC 的独占试开是否同样被拒（Windows 共享模式按句柄判，预期是拒；没实测过，首跑看）。
- 停 CC 后服务端多久判这台电脑离线、三笔是否一直不被领；`cc_power.py start` 在一次性计划任务里起的 CC 任务结束后是否还在（cfg_guard 同一做法已用过）。
- 验收员工要有 LINE（`integration.read`）与收款写入权限，没有就记前置条件不满足。
- offline 会停 / 起测试机2 的 CC：跑前先问主会话；占 `testpc2-desktop`，与代录不同时跑。

### B. MR.ERP 剩下的（同模块 `scenarios/erp_mrerp.py`、站点脚本 `site/erp_mrerp_post.py`，都每周跑、占 `mrerp:test01`）

| 场景 id | 怎么造现场（站点脚本 kind） | 断言 |
|---|---|---|
| `acc.erp.mrerp.menu.choice`（T9） | `menu_choice`：提交前去掉公司在这本账套下记的「采购」导入菜单 → 导入 → 以开单人 `express.api.answer` 选回原来那个 → 再导入；没记回就按原样写回 | 第一次停在 `choice_needed:mrerp`、没单号；选项里有原来那个菜单；选后写成、公司上又记着它；读回同 T1。只有一个可选菜单时自动选了 → 记没测到 |
| `acc.erp.mrerp.retry`（T10） | `retry`：第一次导入只在进程里让 `connection.lease` 回空登录名（不改存的凭据）→ 停在没登录 → 以开单人 `express.api.retry` | 第一次停在 `mrerp.login_missing`、没单号；单据页可「再写一次」；点了写成、读回 |
| `acc.erp.mrerp.cc.push`（T11，真模型，budget_usd = 1，组 chat） | `held_kicked`：记这个账号「被踢」→ 提交 → 自动导入快速失败停下（不清标记）→ ERP 的 CC 对话说「把采购单 X 写进 MR.ERP」→ 写入计划 → 点写入 → `readback` 等导入落定 → 读回；没写成收尾 `clear_kick` | 自动导入没写成；一张写入计划、一张单；点了写成（= 人点的推送清掉了被踢标记）；读回同 T1；底线 b1 / b6 / b7（MR.ERP 单号 `PG/PN/SI/SE……-……`）。**被测站点源码里写账开关关着时记没测到**（对话在网页进程里跑，只在站点脚本进程里打开不算数）：开关怎么在实验栈打开要主会话定 |
| `acc.erp.mrerp.void.importing`（T12，M03） | `void_importing`：包一层 `pearnly_core.mrerp.push.execute.execute`，真导入回来、记结论之前以开单人 `sales.entry.cancel` | 导入其实写进去了；单据作废、账本栏 `held` + `express.voided_after_write`、单号留着；MR.ERP 列表里恰好一张、分录对得上 |
| `acc.erp.mrerp.switch.off`（T16） | `switch_off`：开着开关提交（排队）→ 关开关跑 `mrerp.push.poll` 与 `express.push.mrerp_run` → 开回再导入 | 关着时 `handles` 假、仍排着没单号、公司的 express_enabled / mrerp_enabled / 账套键不变；开回认得、写成、读回 |
| `acc.erp.mrerp.tax.purchase` / `.tax.sales`（T18） | `tax_purchase` / `tax_sales`：写成后在 MR.ERP 归期页（进项 acvatdsel 210 / 销项 acvatsaledsel 214）只勾这一张、报税日期 = 今天佛历 → 取税报告 xlsx（进项 226/43、销项 232/45） | 归期前这张税票恰好一条待归期；提交后不再待归期；报告里恰好一行列着它的税票号、那一行有一格 = 单据税额。**会改 MR.ERP 测试账套里本次这张单的归期**（TEST2019 专属测试） |
| 称谓分栏读回（改现有 `purchase.goods`、`sales.credit`） | 读回时另取 MR.ERP 对方档查看页（`apmas` / `armas`，按单据页对方代码找档）；`sales.credit` 的客户名改成「ห้างหุ้นส่วนจำกัด …」 | 供应商档称谓栏 = บริษัท、名称栏「เอซีซี … จำกัด」；客户档称谓栏 = หจก.（全称落成称谓表里的简称，11-15 T3 缺陷 84facae90）、名称栏不带它 |

首跑要校准的：归期页与税报告的提交字段、报告列位（只核「有一格 = 税额」）、佛历日期格式（照写账窗口 11-15 实测 `15/11/2569`）；
销项税票号取单据页 `txtvatnum`（进项是我方单据号）；`void.importing` 作废时机靠包住导入函数，真导入耗时几十秒；
称谓下拉的选中项文字（`selprefix` 选中的 option）。MR.ERP 里的单与归期都不删，数据累积。

### 留桩与没做

- 留桩：`acc.erp.collection.books`（见上，没登记、不还欠账）。
- 没做：收款 M03 / M04 / M05 / M11 / M12（CC 草稿 / 写入工具、LINE 按钮那几项）——要对话或界面驱动；MR.ERP 同期两本选一次（test01 下只有一份同期库）。

## 晚间一轮（11-16 19:1x–19:50，被测 = main 847fc020c（含 H3 修复 erp-operator-intake、写账并发锁）+ 本分支）

| 场景 | 结果 | 说明 | 证据 |
|---|---|---|---|
| acc.erp.operator.intake.purchase（H3） | 不通过（**主流程已转绿**，差 1 条） | 卡片认出 `I:`、记下不 403、交单成采购单、代录写成 HP0000090、账本读回全过；只差「代录任务里这张已录入带单号」：任务视图里这张仍是 `I:LI-…` / queued / 单号空。待查：交单后任务条目不跟随提交出的 P: 单（报主会话转 H3 修复窗口） | 20261116-191454-eef4b4c34 |
| acc.erp.collection.offline（M07） | **通过** | 先在线把发票读进快照（场景修正 574d61f9b），停 CC → 网页 / LINE / CC 三处都停在待写、写账件没人领、账本不动 → 起 CC → 写成、各只一张 | 20261116-19*-574d61f9b |
| acc.erp.operator.talk.purchase（真模型） | 不通过（产品：收尾不报单号） | 第 1 次站点有前几格残留的待写单（计划 4 张、两张选择卡），作废残留后第 2 次：计划只这一张、无要人处理的卡、写成 HP0000090、账本读回一致；**最后的回复没提写成的单号**。「回复语言」那条是判定误判（中文回复里有泰文公司名被判成 th），已修判定（13b34adf5），不重跑 | 20261116-193723-574d61f9b、第 2 次同日 19:44 |
| acc.erp.mrerp.cc.push（真模型） | 没跑 | maclab 站点上没有接 MR.ERP 测试账套的公司；test01 登录会踢掉别处会话，MR.ERP 写账窗口今晚在用。要先在 maclab 配 MR.ERP 公司接入，排写账窗口错开的时段 | — |

模型花费台账（11-16）：operator.talk 两次 0.138 + 0.114 = **0.252 美元**（批的上限两格合计 2 美元）；mrerp.cc.push 0。密钥只临时写进 maclab 站点配置，跑完已删并回读（engine / key 都为空）。

## 验收场景 × 引导式全覆盖总表的格（11-16 19:5x 对齐；总表见共享笔记「引导式-全覆盖总表-11-16.md」）

引导式总表的格是「产品 × 直写 / 代录 × 业务 × 账本」（范围内 65 格），验收 L3 场景守的是格背后同一条写账链（网页 / LINE / CC 交进去的单写进真账本并读回）。对得上的：

| 格 | 验收场景（L3） | 状态 |
|---|---|---|
| ERP-D-P-L 采购·直写·本地 | purchase.express / .twice、purchase.cancel、operator.intake.purchase（放票这条入口） | 有证据 3；intake 差 1 条 |
| ERP-D-S-L 销售·直写·本地 | sales.credit、sales.cancel | cancel 有证据；credit 缺陷 1 |
| ERP-D-COL-L 收款·直写·本地 | collection.three / nbsp / web.twice / line.edit / line.refused / offline / busy / line.redelivered / line.unbound、cc.collection / .nbsp | offline 通过；5 格缺陷 1；其余没跑 |
| ERP-D-ADD 公司接入 | company.connect.fresh / connect / twice、denied | 有证据 4 |
| ERP-D-ST 盘点 | stock.count | 有证据 |
| ERP-D-SC 库存收发报表 | （无：只读报表，L2 流程管） | — |
| ERP-D-PD-L 待处理 / 结果核对 | cc.push.purchase / .sales（停下的单再写一次） | 没跑（真模型） |
| ERP-D-P-M / S-M MR.ERP | mrerp.* 16 条 | 没跑（每周 + test01 时段） |
| ERP-O-P-L 采购·代录·本地 | operator.purchase、operator.talk.purchase、operator.stock.group、operator.masters.link、office.actor.purchase、operator.no.computer | purchase 有证据；talk 产品缺陷（不报单号）；H5 / 挂码修复前红；两格要备现场 |
| ERP-O-S-L 销售·代录·本地 | operator.sales、operator.talk.sales（第 7 批，对话） | operator.sales 缺陷 2；talk.sales 有场景，没跑 |
| Cowork-D-P-L 采购·直写·本地 | cowork.intake.express / .twice、cowork.cc.direct.purchase（第 7 批，CC 对话放票） | 有场景，没跑（intake 只在测试机1 实验栈；cc.direct 真模型 + 一次真识别） |
| Cowork-D-S-L 销售·直写·本地 | cowork.cc.direct.sales（第 7 批） | 有场景，没跑（样票 sales-a1 待补，跑到记没测到） |
| 其余 Cowork 直写 / 代录、所有 云端 Express 列、ERP 盘点外的库存单（入库 / 出库 / 调整 / 调拨 / 作废 / 收银日汇总） | 库存 6 格、pos.day ×2 有证据，但引导式总表没有单列这几类格（在「范围外」或归入盘点）；云端列 19 格、Cowork 37+6 格中除直写采购 / 销售本地两格外**没有验收场景** | 欠账 |

数：总表 65 格里**有对应 L3 场景的 11 格**（ERP-D-P-L、D-S-L、D-COL-L、D-ADD、D-ST、D-PD-L、D-P-M、D-S-M、O-P-L、O-S-L、Cowork-D-P-L），其中**至少一条场景有新鲜真环境证据的 6 格**（D-P-L、D-S-L、D-COL-L、D-ADD、D-ST、O-P-L）；**其余 54 格没有验收场景**，排进第二期后续批次（先 Cowork 直写采购 / 销售本地、ERP 代录销售补齐，云端列等云端线出写法后）。

## 11-17 代他窗口验证与花费台账
- frontend-backlog `pearnly/cc-no-self-read-2`（670cd3516）真模型一格：ERP CC 直写对话放 3 页泰文发票 PDF「帮我看看这张发票金额」。
  被测分支：不逐页读、不报金额，一句话指向产品上传页，无「识别引擎」字样（0.185 美元）；main 847fc020c 对照：bash 逐页读、报全部金额并自行推算（0.103 美元）。环境 maclab erp（无测试机、不写账本），PDF 现生成不入库，密钥用完已删并回读。
- 模型花费台账：11-16 0.252 美元（operator.talk 两次）；11-17 0.288 美元（上面一格两次）。

（第 7 批后：有对应 L3 场景的格 11 → **12**（加 Cowork-D-S-L；Cowork-D-P-L、ERP-O-S-L 各多一条对话场景），没有场景的 54 → **53**；有新鲜证据的仍 6 格。）

## 第 7 批：对齐引导总表补的格（11-17，本地分支 `pearnly/acceptance-batch7`，基点 batch5 015d22b3c）

只改验收脚本、登记、单测与本记录，不改产品代码；**都写好未跑**（不连测试机 / 实验栈 / 生产），模型费 0。登记 56 → 59 条；
欠账基线 976 → 970（还上 6 项：Cowork 的 `cowork_push_documents`、`pearnly_write_start` 两行的「正常主线」「L3 正常主线」「串联」）。
新链 `cowork.cc-direct-to-express`；新组 `first_run.py --group cowork-chat`（采购 → 销售），ERP 代录销售排进 `chat` 组、在代录采购之后。

| 场景 id | 格（引导总表） | 走什么 | 状态 |
|---|---|---|---|
| `acc.cowork.cc.direct.purchase` | CW-D-P-L | Cowork 的 CC 对话（直写）放样票 purchase-a1、说「บันทึกให้หน่อย」→ 等写入计划（提问卡按每题第一项代答并留证，允许卡不替点）→ 核点头前账套 = 基线 → 点写入 → 只读站点脚本 `cowork_cc_state.py` 等推送记录 → 账本按单号读回 → 底线 ①⑥⑦⑧ | 写成，没跑 |
| `acc.cowork.cc.direct.sales` | CW-D-S-L | 同上，样票 sales-a1（卖方是验收公司、买方是基线里的客户），读回 ARTRN | **留桩**：样票库没有销售票，跑到记没测到（原因写明要补什么样的票）；其余步骤已写好、替身单测按补好的样票走通 |
| `acc.erp.operator.talk.sales` | ERP-O-S-L | 照 `talk.purchase`：开一张已提交、没交进写账队列的赊销单（新建「ร้าน…」客户）→ **开对话前核本月待录只有这一张** → 代录对话说「开始录这个月的单」→ 写入计划 → 点写入 → 等录成 → 读回同 `operator.sales.express` | 写成，没跑 |

示范 2（`cowork_cc_direct.py`，11-14 写、没登记）按现在的共用件改过：基线 / 现场目录 / 收尾 / 读回改走 `erp_chain`（与 ERP 各条同一套，重跑不撞目录）；
单号取 Cowork 推送记录（不再从账本差异里猜新增行）、核「只推了这一张、写的是验收账套、真写不是认作重复跳过」；对话建在样票那个月（`session.period`）；
站点目标可以是本机 Mac 实验栈；单号格式按 HP / RR / IV / HS 判。代录对话（采购、销售两条）开对话前都核本月待录（只读站点脚本 `erp_operator_sources.py`，
调产品的 `express.operator.sources`，代录写入计划取的就是它），不是只有本格这一张就记前置条件不满足并列出是哪几张（11-16 晚首跑第 1 次的教训）。
样票库与生成脚本支持销售票（`direction = "sales"`、`customer`）。替身单测：`tests/test_acceptance_cc_driver.py` 的 `DirectWriteScenarioTest`（重写：采购 / 销售正确时通过并恢复基线；
站点有残留待录单、没有销售样票时记没测到且不开对话；金额不对、推了两条、写到别的账套时不通过并留会话事件）、
`tests/test_acceptance_erp_chain.py` 新用例（代录销售正确时通过；代录不是代录交的、多一件写账、客户码没挂回时不通过；采购 / 销售两条有残留待录单时都记没测到）、
`tests/test_acceptance_samples.py` 销售票票面。

前置条件：
- **Cowork 两条**：Cowork 站点（本机 `scripts/maclab.sh up cowork`，站点 cowork.maclab.localhost；或测试机实验栈）接真模型（`pearnly_assistant_*` 临时写、跑完删并回读）；
  识别是真识别（Mac 栈站点名不在产品回放名单里，每遍约 ฿0.2–0.35，不算在 budget_usd 的模型用量里）；测试机2 的 CC 桌面版 `maclab_pc.py attach --host testpc2 --app cowork`、只报验收账套；
  环境变量 `PEARNLY_ACC_COWORK_COMPANY`（Cowork Company 编号，账套就是验收账套）、`PEARNLY_ACC_REAL_BASE / _SITE / _USER / _TARGET`（_SITE 要等于 Cowork 站点，否则记没测到）、
  `PEARNLY_ACC_BASELINE`；这家公司在样票那个月没有别的待录单、站点上没有前几轮没推成的 `ACC-` 样票。
- **销售样票**：要对着验收账套基线 ARMAS 挑一位真有的客户，按 `samples.toml` 文件头的格式登记 `sales-a1`（只记账的一行）。今天不碰测试机，没核基线，所以没登记。
- **代录销售**：同 `talk.purchase`（占测试机桌面、CC 上的员工做过 Express「授权登录」、20:40 前收尾）；站点上这家公司这个月没有别的待录单。

首跑要校准的：
- Cowork：识别后 CC 是先出判定卡（录不录、方向、对方、现赊、抵扣……）还是直接出计划——代答按每题第一项，答得不对就会写错（看 `cards-before-plan.json`）；
  推送记录按「站点时刻之后识别、票号或对方税号对得上」认，识别把票号读歪时只靠税号；票号记在 REFNUM 还是 YOUREF（现为软断言）；
  采购票 purchase-a1 是一行货品（น้ำแข็งหลอด）：账套里没有这个物料时会问新建物料或停下，必要时换 purchase-a2（服务）；
  允许变化的表沿用采购 / 销售直写那两份清单。
- 代录销售：首跑缺陷 2（代录回执的码不挂回 ERP 客户）没修好前，这条停在「新客户在 ERP 档上挂回了 Express 客户编码」；最后的回复报不报单号（采购那条 11-16 晚就差这一条）。

## MR.ERP 组首跑（11-17 09:4x–09:51，maclab erp + MR.ERP 测试账套 test01 · TEST2019；写账窗口让出 test01 到 13:00）
- 现场：maclab 站点新建测试公司 CO-00864（บริษัท ทดสอบ ACC-MRERP，0105989581512），按写账窗口 erp_lab.py 的 cmd_setup 接 MR.ERP（连接、选 6:1、读菜单与默认；默认 2120-03 / 1112-01 / 5390-09，菜单 68 / 454 / 119 / 126）。
  MR.ERP 写账开关只在临时工作树挂上去的源码里改 True（不提交）；凭据只经标准输入进容器。跑完删临时工作树、模型密钥并回读。
- 结果：**16 格全部通过**（menu.choice、purchase.goods / expense / cash / novat / nobook、sales.credit / cash / discount、twice、retry、void.importing、switch.off、tax.purchase / sales、cc.push 真模型）。证据 `acceptance-runs/20261117-094*`–`0951*-5def7f3c3`。
- 首跑校准（脚本问题）：单据页单号拆在 txtdoctype + txtdocnum 两栏；多仓公司开单要带仓；第一次导入碰 ERR_MRERP_ACCOUNT_IN_USE（接入时的登录与第一格互踢），单独重跑即过；销售菜单要正式选过（预选值不算）。
- 花费台账：cc.push 两次 0.2545 + 0.125 = 0.380 美元；11-17 累计（含代 frontend 验证 0.288）0.668 美元。

## 代录三件修复复跑（11-17 11:33–11:44，maclab erp 挂 pearnly/operator-intake-receipt c6bcde9db + 测试机2 CC 1.12.21）
| 场景 | 结果 | 说明 |
|---|---|---|
| acc.erp.operator.intake.purchase | **通过** | ① 修好：交单后任务视图这张变已录入、带 Express 单号 |
| acc.erp.operator.talk.purchase ×3 | 不通过 ×3 | ② 没修：三次最后回复都没报写成的单号（HP0000090 / HP691117-001）；③ 没复现：三次可见文字都没有英文步骤句或工具名。第 2 次写账件不是代录交的（单号是直写格式 HP691117-001）：后台直写先领走了这张——场景前置没守住，按规矩应记「没测到」，场景要在交代录前核这张没被直写领走 |

花费：0.100 + 0.248 + 0.131 = 0.479 美元（11-17 累计 1.147 美元）。主会话 11:4x 通知暂停，Cowork A7 两格（被测 cc-remembered-every-basis f32b4325b）与 1.12.21 补 7 格、三条新场景改到明天。

## A7 两格登记（11-19）
- `acc.cowork.intake.detail.amount_only` / `.skip`（场景 `cowork_intake_express.py` 的 A7 分支，站点脚本 `cowork_intake_post.py` 的 `detail` 参数），进 first_run 的 cowork 组；站点目标可由 `PEARNLY_ACC_SITE_TARGET` 指成 maclab。
- 首跑（11-19 11:5x–12:06，小驱动，被测 cc-remembered-every-basis f32b4325b、maclab cowork + 测试机2 + TEST2）：两格通过——只记金额写成 RR691119-001（APTRN 税前 100 / 税 7 / 合计 107、STCRD 无行、凭证一张、进项税一行）；不入账停在 manual / items_mismatch、账本不动。ERP 两格「不适用」（ERP 单据合计按明细算，对不上时写账前硬拒，guided-erp 定）。
- 前置：Cowork 站点要配智能体引擎（`sessions.create` 要连引擎建会话，不发消息、模型费 0，用完删密钥）；手造的识别记录要写上 `push_connection` / `push_account_set`（CC 对话放票时产品本来就写）；新接的 Cowork 公司首次判卡要等电脑读一次科目表。
- 测试机2 基准哈希 11-19 起为 176DE98E（testerp 档案正常续换令牌，C216460D 作废）。
