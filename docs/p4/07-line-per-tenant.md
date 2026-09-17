# LINE 每租户隔离

日期：2026-09-18。状态：**传输层与绑定跑通，隔离边界已实测**。

## 用户定的模型

> 每个租户一个域名一个 app **一条 LINE**。
> 旧的那边 DMS 已经接通过；同时为 ERP、Cowork 留接口；未来 LINE 可扩张。

模型是 **公司 × 产品 = 一个部署 = 一个域名 = 一条 LINE**。所以 LINE 是
**部署级**资源，不是产品级、更不是全局共用。

## 与旧系统的根本区别（这是本次要解决的问题）

| | 旧系统 | 现在 |
|---|---|---|
| 凭据粒度 | **按产品共享**：环境变量 `LINE_<产品>_CHANNEL_SECRET`，所有租户同一个密钥 | **按部署一条**：凭据存在控制面的 `Pearnly LINE Channel`，开通时推到该租户自己的站点 |
| webhook 地址 | 全站一个 `/api/line/dms/webhook` | 每租户自己的地址 `http://<租户域名>:<端口>/api/method/pearnly_core.line.webhook` |
| 隔离靠什么 | 靠路由里判断 `tenant_id` | **靠凭据本身**：站点只认自己那条密钥，别人签名验不过 |
| 绑定记录 | 表里带 `tenant_id` 来区分是谁家的 | 表在租户自己的站点里，**不需要 tenant 字段**（结构上不可能串号） |

"靠凭据本身"是刻意的选择：少一处 `if`，就少一处写错的机会。

## 分层

```
控制面  Pearnly LINE Channel ── 每部署一条：channel_id / secret / token / LIFF
           │  开通时随 set_line 步骤下发
           ▼
租户站点 pearnly_core.line ── 传输层：验签、幂等、发消息、webhook 入口
           │  hooks: pearnly_line_handlers
           ▼
产品    pearnly_dms.line_handlers ── 业务判断（当前：绑定流程）
        pearnly_erp_portal / pearnly_cowork ── 加一行 hook 即可接入
```

传输层只做"收事件 → 交给产品 → 回执"，三个产品共用；产品差异全在处理器里。
**接入新产品不用改传输层**：在它的 `hooks.py` 里加
`pearnly_line_handlers = ["<app>.line_handlers.handle"]` 即可。

## 身份绑定（共用层，三个产品都用）

```
网页 generate_bind_code(user) → 6 位一次性短码（30 分钟有效）
  → 用户把码发给本租户的 LINE 官方号
  → webhook 收到文本，consume_bind_code(code) 一次性消费 → 建绑定
  → 之后该 LINE 用户的消息按绑定找本地账号
```

短码字母表去掉了容易看错的字符（0/O、1/I/l），因为用户要手打。
**一人同时只有一个有效码**（生成新码会作废旧的）；**用过的码不能再用**。

## OCR：从控制面移除（用户 2026-09-18 明确）

> OCR 是所有 app 的共享底座，不是可选项，后台管理这边去掉吧。

已从控制面移除：`Pearnly Issue` 的 A/B 两档勾选、摘要字段、接口参数与返回、
旧脚本里的传参全部删掉；`product_manifest.py` 里换成了 `line_channels`，
并留了一行注释说明为什么 OCR 不该出现在这里（避免以后又被人当"可勾选能力"加回去）。

## 验证证据

脚本 `scripts/sample-line-isolation.sh`（可重复执行），对 **DMS 租户站点**实测：

| 用例 | 结果 |
|---|---|
| 用**本租户自己的**密钥签名 | **200** `{"ok":true}` ✓ |
| 用**别的租户的**密钥签名 | **400** `{"ok":false,"error":"bad_signature"}` ✓ 拒收 |
| 没配 LINE 的站点（Cowork）收任何签名 | **400** ✓ 关门（fail closed） |
| 同一条事件重投两次 | 库里只有 **1** 条 ✓ 不重复处理 |
| 绑定：生成码 → 发码 | 绑定到 `Administrator` ✓ |
| 同一个码再用一次 | **不建绑定** ✓ 一次性 |

开通流程实测（`ISS-2026-0039`）：

```
DEP-2026-0042:set_line → SKIPPED: 该租户还没配 LINE 渠道   （记为 skipped）
DEP-2026-0043:set_line → 已写 LINE 渠道配置 channel=@pearnly-dms-test
```

**"没配渠道"走显式的 `skipped`，不是含糊的 done** —— 看进度的人要能分清
"这个租户还不用做"和"这一步做完了"。

管理入口（Desk → 左侧「租户开通」）：**部署 / LINE 渠道 / 开通进度**已加入侧栏。

## 尚未做（不掩饰）

1. **业务动作还没接**：DMS 处理器目前只做绑定与引导。身份证录入、客户查询这些
   真正的业务动作要按旧系统已验证的流程逐个接进来。
2. **LIFF 未接**：字段留了（`liff_id`），但 LIFF 页面与身份换票还没做。
   旧系统 DMS 有"自动登录中继"，那属于 P6 的活。
3. **图片消息只回了一句提示**，还没进 OCR 流程。
4. **ERP / Cowork 只有接口没有处理器**：加一行 hook 就能接；没接之前
   它们的事件会被记成 `ignored`（不是错误）。
5. **没有与 LINE 平台真实通信过**：测试用的是自造密钥，只能验证隔离与流程。
   真机要真实的 channel secret 与 token（旧 DMS 那条可以拿来做第一个真机样本）。
6. **重试/补偿**：失败的事件只落 `failed`，没有自动重放（照旧系统的判断：
   LINE 重投是控制台开关、不归我们控，补救靠请用户重发）。
