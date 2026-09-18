<script setup lang="ts">
// 页面清单里有、但还没做的页面。
//
// 为什么要有这个页而不是直接 404：菜单项是照清单列出来的（甲方看到的就是这些入口），
// 点进去 404 会让人以为是坏了。这里如实说"还没做"，把"清单是什么"和"做到哪了"分开。
import { useRoute } from "vue-router";

const route = useRoute();
</script>

<template>
  <div class="pn-card">
    <h2 style="margin: 0 0 6px; font-size: 18px">这一页还没做</h2>
    <p class="pn-muted" style="margin: 0 0 12px">
      菜单是照页面清单列出来的，功能还没实现。当前路由：<code>{{ route.path }}</code>
    </p>
    <RouterLink class="pn-btn pn-btn--ghost" to="/home">回到首页</RouterLink>
  </div>
</template>
