<script setup lang="ts">
// 开单回执差异卡的「แก้ไข」（LINE 内，票据模式 `review`）：只列有差异的那几项。
//
// 每项二选一：按确认时的值改回 DMS，或以 DMS 现值为准；选「确认时的值」时，文字栏（姓名、电话、
// 门牌……）还可以手改。保存后由后台把选定的值写进**同一张**订车单并逐项核对，结果发回 LINE
// （`booking/conversation/review_fix.py`）。不开新单、不改客户主档。
import { onMounted, reactive, ref } from "vue";
import { useRoute } from "vue-router";
import { Button, ErrorMessage, FormControl, Spinner } from "frappe-ui";

import LineLayout from "../../components/LineLayout.vue";
import { ApiError } from "../../lib/api";
import {
  ensureTicket,
  forgetTicket,
  loadConfig,
  loadReview,
  saveReview,
  type ReviewChoice,
  type ReviewItem,
} from "../../lib/dms-editor";
import { adoptAccountLocale, createTexts, type Locale } from "../../lib/i18n";
import { loadLiffSdk } from "../../lib/liff";

const { t, locale, load: loadTexts, setLocale } = createTexts([], "dms.");
const route = useRoute();
const draft = String(route.query.draft || "");

const stage = ref<"loading" | "ready" | "saved" | "failed">("loading");
const busy = ref(false);
const errorText = ref("");
const bookingNo = ref("");
const items = ref<ReviewItem[]>([]);
const choices = reactive<Record<string, ReviewChoice>>({});
let ticket = "";

function explain(err: unknown): string {
  if (err instanceof ApiError && (err.status === 409 || err.message === "dms_booking.expired"))
    return t("dms.edit.reviewExpired");
  if (err instanceof ApiError && err.status === 401) return t("dms.edit.liffUnavailable");
  return t("dms.edit.failed");
}

function bookingLine(): string {
  return t("dms.card.TXT_REVIEW_BOOKING_NO").replace("{booking_no}", bookingNo.value);
}

async function closePage(): Promise<void> {
  const liff = (window as unknown as { liff?: Record<string, () => unknown> }).liff;
  if (liff && typeof liff.isInClient === "function" && liff.isInClient()) {
    (liff.closeWindow as () => void)();
    return;
  }
  history.back();
}

async function save(): Promise<void> {
  busy.value = true;
  errorText.value = "";
  try {
    await saveReview(ticket, draft, choices);
    stage.value = "saved";
    setTimeout(() => void closePage(), 900);
  } catch (err) {
    errorText.value = explain(err);
  } finally {
    busy.value = false;
  }
}

onMounted(async () => {
  try {
    const fromLine = sessionStorage.getItem("pearnly_dms_lang");
    if (fromLine) adoptAccountLocale(fromLine);
  } catch {
    /* 隐私模式读不到会话存储：按原来的缓存 / 浏览器语言 */
  }
  document.documentElement.lang = locale.value;
  try {
    await loadTexts();
  } catch {
    /* 文案拉不到就显示 key（看得见），不假装页面正常 */
  }
  if (!draft) {
    errorText.value = t("dms.edit.reviewExpired");
    stage.value = "failed";
    return;
  }
  try {
    const config = await loadConfig();
    if (config.liff_id) void loadLiffSdk();
    ticket = await ensureTicket(draft, "review");
    let data;
    try {
      data = await loadReview(ticket, draft);
    } catch (err) {
      // 缓存里那张票据可能已过期（30 分钟）：清掉再换一张。
      if (!(err instanceof ApiError) || err.status !== 401) throw err;
      forgetTicket();
      ticket = await ensureTicket(draft, "review");
      data = await loadReview(ticket, draft);
    }
    bookingNo.value = data.booking_no;
    items.value = data.items;
    for (const item of data.items) {
      choices[item.key] = {
        use: "sent",
        inputs: Object.fromEntries(item.inputs.map((input) => [input.field, input.value])),
      };
    }
    stage.value = "ready";
  } catch (err) {
    errorText.value = explain(err);
    stage.value = "failed";
  }
});

function changeLocale(value: Locale): void {
  setLocale(value);
}
</script>

<template>
  <LineLayout :locale="locale" :disabled="busy" @update:locale="changeLocale">
    <section v-if="stage === 'loading'" class="pn-review-state">
      <Spinner class="h-6 w-6" />
      <p>{{ t("dms.edit.loading") }}</p>
    </section>

    <section v-else-if="stage === 'failed'" class="pn-review-state">
      <h1 class="pn-review-h1">{{ errorText }}</h1>
    </section>

    <section v-else-if="stage === 'saved'" class="pn-review-state">
      <h1 class="pn-review-h1">{{ t("dms.edit.reviewSaved") }}</h1>
      <Button variant="solid" :label="t('dms.edit.cancel')" @click="closePage" />
    </section>

    <form v-else @submit.prevent="save">
      <div class="pn-review-intro">
        <h1 class="pn-review-h1">{{ t("dms.edit.reviewTitle") }}</h1>
        <p class="pn-review-sub">{{ bookingLine() }}</p>
      </div>

      <section v-for="item in items" :key="item.key" class="pn-review-item">
        <h2 class="pn-review-h2">{{ t(item.label) }}</h2>
        <div class="pn-review-choice">
          <Button
            :variant="choices[item.key].use === 'sent' ? 'solid' : 'outline'"
            :aria-pressed="choices[item.key].use === 'sent'"
            :disabled="busy"
            @click="choices[item.key].use = 'sent'"
          >
            {{ t("dms.edit.reviewConfirmed") }}
          </Button>
          <Button
            :variant="choices[item.key].use === 'dms' ? 'solid' : 'outline'"
            :aria-pressed="choices[item.key].use === 'dms'"
            :disabled="busy"
            @click="choices[item.key].use = 'dms'"
          >
            {{ t("dms.edit.reviewInDms") }}
          </Button>
        </div>
        <p class="pn-review-value">
          {{ choices[item.key].use === "dms" ? item.dms || "—" : item.sent || "—" }}
        </p>
        <div v-if="choices[item.key].use === 'sent' && item.inputs.length" class="pn-review-grid">
          <FormControl
            v-for="input in item.inputs"
            :key="input.field"
            v-model="choices[item.key].inputs[input.field]"
            variant="outline"
            :label="t(input.label)"
            :disabled="busy"
          />
        </div>
      </section>

      <ErrorMessage v-if="errorText" :message="errorText" />
      <div class="pn-review-actions">
        <Button :label="t('dms.edit.cancel')" :disabled="busy" @click="closePage" />
        <Button type="submit" variant="solid" :label="t('dms.edit.save')" :loading="busy" />
      </div>
    </form>
  </LineLayout>
</template>

<style scoped>
/* 手机上的 LINE 页面：单列、贴底操作条；控件是官方组件，这里只管布局（与 EditorPage 同一套）。 */
.pn-review-state {
  display: grid;
  place-items: center;
  gap: 12px;
  padding: 48px 8px;
  text-align: center;
}

.pn-review-intro {
  margin-bottom: 14px;
}

.pn-review-h1 {
  font-size: 19px;
  font-weight: 700;
  margin: 0 0 6px;
}

.pn-review-sub {
  margin: 0;
  font-size: 13px;
  color: var(--ink-gray-6, #6b7280);
}

.pn-review-item {
  padding: 12px 0;
  border-bottom: 1px solid var(--outline-gray-2, #e5e7eb);
}

.pn-review-h2 {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 8px;
}

.pn-review-choice {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.pn-review-value {
  margin: 8px 0 0;
  font-size: 14px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.pn-review-grid {
  display: grid;
  gap: 10px;
  margin-top: 10px;
}

.pn-review-actions {
  position: sticky;
  bottom: 0;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 10px 0 16px;
  background: var(--bg);
}
</style>
