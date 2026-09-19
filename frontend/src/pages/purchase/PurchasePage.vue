<script setup lang="ts">
// 采购记录（ERP 第一条纵向切片的主界面）。
//
// 这一页的意义不在"好看"，在于把前后端合同走通一遍：
//   建单 → 落库 → 列表查回 → 汇总对账；没买这个功能的租户连菜单都看不到，
//   直接调接口也会被后端挡（前端隐藏只是不让人点进死路，不是权限边界）。
//
// 金额**不在前端算**：服务端会从小发明细重算小计/增值税/价税合计/应付净额。
// 这里展示的都是服务端返回的数字。
import { computed, onMounted, reactive, ref } from "vue";
import { Button } from "frappe-ui";
import { createTexts } from "../../lib/i18n";
import { call, ApiError } from "../../lib/api";

// 页面文案走语言包（键 = `purchase.*`）：一次取整包，页面上不维护"我要用哪些键"的清单。
const { t, tf, load: loadTexts } = createTexts([], "purchase.");

interface Row {
  name: string;
  doc_kind: string;
  supplier_name: string;
  doc_no: string | null;
  doc_date: string;
  status: string;
  currency: string;
  grand_total: number;
  net_payable: number;
  payment_status: string | null;
}

interface Summary {
  count: number;
  grand_total: string;
  vat_amount: string;
  net_payable: string;
  paid_amount: string;
  outstanding: string;
}

interface LineDraft {
  description: string;
  qty: number;
  unit_price: number;
  vat_rate: number;
}

const rows = ref<Row[]>([]);
const summary = ref<Summary | null>(null);
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const okMessage = ref("");
const showForm = ref(false);

const draft = reactive({
  supplier_name: "",
  doc_date: new Date().toISOString().slice(0, 10),
  doc_no: "",
  lines: [{ description: "", qty: 1, unit_price: 0, vat_rate: 7 }] as LineDraft[],
});

const canSubmit = computed(
  () =>
    draft.supplier_name.trim().length > 0 &&
    draft.lines.every((l) => l.description.trim().length > 0) &&
    !saving.value,
);

function money(value: number | string, currency = "THB"): string {
  const n = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(n)) return String(value);
  return `${currency} ${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function explain(err: unknown): string {
  if (err instanceof ApiError) return err.message || tf("purchase.error.http", err.status);
  return String(err);
}

async function refresh(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    // 两个接口并行取：列表与汇总互不依赖
    const [list, sum] = await Promise.all([
      call<Row[]>("pearnly_erp_portal.purchase.api_list_purchases", { limit: 50 }),
      call<Summary>("pearnly_erp_portal.purchase.api_summary"),
    ]);
    rows.value = list;
    summary.value = sum;
  } catch (err) {
    error.value = explain(err);
  } finally {
    loading.value = false;
  }
}

function addLine(): void {
  draft.lines.push({ description: "", qty: 1, unit_price: 0, vat_rate: 7 });
}

function removeLine(index: number): void {
  if (draft.lines.length > 1) draft.lines.splice(index, 1);
}

async function submit(): Promise<void> {
  saving.value = true;
  error.value = "";
  okMessage.value = "";
  try {
    const created = await call<{ name: string }>(
      "pearnly_erp_portal.purchase.api_create_purchase",
      {
        supplier_name: draft.supplier_name.trim(),
        doc_date: draft.doc_date,
        doc_no: draft.doc_no.trim() || null,
        lines: draft.lines.map((line) => ({
          description: line.description.trim(),
          qty: Number(line.qty) || 0,
          unit_price: Number(line.unit_price) || 0,
          vat_rate: Number(line.vat_rate) || 0,
        })),
      },
      { method: "POST" },
    );
    okMessage.value = tf("purchase.notice.created", created.name);
    // 建完就清空，避免手快连点建出两张一样的
    draft.supplier_name = "";
    draft.doc_no = "";
    draft.lines = [{ description: "", qty: 1, unit_price: 0, vat_rate: 7 }];
    showForm.value = false;
    await refresh();
  } catch (err) {
    error.value = explain(err);
  } finally {
    saving.value = false;
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
    <div class="pn-error" v-if="error" role="alert">{{ error }}</div>
    <div
      v-if="okMessage"
      class="pn-card"
      style="padding: 12px 14px; color: var(--good); font-weight: 700; font-size: 13px"
    >
      {{ okMessage }}
    </div>

    <div style="display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px">
      <div class="pn-card">
        <div class="pn-muted">{{ t('purchase.card.count') }}</div>
        <div style="font-size: 22px; font-weight: 800">{{ summary?.count ?? "—" }}</div>
      </div>
      <div class="pn-card">
        <div class="pn-muted">{{ t('purchase.col.grandTotal') }}</div>
        <div style="font-size: 22px; font-weight: 800">
          {{ summary ? money(summary.grand_total) : "—" }}
        </div>
      </div>
      <div class="pn-card">
        <div class="pn-muted">{{ t('purchase.card.netPayable') }}</div>
        <div style="font-size: 22px; font-weight: 800">
          {{ summary ? money(summary.net_payable) : "—" }}
        </div>
      </div>
      <div class="pn-card">
        <div class="pn-muted">{{ t('purchase.card.outstanding') }}</div>
        <div style="font-size: 22px; font-weight: 800; color: var(--warn)">
          {{ summary ? money(summary.outstanding) : "—" }}
        </div>
      </div>
    </div>

    <div class="pn-card">
      <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px">
        <div>
          <div style="font-size: 16px; font-weight: 800">{{ t('purchase.title') }}</div>
          <div class="pn-muted">{{ t('purchase.subtitle') }}</div>
        </div>
        <div style="display: flex; gap: 8px">
          <!-- 尺寸/间距用官方原生的（Button 自带尺寸档与内边距），只有主色用我们的
               （pn-primary）—— 操作习惯与流程不变。 -->
          <Button
            variant="outline"
            theme="gray"
            :label="loading ? t('purchase.loading') : t('purchase.refresh')"
            :disabled="loading"
            @click="refresh"
          />
          <Button
            variant="solid"
            theme="gray"
            class="pn-primary"
            :label="showForm ? t('purchase.collapse') : t('purchase.action.new')"
            @click="showForm = !showForm"
          />
        </div>
      </div>

      <div v-if="showForm" style="margin-top: 16px; border-top: 1px solid var(--line); padding-top: 16px">
        <div style="display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 12px">
          <label>
            <span class="pn-muted">{{ t('purchase.field.supplier') }}</span>
            <input
              v-model="draft.supplier_name"
              class="form-input w-full"
              :placeholder="t('purchase.placeholder.supplier')"
            />
          </label>
          <label>
            <span class="pn-muted">{{ t('purchase.field.docDate') }}</span>
            <input v-model="draft.doc_date" type="date" class="form-input w-full" />
          </label>
          <label>
            <span class="pn-muted">{{ t('purchase.field.docNo') }}</span>
            <input
              v-model="draft.doc_no"
              class="form-input w-full"
              :placeholder="t('purchase.placeholder.docNo')"
            />
          </label>
        </div>

        <div style="margin-top: 14px">
          <div class="pn-muted" style="margin-bottom: 6px">{{ t('purchase.field.lines') }}</div>
          <div
            v-for="(line, index) in draft.lines"
            :key="index"
            style="display: grid; grid-template-columns: 3fr 1fr 1fr 1fr auto; gap: 8px; margin-bottom: 8px"
          >
            <input
              v-model="line.description"
              class="form-input w-full"
              :placeholder="t('purchase.placeholder.description')"
            />
            <input
              v-model.number="line.qty"
              type="number"
              step="0.01"
              class="form-input w-full"
              :placeholder="t('purchase.placeholder.qty')"
            />
            <input
              v-model.number="line.unit_price"
              type="number"
              step="0.01"
              class="form-input w-full"
              :placeholder="t('purchase.placeholder.unitPrice')"
            />
            <input
              v-model.number="line.vat_rate"
              type="number"
              step="0.01"
              class="form-input w-full"
              :placeholder="t('purchase.placeholder.vatRate')"
            />
            <Button
              variant="outline"
              theme="gray"
              :label="t('purchase.action.removeLine')"
              :disabled="draft.lines.length === 1"
              @click="removeLine(index)"
            />
          </div>
          <Button
            variant="outline"
            theme="gray"
            :label="t('purchase.action.addLine')"
            @click="addLine"
          />
        </div>

        <div style="margin-top: 16px; display: flex; gap: 8px; align-items: center">
          <Button
            variant="solid"
            theme="gray"
            class="pn-primary"
            :label="saving ? t('purchase.saving') : t('purchase.action.save')"
            :disabled="!canSubmit"
            @click="submit"
          />
          <span class="pn-muted">{{ t('purchase.hint.serverTotal') }}</span>
        </div>
      </div>

      <div style="margin-top: 16px">
        <div v-if="loading" style="display: flex; flex-direction: column; gap: 8px">
          <div class="pn-skeleton" v-for="i in 4" :key="i" />
        </div>
        <p v-else-if="!rows.length" class="pn-muted" style="margin: 0">
          {{ t('purchase.empty') }}
        </p>
        <table v-else class="pn-table">
          <thead>
            <tr>
              <th>{{ t('purchase.col.name') }}</th>
              <th>{{ t('purchase.field.supplier') }}</th>
              <th>{{ t('purchase.field.docNo') }}</th>
              <th>{{ t('purchase.col.date') }}</th>
              <th>{{ t('purchase.col.status') }}</th>
              <th class="pn-num">{{ t('purchase.col.grandTotal') }}</th>
              <th class="pn-num">{{ t('purchase.card.netPayable') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.name">
              <td>{{ row.name }}</td>
              <td>{{ row.supplier_name }}</td>
              <td>{{ row.doc_no || "—" }}</td>
              <td>{{ row.doc_date }}</td>
              <td>{{ row.status }}{{ row.payment_status ? ` / ${row.payment_status}` : "" }}</td>
              <td class="pn-num">{{ money(row.grand_total, row.currency) }}</td>
              <td class="pn-num">{{ money(row.net_payable, row.currency) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
