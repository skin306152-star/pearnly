import { createRouter, createWebHashHistory } from "vue-router";

// Product Web runs in native Desk. LINE retains its ticket-based entry.
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: "/dms/account",
      name: "dms-line-account",
      meta: { standalone: true },
      component: () => import("./pages/dms/AccountPage.vue"),
    },
    {
      path: "/dms/scan",
      name: "dms-scan",
      meta: { standalone: true },
      component: () => import("./pages/dms/ScanPage.vue"),
    },
    {
      path: "/dms/editor",
      name: "dms-editor",
      meta: { standalone: true },
      component: () => import("./pages/dms/EditorPage.vue"),
    },
  ],
});
router.beforeEach((to) => {
  if (["/dms/editor", "/dms/account", "/dms/scan"].includes(to.path)) return true;
  window.location.replace("/desk/pearnly-home");
  return false;
});
