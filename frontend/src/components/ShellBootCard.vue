<script setup lang="ts">
import { Card } from "frappe-ui";
// 临时卡片：把启动数据摊在界面上，方便一眼确认"这个壳认对了产品、也认对了买到的功能"。
// 等各页都做完就撤掉。
import { onMounted } from "vue";
import type { ShellBoot } from "../lib/shell";
import { createTexts } from "../lib/i18n";

defineProps<{ boot: ShellBoot }>();

// 卡片文案走语言包（键 = `shell.*`）：跟产品壳自己的话术共用一套。
const { t, load: loadTexts } = createTexts([], "shell.");

onMounted(() => {
  // 文案取不到不该把整页挡住：`t()` 取不到会显示键名 —— 看得见、可排查。
  loadTexts().catch(() => {});
});
</script>

<template>
  <Card :title="t('shell.bootCard.title')" class="">
    <dl class="grid grid-cols-[auto_1fr] gap-3">
      <dt class="pn-muted">{{ t("shell.bootCard.product") }}</dt>
      <dd>{{ boot.product }}（{{ boot.product_label }}）</dd>

      <dt class="pn-muted">{{ t("shell.bootCard.account") }}</dt>
      <dd>{{ boot.full_name }} &lt;{{ boot.user }}&gt;</dd>

      <dt class="pn-muted">{{ t("shell.bootCard.language") }}</dt>
      <dd>{{ boot.language }}</dd>

      <dt class="pn-muted">{{ t("shell.bootCard.features") }}</dt>
      <dd>
        <code v-for="code in boot.features" :key="code" class="pn-chip">{{
          code
        }}</code>
        <span v-if="!boot.features.length" class="pn-muted">{{
          t("shell.bootCard.none")
        }}</span>
      </dd>
    </dl>
  </Card>
</template>

<style scoped>
.pn-chip {
  display: inline-block;
  margin: 2px 6px 2px 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--accent-weak);
  color: var(--accent-deep);
  font-size: 12px;
  font-weight: 700;
}
</style>
