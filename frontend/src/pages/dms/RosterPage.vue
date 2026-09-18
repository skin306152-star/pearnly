<script setup lang="ts">
// DMS「操作员」花名册。
//
// 照老产品搬：`static/dms/dms-roster.js` + `dms-roster-html.js`。
// 页面职责（老产品的原话）：
//   副标题 —— "每行一位销售/管理员：各自 DMS 账密，逐行发 LINE 绑定码"
//   绑定码 —— 是**每一行**上的动作，不是单独一页：点「发绑定码」弹窗，
//              让对方在 LINE 加好友、把码发过来，可重新生成、显示剩余有效期。
//
// 老产品里这一页**仅老板（owner）可见**，员工直调后端会 403。这里前端照做
// （后端 `pearnly_dms.roster._require_admin` 才是真正的边界）。
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
import { Button, Dialog } from "frappe-ui";
import { call, ApiError } from "../../lib/api";

interface Operator {
  name: string;
  display_name: string;
  user: string;
  role: string;
  status: string;
  can_query_dms: boolean;
  dms_username: string;
  has_dms_password: boolean;
  advisor_id: string;
  advisor_name: string;
  line_bound: boolean;
  line_display_name: string;
  line_bound_at: string;
}

// 老产品就两种角色，没有第三种
const ROLE_LABEL: Record<string, string> = { sales: "销售", admin: "管理员" };

const rows = ref<Operator[]>([]);
const loading = ref(true);
const busy = ref("");
const error = ref("");
const notice = ref("");
const addOpen = ref(false);

const draft = reactive({
  display_name: "",
  dms_username: "",
  dms_password: "",
  dms_role: "sales",
  can_query_dms: false,
});

const canSubmit = computed(
  () =>
    draft.display_name.trim().length > 0 &&
    draft.dms_username.trim().length > 0 &&
    draft.dms_password.length > 0,
);

function explain(err: unknown): string {
  if (err instanceof ApiError) return err.message || `请求失败（HTTP ${err.status}）`;
  return String(err);
}

async function refresh(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const data = await call<{ items: Operator[] }>("pearnly_dms.roster.api_list_operators");
    rows.value = data.items || [];
  } catch (err) {
    error.value = explain(err);
  } finally {
    loading.value = false;
  }
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

function createOperator(): Promise<void> {
  return run("create", async () => {
    await call(
      "pearnly_dms.roster.api_create_operator",
      {
        display_name: draft.display_name.trim(),
        dms_username: draft.dms_username.trim(),
        dms_password: draft.dms_password,
        dms_role: draft.dms_role,
        can_query_dms: draft.can_query_dms ? 1 : 0,
      },
      { method: "POST" },
    );
    notice.value = "操作员已新增";
    addOpen.value = false;
    draft.display_name = "";
    draft.dms_username = "";
    draft.dms_password = "";
    draft.dms_role = "sales";
    draft.can_query_dms = false;
    await refresh();
  });
}

// ── 绑定码（行内动作）──────────────────────────────────────────────────────
const codeOpen = ref(false);
const codeTarget = ref<Operator | null>(null);
const code = ref("");
// 倒计时用**剩余秒数**，不用 expires_at 那个字符串：它是站点时区的裸时间，
// 浏览器按自己的时区解析会算错（实测站点时区 IST、浏览器 +07 → 刚生成的码就显示"已过期"）。
const codeRemaining = ref(0);
const remain = ref("");
let timer: number | undefined;

function stopTimer(): void {
  if (timer) {
    clearInterval(timer);
    timer = undefined;
  }
}

function tick(): void {
  if (codeRemaining.value <= 0) {
    remain.value = "已过期";
    stopTimer();
    return;
  }
  const sec = codeRemaining.value;
  remain.value = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  codeRemaining.value -= 1;
}

async function issueCode(row: Operator, open = true): Promise<void> {
  await run(`code:${row.name}`, async () => {
    const data = await call<{ code: string; expires_at: string; expires_in: number }>(
      "pearnly_dms.roster.api_operator_bind_code",
      { name: row.name },
      { method: "POST" },
    );
    code.value = data.code;
    codeRemaining.value = data.expires_in || 0;
    if (open) {
      codeTarget.value = row;
      codeOpen.value = true;
    }
    stopTimer();
    tick();
    timer = window.setInterval(tick, 1000);
  });
}

function regenCode(): void {
  if (codeTarget.value) void issueCode(codeTarget.value, false);
}

// ── 编辑 / 启停 / 删除 ─────────────────────────────────────────────────────
const editOpen = ref(false);
const editTarget = ref<Operator | null>(null);
const editForm = reactive({ dms_username: "", dms_password: "" });

function openEdit(row: Operator): void {
  editTarget.value = row;
  // 老产品：两个框都留空 = 不修改
  editForm.dms_username = "";
  editForm.dms_password = "";
  editOpen.value = true;
}

function saveEdit(): Promise<void> {
  const row = editTarget.value;
  return run(`edit:${row?.name}`, async () => {
    if (!row) return;
    if (!editForm.dms_username && !editForm.dms_password) {
      error.value = "没有要保存的改动";
      return;
    }
    await call(
      "pearnly_dms.roster.api_update_operator",
      {
        name: row.name,
        dms_username: editForm.dms_username || null,
        dms_password: editForm.dms_password || null,
      },
      { method: "POST" },
    );
    notice.value = "已保存";
    editOpen.value = false;
    await refresh();
  });
}

const confirmOpen = ref(false);
const confirmText = ref("");
const confirmAction = ref<() => Promise<void>>();

function ask(text: string, action: () => Promise<void>): void {
  confirmText.value = text;
  confirmAction.value = action;
  confirmOpen.value = true;
}

function runConfirm(): Promise<void> {
  const action = confirmAction.value;
  confirmOpen.value = false;
  return action ? action() : Promise.resolve();
}

function toggleStatus(row: Operator): Promise<void> {
  const toInactive = row.status === "active";
  return new Promise((resolve) => {
    ask(
      toInactive
        ? `确定停用「${row.display_name}」？停用后其 DMS 推送停止并解除 LINE 绑定。`
        : `确定启用「${row.display_name}」？启用后需重新发绑定码连接 LINE。`,
      () =>
        run(`status:${row.name}`, async () => {
          const res = await call<{ status: string }>(
            "pearnly_dms.roster.api_set_operator_status",
            { name: row.name, status: toInactive ? "inactive" : "active" },
            { method: "POST" },
          );
          notice.value = res.status === "active" ? "已启用" : "已停用";
          await refresh();
          resolve();
        }),
    );
  });
}

function removeOperator(row: Operator): Promise<void> {
  return new Promise((resolve) => {
    ask(
      `将解绑「${row.display_name}」的 LINE、移除 DMS 连接；推送记录将保留。确定删除？`,
      () =>
        run(`del:${row.name}`, async () => {
          await call(
            "pearnly_dms.roster.api_delete_operator",
            { name: row.name },
            { method: "POST" },
          );
          notice.value = "已删除";
          await refresh();
          resolve();
        }),
    );
  });
}

onMounted(refresh);
onUnmounted(stopTimer);
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
      <div style="display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap">
        <div>
          <div style="font-size: 16px; font-weight: 800">操作员</div>
          <div class="pn-muted">每行一位销售/管理员：各自 DMS 账密，逐行发 LINE 绑定码</div>
        </div>
        <div style="display: flex; gap: 8px">
          <Button
            variant="outline"
            theme="gray"
            :label="loading ? '加载中…' : '刷新'"
            :disabled="loading"
            @click="refresh"
          />
          <Button
            variant="solid"
            theme="gray"
            class="pn-primary"
            :label="addOpen ? '收起' : '新增操作员'"
            @click="addOpen = !addOpen"
          />
        </div>
      </div>

      <div
        v-if="addOpen"
        style="margin-top: 16px; border-top: 1px solid var(--line); padding-top: 16px"
      >
        <div style="font-weight: 800; margin-bottom: 4px">新增操作员</div>
        <div class="pn-muted" style="margin-bottom: 12px">
          填写显示名与该操作员自己的 DMS 账号密码，保存后逐行发绑定码
        </div>
        <div style="display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px">
          <label>
            <span class="pn-muted">显示名</span>
            <input v-model="draft.display_name" class="form-input w-full" placeholder="如：阿明（销售）" />
          </label>
          <label>
            <span class="pn-muted">DMS 用户名</span>
            <input v-model="draft.dms_username" class="form-input w-full" placeholder="该操作员的 DMS 账号" />
          </label>
          <label>
            <span class="pn-muted">DMS 密码</span>
            <input v-model="draft.dms_password" type="password" class="form-input w-full" placeholder="该操作员的 DMS 密码" />
          </label>
          <label>
            <span class="pn-muted">角色</span>
            <select v-model="draft.dms_role" class="form-input w-full">
              <option value="sales">销售</option>
              <option value="admin">管理员</option>
            </select>
          </label>
          <label style="display: flex; align-items: center; gap: 8px; align-self: end">
            <input v-model="draft.can_query_dms" type="checkbox" />
            <span class="pn-muted">允许在 LINE 使用查询功能</span>
          </label>
          <label>
            <span class="pn-muted">销售提成归属</span>
            <select class="form-input w-full" disabled>
              <option>自动匹配（按 DMS 账号）</option>
            </select>
            <span class="pn-muted" style="font-size: 11px">
              新平台还没有 DMS 顾问名册来源，暂时只能自动匹配
            </span>
          </label>
        </div>
        <div style="margin-top: 14px">
          <Button
            variant="solid"
            theme="gray"
            class="pn-primary"
            :label="busy === 'create' ? '保存中…' : '新增操作员'"
            :disabled="!canSubmit || busy === 'create'"
            @click="createOperator"
          />
        </div>
      </div>
    </div>

    <div class="pn-card">
      <div style="font-size: 16px; font-weight: 800; margin-bottom: 10px">操作员名单</div>
      <div v-if="loading" class="pn-skeleton" style="width: 200px" />
      <p v-else-if="!rows.length" class="pn-muted" style="margin: 0">
        还没有操作员，先在上方新增
      </p>
      <table v-else class="pn-table">
        <thead>
          <tr>
            <th>操作员</th>
            <th>角色</th>
            <th>查询权限</th>
            <th>LINE 状态</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.name">
            <td>
              <div style="font-weight: 700">{{ row.display_name }}</div>
              <div class="pn-muted" style="font-size: 11px">{{ row.dms_username || "—" }}</div>
            </td>
            <td>{{ ROLE_LABEL[row.role] ?? row.role }}</td>
            <td>{{ row.can_query_dms ? "允许" : "未允许" }}</td>
            <td>
              <span :style="{ color: row.line_bound ? 'var(--good)' : 'var(--muted)' }">
                {{ row.line_bound ? "已绑" : "未绑" }}
              </span>
            </td>
            <td>{{ row.status === "active" ? "启用" : "停用" }}</td>
            <td>
              <div style="display: flex; gap: 6px; flex-wrap: wrap">
                <Button
                  variant="outline"
                  theme="gray"
                  label="发绑定码"
                  :disabled="row.status !== 'active' || busy === `code:${row.name}`"
                  @click="issueCode(row)"
                />
                <Button variant="outline" theme="gray" label="编辑" @click="openEdit(row)" />
                <Button
                  variant="outline"
                  theme="gray"
                  :label="row.status === 'active' ? '停用' : '启用'"
                  @click="toggleStatus(row)"
                />
                <Button variant="outline" theme="gray" label="删除" @click="removeOperator(row)" />
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 绑定码弹窗：话术照老产品，不自己改 -->
    <Dialog v-model="codeOpen" :options="{ title: '绑定码', size: 'sm' }">
      <template #body-content>
        <div style="display: flex; flex-direction: column; gap: 12px">
          <div class="pn-muted">1. 让该操作员在 LINE 添加 Pearnly DMS 好友</div>
          <div class="pn-muted">2. 在 LINE 对话中发送此绑定码完成连接</div>
          <div
            style="
              text-align: center;
              font-size: 36px;
              font-weight: 800;
              letter-spacing: 8px;
              color: var(--accent);
            "
          >
            {{ code }}
          </div>
          <div class="pn-muted" style="text-align: center">
            <template v-if="remain && remain !== '已过期'">剩余有效期 {{ remain }}</template>
            <template v-else-if="remain">绑定码已过期，请重新生成</template>
          </div>
          <div style="display: flex; gap: 8px; justify-content: flex-end">
            <Button variant="outline" theme="gray" label="关闭" @click="codeOpen = false" />
            <Button variant="solid" theme="gray" class="pn-primary" label="重新生成" @click="regenCode" />
          </div>
        </div>
      </template>
    </Dialog>

    <!-- 编辑弹窗：两个框都留空 = 不修改 -->
    <Dialog v-model="editOpen" :options="{ title: '编辑操作员', size: 'sm' }">
      <template #body-content>
        <div style="display: flex; flex-direction: column; gap: 12px">
          <label>
            <span class="pn-muted">DMS 用户名</span>
            <input v-model="editForm.dms_username" class="form-input w-full" placeholder="留空则不修改" />
          </label>
          <label>
            <span class="pn-muted">新 DMS 密码</span>
            <input v-model="editForm.dms_password" type="password" class="form-input w-full" placeholder="留空则不修改" />
          </label>
          <div v-if="error" class="pn-error" role="alert">{{ error }}</div>
          <div style="display: flex; gap: 8px; justify-content: flex-end">
            <Button variant="outline" theme="gray" label="取消" @click="editOpen = false" />
            <Button variant="solid" theme="gray" class="pn-primary" label="保存" @click="saveEdit" />
          </div>
        </div>
      </template>
    </Dialog>

    <Dialog v-model="confirmOpen" :options="{ title: '请确认', size: 'sm' }">
      <template #body-content>
        <p style="margin: 0 0 12px">{{ confirmText }}</p>
        <div style="display: flex; gap: 8px; justify-content: flex-end">
          <Button variant="outline" theme="gray" label="取消" @click="confirmOpen = false" />
          <Button variant="solid" theme="gray" class="pn-primary" label="确定" @click="runConfirm" />
        </div>
      </template>
    </Dialog>
  </div>
</template>
