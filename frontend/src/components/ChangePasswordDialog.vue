<script setup lang="ts">
// 改密弹窗。三个产品共用同一个接口（`pearnly_core.password.change_password`，
// 它在共用层里），所以这套界面只要写一次。
//
// 用户当时的要求："账号能自定义自己改密码，放在头像点开密码更改里，
// 输入当前账号、新密码、确定新密码，这套逻辑三个产品共用。"
//
// 校验不在这里自己写：后端已经管了"三项都要填 / 两次不一致 /
// 不能与当前相同 / 当前密码是否正确"。前端把它的报错原样显示出来就行 ——
// 在两边各写一套规则，迟早会不一致。
//
// **密码强度前端也不卡**（用户 2026-09-18 要求："密码不要卡得太严格，
// 纯字母，纯数字都可以，包括首次使用"）：这里只提示"两次输入不一致"，
// 不再提示长度，免得出现"界面说还差几位、其实后端已经收下"的错配。
import { computed, onMounted, ref, watch } from "vue";
import { Button, Dialog } from "frappe-ui";
import { createTexts } from "../lib/i18n";
import { call, ApiError } from "../lib/api";

// 弹窗文案走语言包（键 = `account.*`）。
const { t, tf, load: loadTexts } = createTexts([], "account.");

const open = defineModel<boolean>("open", { required: true });
const emit = defineEmits<{ changed: [] }>();

const currentPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const saving = ref(false);
const error = ref("");
const done = ref("");

const hint = computed(() => {
  if (confirmPassword.value && newPassword.value !== confirmPassword.value) {
    return t("account.pwd.mismatch");
  }
  return "";
});

const canSubmit = computed(
  () =>
    currentPassword.value.length > 0 &&
    newPassword.value.length > 0 &&
    confirmPassword.value.length > 0 &&
    !saving.value,
);

// 每次打开都清空：密码字段留在界面上没有任何好处
watch(open, (isOpen) => {
  if (isOpen) {
    currentPassword.value = "";
    newPassword.value = "";
    confirmPassword.value = "";
    error.value = "";
    done.value = "";
  }
});

function explain(err: unknown): string {
  if (err instanceof ApiError) return err.message || tf("account.error.http", err.status);
  return String(err);
}

onMounted(() => {
  // 文案取不到不该把整页挡住：`t()` 取不到会显示键名 —— 看得见、可排查。
  loadTexts().catch(() => {});
});

async function submit(): Promise<void> {
  saving.value = true;
  error.value = "";
  done.value = "";
  try {
    await call(
      "pearnly_core.password.change_password",
      {
        current_password: currentPassword.value,
        new_password: newPassword.value,
        confirm_password: confirmPassword.value,
      },
      { method: "POST" },
    );
    done.value = t("account.pwd.done");
    emit("changed");
    setTimeout(() => {
      open.value = false;
    }, 1200);
  } catch (err) {
    error.value = explain(err);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <Dialog v-model="open" :options="{ title: t('account.changePassword'), size: 'sm' }">
    <template #body-content>
      <div style="display: flex; flex-direction: column; gap: 12px">
        <label>
          <span class="pn-muted">{{ t('account.pwd.current') }}</span>
          <input
            v-model="currentPassword"
            type="password"
            autocomplete="current-password"
            class="form-input w-full"
          />
        </label>
        <label>
          <span class="pn-muted">{{ t('account.pwd.new') }}</span>
          <input
            v-model="newPassword"
            type="password"
            autocomplete="new-password"
            class="form-input w-full"
          />
        </label>
        <label>
          <span class="pn-muted">{{ t('account.pwd.confirm') }}</span>
          <input
            v-model="confirmPassword"
            type="password"
            autocomplete="new-password"
            class="form-input w-full"
          />
        </label>

        <p v-if="hint" class="pn-muted" style="margin: 0">{{ hint }}</p>
        <div v-if="error" class="pn-error" role="alert">{{ error }}</div>
        <p v-if="done" style="margin: 0; color: var(--good); font-weight: 700; font-size: 13px">
          {{ done }}
        </p>

        <div style="display: flex; gap: 8px; justify-content: flex-end; margin-top: 4px">
          <Button
            variant="outline"
            theme="gray"
            :label="t('account.cancel')"
            @click="open = false"
          />
          <Button
            variant="solid"
            theme="gray"
            class="pn-primary"
            :label="saving ? t('account.saving') : t('account.save')"
            :disabled="!canSubmit"
            @click="submit"
          />
        </div>
      </div>
    </template>
  </Dialog>
</template>
