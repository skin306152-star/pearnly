# 上线车 11-15（集成分支 pearnly/integration-1115，周一 11-16 上线）

拼车窗口维护。部署不由本窗口发起，等主会话通知。线上现为 `6f58f0b48`（镜像 `sha256:054aa603…`，记录 [2026-11-13-release-6f58f0b4.md](2026-11-13-release-6f58f0b4.md)）。
目标（用户 11-15）：线上「下载 CC → 从连接添加新建公司（接 Express）→ LINE 收款写进 Express」整条路不带瑕疵，按普通新用户视角验收。这一车以此为准，其它让路；main 其余合入队列冻结到本车合入。

## 这一车装什么

| # | 内容 | 来源（分支 → 头） | 说明 |
|---|---|---|---|
| 0 | main 已合未上线（比线上多 74 个提交）：公司接入第 1 批（主档整读、erp-masters-worker）、MR.ERP 金额修复、收款闭环（单页收款、一个权限、拒收退回、后台回复语言）、性能体检（索引 v1_29、逐行查询闸门）、作废补漏、启动词条瘦身、验收体系（含真机记录六栏闸门）、CC 安装包走 R2 | origin/main 28e9d53d8 | R2 下载 11-14 已在线上（只动发布流程与 landing.conf） |
| 1 | 公司接入第 2 批：CC 报上来的账套改为提示而不自动建；公司页「从连接添加」；接 Express 只留 `cc_service.link_express` 一个入口；主档可见范围统一走 masters.access | company-onboarding-b2d996 → 2b4ff7223 | 周一核心路 |
| 2 | CC 半截单（服务端部分）：partial_saved 原因、回执 partial / held 净化、未完成单显示半截单号、迟到终态 | cc-desktop-half-doc-1ee3e3 → 18a1361ac | 服务端先上，CC 桌面版后发；含接缝 1 真跨边测试 |
| 3 | 三产品富菜单发布：已有图的不再传图（只看响应状态） | rich-menu-image-check → c3a248f5d | 菜单重发不超时 |
| 4 | 收款旧卡编辑地址修复 | collection-legacy-route-2 → 待确认卡窗口推出最终头 | 昨晚真机唯一不通过项 |
| 5 | 上线车清单与真机记录（本文件） | 拼车窗口 | 文档 |

**不进这一车**：公司接入第 3 批 3764ff83b（无条件改 Express 主档落库：主档加 ledger 字段、SYNC_FIELDS 比对 ledger、补丁 v1_30 回填全部主档、仓库码截断按账本分支、公司页改动——周一路用不到 MR.ERP，主会话 11-15 同意不进）；引导式第 1 期、上传后才识别、MR.ERP 写账弹窗、允许卡、回复整段显示、新建盘点批量写、mrerp-operator*（下一车）。

## 放行检查（0813bc83c，不含收款修复）

| 项 | 结果 |
|---|---|
| 全闸门（gate.sh = development.yml） | 248 / 248 首轮全过 |
| 候选离线（线上镜像 054aa603） | 111 组 LANE EXIT 0 |
| 产品地图 / 共用件连带 | 0 错；真机记录 16 行合规（六栏闸门） |
| 逐行查询 | 935 = 基线 |
| 回归实验室全套 | 进行中：正式建站（不手工接公司）+ `--all --dataset new` |

收款修复进车后在最终头上重跑全闸门、候选离线与受影响流程。

## 需在最终头上重测的真机行

昨晚 16 行是在含第 3 批、引导式、上传、写账弹窗的头 a31c2ba2f 上测的。逐行对过去掉的分支改到的文件：

| 级别 | 行 | 原因 |
|---|---|---|
| 必须 | erp.line.postback.col、erp.line.postback.col@refused、erp.line.collection-edit、erp.line.collection-edit@old-url、erp.desk.pearnly-collections.action.New（补电脑宽度） | 收款修复进车，都在它的路径上；@old-url 是昨晚不通过项 |
| 必须（新增） | 从连接添加接 Express → 主档落库 → 新公司在收款 / 采购选择框可见（干净电脑、任意账套） | 周一核心路，昨晚没有一行真机测过；昨晚的头带第 3 批，正改这一段 |
| 建议 | erp.desk.boot-messages@cc-connect | 昨晚看到的「收款」芯片是引导式业务菜单，新头没有 |
| 建议 | erp.line.postback.erp_item（至少复测「编辑经 LIFF 打开采购页 + 提交」） | 采购页 transactions.js 昨晚是写账弹窗改过的版本 |
| 建议 | erp.desk.boot-messages@original | 同在采购页 |
| 可沿用 | erp / cowork liff-camera、dms.line.richmenu.liff、三产品菜单发布、boot-messages@line-login | 去掉的分支不碰这些路径 |
| 仍没测到 | express.report.bank-deposit、line.richmenu.publish.dms、CC 拒收退回草稿 | — |

## 上线前置

- 上面「必须」的真机行在最终头上通过。
- 生产只读 1：小写原因码（already_collected / invoice_not_found）影响范围。
- 生产只读 2：「有销售岗位、没勾收款」的人数（收款改成一个权限后这些人看不到收款页）。
- `deploy.sh --dry-run`：核 erp-masters-worker 被建出、只在 ERP 机起。

## 新增进程与配置

- `erp-masters-worker`（队列 `erp_masters`，512M，profile erp）：线上没有，本车首次上（公司接入第 1 批，已在 main）。
- 启动词条：启动数据只带语言与指纹，整表走新接口（游客可调，已登记原因与守卫）。
- R2 安装包：11-14 已上线，本车不再动。

## 迁移与补丁（相对线上）

| 补丁 | 来源 | 说明 |
|---|---|---|
| pearnly_erp_portal.patches.v1_28_admin_collection_menus | 收款闭环（main） | 已绑定 LINE 的人重挂富菜单 |
| pearnly_erp_portal.patches.v1_29_stock_count_indexes | 性能体检（main） | 结存 / 盘点索引 |

DocType 改动：ERP Collection（attempt 字段）；ERP Customer / Supplier / Product（搜索字段加编码、税号，公司接入第 2 批）。只加不删，回旧镜像不需要反向迁移。

## 回滚

部署前给线上打回滚分支（同 11-13 做法 `pearnly/rollback-<日期>` 指 6f58f0b48），出事 `deploy.sh` 以 6f58f0b48 的发布标记重部署；erp-masters-worker 回旧版后由 compose 去掉。

## 上线后必验

- 新用户视角走一遍：下载 CC（R2）→ 从连接添加接 Express → 主档落库 → LINE 收款写进 Express。
- 六家 ERP 租户：收款页（单页）、权限、LINE 收款卡；`erp-masters-worker` 在跑、队列不积压。
- 启动词条：三产品首页、列表页中文 / 泰文正常。
- 登录后 e2e。

## 本车之后

- 「Not permitted」统一（源码与译文各一份；泰 / 日有可见变化，先给用户看清单）。
- 公司接入第 3 批、引导式等按下一车排。

## 集成记录

见共享笔记 `拼车-进度-11-14.md`（11-15 节在顶部）。

## 真机

执行：上线前真机窗口（device-test-1114）。被测集成头 `a31c2ba2f`（工作树 integ1114-device，前端本地构建），底座线上 `054aa603…`，测试机1 testenv 实验栈（erp → cowork → dms 依次 lab-start / lab-stop）。
截图与日志：`~/pearnly-devtools/device-1114/shots/`。原始记录：共享笔记「真机-记录-11-14.md」（含环境与收尾回读）。
结论：16 行，11 通过（4 行注明有分支没测到）、1 不通过（`erp.line.collection-edit@old-url`，收款窗口在修，修后在新头只复测这一项；**这一项没过本车不上线**）、4 没测到。

```
真机记录 | erp.scan.line.stocktake.liff-camera | 2026-11-14 | a31c2ba2f | 三星 SM-A146P + 测试机1 | 通过 | ERP-TEST 富菜单「盘点」经 LIFF 打开盘点页 → 新建测试盘点 device-1114-test（WH-140490）→ 开始盘点 → 扫码 → LINE 原生授权「是否允许此网站访问您的相机？」→ 仅允许本次 → 真相机对测试机屏幕 Code128「ZZG1112A」读出，认出「น้ำแข็งหลอด」、实际仓库带出 → 完成不保存（e54–e69）
真机记录 | cowork.scan.line.stocktake.liff-camera | 2026-11-14 | a31c2ba2f | 三星 SM-A146P + 测试机1 | 通过 | Cowork-test 富菜单「库存盘点」经 LIFF → 盘点「123」→ 开始盘点 → 扫码 → LINE 原生授权 → 真相机读 EAN-13 8851932100014，认出「สบู่ก้อนลักส์ 70 กรัม」、账面 คลังสาขาบางนา / S-02-02 → 完成不保存（c03–c07）
真机记录 | dms.line.richmenu.liff | 2026-11-14 | a31c2ba2f | 三星 SM-A146P + 测试机1 | 通过 | DMS-TEST 富菜单「修改账号密码」经 LIFF 打开 testdms「บัญชี DMS ของฉัน」（用户名 sale02），未保存即关（d01–d02）
真机记录 | erp.line.postback.erp_item | 2026-11-14 | a31c2ba2f | 三星 SM-A146P + 测试机1 | 通过 | 采购录入 → 上传识别 → mr.erp（CO-00036，express/mrerp 均未开）→ 按票面 → 已付现金（不再问服务/库存和仓库）→ 虚构票 TS6911-0014（付费识别 1 次）→ 卡片字段全对，行标 1「入库」2「新建·入库」3 运费「只记账」，追问列新物料 2 → 逐项选择 → 新建并入库 → 更新卡（单仓不问仓）→ 编辑经 LIFF 打开采购页、附件原件在 → 提交 PE2569-000031 → 读回 purchase_in 两条（PRD-00046 ×1、新建 PRD-173814 ×4），运费不进库存。没测到：选已有物料、多仓问仓、有仓位清单问仓位、两张连发、同票重拍、销售「ขาย」（e70–e85）
真机记录 | erp.line.postback.col | 2026-11-14 | a31c2ba2f | 三星 SM-A146P + 测试机1 | 通过 | 收款录入 → 公司 Pearnly → 选账套按钮「2. TEST」（\69EXP\TEST，没选 TEST2）→ 客户 นาย ทดสอบ → IV0000027 → BBL-S/A ·0555 → 2,461.00 → 转账凭证照片 → 确认卡（账套 69EXP\TEST）→ 确认 → 测试机1 CC 写 RE0000018（YOUREF COL261114-1150）→ LINE「已写入 Express：RE0000018」；69EXP\TEST 只读复制读回：RE0000018 2,461.00、BKTRN 新增 bR TRRE0000018、IV0000027 余额 0（e03–e22）
真机记录 | erp.line.postback.col@refused | 2026-11-14 | a31c2ba2f | 三星 SM-A146P + 测试机1 | 通过：服务端写前拒；没测到：CC 拒收退回草稿那一支（真机造不出 Express 与服务端不一致） | 开新一笔会把旧草稿作废（COL261114-6BFE withdrawn，点旧卡确认回「此项已过期，请重新开始。」）；LINE 卡 COL261114-F8B3（IV0000010 现金）未确认时，网页先收同一张并写成 RE0000020，再点 LINE 确认 → 回「此发票已收清」、卡片去掉确认、草稿保留，69EXP\TEST 无多写（e25–e32、e49）
真机记录 | erp.line.collection-edit | 2026-11-14 | a31c2ba2f | 三星 SM-A146P + 测试机1 | 通过 | 新卡 COL261114-1150【编辑】经 LIFF 打开收款页那一笔原生弹窗（账套、发票、方式），关闭未保存，背后是只留一页的新收款列表（e18–e20）
真机记录 | erp.line.collection-edit@old-url | 2026-11-14 | a31c2ba2f | 三星 SM-A146P + 测试机1 | 不通过：11-13 旧卡【编辑】落到「首页」，没转到那一笔 | 旧卡 COL261113-ED1B【编辑】：服务端日志 line_login.enter?to=/desk/pearnly-my-collections/COL261113-ED1B → GET /desk/pearnly-my-collections/COL261113-ED1B 200，页面最终是「首页」；aksarapak 的 boot.pearnly_pages 有 pearnly-collections、无 pearnly-my-collections（e50–e52）；已派收款窗口修，修后在新头只复测这一项
真机记录 | erp.desk.pearnly-collections.action.New | 2026-11-14 | a31c2ba2f | 三星 SM-A146P + 测试机1 | 通过（手机宽度）；没测到：电脑宽度（Mac 无 testerp 登录） | 收款页「＋」原生弹窗：公司 Pearnly → 客户 → 发票表勾 IV0000010 并在「金额」格填 3,054.85 → 现金 3,054.85 → 提交 →「已记录 · 待写入」→ CC 写 RE0000020（YOUREF COL261114-E5B7），读回一致；只勾不填金额时提交报「请至少选择一张发票」（e34–e48）
真机记录 | line.richmenu.publish.erp | 2026-11-14 | a31c2ba2f | 测试机1 实验栈 + 三星 | 通过 | 绑定名单 1 人（用户本人 aksarapak，全是测试账号）；line_menu.publish 两次 69.7 / 68.1 秒、60 套，无超时报错；该用户菜单前后同为 richmenu-8e0bb661…，三星富菜单含第 4 格「收款录入」（e02）
真机记录 | line.richmenu.publish.cowork | 2026-11-14 | a31c2ba2f | 测试机1 实验栈 + 三星 | 通过 | 绑定名单 1 人（用户本人，同一 LINE），publish 两次 38.3 / 33.0 秒、28 套，无超时；挂上新套 317d827a 后已手动挂回原 richmenu-cad3b30e… 并回读（c02）
真机记录 | line.richmenu.publish.dms | 2026-11-14 | a31c2ba2f | 测试机1 实验栈 | 没测到：绑定名单有身份不明的一行，按规矩不发布 | 名单 2 行同属操作员 dmsop-bda3b7da：active 尾号 47fe（三星 Skin）；unbound 尾号 4706，建于 2026-10-03 02:47:36、02:47:36 绑定、02:47:38 解绑（与 10-03 剖析脚本临时身份事故同一时刻）。旁证：三产品「发布不下载图」同一写法（共用 pearnly_core.line.rich_menu_has_image），ERP、Cowork 两次真机已过
真机记录 | erp.desk.boot-messages@line-login | 2026-11-14 | a31c2ba2f | 三星 SM-A146P + 测试机1 | 通过（中文）；没测到：泰文 | 经 ERP-TEST 进网页（LIFF 内 LINE 登录）：收款、盘点、首页、采购页文字中文正常，无英文漏翻；词条按指纹整表取（boot_messages.messages?lang=zh&v=… 200）
真机记录 | erp.desk.boot-messages@cc-connect | 2026-11-14 | a31c2ba2f | 测试机1 | 通过（对话页）；没测到：连接页 | 测试机1 CC（连实验栈 testerp）开窗口：侧栏、问候、输入框、「收款」芯片中文正常（e86）；看完只关窗口进程，常驻进程不动
真机记录 | erp.desk.boot-messages@original | 2026-11-14 | a31c2ba2f | 三星 SM-A146P + 测试机1 | 通过 | 采购页 PE2569-000031 附件区原件（测试票图）显示、可缩放，页面文字正常（e82、e85）
真机记录 | express.report.bank-deposit | 2026-11-14 | a31c2ba2f | 测试机1 | 没测到：报表窗口超出屏幕需盲按、有误点打印风险，留 11-15 上午 | 数据层旁证：69EXP\TEST BKTRN 有 bR TRRE0000018（2026-11-14）
```
