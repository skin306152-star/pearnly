<script setup lang="ts">
// DMS「推送记录」。
//
// 这一页是**只读**的，而且只能读：DMS 的身份证录入作业在 LINE 上完成，所以网页端
// 不给"新建" —— 后端也没给这张表 create 权限（见 pearnly_control/features.py 的
// READ_ONLY_ON_WEB）。界面和后端两边一致，不是靠把按钮灰掉。
//
// 列表接口本身就不返回证件号（只返回末四位用于核对），这一页连末四位也不展示：
// 推送记录要看的是"推没推成功、推给谁"，不是证件信息。
import { computed, onMounted, ref } from "vue";
import { Button } from "frappe-ui";
import { call, ApiError } from "../../lib/api";

interface IntakeRow {
  name: string;
  status: string;
  match_result: string;
  endpoint: string | null;
  external_ref: string | null;
  full_name: string | null;
  birth_date: string | null;
  push_log_ref: string | null;
  creation: string;
}

interface Summary {
  total: number;
  by_status: Record<string, number>;
  new_customers: number;
  reused_customers: number;
}

// 状态码的中文说法（与后端 pearnly_dms/id_card.py 的状态机一一对应）
const STATUS_LABEL: Record<string, string> = {
  uploaded: "已上传",
  recognized: "已识别",
  matched: "已匹配",
  confirmed: "已确认",
  pushed: "已推送",
};

const MATCH_LABEL: Record<string, string> = {
  unknown: "未匹配",
  existing: "已有客户",
  new: "新客户",
};

const rows = ref<IntakeRow[]>([]);
const summary = ref<Summary | null>(null);
const filterStatus = ref("");
const loading = ref(true);
const error = ref("");

function explain(err: unknown): string {
  if (err instanceof ApiError) return err.message || `请求失败（HTTP ${err.status}）`;
  return String(err);
}

function shortTime(value: string | null | undefined): string {
  return (value || "").slice(0, 16);
}

const pushedCount = computed(() => summary.value?.by_status?.pushed ?? 0);

async function refresh(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const [list, sum] = await Promise.all([
      call<IntakeRow[]>("pearnly_dms.id_card.api_list_intake", {
        status: filterStatus.value || undefined,
        limit: 100,
      }),
      call<Summary>("pearnly_dms.id_card.api_summary"),
    ]);
    rows.value = list;
    summary.value = sum;
  } catch (err) {
    error.value = explain(err);
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);
</script>

<template>
  <div style="display: flex; flex-direction: column; gap: 16px">
    <div v-if="error" class="pn-error" role="alert">{{ error }}</div>

    <div style="display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px">
      <div class="pn-card">
        <div class="pn-muted">记录总数</div>
        <div style="font-size: 22px; font-weight: 800">{{ summary?.total ?? "—" }}</div>
      </div>
      <div class="pn-card">
        <div class="pn-muted">已推送 DMS</div>
        <div style="font-size: 22px; font-weight: 800">{{ pushedCount }}</div>
      </div>
      <div class="pn-card">
        <div class="pn-muted">新客户</div>
        <div style="font-size: 22px; font-weight: 800">{{ summary?.new_customers ?? "—" }}</div>
      </div>
      <div class="pn-card">
        <div class="pn-muted">已有客户（复用）</div>
        <div style="font-size: 22px; font-weight: 800">{{ summary?.reused_customers ?? "—" }}</div>
      </div>
    </div>

    <div class="pn-card">
      <div
        style="
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
        "
      >
        <div>
          <div style="font-size: 16px; font-weight: 800">推送记录</div>
          <div class="pn-muted">
            只读：身份证录入在 LINE 上完成，网页这边不再提供录入入口
          </div>
        </div>
        <div style="display: flex; gap: 8px; align-items: center">
          <select v-model="filterStatus" class="form-input" @change="refresh">
            <option value="">全部状态</option>
            <option v-for="(label, code) in STATUS_LABEL" :key="code" :value="code">
              {{ label }}
            </option>
          </select>
          <Button
            variant="outline"
            theme="gray"
            :label="loading ? '加载中…' : '刷新'"
            :disabled="loading"
            @click="refresh"
          />
        </div>
      </div>

      <div v-if="loading" class="pn-skeleton" style="margin-top: 16px; width: 240px" />
      <p v-else-if="!rows.length" class="pn-muted" style="margin: 16px 0 0">
        还没有记录。在 LINE 里完成录入之后，这里会出现对应的推送记录。
      </p>
      <table v-else class="pn-table" style="margin-top: 12px">
        <thead>
          <tr>
            <th>编号</th>
            <th>状态</th>
            <th>客户匹配</th>
            <th>姓名</th>
            <th>DMS 客户引用</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.name">
            <td class="pn-num">{{ row.name }}</td>
            <td>{{ STATUS_LABEL[row.status] ?? row.status }}</td>
            <td>{{ MATCH_LABEL[row.match_result] ?? row.match_result }}</td>
            <td>{{ row.full_name || "—" }}</td>
            <td class="pn-num">{{ row.external_ref || "—" }}</td>
            <td class="pn-num">{{ shortTime(row.creation) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
