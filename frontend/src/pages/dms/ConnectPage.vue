<script setup lang="ts">
// DMS「连接第三方 DMS」—— 开户之后租户要做的第一件事。
//
// 照老产品搬：`static/dms/dms-connect.js` + 设置页那张连接卡。
// 老产品行为铁律：
//   · DMS 地址写死隐藏 —— 只此一个实例，向导只维护一组有修改权限的账号密码；
//   · 同一组凭据同时当主凭据和管理员凭据（销售用各自账号另建操作员行）；
//   · **先测再存**：拿刚填的账号密码真登一次（并把主档抓回来），登上了才写库。
//     所以不会出现"保存成功但其实连不上"。
// 抓不到主档的"连接成功"是假成功 —— 老产品就是这么判的，这里照做。
//
// 界面上**不放解释性文案**：卡片只列字段，结果只说结果。
import { computed, onMounted, reactive, ref } from "vue";
import { Button, Dialog } from "frappe-ui";
import { call, ApiError } from "../../lib/api";

interface EndpointRow {
  name: string;
  label: string;
  adapter: string;
  enabled: boolean;
  system_url: string;
  username: string;
  has_password: boolean;
  has_admin_creds: boolean;
  last_status: string;
  last_used_at: string;
  success_count: number;
  failure_count: number;
}

interface Connection {
  connected: boolean;
  default_url: string;
  endpoint?: EndpointRow;
}

interface Masters {
  advisors?: unknown[];
  car_models?: unknown[];
  place_books?: unknown[];
  term_sales?: unknown[];
  branches?: unknown[];
  regis_behalfs?: unknown[];
}

interface Probe {
  ok: boolean;
  elapsed_ms?: number;
  defaults_required?: boolean;
  masters?: Masters;
  error_code?: string;
  error_friendly?: string;
}

const conn = ref<Connection | null>(null);
const loading = ref(true);
const busy = ref("");
const error = ref("");
const notice = ref("");

const open = ref(false);
const form = reactive({ username: "", password: "" });

const configured = computed(() => !!conn.value?.connected && !!conn.value?.endpoint);
const ep = computed(() => conn.value?.endpoint ?? null);

const canSubmit = computed(() => form.username.trim().length > 0 && form.password.length > 0);

function explain(err: unknown): string {
  if (err instanceof ApiError) return err.message || `请求失败（HTTP ${err.status}）`;
  return String(err);
}

async function refresh(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    conn.value = await call<Connection>("pearnly_dms.api.get_connection");
  } catch (err) {
    error.value = explain(err);
  } finally {
    loading.value = false;
  }
}

async function run(key: string, fn: () => Promise<void>): Promise<void> {
  busy.value = key;
  error.value = "";
  try {
    await fn();
  } catch (err) {
    error.value = explain(err);
  } finally {
    busy.value = "";
  }
}

function openWizard(): void {
  // 老产品：编辑态两个框都留空 = 改密码才填；这里沿用。
  form.username = configured.value ? (ep.value?.username ?? "") : "";
  form.password = "";
  error.value = "";
  open.value = true;
}

/** 「连接并保存」：真登一次 → 成功才写库。 */
function connectAndSave(): Promise<void> {
  return run("save", async () => {
    const username = form.username.trim();
    const password = form.password;
    const probe = await call<Probe>(
      "pearnly_dms.api.test_credentials",
      { username, password },
      { method: "POST" },
    );
    if (!probe.ok) {
      error.value = probe.error_friendly || "连接失败";
      return;
    }
    await call("pearnly_dms.api.save_connection", { username, password }, { method: "POST" });
    notice.value = `已连接 · ${masterSummary(probe)}`;
    open.value = false;
    form.password = "";
    await refresh();
  });
}

function testSaved(): Promise<void> {
  return run("test", async () => {
    const name = ep.value?.name;
    if (!name) return;
    const probe = await call<Probe>(
      "pearnly_dms.api.test_endpoint",
      { endpoint: name },
      { method: "POST" },
    );
    if (probe.ok) notice.value = `连接正常 · ${masterSummary(probe)}`;
    else error.value = probe.error_friendly || "连接失败";
    await refresh();
  });
}

function toggleEnabled(): Promise<void> {
  return run("toggle", async () => {
    const next = !ep.value?.enabled;
    await call("pearnly_dms.api.set_connection_enabled", { enabled: next ? 1 : 0 }, { method: "POST" });
    notice.value = next ? "已启用" : "已停用";
    await refresh();
  });
}

function masterSummary(p: Probe): string {
  const m = p.masters ?? {};
  return [
    `车型 ${m.car_models?.length ?? 0}`,
    `分店 ${m.branches?.length ?? 0}`,
    `顾问 ${m.advisors?.length ?? 0}`,
    `称谓 ${m.term_sales?.length ?? 0}`,
  ].join(" · ");
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
      <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap">
        <div style="font-size: 16px; font-weight: 800">{{ ep?.label || "连接第三方 DMS" }}</div>
        <div style="display: flex; gap: 8px">
          <Button
            v-if="configured"
            variant="outline"
            theme="gray"
            :label="busy === 'test' ? '测试中…' : '测试连接'"
            :disabled="busy === 'test'"
            @click="testSaved"
          />
          <Button
            v-if="configured"
            variant="outline"
            theme="gray"
            :label="busy === 'toggle' ? '处理中…' : ep?.enabled ? '停用' : '启用'"
            :disabled="busy === 'toggle'"
            @click="toggleEnabled"
          />
          <Button
            variant="solid"
            theme="gray"
            class="pn-primary"
            :label="configured ? '修改账号' : '连接 DMS'"
            @click="openWizard"
          />
        </div>
      </div>

      <div v-if="loading" class="pn-skeleton" style="width: 220px; margin-top: 14px" />
      <div v-else class="pn-fields">
        <div class="pn-label">状态</div>
        <div :style="{ color: ep?.enabled ? 'var(--good)' : 'var(--ink3)', fontWeight: 700 }">
          {{ configured ? (ep?.enabled ? "已连接" : "已停用") : "未连接" }}
        </div>
        <div class="pn-label">账号</div>
        <div>{{ ep?.username || "—" }}</div>

        <div class="pn-label">地址</div>
        <div>{{ ep?.system_url || "—" }}</div>
        <div class="pn-label">有权限账号</div>
        <div>{{ ep?.has_admin_creds ? "是" : "否" }}</div>

        <div class="pn-label">最近结果</div>
        <div>{{ ep?.last_status || "—" }}</div>
        <div class="pn-label">成功 / 失败</div>
        <div>{{ ep?.success_count ?? 0 }} / {{ ep?.failure_count ?? 0 }}</div>
      </div>
    </div>

    <!-- 向导弹窗：地址写死隐藏，只填账号密码 -->
    <Dialog v-model="open" :options="{ title: configured ? '修改账号' : '连接 DMS', size: 'sm' }">
      <template #body-content>
        <div class="pn-form">
          <label class="pn-label">账号</label>
          <input v-model="form.username" class="form-input" placeholder="DMS 登录账号" />
          <label class="pn-label">密码</label>
          <input
            v-model="form.password"
            type="password"
            class="form-input"
            placeholder="DMS 登录密码"
            autocomplete="new-password"
          />
          <div v-if="error" class="pn-error" style="grid-column: 1 / -1" role="alert">
            {{ error }}
          </div>
          <div style="display: flex; gap: 8px; justify-content: flex-end">
            <Button variant="outline" theme="gray" label="取消" @click="open = false" />
            <Button
              variant="solid"
              theme="gray"
              class="pn-primary"
              :label="busy === 'save' ? '连接中…' : '连接并保存'"
              :disabled="!canSubmit || busy === 'save'"
              @click="connectAndSave"
            />
          </div>
        </div>
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
/* 字段对齐：标签固定宽度、值左对齐；一行两组。 */
.pn-fields {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr) 84px minmax(0, 1.4fr);
  align-items: center;
  column-gap: 12px;
  row-gap: 10px;
  margin-top: 14px;
  font-size: 13px;
  word-break: break-all;
}
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
</style>
