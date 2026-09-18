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
const TAB_LABEL: Record<TabKey, string> = {
  usage: "扣费",
  topup: "充值",
  ocr: "识别记录",
};
const PERIODS = [
  { key: "all", label: "全部" },
  { key: "day", label: "今天" },
  { key: "month", label: "本月" },
  { key: "year", label: "今年" },
];
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
  pending: "待审核",
  approved: "已到账",
  rejected: "已拒绝",
};

const OCR_LABEL: Record<string, string> = {
  ok: "成功",
  needs_review: "待复核",
  failed: "失败",
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
  if (err instanceof ApiError) return err.message || `请求失败（HTTP ${err.status}）`;
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
    notice.value = `充值申请已提交（${res.request} · ${baht(res.requested_thb)}），等平台确认到账后余额才会增加。`;
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
    notice.value = `已订阅套餐 ${plan.code}，月费从余额扣除，当前余额 ${baht(res.balance_after)}。`;
    await refresh();
  });
}

onMounted(refresh);
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
      有 {{ pending.length }} 笔充值待平台确认：
      <b>{{ pending.map((p) => baht(p.requested_thb)).join("、") }}</b>
      —— 确认后余额会自动增加。
    </div>

    <!-- 当前套餐 + 账户余额 -->
    <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px">
      <div class="pn-card">
        <div class="pn-muted">当前套餐</div>
        <div v-if="loading" class="pn-skeleton" style="width: 120px; margin-top: 10px" />
        <template v-else-if="sub">
          <div style="font-size: 22px; font-weight: 800; margin-top: 4px">
            套餐 {{ sub.plan_code }}
          </div>
          <div class="pn-muted" style="margin-top: 6px">
            本周期已用 {{ sub.pages_used_this_cycle }} / {{ sub.quota }} 张，剩 {{ sub.remaining }} 张
          </div>
          <div class="pn-muted">超额 {{ baht(sub.over_rate) }}/张 · 周期到 {{ when(sub.cycle_end) }}</div>
          <div class="pn-muted" style="margin-top: 8px; font-size: 12px">
            套餐按 30 天一期，到期结束（不做自动续订）—— 到期后在下面重新订阅即可。
          </div>
        </template>
        <template v-else>
          <div style="font-size: 22px; font-weight: 800; margin-top: 4px">未订阅</div>
          <div class="pn-muted" style="margin-top: 6px">
            当前按量计费 · 订阅后在下方选套餐更省
          </div>
        </template>
      </div>

      <div class="pn-card">
        <div class="pn-muted">账户余额</div>
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
              本月已用 {{ home?.pages_used_this_month ?? 0 }} 张 · 套餐 {{ sub.plan_code }} 本周期剩
              {{ sub.remaining }} 张
            </template>
            <template v-else>
              本月已用 {{ home?.pages_used_this_month ?? 0 }} 张 · 未订阅按量计费
            </template>
          </div>
          <div v-if="canManage" style="margin-top: 12px; display: flex; gap: 8px">
            <Button
              variant="solid"
              theme="gray"
              class="pn-primary"
              label="充值"
              @click="topupOpen = true"
            />
            <Button
              variant="outline"
              theme="gray"
              :label="loading ? '加载中…' : '刷新'"
              :disabled="loading"
              @click="refresh"
            />
          </div>
        </template>
      </div>
    </div>

    <!-- 选择套餐 -->
    <div class="pn-card">
      <div style="font-size: 16px; font-weight: 800">选择套餐</div>
      <div class="pn-muted" style="margin-bottom: 12px">
        订阅后先用套餐额度，超额按套餐单价从余额扣
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
            {{ baht(p.fee) }}<span class="pn-muted" style="font-size: 12px"> / {{ p.cycle_days }} 天</span>
          </div>
          <div class="pn-muted">每周期额度 {{ p.quota }} 张</div>
          <div class="pn-muted">超额 {{ baht(p.over_rate) }}/张</div>
          <div v-if="canManage" style="margin-top: 12px">
            <Button
              variant="solid"
              theme="gray"
              class="pn-primary"
              :label="
                busy === `plan:${p.code}`
                  ? '订阅中…'
                  : sub?.plan_code === p.code
                    ? '当前套餐'
                    : '订阅此套餐'
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
        <div style="font-size: 16px; font-weight: 800">记录明细</div>
        <div style="display: flex; gap: 8px; align-items: center">
          <select v-model="period" class="form-input" style="width: auto" @change="pickPeriod(period)">
            <option v-for="p in PERIODS" :key="p.key" :value="p.key">{{ p.label }}</option>
          </select>
          <Button
            variant="outline"
            theme="gray"
            :label="exporting ? '导出中…' : '导出明细'"
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
        还没有记录
      </p>

      <!-- 扣费 -->
      <table v-else-if="tab === 'usage'" class="pn-table" style="margin-top: 6px">
        <thead>
          <tr>
            <th>时间</th>
            <th>产品</th>
            <th>张数</th>
            <th>金额</th>
            <th>变动后余额</th>
            <th>说明</th>
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
            <th>时间</th>
            <th>单号</th>
            <th>状态</th>
            <th>申请金额</th>
            <th>实收金额</th>
            <th>备注</th>
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
            <th>时间</th>
            <th>结果</th>
            <th>文件</th>
            <th>扣费</th>
            <th>摘要</th>
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

    <Dialog v-model="topupOpen" :options="{ title: '充值', size: 'sm' }">
      <template #body-content>
        <div class="pn-form">
          <label class="pn-label">金额</label>
          <input v-model.number="topupForm.amount" type="number" min="1" class="form-input" />
          <label class="pn-label">付款人</label>
          <input v-model="topupForm.payer_name" class="form-input" placeholder="银行转账时的付款方" />
          <label class="pn-label">备注</label>
          <input v-model="topupForm.note" class="form-input" />
          <div v-if="error" class="pn-error" style="grid-column: 1 / -1" role="alert">{{ error }}</div>
          <div class="pn-muted" style="grid-column: 1 / -1; font-size: 11px">
            提交后平台确认到账，余额才会增加。
          </div>
          <div style="display: flex; gap: 8px; justify-content: flex-end">
            <Button variant="outline" theme="gray" label="取消" @click="topupOpen = false" />
            <Button
              variant="solid"
              theme="gray"
              class="pn-primary"
              :label="busy === 'topup' ? '提交中…' : '提交申请'"
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
