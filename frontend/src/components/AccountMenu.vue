<script setup lang="ts">
// 产品内左下角的账号菜单。
//
// 用户当时的要求："改密放在登录后产品内，左下角或者右下角点开头像改，不是登录页"。
// 三个产品共用同一套（本仓前端本来就只有一份）。
import { computed, onMounted, ref } from "vue";
import { Button, Dialog, Dropdown, ErrorMessage } from "frappe-ui";
import type { ShellBoot } from "../lib/shell";
import {
  adoptAccountLocale,
  LOCALES,
  LOCALE_LABELS,
  localeRef,
  type Locale,
  createTexts,
} from "../lib/i18n";
import { call } from "../lib/api";
import ChangePasswordDialog from "./ChangePasswordDialog.vue";

// 菜单文案走语言包（键 = `account.*`）。
const { t, load: loadTexts } = createTexts([], "account.");

const props = defineProps<{ boot: ShellBoot }>();

const languageOpen = ref(false);
const languageSaving = ref(false);
const languageError = ref("");
async function changeLanguage(language: Locale) {
  if (languageSaving.value) return;
  languageSaving.value = true;
  languageError.value = "";
  try {
    await call(
      "pearnly_core.account.preferences.set_language",
      { language },
      { method: "POST" },
    );
    adoptAccountLocale(language);
    languageOpen.value = false;
    // Boot includes server-translated product text; reload it with the saved language.
    window.location.reload();
  } catch (error) {
    languageError.value =
      error instanceof Error ? error.message : String(error);
  } finally {
    languageSaving.value = false;
  }
}
const passwordOpen = ref(false);
const signOutOpen = ref(false);
const signingOut = ref(false);
const signOutError = ref("");

async function confirmSignOut() {
  if (signingOut.value) return;
  signingOut.value = true;
  signOutError.value = "";
  try {
    if (props.boot.native_account) {
      const result = await call<{ redirect_url: string }>(
        "pearnly_core.identity.sign_out",
        {},
        { method: "POST" },
      );
      window.location.assign(result.redirect_url);
    } else {
      await call("logout", {}, { method: "POST" });
      window.location.assign("/");
    }
  } catch (error) {
    signOutError.value = error instanceof Error ? error.message : String(error);
    signingOut.value = false;
  }
}

function initials(name: string): string {
  const trimmed = (name || "").trim();
  return trimmed ? trimmed.slice(0, 1).toUpperCase() : "?";
}

// **必须是 computed**：语言包是异步取回来的，在这里求值一次就会永久定格成键名。
const options = computed(() => [
  {
    label: t("account.language"),
    icon: "globe",
    onClick: () => {
      languageError.value = "";
      languageOpen.value = true;
    },
  },
  {
    label: t("account.changePassword"),
    icon: "key",
    onClick: () => {
      if (props.boot.native_account) {
        window.location.href =
          "/api/method/pearnly_core.identity.begin_password_change";
        return;
      }
      passwordOpen.value = true;
    },
  },
  {
    label: t("account.signOut"),
    icon: "log-out",
    // 退出走站点自己的登出端点：它会把会话结束并送回统一登录口。
    // （"退出所有设备"是另一个能力，等做会话列表时再放出来，不在这里假装有。）
    onClick: () => {
      signOutError.value = "";
      signOutOpen.value = true;
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
        <span class="pn-avatar">{{
          initials(props.boot.full_name || props.boot.user)
        }}</span>
        <span class="pn-who">
          <strong>{{ props.boot.full_name || props.boot.user }}</strong>
          <span>{{ props.boot.user }}</span>
        </span>
      </button>
    </template>
  </Dropdown>

  <Dialog
    v-model="languageOpen"
    :options="{ title: t('account.language'), size: 'sm' }"
  >
    <template #body-content>
      <div class="flex flex-col gap-2">
        <Button
          v-for="language in LOCALES"
          :key="language"
          :label="LOCALE_LABELS[language]"
          :variant="localeRef === language ? 'solid' : 'outline'"
          :disabled="languageSaving"
          @click="changeLanguage(language)"
        />
        <ErrorMessage v-if="languageError" :message="languageError" />
        <Button
          v-if="languageError && props.boot.native_account"
          :label="t('account.signInAgain')"
          @click="
            signOutOpen = true;
            languageOpen = false;
          "
        />
      </div>
    </template>
  </Dialog>
  <ChangePasswordDialog v-model:open="passwordOpen" />
  <Dialog
    v-model="signOutOpen"
    :options="{ title: t('account.signOut'), size: 'sm' }"
  >
    <template #body-content>
      <p>{{ t("account.signOutConfirm") }}</p>
      <ErrorMessage v-if="signOutError" :message="signOutError" />
      <div class="flex justify-end gap-2 mt-5">
        <Button
          :label="t('account.cancel')"
          :disabled="signingOut"
          @click="signOutOpen = false"
        />
        <Button
          variant="solid"
          :label="t('account.signOut')"
          :loading="signingOut"
          @click="confirmSignOut"
        />
      </div>
    </template>
  </Dialog>
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
