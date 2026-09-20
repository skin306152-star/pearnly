<script setup lang="ts">
import { Button } from "frappe-ui";
import { LOCALES, LOCALE_LABELS, type Locale } from "../lib/i18n";

defineProps<{ locale: Locale; disabled?: boolean }>();
const emit = defineEmits<{ "update:locale": [locale: Locale] }>();
</script>

<template>
  <div class="pn-line">
    <header class="pn-line-bar">
      <span class="pn-line-brand">Pearnly</span>
      <div class="flex flex-wrap gap-1">
        <Button v-for="code in LOCALES" :key="code" size="sm"
          :variant="locale === code ? 'outline' : 'ghost'" :disabled="disabled"
          :aria-pressed="locale === code" @click="emit('update:locale', code)">
          {{ LOCALE_LABELS[code] }}
        </Button>
      </div>
    </header>
    <main class="pn-line-body"><slot /></main>
  </div>
</template>

<style scoped>
.pn-line { min-height: 100dvh; background: var(--bg); color: var(--ink); }
.pn-line-bar { display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: 8px; padding: 10px 14px; background: var(--card);
  border-bottom: 1px solid var(--outline-gray-2); }
.pn-line-brand { font-weight: 600; color: var(--ink); }
.pn-line-body { width: 100%; max-width: 720px; margin: 0 auto; padding: 14px; }
</style>
