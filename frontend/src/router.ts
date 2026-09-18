import { createRouter, createWebHashHistory, type RouteRecordRaw } from "vue-router";

// 用 hash 路由（`#/purchase`）而不是 history 路由：
// 静态文件由站点直接提供，history 路由会要求服务端把任意子路径都回同一个 html；
// 而业务站点的 before_request 会把未登录访客送去身份服务，两者叠在一起容易出
// "刷新就跳登录"的怪毛病。hash 不经过服务端，最省事也最稳。
const routes: RouteRecordRaw[] = [
  { path: "/", redirect: "/home" },
  {
    path: "/home",
    name: "home",
    component: () => import("./pages/HomePage.vue"),
  },
  {
    path: "/purchase",
    name: "purchase",
    component: () => import("./pages/purchase/PurchasePage.vue"),
  },
  {
    path: "/intake",
    name: "intake",
    component: () => import("./pages/cowork/IntakePage.vue"),
  },
  {
    // 「开户 → 使用」的第一站：租户拿到站点后先把自己的 DMS 连接配上
    path: "/connect",
    name: "dms-connect",
    component: () => import("./pages/dms/ConnectPage.vue"),
  },
  {
    // DMS 操作员花名册（老产品里这一页仅老板可见，含每行「发绑定码」）
    path: "/roster",
    name: "roster",
    component: () => import("./pages/dms/RosterPage.vue"),
  },
  {
    // **LINE 内的草稿编辑器**（老产品 `/liff/dms-booking` 的落地页）。
    // `meta.standalone` = 不套产品外壳：这一页在 LINE 的 webview 里打开，身份是
    // 一次性票据而不是站点会话，所以既没有侧栏也不该去问 `shell.describe`。
    path: "/dms/editor",
    name: "dms-editor",
    meta: { standalone: true },
    component: () => import("./pages/dms/EditorPage.vue"),
  },
  {
    // 清单里有、但还没做的页面：给一个诚实的占位，不假装能点
    path: "/:pathMatch(.*)*",
    name: "not-built",
    component: () => import("./pages/NotBuiltPage.vue"),
  },
];

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
});
