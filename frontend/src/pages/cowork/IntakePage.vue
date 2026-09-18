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
import { call, ApiError } from "../../lib/api";

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

// 状态机的中文说法：界面上不直接甩英文状态码给客户看
const STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  recognized: "已识别",
  confirmed: "已确认",
  queued: "待推送",
  pushed: "已推送",
  failed: "推送失败",
  rejected: "已退回",
};

const DIRECTION_LABEL: Record<string, string> = {
  purchase: "进项",
  sales: "销项",
};

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
  if (err instanceof ApiError) return err.message || `请求失败（HTTP ${err.status}）`;
  return String(err);
}

/** 当前状态下能做哪个动作。后端仍会自己检查一遍，这里只是不给人点死路。 */
function actions(row: IntakeRow): { key: string; label: string }[] {
  if (row.status === "draft" || row.status === "recognized") {
    return [{ key: "confirm", label: "确认" }];
  }
  if (row.status === "confirmed") {
    return [{ key: "queue", label: "推送" }];
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
    notice.value = `已录入：${name ?? ""}`;
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
    notice.value = `${row.name} → ${STATUS_LABEL[result.status] ?? result.status}`;
    await refresh();
  } catch (err) {
    error.value = explain(err);
  } finally {
    busy.value = "";
  }
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

    <div style="display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px">
      <div class="pn-card">
        <div class="pn-muted">待确认</div>
        <div style="font-size: 22px; font-weight: 800">
          {{ (summary?.by_status?.draft ?? 0) + (summary?.by_status?.recognized ?? 0) }}
        </div>
      </div>
      <div class="pn-card">
        <!-- 这一格数的是"已确认、还没排队"——那才是现在能推的东西。
             叫"待推送"会和状态里的 queued（已排队待推送）撞名，所以写清楚。 -->
        <div class="pn-muted">已确认 · 可推送</div>
        <div style="font-size: 22px; font-weight: 800">
          {{ summary?.by_status?.confirmed ?? 0 }}
        </div>
      </div>
      <div class="pn-card">
        <div class="pn-muted">进项合计（已确认及以上）</div>
        <div style="font-size: 22px; font-weight: 800">
          {{ summary?.purchase_confirmed ? money(summary.purchase_confirmed) : "—" }}
        </div>
      </div>
      <div class="pn-card">
        <div class="pn-muted">销项合计（已确认及以上）</div>
        <div style="font-size: 22px; font-weight: 800">
          {{ summary?.sales_confirmed ? money(summary.sales_confirmed) : "—" }}
        </div>
      </div>
    </div>

    <div class="pn-card">
      <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap">
        <div>
          <div style="font-size: 16px; font-weight: 800">录入工作台</div>
          <div class="pn-muted">
            上限：未确认的单据推不出去（状态机在后端卡着，不是靠界面按钮灰掉）
          </div>
        </div>
        <div style="display: flex; gap: 8px; align-items: center">
          <select v-model="filterDirection" class="form-input" @change="refresh">
            <option value="">全部方向</option>
            <option value="purchase">进项</option>
            <option value="sales">销项</option>
          </select>
          <select v-model="filterStatus" class="form-input" @change="refresh">
            <option value="">全部状态</option>
            <option v-for="(label, code) in STATUS_LABEL" :key="code" :value="code">{{ label }}</option>
          </select>
          <Button variant="outline" theme="gray" :label="loading ? '加载中…' : '刷新'" :disabled="loading" @click="refresh" />
          <Button
            variant="solid"
            theme="gray"
            class="pn-primary"
            :label="showForm ? '收起' : '新建录入'"
            @click="showForm = !showForm"
          />
        </div>
      </div>

      <div v-if="showForm" style="margin-top: 16px; border-top: 1px solid var(--line); padding-top: 16px">
        <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px">
          <label>
            <span class="pn-muted">进项 / 销项</span>
            <select v-model="draft.direction" class="form-input w-full">
              <option value="purchase">进项（收到的票）</option>
              <option value="sales">销项（自己开的票）</option>
            </select>
          </label>
          <label>
            <span class="pn-muted">来源</span>
            <select v-model="draft.source" class="form-input w-full">
              <option value="upload">上传</option>
              <option value="line">LINE</option>
              <option value="email">邮件</option>
              <option value="manual">手工</option>
            </select>
          </label>
          <label>
            <span class="pn-muted">对方名称</span>
            <input v-model="draft.counterparty_name" class="form-input w-full" placeholder="供应商 / 客户" />
          </label>
          <label>
            <span class="pn-muted">单据号</span>
            <input v-model="draft.doc_no" class="form-input w-full" placeholder="可留空" />
          </label>
          <label>
            <span class="pn-muted">单据日期</span>
            <input v-model="draft.doc_date" type="date" class="form-input w-full" />
          </label>
          <div />
          <label>
            <span class="pn-muted">未税金额</span>
            <input v-model.number="draft.net_amount" type="number" step="0.01" class="form-input w-full" />
          </label>
          <label>
            <span class="pn-muted">税额</span>
            <input v-model.number="draft.vat_amount" type="number" step="0.01" class="form-input w-full" />
          </label>
        </div>
        <div style="margin-top: 16px; display: flex; gap: 8px; align-items: center">
          <Button
            variant="solid"
            theme="gray"
            class="pn-primary"
            :label="busy === 'create' ? '提交中…' : '保存'"
            :disabled="!canSubmit"
            @click="submit"
          />
          <span class="pn-muted">保存后是「草稿」，确认之后才能推送</span>
        </div>
      </div>

      <div style="margin-top: 16px">
        <div v-if="loading" style="display: flex; flex-direction: column; gap: 8px">
          <div v-for="i in 4" :key="i" class="pn-skeleton" />
        </div>
        <p v-else-if="!rows.length" class="pn-muted" style="margin: 0">
          还没有记录。点右上角「新建录入」录第一张。
        </p>
        <table v-else class="pn-table">
          <thead>
            <tr>
              <th>单号</th>
              <th>方向</th>
              <th>对方</th>
              <th>单据号</th>
              <th>日期</th>
              <th>状态</th>
              <th class="pn-num">价税合计</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.name">
              <td>{{ row.name }}</td>
              <td>{{ DIRECTION_LABEL[row.direction] ?? row.direction }}</td>
              <td>{{ row.counterparty_name || "—" }}</td>
              <td>{{ row.doc_no || "—" }}</td>
              <td>{{ row.doc_date || "—" }}</td>
              <td>
                {{ STATUS_LABEL[row.status] ?? row.status }}
                <span v-if="row.push_status" class="pn-muted"> / {{ row.push_status }}</span>
              </td>
              <td class="pn-num">{{ money(row.grand_total, row.currency) }}</td>
              <td>
                <Button
                  v-for="action in actions(row)"
                  :key="action.key"
                  variant="outline"
                  theme="gray"
                  :label="busy === row.name ? '处理中…' : action.label"
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
