# 直写进货单按账套历史定口径：服务品进项税口径（2026-10-29）

主会话 10-29 派：代录窗口离线对照 158 本真实账套语料，发现直写进货单的税务口径与 Express 原生不一致，在共用写账规则里修掉，随下一批上线（不部署）。
分支 `pearnly/express-rules-vat-8fa604`，起初基线 origin/main `8a8c9b0f`；10-30 代录窗口的 `pearnly/purchase-exempt-vat` 合入 main
（`00736f59`，含载荷税率 e8c0443e、整单免税新供应商仍无税 6642b39f）后变基到 origin/main `00736f59`，只有流程登记表一处冲突（两边条目都留）。

主会话 10-29 晚更正分工：缺陷一（进货整单免税、混合免税）归代录窗口（`pearnly/purchase-exempt-vat`），本窗口不改；本窗口做
「按账套历史定口径」的同一类三项：①服务品进项税口径 SRV_VATTYP（本记录已做完）；②Cowork 识别来的税额 0 进货单按供应商历史定免税 /
无增值税（明天）；③赊购单价格类型 1 / 2 跟单据还是跟供应商（明天先统计、报结论再改）。

## 分工与依据

- 缺陷一（进货整单免税）：代录窗口在 `pearnly/purchase-exempt-vat` 做（`6ad15216`、`181993ab`、`bdaa80bf`，CC 1.12.7），本分支以它为准、
  叠在上面，不改同一处。核对中发现的缺口已转给代录窗口：整单免税的载荷税率按「税额 > 0」给 0（`push/mapper.py`），原生免税单税率记 7。
- 缺陷二（服务品进项税口径）：本分支做。评估在代录分支 `docs/work/2026-10-29-service-vat-basis-评估.md`（`150a94f3`），主会话已批「按供应商判定」。
- 开工前在代码里核对代录窗口的结论：
  - 直写进货 `dbf_writer.write_purchase` 原来 `has_vat = vat > 0`，整单免税写成 FLGVAT 0；`dbf_fields.audit_fields` 的 SRV_VATTYP 恒写 `1`（无税写 `-`）。成立。
  - ERP 采购单原来只在混合税率时标免税行，全是 0% 的一行都不标；`express_rules.push.mapper` 的载荷税率按「税额 > 0」给 7，整单免税给 0。

## 语料统计（测试机 `06-录入推送\有答案` 158 本，10-29 只读，脚本只读原表）

- 服务品进项税口径（APTRN.SRV_VATTYP）：
  - 现购单 HP 全是 `1`（价格类型 0 的记 `-`）；只有库存品明细的赊购单 59,676 张全是 `1`；`2` 只出现在带服务品或没有明细的赊购单上。
  - 带服务品的赊购单按供应商：一贯 `1` 8,117 家、一贯 `2` 849 家、两种都有 465 家。
  - 按时间顺序回放本规则（价格类型 1 / 2、带服务品或没有明细的赊购单 98,186 张，历史只用这张单之前的人录单，问过的按真值记住）：
    历史定下 86,527 张、错 533 张；问 7,175 次；记住后复用 4,484 张、错 1,989 张（两种都有的供应商）；合计错 2,522 张。
    以前恒写 `1` 错 14,003 张。
- 免税进货单：税率 7 的 7,286 张、0 的 282 张。整单免税价格类型 1 / 2 约 3,700 张，另有约 1,600 张原生记价格类型 0（多在开了
  「按商品记增值税」的账套）；代录窗口全量统计整单免税 5,078 张，按 55% 的主流写法（1 / 2 + 单头 AMTRAT0、不写进项税记录）。

## 改了什么

### 缺陷二：服务品进项税口径按账套历史定

- 共用规则 `pearnly_core.express_rules.push.service_vat`（纯函数）：
  - 没有税 `-`；现购单、只有库存品的赊购单 `1`；
  - 其余赊购单：这家供应商在本账套的历史一贯一种照用；这家没有历史看整本账套；都定不了用人选过的（按「这本账套 × 这家供应商」记住）；
    没选过回「要问」。
  - 历史只算 Express 里人录的价格类型 1 / 2 的赊购单（作废的、Pearnly 写的不算）。
- CC 桌面版：
  - `companion/service_vat.py`：规则副本（契约测试逐例比对两边）+ 读 APTRN 历史（只读几栏原始字节，按行数增量读、缓存）。
  - `dbf_writer.write_purchase`：写之前按规则定 SRV_VATTYP；定不了、载荷也没带 `srv_vattyp` 就不写，回 `SERVICE_VAT_BASIS_CHOICE`（转人工）。
  - 供应商主档上报带每家的口径串 `srv`（心跳目录 `catalog_probe.probe_suppliers`、ERP 主档读取 `express_masters`；`"0"` = 没有历史）。
  - 载荷 13：带人选的 `srv_vattyp`，旧版不认、诚实拒收。
- 服务端：
  - `choices.resolve` 新一类 `service_vat`：记过的写进载荷 `srv_vattyp`；按主档报上来的 `srv` 判，历史定不了才问（现购、只有库存品、没有税的不问）。
  - ERP：在单据上问（提交前预检与电脑拦下后同一项，`prepare.service_vat_needed`），按公司记、归类键带账套键；单据 Express 弹窗的选项
    显示 Express 原词（เกณฑ์สิทธิ / เกณฑ์เงินสด），新增流程用例 `erp.express.service_vat`。
  - Cowork：暂不问（两处选择卡——Cowork 网页 `targets.js`、CC 前端 `pending.tsx`——还没有这一项，这两页也还没有流程用例）；记过的照用，
    定不了由 CC 拒写转人工，原因文案「服务品进项税口径未定」。Cowork 侧的账套记忆、公司规则、文件夹任务的种类表已认这一类。
  - 拒写码进「要人处理」（`ack_rules`），CC 归为 needs_review。
- 新文案（中 / 英 / 泰 / 日）：「服务品进项税」「权责制 / 收付实现制」（泰文用 Express 原词），以及拒写原因「服务品进项税口径未定」（Cowork 这批没有可选的地方，只陈述事实）。

### 顺带

- `tests/test_companion_contract.py` 的 `unittest.main()` 写在文件中间，后面的 `ErpExpressFront` 等测试类在 CI 里从没跑过；挪到末尾，82 个全过。

## 验证

- 单元 / 契约：CC 全套 1232 过；`tests/test_express_service_vat.py`、`test_companion_contract.py`（规则副本逐例一致、码 / 键 / 版本两边一致）、
  `test_erp_express_direct.py`、Cowork 受影响的测试全过。
- 本地全闸门（照 development.yml，`~/pearnly-devtools/gate.sh`）：150 步全过；推送后 CI（a4b26373）Development checks、Companion checks 都绿。
- 清空测试账套离线回归（测试机 `C:\PearnlyExpress\70EXP` 的 T 账套逐本复制，带 skin3 已装 CC 的索引程序核 CDX，原件只读）：
  - 服务品口径（`scripts/express-service-vat-regression.py`）：92 本通过、0 失败、9 本跳过（3 本没服务品或没会计期间表、3 本只有一家供应商、
    3 本已有赊购历史）。核：没历史不写且账套一字节不变、人选的照写 2、Pearnly 写的不算历史、补一张人录历史后照它写、现购 1、没税 -、
    写入集合外的表不变、CDX 与 DBF 一致。
  - 代录窗口的免税回归（`scripts/express-exempt-purchase-regression.py`）在本分支上：赊购单在没有历史的 T 账套被拒写（新规则的正常行为），
    95 本因此「失败」；给它的载荷补上人选的口径后重跑：98 本通过、0 失败、3 本跳过，与代录窗口在 bdaa80bf 上的结果相同。
    两个分支合并时这份脚本的 `payload()` 要带 `srv_vattyp`（本窗口变基时补）。
- 语料逐字段对照（`scripts/express-service-vat-corpus.py`，50 本答案账套、两种模式：「按商品记增值税」开 45 张、关 163 张）：
  - 服务品口径：口径 1 的 97 张错 3 张、口径 2 的 78 张错 3 张（都是历史两种都有、记住的值与这张不同），与回放一致。
  - **新发现（上线前必须修）**：原生口径 2 的赊购单开单时不写进项税记录（78 / 78），进项税借方挂账套设置的「未到期进项税」科目
    （ISINFO.ACCNUM28，语料 6,690 行），单头 VATPRD 留空；口径 1 挂 ACCNUM06、记 VATPRD、写 ISVAT（79,040 行）。直写写成口径 2
    以后仍写 ISVAT、借 ACCNUM06：Express 付款时会再记一次进项税，等于重复抵扣。**10-30 已修（f59676b3），见下节。**
  - 其余差异是已知的另一类：原生价格类型 1 的赊购单直写记 2（服务品口径 1 的 97 张里 25 张，金额栏跟着不同），即明天的第 ③ 项；
    整单免税有 11 张原生记价格类型 0（缺陷一取主流 1 / 2 的已知差异）；1 张整单免税单 AMT01 超出范围（造载荷用了答案的零税额 > 税前，
    是对照脚本造法问题，不是写法问题）。

## 10-30：口径 2 照原生（f59676b3）

- `dbf_writer.write_purchase`：SRV_VATTYP 为 2 时进项税那一行借「未到期进项税」（ISINFO.ACCNUM28）、单头 VATPRD 空、开单不写 ISVAT；
  写后核对（`_verify`、`write_verify`）按「这张单写不写进项税记录」核。这本账套没设 ACCNUM28 或它不能记账的，不写、回 ACCOUNT_NOT_POSTABLE
  （转人工），不静默挂普通进项税科目。口径 1 照旧（借 ACCNUM06、记税期、写 ISVAT）。
- 测试：CC 全套 1234 过（新增口径 2 三项、科目不能记账拒写且账套不多一行）；本地全闸门 150 步全过；CI（f59676b3）两套都绿。
- 测试机 T 账套回归（带 CC 索引程序核 CDX）：
  - 服务品口径：92 本通过、0 失败、9 本跳过；92 本都有可记账的 ACCNUM28，口径 2 的单逐本核了不写 ISVAT、税期空、借 ACCNUM28。
  - 代录窗口的免税回归（本分支已在它的 `payload()` 补 `srv_vattyp`）：98 本通过、0 失败、3 本跳过。
- 语料逐栏对照（50 本，加了「税期有无」「进项税借方挂哪个设置栏」两项）：口径 2 的赊购单 73 / 78 完全一致（10-29 是 0 / 78，
  全差在 ISVAT）；其余 5 张：3 张是规则判出的口径与答案不同（这家历史两种都有），2 张是价格类型 1 / 2 的老差异（第 ③ 项）。
  口径 1 的 97 张 70 张完全一致，差的 27 张里 25 张是价格类型 1 / 2、3 张口径判错。

## 生产影响（10-29 一次 SSH，只读事务、只 SELECT，经各产品 web 容器的 `bench --site … mariadb`）

| 站点 | 公司 | 直写进货单 | 服务品赊购单写成 1 | 涉及供应商 | 税额 0 写成 FLGVAT 0 | 其中带 13 位税号 |
|---|---|---|---|---|---|---|
| ccdemo | ดาวเหนือ ไอที ซัพพลาย | 18 | 5 | 2 | 4 | 3 |
| ccdemo | ภูผาแก้ว วัสดุก่อสร้าง | 9 | 4 | 3 | 0 | 0 |
| testcowork | นิภา เอ็กเช้นจ์ | 15 | 2 | 1 | 10 | 9 |
| testcowork | ดาวเหนือ ไอที ซัพพลาย | 19 | 0 | 0 | 5 | 4 |
| testcowork | ภูผาแก้ว วัสดุก่อสร้าง | 19 | 0 | 0 | 4 | 4 |
| testcowork | เอเชีย สปอร์ต | 1 | 0 | 0 | 0 | 0 |
| testcowork | แก้วกานดา บิวตี้ สโตร์ | 19 | 0 | 0 | 2 | 2 |
| testerp | กิสส์ มาร์เก็ตติ้ง / เฮง 2015 | 各 1 | 0 | 0 | 0 | 0 |

- 正式客户站点（mrerp、erpcowork 等）0 张直写进货单。ERP 勾了税务发票、税额 0 的已写采购单 0 张（testerp 2 张都不是）。
- 「这家供应商一贯是 2」：生产库里没有账套历史（在各办公室电脑的 Express 账套里），定不了；上表「写成 1」是可能受影响的上限。
  演示 / 测试租户的账套是清空过的 T 账套，没有赊购历史，按新规则这些单都会要人选一次。
- Cowork 识别来的「税额 0」分不出是免税还是供应商不收税（ADR-0012：印了 0 税的完整税票按费用档），照旧 FLGVAT 0。
- 已写的单不改。

## 没验证的 / 限制

- 流程用例 `erp.express.service_vat` 已写、已登记，**没在回归实验室跑**：本机已有 4 套实验栈在跑（含 ERP 验收窗口挂自己工作树的
  `pearnly-regression-lab`），超过「最多三套」，没再起。要在实验室空出来后 `scripts/flows/run.py --changed --base origin/main --post-status`。
- 没上测试机实机（没起实验栈、没装 CC 测试包）：CC 1.12.7 + 服务端的端到端（ERP 单据被拦下→选→重写）没走过。
- Cowork 的「问一次」没做（见上）；Cowork 推送遇到定不了的服务品赊购单会转人工，暂时没有选的地方。
- 原生整单免税约 28% 记价格类型 0（多在开了「按商品记增值税」的账套），直写统一按主流 1 / 2 写，与这部分账套的习惯不同。
- CC 要发新版 1.12.8（1.12.7 不带本分支）；服务端先部署，旧版 CC 收到载荷 13 诚实拒收。

## 交接（10-30，本窗口归档，新窗口接手复核合入）

- **分支与基线**：`pearnly/express-rules-vat-8fa604`，代码最新 `4f272073`（交接记录提交在它之后），已变基到 origin/main `00736f59`
  （代录窗口的免税修复已在 main）。已推送、CI 两套都绿（Development checks、Companion checks）；**没合入 main**（主会话冻结主线到这一批部署完）。
- **本分支内容**（第 ① 项，服务品进项税口径 SRV_VATTYP）：
  - 共用纯函数 `pearnly_core.express_rules.push.service_vat`；CC 副本 `companion/service_vat.py`，契约测试逐例比对两边。
  - CC 写赊购单：按账套历史（供应商 → 整本）定口径，定不了用载荷 `srv_vattyp`，都没有就不写、回 `SERVICE_VAT_BASIS_CHOICE`（转人工，
    原因「服务品进项税口径未定」）。
  - 口径 2 照原生：进项税借 ISINFO.ACCNUM28、VATPRD 空、开单不写 ISVAT；ACCNUM28 没设或不能记账 → 不写、`ACCOUNT_NOT_POSTABLE`。
  - CC 随供应商主档上报口径串 `srv`（心跳目录、ERP 主档读取）；ERP 在单据上问一次（提交前预检 + 电脑拦下后），按「账套 × 供应商」记；
    Cowork 本批不问（主会话选②），记过的照用。
  - **CC 1.12.8**（本分支升的版本号；1.12.7 不带本分支），**载荷 13**（带 `srv_vattyp`），旧版 CC 诚实拒收。
- **验证（都在本记录上文有细节）**：CC 全套 1235 过；本地全闸门 151 步过；测试机 T 账套服务品口径回归 92 过 / 0 不过 / 9 跳过
  （口径 2 三项逐本核）；代录的免税回归（已补 `srv_vattyp`）98 / 0 / 3；语料逐栏对照口径 2 赊购单 73 / 78 一致、口径 1 的 70 / 97 一致
  （差的是价格类型 1 / 2 与历史两种都有的供应商）；生产只读统计：正式客户 0 张直写进货单。
- **没验证**：
  - 流程用例 `erp.express.service_vat`（`scripts/flows/erp_express_service_vat.py`，已登记）**没跑**：本机实验栈一直超过三套。
    接手后在实验室空出来时跑 `scripts/flows/run.py --changed --base origin/main --post-status`（或按 README 用自己的栈名 `PEARNLY_FLOWS_LAB`）。
  - CC 1.12.8 + 服务端的端到端实机（ERP 单据被拦下 → 选 → 重写；口径 2 的单在 Express 里打开、付款后进项税只记一次）没走过。
- **发版顺序**：主会话先部署这一批（含代录的免税修复）并发 CC 1.12.7 → 本分支交验收复核 → 合入 main → 部署服务端（载荷 13 先能出）
  → 发 CC 1.12.8 正式版。服务端先于 CC：旧版 CC 收到载荷 13 诚实拒收，不会写错。
- **待做（不在本分支）**：
  - 第 ③ 项：赊购单原生价格类型 1（含税价）的直写一律记 2、金额栏口径不同（语料对照口径 1 的 97 张里 25 张因此不同，例 BakeLab
    RR670116-011、ASIA-RS RR661124-004）。先用语料统计它跟单据本身的价格口径走还是跟供应商历史走，报主会话结论再改。
  - 第 ② 项：Cowork 识别来的税额 0 进货单按供应商历史定免税 / 无增值税（同一套纯函数，CC 随主档报口径串），定不了问一次、记在
    「账套 × 供应商」。
  - Cowork 的「服务品口径选一次」：等验收窗口的 cc.pending、cc.companies 流程合入后，在上面加断言和 Cowork 带 Express 连接的数据
    （加进 `scripts/regression/datasets.py`），再接 Cowork 两处选择卡（targets.js、pending.tsx、company-cards.tsx、i18n.ts），
    `choices.resolve` 对 Cowork 打开 `ask_service_vat`。
- **复用的脚本**（测试机上跑，源账套只读）：`scripts/express-service-vat-regression.py`、`scripts/express-service-vat-corpus.py`、
  `scripts/express-exempt-purchase-regression.py`，都要 `--runner` 指 skin3 已装 CC 的 `cdx_reindex_runner.exe`。
- **清理**：测试机临时目录与进程已清、占用表已释放；本窗口工作树 `.claude/worktrees/express-rules-vat-8fa604` 留给接手的窗口（合入后删）。

## 现状（10-30）

- 第 ① 项（服务品口径，含口径 2 照原生）做完：推送到 `pearnly/express-rules-vat-8fa604`（最新提交见交付），CI 绿，**没合入 main**。
- 已变基到 origin/main `00736f59`（代录窗口的免税修复已在 main），过闸、CI 绿后交验收复核（结果见交付）。
- 之后接第 ② 项（Cowork 识别来的税额 0 进货单按供应商历史定免税 / 无增值税）与第 ③ 项（价格类型 1 / 2 先统计、报结论再改）；
  Cowork 的选择卡等验收窗口的 cc.pending、cc.companies 流程合入后接。

## 暂停点（10-29 晚，用户下班，主会话要求在安全的点停下）

- 做到哪：服务品口径（第 ① 项）的规则、CC、ERP 问一次、Cowork 不问（选②）已提交并推送 `pearnly/express-rules-vat-8fa604`（见交付 SHA），
  CI 绿；**没合入 main**。
- 明天第一步：修上面「口径 2 不写 ISVAT」：`dbf_writer` 写进货单 SRV_VATTYP 为 2 时，进项税借方改挂 ISINFO.ACCNUM28（不能记账就转人工、
  不静默挂 ACCNUM06）、VATPRD 留空、不写 ISVAT；写后核对（`write_verify`、`_verify`）同步；补测试、T 账套回归加这一条、语料对照再跑。
- 之后：第 ② 项 Cowork 识别来的税额 0 进货单按供应商历史定免税 / 无增值税（同一套纯函数，CC 随主档报口径串）；第 ③ 项赊购单价格类型
  1 / 2 先做语料统计（跟单据还是跟供应商），报主会话结论再改。
- 依赖：
  - 代录窗口 `pearnly/purchase-exempt-vat`（bdaa80bf，未推送）：本分支叠在它上面；它合入 main 后本分支变基到 main，共用层以它为准；
    它明天先修载荷税率缺口（`push/mapper.py`）。变基时给它的免税回归脚本补 `srv_vattyp`。
  - 验收窗口（local_733ef539…）在写 CC 前端流程 cc.pending、cc.companies；那两条合入后，本窗口在上面加「服务品口径选一次」的断言和
    Cowork 带 Express 连接的数据（加进 `scripts/regression/datasets.py`），再把 Cowork 两处选择卡（targets.js、pending.tsx、company-cards.tsx、
    i18n.ts）接上、resolve 对 Cowork 打开 `ask_service_vat`。
  - 流程用例 `erp.express.service_vat` 已登记，等本机实验栈少于三套时跑 `scripts/flows/run.py --changed --base origin/main --post-status`。
- CC：主会话 10-30 冻结主线、1.12.7 不带本分支；本分支 CC 版本号升到 1.12.8，合入后另发；服务端先部署，旧版 CC 收到载荷 13 诚实拒收。

## 收尾

- 10-30：测试机 `C:\PearnlyStress\_srv_reg_src`、`_srv_reg`、`_srv_cmp` 已删，没有本窗口的残留进程，占用表已登记释放。
- 10-29：分支 `pearnly/express-rules-vat-8fa604` 已推送，没合入 main（主会话要求）；工作树保留（明天继续）。
- 测试机：回归、统计进程都已跑完退出；临时目录 `C:\PearnlyStress\_srv_reg_src`、`_srv_stats`、`_srv_reg`、`_srv_cmp` 已删，占用表已登记释放。
- 本机：临时基线检出已删；scratchpad 里的虚拟环境与统计结果随会话清理。
