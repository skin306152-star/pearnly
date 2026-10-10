
// The sidebar is not backed by a database. It's a RemoteThreadListAdapter
// over the Managed Agents session list: each thread IS a session, and the
// thread id assistant-ui hands us (remoteId/externalId) is the session id.
//
//   list        → this site's own session table, the signed-in user's rows only
//   initialize  → sessions.create   (fires on the first message of a new chat)
//   rename      → sessions.update title
//   archive     → sessions.archive
//   delete      → sessions.delete
//   generateTitle → the first user message without the attachment note
//                   (the server writes the same title when the message is sent)

import type { RemoteThreadListAdapter } from "@assistant-ui/react";
import { createAssistantStream } from "assistant-stream";
import {
  archiveSession,
  createSession,
  deleteSession,
  getSession,
  listSessions,
  renameSession,
  starSession,
} from "./api";
import { type SessionSummary, splitAttachmentNote } from "./events";
import { dropSessionController } from "./session-controller";

const TITLE_MAX = 60; // 同服务端 assistant/contracts.py TITLE_MAX

const toThreadMetadata = (s: SessionSummary) => ({
  status: (s.archived_at ? "archived" : "regular") as "archived" | "regular",
  remoteId: s.id,
  externalId: s.id,
  title: s.title ?? undefined,
  lastMessageAt: s.updated_at ? new Date(s.updated_at) : undefined,
  custom: { starred: !!s.starred },
});

export const sessionListAdapter: RemoteThreadListAdapter = {
  async list() {
    const { sessions, nextCursor } = await listSessions();
    return {
      threads: sessions.map(toThreadMetadata),
      nextCursor: nextCursor ?? undefined,
    };
  },

  async initialize() {
    // Called once, on the first message (or first attachment) of a new
    // chat. The returned id becomes both remoteId and externalId, so
    // every later call keys straight off the session id.
    const session = await createSession();
    return { remoteId: session.id, externalId: session.id };
  },

  async rename(remoteId, newTitle) {
    await renameSession(remoteId, newTitle);
  },

  // 加星标存在本站会话表（只是整理标记）；列表按它把会话放进「已加星标」分组。
  async updateCustom(remoteId, custom) {
    await starSession(remoteId, !!custom?.starred);
  },

  async archive(remoteId) {
    await archiveSession(remoteId);
    dropSessionController(remoteId);
  },

  async unarchive() {
    // Managed Agents sessions can't be unarchived; the UI doesn't offer it.
    throw new Error("Archived sessions cannot be restored.");
  },

  async delete(remoteId) {
    await deleteSession(remoteId);
    dropSessionController(remoteId);
  },

  async fetch(threadId) {
    return toThreadMetadata(await getSession(threadId));
  },

  async generateTitle(remoteId, messages) {
    // 标题取用户第一句原文（不含给智能体看的附件说明，同服务端 contracts.title_from），不另调模型。
    // 服务端在发消息时写同样的标题；这里不等它，免得读得太早拿到空标题、侧栏一直是「新对话」。
    const first = messages.find((m) => m.role === "user");
    const text = (first?.content ?? []).map((part) => (part.type === "text" ? part.text : "")).join(" ");
    const { text: body, files } = splitAttachmentNote(text);
    let title = (body.replace(/\s+/g, " ").trim() || files[0]?.filename || "").slice(0, TITLE_MAX);
    if (!title) title = (await getSession(remoteId)).title || "";
    return createAssistantStream((controller) => {
      controller.appendText(title || "New chat");
      controller.close();
    });
  },
};
