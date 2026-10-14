
// The sidebar is not backed by a database. It's a RemoteThreadListAdapter
// over the Managed Agents session list: each thread IS a session, and the
// thread id assistant-ui hands us (remoteId/externalId) is the session id.
//
//   list        → this site's own session table, the signed-in user's rows only,
//                 a page at a time (the sidebar loads the next page on scroll);
//                 rows with unseen notices get the sidebar dot, rows whose notices were
//                 seen elsewhere (unread: false) lose it (activity.ts); the first page
//                 also asks the server for the run state of the rows the sidebar
//                 shows as running (check → runs), so a missed "turn ended" push
//                 does not leave a spinner behind (absorb, below)
//   initialize  → sessions.create   (fires on the first message of a new chat)
//   rename      → sessions.update title
//   archive     → sessions.archive
//   delete      → sessions.delete
//   generateTitle → the first message right away (the server keeps it as the
//                   temporary title); the topic title the server makes after the
//                   first turn arrives as a sidebar change and replaces it in place
//                   (list-sync.ts), on this device and every other one
//
// 侧栏多设备同步（list-sync.ts）把别处的改名、星标、归档、删除并进本页时也走组件库的这几个操作；那时服务端已经改好了，
// 这里不再发请求（takeQuiet）。本页自己改的记下时刻（touch），核对时不拿改之前读到的一页盖掉它。

import type { RemoteThreadListAdapter } from "@assistant-ui/react";
import { createAssistantStream } from "assistant-stream";
import {
  archiveSession,
  createSession,
  deleteSession,
  getSession,
  listSessions,
  renameSession,
  type SessionPage,
  starSession,
} from "./api";
import { clearUnread, noteRun, runningSessions, seedUnread } from "./activity";
import { type SessionSummary, splitAttachmentNote } from "./events";
import { latestTitle, listen } from "./realtime";
import { dropSessionController } from "./session-controller";

const TITLE_MAX = 60; // 同服务端 assistant/contracts.py TITLE_MAX

/** 侧栏列表带回的提醒圆点与运行状态（list 与 list-sync 的核对共用）。asked：这次请求发出的时刻。 */
export function absorb(page: SessionPage, asked: number) {
  // 有没看过的提醒的对话留圆点（刷新后仍在，服务端记着）；在别处看过了（unread 为假）去掉，请求发出后才推来的留着
  for (const s of page.sessions) {
    if (s.unread) seedUnread(s.id);
    else if (s.unread === false) clearUnread(s.id, asked);
  }
  for (const [id, run] of Object.entries(page.runs ?? {})) noteRun(id, run);
}

// ---------------------------------------------------------------- quiet：并入别处的改动时不再发请求
type Kind = "rename" | "custom" | "archive" | "delete";
type Mark = { used: boolean };
const quiet = new Map<string, Mark[]>();
const quietKey = (kind: Kind, remoteId: string) => `${kind}\n${remoteId}`;

function takeQuiet(kind: Kind, remoteId: string): boolean {
  const mark = quiet.get(quietKey(kind, remoteId))?.find((m) => !m.used);
  if (!mark) return false;
  mark.used = true;
  return true;
}

/** 把别处已经做完的改动并进本页（list-sync.ts）：run 里组件库调到这里的那一次不发请求。 */
export async function quietly(kind: Kind, remoteId: string, run: () => Promise<void>): Promise<void> {
  const key = quietKey(kind, remoteId);
  const mark: Mark = { used: false };
  quiet.set(key, [...(quiet.get(key) ?? []), mark]);
  try {
    await run();
  } catch {
    // 并不进去（这一行正处在组件库不让改的状态）：下一次核对再说
  } finally {
    // 没用上的记号一定收回，免得之后用户自己的操作被当成并入而不发请求
    const left = (quiet.get(key) ?? []).filter((m) => m !== mark);
    if (left.length) quiet.set(key, left);
    else quiet.delete(key);
  }
}

// ---------------------------------------------------------------- 本页自己改过的时刻
const touched = new Map<string, number>();

function touch(remoteId: string) {
  touched.set(remoteId, Date.now());
}

/** 本页在 asked 之后自己建好、改名、加星标、归档或删除过这段对话：那之前读的一页不拿来改它（list-sync.ts）。 */
export function touchedSince(remoteId: string, asked: number): boolean {
  return (touched.get(remoteId) ?? -1) >= asked;
}

const toThreadMetadata = (s: SessionSummary) => ({
  status: (s.archived_at ? "archived" : "regular") as "archived" | "regular",
  remoteId: s.id,
  externalId: s.id,
  title: s.title ?? undefined,
  lastMessageAt: s.updated_at ? new Date(s.updated_at) : undefined,
  custom: { starred: !!s.starred },
});

export const sessionListAdapter: RemoteThreadListAdapter = {
  async list(params) {
    const asked = Date.now();
    // 第一页顺带核对侧栏标着在跑的对话（在别处跑完了、推送漏了的不再转圈）
    const page = await listSessions(params?.after, params?.after ? [] : runningSessions());
    absorb(page, asked);
    return {
      threads: page.sessions.map(toThreadMetadata),
      nextCursor: page.nextCursor ?? undefined,
    };
  },

  async initialize() {
    // Called once, on the first message (or first attachment) of a new
    // chat. The returned id becomes both remoteId and externalId, so
    // every later call keys straight off the session id.
    listen();
    const session = await createSession();
    touch(session.id);
    return { remoteId: session.id, externalId: session.id };
  },

  async rename(remoteId, newTitle) {
    if (takeQuiet("rename", remoteId)) return;
    touch(remoteId);
    await renameSession(remoteId, newTitle);
  },

  // 加星标存在本站会话表（只是整理标记）；列表按它把会话放进「已加星标」分组。
  async updateCustom(remoteId, custom) {
    if (takeQuiet("custom", remoteId)) return;
    touch(remoteId);
    await starSession(remoteId, !!custom?.starred);
  },

  async archive(remoteId) {
    if (!takeQuiet("archive", remoteId)) {
      touch(remoteId);
      await archiveSession(remoteId);
    }
    dropSessionController(remoteId);
  },

  async unarchive() {
    // Managed Agents sessions can't be unarchived; the UI doesn't offer it.
    throw new Error("Archived sessions cannot be restored.");
  },

  async delete(remoteId) {
    if (!takeQuiet("delete", remoteId)) {
      touch(remoteId);
      await deleteSession(remoteId);
    }
    dropSessionController(remoteId);
  },

  async fetch(threadId) {
    return toThreadMetadata(await getSession(threadId));
  },

  async generateTitle(remoteId, messages) {
    // 发出第一条消息就有标题（同 Claude）：服务端推来过就用推来的，否则就是首条消息（附件说明换成文件名），与服务端
    // 记的临时标题同一规则。第一轮结束后服务端起好主题标题，经侧栏同步就地换上（list-sync.ts）。
    let title = latestTitle(remoteId) ?? "";
    if (!title) {
      const first = messages.find((m) => m.role === "user");
      const text = (first?.content ?? []).map((part) => (part.type === "text" ? part.text : "")).join(" ");
      const { text: body, files } = splitAttachmentNote(text);
      title = (body.replace(/\s+/g, " ").trim() || files[0]?.filename || "").slice(0, TITLE_MAX);
    }
    return createAssistantStream((controller) => {
      // 什么都没有时不写：侧栏显示「新对话」（按页面语言）
      if (title) controller.appendText(title);
      controller.close();
    });
  },
};
