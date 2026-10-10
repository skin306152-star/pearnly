// 输入框附件（「+」按钮、拖进来、粘贴截图都走这里）。
//
// 选中文件时先确保有会话（新对话会在这一步建会话），再交给本站：服务端校验类型和大小、HEIC 转 JPEG、
// 交给当前引擎放进该会话。随消息带过去的只是智能体读取它的位置，智能体自己去读。
// 改自 Anthropic 官方示例的 attachments.ts（MIT，见 NOTICE.md）。

import type { AttachmentAdapter, CompleteAttachment, PendingAttachment } from "@assistant-ui/react";
import { uploadFile, type UploadedFile } from "./api";
import { beginPending } from "./pending-send";

export const ACCEPT = "image/*,.heic,.heif,.pdf,.xlsx,.xls,.csv,.tsv,.docx,.txt,.md,.json";

/** 已上传附件在沙箱里的位置，按 assistant-ui 的附件编号记，发送时拼进消息。 */
const uploaded = new Map<string, UploadedFile>();
/** 正在上传的附件；已随消息发出的附件编号（上传完成后不再放回输入框）。 */
const uploading = new Map<string, Promise<UploadedFile>>();
const sent = new Set<string>();
/** 本页上传的图片的本地预览，按沙箱位置记（消息气泡里的附件以它为编号）：发出去的图片在气泡里显示缩略图。
 *  引擎那边的附件不可下载，刷新后没有缩略图，显示文件图标。 */
const previews = new Map<string, string>();

export function localPreview(mountPath: string): string | undefined {
  return previews.get(mountPath);
}

export function attachedFilesOf(attachments: readonly CompleteAttachment[] | undefined) {
  return (attachments ?? [])
    .map((a) => uploaded.get(a.id))
    .filter((f): f is UploadedFile => !!f)
    .map((f) => ({ filename: f.filename, mountPath: f.mountPath }));
}

export function createAttachmentAdapter(getSessionId: () => Promise<string>): AttachmentAdapter {
  return {
    accept: ACCEPT,

    async *add({ file }): AsyncGenerator<PendingAttachment, void> {
      const id = `att-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const type = file.type.startsWith("image/") ? ("image" as const) : ("document" as const);
      const base = { id, type, name: file.name, contentType: file.type, file };
      const upload = (async () => {
        const stored = await uploadFile(await getSessionId(), file);
        uploaded.set(id, stored);
        if (/^image\/(png|jpe?g|gif|webp)$/.test(file.type)) previews.set(stored.mountPath, URL.createObjectURL(file));
        return stored;
      })();
      uploading.set(id, upload);
      yield { ...base, status: { type: "running", reason: "uploading", progress: 0.3 } };
      try {
        await upload;
      } catch (err) {
        if (sent.has(id)) return;
        yield {
          ...base,
          status: { type: "incomplete", reason: "error", message: err instanceof Error ? err.message : String(err) },
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
      // 上传没完成就发送（例如粘贴后马上回车）：等它传完再发，消息里才有它的位置、气泡里才有缩略图
      sent.add(attachment.id);
      const waiting = uploading.get(attachment.id);
      if (!uploaded.has(attachment.id) && waiting) beginPending(attachment.name);
      const info = uploaded.get(attachment.id) ?? (await waiting?.catch(() => undefined));
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
