<script setup lang="ts">
// 产品壳。**侧栏用官方 Frappe UI 组件**（用户 2026-09-18 定："侧栏的前端组件用官方的"），
// 业务内容用自己的（见 src/pages/）。
//
// 侧栏里显示哪些项由**后端给的授权**决定（`pearnly_core.shell.describe` 的 features），
// 前端不自己判断买了什么。前端隐藏只是别让人点进死路，真正的拦截在后端。
import { computed, onMounted, provide, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { Sidebar } from "frappe-ui";
import { loadShell, type ShellBoot } from "./lib/shell";
import { navFor, titleFor, type NavEntry } from "./lib/nav";
import { adoptAccountLocale, createTexts } from "./lib/i18n";
import { iconFor } from "./lib/icons";
import { ApiError } from "./lib/api";
import { SHELL_BOOT } from "./lib/boot-context";
import AccountMenu from "./components/AccountMenu.vue";

const boot = ref<ShellBoot | null>(null);
const error = ref("");
const route = useRoute();

// 侧栏文案走语言包（键 = `nav.*`）。`createTexts([], "nav.")` 表示
// "把 nav. 这个命名空间的整包取回来" —— 页面不维护"我要用哪些键"的清单，
// 清单和语言包一旦不同步，界面上就会露出键名（真发生过）。
const { t: tNav, load: loadNavTexts } = createTexts([], "nav.");

// 独立页（LINE 内的编辑器）：**不套外壳、也不问启动数据**。
// 它靠一次性票据认身份，没有站点会话 —— 去调 `shell.describe` 只会拿到 401，
// 页面就永远停在"需要登录站点"那一屏（真踩过）。
const standalone = computed(() => route.meta.standalone === true);

onMounted(async () => {
  if (standalone.value) return;
  try {
    boot.value = await loadShell();
    // 文案取不到不该把整个壳挡住：`t()` 取不到会显示键名 —— 看得见、可排查。
    await loadNavTexts().catch(() => {});
  } catch (err) {
    error.value =
      err instanceof ApiError
        ? `启动失败（HTTP ${err.status}）：${err.message}`
        : `启动失败：${String(err)}`;
  }
});

provide(SHELL_BOOT, boot as unknown as ShellBoot);

watch(
  boot,
  (value) => {
    if (!value) return;
    // 语言以**账号**为准（ADR-006）：浏览器里缓存的那份只当兜底。
    // 不这样做，同一个账号换台电脑就会看到不同语言。
    adoptAccountLocale(value.language || "");
    document.documentElement.lang = value.language || "zh-CN";
    if (value.product_label) document.title = `Pearnly ${value.product_label}`;
  },
  { immediate: true },
);

// 官方 Sidebar 要的形状：header + sections[{ label, items[] }]
const header = computed(() => ({
  title: "Pearnly",
  subtitle: boot.value?.product_label || "",
}));

const sections = computed(() => {
  if (!boot.value) return [];
  const current = route.path;
  return navFor(boot.value.product, boot.value.features).map((entry: NavEntry) => {
    const items = "items" in entry ? entry.items : [entry];
    return {
      label: "items" in entry ? tNav(entry.key) : "",
      items: items.map((item) => ({
        label: tNav(item.key),
        icon: iconFor(item.route),
        to: item.route,
        isActive: current === item.route,
      })),
    };
  });
});

const pageTitle = computed(() => {
  for (const section of sections.value) {
    const hit = section.items.find((i) => i.isActive);
    if (hit) return hit.label;
  }
  // 侧栏里找不到不等于"没这一页"：没买的功能不显示在菜单里，但页面本身在。
  // 标题按路由查，查不到才是真的没建。
  return titleFor(route.path) ? tNav(titleFor(route.path)) : "未建页面";
});

</script>

<template>
  <RouterView v-if="standalone" />

  <div v-else-if="error" class="pn-center">
    <div class="pn-card">
      <div class="pn-error">{{ error }}</div>
      <p class="pn-muted" style="margin: 12px 0 0">
        这一页需要登录站点之后才能打开。请从站点域名进入并完成登录。
      </p>
    </div>
  </div>

  <div v-else-if="!boot" class="pn-center">
    <div class="pn-skeleton" style="width: 220px" />
  </div>

  <div v-else class="pn-shell">
    <!-- 侧栏：官方组件 -->
    <Sidebar :header="header" :sections="sections">
      <template #footer-items>
        <!-- 左下角账号菜单：点开是改密 / 退出（三个产品共用同一套） -->
        <AccountMenu :boot="boot" />
      </template>
    </Sidebar>

    <main class="pn-main">
      <header class="pn-topbar">
        <span class="pn-page-title">{{ pageTitle }}</span>
        <span class="pn-muted">Pearnly {{ boot.product_label }}</span>
      </header>
      <div class="pn-content">
        <RouterView />
      </div>
    </main>
  </div>
</template>

<style scoped>
.pn-center {
  display: grid;
  place-items: center;
  height: 100%;
  padding: 24px;
}
</style>
