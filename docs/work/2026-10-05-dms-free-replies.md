# DMS 删除审批、页面与慢任务改走免费回覆（2026-10-05）

用户：「审批通知是代码存在的吗，我早就说过不做，给我删掉，其它的做掉吧；不要随意加图标，就加文案就好了。」
分支 `codex/dms-free-replies`，基线 `origin/main` 9d5ac2aa。

## 背景

LINE 免费回覆只能回给刚发来消息的人、收到后 1 分钟内用一次（官方文档）；其余都是推播，占每个官方号每月 300 条额度
（2026-10-05 查 LINE 接口：ris 本月 1 条、testdms 10 条）。DMS 里会推播的三类：页面直接调接口（扫身份证、编辑页保存）、
任务超过 1 分钟、发给另一个人（只有审批）。

## 删除审批

- 审批策略本来就关着（`APPROVAL_POLICY = {"customer_profile": False, ...}`），提审分支从没走到；删除后差异卡行为不变：
  本人 DMS 账号有写权或花名册是管理员 → 「更新」，否则只给「用旧资料」（`intake/review.diff_card`，原 `exact_diff_card` 的非审批分支）。
- 删：`approval/`（flow / cards / store）、兼容入口 `dms_approval_flow.py`、DocType `DMS Change Request`（迁移补丁
  `pearnly_dms.patches.v0_0_1_drop_dms_change_request` 删表定义与表；生产 ris / testdms 表为空）、`cards` 的审批动作与提审按钮、
  `roster.list_profiles` / `line_user_id_of`（只审批用）、4 个审批验证脚本、相关测试与断言、DMS 25 条审批文案与 core 16 条只给那张表用的字段名
  （英文原文在别处还用的保留，如 Customer Name、Requested By）。
- 架构登记：`dms-module-map.json` 删审批映射与兼容入口；`architecture-policy.json` 删 `approval` 域，`intake` 加依赖 `operators`
  （`diff_card` 要读花名册角色；operators 不依赖 intake，无环）。
- 旧会话里残留的 `approval` 键与 `approval_pending` 态无害：建卡时照旧剔除，点菜单即清。

## 页面结果走免费回覆（`line/page_reply.py`）

- 页面做完后替业务员在聊天里发一句**纯文字**「<文案> #<6 位短码>」（不加图标；按四语原文认，不靠符号），webhook 用它的令牌回覆。
- 扫身份证：去掉原来的 📷；`scan.handle_code` 按短码开始识别。
- 编辑客户档 / 订车单：`save(..., deliver=)` 可注入送卡方式；LINE 编辑页（`browser_save`）把新卡先存 15 分钟（不淘汰 Redis，
  同一张只送一次），交接页 `editor=page_sent` 发那句话；发不了 → `browser_page_deliver` 推播（发送失败回 503 可重试，卡不在了回 409）。
  其它调用方不传 `deliver`，照旧推播。
- 交接页 `scan_sent` 合并为 `page_sent`（会话键 `pearnly_dms_page_*`，带页面类型选回落接口）。

## 慢任务（`line/out._SlowReply`、`show_result`）

- webhook 收事件时记下回覆令牌收到时间（2 分钟）。`run_guarded` 里的后台任务跑到 40 秒还没完、令牌还剩时间：计时线程用
  `line.reply_sender`（共用层新增，令牌与请求头在任务线程取好）先回「还在处理，做完后请点「查看结果」」带「查看结果」按钮；
  任务结束时这批发给本人的消息不推播，存 24 小时；点按钮（新令牌）免费回覆，还在跑回「还没好」，一次回 5 条、多的留着再点。
- 令牌已被 webhook 用掉或已过 55 秒的不计时，照原样（回覆失败转推播）。

## 验证

- `scripts/test-dms-line-runtime.py` 63 条（新增 `DmsPageReply` 4、`DmsSlowReply` 2；扫描标记改为纯文字）；`test-dms-intake-queue` 23、
  `test_dms_query_push` 11：生产镜像 6e29deef 挂载本分支、断网，通过。
- 第一次分支构建在候选离线道失败：`test-dms-line-language` 的假编辑器 `save` 不收 `deliver`、`test-dms-snapshot-consumers` 的状态存储替身让
  `_SlowReply` 把假对象当时间算。前者改测试（并断言那句话按收件人语言），后者改代码（读回的不是时间就不计时）。之后生产镜像挂载本分支
  跑完整候选离线道 54 步全过。
- `check-architecture` PASS；`check-dms-offline`（Python 3.14）20 项 PASS；本地 `development.yml` 同名 38 步通过；`vue-tsc` / `vite build` 通过。
- `scripts/test-dms-customer-write-flow.py`：main 上本来就 2 失败 4 报错（不在任何 CI 道里），本分支删掉 4 个审批测试后余下同样 6 条，与本任务无关，未修。
- 旧系统对照：删审批 3 条登记，只重新登记本分支改过的 7 个模块（out、booking_edit、cards、customer_edit、intake/flow ×2、text_router）。

## 测试机发现：扫描的身份证没附进订车单（19cdfd38 引入，已修）

- 用户在测试机扫身份证 → 建档 → 订车：DMS 里订车单建成（BK000002610000003），附件「สำเนาบัตรประชาชน」失败
  `line content download failed`。原因：扫描图的消息编号是 `scan:<id>`，订车附件仍去 LINE 下载；且识别后即删了原件。
- 修：`booking/attachments` 对 `scan:` 读站点里的原件（`intake.scan.content`）；原件与归属改留 7 天（订车可能晚做、失败要补传，
  与聊天里发的照片一样能取），`read` 不再删。
- 生产（19cdfd38 上线后）核对：ris / testdms 没有因此附件失败的推送记录，也没有留存的扫描原件，暂无受影响订单。
- 测试机那张 BK000002610000003 缺身份证附件，原件已删，无法补；是测试数据。

## 免费回覆实测（测试机，iPhone）

扫描上传 2 次、编辑页保存 3 次，全部走「页面发那句话 → 免费回覆」，回落接口 0 次；testdms 本月推播额度测试前后都是 10 条。

## 未验 / 下一步

- 分支镜像（构建流水线含 DMS 流程实验室，补丁在真站点迁移）→ 测试机真机：扫描回聊天出现纯文字那句并免费回覆、编辑页保存后新卡、
  慢任务「查看结果」。
