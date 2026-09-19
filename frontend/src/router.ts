import { createRouter, createWebHashHistory } from "vue-router";

// Product Web runs in native Desk. LINE retains its ticket-based entry.
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: "/dms/editor",
      name: "dms-editor",
      meta: { standalone: true },
      component: () => import("./pages/dms/EditorPage.vue"),
    },
  ],
});
router.beforeEach((to) => {
  if (to.path === "/dms/editor") return true;
  window.location.replace("/desk/pearnly-home");
  return false;
});
