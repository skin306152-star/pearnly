<script setup lang="ts">
// 产品内左下角的账号菜单。
//
// 用户当时的要求："改密放在登录后产品内，左下角或者右下角点开头像改，不是登录页"。
// 三个产品共用同一套（本仓前端本来就只有一份）。
import { computed, onMounted, ref } from "vue";
import { Dropdown } from "frappe-ui";
import type { ShellBoot } from "../lib/shell";
import { createTexts } from "../lib/i18n";
import ChangePasswordDialog from "./ChangePasswordDialog.vue";

// 菜单文案走语言包（键 = `account.*`）。
const { t, load: loadTexts } = createTexts([], "account.");

const props = defineProps<{ boot: ShellBoot }>();

const passwordOpen = ref(false);

function initials(name: string): string {
  const trimmed = (name || "").trim();
  return trimmed ? trimmed.slice(0, 1).toUpperCase() : "?";
}

// **必须是 computed**：语言包是异步取回来的，在这里求值一次就会永久定格成键名。
const options = computed(() => [
  {
    label: t("account.changePassword"),
    icon: "key",
    onClick: () => {
      passwordOpen.value = true;
    },
  },
  {
    label: t("account.signOut"),
    icon: "log-out",
    // 退出走站点自己的登出端点：它会把会话结束并送回统一登录口。
    // （"退出所有设备"是另一个能力，等做会话列表时再放出来，不在这里假装有。）
    onClick: () => {
      window.location.href = "/api/method/logout?redirect-to=/";
    },
  },
]);

onMounted(() => {
  // 文案取不到不该把整页挡住：`t()` 取不到会显示键名 —— 看得见、可排查。
  loadTexts().catch(() => {});
});
</script>

<template>
  <Dropdown :options="options" placement="right" side="top">
    <template #trigger>
      <button type="button" class="pn-account" :title="props.boot.user">
        <span class="pn-avatar">{{ initials(props.boot.full_name || props.boot.user) }}</span>
        <span class="pn-who">
          <strong>{{ props.boot.full_name || props.boot.user }}</strong>
          <span>{{ props.boot.user }}</span>
        </span>
      </button>
    </template>
  </Dropdown>

  <ChangePasswordDialog v-model:open="passwordOpen" />
</template>

<style scoped>
.pn-account {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 6px 4px;
  min-width: 0;
  border: 0;
  border-radius: var(--radius);
  background: transparent;
  cursor: pointer;
  text-align: left;
  font: inherit;
}

.pn-account:hover {
  background: var(--line2);
}
</style>
