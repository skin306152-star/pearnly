<script setup lang="ts">
// Cowork 录入工作台。
//
// 这一页对应旧系统的「录入工作台 → 识别记录 → 推送日志」那条线，后端（P3.04 切片）
// 已经跑通，状态机不允许跳步：
//
//   draft ──> recognized ──> confirmed ──> queued ──> pushed
//                                          └──> failed / rejected
//
// 界面上只给**当前状态下合法**的那个动作，但真正的拦截在后端 ——
// 后端对着状态机检查，没确认过的单据推不出去（这条被实测拦过）。
//
// 操作习惯沿用我们自己的：进项/销项分本批区分、列表按状态筛、逐条确认再推送。
import { computed, onMounted, reactive, ref } from "vue";
import { Button } from "frappe-ui";
import { createTexts } from "../../lib/i18n";
import { call, ApiError } from "../../lib/api";

// 页面文案走语言包（键 = `intake.*`）：一次取整包，页面上不维护"我要用哪些键"的清单。
const { t, tf, load: loadTexts } = createTexts([], "intake.");

interface IntakeRow {
  name: string;
  direction: string;
  source: string;
  status: string;
  doc_no: string | null;
  doc_date: string | null;
  counterparty_name: string | null;
  grand_total: number;
  currency: string;
  push_channel: string;
  push_status: string | null;
}

interface IntakeSummary {
  // 字段名以服务端返回为准（pearnly_cowork/intake.py 的 summary()）：
  //   total / by_status / purchase_confirmed / sales_confirmed
  // 这里曾经写成 count / purchase_total / sales_total，结果金额卡片一直显示 "—"
  // —— 接口名对不上**不会报错**，只会安静地显示空值，所以照抄服务端的键名。
  total?: number;
  by_status?: Record<string, number>;
  purchase_confirmed?: string;
  sales_confirmed?: string;
}

const ROWS: IntakeRow[] = [];
const rows = ref<IntakeRow[]>(ROWS);
const summary = ref<IntakeSummary | null>(null);
const loading = ref(true);
const busy = ref("");
const error = ref("");
const notice = ref("");
const showForm = ref(false);
const filterStatus = ref("");
const filterDirection = ref("");

// 状态机的中文说法：界面上不直接甩英文状态码给客户看。
//
// 这里存的是**文案编号**、不是文案本身 —— 语言包是异步取的，
// setup 那一刻取一次就会永远定格成键名（页面上真的显示过 `intake.status.draft`）。
const STATUS_KEYS: Record<string, string> = {
  draft: "intake.status.draft",
  recognized: "intake.status.recognized",
  confirmed: "intake.status.confirmed",
  queued: "intake.status.queued",
  pushed: "intake.status.pushed",
  failed: "intake.status.failed",
  rejected: "intake.status.rejected",
};

const STATUS_CODES = Object.keys(STATUS_KEYS);

const DIRECTION_KEYS: Record<string, string> = {
  purchase: "intake.dir.purchase",
  sales: "intake.dir.sales",
};

/** 认不出的状态码原样显示：宁可露出英文码，也不显示成空白。 */
function statusLabel(code: string): string {
  return STATUS_KEYS[code] ? t(STATUS_KEYS[code]) : code;
}

function dirLabel(code: string): string {
  return DIRECTION_KEYS[code] ? t(DIRECTION_KEYS[code]) : code;
}

const draft = reactive({
  direction: "purchase",
  source: "upload",
  doc_no: "",
  doc_date: new Date().toISOString().slice(0, 10),
  counterparty_name: "",
  net_amount: 0,
  vat_amount: 0,
});

const canSubmit = computed(
  () => draft.counterparty_name.trim().length > 0 && !busy.value,
);

function money(value: number | string, currency = "THB"): string {
  const n = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(n)) return String(value);
  return `${currency} ${n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function explain(err: unknown): string {
  if (err instanceof ApiError) return err.message || tf("intake.error.http", err.status);
  return String(err);
}

/**
 * 当前状态下能做哪个动作。后端仍会自己检查一遍，这里只是不给人点死路。
 *
 * 返回的是**文案编号**：`label` 在模板里现取，不在这里定格（语言包是异步的）。
 */
function actions(row: IntakeRow): { key: string; label: string }[] {
  if (row.status === "draft" || row.status === "recognized") {
    return [{ key: "confirm", label: t("intake.action.confirm") }];
  }
  if (row.status === "confirmed") {
    return [{ key: "queue", label: t("intake.action.queue") }];
  }
  return [];
}

async function refresh(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const [list, sum] = await Promise.all([
      call<IntakeRow[]>("pearnly_cowork.intake.api_list_intake", {
        status: filterStatus.value || undefined,
        direction: filterDirection.value || undefined,
        limit: 50,
      }),
      call<IntakeSummary>("pearnly_cowork.intake.api_summary"),
    ]);
    rows.value = list;
    summary.value = sum;
  } catch (err) {
    error.value = explain(err);
  } finally {
    loading.value = false;
  }
}

async function submit(): Promise<void> {
  busy.value = "create";
  error.value = "";
  notice.value = "";
  try {
    const created = await call<{ name: string } | string>(
      "pearnly_cowork.intake.api_create_intake",
      {
        direction: draft.direction,
        source: draft.source,
        doc_no: draft.doc_no.trim() || null,
        doc_date: draft.doc_date,
        counterparty_name: draft.counterparty_name.trim(),
        net_amount: Number(draft.net_amount) || 0,
        vat_amount: Number(draft.vat_amount) || 0,
      },
      { method: "POST" },
    );
    const name = typeof created === "string" ? created : created?.name;
    notice.value = tf("intake.notice.created", name ?? "");
    draft.counterparty_name = "";
    draft.doc_no = "";
    draft.net_amount = 0;
    draft.vat_amount = 0;
    showForm.value = false;
    await refresh();
  } catch (err) {
    error.value = explain(err);
  } finally {
    busy.value = "";
  }
}

async function doAction(row: IntakeRow, action: { key: string; label: string }): Promise<void> {
  busy.value = row.name;
  error.value = "";
  notice.value = "";
  try {
    const method =
      action.key === "confirm"
        ? "pearnly_cowork.intake.api_confirm_intake"
        : "pearnly_cowork.intake.api_queue_for_push";
    const body: Record<string, unknown> = { name: row.name };
    if (action.key === "queue") body.channel = "companion"; // 小助手出站拉取
    const result = await call<{ name: string; status: string }>(method, body, { method: "POST" });
    notice.value = tf("intake.notice.moved", row.name, statusLabel(result.status));
    await refresh();
  } catch (err) {
    error.value = explain(err);
  } finally {
    busy.value = "";
  }
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

    <div style="display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px">
      <div class="pn-card">
        <div class="pn-muted">{{ t('intake.card.waiting') }}</div>
        <div style="font-size: 22px; font-weight: 800">
          {{ (summary?.by_status?.draft ?? 0) + (summary?.by_status?.recognized ?? 0) }}
        </div>
      </div>
      <div class="pn-card">
        <!-- 这一格数的是"已确认、还没排队"——那才是现在能推的东西。
             叫"待推送"会和状态里的 queued（已排队待推送）撞名，所以写清楚。 -->
        <div class="pn-muted">{{ t('intake.card.readyToPush') }}</div>
        <div style="font-size: 22px; font-weight: 800">
          {{ summary?.by_status?.confirmed ?? 0 }}
        </div>
      </div>
      <div class="pn-card">
        <div class="pn-muted">{{ t('intake.card.purchaseTotal') }}</div>
        <div style="font-size: 22px; font-weight: 800">
          {{ summary?.purchase_confirmed ? money(summary.purchase_confirmed) : "—" }}
        </div>
      </div>
      <div class="pn-card">
        <div class="pn-muted">{{ t('intake.card.salesTotal') }}</div>
        <div style="font-size: 22px; font-weight: 800">
          {{ summary?.sales_confirmed ? money(summary.sales_confirmed) : "—" }}
        </div>
      </div>
    </div>

    <div class="pn-card">
      <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap">
        <div>
          <div style="font-size: 16px; font-weight: 800">{{ t('intake.title') }}</div>
          <div class="pn-muted">
            {{ t('intake.subtitle') }}
          </div>
        </div>
        <div style="display: flex; gap: 8px; align-items: center">
          <select v-model="filterDirection" class="form-input" @change="refresh">
            <option value="">{{ t('intake.filter.allDirections') }}</option>
            <option value="purchase">{{ t('intake.dir.purchase') }}</option>
            <option value="sales">{{ t('intake.dir.sales') }}</option>
          </select>
          <select v-model="filterStatus" class="form-input" @change="refresh">
            <option value="">{{ t('intake.filter.allStatuses') }}</option>
            <option v-for="code in STATUS_CODES" :key="code" :value="code">{{ statusLabel(code) }}</option>
          </select>
          <Button
            variant="outline"
            theme="gray"
            :label="loading ? t('intake.loading') : t('intake.refresh')"
            :disabled="loading"
            @click="refresh"
          />
          <Button
            variant="solid"
            theme="gray"
            class="pn-primary"
            :label="showForm ? t('intake.collapse') : t('intake.action.new')"
            @click="showForm = !showForm"
          />
        </div>
      </div>

      <div v-if="showForm" style="margin-top: 16px; border-top: 1px solid var(--line); padding-top: 16px">
        <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px">
          <label>
            <span class="pn-muted">{{ t('intake.field.direction') }}</span>
            <select v-model="draft.direction" class="form-input w-full">
              <option value="purchase">{{ t('intake.dir.purchaseFull') }}</option>
              <option value="sales">{{ t('intake.dir.salesFull') }}</option>
            </select>
          </label>
          <label>
            <span class="pn-muted">{{ t('intake.field.source') }}</span>
            <select v-model="draft.source" class="form-input w-full">
              <option value="upload">{{ t('intake.source.upload') }}</option>
              <option value="line">LINE</option>
              <option value="email">{{ t('intake.source.email') }}</option>
              <option value="manual">{{ t('intake.source.manual') }}</option>
            </select>
          </label>
          <label>
            <span class="pn-muted">{{ t('intake.field.counterparty') }}</span>
            <input
              v-model="draft.counterparty_name"
              class="form-input w-full"
              :placeholder="t('intake.placeholder.counterparty')"
            />
          </label>
          <label>
            <span class="pn-muted">{{ t('intake.field.docNo') }}</span>
            <input
              v-model="draft.doc_no"
              class="form-input w-full"
              :placeholder="t('intake.placeholder.docNo')"
            />
          </label>
          <label>
            <span class="pn-muted">{{ t('intake.field.docDate') }}</span>
            <input v-model="draft.doc_date" type="date" class="form-input w-full" />
          </label>
          <div />
          <label>
            <span class="pn-muted">{{ t('intake.field.netAmount') }}</span>
            <input v-model.number="draft.net_amount" type="number" step="0.01" class="form-input w-full" />
          </label>
          <label>
            <span class="pn-muted">{{ t('intake.field.vatAmount') }}</span>
            <input v-model.number="draft.vat_amount" type="number" step="0.01" class="form-input w-full" />
          </label>
        </div>
        <div style="margin-top: 16px; display: flex; gap: 8px; align-items: center">
          <Button
            variant="solid"
            theme="gray"
            class="pn-primary"
            :label="busy === 'create' ? t('intake.saving') : t('intake.save')"
            :disabled="!canSubmit"
            @click="submit"
          />
          <span class="pn-muted">{{ t('intake.hint.afterSave') }}</span>
        </div>
      </div>

      <div style="margin-top: 16px">
        <div v-if="loading" style="display: flex; flex-direction: column; gap: 8px">
          <div v-for="i in 4" :key="i" class="pn-skeleton" />
        </div>
        <p v-else-if="!rows.length" class="pn-muted" style="margin: 0">
          {{ t('intake.empty') }}
        </p>
        <table v-else class="pn-table">
          <thead>
            <tr>
              <th>{{ t('intake.col.name') }}</th>
              <th>{{ t('intake.col.direction') }}</th>
              <th>{{ t('intake.col.counterparty') }}</th>
              <th>{{ t('intake.field.docNo') }}</th>
              <th>{{ t('intake.col.date') }}</th>
              <th>{{ t('intake.col.status') }}</th>
              <th class="pn-num">{{ t('intake.col.grandTotal') }}</th>
              <th>{{ t('intake.col.actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.name">
              <td>{{ row.name }}</td>
              <td>{{ dirLabel(row.direction) }}</td>
              <td>{{ row.counterparty_name || "—" }}</td>
              <td>{{ row.doc_no || "—" }}</td>
              <td>{{ row.doc_date || "—" }}</td>
              <td>
                {{ statusLabel(row.status) }}
                <span v-if="row.push_status" class="pn-muted"> / {{ row.push_status }}</span>
              </td>
              <td class="pn-num">{{ money(row.grand_total, row.currency) }}</td>
              <td>
                <Button
                  v-for="action in actions(row)"
                  :key="action.key"
                  variant="outline"
                  theme="gray"
                  :label="busy === row.name ? t('intake.processing') : action.label"
                  :disabled="!!busy"
                  @click="doAction(row, action)"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
