<script setup lang="ts">
import LineLayout from "../../components/LineLayout.vue";
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { Button, FormControl, Spinner, ErrorMessage } from "frappe-ui";
import { ApiError, call, setCsrfToken } from "../../lib/api";
import { adoptAccountLocale, createTexts } from "../../lib/i18n";

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
const api = "pearnly_dms.account.api.";

onMounted(async () => {
  try {
    adoptAccountLocale(sessionStorage.getItem("pearnly_dms_lang") || "th");
    await load();
    const config = await call<{ csrf_token: string }>("pearnly_dms.editor.browser_api.browser_config");
    setCsrfToken(config.csrf_token);
    ticket = sessionStorage.getItem("pearnly_dms_account_ticket") || "";
    if (!ticket) throw new Error("dms_booking.line_auth_required");
    const result = await call<{ username: string }>(api + "credentials", { ticket }, { method: "POST" });
    username.value = result.username;
    ready.value = true;
  } catch (e) {
    error.value = t("dms.account.expired");
  }
});

async function submit() {
  if (busy.value) return;
  busy.value = true; error.value = ""; saved.value = false;
  try {
    if (portal) {
      const result = await call<{ url: string }>(api + "portal_ticket", { ticket, language: locale.value }, { method: "POST" });
      window.location.assign(result.url);
    } else {
      await call(api + "save_credentials", { ticket, username: username.value, password: password.value }, { method: "POST" });
      password.value = "";
      saved.value = true;
    }
  } catch (e) {
    const key = e instanceof Error ? e.message : "";
    if (e instanceof ApiError && [401, 403].includes(e.status)) {
      ready.value = false;
      error.value = t("dms.account.expired");
    } else {
      error.value = t(key === "dms.account.invalid" ? key : "dms.account.failed");
    }
  } finally { busy.value = false; }
}
</script>

<template>
  <LineLayout :locale="locale" :disabled="busy" @update:locale="setLocale">
    <section class="space-y-5 rounded-xl border border-outline-gray-2 bg-surface-white p-4">
      <h1 class="text-xl font-semibold">{{ t(portal ? "dms.account.portal" : "dms.account.title") }}</h1>
      <p class="text-sm text-gray-600">{{ t(portal ? "dms.account.portalHint" : "dms.account.hint") }}</p>
      <ErrorMessage v-if="error" :message="error" />
      <Spinner v-if="!ready && !error" class="h-5 w-5" />
      <form v-if="ready" class="space-y-5" @submit.prevent="submit">
        <FormControl v-model="username" :label="t('dms.account.username')" :disabled="portal || busy" autocomplete="username" required />
        <FormControl v-if="!portal" v-model="password" type="password" :label="t('dms.account.password')" :disabled="busy" autocomplete="off" required />
        <p v-if="saved" role="status" class="text-sm text-green-700">{{ t('dms.account.saved') }}</p>
        <Button variant="solid" theme="gray" type="submit" :loading="busy" :disabled="!portal && (!username.trim() || !password)">
          {{ t(portal ? 'dms.account.open' : 'dms.account.save') }}
        </Button>
      </form>
    </section>
  </LineLayout>
</template>
