<script setup lang="ts">
// 首页 = 订阅与计费（三个产品共用这一个页面）。
//
// 照老产品搬（`src/home/subscription.ts` + `static/dms/dms-billing*.js` 那两套
// 各写各的实现合成了这一份）：
//   当前套餐 · 账户余额 · 选择套餐 · 记录明细 · 充值
//
// 数据全在控制面（钱只有一本账），这里经过本站后端中转：
// 浏览器 → 本站白名单方法 → 控制面（服务令牌）。**服务令牌绝不下发到浏览器。**
import { computed, onMounted, reactive, ref } from "vue";
import { Button, Dialog } from "frappe-ui";
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
const topupForm = reactive({ amount: 1000, note: "", payer_name: "" });

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
const tabs = computed<TabKey[]>(() => (canManage.value ? ["usage", "topup", "ocr"] : ["usage", "ocr"]));

const STATUS_LABEL: Record<string, string> = {
  pending: t("home.status.pending"),
  approved: t("home.status.credited"),
  rejected: t("home.status.rejected"),
};

const OCR_LABEL: Record<string, string> = {
  ok: t("home.status.ok"),
  needs_review: t("home.status.toreview"),
  failed: t("home.status.failed"),
};

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
  if (err instanceof ApiError) return err.message || tf("home.error.http", err.status);
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
      { amount_thb: topupForm.amount, note: topupForm.note, payer_name: topupForm.payer_name },
      { method: "POST" },
    );
    notice.value = tf("home.topup.submitted", res.request, baht(res.requested_thb));
    topupOpen.value = false;
    await refresh();
  });
}

function pickPlan(plan: Plan): Promise<void> {
  return run(`plan:${plan.code}`, async () => {
    const res = await call<{ balance_after: number }>(
      "pearnly_core.billing_www.subscribe",
      { plan_code: plan.code },
      { method: "POST" },
    );
    notice.value = tf("home.plan.subscribed", plan.code, baht(res.balance_after));
    await refresh();
  });
}

onMounted(() => {
  // 文案取不到不该把整页挡住：`t()` 取不到会显示键名 —— 看得见、可排查。
  loadTexts().catch(() => {});
  return refresh();
});
</script>

<template>
  <div style="display: flex; flex-direction: column; gap: 16px">
    <div v-if="error" class="pn-error" role="alert">{{ error }}</div>
    <div
      v-if="notice"
      class="pn-card"
      style="padding: 12px 14px; color: var(--good); font-weight: 700; font-size: 13px"
    >
      {{ notice }}
    </div>
    <div
      v-if="pending.length"
      class="pn-card"
      style="padding: 12px 14px; font-size: 13px"
    >
      {{ tf('home.pending.count', pending.length) }}
      <b>{{ pending.map((p) => baht(p.requested_thb)).join("、") }}</b>
      {{ t('home.pending.hint') }}
    </div>

    <!-- 当前套餐 + 账户余额 -->
    <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px">
      <div class="pn-card">
        <div class="pn-muted">{{ t('home.plan.current') }}</div>
        <div v-if="loading" class="pn-skeleton" style="width: 120px; margin-top: 10px" />
        <template v-else-if="sub">
          <div style="font-size: 22px; font-weight: 800; margin-top: 4px">
            {{ t('home.plan.code') }} {{ sub.plan_code }}
          </div>
          <div class="pn-muted" style="margin-top: 6px">
            {{ tf('home.plan.usage', sub.pages_used_this_cycle, sub.quota, sub.remaining) }}
          </div>
          <div class="pn-muted">{{ tf('home.plan.over', baht(sub.over_rate), when(sub.cycle_end)) }}</div>
          <div class="pn-muted" style="margin-top: 8px; font-size: 12px">
            {{ t('home.plan.cycleNote') }}
          </div>
        </template>
        <template v-else>
          <div style="font-size: 22px; font-weight: 800; margin-top: 4px">{{ t('home.plan.none') }}</div>
          <div class="pn-muted" style="margin-top: 6px">
            {{ t('home.plan.noneHint') }}
          </div>
        </template>
      </div>

      <div class="pn-card">
        <div class="pn-muted">{{ t('home.balance.title') }}</div>
        <div v-if="loading" class="pn-skeleton" style="width: 120px; margin-top: 10px" />
        <template v-else>
          <div
            style="font-size: 26px; font-weight: 800; margin-top: 4px"
            :style="{ color: (home?.balance_thb ?? 0) > 0 ? 'var(--ink)' : 'var(--danger, #b91c1c)' }"
          >
            {{ baht(home?.balance_thb) }}
          </div>
          <div class="pn-muted" style="margin-top: 6px">
            <template v-if="sub">
              {{ tf('home.balance.used', home?.pages_used_this_month ?? 0, sub.plan_code, sub.remaining) }}
            </template>
            <template v-else>
              {{ tf('home.balance.usedNoPlan', home?.pages_used_this_month ?? 0) }}
            </template>
          </div>
          <div v-if="canManage" style="margin-top: 12px; display: flex; gap: 8px">
            <Button
              variant="solid"
              theme="gray"
              class="pn-primary"
              :label="t('home.topup')"
              @click="topupOpen = true"
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
      </div>
    </div>

    <!-- 选择套餐 -->
    <div class="pn-card">
      <div style="font-size: 16px; font-weight: 800">{{ t('home.plans.title') }}</div>
      <div class="pn-muted" style="margin-bottom: 12px">
        {{ t('home.plans.hint') }}
      </div>
      <div style="display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px">
        <div
          v-for="p in home?.plans ?? []"
          :key="p.code"
          class="pn-card"
          style="padding: 14px"
          :style="sub?.plan_code === p.code ? { borderColor: 'var(--accent)' } : {}"
        >
          <div style="font-weight: 800">Package {{ p.code }}</div>
          <div style="font-size: 20px; font-weight: 800; margin: 6px 0">
            {{ baht(p.fee) }}<span class="pn-muted" style="font-size: 12px"> {{ tf('home.plans.perCycle', p.cycle_days) }}</span>
          </div>
          <div class="pn-muted">{{ tf('home.plans.quota', p.quota) }}</div>
          <div class="pn-muted">{{ tf('home.plans.over', baht(p.over_rate)) }}</div>
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
        </div>
      </div>
    </div>

    <!-- 记录明细 -->
    <div class="pn-card">
      <div style="display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap">
        <div style="font-size: 16px; font-weight: 800">{{ t('home.ledger.title') }}</div>
        <div style="display: flex; gap: 8px; align-items: center">
          <select v-model="period" class="form-input" style="width: auto" @change="pickPeriod(period)">
            <option v-for="p in PERIODS" :key="p.key" :value="p.key">{{ p.label }}</option>
          </select>
          <Button
            variant="outline"
            theme="gray"
            :label="exporting ? t('home.ledger.exporting') : t('home.ledger.export')"
            :disabled="exporting"
            @click="exportRecords"
          />
        </div>
      </div>

      <div style="display: flex; gap: 6px; margin: 12px 0">
        <button
          v-for="k in tabs"
          :key="k"
          type="button"
          class="pn-tab"
          :class="{ 'is-on': tab === k }"
          @click="pickTab(k)"
        >
          {{ TAB_LABEL[k] }}
        </button>
      </div>

      <div v-if="rowsLoading" class="pn-skeleton" style="width: 200px; margin-top: 12px" />
      <p v-else-if="!rows.length" class="pn-muted" style="margin: 12px 0 0">
        {{ t('home.ledger.none') }}
      </p>

      <!-- 扣费 -->
      <table v-else-if="tab === 'usage'" class="pn-table" style="margin-top: 6px">
        <thead>
          <tr>
            <th>{{ t('home.col.time') }}</th>
            <th>{{ t('home.col.product') }}</th>
            <th>{{ t('home.col.pages') }}</th>
            <th>{{ t('home.col.amount') }}</th>
            <th>{{ t('home.col.balanceAfter') }}</th>
            <th>{{ t('home.col.description') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in rows" :key="String(t.name)">
            <td class="pn-muted">{{ when(String(t.creation)) }}</td>
            <td>{{ t.product || "—" }}</td>
            <td>{{ t.pages || 0 }}</td>
            <td style="font-weight: 700; color: var(--danger, #b91c1c)">
              {{ baht(Number(t.amount_thb)) }}
            </td>
            <td>{{ baht(Number(t.balance_after)) }}</td>
            <td class="pn-muted">{{ t.description }}</td>
          </tr>
        </tbody>
      </table>

      <!-- 充值 -->
      <table v-else-if="tab === 'topup'" class="pn-table" style="margin-top: 6px">
        <thead>
          <tr>
            <th>{{ t('home.col.time') }}</th>
            <th>{{ t('home.col.reference') }}</th>
            <th>{{ t('home.col.status') }}</th>
            <th>{{ t('home.col.requested') }}</th>
            <th>{{ t('home.col.received') }}</th>
            <th>{{ t('home.col.note') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in rows" :key="String(t.name)">
            <td class="pn-muted">{{ when(String(t.creation)) }}</td>
            <td>{{ t.name }}</td>
            <td>{{ STATUS_LABEL[String(t.status)] ?? t.status }}</td>
            <td>{{ baht(Number(t.requested_thb)) }}</td>
            <td>{{ t.approved_thb ? baht(Number(t.approved_thb)) : "—" }}</td>
            <td class="pn-muted">{{ t.review_note || t.note || "—" }}</td>
          </tr>
        </tbody>
      </table>

      <!-- 识别记录 -->
      <table v-else class="pn-table" style="margin-top: 6px">
        <thead>
          <tr>
            <th>{{ t('home.col.time') }}</th>
            <th>{{ t('home.col.result') }}</th>
            <th>{{ t('home.col.file') }}</th>
            <th>{{ t('home.col.charges') }}</th>
            <th>{{ t('home.col.summary') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="String(r.name)">
            <td class="pn-muted">{{ when(String(r.creation)) }}</td>
            <td :style="{ color: OCR_COLOR[String(r.status)] ?? 'inherit', fontWeight: 700 }">
              {{ OCR_LABEL[String(r.status)] ?? r.status }}
            </td>
            <td>{{ r.filename || "—" }}</td>
            <!-- 0 要说成 ฿0.00（套餐内就是这么扣的），不能显示成"—"（那是"没这笔"） -->
            <td>{{ r.charged_thb === null || r.charged_thb === undefined ? "—" : baht(Number(r.charged_thb)) }}</td>
            <td class="pn-muted">{{ r.error || r.detail || "—" }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <Dialog v-model="topupOpen" :options="{ title: t('home.topup'), size: 'sm' }">
      <template #body-content>
        <div class="pn-form">
          <label class="pn-label">{{ t('home.col.amount') }}</label>
          <input v-model.number="topupForm.amount" type="number" min="1" class="form-input" />
          <label class="pn-label">{{ t('home.field.payer') }}</label>
          <input v-model="topupForm.payer_name" class="form-input" :placeholder="t('home.topup.payerHint')" />
          <label class="pn-label">{{ t('home.col.note') }}</label>
          <input v-model="topupForm.note" class="form-input" />
          <div v-if="error" class="pn-error" style="grid-column: 1 / -1" role="alert">{{ error }}</div>
          <div class="pn-muted" style="grid-column: 1 / -1; font-size: 11px">
            {{ t('home.topup.hint') }}
          </div>
          <div style="display: flex; gap: 8px; justify-content: flex-end">
            <Button variant="outline" theme="gray" :label="t('home.cancel')" @click="topupOpen = false" />
            <Button
              variant="solid"
              theme="gray"
              class="pn-primary"
              :label="busy === 'topup' ? t('home.topup.submitting') : t('home.topup.submit')"
              :disabled="busy === 'topup' || !topupForm.amount"
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
  grid-template-columns: 56px minmax(0, 1fr);
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
.pn-tab {
  padding: 5px 14px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  color: var(--ink3);
  font-size: 13px;
  cursor: pointer;
}
.pn-tab.is-on {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-ink);
  font-weight: 700;
}
</style>
