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
    // DMS 操作员花名册（老产品里这一页仅老板可见，含每行「发绑定码」）
    path: "/roster",
    name: "roster",
    component: () => import("./pages/dms/RosterPage.vue"),
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
