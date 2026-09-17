# 控制面「租户开通」表单（Desk 原生）

日期：2026-09-17。状态：**已实现并端到端验证通过**。

## 用户定的规则（这一天逐条对齐出来的）

| 项 | 决定 |
|---|---|
| 位置 | Desk（Frappe 原生后台），**前端只用官方原生组件**，不自研页面、不自扩展 |
| 产品 | 仍然三个：Cowork / ERP / DMS |
| 域名 | **一个租户、每个产品各一个域名**；填的时候**只填前缀**，系统补成 `前缀.pearnly.com`（如 `aerp` → `aerp.pearnly.com`） |
| 开通粒度 | 不再"按产品整体开通"，改成**按功能模版勾选**，三个产品各自一块 |
| 每个产品都有首页 | 首页 = 现在的订阅与计费系统，三个产品都带 `home_subscription` |
| 凭据 | 用户收到**邀请邮件**后自己设密码；手机号不做（Keycloak 原生不支持，不另造） |
| 员工账号 | 租户自己在产品里加，开通时只建一个租户管理员 |

## 域名的两层约束（写下来免得以后踩）

命名规则是**一层**：`xxx.pearnly.com`。原因是证书，2026-09-17 实测：

| 测试域名 | 结果 |
|---|---|
| `aerp.pearnly.com` | ✅ 校验通过 |
| `zz-cowork.pearnly.com` | ✅ 校验通过 |
| `a.cowork.pearnly.com`（两层对照） | ❌ 握手失败 |

线上证书的 SAN 是 `DNS:*.pearnly.com, DNS:pearnly.com`（Let's Encrypt，Cloudflare 通用证书），
而通配符按标准只匹配**一层**。所以一层写法零证书工作，两层要另外买证书。
**写域名时不要写成两层。**

基础域名不是写死的：站点配置 `pearnly_base_domain` 可覆盖，默认 `pearnly.com`
（见 `pearnly_control/domains.py`）。前端那份从 boot 拿，避免前后端两套规则。

## 数据模型

新增两个 DocType，改了两个：

| DocType | 变化 |
|---|---|
| `Pearnly Feature`（新） | 功能目录：product + 功能代码 + 名称 + 是否常规开通 + 说明 |
| `Pearnly Issue Feature`（新，子表） | 只存一行「功能」链接 |
| `Pearnly Issue`（改） | 加 联系电话、三块功能表（cowork/erp/dms）、功能摘要、凭据交付方式、邀请邮件发送时间 |
| `Pearnly Issue App`（改） | `domain` 拆成 `domain_prefix`（填写）+ `domain`（只读，服务端算） |
| `Pearnly Target`（改） | 加「邀请链接用的客户端 ID」 |

功能目录由 `pearnly_control/features.py` 定义，`after_migrate` 钩子自动同步进
`Pearnly Feature`（幂等，**只增改不删除**，否则历史单据的勾选会变悬空链接）。
同步结果实测：Cowork 10 项 / ERP 8 项 / DMS 6 项，共 24 项。

目录是各产品 `product_manifest.py` 的**镜像**——控制面站点只装了
frappe + pearnly_core + pearnly_control，import 不到三个产品的清单，所以必须自己持一份。
**产品清单改了，这里要跟着改。**

## 服务端校验（都有实测）

| 输入 | 结果 |
|---|---|
| 前缀含大写（`Foo…`） | 规范成小写（DNS 本就不区分大小写），域名 `foo….pearnly.com` |
| 前缀含下划线 | 拒绝：前缀不合法 |
| 前缀全是数字 | 拒绝：不能全是数字 |
| 前缀是 `www` 等保留名 | 拒绝：保留名。**这条必须在开通时挡**——通配证书会把这些名字一起盖住 |
| 同单里域名重复 | 拒绝 |
| 域名已被别的单占用 | 拒绝，并指出是哪张单 |
| 把 ERP 的功能勾进 Cowork 栏 | 拒绝：功能 sales 属于 ERP，不能勾在 Cowork 那一栏 |
| 功能代码不存在 | 拒绝：Cowork 里没有这个功能 |
| 勾了 DMS 功能但没开 DMS | 拒绝 |
| 账号已存在 | 拒绝（不按邮箱合并到别人的账号） |

域名的真值是**服务端算出来的**，不看前端传什么：落库回读确认存的是
`domain_prefix` + `domain`（完整域名），不是用户输入。

## 凭据交付的两种方式

- `invite`（默认）：建号时**不设密码**，由身份服务发「自己设密码」的邀请邮件。
  发不出去会**报错并终止**——因为这种账号没密码，发不出去就等于开了一个登不进来的号。
- `password`：当场生成一次性初始密码，只在返回值里出现一次，不落库。
  留着是为了本机验证（测试账号是 `@sample.local`，邮件投不出去）。

## 验证证据

脚本 `scripts/sample-issue-form.sh`（可重复执行，走表单同一条接口）：

- 正常开通：`ISS-2026-0021`，cowork + dms 两个前缀 → `fcwo….pearnly.com` / `fdms….pearnly.com`，
  功能模版落库正确，交付方式 invite，**不返回一次性密码**。
- 上面那张校验表里的 9 个用例全部符合预期。
- 落库回读：`[{"app":"cowork","domain_prefix":"fcwo…","domain":"fcwo….pearnly.com"}, …]`。
- 浏览器实测（Playwright + 真实 Chromium）打开 `/desk/pearnly-issue/new`：
  表单渲染正常、**无 JS 报错**，字段为：租户名称 / 登录账号 / 使用人姓名 / 联系电话 /
  产品与域名（产品·域名前缀·访问域名·界面标题）/ Cowork·ERP·DMS 三块功能模版 / OCR 能力。
  截图：`docs/p4/evidence/issue-form-desk.png`。
- boot 实测：`pearnly_base_domain = pearnly.com`。
- **真发一封邀请邮件**到 `pearnlyai@gmail.com`（`ISS-2026-0027`，身份服务日志无错误）。
  收件确认需要用户看邮箱——这一层我读不了那个信箱。

## 已知限制与后续

1. **邀请链接用的客户端还是共用的**（本地样板配 `cowork-a`）。真实租户应当各自有自己的
   OIDC 客户端——那要等"建站"那一步做出来，届时换成租户自己的客户端。
2. **功能模版没有一键套用默认值**。目录里标了「常规开通」用来区分日常项与加购项
   （如协作看板、小助手），但勾选仍要手工点。要不要做成"新建时自动带上常规项"待定。
3. ~~**开通和建站还是两件事**~~ **已完成**：发号单现在能真的建出站点并让租户登进去，
   见 [06-provisioning.md](06-provisioning.md)。
4. **三个域名 `pearnlycowork.com` / `pearnlydms.com` / `pearnlyerp.com` 未注册**——
   这条已被子域名方案取代（见上），不再需要。
5. **通配 DNS 与边缘路由还没加**：Cloudflare 目前只有 `www` 有记录。
   要让租户真能打开网址，需要一次性加 `*.pearnly.com` 记录 + Worker 的 `*.pearnly.com/*` 路由。
   属于新系统上服务器时的事。
6. **旧的自研发号页 `/pearnly-issue` 还在**（`www/pearnly-issue.py` + `sample-issue-ui.sh`）。
   它现在和 Desk 表单做同一件事，属于重复入口，建议退役——等用户确认后再删。

---

## 补充（同日）：桌面入口 + 一键套用常规项

用户反馈"租户入口不知道从哪进"。在原生的桌面图标区（就是"框架"那张卡片旁边）
加了一个入口，点它同标签进入开通单列表。

### 桌面入口是怎么做的（原生机制）

| 环节 | 做法 |
|---|---|
| 入口本身 | 一条 `Desktop Icon`（label「租户开通」） |
| 点击去向 | `link_type=Workspace Sidebar` → 同标签跳转；**不用 `External`**，因为源码对 `External` 强制 `target="_blank"`（新标签页），不适合内部页面 |
| 配套侧边栏 | `Workspace Sidebar`「租户开通」，四项：开通单 / 租户 / 功能目录 / 开户目标。**第一项必须是 Link**，它决定点图标后落到哪 |
| 图标图形 | 本 App 自带 SVG：`public/icons/desktop_icons/{solid,subtle}/租户开通.svg`，尺寸配色照抄原生（54×54） |
| 可见性 | 非 standard 图标默认**只有创建者能看见**；以 Administrator 身份创建 → owner=Administrator → 所有人可见（源码 `get_desktop_icons` 的查询条件） |

不用 `standard=1`：standard 图标会被 migrate 的 "Removing orphan Desktop Icons"
当成孤儿（它不在任何 app 的 fixtures 里）删掉。改由 `after_migrate` 钩子幂等重建。

### 一键套用常规项

功能目录里标了 `default_on` 的即"常规项"。两条路径都会带上，只补缺、不动已勾的：

1. **选中产品时自动带**：在产品行选了 Cowork/ERP/DMS，且该产品功能表还空着，就自动填常规项；
2. **工具栏「套用常规项」按钮**：手动把所有已选产品的常规项补齐（幂等，重复点不会重复加）。

实测：选 cowork → 自动 6 项；再选 dms → 自动 6 项，且没动 cowork 那 6 项；
再点按钮 → 数量不变。

### 这一轮踩的坑（都会复发，记下来）

1. **中文标签的图标会变成"一个空色块"。** 桌面模板取图的顺序是
   `get_desktop_icon(label, style)` → `logo_url` → **画标签首字母**。
   中文字在字母表 sprite 里没有，第三条路就渲染出空白方块。
   必须给 App 自带 `public/icons/desktop_icons/<style>/<标签>.svg`。
2. **图标风格分 `subtle` 和 `solid` 两套，要都提供。** 本站的
   `desktop_icon_style` 是 **Solid**，只做 subtle 的话照样是空白块。
3. **改了 DocType 的 `.js` 必须 `bench clear-cache`。** 非 developer 模式下
   DocType meta（含 JS 正文）是缓存的。这次的表现是：文件已经是新版，
   但界面还在显示旧版的按钮，排查了好一阵。
4. **`frm.add_custom_button(label, fn, group)` 会把按钮塞进下拉组**，
   界面上直接找 `button` 找不到。常用动作不要传 group。
5. **`app_include_js` 不是必需的**：DocType 的 JS 由
   `frappe/desk/form/meta.py` 在运行时按 `<doctype>.js` 读取注入，不用 `bench build`。

### 验证

- 浏览器实测（真实 Chromium，`earn` 登录）：桌面上出现「租户开通」，
  图标 `img` 加载成功（`naturalWidth > 0`），href 为
  `/desk/pearnly-issue?sidebar=租户开通`，点击后地址变成 `/desk/pearnly-issue`（**同标签**），无 JS 报错。
- 截图：`docs/p4/evidence/desk-tenant-shortcut.png`、`issue-form-defaults.png`。
- 端到端脚本 `scripts/sample-issue-form.sh` 全绿（9 个拒绝用例 + 正常开通 + 落库回读）。
