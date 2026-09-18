<script setup lang="ts">
// DMS「LINE 绑定码」。
//
// 为什么 DMS 网页端只剩这种页面：DMS 的业务（身份证录入、客户查询）都在 LINE
// 对话里完成，网页只负责"把我的账号和 LINE 接起来"（用户 2026-09-17 定：
// "DMS 身份证录入网页端不要了，业务几乎都在 LINE 完成，网页只负责绑定"）。
//
// 所以这一页只做两件事：给我一个一次性绑定码、告诉我现在绑了哪些 LINE 账号。
// 绑定码的规则（一次性消费、有效期、一人同时只有一个有效码）在后端
// `pearnly_core/line_binding.py` 里，界面不复制一份 —— 两边各写一套规则迟早不一致。
import { onMounted, ref } from "vue";
import { Button } from "frappe-ui";
import { call, ApiError } from "../../lib/api";

interface Binding {
  line_user_id: string;
  display_name: string;
  bound_at: string;
}

interface BindCode {
  code: string;
  expires_at: string;
  ttl_minutes?: number;
}

interface MyBindings {
  user: string;
  bindings: Binding[];
  pending_code: { code: string; expires_at: string } | null;
}

const bindings = ref<Binding[]>([]);
const code = ref<BindCode | null>(null);
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const notice = ref("");

function explain(err: unknown): string {
  if (err instanceof ApiError) return err.message || `请求失败（HTTP ${err.status}）`;
  return String(err);
}

/** 服务端给的是站点时区的 "YYYY-MM-DD HH:MM:SS"，显示到分钟就够。 */
function shortTime(value: string | null | undefined): string {
  return (value || "").slice(0, 16);
}

async function refresh(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const state = await call<MyBindings>("pearnly_core.line_binding.api_my_bindings");
    bindings.value = state.bindings || [];
    // 页面刷新后还能看到刚才那个码（只要没过期、没用掉）—— 避免"刷新一下就没了"
    code.value = state.pending_code
      ? { code: state.pending_code.code, expires_at: state.pending_code.expires_at }
      : null;
  } catch (err) {
    error.value = explain(err);
  } finally {
    loading.value = false;
  }
}

async function generate(): Promise<void> {
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    code.value = await call<BindCode>(
      "pearnly_core.line_binding.api_generate_bind_code",
      {},
      { method: "POST" },
    );
    notice.value = "新的绑定码已生成；之前没用过的码同时作废（同时只有一个有效码）。";
  } catch (err) {
    error.value = explain(err);
  } finally {
    busy.value = false;
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

    <div class="pn-card">
      <div style="font-size: 16px; font-weight: 800">把账号和 LINE 接起来</div>
      <p class="pn-muted" style="margin: 6px 0 14px">
        在网页上生成一个一次性绑定码，然后把这个码发给本站点的 LINE 官方号。
        绑定之后，工作就在 LINE 对话里完成，网页这边不用再录入。
      </p>

      <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
        <Button
          variant="solid"
          theme="gray"
          class="pn-primary"
          :label="busy ? '生成中…' : code ? '重新生成' : '生成绑定码'"
          :disabled="busy"
          @click="generate"
        />
        <Button
          variant="outline"
          theme="gray"
          :label="loading ? '加载中…' : '刷新'"
          :disabled="loading"
          @click="refresh"
        />
      </div>

      <div
        v-if="code"
        style="
          margin-top: 16px;
          border: 1px solid var(--line);
          border-radius: 12px;
          padding: 16px;
          text-align: center;
        "
      >
        <div class="pn-muted">把这个码发给 LINE 官方号</div>
        <div
          style="
            font-size: 40px;
            font-weight: 800;
            letter-spacing: 10px;
            margin: 10px 0 6px;
            color: var(--accent);
          "
        >
          {{ code.code }}
        </div>
        <div class="pn-muted">有效期至 {{ shortTime(code.expires_at) }}，用掉或过期即作废</div>
      </div>
    </div>

    <div class="pn-card">
      <div style="font-size: 16px; font-weight: 800; margin-bottom: 10px">已绑定的 LINE 账号</div>
      <div v-if="loading" class="pn-skeleton" style="width: 180px" />
      <p v-else-if="!bindings.length" class="pn-muted" style="margin: 0">
        还没有绑定。生成绑定码发给 LINE 官方号，就能绑上。
      </p>
      <table v-else class="pn-table">
        <thead>
          <tr>
            <th>LINE 账号</th>
            <th>用户标识</th>
            <th>绑定时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in bindings" :key="row.line_user_id">
            <td>{{ row.display_name || "（没有昵称）" }}</td>
            <td class="pn-num">{{ row.line_user_id }}</td>
            <td class="pn-num">{{ shortTime(row.bound_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
