// 叫人判据的页面一半（弹窗通知并进对话 C-4，用户 11-14 口径：人正看着那段对话就不响、不通知，不在才叫；服务端判据
// pearnly_core.assistant.attention，总设计 15.2）。这页报「此刻在看哪段对话、看不看得见」：
//   - 服务端 attention_seen：切对话、显示 / 隐藏、拿到 / 失去焦点时报一次（300ms 内合并），看得见期间每 30 秒续一次
//     （服务端 75 秒没续算不在）；隐藏时报一次「不在」就停，空闲不发请求；
//   - CC 桌面版桥 attention_seen（{session, visible}，同 CC 桌面版窗口 11-17 约定）：只在变化时报，托盘据此判这条提醒弹不弹；
//     旧版桌面端不认这个方法时吞掉、页面照常。
// 设备编号：这台浏览器自己记的一串随机字（服务端按它分设备，同一个人几台设备各报各的）。

import { attentionSeen } from "./managed-agents/api";
import { onViewChange, openSession } from "./managed-agents/activity";
import { desktop } from "./desktop";
import { reportCaught, withReport } from "./caught";

export const RENEW_MS = 30_000;
export const SETTLE_MS = 300;
const DEVICE_KEY = "cc-attention-device";

/** 这台设备的编号（存在本机；存不了就本页内一个）。 */
export function deviceId(store: Pick<Storage, "getItem" | "setItem"> | null = safeStorage()): string {
  const made = () => Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
  try {
    const found = store?.getItem(DEVICE_KEY);
    if (found && /^[A-Za-z0-9_-]{1,64}$/.test(found)) return found;
    const fresh = made();
    store?.setItem(DEVICE_KEY, fresh);
    return fresh;
  } catch (caughtError) { reportCaught(caughtError, "attention");
    return made();
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch (caughtError) { reportCaught(caughtError, "attention");
    return null;
  }
}

/** 此刻报什么：打开着的对话，看得见 = 页面在前台且有焦点。 */
export function currentLook(): { session: string; visible: boolean } {
  const visible = document.visibilityState === "visible" && document.hasFocus();
  return { session: openSession(), visible };
}

/** 页面挂一次：变化时报（合并 300ms），看得见期间每 30 秒续服务端。回撤掉的函数。 */
export function watchAttention(): () => void {
  if (typeof document === "undefined") return () => undefined;
  const device = deviceId();
  let last = "";
  let settle = 0;
  let renew = 0;
  const toServer = (look: { session: string; visible: boolean }) => {
    if (!look.session && !last) return;
    void attentionSeen(look.session, look.visible && !!look.session, device).catch(withReport(() => undefined, "attention"));
  };
  const toDesktop = (look: { session: string; visible: boolean }) =>
    void desktop()
      .then((api) => api?.call("attention_seen", look))
      .catch(withReport(() => undefined, "attention"));
  const report = () => {
    settle = 0;
    const look = currentLook();
    const key = `${look.session}|${look.visible}`;
    window.clearInterval(renew);
    renew = 0;
    if (look.visible && look.session) renew = window.setInterval(() => toServer(currentLook()), RENEW_MS);
    if (key === last) return;
    toServer(look);
    toDesktop(look);
    last = key;
  };
  const soon = () => {
    window.clearTimeout(settle);
    settle = window.setTimeout(report, SETTLE_MS);
  };
  const offView = onViewChange(soon);
  document.addEventListener("visibilitychange", soon);
  window.addEventListener("focus", soon);
  window.addEventListener("blur", soon);
  soon();
  return () => {
    offView();
    document.removeEventListener("visibilitychange", soon);
    window.removeEventListener("focus", soon);
    window.removeEventListener("blur", soon);
    window.clearTimeout(settle);
    window.clearInterval(renew);
  };
}
