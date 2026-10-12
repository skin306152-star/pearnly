
// The sidebar is not backed by a database. It's a RemoteThreadListAdapter
// over the Managed Agents session list: each thread IS a session, and the
// thread id assistant-ui hands us (remoteId/externalId) is the session id.
//
//   list        → this site's own session table, the signed-in user's rows only
//   initialize  → sessions.create   (fires on the first message of a new chat)
//   rename      → sessions.update title
//   archive     → sessions.archive
//   delete      → sessions.delete
//   generateTitle → the short title the server pushes after the first message
//                   (a cheap model summarises long first messages, like the
//                   Claude apps); falls back to the stored title, then to the
//                   first user message without the attachment note

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
import { listen, pushedTitle } from "./realtime";
import { dropSessionController } from "./session-controller";

const TITLE_MAX = 60; // 同服务端 assistant/contracts.py TITLE_MAX
// 等短标题最多这么久（模型概括通常一两秒）；等不到就读一次会话表，再不行用原文。
const TITLE_WAIT_MS = 12_000;

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
    listen();
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
    // 侧栏显示简短标题（同 Claude）：服务端把较长的首条消息概括成几个词后推过来；在那之前侧栏显示「新对话」。
    let title = (await pushedTitle(remoteId, TITLE_WAIT_MS)) || (await getSession(remoteId).catch(() => null))?.title || "";
    if (!title) {
      const first = messages.find((m) => m.role === "user");
      const text = (first?.content ?? []).map((part) => (part.type === "text" ? part.text : "")).join(" ");
      const { text: body, files } = splitAttachmentNote(text);
      title = (body.replace(/\s+/g, " ").trim() || files[0]?.filename || "").slice(0, TITLE_MAX);
    }
    return createAssistantStream((controller) => {
      controller.appendText(title || "New chat");
      controller.close();
    });
  },
};
