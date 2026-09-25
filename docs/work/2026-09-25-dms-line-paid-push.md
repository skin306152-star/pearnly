# DMS LINE：付费推播压到最少（2026-09-25）

## 授权与范围

用户 2026-09-25：「本地先找对应的花钱的地方，改成不花钱或者最少花钱，找完对应的改，全部好后再部署」；
补充「看下能压缩到什么程度，且不会影响业务逻辑」。

- 工作树 `/Users/skin/.codex/worktrees/dms-line-paid-push/pearnly`，分支 `codex/dms-line-paid-push`。
- 基线 `main` = `origin/main` = `693e21d0ebd9e9147a47d2bd24fb9f760168ca25`；线上当时跑 `4997d72`
  （镜像 `sha256:ed26caae…`），与基线只差文档提交。
- 只改**发送方式**：消息内容、顺序、业务判断、nonce/幂等/写者锁都不动。会改变用户所见内容的做法单列，
  没有擅自做。

## 计费规则（官方口径）

- Reply **免费**；一张回覆令牌只能用一次，官方保证 1 分钟内有效。
- Push 按"发给几个人"计数：**一次请求带 1～5 个气泡只算 1 条**
  （[定价说明](https://developers.line.biz/en/docs/messaging-api/pricing/)）。

所以最贵的写法是"同一次反应先回一句、再发一张卡"：第一条用掉令牌，第二条只能推播。

## 改法

1. `pearnly_core.line.batch(用户, 令牌)`（共用传输层，**只有调用方显式开启才生效**，ERP/Cowork 行为不变）：
   块内发给同一个人的消息攒到结束**一次**发出；有令牌就整批免费回覆，没令牌也只算 1 条推播；
   超过 5 条时前 5 条免费、其余再合成 1 次推播。发给别人的消息（审批通知等）照常立即发送。
   带 `retry_key` 的回执需要真实送达结果，当场连同已攒消息一起发出并返回真实结果。
2. DMS 在两个入口开块：webhook 每个事件（`line/handlers.handle`）、每个后台任务（`line/out.run_guarded`，
   带任务里的 `reply_token`）。嵌套同一用户并入外层。
3. 订车「确认建单 / 重试」的令牌带进建单任务（`booking_flow._execute_booking(…, reply_token)`）：
   这次点击本身什么都不回，建单结果、回执、失败提示因此能在 1 分钟内免费回覆。
4. 识别失败提示（`out.ocr_failed`）用发图那口气的令牌。

## 逐项结果（以前 → 现在）

| 你在 LINE 里看到的 | 以前 | 现在 |
| --- | --- | --- |
| 菜单 1 保存成功「บันทึกสำเร็จ…」+「บันทึกข้อมูลลูกค้าเรียบร้อยแล้ว…」 | 第二条推播 1 条 | 一次回覆，**免费** |
| 差异卡点「ใช้ข้อมูลเดิม」→ 回一句 + 收尾句 | 收尾句推播 1 条 | **免费** |
| 问候语进菜单：欢迎语 + 菜单卡 | 菜单卡推播 1 条 | **免费** |
| 建档后接着订车：回执 + 订车第一问 | 第一问推播 1 条 | **免费** |
| 客户档改写撞冲突：提示 + 对比卡 | 卡推播 1 条 | **免费** |
| 点「确认建单」后：建单回执 / 结果未知 / 失败 / 重试卡 | 每条推播 | 1 分钟内完成则**免费**，否则 1 条 |
| 付款没填够：提示 + 预览卡 | 推播 2 条 | 1 分钟内**免费**，否则 1 条 |
| 确认瞬间主档被删：提示 + 重问 | 推播 2 条 | 1 分钟内**免费**，否则 1 条 |
| 识别失败 / 结果待核 | 推播 1 条 | 1 分钟内**免费** |

### 仍然花钱（结构性，或要改交互才能省）

| 场景 | 为什么 | 能否再省（需用户决定） |
| --- | --- | --- |
| 网页编辑页每保存一次，LINE 里收到新卡 | 网页没有回覆令牌 | 可让 LIFF 页面以用户身份发一句（`liff.sendMessages`），机器人再免费回卡；聊天里会多一条用户消息，外部浏览器打开时不可用 |
| 菜单 5 缓存冷时：先回「读取中」，读完推结果 | 「读取中」已用掉令牌 | 改成只显示"正在输入"动画、读完用令牌回；超过 1 分钟仍要推播，且期间没有文字提示 |
| 审批：批准后先回「处理中」，结果推给管理员；通知申请人 / 审批人 | 结果在后台完成；通知对象是另一个人 | 管理员那条同上可改成动画；通知别人的消息无法免费 |
| 建单、识别、写档超过 1 分钟 | 令牌过期 | 不能 |
| 回执首次失败后的定时补发、超时认领的重试卡 | 后台定时任务，没有对话 | 不能 |

## 验证（候选源码，本机）

- `scripts/sample-verify-dms-quota.py` 新增 20 条合并发送断言，共 **46/46**。
- `check-architecture` 0 违规；`test-dms-call-contracts`、`test-dms-customer-identity` 通过；
  `check-dms-offline` 全部通过（`tests/test_dms_change_sync.py` 需 Python ≥ 3.11，本机 3.9 在
  `main` 上同样报错，用 3.11 跑通过）。
- 锁定 Frappe 16.33.0 镜像（arm64 `sha256:d5c87494…`，候选五个 App 源码只读挂载，`--network none`）：
  line-runtime 37、intake-queue 23、query-output 11、entrypoints 4、teardown 3、ERP/控制面 29，全部通过。
- 一次性 flow lab（`pearnly-dms-flow-lab-paidpush`，内网）：line-usage-redis 5、native-flow 10、
  native-recovery 8、booking-flow 33/33、entry-e2e 12/12、query-flow 46/46 通过。
- 按新行为更新的断言（旧断言钉的正是要改掉的推播）：
  - `test-dms-intake-queue`：保存回执与收尾句由"回覆 + 推播"改为"一次回覆两条"；
  - `test-dms-native-flow`：订车回执与主档变更卡改为走确认那次点击的回覆令牌；
    回执失败复用 retry key 的用例改为"回覆被拒 + 推播失败"再补发。
- 已知旧问题（与本次无关，未修）：`sample-verify-dms-menu-wiring.py` 第 268 行直接清空一个带已提交订车标记的会话，
  撞上 09-20 加的会话保护而中止；此前断言均通过。该脚本不在 CI。

## 未验证 / 限制

- 真实 LINE 是否按预期免费，只能在线上用量账看：部署后 `pearnly_core.line.usage` 里 `push_ok` 应明显下降、
  `reply_ok` 上升。HTTP 200 或测试通过不等于账单变化。
- 合并后，同一次反应里的消息在处理结束时一起到达（以前是逐条到）；建档、订车这些后台任务本来就只在结束时发消息，
  看起来不会有差别。

## 合并与上线（2026-09-25 泰国 19:44–20:05）

- 合并：`codex/dms-line-paid-push` 快进合入 `main` = `583262acf24688e723a1d1ac45457e7ae03fac6c`，远端回读一致。
  分支与 main 上的 Development checks 均通过；Build production image `36136873895` 通过。
- 镜像：`ghcr.io/skin306152-star/pearnly/frappe@sha256:b2e32d176c2637f762890e8ca1f0f4570b6f9a04de3dd005c014903c076e5ffe`
  （revision 标签 `583262a`），Stage `36137733418` 成功，`activate-product.sh … dms` 输出
  “Product dms activated with verified image.”，日志 `/opt/pearnly/state/activate-dms-583262a.log`。
  上一版为 `sha256:ed26caae…`（源码 `4997d72`）。
- 回读：8 个 DMS 容器均为 `b2e32d176c26`；`testdms` / `ris` ping 200、`maintenance_mode=0`、scheduler 已开；
  容器内 `line.batch`、webhook 与 `run_guarded` 的收集块、订车令牌透传均在；新容器 3 分钟内日志 0 条错误。
- 部署前用量基线（当天 15:55 发版后）：testdms `reply_ok=1`；ris `reply_ok=30, push_ok=4`。

### 仍未验收

- 真实 LINE 操作后的计费变化：需要实际走一遍菜单 1（保存 / 保留旧资料）、问候语、订车确认，
  再读 `bench --site testdms.pearnly.com execute pearnly_core.line.usage` 看 `push_ok` 是否不再增加。
- 用户确认前不算业务验收。
