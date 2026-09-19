# 正式密码重置邮件与默认泰语

基线 f72c679；用户已确认两封手机预览无问题，并要求默认账户语言为泰语。此工作包将已批准的重置卡片接入 Keycloak 原生 email theme，不创建账号、不重置密码、不追加真实测试邮件。

归属 infrastructure/keycloak/themes/pearnly/email；数据与令牌归 Keycloak。Frappe 欢迎邮件仍由 control onboarding 域管理，不增加并行发信链路。默认语言保持 realm / 新 Frappe User / 欢迎邮件的 th，保留已有用户选择。

实现包含四语言 HTML/text、190×54 公网 Logo、官方邮箱与电话、原生 link/linkExpiration、自动 HTML 转义。单一 UPDATE_PASSWORD 的管理员操作同样使用卡片；其他 required actions 使用上游通用语义。bootstrap-auth.py 持久化 emailTheme 以供未来重建。

验证：生产同版 FreeMarker 引擎 28 项渲染通过，架构检查通过，i18n-build 与前端翻译检查通过。全仓 check-i18n --strict 报 90 条既有内部中文日志；在未修改主线复核同样 90 条，本任务未修改这些 App 文件，不扩大忽略。

部署：仅新增云端已有 theme 挂载内的 email 子目录；最小 realm PUT 设置 emailTheme=pearnly、defaultLocale=th，读回一致。未重启业务容器、未重跑 bootstrap。公开 HTTPS Logo 返回 200 image/png，53105 字节，SHA256 83b030f461e94bb01952151c6c8c77082a9284bf2f40fb15de5a04bfd8844948 与源码一致。旧主题设置备份位于云端 /opt/pearnly/state/releases/20260920-password-email-theme/theme-before.json；此前 emailTheme 未设置，目录不存在。回退只需恢复之前 emailTheme，不变更 SMTP/账户。

手机预览验收来自用户本轮明确确认；正式 Keycloak 邮件尚未触发真实重置验证，不能将 FreeMarker 渲染或图片访问表述为正式收件验证。没有发送额外邮件或改变用户密码。
