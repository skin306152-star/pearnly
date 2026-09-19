# Pearnly 账户安全邮件

Keycloak 原生 email theme，继承 `base`。`password-reset.ftl` 以及仅含 UPDATE_PASSWORD 的 executeActions 使用已验收的卡片；其他操作保留上游文案语义，不能统一冒称改密。

默认账户语言为泰语：生产 realm `defaultLocale=th`，Frappe 新账户和欢迎邮件默认 `th`。用户已有语言选择保留；邮件使用 Keycloak 解析后的 locale。支持 th、zh-CN、en、ja，不以控制面管理员的界面语言选择邮件语言。

重置链接与分钟数由 Keycloak 的 `link` / `linkExpiration` 提供；不生成自己的令牌，不固定有效期，不改变密码生命周期。HTML 自动转义。Logo 使用原生 `url.resourcesUrl` 的绝对 HTTPS 地址（不能用只返回路径的 resourcesPath），190×54；无请求上下文时显示品牌文字。欢迎邮件则继续使用 Frappe 原生 CID，不混用两套图片机制。

验证：用运行中的 Keycloak 26.7.4 / FreeMarker 2.3.32 运行 `tests/CheckPasswordEmailTheme.java`，参数为本 email 目录及官方 themes JAR 解出的 `theme/base/email` 目录。覆盖四语言 HTML/text、单一改密/其他操作、HTML 注入转义、原生动态有效期、无 url 降级，不发送邮件。

部署：投递该 email 子目录到 Keycloak 已挂载的 pearnly theme 下，再设置 realm `emailTheme=pearnly`。bootstrap-auth.py 保存未来重建设置；不要为了改主题重跑其全部身份初始化。检查公开 Logo 返回 PNG 且哈希一致、realm 主题与默认语言。原生密码重置真实收件仍是独立验收。
