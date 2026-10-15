// 公司主档页（?company=<编号>；公司页、侧栏组名、顶栏「公司 ›」点进来）。一家客户一份，全所共用（Cowork companies 域）。
//
// 电脑两栏（右栏 320px），手机单栏、各节可折叠（默认只开「对话」「知识库」）：
//   顶部     名称；下面一行法定名 · 税号 · 分店；合并申报的写「合并申报」。⋯：重命名（就地改，手机用输入框）、删除公司（不确认）
//   输入框   右侧月份胶囊（最近 12 个月 + 不指定，默认上个月）与分店胶囊（多分店才有）；发出 = 在这家开对话并发出
//   对话     只列我的，按活动倒序，带月份短写；从服务端按这家取（分组列表 group=<公司>，分页），不靠侧栏加载了几条
//   右栏     待处理（这家的，没有就不出现）→ 知识库 → 单据文件夹 → 账套（多分店按分店分组）→ 定时任务 → 申报草稿
//            （按分店分组）→ 记住的选择（按类别；付款方式、简易税票抵不抵能直接改，其余只能删；Express 历史学到的只读可删）
//
// 单据文件夹只能在 CC 桌面版窗口里经 Windows 原生选择框选（桥 pick_folder，结果的 binding 原样交给 folder_bind）；
// 浏览器、手机只看状态；旧桌面版提示更新。别处改了这家（pearnly.project upsert）重取，删了（delete）回到公司页。
// 文案走 i18n.ts 的 co；样式只用 globals.css 的 .cc-* 与变量。

import { useAui } from "@assistant-ui/react";
import {
  ArrowUpIcon,
  ChevronDownIcon,
  FolderIcon,
  LoaderIcon,
  MoreHorizontalIcon,
  PencilIcon,
  PlusIcon,
  TrashIcon,
  XIcon,
} from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { type FC, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useStartIn } from "@/components/assistant-ui/projects";
import { companyLine } from "@/components/companies";
import { PendingList } from "@/components/pending";
import { BranchChip, branchName, PeriodChip } from "@/components/period-chip";
import { useSidebar } from "@/components/ui/sidebar";
import { co, t } from "@/i18n";
import {
  type AccountSet,
  bindFolder,
  type Branch,
  branchesOf,
  type Choice,
  type CompanyFolder,
  deleteCompany,
  deleteNote,
  type Detail,
  type Draft,
  dropCachedDetail,
  forgetAnyCompany,
  forgetChoice,
  formatWhen,
  lastMonth,
  loadDetail,
  monthLabel,
  monthShort,
  type Note,
  renameCompany,
  saveNote,
  setChoice,
  setForPeriod,
  taskSeedOf,
  unbindFolder,
  useCompanyNav,
} from "@/lib/companies";
import { knownReaders, openSettings, pickFolder, rememberReader, useDesktopFeatures } from "@/lib/desktop";
import { type ListedSession, listGrouped } from "@/lib/managed-agents/api";
import { forgetProject, hintTitle, renameProjectLocally, useProjects } from "@/lib/managed-agents/projects";
import { onProjectChange } from "@/lib/managed-agents/realtime";
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

// ---------------------------------------------------------------- 页面
export const CompanyPage: FC = () => {
  const company = useCompanyNav((s) => s.company);
  const { setView } = useView();
  const withPeriod = useProjects((s) => s.period);
  const [period, setPeriod] = useState(() => (withPeriod ? lastMonth() : ""));
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const asked = useRef(0);

  const reload = useCallback(async () => {
    if (!company) return;
    const mine = ++asked.current;
    try {
      const next = await loadDetail(company, period);
      if (mine !== asked.current) return;
      hintTitle(next.company.id, next.company.title);
      setDetail(next);
      setError("");
      dropCachedDetail(company);
    } catch (e) {
      if (mine === asked.current) setError(shownError(e));
    }
  }, [company, period]);

  useEffect(() => {
    setDetail(null);
    setError("");
  }, [company]);
  useEffect(() => void reload(), [reload]);
  useEffect(
    () =>
      onProjectChange((change) => {
        if (change.op === "upsert" && change.project.id === company) void reload();
        if (change.op === "delete" && change.id === company) setView("companies");
      }),
    [company, reload, setView],
  );

  if (!company) return null;
  if (!detail) {
    return (
      <div className="flex flex-1 items-center justify-center text-ink-3">
        {error ? <p role="alert" className="text-danger text-sm">{error}</p> : <LoaderIcon className="size-4 animate-spin" />}
      </div>
    );
  }
  return <Master detail={detail} period={period} onPeriod={setPeriod} reload={reload} error={error} />;
};

const Master: FC<{ detail: Detail; period: string; onPeriod: (p: string) => void; reload: () => Promise<void>; error: string }> = ({
  detail,
  period,
  onPeriod,
  reload,
  error,
}) => {
  const { isMobile } = useSidebar();
  const id = detail.company.id;
  const branches = branchesOf(detail);
  const multi = branches.length > 1;
  const side = (
    <>
      <PendingSection company={id} />
      <NotesSection company={id} notes={detail.notes} reload={reload} />
      <FolderSection company={id} folders={detail.folders} reload={reload} />
      <SetsSection sets={detail.account_sets} branches={multi ? branches : []} period={period} />
      {detail.tasks && <TasksSection company={id} detail={detail} period={period} />}
      {detail.drafts && <DraftsSection company={id} drafts={detail.drafts} branches={multi ? branches : []} combined={!!detail.company.combined_vat} />}
      <ChoicesSection company={id} choices={detail.choices} period={period} reload={reload} />
    </>
  );
  return (
    <div data-slot="cc-scroll" className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="cc-company-page mx-auto w-full px-4 pt-4 pb-10 md:pt-8" data-slot="cc-company-page">
        <Header detail={detail} />
        {error && <div role="alert" className="mb-4 rounded-md bg-danger-soft px-3 py-2 text-danger text-sm">{error}</div>}
        <div className="cc-company-cols">
          <div className="flex min-w-0 flex-1 flex-col gap-6">
            <CompanyComposer company={id} period={period} onPeriod={onPeriod} branches={branches} />
            <Section title={co.conversations} open>
              <Conversations company={id} />
            </Section>
            {isMobile && side}
          </div>
          {!isMobile && <aside className="cc-company-side flex flex-col gap-6">{side}</aside>}
        </div>
      </div>
    </div>
  );
};

/** 一节：电脑上一直展开；手机上点标题折叠（open = 默认展开）。 */
const Section: FC<{ title: string; open?: boolean; action?: ReactNode; children: ReactNode; slot?: string }> = ({
  title,
  open = false,
  action,
  children,
  slot,
}) => {
  const { isMobile } = useSidebar();
  const [shown, setShown] = useState(open);
  const expanded = !isMobile || shown;
  return (
    <section className="flex flex-col gap-2" data-slot={slot}>
      <div className="flex items-center gap-2">
        {isMobile ? (
          <button type="button" className="cc-section-toggle cc-company-heading" aria-expanded={expanded} onClick={() => setShown((v) => !v)}>
            {title}
            <ChevronDownIcon className="cc-section-chevron" />
          </button>
        ) : (
          <h2 className="cc-company-heading">{title}</h2>
        )}
        <span className="flex-1" />
        {expanded && action}
      </div>
      {expanded && children}
    </section>
  );
};

// ---------------------------------------------------------------- 顶部
const Header: FC<{ detail: Detail }> = ({ detail }) => {
  const { isMobile } = useSidebar();
  const { setView } = useView();
  const c = detail.company;
  // 改名成功就显示新名字（服务端回的那份），不等整页重取；重取到了以服务端为准
  const [title, setTitle] = useState(c.title);
  useEffect(() => setTitle(c.title), [c.title]);
  const [renaming, setRenaming] = useState(false);
  const [value, setValue] = useState(c.title);
  const [error, setError] = useState("");
  const save = async (input: string) => {
    const next = input.trim();
    setRenaming(false);
    if (!next || next === title) return;
    setError("");
    try {
      const out = await renameCompany(c.id, next);
      const saved = (out as { title?: string } | null)?.title || next;
      setTitle(saved);
      renameProjectLocally(c.id, saved);
      dropCachedDetail(c.id);
    } catch (e) {
      setError(shownError(e));
    }
  };
  const rename = () => {
    if (!isMobile) {
      setValue(title);
      return setRenaming(true);
    }
    const next = window.prompt(t("rename"), title);
    if (next) void save(next);
  };
  const remove = async () => {
    try {
      await deleteCompany(c.id);
      forgetProject(c.id);
      forgetAnyCompany();
      dropCachedDetail(c.id);
      setView("companies");
    } catch (e) {
      setError(shownError(e));
    }
  };
  return (
    <div className="mb-6 flex items-start gap-3">
      <div className="min-w-0 flex-1">
        {renaming ? (
          <input
            autoFocus
            value={value}
            aria-label={t("rename")}
            onChange={(e) => setValue(e.target.value)}
            onFocus={(e) => e.target.select()}
            onBlur={() => void save(value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void save(value);
              if (e.key === "Escape") setRenaming(false);
            }}
            className="cc-input w-full text-lg"
          />
        ) : (
          <h1 className="cc-greeting truncate text-3xl">{title}</h1>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-2 text-ink-3 text-sm">
          <span className="truncate">{[c.legal_name && c.legal_name !== title ? c.legal_name : "", companyLine(c)].filter(Boolean).join(" · ")}</span>
          {!!c.combined_vat && <span className="cc-chip">{co.combinedVat}</span>}
        </div>
        {error && <p role="alert" className="mt-1 text-danger text-sm">{error}</p>}
      </div>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger className="cc-icon-btn" aria-label={t("more")}>
          <MoreHorizontalIcon />
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="end" sideOffset={4} className="cc-menu !min-w-44">
            <DropdownMenu.Item className="cc-menu-item" onSelect={rename}>
              <PencilIcon />
              {t("rename")}
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="cc-menu-sep" />
            <DropdownMenu.Item className="cc-menu-item" data-danger="" onSelect={() => void remove()}>
              <TrashIcon />
              {co.deleteCompany}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
};

// ---------------------------------------------------------------- 输入框
const CompanyComposer: FC<{ company: string; period: string; onPeriod: (p: string) => void; branches: Branch[] }> = ({
  company,
  period,
  onPeriod,
  branches,
}) => {
  const start = useStartIn();
  const withPeriod = useProjects((s) => s.period);
  const withScope = useProjects((s) => s.scope);
  const [scope, setScope] = useState("");
  const [text, setText] = useState("");
  const send = () => {
    const value = text.trim();
    if (!value) return;
    setText("");
    start({ project: company, period: withPeriod ? period : "", scope: withScope ? scope : "" }, value, true);
  };
  return (
    <div className="cc-composer" data-slot="cc-company-composer">
      <textarea
        rows={2}
        value={text}
        aria-label={t("placeholder")}
        placeholder={t("placeholder")}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
        className="block max-h-72 min-h-6 w-full resize-none bg-transparent px-1 font-sans text-base outline-none"
      />
      <div className="flex items-center gap-1.5">
        {withPeriod && <PeriodChip value={period} onChange={onPeriod} />}
        {withScope && <BranchChip branches={branches} value={scope} onChange={setScope} />}
        <span className="flex-1" />
        <button type="button" className="cc-send" aria-label={t("send")} title={t("send")} disabled={!text.trim()} onClick={send}>
          <ArrowUpIcon />
        </button>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------- 对话
/** 我在这家的对话：按这家从服务端取（分组列表 group=<公司>，同侧栏「显示更多」的翻页），按活动倒序，带月份短写；点了切过去。
 *  这家的对话有进出（新建、移入移出、侧栏挂上的条数变了）或公司有变化时重取第一页。 */
export const Conversations: FC<{ company: string }> = ({ company }) => {
  const aui = useAui();
  const { setView } = useView();
  const [rows, setRows] = useState<ListedSession[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const asked = useRef(0);
  // 侧栏知道的、挂在这家的对话条数（新建 / 移入移出时会变）：变了就重取
  const filed = useProjects((s) => Object.values(s.filings).filter((f) => f.project === company).length);
  const load = useCallback(
    async (after = "") => {
      const mine = ++asked.current;
      setBusy(true);
      try {
        const page = await listGrouped({ group: company, after });
        if (mine !== asked.current) return;
        setRows((before) => {
          const kept = after ? (before ?? []) : [];
          const seen = new Set(kept.map((row) => row.id));
          return [...kept, ...page.sessions.filter((row) => !seen.has(row.id) && !row.archived_at)];
        });
        setNext(page.nextCursor);
      } catch {
        if (mine === asked.current) setRows((before) => before ?? []);
      } finally {
        if (mine === asked.current) setBusy(false);
      }
    },
    [company],
  );
  useEffect(() => void load(), [load, filed]);
  useEffect(() => onProjectChange((change) => (change.op === "move" ? void load() : undefined)), [load]);
  if (!rows?.length) return null;
  return (
    <div className="flex flex-col" data-slot="cc-company-chats">
      {rows.map((row) => (
        <button
          key={row.id}
          type="button"
          className="cc-list-row"
          onClick={() => {
            setView("chat");
            void aui.threads().switchToThread(row.id);
          }}
        >
          <span className="min-w-0 flex-1 truncate text-ink">{row.title || t("untitled")}</span>
          {row.period && <span className="shrink-0 text-ink-3 text-xs">{monthShort(row.period)}</span>}
        </button>
      ))}
      {next && (
        <button type="button" className="cc-show-more" disabled={busy} onClick={() => void load(next)}>
          {co.showMore}
        </button>
      )}
    </div>
  );
};

// ---------------------------------------------------------------- 待处理
const PendingSection: FC<{ company: string }> = ({ company }) => (
  <div data-slot="cc-company-pending">
    <PendingList company={company} embedded />
  </div>
);

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

const NotesSection: FC<{ company: string; notes: Note[]; reload: () => Promise<void> }> = ({ company, notes, reload }) => {
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
  return (
    <Section
      title={co.notes}
      open
      slot="cc-company-notes"
      action={
        <button type="button" className="cc-group-btn" aria-label={co.add} title={co.add} onClick={() => setAdding(true)}>
          <PlusIcon />
        </button>
      }
    >
      <div className="flex flex-col gap-1">
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
                className="cc-group-btn opacity-0 group-hover/note:opacity-100 focus-visible:opacity-100 max-md:opacity-100"
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
    </Section>
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
    <div className="flex flex-col gap-2" data-slot="cc-note-editor">
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
export const FolderSection: FC<{ company: string; folders: CompanyFolder[]; reload: () => Promise<void> }> = ({ company, folders: all, reload }) => {
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
  return (
    <Section title={co.docFolder} slot="cc-company-folders">
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
    </Section>
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
    <Section title={co.accountSets} slot="cc-company-sets">
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
    </Section>
  );
};

// ---------------------------------------------------------------- 定时任务
const TasksSection: FC<{ company: string; detail: Detail; period: string }> = ({ company, detail, period }) => {
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
  return (
    <Section
      title={t("scheduledTasks")}
      slot="cc-company-tasks"
      action={
        <button type="button" className="cc-group-btn" aria-label={co.add} title={co.add} onClick={add}>
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
    </Section>
  );
};

// ---------------------------------------------------------------- 申报草稿
const branchOfDraft = (d: Draft) => d.branch ?? d.company_branch ?? "";

const DraftsSection: FC<{ company: string; drafts: Draft[]; branches: Branch[]; combined: boolean }> = ({ company, drafts, branches, combined }) => {
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
    <Section title={co.taxDrafts} slot="cc-company-drafts">
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
    </Section>
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

const ChoicesSection: FC<{ company: string; choices: Choice[]; period: string; reload: () => Promise<void> }> = ({ company, choices, period, reload }) => {
  // 页面上显示的：改 / 删成功时先按刚做的改这里（下拉框不弹回旧值），整页重取到了以服务端为准
  const [list, setList] = useState(choices);
  useEffect(() => setList(choices), [choices]);
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
    <Section title={co.remembered} slot="cc-company-choices">
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
    </Section>
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
        <span className="block truncate text-ink text-sm">
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
      <button type="button" className="cc-group-btn" aria-label={t("delete")} title={t("delete")} onClick={onForget}>
        <XIcon />
      </button>
    </div>
  );
};

