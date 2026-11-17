// 输入框草稿按对话存本机（批 A ③，对照文档第 1 项：以前没发的字、已传好的附件刷新就没了，Claude 刷新后都还在）。
//
// 按「这台设备上的人 + 对话」存 localStorage：字，加上已传进会话的附件（服务端位置，拿回来不用重传，发送时照常带上）；
// 还没传完、传失败的附件不存（刷新后本来也拿不回原文件）。新对话（还没有编号）按模式记一份（new:chat / new:operator）。
// 输入框空了（发出、清空）就删；放了 MAX_AGE 以上的不拿回；坏数据当没有。人取这台设备上次登录的那个（cc.sidebar.user，
// 侧栏记的），换人登录读不到别人的；退出登录清掉本人的（assistant.tsx 的 signOut）。存不了（无痕窗口）时本次照样能用。
// 只在打开一段对话、输入框还空着时拿回，不盖掉正在打的字。

import { useAuiState, useComposerRuntime } from "@assistant-ui/react";
import { useEffect, useRef } from "react";
import type { UploadedFile } from "./api";
import { rememberUploaded, uploadedInfo } from "./attachments";
import { useListMode } from "./list-mode";
import { reportCaught } from "../caught";

const PREFIX = "cc.draft:";
const MAX_AGE = 7 * 86400_000;
const SAVE_DELAY = 400;

export type DraftFile = { id: string; type: "image" | "document" | "file"; name: string; contentType: string; info: UploadedFile };
export type Draft = { text: string; files: DraftFile[]; at: number };

function storage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

function who(): string {
  try {
    return storage()?.getItem("cc.sidebar.user") ?? "";
  } catch (caughtError) {
    reportCaught(caughtError, "drafts");
    return "";
  }
}

const keyOf = (thread: string, user = who()) => `${PREFIX}${user}:${thread}`;

/** 存一份（字与已传好的附件）；都空了就删。 */
export function saveDraft(thread: string, text: string, files: DraftFile[]): void {
  try {
    const key = keyOf(thread);
    if (!text.trim() && !files.length) storage()?.removeItem(key);
    else storage()?.setItem(key, JSON.stringify({ text, files, at: Date.now() } satisfies Draft));
  } catch (caughtError) {
    reportCaught(caughtError, "drafts");
  }
}

/** 这段对话存着的草稿；没有、过期、坏数据都回 null。 */
export function loadDraft(thread: string): Draft | null {
  let raw: string | null;
  try {
    raw = storage()?.getItem(keyOf(thread)) ?? null;
  } catch (caughtError) {
    reportCaught(caughtError, "drafts");
    return null;
  }
  if (!raw) return null;
  let draft: Partial<Draft>;
  try {
    draft = JSON.parse(raw) as Partial<Draft>;
  } catch {
    return null;
  }
  if (!draft || typeof draft.text !== "string" || !Array.isArray(draft.files) || typeof draft.at !== "number") return null;
  if (Date.now() - draft.at > MAX_AGE) return null;
  return { text: draft.text, files: draft.files.filter((f) => f && typeof f.id === "string" && f.info?.mountPath), at: draft.at };
}

/** 退出登录：清掉这台设备上本人的全部草稿。 */
export function clearDrafts(): void {
  const s = storage();
  if (!s) return;
  const mine = `${PREFIX}${who()}:`;
  try {
    const keys: string[] = [];
    for (let i = 0; i < s.length; i++) {
      const key = s.key(i);
      if (key?.startsWith(mine)) keys.push(key);
    }
    keys.forEach((key) => s.removeItem(key));
  } catch (caughtError) {
    reportCaught(caughtError, "drafts");
  }
}

type ComposerAttachment = { id: string; type: string; name: string; contentType?: string; status?: { type: string } };

/** 输入框里挂着：打开一段对话、输入框还空着时拿回草稿；字或附件一变就（稍后）存。放在每段对话的输入框里（thread.tsx 的 Composer）。 */
export function useDraftKeeper(uploadedOf: (id: string) => UploadedFile | undefined = uploadedInfo): void {
  const composer = useComposerRuntime();
  const session = useAuiState((s) => s.threadListItem.externalId ?? s.threadListItem.remoteId ?? null);
  const mode = useListMode((s) => s.mode);
  const thread = session ?? `new:${mode}`;
  const text = useAuiState((s) => s.composer.text);
  const attachments = useAuiState((s) => s.composer.attachments) as readonly ComposerAttachment[];
  const restoredFor = useRef<string | null>(null);

  // 打开（换到）这段对话：输入框空着才拿回
  useEffect(() => {
    if (restoredFor.current === thread) return;
    restoredFor.current = thread;
    const state = composer.getState();
    if (state.text || state.attachments.length) return;
    const draft = loadDraft(thread);
    if (!draft) return;
    if (draft.text) composer.setText(draft.text);
    for (const file of draft.files) {
      rememberUploaded(file.id, file.info, session ?? undefined);
      const back = composer.addAttachment({ id: file.id, type: file.type, name: file.name, contentType: file.contentType, content: [] } as never);
      void back.catch((caughtError: unknown) => reportCaught(caughtError, "drafts"));
    }
  }, [composer, thread]);

  // 字、附件变了：稍后存（打字时不每个字都写一次）。拿回之前不存，免得空输入框先把存着的那份删掉
  const save = (at: string, words: string, items: readonly ComposerAttachment[]) => {
    const files: DraftFile[] = [];
    for (const a of items) {
      const info = uploadedOf(a.id);
      if (info) files.push({ id: a.id, type: (a.type as DraftFile["type"]) || "file", name: a.name, contentType: a.contentType ?? "", info });
    }
    saveDraft(at, words, files);
  };
  useEffect(() => {
    if (restoredFor.current !== thread) return;
    const timer = setTimeout(() => save(thread, text, attachments), SAVE_DELAY);
    return () => clearTimeout(timer);
  }, [thread, text, attachments, uploadedOf]);

  // 换对话（点通知、托盘、侧栏切走）时当场把旧的那段存一次，不等 SAVE_DELAY：切走前最后打的字也进本机草稿（主会话 11-17）。
  // latest 在提交后才换成这一次的值，换对话时的清理先于它跑，读到的是旧那段的字和附件
  const latest = useRef({ thread, text, attachments });
  useEffect(() => {
    latest.current = { thread, text, attachments };
  });
  const saveRef = useRef(save);
  saveRef.current = save;
  useEffect(() => () => {
    const last = latest.current;
    if (restoredFor.current === last.thread) saveRef.current(last.thread, last.text, last.attachments);
  }, [thread]);
}
