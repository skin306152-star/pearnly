// 输入框附件（「+」按钮、拖进来、粘贴截图都走这里）。
//
// 选中文件时先确保有会话（新对话会在这一步建会话），再交给本站：服务端校验类型和大小、HEIC 转 JPEG、
// 存进 Anthropic 文件库并只读挂进该会话的沙箱。随消息带过去的只是沙箱里的路径，智能体自己去读。
// 改自 Anthropic 官方示例的 attachments.ts（MIT，见 NOTICE.md）。

import type { AttachmentAdapter, CompleteAttachment, PendingAttachment } from "@assistant-ui/react";
import { uploadFile, type UploadedFile } from "./api";

export const ACCEPT = "image/*,.heic,.heif,.pdf,.xlsx,.xls,.csv,.tsv,.docx,.txt,.md,.json";

/** 已上传附件在沙箱里的位置，按 assistant-ui 的附件编号记，发送时拼进消息。 */
const uploaded = new Map<string, UploadedFile>();

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
      yield { ...base, status: { type: "running", reason: "uploading", progress: 0.1 } };
      try {
        const sessionId = await getSessionId();
        yield { ...base, status: { type: "running", reason: "uploading", progress: 0.5 } };
        uploaded.set(id, await uploadFile(sessionId, file));
        yield { ...base, status: { type: "requires-action", reason: "composer-send" } };
      } catch (err) {
        yield {
          ...base,
          status: { type: "incomplete", reason: "error", message: err instanceof Error ? err.message : String(err) },
        };
      }
    },

    async remove(attachment) {
      // 已挂进沙箱的文件只读、只属于本会话，留着无害；这里只把输入框里的这一项去掉。
      uploaded.delete(attachment.id);
    },

    async send(attachment): Promise<CompleteAttachment> {
      const info = uploaded.get(attachment.id);
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
