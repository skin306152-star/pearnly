<script setup lang="ts">
// 产品壳：左边导航（按买到的功能过滤）+ 上边栏 + 主区。
//
// 启动数据在挂载前就问好（main.ts 之前？不 —— 在这里问），因为这决定整个壳长什么样：
// 什么产品、买了什么、界面语言。拿不到就显示错误，不装作能跑。
import { computed, onMounted, provide, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { loadShell, type ShellBoot } from "./lib/shell";
import { navFor, type NavEntry } from "./lib/nav";
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

// 页面通过 inject 拿启动数据（不是给 RouterView 传 props —— 那条路传不通）
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

const nav = computed<NavEntry[]>(() =>
  boot.value ? navFor(boot.value.product, boot.value.features) : [],
);

const pageTitle = computed(() => {
  if (!boot.value) return "";
  for (const entry of nav.value) {
    if ("items" in entry) {
      const hit = entry.items.find((i) => i.route === route.path);
      if (hit) return hit.label;
    } else if (entry.route === route.path) {
      return entry.label;
    }
  }
  return "未建页面";
});

function initial(name: string): string {
  const trimmed = (name || "").trim();
  return trimmed ? trimmed.slice(0, 1).toUpperCase() : "?";
}
</script>

<template>
  <div v-if="error" class="pn-boot-error">
    <div class="pn-card">
      <div class="pn-error">{{ error }}</div>
      <p class="pn-muted" style="margin: 12px 0 0">
        这一页需要登录站点之后才能打开。请从站点域名进入并完成登录。
      </p>
    </div>
  </div>

  <div v-else-if="!boot" class="pn-boot-loading">
    <div class="pn-skeleton" style="width: 220px" />
  </div>

  <div v-else class="pn-shell">
    <aside class="pn-sidebar">
      <div class="pn-brand">
        <span class="pn-brand-dot" aria-hidden="true" />
        <span>Pearnly</span>
      </div>

      <nav class="pn-nav" aria-label="主导航">
        <template v-for="entry in nav" :key="'items' in entry ? entry.label : entry.route">
          <div v-if="'items' in entry" class="pn-nav-group">
            <div class="pn-nav-group-label">{{ entry.label }}</div>
            <RouterLink
              v-for="item in entry.items"
              :key="item.route"
              class="pn-nav-item"
              :class="{ 'is-active': route.path === item.route }"
              :to="item.route"
            >
              {{ item.label }}
            </RouterLink>
          </div>
          <RouterLink
            v-else
            class="pn-nav-item"
            :class="{ 'is-active': route.path === entry.route }"
            :to="entry.route"
          >
            {{ entry.label }}
          </RouterLink>
        </template>
      </nav>

      <div class="pn-sidebar-foot">
        <span class="pn-avatar">{{ initial(boot.full_name || boot.user) }}</span>
        <span class="pn-who">
          <strong>{{ boot.full_name || boot.user }}</strong>
          <span>{{ boot.user }}</span>
        </span>
      </div>
    </aside>

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
.pn-boot-error,
.pn-boot-loading {
  display: grid;
  place-items: center;
  height: 100%;
  padding: 24px;
}
</style>
