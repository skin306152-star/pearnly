// 本站白名单方法的调用（站点会话登录；写操作带 www/assistant 注入的 CSRF 令牌）。
// 浏览器从不直接连模型厂商：引擎与密钥只在服务端。

import { lang, t } from "@/i18n";

const BASE = "/api/method/pearnly_core.assistant.api.";

declare global {
  interface Window {
    csrf_token?: string;
  }
}

/** 请求头：页面当前的界面语言（pearnly_core.account.preferences.use_page_language）。 */
export const LANG_HEADER = "X-Pearnly-Lang";

/** 调本站接口一律经这里：带上页面当前的界面语言，这次请求的提示、步骤说明、术语、工具结果都按它，
 *  不按账号里存的另一种（同一台设备上看到的是同一种语言）。 */
export function siteFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = { ...(init.headers as Record<string, string> | undefined), [LANG_HEADER]: lang };
  return fetch(url, { credentials: "same-origin", ...init, headers });
}

/** 本站拒收时（frappe.throw）给的原文：已按用户语言翻译，可以直接给用户看。网络等别的失败是普通 Error。 */
export class SiteError extends Error {}

/** 页面上给用户看的报错：本站拒收（SiteError）照原文；别的失败（数据库冲突、网络断开、服务端原始异常，多是英文、带错误码）
 *  只说通用的一句，原文写进控制台（2026-10-15 生产：公司页、定时任务页把「(1213, 'Deadlock found…')」显示成红色横幅）。 */
export function shownError(error: unknown): string {
  if (error instanceof SiteError && error.message) return error.message;
  console.debug("[cc] request failed:", error);
  return t("somethingWrong");
}

function serverMessage(body: unknown): string {
  const b = (body ?? {}) as { _server_messages?: string };
  if (!b._server_messages) return "";
  try {
    const first = JSON.parse(JSON.parse(b._server_messages)[0]) as { message?: string };
    return first.message ? first.message.replace(/<[^>]+>/g, "") : "";
  } catch {
    return "";
  }
}

function errorOf(body: unknown, status: number): string {
  const b = (body ?? {}) as { exception?: string; message?: string };
  if (typeof b.exception === "string") return b.exception.split(":").slice(1).join(":").trim() || b.exception;
  return `request failed (${status})`;
}

// 登录过期：重新打开本页，由站点决定去哪（CC 入口，见 pearnly_core/login_redirect.py）。
// 403 也可能只是没有权限，先问一下本页是否要跳登录，只有要跳才走。
async function signedOut(status: number): Promise<boolean> {
  if (status === 401) return true;
  if (status !== 403) return false;
  const page = await fetch("/assistant", { redirect: "manual", cache: "no-store", credentials: "same-origin" }).catch(() => null);
  return page?.type === "opaqueredirect";
}

async function parse<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => null);
  if (await signedOut(res.status)) {
    location.href = "/assistant";
    await new Promise(() => {});
  }
  if (!res.ok) {
    const told = serverMessage(body);
    throw told ? new SiteError(told) : new Error(errorOf(body, res.status));
  }
  return (body as { message: T }).message;
}

export async function get<T>(method: string, args: Record<string, string | number | undefined> = {}): Promise<T> {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(args)) if (v !== undefined && v !== null) query.set(k, String(v));
  const res = await siteFetch(`${BASE}${method}?${query}`, { cache: "no-store" });
  return parse<T>(res);
}

export async function post<T>(method: string, args: Record<string, unknown> = {}): Promise<T> {
  const res = await siteFetch(`${BASE}${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": window.csrf_token ?? "" },
    body: JSON.stringify(args),
  });
  return parse<T>(res);
}

export async function upload<T>(method: string, args: Record<string, string>, file: File): Promise<T> {
  const form = new FormData();
  for (const [k, v] of Object.entries(args)) form.set(k, v);
  form.set("file", file, file.name);
  const res = await siteFetch(`${BASE}${method}`, {
    method: "POST",
    headers: { "X-Frappe-CSRF-Token": window.csrf_token ?? "" },
    body: form,
  });
  return parse<T>(res);
}

/** 其他 App 的白名单方法（例如 Cowork 的定时任务 `pearnly_cowork.folder_intake.api.*`），同一套登录与错误处理。 */
export async function getMethod<T>(method: string, args: Record<string, string | number | undefined> = {}): Promise<T> {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(args)) if (v !== undefined && v !== null) query.set(k, String(v));
  const res = await siteFetch(`/api/method/${method}?${query}`, { cache: "no-store" });
  return parse<T>(res);
}

export async function postMethod<T>(method: string, args: Record<string, unknown> = {}): Promise<T> {
  const res = await siteFetch(`/api/method/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": window.csrf_token ?? "" },
    body: JSON.stringify(args),
  });
  return parse<T>(res);
}

/** 用户在这台设备上选语言（左下角菜单「语言」、设置里的「语言」）：存成账号语言，也记在这台设备上
 *  （pearnly_core.account.preferences.set_language），再按新语言重新打开本页。 */
export async function chooseLanguage(code: string): Promise<void> {
  await siteFetch("/api/method/pearnly_core.account.preferences.set_language", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": window.csrf_token ?? "" },
    body: JSON.stringify({ language: code }),
  });
  location.reload();
}
