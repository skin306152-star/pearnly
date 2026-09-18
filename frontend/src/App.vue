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
import { navFor, type NavEntry } from "./lib/nav";
import { iconFor } from "./lib/icons";
import { ApiError } from "./lib/api";
import { SHELL_BOOT } from "./lib/boot-context";

const boot = ref<ShellBoot | null>(null);
const error = ref("");
const route = useRoute();

onMounted(async () => {
  try {
    boot.value = await loadShell();
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
      label: "items" in entry ? entry.label : "",
      items: items.map((item) => ({
        label: item.label,
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
  return "未建页面";
});

function initials(name: string): string {
  const trimmed = (name || "").trim();
  return trimmed ? trimmed.slice(0, 1).toUpperCase() : "?";
}
</script>

<template>
  <div v-if="error" class="pn-center">
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
        <div class="pn-account">
          <span class="pn-avatar">{{ initials(boot.full_name || boot.user) }}</span>
          <span class="pn-who">
            <strong>{{ boot.full_name || boot.user }}</strong>
            <span>{{ boot.user }}</span>
          </span>
        </div>
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
