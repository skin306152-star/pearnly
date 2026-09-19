<script setup lang="ts">
// DMS 草稿编辑器 —— **LINE 内那一页**（老产品 `static/dms-booking-edit/` 的对应物）。
//
// 一个页面两种模式，靠 query 分（与老产品一致）：
//   · `#/dms/editor?draft=<nonce>`                → 订车草稿（客户 + 订车 + 付款 + 附件）
//   · `#/dms/editor?draft=<nonce>&editor=customer`→ 客户档（只有客户资料）
//
// 三条不能改的行为（都来自老产品，见 `docs/p4/40-edit-link.md` 一带的记录）：
//   1. **保存的语义是"卡片送达"**：服务端要拿到 LINE 回执才算成功。失败时服务端会把草稿
//      回滚到保存前，页面只要如实报错、让用户重试。
//   2. **换车型要重取颜色**：颜色属于车型，拿旧车型的颜色提交会被服务端挡下。
//   3. **取消 = 回 LINE**：LINE 内 `liff.closeWindow()`，普通浏览器里退一步。
//
// 界面：外壳与控件用官方 Frappe UI，配色取 Pearnly 令牌（ADR-007）；
// 文案全部来自服务端语言包（`dms.edit.*`），代码里只出现文案编号。
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute } from "vue-router";
import { Button, FormControl, Spinner, ErrorMessage } from "frappe-ui";

import { ApiError } from "../../lib/api";
import {
  CHANNELS,
  CHANNEL_BANK,
  CHANNEL_FIELDS,
  MAX_PAYMENTS,
  ensureTicket,
  forgetTicket,
  loadConfig,
  loadDraft,
  loadGeo,
  loadPaints,
  saveDraft,
  type Draft,
  type EditorMode,
  type Option,
  type Payment,
} from "../../lib/dms-editor";
import {
  LOCALES,
  LOCALE_LABELS,
  createTexts,
  type Locale,
} from "../../lib/i18n";

const route = useRoute();
const mode = computed<EditorMode>(() =>
  String(route.query.editor || "") === "customer" ? "customer" : "booking",
);
const nonce = ref(String(route.query.draft || ""));

// keys 传空 + 给前缀 → 服务端把 `dms.edit.*` 整包给回来（四语齐全）。
// 页面不维护"我要用哪些键"的清单：清单与语言包一旦不同步，页面上就会露出 key。
const { t, locale, load: loadTexts, setLocale } = createTexts([], "dms.edit.");

const stage = ref<"loading" | "ready" | "saved" | "failed">("loading");
const errorText = ref("");
const busy = ref(false);

const ticket = ref("");
const masters = reactive<Record<string, Option[]>>({});
const geo = reactive<Record<string, Option[]>>({
  provinces: [],
  districts: [],
  subdistricts: [],
  zipcodes: [],
});
const paints = ref<Option[]>([]);
const manualBanks = ref<string[]>([]);
const advisor = ref("");
const hasFiles = reactive({ id_card: false, slip: false });

// 表单模型：字段名与老产品提交的 payload 一字不差（服务端就按这些名字读）。
const customer = reactive<Record<string, string>>({});
const answers = reactive<Record<string, string>>({
  place_id: "",
  car_id: "",
  paint_id: "",
  delivery_date_be: "",
  term_id: "",
  regis_id: "",
  regis_name: "",
});
const payments = ref<Payment[]>([]);
const keep = reactive({ id_card: true, slip: true });

const CUSTOMER_TEXT_FIELDS = [
  "name",
  "people_id",
  "birthday_be",
  "phone",
  "house_no",
  "building",
  "floor",
  "room",
  "village",
  "moo",
  "soi",
  "road",
] as const;

/** 错误码 → 文案编号。照老产品 `DMS_BOOKING_ERROR_KEYS` 一字不差地搬。 */
const ERROR_KEYS: Record<string, string> = {
  "dms_booking.invalid_master": "masterChanged",
  "dms_booking.invalid_bank": "masterChanged",
  "dms_booking.master_unavailable": "masterUnavailable",
  "dms_booking.invalid_zipcode_id": "postcodeMissing",
  "dms_booking.geo_unavailable": "geoUnavailable",
  "dms_booking.preview_send_failed": "previewSendFailed",
  "dms_booking.slip_without_transfer": "slipConflict",
  "dms_booking.payment_detail_required": "paymentDetailRequired",
  "dms_booking.duplicate_payment": "duplicatePayment",
  "dms_booking.liff_unavailable": "liffUnavailable",
  // 下面几条老产品没有单列（当时会落到通用"保存失败"），但服务端**确实**会回这些码，
  // 而且它们各有对策（重进页面 / 重填某一格），所以补上对应话术，别让人干瞪眼。
  "dms_booking.expired": "expired",
  "dms_booking.not_bound": "liffUnavailable",
  "dms_booking.no_endpoint": "masterUnavailable",
};

const optionList = (rows: Option[] | undefined) =>
  (rows || []).map((row) => ({ label: row.label, value: row.id }));

const total = computed(() =>
  payments.value.reduce(
    (sum, row) => sum + (Number(String(row.amount).replace(/,/g, "")) || 0),
    0,
  ),
);
const totalText = computed(() =>
  total.value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }),
);

function explain(err: unknown): string {
  const code = err instanceof ApiError ? err.message : "";
  const key = ERROR_KEYS[code];
  if (key) return t(`dms.edit.${key}`);
  if (err instanceof ApiError && err.status === 409)
    return t("dms.edit.expired");
  return t("dms.edit.failed");
}

function fail(err: unknown): void {
  errorText.value = explain(err);
  stage.value = "failed";
}

/** LINE 内的"关掉这一页"：SDK 只在真的配了 LIFF 时才加载，本机不会去拉它。 */
let liffPromise: Promise<void> | null = null;
function loadLiff(): Promise<void> {
  if (liffPromise) return liffPromise;
  liffPromise = new Promise<void>((resolve) => {
    const script = document.createElement("script");
    script.src = "https://static.line-scdn.net/liff/edge/2/sdk.js";
    script.onload = () => resolve();
    script.onerror = () => resolve();
    document.head.appendChild(script);
  });
  return liffPromise;
}

async function closePage(): Promise<void> {
  const liff = (window as unknown as { liff?: Record<string, () => unknown> })
    .liff;
  if (liff && typeof liff.isInClient === "function" && liff.isInClient()) {
    (liff.closeWindow as () => void)();
    return;
  }
  history.back();
}

/** 首屏：把服务端给的草稿铺进表单模型。 */
function adopt(draft: Draft): void {
  Object.keys(draft.form.customer || {}).forEach((key) => {
    customer[key] = String(draft.form.customer[key] ?? "");
  });
  CUSTOMER_TEXT_FIELDS.forEach((field) => {
    if (!(field in customer)) customer[field] = "";
  });

  const a = (draft.form.answers || {}) as Record<
    string,
    { id?: string } | string
  >;
  const ref = (key: string) => {
    const value = a[key];
    return value && typeof value === "object" ? String(value.id || "") : "";
  };
  answers.place_id = ref("place");
  answers.car_id = ref("car");
  answers.paint_id = ref("paint");
  answers.term_id = ref("term");
  answers.regis_id = ref("regis");
  answers.delivery_date_be = String(a.delivery_date_be || "");
  answers.regis_name = String(a.regis_name || "");

  payments.value = (draft.form.payments || []).map((row) => ({
    channel: row.channel,
    amount: String(row.amount || ""),
    extra: { ...(row.extra || {}) },
  }));
  keep.id_card = Boolean(draft.form.files?.id_card);
  keep.slip = Boolean(draft.form.files?.slip);
  hasFiles.id_card = Boolean(draft.form.files?.id_card);
  hasFiles.slip = Boolean(draft.form.files?.slip);
  advisor.value = String(draft.form.advisor?.name || "");
  manualBanks.value = draft.manual_banks || [];

  Object.keys(draft.masters || {}).forEach((key) => {
    masters[key] = draft.masters[key] || [];
  });
  paints.value = draft.masters?.paints || [];
  Object.keys(geo).forEach((level) => {
    geo[level] = draft.geo?.[level] || [];
  });
}

/**
 * 四级地址：首屏先用服务端**同一次登录**带回来的那份，缺哪级才单独去拉。
 * 老产品就是这么做的（移动端不该为一个地址登四次 DMS）。
 */
async function hydrateGeo(): Promise<void> {
  const levels: Array<[string, string, string]> = [
    ["districts", "province_id", "districtId"],
    ["subdistricts", "district_id", "subdistrictId"],
    ["zipcodes", "subdistrict_id", "zipcodeId"],
  ];
  for (const [level, parentField, current] of levels) {
    const parent = customer[parentField] || "";
    if (geo[level]?.length) {
      if (customer[current]) customer[current] = String(customer[current]);
      continue;
    }
    if (!parent) {
      geo[level] = [];
      continue;
    }
    geo[level] = await loadGeo(ticket.value, nonce.value, level, parent);
  }
}

/** 取下一级选项；父级为空就是空表（与老产品一致，不去请求）。 */
async function cascade(
  level: string,
  parentId: string,
  targetField: string,
): Promise<void> {
  geo[level] = [];
  customer[targetField] = "";
  if (!parentId) return;
  try {
    geo[level] = await loadGeo(ticket.value, nonce.value, level, parentId);
  } catch (err) {
    errorText.value = explain(err);
  }
}

async function onProvinceChange(): Promise<void> {
  customer.district_id = "";
  customer.subdistrict_id = "";
  customer.zipcode_id = "";
  geo.districts = [];
  geo.subdistricts = [];
  geo.zipcodes = [];
  await cascade("districts", customer.province_id, "district_id");
}

async function onDistrictChange(): Promise<void> {
  customer.subdistrict_id = "";
  customer.zipcode_id = "";
  geo.subdistricts = [];
  geo.zipcodes = [];
  await cascade("subdistricts", customer.district_id, "subdistrict_id");
}

async function onSubdistrictChange(): Promise<void> {
  customer.zipcode_id = "";
  geo.zipcodes = [];
  await cascade("zipcodes", customer.subdistrict_id, "zipcode_id");
}

/** 换车型 → 颜色必须重取（颜色属于车型，留着旧颜色提交必被服务端挡）。 */
async function onCarChange(): Promise<void> {
  answers.paint_id = "";
  paints.value = [];
  if (!answers.car_id) return;
  try {
    paints.value = await loadPaints(ticket.value, nonce.value, answers.car_id);
  } catch (err) {
    errorText.value = explain(err);
  }
}

// ── 付款 ─────────────────────────────────────────────────────────────────
function usedChannels(): string[] {
  return payments.value.map((row) => row.channel);
}

function freeChannel(): string {
  const used = usedChannels();
  return CHANNELS.find((channel) => !used.includes(channel)) || "";
}

function addPayment(): void {
  const channel = freeChannel();
  if (!channel) return;
  payments.value.push({ channel, amount: "", extra: {} });
}

function removePayment(index: number): void {
  payments.value.splice(index, 1);
}

function bankList(channel: string): Option[] {
  const key = CHANNEL_BANK[channel];
  return key ? masters[key] || [] : [];
}

function bankIsManual(channel: string): boolean {
  const key = CHANNEL_BANK[channel];
  return Boolean(key) && manualBanks.value.includes(key);
}

// ── 提交 ─────────────────────────────────────────────────────────────────
function labelOf(rows: Option[], id: string): string {
  const hit = (rows || []).find((row) => String(row.id) === String(id));
  return hit ? hit.label : "";
}

function payload(): Record<string, unknown> {
  const out: Record<string, string> = {};
  Object.keys(customer).forEach((key) => {
    out[key] = String(customer[key] ?? "").trim();
  });
  // 派生的展示名照老产品在客户端拼（服务端拿到后会用权威主档**重新核一遍**）。
  out.province_name = labelOf(geo.provinces, customer.province_id);
  out.district_name = labelOf(geo.districts, customer.district_id);
  out.subdistrict_name = labelOf(geo.subdistricts, customer.subdistrict_id);
  out.zipcode = labelOf(geo.zipcodes, customer.zipcode_id);
  if (mode.value === "customer") return { customer: out };
  return {
    customer: out,
    answers: { ...answers },
    payments: payments.value,
    keep_files: { id_card: keep.id_card, slip: keep.slip },
  };
}

async function save(): Promise<void> {
  busy.value = true;
  errorText.value = "";
  try {
    const result = await saveDraft(ticket.value, nonce.value, payload());
    // 服务端换了新 nonce（旧卡作废）。页面留着它，用户再点一次保存也能成。
    nonce.value = result.nonce;
    stage.value = "saved";
    setTimeout(() => void closePage(), 900);
  } catch (err) {
    fail(err);
  } finally {
    busy.value = false;
  }
}

onMounted(async () => {
  document.documentElement.lang = locale.value;
  try {
    await loadTexts();
  } catch {
    /* 文案拉不到就显示 key（看得见），不假装页面正常 */
  }
  if (!nonce.value) {
    errorText.value = t("dms.edit.expired");
    stage.value = "failed";
    return;
  }
  try {
    const config = await loadConfig();
    if (config.liff_id) void loadLiff();
    ticket.value = await ensureTicket(nonce.value, mode.value);
    let draft: Draft;
    try {
      draft = await loadDraft(ticket.value, nonce.value);
    } catch (err) {
      // 缓存里那张票据可能已经过期（30 分钟）或早就被换掉了。**清掉再来一次** ——
      // 不清的话会一直拿同一张废票重试，页面上只剩一句看不懂的失败。
      if (!(err instanceof ApiError) || err.status !== 401) throw err;
      forgetTicket();
      ticket.value = await ensureTicket(nonce.value, mode.value);
      draft = await loadDraft(ticket.value, nonce.value);
    }
    adopt(draft);
    await hydrateGeo();
    stage.value = "ready";
  } catch (err) {
    fail(err);
  }
});

function changeLocale(value: Locale): void {
  setLocale(value);
}
</script>

<template>
  <div class="pn-editor">
    <header class="pn-editor-bar">
      <span class="pn-editor-brand">
        <span class="pn-editor-mark">P</span>
        <span>Pearnly DMS</span>
      </span>
      <FormControl
        type="select"
        class="w-28"
        :model-value="locale"
        :aria-label="t('dms.edit.title')"
        @update:model-value="changeLocale"
        :options="[
          ...LOCALES.map((code) => ({
            value: code,
            label: LOCALE_LABELS[code],
          })),
        ]"
      />
    </header>

    <main class="pn-editor-body">
      <section v-if="stage === 'loading'" class="pn-editor-state">
        <Spinner class="h-6 w-6" />
        <p>{{ t("dms.edit.loading") }}</p>
      </section>

      <section v-else-if="stage === 'failed'" class="pn-editor-state">
        <h1 class="pn-editor-h1">{{ errorText }}</h1>
      </section>

      <section v-else-if="stage === 'saved'" class="pn-editor-state">
        <h1 class="pn-editor-h1">{{ t("dms.edit.saved") }}</h1>
        <Button
          variant="solid"
          class="pn-primary"
          :label="t('dms.edit.cancel')"
          @click="closePage"
        />
      </section>

      <form v-else class="pn-editor-form" @submit.prevent="save">
        <div class="pn-editor-intro">
          <h1 class="pn-editor-h1">
            {{
              t(
                mode === "customer"
                  ? "dms.edit.customerTitle"
                  : "dms.edit.title",
              )
            }}
          </h1>
          <p class="pn-editor-sub">
            {{
              t(mode === "customer" ? "dms.edit.customerSub" : "dms.edit.sub")
            }}
          </p>
        </div>

        <section class="pn-editor-section">
          <h2 class="pn-editor-h2">{{ t("dms.edit.customer") }}</h2>
          <div class="pn-editor-grid">
            <FormControl
              type="select"
              :label="t('dms.edit.prefix')"
              required
              :options="optionList(masters.prefixes)"
              v-model="customer.prefix_id"
            />
            <FormControl
              type="text"
              :label="t('dms.edit.name')"
              required
              v-model="customer.name"
            />
            <FormControl
              type="text"
              :label="t('dms.edit.confirmId')"
              required
              v-model="customer.people_id"
            />
            <FormControl
              type="text"
              :label="t('dms.edit.birth')"
              required
              v-model="customer.birthday_be"
            />
            <FormControl
              type="text"
              :label="t('dms.edit.phone')"
              required
              v-model="customer.phone"
            />
            <FormControl
              type="text"
              :label="t('dms.edit.house')"
              required
              v-model="customer.house_no"
            />
            <FormControl
              type="text"
              :label="t('dms.edit.building')"
              v-model="customer.building"
            />
            <FormControl
              type="text"
              :label="t('dms.edit.floor')"
              v-model="customer.floor"
            />
            <FormControl
              type="text"
              :label="t('dms.edit.room')"
              v-model="customer.room"
            />
            <FormControl
              type="text"
              :label="t('dms.edit.village')"
              v-model="customer.village"
            />
            <FormControl
              type="text"
              :label="t('dms.edit.moo')"
              v-model="customer.moo"
            />
            <FormControl
              type="text"
              :label="t('dms.edit.soi')"
              v-model="customer.soi"
            />
            <FormControl
              type="text"
              :label="t('dms.edit.road')"
              v-model="customer.road"
            />
            <FormControl
              type="select"
              :label="t('dms.edit.province')"
              required
              :options="optionList(geo.provinces)"
              v-model="customer.province_id"
              @update:model-value="onProvinceChange"
            />
            <FormControl
              type="select"
              :label="t('dms.edit.district')"
              required
              :options="optionList(geo.districts)"
              v-model="customer.district_id"
              @update:model-value="onDistrictChange"
            />
            <FormControl
              type="select"
              :label="t('dms.edit.subdistrict')"
              required
              :options="optionList(geo.subdistricts)"
              v-model="customer.subdistrict_id"
              @update:model-value="onSubdistrictChange"
            />
            <FormControl
              type="select"
              :label="t('dms.edit.postcode')"
              required
              :options="optionList(geo.zipcodes)"
              v-model="customer.zipcode_id"
            />
          </div>
          <p class="pn-editor-hint">{{ t("dms.edit.idWarn") }}</p>
        </section>

        <template v-if="mode === 'booking'">
          <section class="pn-editor-section">
            <h2 class="pn-editor-h2">{{ t("dms.edit.booking") }}</h2>
            <div class="pn-editor-grid">
              <FormControl
                type="text"
                :label="t('dms.edit.advisor')"
                :model-value="advisor"
                disabled
              />
              <FormControl
                type="select"
                :label="t('dms.edit.place')"
                required
                :options="optionList(masters.places)"
                v-model="answers.place_id"
              />
              <FormControl
                type="select"
                :label="t('dms.edit.car')"
                required
                :options="optionList(masters.cars)"
                v-model="answers.car_id"
                @update:model-value="onCarChange"
              />
              <FormControl
                type="select"
                :label="t('dms.edit.paint')"
                required
                :options="optionList(paints)"
                v-model="answers.paint_id"
              />
              <FormControl
                type="text"
                :label="t('dms.edit.delivery')"
                required
                v-model="answers.delivery_date_be"
              />
              <FormControl
                type="select"
                :label="t('dms.edit.term')"
                required
                :options="optionList(masters.terms)"
                v-model="answers.term_id"
              />
              <FormControl
                type="select"
                :label="t('dms.edit.regis')"
                required
                :options="optionList(masters.regis)"
                v-model="answers.regis_id"
              />
              <FormControl
                type="text"
                :label="t('dms.edit.regisName')"
                required
                v-model="answers.regis_name"
              />
            </div>
          </section>

          <section class="pn-editor-section">
            <h2 class="pn-editor-h2">{{ t("dms.edit.payment") }}</h2>
            <div
              v-for="(row, index) in payments"
              :key="index"
              class="pn-editor-payment"
            >
              <FormControl
                type="select"
                :label="t('dms.edit.channel')"
                :options="
                  optionList(
                    CHANNELS.map((channel) => ({
                      id: channel,
                      label: t(`dms.edit.${channel}`),
                    })),
                  )
                "
                v-model="row.channel"
              />
              <FormControl
                type="text"
                :label="t('dms.edit.amount')"
                required
                v-model="row.amount"
              />
              <FormControl
                v-if="row.channel === 'transfer'"
                type="select"
                :label="t('dms.edit.destination')"
                required
                :options="optionList(masters.company_banks)"
                v-model="row.extra.dst_id"
              />
              <template v-if="CHANNEL_BANK[row.channel]">
                <FormControl
                  v-if="bankIsManual(row.channel)"
                  type="text"
                  :label="t('dms.edit.bankName')"
                  required
                  v-model="row.extra.bank_name"
                />
                <FormControl
                  v-else
                  type="select"
                  :label="t('dms.edit.bankName')"
                  required
                  :options="optionList(bankList(row.channel))"
                  v-model="row.extra.bank_id"
                />
              </template>
              <FormControl
                v-for="field in CHANNEL_FIELDS[row.channel] || []"
                :key="field"
                type="text"
                :label="t(`dms.edit.${field}`)"
                required
                v-model="row.extra[field]"
              />
              <Button
                variant="ghost"
                :label="t('dms.edit.remove')"
                @click="removePayment(index)"
              />
            </div>
            <Button
              variant="outline"
              theme="gray"
              :label="t('dms.edit.addPayment')"
              :disabled="payments.length >= MAX_PAYMENTS || !freeChannel()"
              @click="addPayment"
            />
            <FormControl
              type="text"
              :label="t('dms.edit.total')"
              :model-value="totalText"
              disabled
            />
          </section>

          <section class="pn-editor-section">
            <h2 class="pn-editor-h2">{{ t("dms.edit.files") }}</h2>
            <p class="pn-editor-hint">{{ t("dms.edit.fileHint") }}</p>
            <FormControl
              type="checkbox"
              :label="`${t('dms.edit.idCard')} · ${t('dms.edit.attached')}`"
              :disabled="!hasFiles.id_card"
              v-model="keep.id_card"
            />
            <FormControl
              type="checkbox"
              :label="`${t('dms.edit.slip')} · ${t('dms.edit.attached')}`"
              :disabled="!hasFiles.slip"
              v-model="keep.slip"
            />
          </section>
        </template>

        <ErrorMessage v-if="errorText" :message="errorText" />

        <div class="pn-editor-actions">
          <Button
            variant="outline"
            theme="gray"
            :label="t('dms.edit.cancel')"
            @click="closePage"
          />
          <Button
            type="submit"
            variant="solid"
            class="pn-primary"
            :label="t('dms.edit.save')"
            :loading="busy"
          />
        </div>
      </form>
    </main>
  </div>
</template>

<style scoped>
/* 这一页是**手机上的 LINE 页面**，不是产品后台：单列、贴底操作条、按令牌配色。
   控件本身还是官方组件（间距圆角由它们自己带），这里只管布局。 */
.pn-editor {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  background: var(--bg);
  color: var(--ink);
}

.pn-editor-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 14px;
  background: var(--card);
  border-bottom: 1px solid var(--outline-gray-2, #e5e7eb);
  position: sticky;
  top: 0;
  z-index: 5;
}

.pn-editor-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.pn-editor-mark {
  width: 24px;
  height: 24px;
  border-radius: 8px;
  background: var(--accent);
  color: var(--accent-ink);
  display: grid;
  place-items: center;
  font-size: 13px;
}

.pn-editor-lang {
  border: 1px solid var(--outline-gray-2, #e5e7eb);
  border-radius: 8px;
  padding: 4px 8px;
  background: var(--card);
  color: inherit;
  font-size: 14px;
}

.pn-editor-body {
  flex: 1;
  padding: 14px;
  max-width: 720px;
  width: 100%;
  margin: 0 auto;
}

.pn-editor-state {
  display: grid;
  place-items: center;
  gap: 12px;
  padding: 48px 8px;
  text-align: center;
}

.pn-editor-intro {
  margin-bottom: 14px;
}

.pn-editor-h1 {
  font-size: 19px;
  font-weight: 700;
  margin: 0 0 6px;
}

.pn-editor-sub {
  margin: 0;
  font-size: 13px;
  color: var(--ink-gray-6, #6b7280);
  line-height: 1.5;
}

.pn-editor-section {
  background: var(--card);
  border: 1px solid var(--outline-gray-2, #e5e7eb);
  border-radius: 14px;
  padding: 14px;
  margin-bottom: 14px;
}

.pn-editor-h2 {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 10px;
}

.pn-editor-grid {
  display: grid;
  gap: 10px;
}

@media (min-width: 560px) {
  .pn-editor-grid {
    grid-template-columns: 1fr 1fr;
  }
}

.pn-editor-payment {
  display: grid;
  gap: 8px;
  padding: 10px 0;
  border-bottom: 1px dashed var(--outline-gray-2, #e5e7eb);
}

.pn-editor-hint {
  margin: 8px 0;
  font-size: 12px;
  color: var(--ink-gray-6, #6b7280);
  line-height: 1.5;
}

.pn-editor-error {
  margin: 0 0 12px;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--surface-red-1, #fef2f2);
  color: var(--ink-red-6, #b3261e);
  font-size: 14px;
}

/* 操作条贴底：手机上单手够得着（老产品也是 sticky）。 */
.pn-editor-actions {
  position: sticky;
  bottom: 0;
  display: flex;
  gap: 10px;
  padding: 10px 0 16px;
  background: linear-gradient(to top, var(--bg) 70%, transparent);
}

.pn-editor-actions > * {
  flex: 1;
}
</style>
