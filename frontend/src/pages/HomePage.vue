<script setup lang="ts">
// 首页 = 订阅与计费（三个产品共用这一个页面）。
//
// 照老产品搬（`src/home/subscription.ts` + `static/dms/dms-billing*.js` 那两套
// 各写各的实现合成了这一份）：
//   当前套餐 · 账户余额 · 选择套餐 · 记录明细 · 充值
//
// 数据全在控制面（钱只有一本账），这里经过本站后端中转：
// 浏览器 → 本站白名单方法 → 控制面（服务令牌）。**服务令牌绝不下发到浏览器。**
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
import {
  Card,
  FileUploader,
  ErrorMessage,
  Button,
  Dialog,
  TabButtons,
  Alert,
  LoadingIndicator,
  FormControl,
  ListView,
} from "frappe-ui";
import { call, ApiError } from "../lib/api";
import { createTexts } from "../lib/i18n";

// 页面文案走语言包（键 = `home.*`）：一次取整包，页面上不维护"我要用哪些键"的清单。
const { t, tf, load: loadTexts } = createTexts([], "home.");

interface Txn {
  name: string;
  type: string;
  amount_thb: number;
  balance_after: number;
  description: string;
  creation: string;
}

interface Plan {
  code: string;
  quota: number;
  fee: number;
  over_rate: number;
  cycle_days: number;
}

interface Subscription {
  plan_code: string;
  quota: number;
  pages_used_this_cycle: number;
  remaining: number;
  over_rate: number;
  monthly_fee: number;
  auto_renew: boolean;
  cycle_end: string;
}

interface Home {
  collection_account: {
    bank: string;
    branch: string;
    account_number: string;
    account_name: string;
  };
  balance_thb: number;
  pages_used_this_month: number;
  transactions: Txn[];
  subscription: Subscription | null;
  plans: Plan[];
  pending_topups: { name: string; requested_thb: number; creation: string }[];
  can_manage: boolean;
}

const home = ref<Home | null>(null);
const loading = ref(true);
const busy = ref("");
const error = ref("");
const notice = ref("");

const topupOpen = ref(false);
const topupForm = reactive({
  amount: 1000,
  note: "",
  payer_name: "",
  slip_file: "",
});
const bank = computed(() => home.value?.collection_account);
let poll: ReturnType<typeof setInterval> | undefined;
async function pollBalance(): Promise<void> {
  if (document.hidden || busy.value || topupOpen.value) return;
  try {
    home.value = await call<Home>("pearnly_core.billing_www.home");
    if (tab.value === "topup") await loadRows();
  } catch {
    /* The next explicit action reports connectivity failures. */
  }
}
function slipUploaded(file: { file_url: string }): void {
  topupForm.slip_file = file.file_url;
}

// 记录明细：三个页签 + 区间筛选（照老产品：扣费 / 充值 / 识别记录）
type TabKey = "usage" | "topup" | "ocr";
// ⚠️ 必须写成 computed：这几处标签原来是普通常量，`t()` 在 setup 时就求值了一次
// —— 那时文案还没取回来，于是页签和区间下拉**永远显示键名**（`home.ledger.usage`）。
// 界面上要变的文字，取值必须留在**渲染时**发生。
const TAB_LABEL = computed<Record<TabKey, string>>(() => ({
  usage: t("home.ledger.usage"),
  topup: t("home.topup"),
  ocr: t("home.ledger.ocr"),
}));
const PERIODS = computed(() => [
  { key: "all", label: t("home.period.all") },
  { key: "day", label: t("home.period.today") },
  { key: "month", label: t("home.period.month") },
  { key: "year", label: t("home.period.year") },
]);
const tab = ref<TabKey>("usage");
const period = ref("all");
const rows = ref<Record<string, unknown>[]>([]);
const rowsLoading = ref(false);
const exporting = ref(false);

const sub = computed(() => home.value?.subscription ?? null);
const canManage = computed(() => home.value?.can_manage ?? false);
const pending = computed(() => home.value?.pending_topups ?? []);
const tabs = computed<TabKey[]>(() =>
  canManage.value ? ["usage", "topup", "ocr"] : ["usage", "ocr"],
);

const STATUS_LABEL = computed<Record<string, string>>(() => ({
  pending: t("home.status.pending"),
  approved: t("home.status.credited"),
  rejected: t("home.status.rejected"),
}));

const OCR_LABEL = computed<Record<string, string>>(() => ({
  ok: t("home.status.ok"),
  needs_review: t("home.status.toreview"),
  failed: t("home.status.failed"),
}));

const OCR_COLOR: Record<string, string> = {
  ok: "var(--good)",
  needs_review: "var(--warn, #b45309)",
  failed: "var(--danger, #b91c1c)",
};

function baht(n: number | undefined): string {
  return `฿${Number(n ?? 0).toFixed(2)}`;
}

function when(s: string): string {
  return (s || "").slice(0, 16);
}

function explain(err: unknown): string {
  if (err instanceof ApiError)
    return err.message || tf("home.error.http", err.status);
  return String(err);
}

async function refresh(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    home.value = await call<Home>("pearnly_core.billing_www.home");
    await loadRows();
  } catch (err) {
    error.value = explain(err);
  } finally {
    loading.value = false;
  }
}

async function loadRows(): Promise<void> {
  rowsLoading.value = true;
  try {
    const data = await call<{ rows: Record<string, unknown>[] }>(
      "pearnly_core.billing_www.records",
      { tab: tab.value, period: period.value },
    );
    rows.value = data.rows || [];
  } catch (err) {
    error.value = explain(err);
    rows.value = [];
  } finally {
    rowsLoading.value = false;
  }
}

function pickTab(k: TabKey): void {
  tab.value = k;
  void loadRows();
}

function pickPeriod(p: string): void {
  period.value = p;
  void loadRows();
}

function exportRecords(): void {
  // 走浏览器下载：同源 + 会话 cookie，拿到的就是 xlsx（三个 sheet）
  exporting.value = true;
  window.location.href = "/api/method/pearnly_core.billing_www.export_records";
  window.setTimeout(() => (exporting.value = false), 4000);
}

async function run(key: string, fn: () => Promise<void>): Promise<void> {
  busy.value = key;
  error.value = "";
  notice.value = "";
  try {
    await fn();
  } catch (err) {
    error.value = explain(err);
  } finally {
    busy.value = "";
  }
}

function submitTopup(): Promise<void> {
  return run("topup", async () => {
    const res = await call<{ request: string; requested_thb: number }>(
      "pearnly_core.billing_www.topup",
      {
        amount_thb: topupForm.amount,
        note: topupForm.note,
        payer_name: topupForm.payer_name,
        slip_file: topupForm.slip_file,
      },
      { method: "POST" },
    );
    notice.value = tf(
      "home.topup.submitted",
      res.request,
      baht(res.requested_thb),
    );
    topupOpen.value = false;
    topupForm.slip_file = "";
    await refresh();
  });
}

function pickPlan(plan: Plan): Promise<void> {
  return run(`plan:${plan.code}`, async () => {
    const res = await call<{
      ok: boolean;
      reason?: string;
      balance_after: number;
    }>(
      "pearnly_core.billing_www.subscribe",
      { plan_code: plan.code },
      { method: "POST" },
    );
    if (res.ok !== true) throw new Error(res.reason || t("home.plan.failed"));
    notice.value = tf(
      "home.plan.subscribed",
      plan.code,
      baht(res.balance_after),
    );
    await refresh();
  });
}

onMounted(() => {
  // 文案取不到不该把整页挡住：`t()` 取不到会显示键名 —— 看得见、可排查。
  loadTexts().catch(() => {});
  poll = setInterval(() => void pollBalance(), 30000);
  window.addEventListener("focus", pollBalance);
  return refresh();
});
onUnmounted(() => {
  clearInterval(poll);
  window.removeEventListener("focus", pollBalance);
});
</script>

<template>
  <div style="display: flex; flex-direction: column; gap: 16px">
    <Alert v-if="error" theme="red" :title="error" />
    <Alert v-if="notice" theme="green" :title="notice" />
    <Alert
      v-if="pending.length"
      theme="yellow"
      :title="
        tf('home.pending.count', pending.length) +
        ' ' +
        pending.map((p) => baht(p.requested_thb)).join(', ') +
        ' ' +
        t('home.pending.hint')
      "
    />

    <!-- 当前套餐 + 账户余额 -->
    <div
      style="
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 16px;
      "
    >
      <Card :title="t('home.plan.current')" class="">
        <LoadingIndicator v-if="loading" class="h-5 w-5" />
        <template v-else-if="sub">
          <div style="font-size: 22px; font-weight: 800; margin-top: 4px">
            {{ t("home.plan.code") }} {{ sub.plan_code }}
          </div>
          <div class="pn-muted" style="margin-top: 6px">
            {{
              tf(
                "home.plan.usage",
                sub.pages_used_this_cycle,
                sub.quota,
                sub.remaining,
              )
            }}
          </div>
          <div class="pn-muted">
            {{ tf("home.plan.over", baht(sub.over_rate), when(sub.cycle_end)) }}
          </div>
          <div class="pn-muted" style="margin-top: 8px; font-size: 12px">
            {{ t("home.plan.cycleNote") }}
          </div>
        </template>
        <template v-else>
          <div style="font-size: 22px; font-weight: 800; margin-top: 4px">
            {{ t("home.plan.none") }}
          </div>
          <div class="pn-muted" style="margin-top: 6px">
            {{ t("home.plan.noneHint") }}
          </div>
        </template>
      </Card>

      <Card :title="t('home.balance.title')" class="">
        <LoadingIndicator v-if="loading" class="h-5 w-5" />
        <template v-else>
          <div
            style="font-size: 26px; font-weight: 800; margin-top: 4px"
            :style="{
              color:
                (home?.balance_thb ?? 0) > 0
                  ? 'var(--ink)'
                  : 'var(--danger, #b91c1c)',
            }"
          >
            {{ baht(home?.balance_thb) }}
          </div>
          <div class="pn-muted" style="margin-top: 6px">
            <template v-if="sub">
              {{
                tf(
                  "home.balance.used",
                  home?.pages_used_this_month ?? 0,
                  sub.plan_code,
                  sub.remaining,
                )
              }}
            </template>
            <template v-else>
              {{
                tf("home.balance.usedNoPlan", home?.pages_used_this_month ?? 0)
              }}
            </template>
          </div>
          <div
            v-if="canManage"
            style="margin-top: 12px; display: flex; gap: 8px"
          >
            <Button
              variant="solid"
              theme="gray"
              class="pn-primary"
              :label="t('home.topup')"
              @click="
                error = '';
                topupOpen = true;
              "
            />
            <Button
              variant="outline"
              theme="gray"
              :label="loading ? t('home.loading') : t('home.refresh')"
              :disabled="loading"
              @click="refresh"
            />
          </div>
        </template>
      </Card>
    </div>

    <!-- 选择套餐 -->
    <Card :title="t('home.plans.title')" class="">
      <div class="pn-muted" style="margin-bottom: 12px">
        {{ t("home.plans.hint") }}
      </div>
      <div
        style="
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 12px;
        "
      >
        <Card
          :title="t('home.plan.code') + ' ' + p.code"
          v-for="p in home?.plans ?? []"
          :key="p.code"
          class=""
          style="padding: 14px"
          :style="
            sub?.plan_code === p.code ? { borderColor: 'var(--accent)' } : {}
          "
        >
          <div style="font-size: 20px; font-weight: 800; margin: 6px 0">
            {{ baht(p.fee)
            }}<span class="pn-muted" style="font-size: 12px">
              {{ tf("home.plans.perCycle", p.cycle_days) }}</span
            >
          </div>
          <div class="pn-muted">{{ tf("home.plans.quota", p.quota) }}</div>
          <div class="pn-muted">
            {{ tf("home.plans.over", baht(p.over_rate)) }}
          </div>
          <div v-if="canManage" style="margin-top: 12px">
            <Button
              variant="solid"
              theme="gray"
              class="pn-primary"
              :label="
                busy === `plan:${p.code}`
                  ? t('home.plan.subscribing')
                  : sub?.plan_code === p.code
                    ? t('home.plan.current')
                    : t('home.plan.subscribe')
              "
              :disabled="busy === `plan:${p.code}` || sub?.plan_code === p.code"
              @click="pickPlan(p)"
            />
          </div>
        </Card>
      </div>
    </Card>

    <!-- 记录明细 -->
    <Card :title="t('home.ledger.title')" class="">
      <div
        style="
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          flex-wrap: wrap;
        "
      >
        <div style="display: flex; gap: 8px; align-items: center">
          <FormControl
            type="select"
            v-model="period"
            style="width: auto"
            @update:model-value="pickPeriod"
            :options="[
              ...PERIODS.map((p) => ({ value: p.key, label: p.label })),
            ]"
          />
          <Button
            variant="outline"
            theme="gray"
            :label="
              exporting ? t('home.ledger.exporting') : t('home.ledger.export')
            "
            :disabled="exporting"
            @click="exportRecords"
          />
        </div>
      </div>

      <TabButtons
        :model-value="tab"
        :buttons="tabs.map((k) => ({ label: TAB_LABEL[k], value: k }))"
        @update:model-value="pickTab"
      />

      <LoadingIndicator v-if="rowsLoading" class="h-5 w-5" />
      <p v-else-if="!rows.length" class="pn-muted" style="margin: 12px 0 0">
        {{ t("home.ledger.none") }}
      </p>

      <!-- 扣费 -->
      <ListView
        v-else-if="tab === 'usage'"
        style="margin-top: 6px"
        :columns="[
          { key: 'c0', label: t('home.col.time'), width: '150px' },
          { key: 'c1', label: t('home.col.product'), width: '150px' },
          { key: 'c2', label: t('home.col.pages'), width: '150px' },
          { key: 'c3', label: t('home.col.amount'), width: '150px' },
          { key: 'c4', label: t('home.col.balanceAfter'), width: '150px' },
          { key: 'c5', label: t('home.col.description'), width: '150px' },
        ]"
        :rows="rows"
        row-key="name"
        :options="{ selectable: false, showTooltip: false }"
      >
        <template #cell="{ column, row: t }">
          <span v-if="column.key === 'c0'" class="pn-muted">{{
            when(String(t.creation))
          }}</span>
          <span v-if="column.key === 'c1'">{{ t.product || "—" }}</span>
          <span v-if="column.key === 'c2'">{{ t.pages || 0 }}</span>
          <span
            v-if="column.key === 'c3'"
            style="font-weight: 700; color: var(--danger, #b91c1c)"
          >
            {{ baht(Number(t.amount_thb)) }}
          </span>
          <span v-if="column.key === 'c4'">{{
            baht(Number(t.balance_after))
          }}</span>
          <span v-if="column.key === 'c5'" class="pn-muted">{{
            t.description
          }}</span>
        </template>
      </ListView>

      <!-- 充值 -->
      <ListView
        v-else-if="tab === 'topup'"
        style="margin-top: 6px"
        :columns="[
          { key: 'c0', label: t('home.col.time'), width: '150px' },
          { key: 'c1', label: t('home.col.reference'), width: '150px' },
          { key: 'c2', label: t('home.col.status'), width: '150px' },
          { key: 'c3', label: t('home.col.requested'), width: '150px' },
          { key: 'c4', label: t('home.col.received'), width: '150px' },
          { key: 'c5', label: t('home.col.note'), width: '150px' },
        ]"
        :rows="rows"
        row-key="name"
        :options="{ selectable: false, showTooltip: false }"
      >
        <template #cell="{ column, row: t }">
          <span v-if="column.key === 'c0'" class="pn-muted">{{
            when(String(t.creation))
          }}</span>
          <span v-if="column.key === 'c1'">{{ t.name }}</span>
          <span v-if="column.key === 'c2'">{{
            STATUS_LABEL[String(t.status)] ?? t.status
          }}</span>
          <span v-if="column.key === 'c3'">{{
            baht(Number(t.requested_thb))
          }}</span>
          <span v-if="column.key === 'c4'">{{
            t.approved_thb ? baht(Number(t.approved_thb)) : "—"
          }}</span>
          <span v-if="column.key === 'c5'" class="pn-muted">{{
            t.review_note || t.note || "—"
          }}</span>
        </template>
      </ListView>

      <!-- 识别记录 -->
      <ListView
        v-else
        style="margin-top: 6px"
        :columns="[
          { key: 'c0', label: t('home.col.time'), width: '150px' },
          { key: 'c1', label: t('home.col.result'), width: '150px' },
          { key: 'c2', label: t('home.col.file'), width: '150px' },
          { key: 'c3', label: t('home.col.charges'), width: '150px' },
          { key: 'c4', label: t('home.col.summary'), width: '150px' },
        ]"
        :rows="rows"
        row-key="name"
        :options="{ selectable: false, showTooltip: false }"
      >
        <template #cell="{ column, row: r }">
          <span v-if="column.key === 'c0'" class="pn-muted">{{
            when(String(r.creation))
          }}</span>
          <span
            v-if="column.key === 'c1'"
            :style="{
              color: OCR_COLOR[String(r.status)] ?? 'inherit',
              fontWeight: 700,
            }"
          >
            {{ OCR_LABEL[String(r.status)] ?? r.status }}
          </span>
          <span v-if="column.key === 'c2'">{{ r.filename || "—" }}</span>
          <span v-if="column.key === 'c3'">{{
            r.charged_thb === null || r.charged_thb === undefined
              ? "—"
              : baht(Number(r.charged_thb))
          }}</span>
          <span v-if="column.key === 'c4'" class="pn-muted">{{
            r.error || r.detail || "—"
          }}</span>
        </template>
      </ListView>
    </Card>

    <Dialog
      v-model="topupOpen"
      :options="{ title: t('home.topup'), size: 'sm' }"
    >
      <template #body-content>
        <div class="pn-form">
          <Alert theme="blue" :title="t('home.topup.transfer')" />
          <dl v-if="bank" class="space-y-2 text-base">
            <div>
              <dt class="text-ink-gray-5">{{ t("home.bank.name") }}</dt>
              <dd>{{ bank.bank }} · {{ bank.branch }}</dd>
            </div>
            <div>
              <dt class="text-ink-gray-5">{{ t("home.bank.account") }}</dt>
              <dd>{{ bank.account_number }}</dd>
            </div>
            <div>
              <dt class="text-ink-gray-5">{{ t("home.bank.holder") }}</dt>
              <dd>{{ bank.account_name }}</dd>
            </div>
          </dl>
          <FormControl
            :label="t('home.col.amount')"
            :model-value="topupForm.amount"
            @update:model-value="topupForm.amount = Number($event)"
            type="number"
            min="0.01"
            max="500000"
            step="0.01"
          />
          <FormControl
            :label="t('home.field.payer')"
            v-model="topupForm.payer_name"
            :placeholder="t('home.topup.payerHint')"
          />
          <FormControl :label="t('home.col.note')" v-model="topupForm.note" />
          <FileUploader
            :upload-args="{
              private: true,
              upload_endpoint: '/api/method/pearnly_core.payments.proof.upload',
            }"
            file-types="image/jpeg,image/png,application/pdf"
            @success="slipUploaded"
            @failure="topupForm.slip_file = ''"
          >
            <template
              #default="{
                openFileSelector,
                uploading,
                progress,
                error: uploadError,
              }"
            >
              <Button
                :loading="uploading"
                :disabled="busy === 'topup'"
                :label="t('home.topup.slip')"
                @click="
                  topupForm.slip_file = '';
                  openFileSelector();
                "
              />
              <p class="text-sm text-ink-gray-5">
                {{ t("home.topup.slipFormats") }}
              </p>
              <span v-if="uploading"> {{ progress }}%</span>
              <p v-if="topupForm.slip_file" class="text-ink-gray-6">
                {{ t("home.topup.attached") }}
              </p>
              <ErrorMessage v-if="uploadError" :message="uploadError" />
            </template>
          </FileUploader>
          <Alert
            v-if="error"
            style="grid-column: 1 / -1"
            theme="red"
            :title="error"
          />
          <div class="pn-muted" style="grid-column: 1 / -1; font-size: 11px">
            {{ t("home.topup.hint") }}
          </div>
          <div style="display: flex; gap: 8px; justify-content: flex-end">
            <Button
              variant="outline"
              theme="gray"
              :label="t('home.cancel')"
              @click="topupOpen = false"
            />
            <Button
              variant="solid"
              theme="gray"
              class="pn-primary"
              :label="
                busy === 'topup'
                  ? t('home.topup.submitting')
                  : t('home.topup.submit')
              "
              :disabled="
                busy === 'topup' ||
                !topupForm.slip_file ||
                topupForm.amount <= 0 ||
                topupForm.amount > 500000
              "
              @click="submitTopup"
            />
          </div>
        </div>
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.pn-form {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-items: center;
  column-gap: 10px;
  row-gap: 12px;
}
.pn-form > div:last-child {
  grid-column: 1 / -1;
}
.pn-label {
  color: var(--ink3);
  font-size: 13px;
}
</style>
