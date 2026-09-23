<script setup lang="ts">
import LineLayout from "../../components/LineLayout.vue";
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { Button, FormControl, Spinner, ErrorMessage } from "frappe-ui";
import { ApiError, call, setCsrfToken } from "../../lib/api";
import { adoptAccountLocale, createTexts } from "../../lib/i18n";
import { initLiff, openExternal } from "../../lib/liff";

const route = useRoute();
const portal = route.query.action === "portal";
const { t, load, locale, setLocale } = createTexts([], "dms.account.");
const username = ref("");
const password = ref("");
const ready = ref(false);
const busy = ref(false);
const error = ref("");
const saved = ref(false);
let ticket = "";
let liffId = "";
const api = "pearnly_dms.account.api.";

onMounted(async () => {
  try {
    adoptAccountLocale(sessionStorage.getItem("pearnly_dms_lang") || "th");
    await load();
    const config = await call<{ csrf_token: string; liff_id?: string }>("pearnly_dms.editor.browser_api.browser_config");
    setCsrfToken(config.csrf_token);
    liffId = config.liff_id || "";
    ticket = sessionStorage.getItem("pearnly_dms_account_ticket") || "";
    if (!ticket) throw new Error("dms_booking.line_auth_required");
    if (portal) {
      // 菜单3「เข้าสู่ระบบ DMS」：照老产品**自动**换取门户链接并直接跳转。
      // 老页面是 LIFF 侧自动 `openWindow` + 中转页一个按钮；这里多留一个按钮
      // 就变成"点两次才打开 DMS"，销售会以为坏了（2026-09-21 回归修复）。
      await openPortal();
      return;
    }
    const result = await call<{ username: string }>(api + "credentials", { ticket }, { method: "POST" });
    username.value = result.username;
    ready.value = true;
  } catch (e) {
    error.value = t("dms.account.expired");
  }
});

async function openPortal(): Promise<void> {
  busy.value = true;
  error.value = "";
  try {
    // **先在 LINE 内把身份验完、换成票，再"带票出站"**（对齐老产品 portalMode）：
    // 系统浏览器里没有 LIFF、没有会话，只有这张 60 秒的一次性票。
    const liff = await initLiff(liffId);
    const result = await call<{ url: string }>(api + "portal_ticket", { ticket, language: locale.value }, { method: "POST" });
    openExternal(liff, result.url);
  } catch (e) {
    const key = e instanceof ApiError && [401, 403].includes(e.status) ? "dms.account.expired" : "dms.account.failed";
    error.value = t(key);
    busy.value = false;
  }
}

async function submit() {
  if (busy.value) return;
  busy.value = true; error.value = ""; saved.value = false;
  try {
    // 只提交密码：用户名由后台发号、绑死这个租户，服务端不再接受改动。
    await call(api + "save_credentials", { ticket, password: password.value }, { method: "POST" });
    password.value = "";
    saved.value = true;
  } catch (e) {
    const key = e instanceof Error ? e.message : "";
    if (e instanceof ApiError && [401, 403].includes(e.status)) {
      ready.value = false;
      error.value = t("dms.account.expired");
    } else {
      error.value = t(["dms.account.invalid", "dms.account.usernameFixed"].includes(key)
        ? key : "dms.account.failed");
    }
  } finally { busy.value = false; }
}
</script>

<template>
  <LineLayout :locale="locale" :disabled="busy" @update:locale="setLocale">
    <section class="space-y-5 rounded border border-outline-gray-2 bg-surface-white p-4">
      <h1 class="text-xl font-semibold">{{ t(portal ? "dms.account.portal" : "dms.account.title") }}</h1>
      <p class="text-sm text-ink-gray-9">{{ t(portal ? "dms.account.portalHint" : "dms.account.hint") }}</p>
      <ErrorMessage v-if="error" :message="error" />
      <Spinner v-if="!error && (portal || !ready)" class="h-5 w-5" />
      <form v-if="ready && !portal" class="space-y-5" @submit.prevent="submit">
        <!-- 用户名（DMS 登录名）由后台发号、绑死这个租户，只给看不给改；能改的只有密码
             （用户 2026-09-23："可以用用户名登录，但是不能修改…用户只可以自己改密码"）。 -->
        <FormControl variant="outline" v-model="username" :label="t('dms.account.username')" :disabled="true" autocomplete="username" />
        <FormControl variant="outline" v-model="password" type="password" :label="t('dms.account.password')" :disabled="busy" autocomplete="off" required />
        <p v-if="saved" role="status" class="text-sm text-ink-gray-9">{{ t('dms.account.saved') }}</p>
        <Button variant="solid" theme="gray" class="pn-primary" type="submit" :loading="busy" :disabled="!username.trim() || !password">
          {{ t('dms.account.save') }}
        </Button>
      </form>
    </section>
  </LineLayout>
</template>
