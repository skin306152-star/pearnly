// 公司资料的卡片（ADR-020，只在 Cowork）：右侧会话栏（components/session-panel.tsx）与公司页（company-page.tsx 的概览、
// 「资料」「定时任务」标签）共用同一套，日常在会话栏里改，公司页做管理和总览。
//
//   卡片外框 PanelCard（设计文档 3.13）：透明底 + 1px 内环、圆角 10；卡头 26 高，整行（标题 + 箭头 + 右侧淡色摘要）点了折叠；
//            卡头右侧操作（＋）与 6 点拖动把手悬停才出；在会话栏里（PanelControlContext）折叠、顺序记在本机，拖动或
//            Alt+↑/↓ 调顺序；别处（公司页）各自记在页面里，默认展开。
//   各卡     待处理（这家的，当场选沿用 F18 规则）→ 知识库（新增 / 修改有「取消」「保存」，F11e）→ 单据文件夹（只有 CC 桌面版
//            窗口能选，桥 pick_folder 的结果原样交给 folder_bind）→ 账套（多分店按分店分组，覆盖本月的高亮）→ 定时任务
//            （点一行跳任务页，「＋」带预填）→ 申报草稿（按分店分组）→ 记住的选择（按类别；付款方式、简易税票抵不抵能直接改，
//            其余只能删；Express 历史学到的只读可删）。没内容的卡在会话栏里不显示（keepEmpty 时照样显示，公司页用）。
//
// 文案走 i18n.ts 的 co；样式只用 globals.css 的 .cc-* 与变量。

import { ChevronRightIcon, FolderIcon, GripVerticalIcon, LoaderIcon, PlusIcon, XIcon } from "lucide-react";
import {
  createContext,
  type FC,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useStartIn } from "@/components/assistant-ui/projects";
import { PendingList } from "@/components/pending";
import { branchName } from "@/components/period-chip";
import { co, t } from "@/i18n";
import {
  type AccountSet,
  bindFolder,
  type Branch,
  type Choice,
  type CompanyFolder,
  deleteNote,
  type Detail,
  type Draft,
  forgetChoice,
  formatWhen,
  monthLabel,
  type Note,
  saveNote,
  setChoice,
  setForPeriod,
  taskSeedOf,
  unbindFolder,
  useCompanyNav,
  usePendingCounts,
} from "@/lib/companies";
import { knownReaders, openSettings, pickFolder, rememberReader, useDesktopFeatures } from "@/lib/desktop";
import { useProjects } from "@/lib/managed-agents/projects";
import { useView } from "@/lib/nav";
import { shownError } from "@/lib/frappe";
import { touchOnly } from "@/lib/touch";

const NOTE_MAX = 500; // 同服务端：每条 ≤500 字

/** 覆盖这个月的账套（没有月份或都不覆盖时取最新可写的；lib/companies 的 setForPeriod）。 */
export const setFor: (sets: readonly AccountSet[], period: string) => AccountSet | undefined = setForPeriod;

/** 文件夹状态的字。 */
export function folderState(folder: Pick<CompanyFolder, "state">): string {
  switch (folder.state) {
    case "ok":
      return co.folderOk;
    case "waiting":
      return co.folderWaiting;
    case "offline":
      return co.folderOffline;
    default:
      return co.folderMissing;
  }
}

/** 记住的选择分小节：Express 按类别，其余按来源（vat 预设叫「增值税核对」，免得与简易税票那一类混）。 */
export function choiceSection(choice: Pick<Choice, "source" | "kind">): string {
  if (choice.source === "express") return choice.kind;
  if (choice.source === "vat") return "vatCheck";
  return choice.source;
}

const SECTION_ORDER = ["party", "account", "payment", "vat", "item", "department", "receipt", "pos", "income", "vatCheck", "history"];

// ---------------------------------------------------------------- 卡片外框
/** 会话栏给卡片的控制：折叠（记在本机）、调顺序（Alt+↑/↓、拖动把手）。公司页不给，卡片各自记折叠、没有把手。 */
export type PanelControl = {
  collapsed: readonly string[];
  toggle: (id: string) => void;
  move: (id: string, delta: number) => void;
  drag: (id: string, e: ReactPointerEvent<HTMLElement>) => void;
  dragging: string | null;
};
export const PanelControlContext = createContext<PanelControl | null>(null);

/** 一张卡：卡头整行（标题、箭头、摘要）是一个折叠开关；右侧操作与拖动把手；forceOpen = 正在里面新增 / 编辑，折叠了也展开。 */
export const PanelCard: FC<{
  id: string;
  title: string;
  summary?: ReactNode;
  action?: ReactNode;
  slot?: string;
  forceOpen?: boolean;
  children: ReactNode;
}> = ({ id, title, summary, action, slot, forceOpen = false, children }) => {
  const control = useContext(PanelControlContext);
  const [folded, setFolded] = useState(false);
  const collapsed = !forceOpen && (control ? control.collapsed.includes(id) : folded);
  const toggle = () => (control ? control.toggle(id) : setFolded((v) => !v));
  const keys = (e: ReactKeyboardEvent) => {
    if (!control || !e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    e.preventDefault();
    // 挪动时 React 可能把这个节点摘下再插回，焦点会丢：挪完把焦点放回原来的按钮
    const el = e.currentTarget as HTMLElement;
    control.move(id, e.key === "ArrowUp" ? -1 : 1);
    requestAnimationFrame(() => {
      if (el.isConnected && document.activeElement !== el) el.focus();
    });
  };
  const shownSummary = summary !== undefined && summary !== null && summary !== "" && summary !== 0;
  return (
    <section className="cc-panel-card" data-card={id} data-slot={slot} data-dragging={control?.dragging === id || undefined}>
      <div className="cc-panel-card-head">
        <button type="button" className="cc-panel-card-toggle" aria-expanded={!collapsed} onClick={toggle} onKeyDown={keys}>
          <span className="truncate">{title}</span>
          <ChevronRightIcon className="cc-panel-card-chevron" aria-hidden />
          {shownSummary && <span className="cc-panel-card-summary">{summary}</span>}
        </button>
        {(action || control) && (
          <span className="cc-panel-card-tools">
            {action}
            {control && (
              <button
                type="button"
                className="cc-panel-btn cc-panel-grip"
                aria-label={t("reorder")}
                title={t("reorder")}
                onPointerDown={(e) => control.drag(id, e)}
                onKeyDown={keys}
              >
                <GripVerticalIcon aria-hidden />
              </button>
            )}
          </span>
        )}
      </div>
      {!collapsed && <div className="cc-panel-card-body">{children}</div>}
    </section>
  );
};

// ---------------------------------------------------------------- 待处理
/** 这家的待处理（当场选、交给 CC 都在 PendingList 里，F18 规则不变）；计数为 0 不显示。 */
export const PendingCard: FC<{ company: string }> = ({ company }) => {
  const count = usePendingCounts((s) => s.companies[company] ?? 0);
  if (!count) return null;
  return (
    <PanelCard id="pending" title={co.pending} summary={count} slot="cc-company-pending">
      <PendingList company={company} embedded />
    </PanelCard>
  );
};

// ---------------------------------------------------------------- 知识库
/** 同服务端 notes.clean：连续空白（含换行）并成一个空格。 */
export const noteText = (text: string) => text.replace(/\s+/g, " ").trim();

/** 存好的那一条放进列表：改的替换原位，新的接在最后（服务端按创建先后列）。 */
export function putNote(notes: readonly Note[], saved: Note): Note[] {
  return notes.some((n) => n.id === saved.id) ? notes.map((n) => (n.id === saved.id ? { ...n, ...saved } : n)) : [...notes, saved];
}

/** 存一条，回「把它放进列表」：页面直接用服务端回的那一条，不等之后整页重取（重取失败时也不丢；2026-10-15 F11e）。
 *  回的是更新函数，交给 setState 时套在那一刻的列表上（保存期间整页重取到了也不被盖回旧的）。 */
export async function commitNote(company: string, text: string, note?: string): Promise<(notes: readonly Note[]) => Note[]> {
  const saved = await saveNote(company, text, note);
  return (notes) => (saved?.id ? putNote(notes, saved) : [...notes]);
}

/** 删一条，回「从列表里拿掉它」。 */
export async function removeNote(company: string, note: string): Promise<(notes: readonly Note[]) => Note[]> {
  await deleteNote(company, note);
  return (notes) => notes.filter((n) => n.id !== note);
}

export const NotesSection: FC<{ company: string; notes: Note[]; reload: () => Promise<void>; keepEmpty?: boolean }> = ({
  company,
  notes,
  reload,
  keepEmpty = false,
}) => {
  // 页面上显示的知识库：整页重取到了就以服务端为准；存 / 删成功时先按服务端的回应改这里
  const [list, setList] = useState(notes);
  useEffect(() => setList(notes), [notes]);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState("");
  const [error, setError] = useState("");
  const remove = async (id: string) => {
    setError("");
    try {
      setList(await removeNote(company, id));
      void reload();
    } catch (e) {
      setError(shownError(e));
    }
  };
  /** 编辑框的保存：成功就收起编辑框；失败抛给编辑框，它留着字并显示报错。 */
  const save = async (text: string, id?: string) => {
    setList(await commitNote(company, text, id));
    if (id) setEditing("");
    else setAdding(false);
    void reload();
  };
  if (!keepEmpty && !list.length && !adding && !error) return null;
  return (
    <PanelCard
      id="notes"
      title={co.notes}
      summary={list.length}
      slot="cc-company-notes"
      forceOpen={adding || !!editing}
      action={
        <button type="button" className="cc-panel-btn" aria-label={co.add} title={co.add} onClick={() => setAdding(true)}>
          <PlusIcon />
        </button>
      }
    >
      <div className="flex flex-col">
        {list.map((note) =>
          editing === note.id ? (
            <NoteEditor key={note.id} initial={note.text} onSave={(text) => save(text, note.id)} onCancel={() => setEditing("")} />
          ) : (
            <div key={note.id} className="cc-note group/note">
              <button type="button" className="min-w-0 flex-1 text-start" onClick={() => setEditing(note.id)}>
                {note.text}
              </button>
              <button
                type="button"
                className="cc-panel-btn opacity-0 group-hover/note:opacity-100 focus-visible:opacity-100 max-md:opacity-100"
                aria-label={t("delete")}
                title={t("delete")}
                onClick={() => void remove(note.id)}
              >
                <XIcon />
              </button>
            </div>
          ),
        )}
        {adding && <NoteEditor initial="" onSave={(text) => save(text)} onCancel={() => setAdding(false)} />}
        {error && <p role="alert" className="text-danger text-sm">{error}</p>}
      </div>
    </PanelCard>
  );
};

/** 编辑框里按键要不要保存：电脑上回车保存（Shift+回车换行，原有快捷键）；手机上回车换行、保存靠按钮；
 *  Ctrl / ⌘+回车两边都保存。选字中的回车（isComposing、keyCode 229）不算。 */
export function noteKey(e: { key: string; shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean; keyCode?: number;
  nativeEvent?: { isComposing?: boolean } }, touch: boolean): "save" | "cancel" | null {
  if (e.key === "Escape") return "cancel";
  if (e.key !== "Enter" || e.nativeEvent?.isComposing || e.keyCode === 229) return null;
  if (e.ctrlKey || e.metaKey) return "save";
  return touch || e.shiftKey ? null : "save";
}

/** 新增、修改一条知识库：多行输入框 + 「取消」「保存」（电脑与手机一样）。保存中按钮不可点；失败时留着字、在下面显示报错。 */
export const NoteEditor: FC<{ initial: string; onSave: (text: string) => Promise<void>; onCancel: () => void }> = ({
  initial,
  onSave,
  onCancel,
}) => {
  const [value, setValue] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const text = noteText(value);
  const canSave = !!text && text !== noteText(initial) && !busy;
  const save = async () => {
    if (!canSave) return;
    setBusy(true);
    setError("");
    try {
      await onSave(text);
    } catch (e) {
      setError(shownError(e));
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col gap-2 py-1" data-slot="cc-note-editor">
      <textarea
        autoFocus
        rows={2}
        maxLength={NOTE_MAX}
        value={value}
        aria-label={co.notes}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          const action = noteKey(e, touchOnly());
          if (!action) return;
          e.preventDefault();
          if (action === "cancel") onCancel();
          else void save();
        }}
        className="cc-input h-auto w-full py-1.5"
      />
      {error && <p role="alert" className="text-danger text-sm">{error}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" className="cc-btn cc-tap" disabled={busy} onClick={onCancel}>
          {t("cancel")}
        </button>
        <button type="button" className="cc-btn cc-tap" data-primary="" disabled={!canSave} onClick={() => void save()}>
          {t("save")}
        </button>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------- 单据文件夹
/** hideEmpty（会话栏）：浏览器、手机里没有绑定的文件夹时整张卡不显示（这里什么也做不了）。 */
export const FolderSection: FC<{ company: string; folders: CompanyFolder[]; reload: () => Promise<void>; hideEmpty?: boolean }> = ({
  company,
  folders: all,
  reload,
  hideEmpty = false,
}) => {
  const { app, companyFolders } = useDesktopFeatures();
  // 刚移除的（服务端已删）先不显示，不等整页重取；重取到了以服务端为准
  const [gone, setGone] = useState<string[]>([]);
  useEffect(() => setGone([]), [all]);
  const folders = all.filter((f) => !gone.includes(f.reader));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const readers = knownReaders();
  const mine = (f: CompanyFolder) => !!f.mine || readers.includes(f.reader);
  const hasMine = folders.some(mine);
  const choose = async () => {
    setError("");
    setBusy(true);
    try {
      const picked = await pickFolder();
      if (!picked) return;
      if (!picked.ok) {
        if (!picked.cancelled) setError(picked.message);
        return;
      }
      rememberReader(picked.binding.reader);
      await bindFolder(company, picked.binding);
      setGone((before) => before.filter((reader) => reader !== picked.binding.reader));
      await reload();
    } catch (e) {
      setError(shownError(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async (reader: string) => {
    setError("");
    try {
      await unbindFolder(company, reader);
      setGone((before) => [...before, reader]);
      void reload();
    } catch (e) {
      setError(shownError(e));
    }
  };
  if (hideEmpty && !app && !folders.length && !error) return null;
  return (
    <PanelCard id="folders" title={co.docFolder} slot="cc-company-folders">
      <div className="flex flex-col gap-1">
        {folders.map((f) => (
          <div key={`${f.reader}-${f.device}`} className="cc-folder-line" data-state={f.state}>
            <FolderIcon />
            <span className="min-w-0 flex-1">
              <span className="block truncate" title={f.label}>{[f.device, f.label].filter(Boolean).join(" · ")}</span>
              <span className="block truncate text-ink-3 text-xs">
                {[folderState(f), f.month_folder ? co.monthFolder(f.month_folder) : ""].filter(Boolean).join(" · ")}
              </span>
            </span>
            {app && companyFolders && mine(f) && (
              <>
                <button type="button" className="cc-link-btn" disabled={busy} onClick={() => void choose()}>
                  {co.changeFolder}
                </button>
                <button type="button" className="cc-link-btn" disabled={busy} onClick={() => void remove(f.reader)}>
                  {co.removeFolder}
                </button>
              </>
            )}
          </div>
        ))}
        {app && companyFolders && !hasMine && (
          <button type="button" className="cc-btn self-start" data-slot="cc-choose-folder" disabled={busy} onClick={() => void choose()}>
            {busy ? <LoaderIcon className="size-4 animate-spin" /> : <FolderIcon className="size-4" />}
            {co.chooseOnComputer}
          </button>
        )}
        {app && !companyFolders && (
          <button type="button" className="cc-btn self-start" onClick={() => openSettings("system")}>
            {co.updateDesktop}
          </button>
        )}
        {!app && !folders.length && <p className="text-ink-3 text-sm">{co.chooseOnComputer}</p>}
        {error && <p role="alert" className="text-danger text-sm">{error}</p>}
      </div>
    </PanelCard>
  );
};

// ---------------------------------------------------------------- 账套
const SetRow: FC<{ set: AccountSet; covering: boolean }> = ({ set, covering }) => (
  <div className="cc-list-row cursor-default" data-covering={covering || undefined}>
    <span className="min-w-0 flex-1 truncate">{set.label}</span>
    <span className="shrink-0 text-ink-3 text-xs">{set.year_label}</span>
  </div>
);

export const SetsSection: FC<{ sets: AccountSet[]; branches: Branch[]; period: string }> = ({ sets, branches, period }) => {
  const covering = setFor(sets, period);
  if (!sets.length) return null;
  return (
    <PanelCard id="sets" title={co.accountSets} slot="cc-company-sets">
      {branches.length ? (
        branches.map((b) => (
          <div key={b.branch} className="flex flex-col">
            <div className="cc-subheading">{branchName(b)}</div>
            {b.account_sets.map((set) => (
              <SetRow key={`${set.connection}-${set.key}`} set={set} covering={setFor(b.account_sets, period) === set} />
            ))}
          </div>
        ))
      ) : (
        <div className="flex flex-col">
          {sets.map((set) => <SetRow key={`${set.connection}-${set.key}`} set={set} covering={covering === set} />)}
        </div>
      )}
    </PanelCard>
  );
};

// ---------------------------------------------------------------- 定时任务
/** 这家的定时任务：点一行到任务页并定位，「＋」到任务页接着开新建表单（预填覆盖本月的账套，本机绑了文件夹时连同文件夹）。 */
export const TasksSection: FC<{ company: string; detail: Detail; period: string; keepEmpty?: boolean }> = ({
  company,
  detail,
  period,
  keepEmpty = false,
}) => {
  const { setView } = useView();
  const { app, companyFolders } = useDesktopFeatures();
  const tasks = detail.tasks ?? [];
  const open = (name: string) => {
    useCompanyNav.setState({ taskCompany: company, taskFocus: name, taskSeed: null });
    setView("tasks");
  };
  const add = () => {
    useCompanyNav.setState({
      taskCompany: company,
      taskFocus: "",
      taskSeed: taskSeedOf(detail, period, knownReaders(), app && companyFolders),
    });
    setView("tasks");
  };
  if (!detail.tasks || (!tasks.length && !keepEmpty)) return null;
  return (
    <PanelCard
      id="tasks"
      title={t("scheduledTasks")}
      summary={tasks.length}
      slot="cc-company-tasks"
      action={
        <button type="button" className="cc-panel-btn" aria-label={co.add} title={co.add} onClick={add}>
          <PlusIcon />
        </button>
      }
    >
      <div className="flex flex-col">
        {tasks.map((task) => (
          <button key={task.name} type="button" className="cc-list-row" onClick={() => open(task.name)}>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-ink">{task.title}</span>
              <span className="block truncate text-ink-3 text-xs">{[task.account_label, task.folder_label].filter(Boolean).join(" · ")}</span>
            </span>
          </button>
        ))}
      </div>
    </PanelCard>
  );
};

// ---------------------------------------------------------------- 申报草稿
const branchOfDraft = (d: Draft) => d.branch ?? d.company_branch ?? "";

export const DraftsSection: FC<{ company: string; drafts: Draft[]; branches: Branch[]; combined: boolean }> = ({ company, drafts, branches, combined }) => {
  const start = useStartIn();
  const withPeriod = useProjects((s) => s.period);
  if (!drafts.length) return null;
  const open = (d: Draft) => {
    const period = /^\d{4}-\d{2}/.test(d.period) ? d.period.slice(0, 7) : "";
    start({ project: company, period: withPeriod ? period : "", scope: "" }, co.draftText(d.kind, period ? monthLabel(period) : d.period), false);
  };
  const row = (d: Draft) => (
    <button key={d.name} type="button" className="cc-list-row" onClick={() => open(d)}>
      <span className="min-w-0 flex-1 truncate text-ink">{[d.period, d.kind].filter(Boolean).join(" · ")}</span>
      <span className="shrink-0 text-ink-3 text-xs">{formatWhen(d.updated_at)}</span>
    </button>
  );
  const grouped = branches.length > 1 && !combined;
  return (
    <PanelCard id="drafts" title={co.taxDrafts} summary={drafts.length} slot="cc-company-drafts">
      {combined && <span className="cc-chip self-start">{co.combinedVat}</span>}
      {grouped ? (
        branches.map((b) => {
          const mine = drafts.filter((d) => branchOfDraft(d) === b.branch);
          if (!mine.length) return null;
          return (
            <div key={b.branch} className="flex flex-col">
              <div className="cc-subheading">{branchName(b)}</div>
              {mine.map(row)}
            </div>
          );
        })
      ) : (
        <div className="flex flex-col">{drafts.map(row)}</div>
      )}
    </PanelCard>
  );
};

// ---------------------------------------------------------------- 记住的选择
const EDITABLE: Record<string, Array<{ value: string; label: () => string }>> = {
  payment: [
    { value: "cash", label: () => co.payCash },
    { value: "credit", label: () => co.payCredit },
  ],
  vat: [
    { value: "claim", label: () => co.vatClaim },
    { value: "expense", label: () => co.vatExpense },
  ],
};

/** 改了一条记住的选择后列表的样子：值与显示名换成刚选的（选项的显示名在 EDITABLE）。 */
export function setChoiceShown(choices: readonly Choice[], key: string, value: string): Choice[] {
  return choices.map((c) => {
    if (c.key !== key) return c;
    const label = EDITABLE[c.kind]?.find((o) => o.value === value)?.label();
    return { ...c, value, ...(label ? { value_label: label } : {}) };
  });
}

/** choices：服务端在没有录入权限时给 null，按没有处理。 */
export const ChoicesSection: FC<{ company: string; choices: Choice[] | null; period: string; reload: () => Promise<void> }> = ({
  company,
  choices,
  period,
  reload,
}) => {
  // 页面上显示的：改 / 删成功时先按刚做的改这里（下拉框不弹回旧值），整页重取到了以服务端为准
  const [list, setList] = useState(choices ?? []);
  useEffect(() => setList(choices ?? []), [choices]);
  const [error, setError] = useState("");
  const groups = useMemo(() => {
    const map = new Map<string, Choice[]>();
    for (const c of list) map.set(choiceSection(c), [...(map.get(choiceSection(c)) ?? []), c]);
    return [...map.entries()].sort((a, b) => SECTION_ORDER.indexOf(a[0]) - SECTION_ORDER.indexOf(b[0]));
  }, [list]);
  if (!list.length) return null;
  const run = async (work: () => Promise<unknown>, shown: (rows: readonly Choice[]) => Choice[]) => {
    setError("");
    try {
      await work();
      setList(shown);
      void reload();
    } catch (e) {
      setError(shownError(e));
    }
  };
  return (
    <PanelCard id="choices" title={co.remembered} summary={list.length} slot="cc-company-choices">
      {groups.map(([section, rows]) => (
        <div key={section} className="flex flex-col">
          <div className="cc-subheading">{co.kind[section] ?? section}</div>
          {rows.map((c) => (
            <ChoiceRow
              key={c.key}
              choice={c}
              onSet={(v) => void run(() => setChoice(company, c.key, v, period), (rows) => setChoiceShown(rows, c.key, v))}
              onForget={() => void run(() => forgetChoice(company, c.key), (rows) => rows.filter((row) => row.key !== c.key))}
            />
          ))}
        </div>
      ))}
      {error && <p role="alert" className="text-danger text-sm">{error}</p>}
    </PanelCard>
  );
};

export const ChoiceRow: FC<{ choice: Choice; onSet: (value: string) => void; onForget: () => void }> = ({ choice, onSet, onForget }) => {
  const history = choice.source === "history";
  const options = choice.source === "express" && choice.editable !== false ? EDITABLE[choice.kind] : undefined;
  const subject = choice.subject_label || choice.supplier_name || "";
  const value = choice.value_label || choice.account_label || choice.value;
  const meta = history
    ? [co.fromHistory(Number(choice.count) || 0), formatWhen(choice.last_date ?? choice.set_at)]
    : [choice.year_label ?? "", choice.set_by ?? "", formatWhen(choice.set_at)];
  return (
    <div className="cc-choice-row" data-slot="cc-choice-row" data-source={choice.source}>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-ink">
          {subject}
          {!options && <span className="text-ink-3">{` → ${value}`}</span>}
        </span>
        <span className="block truncate text-ink-3 text-xs">{meta.filter(Boolean).join(" · ")}</span>
      </span>
      {options && (
        <select className="cc-input h-8 w-auto" aria-label={subject} value={choice.value} onChange={(e) => onSet(e.target.value)}>
          {options.map((o) => (
            <option key={o.value} value={o.value}>{o.label()}</option>
          ))}
        </select>
      )}
      <button type="button" className="cc-panel-btn" aria-label={t("delete")} title={t("delete")} onClick={onForget}>
        <XIcon />
      </button>
    </div>
  );
};
