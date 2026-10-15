// 本站工具那一步点开后显示的参数与结果（测试窗口第 2 轮：展开后是原样的 JSON，满是连接名 CWEC-…、file_…、记录名 CWH-…、
// c:\pearnlyexpress\… 这种本地路径）。只留给人看的：单号、名称、金额、日期、状态、说明；内部的名字与路径一律拿掉。
// 纯函数，不调模型；人给的字（搜索词、月份）原样留着。不是 JSON 的（报错的一句话等）原样显示。
//
// 拿掉的：
//   · 键本身就是内部的：连接、账套键 / 路径、会话、附件 / 批次 / 推送记录的名字（INTERNAL_KEYS）
//   · 值像内部的字符串：随机名与 CW…- 记录名（looksInternal）、file_ / sesn_ 这类编号、本地路径（盘符、\\共享、/Users/…）
//   · 拿掉以后空了的列表与对象（原来就空的照留）

import { looksInternal } from "@/lib/step-subject";

type Json = Record<string, unknown>;

// 文件夹里的文件名、相对路径是给人看的（folder_list 的 files / path），不按键拿掉，只按值拿掉内部编号与本地绝对路径。
/** 键本身就是内部的（值不管长什么样都不显示）。 */
const INTERNAL_KEYS = new Set([
  "connection", "push_connection", "connection_name", "account_set_key", "account_set", "push_account_set",
  "target_account_set", "account_dir", "account_root", "root", "root_id", "reader", "folder_file", "source_id",
  "session", "session_id", "file_id", "batch", "batches", "record", "records", "last_push_record", "request",
  "request_id", "request_key", "history",
]);

/** 编号式的内部名字：Anthropic 的文件 / 会话 / 消息编号等（file_011C…、sesn_…）。 */
const PREFIXED_ID = /^(file|sesn|session|msg|toolu|srvtoolu|agent|env|user|org)_[A-Za-z0-9]{6,}$/;
/** 本地路径：盘符（c:\…、C:/…）、网络共享（\\server\…）、Unix 的用户 / 临时目录。 */
const LOCAL_PATH = /^(?:[A-Za-z]:[\\/]|\\\\[^\\]|\/(?:Users|home|tmp|private|var|mnt|opt|srv)\/)/;

/** 字符串值像内部的（不给人看）。 */
export function internalValue(value: string): boolean {
  const plain = value.trim();
  return Boolean(plain) && (looksInternal(plain) || PREFIXED_ID.test(plain) || LOCAL_PATH.test(plain));
}

const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);
const DROP = Symbol("drop");

function clean(value: unknown, depth: number): unknown {
  if (typeof value === "string") return internalValue(value) ? DROP : value;
  if (depth > 12 || value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) {
    const kept = value.map((item) => clean(item, depth + 1)).filter((item) => item !== DROP);
    return value.length && !kept.length ? DROP : kept;
  }
  const out: Json = {};
  for (const [key, item] of Object.entries(value)) {
    if (INTERNAL_KEYS.has(key)) continue;
    const kept = clean(item, depth + 1);
    if (kept !== DROP) out[key] = kept;
  }
  return Object.keys(value).length && !Object.keys(out).length ? DROP : out;
}

/** 去掉内部名字与路径后的值；什么都不剩回 undefined。 */
export function sanitizeDetail(value: unknown): unknown {
  const kept = clean(value, 0);
  return kept === DROP ? undefined : kept;
}

/** 参数 → 点开后显示的文字（JSON 缩进）；去掉以后没有给人看的就不显示（undefined）。 */
export function detailArgs(args: Readonly<Json> | undefined): string | undefined {
  if (!args || !Object.keys(args).length) return undefined;
  const kept = sanitizeDetail(args);
  return isObject(kept) && Object.keys(kept).length ? JSON.stringify(kept, null, 2) : undefined;
}

/** 结果的文字 → 点开后显示的文字：JSON 去掉内部的、缩进显示；不是 JSON 的原样。 */
export function detailOutput(text: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return text;
  }
  if (parsed === null || typeof parsed !== "object") return text;
  const kept = sanitizeDetail(parsed);
  return kept === undefined ? "" : JSON.stringify(kept, null, 2);
}
