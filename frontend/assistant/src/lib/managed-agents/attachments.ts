// 输入框附件（「+」按钮、拖进来、粘贴截图都走这里）。
//
// 选中文件时先确保有会话（新对话会在这一步建会话），再交给本站：服务端校验类型和大小、HEIC 转 JPEG、
// 交给当前引擎放进该会话。随消息带过去的只是智能体读取它的位置，智能体自己去读。
// 改自 Anthropic 官方示例的 attachments.ts（MIT，见 NOTICE.md）。

import type { AttachmentAdapter, CompleteAttachment, PendingAttachment } from "@assistant-ui/react";
import { uploadFile, type UploadedFile } from "./api";
import { beginPending } from "./pending-send";
import { shownError } from "@/lib/frappe";

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
  for (const a of attachments ?? []) {
    if (uploaded.has(a.id)) continue;
    const error = unsent.get(a.id);
    unsent.delete(a.id);
    throw error ?? new Error(`attachment not uploaded: ${a.name}`);
  }
  return attachedFilesOf(attachments);
}

export function createAttachmentAdapter(getSessionId: () => Promise<string>): AttachmentAdapter {
  /** 传进会话并记下位置、大小、本地预览（选中文件时、发送时重传都走这里）。 */
  const store = async (id: string, file: File): Promise<UploadedFile> => {
    const stored = await uploadFile(await getSessionId(), file);
    uploaded.set(id, stored);
    metas.set(stored.mountPath, { size: stored.size_bytes ?? file.size, ...(stored.pages ? { pages: stored.pages } : {}) });
    if (/^image\/(png|jpe?g|gif|webp)$/.test(file.type)) previews.set(stored.mountPath, URL.createObjectURL(file));
    return stored;
  };

  return {
    accept: ACCEPT,

    async *add({ file }): AsyncGenerator<PendingAttachment, void> {
      const id = `att-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const type = file.type.startsWith("image/") ? ("image" as const) : ("document" as const);
      const base = { id, type, name: file.name, contentType: file.type, file };
      const upload = store(id, file);
      uploading.set(id, upload);
      yield { ...base, status: { type: "running", reason: "uploading", progress: 0.3 } };
      try {
        await upload;
      } catch (err) {
        // 已点了发送：这一项归发送管（send 重传一次，仍没传上就整条放回输入框、重新传），这里不再动它
        if (sent.has(id)) return;
        yield {
          ...base,
          status: { type: "incomplete", reason: "error", message: shownError(err) },
        };
        return;
      } finally {
        uploading.delete(id);
      }
      // 还在上传时就点了发送：消息已带着它发出去（见 send），这里不再把它放回输入框
      if (sent.has(id)) return;
      yield { ...base, status: { type: "requires-action", reason: "composer-send" } };
    },

    async remove(attachment) {
      // 已挂进沙箱的文件只读、只属于本会话，留着无害；这里只把输入框里的这一项去掉。
      uploaded.delete(attachment.id);
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
        beginPending(attachment.name);
        const waiting = uploading.get(attachment.id);
        try {
          if (waiting) info = await waiting.catch(() => undefined);
          if (!info) {
            if (!attachment.file) throw new Error(`attachment not uploaded: ${attachment.name}`);
            info = await store(attachment.id, attachment.file);
          }
        } catch (err) {
          unsent.set(attachment.id, err);
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
