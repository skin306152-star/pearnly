<script setup lang="ts">
// 首页 = 订阅与计费（三个产品同一个页面）。
//
// ⚠️ 现在**只是占位**：新平台还没有订阅/计费的后端（全仓搜不到相关服务，
// 只有功能目录里提到过这个功能）。所以这里如实说明，不画一个点了没反应的假界面。
//
// 旧系统里这一页有两套各写各的实现（产品壳 303 行 / DMS 1747 行），合并时要先定
// 后端接口以哪套为准 —— 见 docs/frontend/01-page-inventory.md。
import { useBoot } from "../composables/useBoot";
import ShellBootCard from "../components/ShellBootCard.vue";

const boot = useBoot();
</script>

<template>
  <div class="pn-card">
    <h2 style="margin: 0 0 6px; font-size: 20px">订阅与计费</h2>
    <p class="pn-muted" style="margin: 0 0 14px">管理套餐 · 用量 · 余额</p>
    <div class="pn-error" style="background: var(--warn-weak); color: var(--warn)">
      这一页还没做：新平台目前没有订阅/计费的后端服务，做不出可用的界面。
    </div>
    <p class="pn-muted" style="margin: 14px 0 0">
      三个产品（Cowork / ERP / DMS）共用同一个充值入口，所以这一页只需要一套实现。
      等后端接口定下来就接上。
    </p>
    <p class="pn-muted" style="margin: 8px 0 0">
      当前产品：{{ boot.product_label }}
    </p>
  </div>

  <div style="margin-top: 16px">
    <ShellBootCard :boot="boot" />
  </div>
</template>
