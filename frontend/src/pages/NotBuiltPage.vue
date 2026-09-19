<script setup lang="ts">
// 页面清单里有、但还没做的页面。
//
// 为什么要有这个页而不是直接 404：菜单项是照清单列出来的（甲方看到的就是这些入口），
// 点进去 404 会让人以为是坏了。这里如实说"还没做"，把"清单是什么"和"做到哪了"分开。
import { onMounted } from "vue";
import { useRoute } from "vue-router";
import { Button } from "frappe-ui";
import { createTexts } from "../lib/i18n";

const route = useRoute();

// 页面文案走语言包（键 = `shell.*`）：跟产品壳自己的话术共用一套。
const { t, load: loadTexts } = createTexts([], "shell.");

onMounted(() => {
  // 文案取不到不该把整页挡住：`t()` 取不到会显示键名 —— 看得见、可排查。
  loadTexts().catch(() => {});
});
</script>

<template>
  <div class="pn-card">
    <h2 style="margin: 0 0 6px; font-size: 18px">{{ t('shell.notBuilt.title') }}</h2>
    <p class="pn-muted" style="margin: 0 0 12px">
      {{ t('shell.notBuilt.body') }}<code>{{ route.path }}</code>
    </p>
    <Button variant="outline" theme="gray" :label="t('shell.backHome')" route="/home" />
  </div>
</template>
