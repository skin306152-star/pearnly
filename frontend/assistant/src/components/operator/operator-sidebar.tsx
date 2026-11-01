// 「实操」的侧栏：同对话侧栏一套（components/assistant-ui/thread-list.tsx 的 SidebarGroups、RowShell、RowMenu、BulkMenu、InlineRename、
// ListMore，projects.tsx 的 CompanyHeaderView），只是数据换成任务（站点的任务记录，tasks.ts）：
//   顶部「新建任务」（Ctrl / ⌘ + N）；下面按公司分组：已置顶 → 各公司（组头 ＋ = 在这家新建任务，月份默认上个月）；
//   拖动（共用拖动）只在本公司组里排先后或拖进已置顶；置顶 P、重命名 R、删除 D（右键 / 行尾「···」/ 手机长按，多选整批）；
//   就地改名；删除先问一次「删除任务？」；搜索（标题或公司名）；键盘同对话侧栏（sidebar-keys.ts 按同样的 data-slot / data-roving 找行）。
// 一行同对话行：只有标题，行尾只有对话行那两种标记（在跑脉冲点、没看过的蓝点）；进度、要你处理的只在主栏与右侧栏。
// 手排顺序、组的折叠是实操自己的一份（prefs.ts，同步进服务端偏好的 operator 份），任务号不进对话的手排顺序。

import { type ComponentPropsWithoutRef, createContext, type FC, forwardRef, memo, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { CompanyHeaderView, focusComposer, SidebarErrorLine } from "@/components/assistant-ui/projects";
import { GroupNew } from "@/components/assistant-ui/sidebar-group";
import {
  ActivityMark,
  BulkMenu,
  type CompanyHeaderProps,
  type GroupScope,
  InlineRename,
  type ItemsProps,
  ListMore,
  NavNewLabel,
  type PinScope,
  type RowDrag,
  RowMenu,
  RowScopeContext,
  RowShell,
  SidebarGroups,
  useGroupRowDrag,
  usePinRowDrag,
} from "@/components/assistant-ui/thread-list";
import { askDelete } from "@/components/delete-chats";
import { Marquee } from "@/components/ui/marquee";
import { useLongPressMenu } from "@/hooks/use-long-press";
import { op, t } from "@/i18n";
import { lastMonth, monthLabel } from "@/lib/companies";
import { shownError } from "@/lib/frappe";
import { type Activity, groupActivity } from "@/lib/managed-agents/activity";
import { FIRST_SHOWN, type ProjectRow, SHOW_MORE_STEP } from "@/lib/managed-agents/projects";
import { companySections, groupStamp, type SidebarRow } from "@/lib/managed-agents/sidebar-groups";
import type { LiftSpot, StarItem } from "@/lib/managed-agents/sidebar-order";
import { useModePlace } from "@/lib/nav";
import { useProfile } from "@/lib/profile";
import { clearSelection, rowOrder, runInOrder, useSidebarSelection } from "@/lib/sidebar-keys";
import { type Operator, useOperator } from "./controller";
import { siteSeconds } from "./live-source";
import { bindOperatorUser, taskFold, taskOrder } from "./prefs";
import { effectiveStatus, loadTasks, markSeen, noteLive, overrideStar, setTasksError, taskActivity, upsertTask, useSeen, useTasks } from "./tasks";
import type { OperatorSource, TaskRecord } from "./types";
import { withReport } from "@/lib/caught";

/** 标题：公司 · 月份（没有月份只写公司）。 */
export const taskTitle = (company: string, period?: string) => [company, period ? monthLabel(period) : ""].filter(Boolean).join(" · ");

/** 任务显示的名字：改过名的用名字，否则「公司 · 月份」。 */
export const titleOf = (task: Pick<TaskRecord, "title" | "company" | "company_title" | "period">) =>
  task.title.trim() || taskTitle(task.company_title || task.company, task.period);

/** 侧栏的行（同对话侧栏的 SidebarRow：index 是列表里的位置，group 是公司，key 是任务号）与公司组（任务里出现过的公司）。 */
export function taskSidebar(tasks: readonly TaskRecord[], stars: Readonly<Record<string, boolean>> = {}, pinning: ReadonlySet<string> = new Set()) {
  const rows: SidebarRow[] = tasks.map((task, index) => ({
    index,
    title: titleOf(task),
    starred: (stars[task.name] ?? task.starred) || pinning.has(task.name),
    time: siteSeconds(task.modified) * 1000 || Number.MAX_SAFE_INTEGER,
    group: task.company,
    period: task.period,
    created: siteSeconds(task.creation) * 1000 || undefined,
    key: task.name,
  }));
  const projects: ProjectRow[] = [];
  for (const task of tasks) {
    if (task.company && !projects.some((p) => p.id === task.company)) projects.push({ id: task.company, title: task.company_title || task.company });
  }
  return { rows, projects };
}

/** 交出去还没收尾的（等电脑空闲、进行中）不能删。 */
export const canDelete = (task: Pick<TaskRecord, "status">, live?: Parameters<typeof effectiveStatus>[1]) => {
  const status = effectiveStatus(task, live);
  return status !== "queued" && status !== "running";
};

type RowData = { task: TaskRecord; title: string; starred: boolean; activity?: Activity; deletable: boolean };
const RowsContext = createContext<ReadonlyMap<string, RowData>>(new Map());

type Actions = {
  open(name: string): void;
  newIn(project: ProjectRow): void;
  pin(names: readonly string[], starred: boolean): void;
  rename(name: string, title: string): void;
  remove(names: readonly string[]): void;
};
const ActionsContext = createContext<Actions | null>(null);
const useActions = () => useContext(ActionsContext)!;

const report = (error: unknown) => setTasksError(shownError(error));

/** 置顶 / 取消置顶一个任务（同对话的 starChat：取消置顶回到它那一组的最上面，请求没回之前按点的样子摆，失败撤回）。 */
export async function starTask(source: OperatorSource, name: string, starred: boolean, after: readonly string[] = []) {
  const item: StarItem = {
    getState: () => ({ id: name, remoteId: name, custom: { starred: !starred } }),
    updateCustom: async (custom) => upsertTask(await source.updateTask(name, { starred: !!custom.starred })),
  };
  overrideStar(name, starred);
  try {
    await taskOrder.starChat(item, starred, after);
  } finally {
    overrideStar(name, undefined);
  }
}

export const OperatorSidebar: FC<{ search?: string; onPick?: () => void }> = ({ search = "", onPick }) => {
  const o = useOperator();
  const { source } = o;
  const user = useProfile()?.user;
  useEffect(() => {
    if (user) bindOperatorUser(user);
  }, [user]);
  // 多选只在这一边（对话侧栏、任务侧栏不同时在）：换过来、换回去都清空
  useEffect(() => {
    clearSelection();
    return clearSelection;
  }, []);
  const tasks = useTasks((s) => s.rows);
  const next = useTasks((s) => s.next);
  const loaded = useTasks((s) => s.loaded);
  const loading = useTasks((s) => s.loading);
  const error = useTasks((s) => s.error);
  const stars = useTasks((s) => s.stars);
  const live = useTasks((s) => s.live);
  const seenAt = useTasks((s) => s.seenAt);
  const seen = useSeen();
  const order = taskOrder.useSidebarOrder();
  const collapsed = taskFold.useSidebarFold((s) => s.collapsed);
  const [pinning, setPinning] = useState<ReadonlySet<string>>(() => new Set());
  const [shown, setShown] = useState<Record<string, number>>({});
  const query = search.trim();

  // 这台电脑上在跑的进度（桥）：行尾在跑的标记
  useEffect(() => source.subscribe(noteLive), [source]);
  useEffect(() => {
    if (Number.isFinite(seenAt)) taskOrder.noteListRead(seenAt);
  }, [seenAt]);
  // 正开着的任务：记下看过时它的状态
  const open = tasks.find((task) => task.name === o.id);
  const openStatus = open ? effectiveStatus(open, live[open.name]) : undefined;
  useEffect(() => {
    if (open && openStatus) markSeen(open.name, openStatus);
  }, [open, openStatus]);

  // 同对话侧栏：取消置顶的从手排的置顶顺序里去掉、置顶了的从组里手排的先后里去掉（请求还没回的不算）；侧栏里没有这家公司了
  // （列表都取完才算）整组去掉；取消置顶的回到它那一组的最上面
  useEffect(() => {
    if (!loaded) return;
    const unpinned: string[] = [];
    const pinnedNow: string[] = [];
    for (const task of tasks) {
      if (taskOrder.starPending(task.name)) continue;
      if (task.starred) pinnedNow.push(task.name);
      else if (!pinning.has(task.name)) unpinned.push(task.name);
    }
    const gone = unpinned.filter((key) => order.pinned.includes(key));
    if (gone.length) taskOrder.forgetPinned(gone);
    const listed = new Set(Object.values(order.groups).flatMap((group) => group.keys));
    const left = pinnedNow.filter((key) => listed.has(key));
    if (left.length) taskOrder.forgetInGroups(left, true);
  }, [loaded, tasks, order.pinned, order.groups, pinning]);
  useEffect(() => {
    if (!loaded || next !== null) return;
    const known = new Set(tasks.map((task) => task.company));
    const gone = Object.keys(order.groups).filter((group) => group && !known.has(group));
    if (gone.length) taskOrder.forgetGroups(gone);
  }, [loaded, next, tasks, order.groups]);
  const liftTick = taskOrder.useLifts((s) => s.tick);
  const spotRef = useRef<(key: string) => LiftSpot>(() => null);
  useEffect(() => taskOrder.applyLifts(spotRef.current), [liftTick, tasks, order.groups, pinning, stars]);

  const ids = useMemo(() => tasks.map((task) => task.name), [tasks]);
  const { rows, sections } = useMemo(() => {
    const { rows, projects } = taskSidebar(tasks, stars, pinning);
    const sections = companySections(rows, projects, {
      query,
      shown,
      companyOrder: order.companies,
      pinnedOrder: order.pinned,
      groupOrders: order.groups,
    });
    return { rows, sections };
  }, [tasks, stars, pinning, query, shown, order]);
  spotRef.current = (key) => {
    const row = rows.find((entry) => entry.key === key);
    if (!row) return null;
    if (row.starred) return "starred";
    return { group: row.group, stamp: groupStamp(rows.filter((entry) => !entry.starred && entry.group === row.group)) };
  };
  const data = useMemo(() => {
    const map = new Map<string, RowData>();
    for (const task of tasks) {
      const starred = (stars[task.name] ?? task.starred) || pinning.has(task.name);
      map.set(task.name, {
        task,
        title: titleOf(task),
        starred,
        activity: taskActivity(task, live[task.name], seen, task.name === o.id),
        deletable: canDelete(task, live[task.name]),
      });
    }
    return map;
  }, [tasks, stars, pinning, live, seen, o.id]);

  // 动作：一份（行、组头、菜单都是记住的，拿最新的 Operator 做）
  const latest = useRef<{ o: Operator; data: ReadonlyMap<string, RowData>; onPick?: () => void }>({ o, data, onPick });
  latest.current = { o, data, onPick };
  const actions = useMemo<Actions>(
    () => ({
      open(name) {
        latest.current.o.select(name);
        latest.current.onPick?.();
      },
      newIn(project) {
        latest.current.o.create({ company: project.id, company_title: project.title, period: lastMonth() });
        latest.current.onPick?.();
      },
      pin(names, starred) {
        // 一起取消置顶的按侧栏里眼前的先后回到组顶：逐条做，后一条排在同批已经回来的那几条后面（同对话的多选）
        const shownOrder = starred ? [] : rowOrder(document.querySelector('[data-slot="aui_thread-list-root"]'));
        const ordered = starred ? [...names] : [...names].sort((a, b) => shownOrder.indexOf(a) - shownOrder.indexOf(b));
        const back: string[] = [];
        void runInOrder(ordered, async (name) => {
          if (latest.current.data.get(name)?.starred === starred) return;
          await starTask(source, name, starred, [...back]);
          back.push(name);
        }, report).then(() => names.length > 1 && clearSelection());
      },
      rename(name, title) {
        const task = latest.current.data.get(name)?.task;
        if (task) upsertTask({ ...task, title });
        void source.updateTask(name, { title }).then(upsertTask, (error: unknown) => {
          if (task) upsertTask(task);
          report(error);
        });
      },
      remove(names) {
        const ids = names.filter((name) => latest.current.data.get(name)?.deletable);
        if (!ids.length) return;
        askDelete(ids, () => runInOrder(ids, (name) => latest.current.o.remove(name), report).then(clearSelection), {
          title: op.deleteTask,
          current: () => useModePlace.getState().batch,
        });
      },
    }),
    [source],
  );

  const pinOne = useCallback(
    async (name: string) => {
      if (latest.current.data.get(name)?.task.starred) return;
      upsertTask(await source.updateTask(name, { starred: true }));
    },
    [source],
  );
  const more = useCallback(() => void loadTasks(source, useTasks.getState().rows.length).catch(withReport(undefined, "operator-tasks")), [source]);

  return (
    <nav className="flex min-w-0 flex-col gap-(--cc-row-gap)" aria-label={op.tasks} data-slot="cc-operator-sidebar">
      <button
        type="button"
        className="cc-nav-item"
        data-slot="cc-operator-new"
        data-roving=""
        title={op.newTask}
        data-active={!o.id || undefined}
        onClick={() => {
          actions.open("");
          focusComposer();
        }}
      >
        <NavNewLabel label={op.newTask} />
      </button>
      <div data-slot="aui_thread-list-root" className="flex flex-col gap-(--cc-space-2)">
        <div data-slot="aui_thread-list-items" className="flex flex-col gap-(--cc-space-2)">
          <ActionsContext.Provider value={actions}>
            <RowsContext.Provider value={data}>
              <SidebarGroups
                ids={ids}
                rows={rows}
                sections={sections}
                byCompany
                searching={!!query}
                sort={{ by: "updated", desc: true }}
                orders={taskOrder}
                collapsed={collapsed}
                toggleSection={taskFold.toggleFoldSection}
                keyOf={(id) => id}
                starredOf={(id) => !!data.get(id)?.task.starred}
                pinOne={pinOne}
                setPinning={setPinning}
                Items={TaskItems}
                Header={TaskCompanyHeader}
                sessionsOf={(indices) => indices.map((index) => ids[index]!).filter(Boolean)}
                moreOf={(section) =>
                  section.more
                    ? () => setShown((s) => ({ ...s, [section.project.id]: (s[section.project.id] ?? FIRST_SHOWN) + SHOW_MORE_STEP }))
                    : undefined}
                sortMenu={false}
                onError={report}
                top={<SidebarErrorLine error={error} onClose={() => setTasksError("")} />}
                empty={<div data-slot="aui_thread-list-empty" className="cc-empty-line">{t("noResults")}</div>}
              />
            </RowsContext.Provider>
          </ActionsContext.Provider>
          {loaded && !tasks.length && !error && !query && <div className="cc-empty-line">{op.tasksEmpty}</div>}
          <ListMore hasMore={next !== null} loading={loading && loaded} searching={!!query} onMore={more} />
        </div>
      </div>
    </nav>
  );
};

// ---------------------------------------------------------------- 行
const TaskItems = memo(function TaskItems({ indices, ids }: ItemsProps) {
  return indices.map((index) => <TaskItem key={ids[index]} id={ids[index]!} />);
});

/** 一行在哪一种拖动里（同对话行 ThreadListItem）：置顶里排先后、组里排先后或拖进置顶。 */
const TaskItem: FC<{ id: string }> = ({ id }) => {
  const scope = useContext(RowScopeContext);
  if (scope?.mode === "pin") return scope.guest?.local === id ? <TaskGroupRow id={id} scope={scope.guest.scope} /> : <TaskPinnedRow id={id} scope={scope} />;
  if (scope?.mode === "row") return <TaskGroupRow id={id} scope={scope} />;
  return <TaskRow id={id} />;
};

const TaskPinnedRow: FC<{ id: string; scope: PinScope }> = ({ id, scope }) => {
  const title = useContext(RowsContext).get(id)?.title ?? "";
  const { drag, onMove } = usePinRowDrag(scope, id, title);
  return <TaskRow id={id} drag={drag} onMove={onMove} />;
};

const TaskGroupRow: FC<{ id: string; scope: GroupScope }> = ({ id, scope }) => {
  const title = useContext(RowsContext).get(id)?.title ?? "";
  const { drag, onMove } = useGroupRowDrag(scope, id, id, title, op.taskCount);
  return <TaskRow id={id} drag={drag} onMove={onMove} />;
};

/** 任务行的开关（普通按钮；对话行用组件库的切换钮）。 */
const RowButton = forwardRef<HTMLButtonElement, ComponentPropsWithoutRef<"button">>((props, ref) => <button ref={ref} type="button" {...props} />);
RowButton.displayName = "RowButton";

/** 一行（同对话行 Row：RowShell 外壳、行尾标记、「···」菜单、就地改名）。 */
const TaskRow = memo(function TaskRow({ id, drag, onMove }: { id: string; drag?: RowDrag; onMove?: (delta: -1 | 1) => void }) {
  const row = useContext(RowsContext).get(id);
  const actions = useActions();
  const [renaming, setRenaming] = useState(false);
  const { open: menuOpen, setOpen: setMenuOpen, modal: menuModal, press } = useLongPressMenu();
  const startRename = useCallback(() => setRenaming(true), []);
  const current = useModePlace((s) => s.batch === id);
  const selected = useSidebarSelection((s) => s.ids.includes(id));
  if (!row) return null;
  return (
    <RowShell
      Root="div"
      Trigger={RowButton}
      drag={drag}
      onMove={onMove}
      localId={id}
      current={current}
      selected={selected}
      companies
      press={press}
      renaming={renaming}
      onMenuKey={() => setMenuOpen(true)}
      onTrigger={() => actions.open(id)}
      rename={<InlineRename value={row.title} onSave={(title) => actions.rename(id, title)} onDone={() => setRenaming(false)} />}
      label={<TaskLabel title={row.title} activity={row.activity} />}
      menu={<TaskMenu id={id} open={menuOpen} modal={menuModal} onOpenChange={setMenuOpen} onRename={startRename} />}
    />
  );
});

/** 行里的字：只有标题（同对话行，没有第二行），行尾只有在跑 / 没看过的标记。 */
const TaskLabel = memo(function TaskLabel({ title, activity }: { title: string; activity?: Activity }) {
  return (
    <>
      <span className="cc-row-text">
        <Marquee data-slot="aui_thread-list-item-title" className="cc-row-title">
          {title}
        </Marquee>
      </span>
      <span className="cc-row-end">{activity && <ActivityMark activity={activity} />}</span>
    </>
  );
});

/** 行尾「···」（同对话行）：置顶 P、重命名 R、删除 D（交出去还没收尾的置灰）；多选两条以上时「置顶 N」「删除 N」。 */
const TaskMenu = memo(function TaskMenu({
  id,
  open,
  modal,
  onOpenChange,
  onRename,
}: {
  id: string;
  open: boolean;
  modal: boolean;
  onOpenChange: (open: boolean) => void;
  onRename: () => void;
}) {
  const rows = useContext(RowsContext);
  const actions = useActions();
  const selection = useSidebarSelection((s) => s.ids);
  const row = rows.get(id);
  const bulk = selection.length > 1 && selection.includes(id);
  const close = () => onOpenChange(false);
  const picked = selection.filter((name) => rows.has(name));
  const allPinned = picked.every((name) => rows.get(name)!.starred);
  return (
    <RowMenu
      open={open}
      modal={modal}
      onOpenChange={onOpenChange}
      starred={!!row?.starred}
      onPin={() => actions.pin([id], !row?.starred)}
      onRename={onRename}
      onDelete={() => actions.remove([id])}
      canDelete={!!row?.deletable}
      bulk={
        bulk ? (
          <BulkMenu count={picked.length} allPinned={allPinned} onPinAll={() => actions.pin(picked, !allPinned)} onDeleteAll={() => actions.remove(picked)} close={close} />
        ) : undefined
      }
    />
  );
});

/** 公司组头（同对话的公司组头 CompanyHeaderView）：收起时组里有在跑 / 没看过的，名字后一个点；＋ = 在这家新建任务。 */
const TaskCompanyHeader = memo(function TaskCompanyHeader({ project, open, onToggle, sessions, handle, onMove }: CompanyHeaderProps) {
  const rows = useContext(RowsContext);
  const actions = useActions();
  const activity = groupActivity(sessions.map((name) => rows.get(name)?.activity));
  const onNew = useCallback(() => actions.newIn(project), [actions, project]);
  return (
    <CompanyHeaderView project={project} open={open} onToggle={onToggle} handle={handle} onMove={onMove} activity={activity}>
      <GroupNew onClick={onNew} label={op.newTask} />
    </CompanyHeaderView>
  );
});
