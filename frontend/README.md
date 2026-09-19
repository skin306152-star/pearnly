# LINE 独立编辑器

产品 Web 已按 ADR-005 改用自有 App 中的原生 Frappe Desk Page；入口 `/desk/pearnly-home`。旧 `/pearnly-app` 书签映射到对应 Desk 页面。

本 Vue 工程保留 LINE 一次性票据编辑器 `/pearnly-app/#/dms/editor`。该入口不是 Desk 登录页，保持现有票据、查询参数和 LIFF 契约。通用控件使用锁定的 Frappe UI，不新建第二套产品壳。

构建：`npm ci && npm run build`（Node 22，见锁文件）。产物写入 core/public/pearnly-app，遵循 .gitignore，不提交。产品 Desk 的 JavaScript 位于各 App 的 public/desk，Page 元数据随 bench migrate 同步。

旧 Vue Web 页面在当前迁移分支中仅作迁移对照，路由不再进入这些页面；完成三个产品的真实 Site 验收后再删除对照实现。新增功能必须走 Desk，不允许继续扩展旧 Web 页面。
