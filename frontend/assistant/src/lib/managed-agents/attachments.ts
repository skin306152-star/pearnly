// 输入框附件（「+」按钮、拖进来、粘贴截图都走这里）。
//
// 选中文件时先确保有会话（新对话会在这一步建会话），再交给本站：服务端校验类型和大小、HEIC 转 JPEG、
// 交给当前引擎放进该会话。随消息带过去的只是智能体读取它的位置，智能体自己去读。代录会话同一套（2026-11-06）：服务端另在
// 会话挂的任务上记一行「待发送」，消息发出后才交识别（11-14：选中就上传是为了发送不用等，不是上传就识别）；输入框里只显示上传状态，
// 没发出就移除的告诉服务端删掉（dropUpload，两种会话都是），不识别、不扣费。
// 改自 Anthropic 官方示例的 attachments.ts（MIT，见 NOTICE.md）。

import type { AttachmentAdapter, CompleteAttachment, PendingAttachment } from "@assistant-ui/react";
import { dropUpload, pageUrl, uploadFile, type UploadedFile } from "./api";
import { beginPending } from "./pending-send";
import { SiteError, shownError, tooLargeText, uploadExtensions, uploadMaxBytes } from "@/lib/frappe";
import { useSyncExternalStore } from "react";
import { t } from "@/i18n";
import { reportCaught, withReport } from "../caught";

/** 选文件框里列出的类型（「+」按钮用，components/assistant-ui/attachment.tsx）。选中、拖进、粘贴的文件收不收一律由 refusalOf 判：
 *  适配器自己的 accept 是 "*"——组件库按 accept 拦下时只抛错、页面没反应，还留一条未接住的错误进站点错误日志。 */
export const ACCEPT = "image/*,.heic,.heif,.pdf,.xlsx,.xls,.csv,.tsv,.docx,.txt,.md,.json";

/** 已上传附件在沙箱里的位置，按 assistant-ui 的附件编号记，发送时拼进消息。 */
const uploaded = new Map<string, UploadedFile>();
/** 正在上传的附件；已随消息发出的附件编号（上传完成后不再放回输入框）。 */
const uploading = new Map<string, Promise<UploadedFile>>();
const sent = new Set<string>();
/** 发送时重传也没传上的附件（编号 → 那次的错误）：这条消息不发，由 filesToSend 说出原因。 */
const unsent = new Map<string, unknown>();
/** 本页上传的图片的本地预览，按沙箱位置记（消息气泡里的附件以它为编号）：发出去的图片在气泡里显示缩略图。
 *  引擎那边的附件不可下载，刷新后没有缩略图，显示文件图标。 */
const previews = new Map<string, string>();
/** 本页上传的文件的大小与 PDF 页数，按沙箱位置记：刚发出、本会话文件列表还没重取时，气泡里的附件卡片先用它。 */
const metas = new Map<string, { size: number; pages?: number }>();
/** 输入框里已移除的附件：上传晚到时不再把它放回输入框，传上的那份告诉服务端删掉（组件库按编号「有就换、没有就加」）。 */
const dropped = new Set<string>();
/** 没传上、可以再传一次的附件（编号 → 原文件）：方块点一下重试（上传研究 U5）。服务端说了原因拒收的（类型、大小）不在这里。 */
const retryable = new Map<string, File>();
/** 正在上传的字节进度（编号 → 0–1）：发送后临时气泡里的附件卡片接着显示（输入框方块读组件库状态里的 progress）。 */
const progress = new Map<string, number>();
const progressListeners = new Set<() => void>();

function setProgress(id: string, value: number | undefined) {
  if (value === undefined) progress.delete(id);
  else progress.set(id, value);
  for (const listener of progressListeners) listener();
}

/** 这一项上传到哪了（0–1）；没在传回 undefined。 */
export function useUploadProgress(id: string | undefined): number | undefined {
  return useSyncExternalStore(
    (listener) => {
      progressListeners.add(listener);
      return () => progressListeners.delete(listener);
    },
    () => (id ? progress.get(id) : undefined),
  );
}

/** 这一项没传上、能再传一次：交回原文件并清掉记录（方块点重试时，attachment.tsx 移除这一项再用同一份文件重新放进输入框）。 */
export function takeRetry(id: string): File | undefined {
  const file = retryable.get(id);
  retryable.delete(id);
  return file;
}

/** 这一项没传上、能再传一次（方块显示成可点重试）。 */
export function canRetry(id: string): boolean {
  return retryable.has(id);
}

/** 进度回调只在多走了一截（2%）或传完时才交给组件库，免得每个网络包都重画一次输入框。 */
function progressFeed() {
  let latest: number | undefined;
  let shown = 0;
  let wake: (() => void) | undefined;
  return {
    push(value: number) {
      if (value < 1 && value - shown < 0.02) return;
      shown = value;
      latest = value;
      wake?.();
    },
    async next(): Promise<number> {
      while (latest === undefined) await new Promise<void>((resolve) => (wake = resolve));
      const value = latest;
      latest = undefined;
      return value;
    },
  };
}

export function localPreview(mountPath: string): string | undefined {
  return previews.get(mountPath);
}

export function localMeta(mountPath: string): { size: number; pages?: number } | undefined {
  return metas.get(mountPath);
}

/** 沙箱位置（`/mnt/session/uploads/<文件编号>/<文件名>`）里的文件编号：右侧面板按它打开这个附件；认不出给 null。 */
export function uploadFileId(mountPath: string): string | null {
  return /\/uploads\/([A-Za-z0-9_.:-]+)\/[^/]+$/.exec(mountPath)?.[1] ?? null;
}

/** 附件传进的是哪段会话（编号 → 会话编号）：输入框方块取 PDF 第一页小图要它。 */
const sessionOf = new Map<string, string>();

/** 输入框方块里 PDF 传完后显示的第一页小图（服务端画的，同发出后的卡片，上传研究 U1b）；还没传完、不是 PDF 回 undefined。 */
export function composerPageThumb(attachmentId: string): string | undefined {
  const info = uploaded.get(attachmentId);
  const session = sessionOf.get(attachmentId);
  if (!info || !session || !/\.pdf$/i.test(info.filename)) return undefined;
  return pageUrl(session, info.fileId, 1, "thumb");
}

/** 这一项已传进会话的信息（输入框草稿存它，drafts.ts）。 */
export function uploadedInfo(attachmentId: string): UploadedFile | undefined {
  return uploaded.get(attachmentId);
}

/** 刷新后从草稿拿回的附件：记回已传好的（服务端位置不变），发送时照常带上、不重传（drafts.ts，批 A ③）。 */
export function rememberUploaded(attachmentId: string, info: UploadedFile): void {
  uploaded.set(attachmentId, info);
  metas.set(info.mountPath, { size: info.size_bytes, ...(info.pages ? { pages: info.pages } : {}) });
}

/** 这一项已传进会话（发送没成功放回输入框时，传过的原样放回，没传上的重新传，send-failure.ts）。 */
export function isUploaded(attachmentId: string): boolean {
  return uploaded.has(attachmentId);
}

export function attachedFilesOf(attachments: readonly CompleteAttachment[] | undefined) {
  return (attachments ?? [])
    .map((a) => uploaded.get(a.id))
    .filter((f): f is UploadedFile => !!f)
    .map((f) => ({ filename: f.filename, mountPath: f.mountPath }));
}

/** 这条消息要带的附件在沙箱里的位置。有一项没传上就抛出（重传那次的错误，没有就通用的一句）：消息不发，原因写在输入框上方，
 *  字和附件放回输入框（send-failure.ts）。不静默丢附件（2026-10-17 第 9 轮第 15 条：带红色感叹号的附件点发送，消息不带它
 *  就发出去了，CC 答的是上一张旧图）。 */
export function filesToSend(attachments: readonly CompleteAttachment[] | undefined) {
  const missing = (attachments ?? []).find((a) => !uploaded.has(a.id));
  if (missing) {
    const error = unsent.get(missing.id);
    // 这条消息的附件都放回输入框重新传：记下的错误一并清掉，不留旧编号
    for (const a of attachments ?? []) unsent.delete(a.id);
    throw error ?? new Error(`attachment not uploaded: ${missing.name}`);
  }
  return attachedFilesOf(attachments);
}

/** 永远传不上的文件为什么不收（给人看的那句）；收就回 ""。超过上限、空文件、服务端不收的类型：大小和名字选中那一刻就知道，
 *  当场拒收，不进输入框、不留失败卡（主会话 11-12：留着失败卡，发送时重传必败、整条发不出，不删卡就永远发不出）。
 *  上限与类型清单都是服务端写进页面的那份（lib/frappe.ts），前端不另写。 */
export function refusalOf(file: File): string {
  const max = uploadMaxBytes();
  if (max && file.size > max) return tooLargeText();
  if (file.size === 0) return t("fileEmpty");
  const allowed = uploadExtensions();
  const dot = file.name.lastIndexOf(".");
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
  if (allowed.length && !allowed.includes(ext)) return t("fileTypeUnsupported");
  return "";
}

/** refused：选中的文件被当场拒收时带上原因（写在输入框上方那一行，send-failure.ts 的 noteRefusedFile）。held：此刻不收（链接 / 通知
 *  正在打开别的对话，lib/opening.ts）——拖放、粘贴绕过「+」按钮时也不建会话、不上传、不出卡片。 */
export function createAttachmentAdapter(getSessionId: () => Promise<string>, refused?: (message: string) => void,
                                        held?: () => boolean): AttachmentAdapter {
  /** 传进会话并记下位置、大小、本地预览（选中文件时、发送时重传都走这里）。 */
  const store = async (id: string, file: File, onProgress?: (fraction: number) => void): Promise<UploadedFile> => {
    const session = await getSessionId();
    const stored = await uploadFile(session, file, onProgress);
    uploaded.set(id, stored);
    sessionOf.set(id, session);
    metas.set(stored.mountPath, { size: stored.size_bytes ?? file.size, ...(stored.pages ? { pages: stored.pages } : {}) });
    if (/^image\/(png|jpe?g|gif|webp)$/.test(file.type)) previews.set(stored.mountPath, URL.createObjectURL(file));
    return stored;
  };

  return {
    accept: "*",

    async *add({ file }): AsyncGenerator<PendingAttachment, void> {
      // 永远传不上的（超限、空的、类型不收）当场拒收：什么都不交给输入框（没有附件卡），也不建会话、不上传
      if (held?.()) return;
      const refusal = refusalOf(file);
      if (refusal) {
        refused?.(refusal);
        return;
      }
      const id = `att-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const type = file.type.startsWith("image/") ? ("image" as const) : ("document" as const);
      const base = { id, type, name: file.name, contentType: file.type, file };
      const feed = progressFeed();
      const upload = store(id, file, (fraction) => {
        setProgress(id, fraction);
        feed.push(fraction);
      });
      uploading.set(id, upload);
      // 已点了发送（消息带着它发出去了，见 send）或已从输入框移除：不再交给输入框，否则组件库会把它加回去
      const gone = () => sent.has(id) || dropped.has(id);
      // 进度是真数（已传字节 / 整份），一开始是 0；字节传完到服务端回话之间停在 1（方块中间转圈）
      yield { ...base, status: { type: "running", reason: "uploading", progress: 0 } };
      const settled = upload.then(() => true, () => true);
      while (!(await Promise.race([settled, feed.next().then(() => false)]))) {
        if (gone()) break;
        yield { ...base, status: { type: "running", reason: "uploading", progress: progress.get(id) ?? 0 } };
      }
      try {
        await upload;
      } catch (err) { reportCaught(err, "attachments");
        // 已点了发送：这一项归发送管（send 重传一次，仍没传上就整条放回输入框、重新传），这里不再动它
        if (gone()) return;
        // 服务端说了原因拒收的（SiteError：类型、大小等）再传也一样，只能移除；断线、超时、服务端出错可以点方块重试
        if (!(err instanceof SiteError)) retryable.set(id, file);
        yield {
          ...base,
          status: { type: "incomplete", reason: "error", message: shownError(err) },
        };
        return;
      } finally {
        uploading.delete(id);
        setProgress(id, undefined);
      }
      if (sent.has(id)) return;
      if (dropped.has(id)) {
        // 传的时候就移除了：服务端留的那份删掉（同 remove），不识别、不扣费
        const info = uploaded.get(id);
        uploaded.delete(id);
        if (info) await dropUpload(await getSessionId(), info.fileId).catch(withReport(() => undefined, "attachments"));
        return;
      }
      yield { ...base, status: { type: "requires-action", reason: "composer-send" } };
    },

    async remove(attachment) {
      // 已挂进沙箱的文件只读、只属于本会话，留着无害；这里把输入框里的这一项去掉，还没发出的告诉服务端删掉本站留的那份
      // （两种会话都是；代录另删任务上那一行「待发送」：发出后才交识别，移除的不识别、不扣费）。删不掉只报错，不挡移除
      dropped.add(attachment.id);
      retryable.delete(attachment.id);
      const info = uploaded.get(attachment.id);
      uploaded.delete(attachment.id);
      if (info && !sent.has(attachment.id)) {
        await dropUpload(await getSessionId(), info.fileId).catch(withReport(() => undefined, "attachments"));
      }
    },

    async send(attachment): Promise<CompleteAttachment> {
      // 上传没完成就发送（例如粘贴后马上回车）：等它传完再发，消息里才有它的位置、气泡里才有缩略图。
      // 没传上（之前就出错带红色感叹号的，或等的这一会儿断网、出错的）：先重传一次，传上了照常带着发；仍没传上记下错误，
      // 这条消息不发（filesToSend 抛出，原因写在输入框上方，字和附件放回输入框）。这里不抛：组件库调发送不等也不接错，
      // 抛了就是未捕获的错误，也不会经过我们的发送失败处理。
      sent.add(attachment.id);
      unsent.delete(attachment.id);
      let info = uploaded.get(attachment.id);
      if (!info) {
        beginPending(attachment.name, attachment.id);
        const waiting = uploading.get(attachment.id);
        try {
          if (waiting) info = await waiting.catch(withReport(() => undefined, "attachments"));
          if (!info) {
            if (!attachment.file) throw new Error(`attachment not uploaded: ${attachment.name}`);
            info = await store(attachment.id, attachment.file, (fraction) => setProgress(attachment.id, fraction));
          }
        } catch (err) { reportCaught(err, "attachments");
          unsent.set(attachment.id, err);
        } finally {
          setProgress(attachment.id, undefined);
        }
      }
      return {
        ...attachment,
        status: { type: "complete" },
        content: [
          {
            type: "file",
            filename: info?.filename ?? attachment.name,
            data: info?.mountPath ?? attachment.name,
            mimeType: attachment.contentType || "application/octet-stream",
          },
        ],
      };
    },
  };
}
