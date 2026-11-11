# 收款第 3 步：ERP 的 LINE 收款入口（11-11）

> 评估与审定：`docs/work/2026-11-11-collection-receipt-assessment.md`（在评估分支 `pearnly/collection-receipt-544a06`；§0、§6 入口表、§12-1 放行统一、审定结论、第 2 步结果）。
> 分支 `pearnly/collection-line`，基于 `pearnly/collection-server`（`5bc7f45c8`，第 2 步服务端），第 2、3 步一起合入。未合 main、未部署、未发富菜单（`line_menu.publish()` 没跑）、没用真手机、没碰测试机。

## 做了什么

| 入口 | 用途 | 实现 | 配置 / 角色 | 点击去向 | 验证 |
|---|---|---|---|---|---|
| LINE 富菜单左下「收款」（第 4 格，原锁格） | 收款员录一笔收款 | `integrations/line_menu.py`（`FUNCTIONS` 加 `collection`，15 种组合）、格图 `public/images/line-menu/tiles/collection-{th,zh,en,ja}.png`（`scripts/render-line-menu-tiles.py` 生成） | `collection.write`（收款员岗位）+ LINE；无岗位是锁图 | postback `a=col&s=start` | 单测 + 流程 `erp.line.collection` |
| 「เมนู」里的「收款」按钮 | 同上 | `line_flow.menu` | 同上 | 同上 | `test_line_intake` |
| LINE 收款各步 `a=col&s=…` | 选公司 → 打客户 → 勾发票（Flex 多选、合计、余额已扣预留）→ 方式（现金 + 能收钱的转账）→ 金额 → 转账凭证照片 → 【再加一种方式】【折扣】【下次再收】（哪张留余额 / 先收早的 / 逐张输入）→ 确认卡【确认】【编辑】【撤回】 | `integrations/line_collection.py`、`line_collection_cards.py`；规则只调 `collection.service` | 会话 `erp_collection`（`line_session`，30 分钟） | 确认 → `service.release(名, 卡片版本, "line:"+事件号)`；打「ยืนยัน / 确认 / 確認 / ok」同一条 | 单测 29 条 + 流程 |
| 确认卡「编辑」 | 改这张草稿（各张本次收、现金、各方式与折扣、销售员、部门） | `line_login.entry_url("/desk/pearnly-my-collections/<记录>")` → 新页「我的收款」原生 Dialog（`public/desk/my-collections.js`、`collection/api.py` 三个接口） | 只给收款员岗位；在左下角菜单，不进侧栏 | 存好回 LINE 点旧卡【确认】→ 版本变了重出新卡再确认 | 流程 `erp.line.collection`、`erp.collections.refused` |
| 结果 | 写成回 RE 号；没写成回「已记录 · 待写入」+【撤回】，之后推送结果给收款员本人 | 放行后后台等最多 20 秒（`await_result`，三个点）；回执 `collection.push.job_changed` → 提交后排 `line_collection.notify`；同一结果只说一次（不淘汰 Redis 的 NX 键） | 推送用这个人手机的 LINE 语言 | — | 单测（回复 vs 推送、已被收带余额、普通失败、CC 录的不推 LINE） |

第 2 步服务端按主会话 11-11 补的两处（同分支）：
- RE 抬头销售员 / 部门不留空交 CC 回退：默认第一张发票的，没有部门用 RE 单据簿默认部门（`receipt_run.department`）；收款员在编辑里改了就用改的，之后改金额、补凭证沿用；给的值要在这本账套里（停用销售员、不存在的部门拒）。`contracts.staff`、`service._staff / staff_choices`，`create_draft / update_draft` 加 `salesman / department`。
- 回执 `[CASH_BASIS_VAT]`（服务品现金制）与已被收 / 不存在同属「不能收」：照原因记失败、按客户再读一次未收清发票（`contracts.UNCOLLECTABLE`）；文案沿用失败原因的说法，不加说明句。

## 新文案（四语，check-copy 拦截 0）

见交付报告里的 `check-copy --report` 清单；LINE 按钮全部 ≤ 20 字（`check-line-labels` 通过）。

## 真机记录：`erp.scan.line.stocktake.liff-camera`（LINE 富菜单「盘点」→ LIFF 相机）

- 为什么命中：改到 `integrations/line_menu.py`（富菜单格子清单加第 4 格「收款」，组合从 7 种变 15 种）；盘点那格的动作与 LIFF 链接逐字没改，扫码、相机、LIFF 代码没动。
- 已证明（2026-11-11，基于 5bc7f45c8 的工作树）：`test_line_intake.test_tiles_follow_permissions_and_stocktake_opens_the_native_page` 照旧核盘点格链接；实验栈流程 `erp.line.collection` 里收款员那套是「收款」一格、销售没有。
- 没测到：LINE 原生相机授权与真机读码（任务书不用真手机；这次没动这一段）。
- 上线后补（testerp，三星 LINE）：照 [11-06 记录](2026-11-06-device-erp-stocktake-liff-camera.md)「上线后补全计划」走一遍授权与读码；并按下面清单测收款。
- 部署注意：富菜单组合变了，旧发布里没有新组合的键时 `line_menu._published()` 当没发布、不动官方号菜单；部署后要重跑一次 `publish()` 收款格才出现（写 LINE 官方号，按部署规矩做）。

## 部署后三星真机清单（收款）

1. 收款员账号绑定 ERP-TEST 官方号 → 富菜单左下是「收款」（四种手机语言格图各看一次）；没有收款员岗位的账号是锁。
2. 点「收款」→ 一家公司直接问客户；打客户名一部分 → 编号列表；打完整编号直接选上；打很宽的字 → 「请输入更多关键字」。
3. 发票 Flex：点选择 / 已选来回切、合计对；多于 10 张是可左右滑的几张；【下一步】没选时提示。
4. 现金全额 → 确认卡；BBL 转账 → 发照片（三个点后接着问）→ 再加现金 → 折扣 → 卡片三种方式、合计对。
5. 下次再收：哪张留余额 / 先收早的 / 逐张输入（输错合计重来）；超过未收的金额要重输。
6. 卡片【编辑】→ LINE 内置浏览器（LIFF）打开「我的收款」弹窗，免密登录；改金额、销售员、部门保存 → 回 LINE 点旧卡【确认】→ 出新卡 → 再确认。
7. 打「ok」「确认」「ยืนยัน」各确认一笔；电脑开着 20 秒内回 RE 号；电脑关着回「已记录 · 待写入」+【撤回】，开电脑后收到推送（只推给录入的收款员）。
8. 撤回：卡片上、待写入时各一次；电脑正在写时回「CC 桌面版正在写入」。
9. 两个收款员同时收同一张：后到的余额已扣；Express 里先手工收掉再放行 → 推送「已被收款，当前余额」。
