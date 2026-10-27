# 团队管理「添加用户」邮箱可不填（10-27）

> 分支 `pearnly/team-placeholder-email`，基线 main `5afc3755`。主会话转用户要求：租户在产品里「添加用户」也用开户单（61052381）同样的逻辑。

## 做法

- 共用：`pearnly_core/account/placeholder_email.py`——`for_user(登录名)` 生成 `<登录名>.<本站域名前缀>@pearnly.com`（全小写），
  `valid(邮箱)` 用开户单同一条格式。
- **前缀取本站自己的域名前缀**（站点名第一段），不是「租户第一行产品的前缀」：产品站点上没有记租户开户单里哪个产品排第一
  （`pearnly_tenant` 是控制面租户号，不是前缀）。单产品租户两者相同；多产品租户每个产品各用自己的前缀，前缀开户时已全局查重，地址照样唯一。
- ERP、Cowork 的 `team/service.add_member`：填了邮箱必须合法（原来只看有没有 `@`），否则「请填写有效邮箱」；留空 → 生成；
  生成的地址已被别的账号占了 → 拒绝，提示「用户名 X 已存在」（让管理员换登录名），不合并账号；手填的邮箱被占照旧「已存在」。
  统一身份池里撞名照旧由建号那一步拒绝。
- 前端：两产品团队页邮箱栏去掉 `reqd`（原生 FieldGroup），不加文案。
- 欢迎邮件 / 邀请：现有流程本来就不发（`send_welcome_email: 0`，初始密码由管理员当面给），不变；找回密码时信发到这个地址，经 Cloudflare 转到开户人。
- DMS 没有带邮箱的「添加用户」：人员页（roster）建操作员用系统自己生成的内部账号，不涉及。
- 生成的地址里带点，撞不上 `hello@pearnly.com` 这类官方地址；`hello` 作为站点前缀已被开户单保留前缀挡住。

## 验证（本机一次性实验栈 `pearnly-drop-lab`，线上同版镜像 fb1037ad，挂本分支）

| 场景 | ERP（erp-nb） | Cowork（cowork-nb） |
| --- | --- | --- |
| 管理员身份调 `add_member`，邮箱留空 | 建成 `blanke9a93.erp-nb@pearnly.com` | 建成 `blank35b8b.cowork-nb@pearnly.com` |
| 手填 `Typed…@Example.co.th` | 建成，存小写 | 建成，存小写 |
| 手填 `bad@example` | 拒：Enter a valid email | 拒：同左 |
| 生成的地址已被别的账号占 | 拒：Username … already exists | 拒：同左 |
| 团队页「添加用户」整页，邮箱留空保存（无头 Chrome） | 没测到：实验站点没开 ERP 功能与计费配置，团队页数据接口回「无权限」（环境，不是本改动） | 通过：邮箱栏无必填标记，保存后回列表，库里建成 `uiatm4p.cowork-nb@pearnly.com` |

- `tests/test_team_placeholder_email.py`（进 CI）：生成规则、格式校验、两产品都接上、邮箱栏非必填。
- 实验中发现：没有标记「初始设置已完成」的站点，框架会强制跳设置向导，外壳路由白名单再换回首页，两边来回导致页面栈溢出。
  生产站点都已完成初始设置，不受影响；外壳放行设置向导是另一件小事，已报主会话。

`check-impact`：新文件放在 `pearnly_core/account/`，命中共用件 login-session（5 个入口）连同团队页等共 11 个入口。

| 入口 | 结果 |
| --- | --- |
| `shared.team.members`、`cowork.page.team` | 通过（见上表） |
| `erp.page.team` | 服务端通过；页面没测到（实验站点 ERP 团队页数据接口无权限，环境原因） |
| `shared.login`、`shared.line.login`、`shared.shell-gate`、`control.issue-and-entry`、`erp/cowork.line.binding`、`erp.company-menu`、`shared.navigation` | 没测到：新模块只被两产品的 `add_member` 调用，登录、会话、LINE 绑定、开通闸、侧栏代码没改 |
