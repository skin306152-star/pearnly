// 本站白名单方法的调用（站点会话登录；写操作带 www/assistant 注入的 CSRF 令牌）。
// 浏览器从不直接连模型厂商：引擎与密钥只在服务端。

import { lang, t } from "@/i18n";
import { withReport } from "./caught";

const BASE = "/api/method/pearnly_core.assistant.api.";

declare global {
  interface Window {
    csrf_token?: string;
    /** 一份附件最多多大（字节）：页面由服务端写进来（`www/assistant.py`，共用层 `originals.rules.UPLOAD_MAX_BYTES`）。 */
    pearnly_upload_max_bytes?: number;
    /** 附件收哪些类型（小写扩展名，带点）：同样由服务端写进来（`assistant.contracts.UPLOAD_EXTENSIONS`）。 */
    pearnly_upload_extensions?: readonly string[];
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
export class SiteError extends Error {
  // 名字写明：出错回传（lib/caught.ts 交给站点页面的那一处）按名字认出「站点已拒收、页面已提示」，不再记进站点错误日志。
  // 以前没写（name 是 "Error"），每次带着传不上的附件点发送都往站点错误日志写一条（11-11 发布核查）
  override name = "SiteError";
}

/** 站点回了错误码、又没有给人看的原文（服务端原始异常、网关错误）：带上 HTTP 状态，调用方据此分辨是不是一时的。 */
export class RequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

// 一时的失败：网络断开 / 请求没发出去（fetch 抛 TypeError）、网关与服务端错误（5xx、408、429）、数据库死锁或等锁超时
// （1213 / 1205，2026-10-15 生产公司页出现过）。本站拒收（SiteError，带原文）不是。
const TRANSIENT_STATUS = new Set([408, 429]);
const TRANSIENT_DB = /\b(1213|1205)\b|Deadlock found|Lock wait timeout/;

export function transientError(error: unknown): boolean {
  if (error instanceof SiteError) return false;
  if (error instanceof TypeError) return true;
  if (error instanceof RequestError) return error.status >= 500 || TRANSIENT_STATUS.has(error.status) || TRANSIENT_DB.test(error.message);
  return false;
}

/** 只读、可重复的请求碰到一时的失败（transientError）时隔一会儿再取，最多 ``tries`` 次；其余失败与最后一次照常抛出。 */
export async function withRetry<T>(run: () => Promise<T>, tries = 3, waitMs = 600): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await run();
    } catch (error) {
      if (attempt >= tries || !transientError(error)) throw error;
      await new Promise((resolve) => setTimeout(resolve, waitMs * attempt));
    }
  }
}

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
  const page = await fetch("/assistant", { redirect: "manual", cache: "no-store", credentials: "same-origin" }).catch(withReport(() => null, "frappe"));
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
    throw told ? new SiteError(told) : new RequestError(errorOf(body, res.status), res.status);
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

/** 上传进度：已传给服务器的字节占整份请求的比例（0–1，浏览器给的真数，不估算）。 */
export type UploadProgress = (fraction: number) => void;

export async function upload<T>(method: string, args: Record<string, string>, file: File, onProgress?: UploadProgress): Promise<T> {
  return postForm<T>(`${BASE}${method}`, args, file, onProgress);
}

/** 人交来的一份文件最多多大：服务端写进页面的那个数（同 Frappe 请求体上限、租户站 nginx 一个来源）；页面没写就是 0，不在页面先拦。 */
export function uploadMaxBytes(): number {
  const max = Number(window.pearnly_upload_max_bytes);
  return Number.isFinite(max) && max > 0 ? max : 0;
}

/** 附件收哪些类型（小写扩展名，带点）：服务端写进页面的那份；页面没写就是空表，不在页面先拦类型。 */
export function uploadExtensions(): readonly string[] {
  const list = window.pearnly_upload_extensions;
  return Array.isArray(list) ? list.map((ext) => String(ext).toLowerCase()) : [];
}

/** 超限时给人看的那句（同服务端词条 upload.tooLarge）。 */
export function tooLargeText(): string {
  const max = uploadMaxBytes();
  return max ? t("fileTooLarge").replace("{0}", String(Math.floor(max / (1024 * 1024)))) : t("uploadFailed");
}

/** 上传一个文件（字段 file，其余参数同表单一起交）：附件与「实操」资料都走这里。超限的先在页面拦下、不白传；
 *  网关或站点回 413（请求体超限，回的是网页、没有原文）也照实说超限，不说「出了点问题」（11-10 压测）。 */
async function postForm<T>(url: string, args: Record<string, string>, file: File, onProgress?: UploadProgress): Promise<T> {
  const max = uploadMaxBytes();
  if (max && file.size > max) throw new SiteError(tooLargeText());
  const form = new FormData();
  for (const [k, v] of Object.entries(args)) form.set(k, v);
  form.set("file", file, file.name);
  const headers = { "X-Frappe-CSRF-Token": window.csrf_token ?? "" };
  // 没有 XMLHttpRequest 的环境（单测的 node）照旧走 fetch，只是没有进度
  const res = onProgress && typeof XMLHttpRequest !== "undefined"
    ? await postWithProgress(url, headers, form, onProgress)
    : await siteFetch(url, { method: "POST", headers, body: form });
  if (res.status === 413) throw new SiteError(tooLargeText());
  return parse<T>(res);
}

/** 同 siteFetch 的 POST（同源带登录、同样的语言头），换成 XMLHttpRequest：fetch 拿不到上传了多少字节，XHR 的 upload.onprogress
 *  给真数（上传研究 U1，docs/work/2026-11-08-cc-upload-ux-研究.md）。回包照样做成 Response 交给 parse，错误处理同一套；
 *  连不上 / 断线同 fetch 抛 TypeError。 */
function postWithProgress(url: string, headers: Record<string, string>, body: FormData, onProgress: UploadProgress): Promise<Response> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    for (const [k, v] of Object.entries({ ...headers, [LANG_HEADER]: lang })) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) onProgress(Math.min(1, e.loaded / e.total));
    };
    const failed = () => reject(new TypeError("Failed to fetch"));
    xhr.onerror = failed;
    xhr.onabort = failed;
    xhr.ontimeout = failed;
    xhr.onload = () => {
      // Response 只收 200–599；别的（0 等）当连不上
      if (xhr.status < 200 || xhr.status > 599) return failed();
      resolve(new Response(xhr.status === 204 ? null : xhr.responseText, { status: xhr.status, statusText: xhr.statusText }));
    };
    xhr.send(body);
  });
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

/** 其他 App 的白名单方法上传一个文件（同附件上传那条路：multipart、CSRF、同一套错误处理）。 */
export function uploadMethod<T>(method: string, args: Record<string, string>, file: File): Promise<T> {
  return postForm<T>(`/api/method/${method}`, args, file);
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
