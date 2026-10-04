// LINE 内编辑器页的接口客户端（订单模式 / 客户档模式共用）。
//
// 身份不是站点会话，而是**一张一次性票据**：
//   · LINE 内打开时，`/liff/dms-booking` 那一页已经验过 LINE 身份并把票据放进
//     sessionStorage（同源，SPA 读得到）；
//   · 直接打开本站点页面（本机验收、或没配 LIFF 的站点）时这里自己换一张 ——
//     走"站点会话回落"，逻辑与判据都在服务端 `dms_browser_api`。
//
// 所以页面永远只有一条路：**先有票据，再谈草稿**。

import { call, setCsrfToken } from "./api";

export type EditorMode = "booking" | "customer";

export interface Option {
  id: string;
  label: string;
  /** 公司收款账户才有：账号 / 支行（要显示给客户看）。 */
  account_no?: string;
  branch_name?: string;
}

export interface CustomerForm {
  people_id: string;
  prefix_id: string;
  name: string;
  birthday_be: string;
  phone: string;
  house_no: string;
  building: string;
  floor: string;
  room: string;
  village: string;
  moo: string;
  soi: string;
  road: string;
  province_id: string;
  district_id: string;
  subdistrict_id: string;
  zipcode_id: string;
  [derived: string]: string;
}

export interface Answer {
  id: string;
  label?: string;
  name?: string;
}

export interface Payment {
  channel: string;
  amount: string;
  extra: Record<string, string>;
}

export interface SnapshotInfo {
  /** Source capture start, Unix seconds; never replace it with browser time. */
  as_of: number;
  generation: string;
  stale: boolean;
  geo_as_of?: number;
  geo_stale?: boolean;
}

const SNAPSHOT_ERRORS = new Set([
  "ERR_DMS_SNAPSHOT_PENDING", "ERR_DMS_SNAPSHOT_STALE", "ERR_DMS_SNAPSHOT_INCOMPLETE",
  "ERR_DMS_SNAPSHOT_SCOPE_CHANGED", "ERR_DMS_SNAPSHOT_REQUEST_LIMIT",
]);

export function snapshotErrorKey(code: string): string {
  const raw = code.replace(/^dms\.err\./, "");
  return SNAPSHOT_ERRORS.has(raw) ? `dms.err.${raw}` : "";
}

export function snapshotLabel(
  snapshot: SnapshotInfo | undefined,
  locale: string,
  translate: (key: string) => string,
): string {
  return capturedLabel(snapshot?.as_of, snapshot?.stale, locale, translate,
    "dms.snapshot.as_of", "dms.snapshot.stale");
}

export function addressSnapshotLabel(
  snapshot: SnapshotInfo | undefined,
  locale: string,
  translate: (key: string) => string,
): string {
  return capturedLabel(snapshot?.geo_as_of, snapshot?.geo_stale, locale, translate,
    "dms.edit.addressSnapshotAsOf", "dms.edit.addressSnapshotStale");
}

function capturedLabel(asOf: number | undefined, stale: boolean | undefined, locale: string,
  translate: (key: string) => string, freshKey: string, staleKey: string): string {
  if (typeof asOf !== "number" || !Number.isFinite(asOf) || asOf <= 0) return "";
  const captured = new Date(asOf * 1000);
  if (!Number.isFinite(captured.getTime())) return "";
  const time = new Intl.DateTimeFormat(locale, {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    hour12: false, timeZoneName: "short",
  }).format(captured);
  return translate(stale ? staleKey : freshKey)
    .replaceAll("{time}", time);
}

export interface Draft {
  form: {
    customer: CustomerForm;
    answers: Record<string, unknown>;
    payments: Payment[];
    files: { id_card: boolean; slip: boolean };
    advisor: { id?: string; name?: string };
  };
  masters: Record<string, Option[]> & { [key: string]: Option[] };
  manual_banks: string[];
  geo: Record<string, Option[]>;
  snapshot?: SnapshotInfo;
}

export interface Config {
  liff_id: string;
  available: boolean;
  configured: boolean;
  csrf_token: string;
}

const TICKET_KEY = "pearnly_dms_ticket";
const API = "pearnly_dms.editor.browser_api.";

export function storedTicket(): string {
  try {
    return sessionStorage.getItem(TICKET_KEY) || "";
  } catch {
    return "";
  }
}

/** 丢掉缓存的那张票据（失效时用：不清掉的话会一直拿它重试）。 */
export function forgetTicket(): void {
  try {
    sessionStorage.removeItem(TICKET_KEY);
  } catch {
    /* 存不下也就没有要清的 */
  }
}

function rememberTicket(ticket: string): string {
  try {
    sessionStorage.setItem(TICKET_KEY, ticket);
  } catch {
    /* 存不下就只用这一次，页面刷新会重新换一张 */
  }
  return ticket;
}

export async function loadConfig(): Promise<Config> {
  const config = await call<Config>(`${API}browser_config`);
  // 写操作要带的 CSRF：站点自己的接口下发（Frappe 不下发 csrf cookie）。
  setCsrfToken(config.csrf_token || "");
  return config;
}

/** 拿到一张能用的票据：优先 sessionStorage 里那张，没有就现场换一张。 */
export async function ensureTicket(draft: string, mode: EditorMode): Promise<string> {
  const stored = storedTicket();
  if (stored) return stored;
  const auth = await call<{ ticket: string; language?: string }>(
    `${API}browser_auth`,
    { draft, editor: mode },
    { method: "POST" },
  );
  return rememberTicket(auth.ticket);
}

/** 扫身份证页的票据（交接页已换好放在 sessionStorage；没有就现场换一张）。 */
export async function ensureScanTicket(): Promise<string> {
  const stored = storedTicket();
  if (stored) return stored;
  const auth = await call<{ ticket: string }>(`${API}browser_auth`, { editor: "scan" }, { method: "POST" });
  return rememberTicket(auth.ticket);
}

/** 上传扫描到的身份证（data URL）。返回要替业务员发回聊天的那句话与短码。 */
export function uploadScan(ticket: string, image: string): Promise<{ code: string; marker: string }> {
  return call<{ code: string; marker: string }>(`${API}browser_scan`, { ticket, image }, { method: "POST" });
}

const SCAN_MARKER_KEY = "pearnly_dms_scan_marker";
const SCAN_CODE_KEY = "pearnly_dms_scan_code";

/**
 * 交回 LINE：替业务员发消息要在 LIFF 登记的地址下 init 之后才能做，所以把那句话与短码放进
 * sessionStorage，跳回交接页（`/liff/dms-booking?editor=scan_sent`）由它发送并关窗。
 */
export function returnToChat(result: { code: string; marker: string }): void {
  try {
    sessionStorage.setItem(SCAN_MARKER_KEY, result.marker);
    sessionStorage.setItem(SCAN_CODE_KEY, result.code);
  } catch {
    /* 存不下：交接页会提示过期，业务员可以直接在聊天里发照片 */
  }
  window.location.replace("/liff/dms-booking?editor=scan_sent");
}

export function loadDraft(ticket: string, nonce: string): Promise<Draft> {
  return call<Draft>(`${API}browser_draft`, { ticket, nonce });
}

export function loadPaints(ticket: string, nonce: string, carId: string): Promise<Option[]> {
  return call<Option[]>(`${API}browser_paints`, { ticket, nonce, car_id: carId });
}

export function loadGeo(
  ticket: string,
  nonce: string,
  level: string,
  parentId = "",
): Promise<Option[]> {
  return call<Option[]>(`${API}browser_geo`, { ticket, nonce, level, parent_id: parentId });
}

export function saveDraft(
  ticket: string,
  nonce: string,
  payload: Record<string, unknown>,
): Promise<{ nonce: string }> {
  return call<{ nonce: string }>(
    `${API}browser_save`,
    { ticket, nonce, form: JSON.stringify(payload) },
    { method: "POST" },
  );
}

// ── 付款渠道 ─────────────────────────────────────────────────────────────
// 六种渠道与各自要填的字段（照老产品 `dms-booking-edit.js` 的 `paymentRow`）。
export const CHANNELS = ["cash", "transfer", "cheque", "cashier_cheque", "card", "other"] as const;
export type Channel = (typeof CHANNELS)[number];

/** 渠道 → 收单银行目录（`null` 表示这个渠道不需要银行）。 */
export const CHANNEL_BANK: Record<string, string> = {
  cheque: "cheque_banks",
  cashier_cheque: "cashier_banks",
  card: "card_banks",
};

/** 渠道 → 除银行外还要填的字段（顺序即界面顺序）。 */
export const CHANNEL_FIELDS: Record<string, string[]> = {
  cheque: ["cheque_no", "cheque_book_no"],
  cashier_cheque: ["cashier_no", "cashier_book_no"],
  card: ["card_type"],
  other: ["detail"],
};

export const MAX_PAYMENTS = 6;
