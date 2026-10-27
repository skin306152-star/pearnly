# 需求影响评估：发号单邮箱留空时自动填「前缀@pearnly.com」

> 基线：`origin/main` ab823298。工作树 `../pearnly-wt-placeholder-email`，分支 `pearnly/tenant-placeholder-email`。
> 状态：用户 2026-10-27 审定，第 12 栏四条全部按建议执行（「后台平台邮箱那个字段……留空时就自动呗」）。
> Cloudflare：pearnly.com 的 Catch-all 已从「Drop / 停用」改为「转发到 skin306152@gmail.com / 启用」（2026-10-27，用户授权后操作）；`hello@pearnly.com` 规则不变。

## 0. 需求原文与理解

- 原话（用户 2026-10-27，本会话）：「现在很多租户，他们没有邮箱……开户时，自动给他创建一个临时邮箱，比如 polymer@pearnly.com，这个邮箱只能用于以后真的租户忘记密码了，要找回的时候用得上能收到邮件而已」；随后要求「开户单在邮箱留空时自动填 前缀@pearnly.com」。
- 理解：开户人在管理后台（本机 control）建发号单时，邮箱一格可以不填；保存时服务端自动填成 `<域名前缀>@pearnly.com`。这个地址不是真邮箱：pearnly.com 的收信已经交给 Cloudflare 邮件转发（MX 为 `route1/2/3.mx.cloudflare.net`），由一条 catch-all 规则把所有 `*@pearnly.com` 转到开户人的邮箱。
- 不在本需求范围：给租户建真实邮箱；改登录页 / Keycloak 主题（「登录页不动」）；改控制面发号页 `www/pearnly-issue.html`（「控制面发号页不要管」）；已开户租户的邮箱批量改写；租户自己改邮箱的入口。

## 1. 现有同类与复用

- 邮箱只有一个落点：`Pearnly Issue.account_email`（`pearnly_issue.json`，`reqd: 1`），校验在 `pearnly_issue.py::_validate_account`。
- 下游读它的地方（全部只读，不需要改）：
  - `issues.py:204/221` 在各身份池查重并建 Keycloak 账号；
  - `onboarding/welcome.py:105` 发欢迎邮件；
  - `provisioning.py:634/748` 站点管理员与开通清单；
  - `account/repool.py`、`assistant_entry/backfill.py`、`teardown.py` 按邮箱找账号。
- 结论：**扩展现有**。只在 `_validate_account` 里补默认值，下游拿到的仍然是一个普通邮箱字符串，不新增字段、不新增流程。

## 2. Frappe 原生能力

- 用 DocType 原生的 `reqd` 和 `validate()` 补值：把 `account_email` 的 `reqd` 改成 0，空值在服务端 `validate()` 里补齐，表单不加自定义控件。
- 结论：原生字段和原生校验钩子，无自定义 UI。

## 3. 按类通用

- 类别：「所有建发号单的入口」。
  - Desk 表单（`/desk/pearnly-issue/new`）：生效。
  - 接口 `issues.create_issue`（`issues.py:79`）：同样走 DocType `validate()`，生效；它自己在 `issues.py:106` 先做 `strip().lower()`，空串照常传进来，由 `validate()` 补。
  - 控制面发号页 `www/pearnly-issue.html`：前端第 175 行仍拦「请填邮箱」，**按「控制面发号页不要管」不改**，那一页继续必填。
- 默认地址取哪个前缀：发号单的产品行可能有多行、前缀不同。建议取**第一行产品的域名前缀**。前缀已全局唯一（`_validate_apps` 跨单查重），所以生成的地址也唯一。

## 4. 上下游数据

- ERP 单据流 / Express：不涉及，邮箱只用于身份与通知。
- CC 桌面版：不涉及，载荷不含开户邮箱。
- Cowork / DMS：同一张发号单开多个产品时，各身份池用同一个邮箱建号（现状就是这样），不受影响。
- 身份池查重：`issues.py:204` 如果某个池里已有同名邮箱就拒绝开户。自动地址按前缀唯一，正常不会撞；如果真撞了，照现在的规则拒绝，不合并。

## 5. 隔离与权限

- 发号单只有 System Manager 能建（管理后台本机），不变。
- 风险：自动地址和我们**自己在用的 pearnly.com 邮箱重名**。发信地址是 `hello@pearnly.com`（`welcome.py:14`），而 `hello` 不在 `RESERVED_PREFIXES` 里。如果租户前缀叫 `hello`，就会和官方发信地址撞名。施工时把 `hello`（以及 Cloudflare 里已有的其他转发地址）加进保留前缀，在服务端拒绝。
- 跨租户串信：catch-all 把所有租户的信都转给开户人一个人，租户之间看不到彼此的信；但开户人能看到所有租户的找回邮件，这是本方案的前提（见第 12 栏）。

## 6. 入口：网页、手机、LINE、拍照

- 只涉及管理后台 Desk 表单和创建接口，其他入口不涉及。
- 租户侧找回密码：登录页「忘记密码」照常发信到 `前缀@pearnly.com`，经 Cloudflare 转到开户人，再由开户人转给租户。租户绑过 LINE 的话，可以直接走 LINE 免密登录，不需要找回。

## 7. 共用件与连带回归

- `python3 scripts/check-impact.py --files .../pearnly_issue.py .../pearnly_issue.json` 输出：「没有改到登记的共用件或其入口」。
- 冒烟：不新增；现有 `scripts/production/test-issue-actions.cjs` 不受影响。

## 8. 存储与成本

- Cloudflare Email Routing 免费，只转发不存储；我们服务器不收信。
- 新增固定月费：无。
- 限制：catch-all 的目标邮箱要先在 Cloudflare 验证；转发失败（目标邮箱满或拒收）时信会被退回，不影响开户本身。

## 9. 删除、作废、追溯

- 删租户（`teardown.py`）照旧按 `account_email` 找账号删除，自动地址和手填地址同等对待。
- Cloudflare 侧没有逐个地址的记录（catch-all），租户删掉后不需要清理。

## 10. 文案与官方 UI

- 不新增文案。表单上「Email」一格的必填星号去掉，标签不变。
- 现有报错「Invalid email」保留：手填了错误格式仍然拒绝。
- 保留前缀报错沿用现有「Domain prefix {0} is reserved; pick another」。

## 11. 测试与验收

- 单元（`tests/test_cloud_onboarding.py` 同类写法）：
  1. 邮箱留空 → 存成 `<第一行前缀>@pearnly.com`；
  2. 手填邮箱 → 原样小写保存，不被覆盖；
  3. 手填格式错误 → 仍报「Invalid email」；
  4. 前缀 `hello` → 拒绝；
  5. 留空且没有产品行 → 报「Pick at least one product」，不生成半个地址。
- 闸门：`scripts/check-architecture.py`、`scripts/check-copy.py`，以及 development.yml 同名闸门。
- 实测：本机管理后台建一张邮箱留空的测试发号单，看回填地址；开户后在登录页点「忘记密码」，确认开户人邮箱收到转发。这一条依赖 Cloudflare catch-all 已打开。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 多个产品前缀不同时，用哪个前缀生成地址？ | 第一行产品的前缀 | 不定就无法生成唯一地址 |
| 2 | 自动地址的租户还发不发欢迎邮件？ | 照发：它会转到你邮箱，正好验证转发是通的 | 不发的话要改 `welcome.py`，范围变大 |
| 3 | catch-all 转到哪个邮箱？ | 你登录 Cloudflare 用的那个邮箱（已验证，不用再收验证信） | 不定就不能打开转发，找回邮件会被退回 |
| 4 | 找回邮件都进你的邮箱，由你转给租户，这个前提可以接受吗？ | 可以接受；租户主要靠 LINE 免密登录，找回是兜底 | 不接受就只能给租户建真邮箱，会有月费 |

## 13. 工作量与排期

- 一步交付：改 `pearnly_issue.json`（`reqd`）+ `pearnly_issue.py`（补默认值 + 保留 `hello`）+ 单元测试。
- 机器耗时：写代码约 10 分钟，本地闸门约 10 分钟，推分支等 CI 约 10 分钟；管理后台是本机，合入后在本机 control 迁移即生效，不涉及生产部署。
- 依赖：Cloudflare catch-all 打开后才能做最后那条实测。
