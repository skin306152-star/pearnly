# 上线车 11-14（集成分支 pearnly/integration-1114）

拼车窗口维护。部署不由本窗口发起，等主会话通知。线上现为 `6f58f0b48`（镜像 `sha256:054aa603…`，记录 [2026-11-13-release-6f58f0b4.md](2026-11-13-release-6f58f0b4.md)）。

## 这一车装什么（按进车顺序；「待进」= 还没推或 CI 没绿）

| # | 线 | 来源 | 状态 | 闸门 | 候选离线 | 流程 | 真机 |
|---|---|---|---|---|---|---|---|
| 0 | main 已合未上线：公司接入第 1 批（主档整读、erp-masters-worker）、MR.ERP 金额修复、收款闭环（单页收款、一个权限、拒收退回、后台回复语言）、性能体检（11-14 预批合入） | origin/main 12e71d3a9 | 已在 main | 见下 | 见下 | 见下 | 见「真机」节 |
| 1 | 公司接入第 2 批 | origin/pearnly/company-onboarding-b2d996 43d0ceda5 | 已进（CI 缺真机记录会红，主会话定进集成分支） | 逐行查询闸门：cc_service.create_many 待标有界 | | | 见「真机」节 |
| 2 | 引导式第 1 期 | origin/pearnly/cc-guided-phase1 97cc633bf | 已进 | | | 待跑 | 真模型三格窗口自测 1.43 美元 |
| 3 | 上传后才识别 | origin/pearnly/cc-upload-read-on-send-c21d68 88562fb05 | 已进 | 逐行查询闸门：files.start_sent 待标有界 | | | |
| 4 | 性能体检 | 已在 main 12e71d3a9 | 已在 main | 带来逐行查询闸门 | | | 上线后 testerp 复量 |
| 5 | MR.ERP 写账弹窗 + 现金采购 | pearnly/mrerp-ledger-dialog | 待进（未推）；mrerp-cash-purchase 不单独进 | | | | |
| 5' | ERP 作废缺口 | origin/pearnly/erp-void-gaps-1114 dde614495 | 已进 | | | | |
| 6 | 访问速度 C 启动词条 | origin/pearnly/boot-messages-cache 5c2fbb833 | 已进 | | | | |
| 7 | CC 半截单（服务端部分；CC 后发） | origin/pearnly/cc-desktop-half-doc-1ee3e3 18a14ddf4 | 已进 | 逐行查询闸门：两产品 status 逐行查半截单号，待改一次取回 | | | 测试机真跑待做 |
| 8 | 收款菜单发布 | origin/pearnly/rich-menu-image-check c3a248f5d | 已进（CI 缺真机记录会红，属预期） | | | | 见「真机」节 |
| 9 | 公司接入第 3 批（含 MR.ERP 开关） | pearnly/company-onboarding-3-307a0a | 待进（wip） | | | | testerp 真写 TEST2019 |

集成分支上本窗口自己的提交：接缝 1 真跨边测试 `companion/tests/test_seam_half_doc_server.py`（交半截单窗口收进其分支）。

## 本车之后（不进这一车）

- 「Not permitted」统一：源码统一 `_("Not permitted")`，译文只在 pearnly_core 留一份；泰 / 日有可见变化，先给用户看前后清单。单独小分支、一个窗口做。
- 产品地图真机记录规则收紧（验收体系窗口在改）。

## 上线前置

- 真机 1：LIFF 相机 3 行（共用 link.js，公司接入第 2 批 + 收款菜单）。
- 真机 2：收款写进 Express（NLTTEST）。
- MR.ERP 开关打开前：testerp 真写 TEST2019（写账验收清单 B 段，主会话排 testenv）。
- 生产只读 1：小写原因码（already_collected / invoice_not_found）影响范围。
- 生产只读 2：「有销售岗位、没勾收款」的人数（收款改成一个权限后这些人看不到收款页）。
- 上线后：testerp 性能复量（盘点明细 6–7 秒那页）。

## 新增进程与配置

- `erp-masters-worker`（队列 `erp_masters`，512M，profile erp）：线上没有，本车首次上。compose / provision-agent / activate-product / 实验室 / testenv / 容量检查已在 main；`deploy.sh --dry-run` 时核它被建出来、只有 ERP 租户机起。
- R2 密钥：安装包走 R2（访问速度 B）尚未进车；进车时在此登记。
- 启动词条：启动数据只带语言与指纹，整表走新接口（游客可调，已登记原因与守卫）；部署后浏览器首次加载多一次请求，之后缓存。

## 迁移与补丁（相对线上）

| 补丁 | 来源 | 说明 |
|---|---|---|
| pearnly_erp_portal.patches.v1_28_admin_collection_menus | 收款闭环（main） | 已绑定 LINE 的人重挂富菜单 |
| pearnly_erp_portal.patches.v1_28_stock_count_indexes → v1_29 | 性能体检 | 结存 / 盘点索引；性能窗口本地已改名 v1_29，推后替换 |
| v1_29_master_ledger_source、v1_30_mrerp_company_identity | 公司接入第 3 批（待进） | 与性能 v1_29 撞号，需改号 |

DocType 改动：Pearnly Assistant Session（引导式 business / business_at）、ERP Collection（attempt）。

## 回滚

部署前给线上打回滚分支（同 11-13 做法 `pearnly/rollback-<日期>` 指 6f58f0b48），出事 `deploy.sh` 以 6f58f0b48 的发布标记重部署。
补丁只加索引与重挂菜单、DocType 只加字段，回旧镜像不需要反向迁移；erp-masters-worker 回旧版后由 compose 去掉。

## 上线后必验

- 六家 ERP 租户：collection 页面（单页）、权限、LINE 收款卡；`erp-masters-worker` 在跑、队列不积压。
- 启动词条：三产品首页、列表页中文 / 泰文显示正常（erp/cowork.desk.boot-messages）。
- 盘点明细页时间（testerp）。
- 登录后 e2e。

## 集成记录

见共享笔记 `拼车-进度-11-14.md`（盘点表、冲突解法、接缝核对、口径差异清单）。

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
