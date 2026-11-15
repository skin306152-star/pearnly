# 上线车 11-15（集成分支 pearnly/integration-1115，周一 11-16 上线）

拼车窗口维护。部署不由本窗口发起：主会话外部核对后合入 main、打标记，部署前先告诉用户。
线上现为 `6f58f0b48`（镜像 `sha256:054aa603…`，记录 [2026-11-13-release-6f58f0b4.md](2026-11-13-release-6f58f0b4.md)）。
目标（用户 11-15）：线上「下载 CC → 从连接添加新建公司（接 Express）→ LINE 收款写进 Express」整条路不带瑕疵，按普通新用户视角验收。

## 这一车装什么（最终候选头 `bcda444ae`，比线上多 122 个提交）

| # | 内容 | 来源分支 → 头 |
|---|---|---|
| 0 | main 已合未上线：公司接入第 1 批（主档整读、erp-masters-worker）、MR.ERP 金额修复、收款闭环（单页收款、一个权限、拒收退回、后台回复语言）、性能体检（索引 v1_29、逐行查询闸门）、作废补漏、启动词条瘦身、验收体系（真机记录六栏闸门）、CC 安装包走 R2（11-14 已在线上） | origin/main 28e9d53d8 |
| 1 | 公司接入第 2 批：CC 报上来的账套改为提示、不自动建；「从连接添加」；接 Express 只留 `cc_service.link_express`；主档可见范围统一走 masters.access | company-onboarding-b2d996 → 2b4ff7223 |
| 2 | CC 半截单服务端：partial_saved、回执 partial / held 净化、未完成单显示半截单号、迟到终态（CC 桌面版后发） | cc-desktop-half-doc-1ee3e3 → 18a1361ac |
| 3 | 三产品富菜单发布：已有图的不再传图 | rich-menu-image-check → c3a248f5d |
| 4 | 收款旧卡编辑地址修复、新建收款弹窗勾选与金额保留 | collection-legacy-route-2 → 6b7810944 |
| 5 | 「从连接添加」弹窗重排（已添加禁用 / 已停用可重启 / 空状态）、刷新经 `cc_desktop.rescan` 让在线电脑立即重报、左上公司下拉入口、首次添加接管空默认公司 | company-connect-empty-state → dc86d2d26 |
| 6 | CC 工具编码按主档回查换回原样（\xa0）、写入被拒定式说明、CC 查发票 / 建草稿先按客户即时读回（ready / reading / offline / outdated） | cc-code-fidelity → 199315fdb |
| 7 | 切换当前公司缓存竞态修复（共用层 `account/user_defaults.py`，DefaultValue after_insert 提交后清缓存，覆盖 8 处写用户默认值）、删「设为当前」、刷新 20 秒无人领单判未连接、重新启用提示「已更新」 | company-connect-empty-state → 7b25f84d6 |
| 8 | 真机 14 条里本车修的：LINE 选公司 / 客户 / 供应商搜索统一、选定公司后预热读未收发票、未付款单不显示「现金」、收款页新建加载态与公司下拉不自动展开、收款状态实时刷新、首页 6 条漏翻、「未收」文案 | device-1115-fixes → fb08ab5d7 |

**不进这一车**：公司接入第 3 批（无条件改 Express 主档落库，周一路用不到 MR.ERP，主会话 11-15 同意不进）、引导式第 1 期、上传后才识别、MR.ERP 写账弹窗、允许卡、回复整段显示、新建盘点批量写、mrerp-operator*。

## 放行检查表（bcda444ae）

| 项 | 结果 | 证据 |
|---|---|---|
| 全闸门（gate.sh = development.yml） | 251 / 251 首轮全过 | ~/pearnly-devtools/integ1115-gate-6.log |
| 候选离线（线上镜像 054aa603） | 111 组 LANE EXIT 0 | ~/pearnly-devtools/integ1115-cand-6.log |
| Development / Companion checks | 532de63b1 两项 success；bcda444ae 推送后待查 | GitHub |
| 产品地图 / 共用件连带 / i18n / 逐行查询 | 0 错 / 通过 / 通过 / 935 = 基线 | 本地 |
| 收款三组测试（_collection / _line / _cc） | 全 OK；翻译包合并头上重生成零差异 | 本地 |
| 全套流程 new 数据集（0813bc83c，正式建站） | 210 / 210 | ~/pearnly-devtools/full-1115.log |
| 本车后半段改动涉及流程（bcda444ae，两套数据） | 39 + 52 过；不过的见下 | ~/pearnly-devtools/integ1115/phase2.log |
| 空默认公司接管（新建空站点实走） | 空默认 9/9、默认有记录 4/4 | 拼车笔记 |
| 8 处写用户默认值并发回填核对 | 8/8 | 拼车笔记 |
| 手机宽度「从连接添加」弹窗空白 | 已收掉（最终头重拍） | ~/pearnly-devtools/integ1115/shots/ |
| 全套 `--all --post-status`（部署流程闸要的那一份） | 进行中（实验室 down -v 重新正式建站） | GitHub pearnly/flows |

已知、不挡上线（下一车）：
- `erp.link.ledger-first`：选择框排序「本公司的档排前」不分账本物料与本公司手工物料，手工物料多时账本物料被挤出第一页；新建站数据少能过。修法：本公司之后加「有账本编码」排前（公司接入窗口）。
- 收款预热：较旧的整本预热结果晚于按客户读回落库时，可能把刚收清的发票插回、显示到下次读回；兜底 CC 写前 already_collected 拒收（收款窗口）。

## 需在最终头上重测的真机行

昨晚 16 行在含第 3 批、引导式、上传、写账弹窗的头 a31c2ba2f 上测的；最终头 bcda444ae 由接手的真机窗口测：

| 级别 | 行 | 原因 |
|---|---|---|
| 必须 | erp.line.postback.col、erp.line.postback.col@refused、erp.line.collection-edit、erp.line.collection-edit@old-url、erp.desk.pearnly-collections.action.New（补电脑宽度） | 收款修复与 device-1115-fixes 都在路径上；@old-url 是昨晚不通过项 |
| 必须（新增） | 从连接添加接 Express → 主档落库 → 新公司在收款 / 采购选择框可见（干净电脑、任意账套）；含首次添加接管空默认公司、刷新让在线电脑立即重报 | 周一核心路；在线电脑重报只有真机能验 |
| 必须（新增） | CC 两条：编码带不换行空格的单写进 Express；查发票先读回再说「已收清」 | cc-code-fidelity 修的挡上线项 |
| 建议 | erp.desk.boot-messages@cc-connect、erp.line.postback.erp_item（至少编辑经 LIFF 打开采购页 + 提交）、erp.desk.boot-messages@original | 昨晚看到的内容来自去掉的分支 |
| 可沿用 | erp / cowork liff-camera、dms.line.richmenu.liff、三产品菜单发布、boot-messages@line-login | 去掉的分支不碰这些路径 |
| 仍没测到 | express.report.bank-deposit、line.richmenu.publish.dms、CC 拒收退回草稿 | — |

## 上线前置

- 上面「必须」的真机行在 bcda444ae 上通过（只有「通过」放行）。
- 全套 `--all --post-status` 在要部署的提交上通过（或其后只改 docs 沿用）。
- 生产只读（连生产前先把脚本给主会话）：① 小写原因码（already_collected / invoice_not_found）影响范围；② 「有销售岗位、没勾收款」的人数（收款改成一个权限后这些人看不到收款页）。

## 部署预演步骤（主会话执行）

1. main 快进到本车提交（bcda444ae 或其后只加 docs 的提交），推送、回读远端 SHA。
2. 打回滚分支 `pearnly/rollback-1116` 指 6f58f0b48（线上现版本）。
3. `scripts/production/deploy.sh --dry-run`：流程闸（pearnly/flows 全套通过或可沿用）、租户配置对比、LIFF 落地地址、站外设置对比全过；核 **erp-masters-worker**（队列 erp_masters、512M、profile erp）只在 ERP 机上会被建出。
4. 正式 `scripts/production/deploy.sh`（后台，约 40 分钟，含回读与登录后 e2e）。
5. 迁移前后核（一次 SSH，web 容器里 bench，只读）：
   - 补丁：`pearnly_erp_portal.patches.v1_28_admin_collection_menus`（已绑定 LINE 的人重挂富菜单）、`pearnly_erp_portal.patches.v1_29_stock_count_indexes`（结存 / 盘点索引）都在 Patch Log。
   - DocType：ERP Collection（新字段 attempt；source 选项加 web）；ERP Customer / Supplier / Product（search_fields 加编码、税号）。
   - 共用层新钩子：DefaultValue after_insert → `pearnly_core.account.user_defaults.after_write`（hooks 生效即可，无表结构）。
   - 菜单：ERP「收款」页权限改为收款岗位 + 管理员；「我的收款」不进菜单、旧地址转收款页；公司页按钮去掉「设为当前」、加「从连接添加」。
6. 上线后必验：新用户视角走一遍（下载 CC（R2）→ 从连接添加接 Express → 主档落库 → LINE 收款写进 Express）；六家 ERP 租户收款页 / 权限 / LINE 收款卡；erp-masters-worker 在跑、队列不积压；三产品首页 / 列表中泰文正常；登录后 e2e。

## 回滚

`deploy.sh --tag <6f58f0b48 的发布标记>` 重部署；补丁只加索引与重挂菜单、DocType 只加字段与选项，回旧镜像不需要反向迁移；erp-masters-worker 回旧版后由 compose 去掉；DefaultValue 钩子随代码回退。

## 本车之后

- 「Not permitted」统一（源码与译文各一份；泰 / 日有可见变化，先给用户看清单）。
- 选择框账本物料排前、收款预热边角（见上）。
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
