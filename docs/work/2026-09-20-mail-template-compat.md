# 手机 Gmail 图片修复与重置密码卡片预览

基线 e97328f；用户反馈真实 Gmail 手机截图：顶部图片破图、底部出现 Logo 附件。要求修复并设计相同风格的重置密码模板，发送两封到本轮明确指定的 pearnlyai@gmail.com。

根因：原模板同时声明 src 和 embed；Frappe 16.33 replace_filename_with_cid 只将 embed 替换为 src，导致最终 img 有两个 src。旧检查仅查 MIME 内存在 CID，不能证明图片引用有效。新增 HTML 属性回归，在旧模板失败、修复版本通过；检查恰好一个 CID src、对应附件 Content-ID 和固定 190×54 尺寸。四语言原生 MIME/转义/脱敏回归通过。另将关键容器宽度、留白内联，减少邮件客户端忽略 head CSS 的影响。

欢迎模板修复位于自有 App 原生 templates/emails。密码重置设计稿位于 docs/previews/welcome-email/password-reset-preview.html.jinja：同品牌与联系卡、重置按钮、示例有效期30分钟、非本人操作提示，无密码回显。此轮仅发设计预览，尚未替换云端 Keycloak 的正式重置模板；正式接入时须使用 Keycloak 原生 link/linkExpiration，而不是示例链接。已读取正在运行的 Keycloak 26.7.4 原生 password-reset/executeActions 模板确认变量契约。

两封预览均走实际 Frappe Email Account/Email Queue，官方 From/Reply-To=hello@pearnly.com，收件人 pearnlyai@gmail.com。账号、密码、域名均为示例；重置地址 example.com，不生成真实重置令牌、不改变账号密码。发送前检查最终 MIME 引用，发送后由原生队列脱敏。手机最终显示仍需用户查看，不能用服务端 MIME 检查代替客户端验收。

实际发送回执：欢迎新版 p5ud09a3co、密码重置预览 p7uel6icaq，原生队列均为 Sent。该状态证明 SMTP 投递完成，不代替手机显示验收。
