# ADR-005 产品 Web 使用原生 Frappe Desk

状态：2026-09-20 用户确认。取代此前“产品 Web 使用 Vue + Frappe UI”的决定。

## 决定

用户所说的“官方 UI”明确指本地管理后台正在使用的 **Frappe Desk**。产品 Web 与管理后台使用同一个锁定版本的 Desk 运行时，不再维护独立 Vue 产品壳。官方 Frappe UI（Vue 组件库）与 Desk 是不同体系，不能用前者声称与后台原生组件一致。

- 页面使用自有 App 的标准 Page JSON/JS；导航使用 Workspace Sidebar。
- 表单使用 FieldGroup / Control，编辑使用 Dialog，明细使用原生 Grid / DataTable。
- 确认使用 frappe.confirm；错误使用服务端 frappe.throw 与 frappe.call 的原生反馈；成功使用 frappe.show_alert。提示音使用 frappe.utils.play_sound，遵守账户静音与浏览器播放限制。
- 页面继续调用现有业务 API。业务权限、状态机、金额、审计、幂等留在服务端；迁到 Desk 不新增 System Manager，不让客户获得平台管理权限。
- 侧栏继续按产品和购买功能过滤，页面入口检查同一份授权投影。原来未实现的功能仍明确未实现，不用假数据填充。
- 默认落点 /desk/pearnly-home；旧 /pearnly-app 路由按原页面映射跳转，保持书签兼容。
- 账户语言通过已验证的 Keycloak 会话同步身份资料和 Frappe User；默认泰语。Keycloak 认证页面继续用原生认证流程与自有主题，不嵌入 Desk。

## 明确的例外

LINE 的 /pearnly-app/#/dms/editor 使用一次性票据而非 Desk 会话。它保留独立 Vue + Frappe UI 页面、既有 URL/查询参数/票据契约；不能为外观统一而强制站点登录或放松票据鉴权。此例外不授权新增第二套普通产品 Web 界面。

## 开发与验收

新产品页放所属 App 的 `<module>/page/` 与 `public/desk/`，共用适配放 core/desk，不修改 Frappe 源码。共享规则由根 AGENTS、架构检查和 CI 执行。静态资源按内容生成版本号，避免旧浏览器缓存使代码与界面不一致。

本地验收需要真实 Site、角色、语言和计费 RPC；代理、socket.io、worker 与隔离数据齐备。打开页面不等于完成业务验收。生产部署、外部邮件/LINE/OCR/ERP结果与用户验收分别记录。

参考：[Page API](https://docs.frappe.io/framework/user/en/api/page)、[Dialog API](https://docs.frappe.io/framework/user/en/api/dialog)。具体可用方法以本仓锁定的 Frappe 源码为准。
