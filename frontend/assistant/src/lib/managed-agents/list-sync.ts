// 侧栏多设备同步（同 Claude，教程第 7 条 2026-10-13）：同一账号在别的设备上新建、改名（含服务端起好的标题）、加星标、
// 归档、删除对话时，服务端按用户推一条 pearnly.session（pearnly_core.assistant.sidebar，经 realtime.ts 转来），
// 这里并进本页的侧栏：
//   - 改名、星标：就地改那一行（组件库自己的 rename / updateCustom；服务端已经改好了，会话列表接口这次不再发请求：quiet）；
//   - 归档、删除：从侧栏去掉（同上；正开着它就由组件库切到新对话）；
//   - 新建、或推来的是本页还没加载的对话：重拉一次列表（组件库没有只插一行的接口）——几条合成一次，列表在加载、
//     新对话正在建时先不拉，到时已在列表里就不拉（notice-reload.ts）。
// 回到前台、从后台缓存恢复、重新联网、实时通道断开后重连，这几个时刻补一次核对（watchList）：读一次第一页（顺带核对
// 侧栏在转圈的对话，absorb），新出现的重拉、标题星标不一样的就地改、本该在这一页里却没有的（在别处删了、归档了）去掉。
// 本页自己刚改过的对话（这次请求发出之后改的）不按这一页改。不另开定时。
// 一段对话在本页可能有两行数据（本页新建的那行 + 重拉列表后服务端给的那行：组件库按编号各存一份），都改。
//
// 公司分组（projects.ts）走同一个通道：pearnly.project 的改名（upsert）、删公司（delete）、对话改月份分店或删公司时回到最近（move）
// 随推随并；移来的对话本页还没加载就重拉一次列表。核对的那几个时刻同时重取一次 projects()；产品从没有公司分组变成有
// （或反过来）时整表重拉；侧栏「分组依据」在公司与月份 / 不分组之间换（取的接口跟着换，sidebar-view.ts）时也整表重拉。
// 按公司分组的第一页里，每一组各按它自己翻到哪儿判断「不在这一页」是不是在别处删了。

import type { SessionPage } from "./api";
import { runningSessions } from "./activity";
import { noticeReloader } from "./notice-reload";
import { applyProjectChange, groupOf, refreshProjects, useProjects } from "./projects";
import { onConnectionChange, onProjectChange, onSessionChange, type SessionChange } from "./realtime";
import { absorb, firstPage, quietly, touchedSince } from "./session-list-adapter";
import { useSidebarView } from "./sidebar-view";

// 推来的几条合成一次重拉
const RELOAD_MS = 1000;

type ItemData = {
  readonly id: string;
  readonly remoteId?: string | undefined;
  readonly status: string;
  readonly title?: string | undefined;
  readonly lastMessageAt?: Date | undefined;
  readonly custom?: Record<string, unknown> | undefined;
};

/** 组件库的对话列表（AssistantRuntime.threads）用到的那几样。 */
export type ListThreads = {
  getState(): {
    readonly mainThreadId: string;
    readonly threadIds: readonly string[];
    readonly threadItems: Readonly<Record<string, ItemData>>;
    readonly isLoading: boolean;
    readonly isLoadingMore: boolean;
  };
  reload(): Promise<void>;
  getItemById(id: string): {
    rename(title: string): Promise<void>;
    updateCustom(custom: Record<string, unknown> | undefined): Promise<void>;
    archive(): Promise<void>;
    delete(): Promise<void>;
  };
};

// ---------------------------------------------------------------- 并入
// 改名、星标、归档、删除都走组件库自己的操作（乐观更新、正开着就切走都由它管），经 quietly 让会话列表接口这一次不发请求
function rowsOf(threads: ListThreads, remoteId: string): ItemData[] {
  return Object.values(threads.getState().threadItems).filter(
    (item) => item.remoteId === remoteId && (item.status === "regular" || item.status === "archived"),
  );
}

function retitle(threads: ListThreads, rows: ItemData[], title: string) {
  for (const row of rows) {
    if (row.title !== title) void quietly("rename", row.remoteId!, () => threads.getItemById(row.id).rename(title));
  }
}

function restar(threads: ListThreads, rows: ItemData[], starred: boolean) {
  for (const row of rows) {
    if (!!row.custom?.starred !== starred) {
      void quietly("custom", row.remoteId!, () => threads.getItemById(row.id).updateCustom({ ...row.custom, starred }));
    }
  }
}

function drop(threads: ListThreads, rows: ItemData[], archived: boolean) {
  for (const row of rows) {
    if (archived && row.status !== "regular") continue;
    const item = threads.getItemById(row.id);
    void quietly(archived ? "archive" : "delete", row.remoteId!, () => (archived ? item.archive() : item.delete()));
  }
}

type Reloader = { note: (session: string) => void; stop: () => void };

/** 一条推来的侧栏同步事件并进本页。 */
export function applyChange(threads: ListThreads, change: SessionChange, reloader: Reloader) {
  const rows = rowsOf(threads, change.session);
  if (change.change === "archived" || change.change === "deleted") return drop(threads, rows, change.change === "archived");
  if (!rows.length) return reloader.note(change.session);
  if (change.change === "titled" && change.title) retitle(threads, rows, change.title);
  if (change.change === "starred" && change.starred !== undefined) restar(threads, rows, change.starred);
}

const time = (value: Date | string | null | undefined) => (value ? new Date(value).getTime() : Number.NaN);

/** 侧栏第一页（asked：这次请求发出的时刻）并进本页：新出现的重拉、标题星标就地改、本该在这一页里却没有的去掉。 */
export function reconcile(threads: ListThreads, page: SessionPage, asked: number, reloader: Reloader) {
  const listed = new Set(page.sessions.map((s) => s.id));
  for (const s of page.sessions) {
    if (touchedSince(s.id, asked)) continue;
    const rows = rowsOf(threads, s.id).filter((row) => row.status === "regular");
    if (!rows.length) {
      reloader.note(s.id);
      continue;
    }
    if (s.title) retitle(threads, rows, s.title);
    restar(threads, rows, !!s.starred);
  }
  // 第一页 = 全部置顶的 + 其余最近的一页。没有下一页就是全部；有下一页时，比这一页里最早的还新的也一定在这一页里。
  // 按公司分组时第一页 = 全部置顶 + 最近一页 + 每家公司最新几条：每一组各按自己的（groups 里有这家 = 这家还有更多）。
  // 组里手排过、按手排一并给的（ordered）不算：它们可能很旧，算进来会把这组翻页取到的较新的当成在别处删了
  const grouped = page.groups !== undefined;
  const early = new Set(page.ordered ?? []);
  const oldestOf = (group: string | null) => {
    const rest = page.sessions.filter((s) => !s.starred && !early.has(s.id) && (group === null || (s.project ?? "") === group));
    return rest.length ? Math.min(...rest.map((s) => time(s.updated_at))) : Number.POSITIVE_INFINITY;
  };
  const gone = new Set<string>();
  for (const item of Object.values(threads.getState().threadItems)) {
    const id = item.remoteId;
    if (!id || item.status !== "regular" || listed.has(id) || touchedSince(id, asked)) continue;
    const group = grouped ? groupOf(id) : null;
    const more = group === null || group === "" ? !!page.nextCursor : group in page.groups!;
    if (more && !(time(item.lastMessageAt) > oldestOf(group))) continue;
    gone.add(id);
  }
  for (const id of gone) drop(threads, rowsOf(threads, id), false);
}

/** 接上侧栏同步：推来的事件随到随并；回到前台、恢复、联网、实时通道断过又连上时核对一次。返回取消。 */
export function watchList(threads: () => ListThreads): () => void {
  const view = {
    getState: () => {
      const state = threads().getState();
      return { ...state, threadItems: Object.values(state.threadItems) };
    },
    reload: () => threads().reload(),
  };
  const reloader = noticeReloader(() => view, RELOAD_MS);
  const offChange = onSessionChange((change) => applyChange(threads(), change, reloader));
  // 公司改名、删公司、对话改月份分店（删公司时回到最近）：随推随并；本页还没有这段对话就重拉
  const offProject = onProjectChange((change) => {
    const moved = applyProjectChange(change);
    if (moved && !rowsOf(threads(), moved).length) reloader.note(moved);
  });
  // 产品有没有公司分组变了（第一次读不到、后来读到了）：整表按新的方式重拉
  const offKind = useProjects.subscribe((now, before) => {
    if (before.loaded && now.kind !== before.kind) void threads().reload().catch(() => undefined);
  });
  // 按公司取用分组接口，按月份 / 不分组用普通列表：在这两类之间换时整表按新的方式重拉（月份与不分组之间换只是重排）
  const offView = useSidebarView.subscribe((now, before) => {
    const grouped = (by: string) => by === "company";
    if (useProjects.getState().kind && grouped(now.by) !== grouped(before.by)) void threads().reload().catch(() => undefined);
  });
  let checking = false;
  let again = false;
  const check = async () => {
    if (checking) {
      again = true;
      return;
    }
    checking = true;
    const asked = Date.now();
    try {
      // 公司跟着重取（别处改名、删了，推送漏了的）；第一页顺带核对侧栏标着在跑的对话（在别处跑完了、推送漏了的不再转圈）
      if (useProjects.getState().loaded) await refreshProjects();
      const page = await firstPage(runningSessions());
      absorb(page, asked);
      reconcile(threads(), page, asked, reloader);
    } catch {
      // 下一个时刻再核对
    } finally {
      checking = false;
      if (again) {
        again = false;
        void check();
      }
    }
  };
  let lost = false;
  const onVisible = () => document.visibilityState === "visible" && void check();
  const onPageShow = (event: { persisted?: boolean }) => event.persisted && void check();
  const onOnline = () => void check();
  const offConnection = onConnectionChange((connected) => {
    if (!connected) lost = true;
    else if (lost) {
      lost = false;
      void check();
    }
  });
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("pageshow", onPageShow);
  window.addEventListener("online", onOnline);
  return () => {
    offChange();
    offProject();
    offKind();
    offView();
    offConnection();
    reloader.stop();
    document.removeEventListener("visibilitychange", onVisible);
    window.removeEventListener("pageshow", onPageShow);
    window.removeEventListener("online", onOnline);
  };
}
