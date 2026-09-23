<script setup lang="ts">
// DMS「操作员」花名册。
//
// 照老产品搬：`static/dms/dms-roster.js` + `dms-roster-html.js`。
// 页面职责（老产品的原话）：
//   副标题 —— "{{ t('roster.subtitle') }}"
//   绑定码 —— 是**每一行**上的动作，不是单独一页：点「发绑定码」弹窗，
//              让对方在 LINE 加好友、把码发过来，可重新生成、显示剩余有效期。
//
// 老产品里这一页**仅老板（owner）可见**，员工直调后端会 403。这里前端照做
// （后端 `pearnly_dms.operators.roster._require_admin` 才是真正的边界）。
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
import {
  Card,
  Button,
  Dialog,
  Alert,
  LoadingIndicator,
  FormControl,
  ListView,
} from "frappe-ui";
import { createTexts } from "../../lib/i18n";
import { call, ApiError } from "../../lib/api";

// 页面文案走语言包（键 = `roster.*`）：一次取整包，页面上不维护"我要用哪些键"的清单。
const { t, tf, load: loadTexts } = createTexts([], "roster.");

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

// 老产品就两种角色，没有第三种。
//
// 这里存的是**文案编号**：语言包是异步取回来的，在这一行就把 `t()` 的结果存下来，
// 角色列会永远显示键名（`roster.role.sales`）—— 页面上真的这样显示过。
const ROLE_KEYS: Record<string, string> = {
  sales: "roster.role.sales",
  admin: "roster.role.admin",
};

/** 认不出的角色原样显示：宁可露出英文码，也不显示成空白。 */
function roleLabel(code: string): string {
  return ROLE_KEYS[code] ? t(ROLE_KEYS[code]) : code;
}

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
  if (err instanceof ApiError)
    return err.message || tf("roster.error.http", err.status);
  return String(err);
}

async function refresh(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const data = await call<{ items: Operator[] }>(
      "pearnly_dms.operators.roster.api_list_operators",
    );
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
      "pearnly_dms.operators.roster.api_create_operator",
      {
        display_name: draft.display_name.trim(),
        dms_username: draft.dms_username.trim(),
        dms_password: draft.dms_password,
        dms_role: draft.dms_role,
        can_query_dms: draft.can_query_dms ? 1 : 0,
      },
      { method: "POST" },
    );
    notice.value = t("roster.notice.created");
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
    remain.value = t("roster.code.expired");
    stopTimer();
    return;
  }
  const sec = codeRemaining.value;
  remain.value = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  codeRemaining.value -= 1;
}

async function issueCode(row: Operator, open = true): Promise<void> {
  await run(`code:${row.name}`, async () => {
    const data = await call<{
      code: string;
      expires_at: string;
      expires_in: number;
    }>(
      "pearnly_dms.operators.roster.api_operator_bind_code",
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
// 编辑只改密码：DMS 用户名由后台发号、绑死这个租户，之后不能改（用户 2026-09-23：
// "可以用用户名登录，但是不能修改…用户只可以自己改密码"）。所以这里**没有**用户名的框。
const editForm = reactive({ dms_password: "" });

function openEdit(row: Operator): void {
  editTarget.value = row;
  // 留空 = 不修改密码
  editForm.dms_password = "";
  editOpen.value = true;
}

function saveEdit(): Promise<void> {
  const row = editTarget.value;
  return run(`edit:${row?.name}`, async () => {
    if (!row) return;
    if (!editForm.dms_password) {
      error.value = t("roster.notice.noChanges");
      return;
    }
    await call(
      "pearnly_dms.operators.roster.api_update_operator",
      {
        name: row.name,
        dms_password: editForm.dms_password || null,
      },
      { method: "POST" },
    );
    notice.value = t("roster.notice.saved");
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
        ? tf("roster.confirm.disable", row.display_name)
        : tf("roster.confirm.enable", row.display_name),
      () =>
        run(`status:${row.name}`, async () => {
          const res = await call<{ status: string }>(
            "pearnly_dms.operators.roster.api_set_operator_status",
            { name: row.name, status: toInactive ? "inactive" : "active" },
            { method: "POST" },
          );
          notice.value =
            res.status === "active"
              ? t("roster.notice.enabled")
              : t("roster.notice.disabled");
          await refresh();
          resolve();
        }),
    );
  });
}

function removeOperator(row: Operator): Promise<void> {
  return new Promise((resolve) => {
    ask(tf("roster.confirm.delete", row.display_name), () =>
      run(`del:${row.name}`, async () => {
        await call(
          "pearnly_dms.operators.roster.api_delete_operator",
          { name: row.name },
          { method: "POST" },
        );
        notice.value = t("roster.notice.deleted");
        await refresh();
        resolve();
      }),
    );
  });
}

onMounted(() => {
  // 文案取不到不该把整页挡住：`t()` 取不到会显示键名 —— 看得见、可排查。
  loadTexts().catch(() => {});
  return refresh();
});
onUnmounted(stopTimer);
</script>

<template>
  <div style="display: flex; flex-direction: column; gap: 16px">
    <Alert v-if="error" theme="red" :title="error" />
    <Alert v-if="notice" theme="green" :title="notice" />

    <Card :title="t('roster.title')" class="">
      <div
        style="
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
        "
      >
        <div>
          <div class="pn-muted">{{ t("roster.subtitle") }}</div>
        </div>
        <div style="display: flex; gap: 8px">
          <Button
            variant="outline"
            theme="gray"
            :label="loading ? t('roster.loading') : t('roster.refresh')"
            :disabled="loading"
            @click="refresh"
          />
          <Button
            variant="solid"
            theme="gray"
            class="pn-primary"
            :label="addOpen ? t('roster.collapse') : t('roster.add')"
            @click="addOpen = !addOpen"
          />
        </div>
      </div>

      <div
        v-if="addOpen"
        style="
          margin-top: 16px;
          border-top: 1px solid var(--line);
          padding-top: 16px;
        "
      >
        <div style="font-weight: 800; margin-bottom: 4px">
          {{ t("roster.add") }}
        </div>
        <div class="pn-muted" style="margin-bottom: 12px">
          {{ t("roster.addHint") }}
        </div>
        <div
          style="
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 12px;
          "
        >
          <FormControl
            :label="t('roster.field.displayName')"
            v-model="draft.display_name"
            class="w-full"
            :placeholder="t('roster.hint.displayName')"
          />
          <FormControl
            :label="t('roster.field.dmsUsername')"
            v-model="draft.dms_username"
            class="w-full"
            :placeholder="t('roster.hint.dmsUsername')"
          />
          <FormControl
            :label="t('roster.field.dmsPassword')"
            v-model="draft.dms_password"
            type="password"
            class="w-full"
            :placeholder="t('roster.hint.dmsPassword')"
          />
          <FormControl
            :label="t('roster.field.role')"
            type="select"
            v-model="draft.dms_role"
            class="w-full"
            :options="[
              { value: 'sales', label: t('roster.role.sales') },
              { value: 'admin', label: t('roster.role.admin') },
            ]"
          />
          <FormControl
            v-model="draft.can_query_dms"
            type="checkbox"
            :label="t('roster.field.canQuery')"
          />
          <FormControl
            type="select"
            :label="t('roster.field.advisor')"
            :model-value="'auto'"
            disabled
            :options="[{ value: 'auto', label: t('roster.advisor.auto') }]"
            :description="t('roster.advisor.note')"
          />
        </div>
        <div style="margin-top: 14px">
          <Button
            variant="solid"
            theme="gray"
            class="pn-primary"
            :label="busy === 'create' ? t('roster.saving') : t('roster.add')"
            :disabled="!canSubmit || busy === 'create'"
            @click="createOperator"
          />
        </div>
      </div>
    </Card>

    <Card :title="t('roster.list.title')" class="">
      <LoadingIndicator v-if="loading" class="h-5 w-5" />
      <p v-else-if="!rows.length" class="pn-muted" style="margin: 0">
        {{ t("roster.list.empty") }}
      </p>
      <ListView
        v-else
        :columns="[
          { key: 'c0', label: t('roster.col.operator'), width: '150px' },
          { key: 'c1', label: t('roster.field.role'), width: '150px' },
          { key: 'c2', label: t('roster.col.query'), width: '150px' },
          { key: 'c3', label: t('roster.col.line'), width: '150px' },
          { key: 'c4', label: t('home.col.status'), width: '150px' },
          { key: 'c5', label: t('roster.col.actions'), width: '220px' },
        ]"
        :rows="rows"
        row-key="name"
        :options="{ selectable: false, showTooltip: false }"
      >
        <template #cell="{ column, row: row }">
          <span v-if="column.key === 'c0'">
            <div style="font-weight: 700">{{ row.display_name }}</div>
            <div class="pn-muted" style="font-size: 11px">
              {{ row.dms_username || "—" }}
            </div>
          </span>
          <span v-if="column.key === 'c1'">{{ roleLabel(row.role) }}</span>
          <span v-if="column.key === 'c2'">{{
            row.can_query_dms ? t("roster.allowQuery") : t("roster.noQuery")
          }}</span>
          <span v-if="column.key === 'c3'">
            <span
              :style="{
                color: row.line_bound ? 'var(--good)' : 'var(--muted)',
              }"
            >
              {{ row.line_bound ? t("roster.bound") : t("roster.unbound") }}
            </span>
          </span>
          <span v-if="column.key === 'c4'">{{
            row.status === "active" ? t("roster.enable") : t("roster.disable")
          }}</span>
          <span v-if="column.key === 'c5'">
            <div style="display: flex; gap: 6px; flex-wrap: wrap">
              <Button
                variant="outline"
                theme="gray"
                :label="t('roster.issueCode')"
                :disabled="
                  row.status !== 'active' || busy === `code:${row.name}`
                "
                @click="issueCode(row)"
              />
              <Button
                variant="outline"
                theme="gray"
                :label="t('roster.edit')"
                @click="openEdit(row)"
              />
              <Button
                variant="outline"
                theme="gray"
                :label="
                  row.status === 'active'
                    ? t('roster.disable')
                    : t('roster.enable')
                "
                @click="toggleStatus(row)"
              />
              <Button
                variant="outline"
                theme="gray"
                :label="t('roster.delete')"
                @click="removeOperator(row)"
              />
            </div>
          </span>
        </template>
      </ListView>
    </Card>

    <!-- 绑定码弹窗：话术照老产品，不自己改 -->
    <Dialog
      v-model="codeOpen"
      :options="{ title: t('roster.code.title'), size: 'sm' }"
    >
      <template #body-content>
        <div style="display: flex; flex-direction: column; gap: 12px">
          <div class="pn-muted">{{ t("roster.code.step1") }}</div>
          <div class="pn-muted">{{ t("roster.code.step2") }}</div>
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
            <template v-if="remain && remain !== t('roster.code.expired')">{{
              tf("roster.code.validFor", remain)
            }}</template>
            <template v-else-if="remain">{{
              t("roster.code.expiredHint")
            }}</template>
          </div>
          <div style="display: flex; gap: 8px; justify-content: flex-end">
            <Button
              variant="outline"
              theme="gray"
              :label="t('roster.close')"
              @click="codeOpen = false"
            />
            <Button
              variant="solid"
              theme="gray"
              class="pn-primary"
              :label="t('roster.regenerate')"
              @click="regenCode"
            />
          </div>
        </div>
      </template>
    </Dialog>

    <!-- 编辑弹窗：只改密码（DMS 用户名开户时定死） -->
    <Dialog
      v-model="editOpen"
      :options="{ title: t('roster.editTitle'), size: 'sm' }"
    >
      <template #body-content>
        <div style="display: flex; flex-direction: column; gap: 12px">
          <p class="text-sm text-ink-gray-7">
            {{ t("roster.field.dmsUsername") }}: {{ editTarget?.dms_username || "—" }}
          </p>
          <FormControl
            :label="t('roster.field.newDmsPassword')"
            v-model="editForm.dms_password"
            type="password"
            class="w-full"
            :placeholder="t('roster.keepBlank')"
          />
          <Alert v-if="error" theme="red" :title="error" />
          <div style="display: flex; gap: 8px; justify-content: flex-end">
            <Button
              variant="outline"
              theme="gray"
              :label="t('roster.cancel')"
              @click="editOpen = false"
            />
            <Button
              variant="solid"
              theme="gray"
              class="pn-primary"
              :label="t('roster.save')"
              @click="saveEdit"
            />
          </div>
        </div>
      </template>
    </Dialog>

    <Dialog
      v-model="confirmOpen"
      :options="{ title: t('roster.confirm.title'), size: 'sm' }"
    >
      <template #body-content>
        <p style="margin: 0 0 12px">{{ confirmText }}</p>
        <div style="display: flex; gap: 8px; justify-content: flex-end">
          <Button
            variant="outline"
            theme="gray"
            :label="t('roster.cancel')"
            @click="confirmOpen = false"
          />
          <Button
            variant="solid"
            theme="gray"
            class="pn-primary"
            :label="t('roster.ok')"
            @click="runConfirm"
          />
        </div>
      </template>
    </Dialog>
  </div>
</template>
