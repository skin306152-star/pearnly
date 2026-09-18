<script setup lang="ts">
// 临时卡片：把启动数据摊在界面上，方便一眼确认"这个壳认对了产品、也认对了买到的功能"。
// 等各页都做完就撤掉。
import type { ShellBoot } from "../lib/shell";

defineProps<{ boot: ShellBoot }>();
</script>

<template>
  <div class="pn-card">
    <div style="font-size: 15px; font-weight: 800; margin-bottom: 10px">本站点启动数据</div>
    <table class="pn-table">
      <tbody>
        <tr>
          <td class="pn-muted">产品</td>
          <td>{{ boot.product }}（{{ boot.product_label }}）</td>
        </tr>
        <tr>
          <td class="pn-muted">当前账号</td>
          <td>{{ boot.full_name }} &lt;{{ boot.user }}&gt;</td>
        </tr>
        <tr>
          <td class="pn-muted">界面语言</td>
          <td>{{ boot.language }}</td>
        </tr>
        <tr>
          <td class="pn-muted">买到的功能</td>
          <td>
            <code v-for="code in boot.features" :key="code" class="pn-chip">{{ code }}</code>
            <span v-if="!boot.features.length" class="pn-muted">（一个都没有）</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
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
