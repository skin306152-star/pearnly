
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
//                 does not leave a spinner behind (checkRunning, below)
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
import { clearUnread, noteRun, runningSessions, seedUnread } from "./activity";
import { type RunStatus, type SessionSummary, splitAttachmentNote } from "./events";
import { listen, onConnectionChange, pushedTitle } from "./realtime";
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

function noteRuns(runs: Record<string, RunStatus> | undefined) {
  for (const [id, run] of Object.entries(runs ?? {})) noteRun(id, run);
}

export const sessionListAdapter: RemoteThreadListAdapter = {
  async list(params) {
    const asked = Date.now();
    // 第一页顺带核对侧栏标着在跑的对话（在别处跑完了、推送漏了的不再转圈）
    const { sessions, nextCursor, runs } = await listSessions(params?.after, params?.after ? [] : runningSessions());
    // 有没看过的提醒的对话留圆点（刷新后仍在，服务端记着）；在别处看过了（unread 为假）去掉，请求发出后才推来的留着
    for (const s of sessions) {
      if (s.unread) seedUnread(s.id);
      else if (s.unread === false) clearUnread(s.id, asked);
    }
    noteRuns(runs);
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

// ---------------------------------------------------------------- 侧栏「在跑」核对
let checking = false;

/** 侧栏标着在跑、却没开着看的对话：向服务端各读一次此刻的运行状态（侧栏列表的 check），转圈按它留或去。没有就不发请求。 */
export async function checkRunning(): Promise<void> {
  const ids = runningSessions();
  if (!ids.length || checking) return;
  checking = true;
  try {
    noteRuns((await listSessions(undefined, ids)).runs);
  } catch {
    // 下一个时刻再核对
  } finally {
    checking = false;
  }
}

/** 回到前台、从后台缓存恢复、重新联网、实时通道断开后重连时核对一次（checkRunning）；返回取消监听。不另开定时。 */
export function watchRunning(): () => void {
  let lost = false;
  const check = () => void checkRunning();
  const onVisible = () => document.visibilityState === "visible" && check();
  const onPageShow = (event: { persisted?: boolean }) => event.persisted && check();
  const off = onConnectionChange((connected) => {
    if (!connected) lost = true;
    else if (lost) {
      lost = false;
      check();
    }
  });
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("pageshow", onPageShow);
  window.addEventListener("online", check);
  return () => {
    off();
    document.removeEventListener("visibilitychange", onVisible);
    window.removeEventListener("pageshow", onPageShow);
    window.removeEventListener("online", check);
  };
}
