
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
// 侧栏多设备同步（list-sync.ts）把别处的改名、置顶、归档、删除并进本页时也走组件库的这几个操作；那时服务端已经改好了，
// 这里不再发请求（takeQuiet）。本页自己改的记下时刻（touch），核对时不拿改之前读到的一页盖掉它。
//
// 按公司分组（projects.ts，只在产品有公司分组时）：列表用 grouped=1，第一页 = 全部置顶 + 最近一页 + 每家公司最新几条；
// 之后组件库每要一页，按复合游标只翻一组（「显示更多」要的那组 → 最近 → 活动最近、还有更多的公司）。每一页带回的
// 归属（公司、处理月份、分店）记进 filings（组件库遇到已认得的编号不更新，所以不放在 custom 里）。新对话建在待建的公司里。
// 侧栏「分组依据」改成处理月份或不分组时（sidebar-view.ts）用普通列表（服务端每行同样带归属），换的时候整表重拉（list-sync.ts）。
//
// 第一页带回全部置顶的：读完第一页，本页每一行的置顶标记就对到了那次请求发出的时刻，记给侧栏按数据收拾手排顺序时判断依据新不新
// （sidebar-order.ts 的 noteListRead；list-sync 的核对同样记）。

import type { RemoteThreadListAdapter } from "@assistant-ui/react";
import { createAssistantStream } from "assistant-stream";
import {
  archiveSession,
  createSession,
  deleteSession,
  getSession,
  listGrouped,
  type ListedSession,
  listSessions,
  renameSession,
  type SessionPage,
  starSession,
} from "./api";
import { clearUnread, noteRun, runningSessions, seedUnread } from "./activity";
import { type SessionSummary, splitAttachmentNote } from "./events";
import {
  decodeCursor,
  encodeCursor,
  type Filing,
  fileLocally,
  filingFrom,
  movedSince,
  NO_FILING,
  noteCursor,
  noteFilings,
  pickGroup,
  projectOrder,
  projectsReady,
  takeRequestedGroup,
  useProjects,
} from "./projects";
import { clock } from "@/lib/prefs-sync";
import { latestTitle, listen } from "./realtime";
import { dropSessionController } from "./session-controller";
import { forgetInGroups, noteListRead } from "./sidebar-order";
import { byCompany, noteCreated } from "./sidebar-view";

const TITLE_MAX = 60; // 同服务端 assistant/contracts.py TITLE_MAX
const STARRED_MAX = 100; // 同服务端 assistant/sessions.py LIST_LIMIT：第一页最多带这么多条置顶的

/** 这一页（第一页）带回了全部置顶的（没到服务端的上限）：本页标着置顶、却不在这一页里的就不再是置顶。 */
export const allStarsIn = (page: SessionPage) => page.sessions.filter((s) => s.starred).length < STARRED_MAX;

// 第几次读第一页（重拉列表时组件库只用最后发出的那一次，先发的晚回来不算读到）
let firstReads = 0;

/** 侧栏列表带回的提醒圆点与运行状态（list 与 list-sync 的核对共用）。asked：这次请求发出的时刻。 */
export function absorb(page: SessionPage, asked: number) {
  // 有没看过的提醒的对话留圆点（刷新后仍在，服务端记着）；在别处看过了（unread 为假）去掉，请求发出后才推来的留着
  for (const s of page.sessions) {
    if (s.unread) seedUnread(s.id);
    else if (s.unread === false) clearUnread(s.id, asked);
    noteCreated(s.id, s.created_at);
  }
  for (const [id, run] of Object.entries(page.runs ?? {})) noteRun(id, run);
  // 归属：每一页都按服务端的覆盖（已认得的编号也覆盖）；请求发出后本页自己移动或新建过的不盖
  const filings: Record<string, Filing> = {};
  for (const s of page.sessions) {
    const filing = filingFrom(s);
    if (filing && !movedSince(s.id, asked) && !touchedSince(s.id, asked)) filings[s.id] = filing;
  }
  noteFilings(filings);
}

/** 这次按公司分组取吗：产品有公司分组，且侧栏「分组依据」是公司（按月份、不分组用普通列表）。 */
async function groupedListing(): Promise<boolean> {
  return !!(await projectsReady()) && byCompany();
}

/** 侧栏第一页（list 与 list-sync 的核对共用）：有公司分组且按公司分组时用分组接口。 */
export async function firstPage(check: readonly string[]): Promise<SessionPage> {
  return (await groupedListing()) ? listGrouped({ check }) : listSessions(undefined, check);
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
    const after = params?.after;
    const read = after ? 0 : ++firstReads;
    // 读完第一页：本页每一行的置顶标记对到这次请求发出的时刻（seen）
    const noteFirst = (page: SessionPage, seen: number) => {
      if (read === firstReads && allStarsIn(page)) noteListRead(seen);
    };
    if (!(await groupedListing())) {
      // 第一页顺带核对侧栏标着在跑的对话（在别处跑完了、推送漏了的不再转圈）
      const seen = clock();
      const page = await listSessions(after, after ? [] : runningSessions());
      absorb(page, asked);
      if (!after) noteFirst(page, seen);
      if (useProjects.getState().kind && !after) noteCursor({ recent: null, groups: {} });
      return { threads: page.sessions.map(toThreadMetadata), nextCursor: page.nextCursor ?? undefined };
    }
    if (!after) {
      const seen = clock();
      const page = await firstPage(runningSessions());
      absorb(page, asked);
      noteFirst(page, seen);
      const cursor = { recent: page.nextCursor || null, groups: { ...(page.groups ?? {}) } };
      noteCursor(cursor);
      return { threads: page.sessions.map(toThreadMetadata), nextCursor: encodeCursor(cursor) };
    }
    const cursor = decodeCursor(after);
    const group = pickGroup(cursor, takeRequestedGroup(), projectOrder());
    if (group === undefined) {
      noteCursor(cursor);
      return { threads: [], nextCursor: undefined };
    }
    const page = await listGrouped({ group, after: group ? cursor.groups[group] : (cursor.recent ?? "") });
    absorb(page, asked);
    const groups = { ...cursor.groups };
    if (group) {
      if (page.nextCursor) groups[group] = page.nextCursor;
      else delete groups[group];
    }
    const next = { recent: group ? cursor.recent : page.nextCursor || null, groups };
    noteCursor(next);
    return { threads: page.sessions.map(toThreadMetadata), nextCursor: encodeCursor(next) };
  },

  async initialize(threadId) {
    // Called once, on the first message (or first attachment) of a new
    // chat. The returned id becomes both remoteId and externalId, so
    // every later call keys straight off the session id.
    listen();
    // 开在待建的公司里（组头 ＋、主档页输入框、输入框上方的公司条）：拿到服务端摘要之前按本页编号归组，不先闪到「最近」；
    // 侧栏还没有这家时先搭出的这一组，会话建好之前重取 projects() 的回复不拿来拆它（projects.ts 的 fileLocally）
    const state = useProjects.getState();
    const draft = state.kind && state.draft.project ? state.draft : undefined;
    const local = draft ? fileLocally({ [threadId]: draft }) : undefined;
    let session: ListedSession;
    try {
      session = await createSession(draft);
    } catch (error) {
      local?.undo({ [threadId]: NO_FILING });
      throw error;
    }
    touch(session.id);
    const filing = filingFrom(session) ?? draft;
    const entries = filing ? { [threadId]: filing, [session.id]: filing } : {};
    if (local) local.done(entries);
    else noteFilings(entries);
    return { remoteId: session.id, externalId: session.id };
  },

  async rename(remoteId, newTitle) {
    if (takeQuiet("rename", remoteId)) return;
    touch(remoteId);
    await renameSession(remoteId, newTitle);
  },

  // 置顶（会话表 starred，只是整理标记）存在本站；列表按它把会话放进「已置顶」分组。
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
    const synced = takeQuiet("delete", remoteId);
    if (!synced) {
      touch(remoteId);
      await deleteSession(remoteId);
    }
    // 删了：从组里手排的先后里去掉（别的设备删的是按数据收拾，不是人改的；此刻这一行已经不在了）
    if (synced) noteListRead(clock(), [remoteId]);
    forgetInGroups([remoteId], synced);
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
