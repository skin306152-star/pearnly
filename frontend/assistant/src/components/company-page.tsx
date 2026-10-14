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
import { forgetProject, renameProjectLocally, useProjects } from "@/lib/managed-agents/projects";
import { onProjectChange } from "@/lib/managed-agents/realtime";
import { useView } from "@/lib/nav";

const NOTE_MAX = 500; // 同服务端：每条 ≤500 字

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

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
      setDetail(next);
      setError("");
      dropCachedDetail(company);
    } catch (e) {
      if (mine === asked.current) setError(errorText(e));
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
  const [renaming, setRenaming] = useState(false);
  const [value, setValue] = useState(c.title);
  const [error, setError] = useState("");
  const save = async (title: string) => {
    const next = title.trim();
    setRenaming(false);
    if (!next || next === c.title) return;
    try {
      await renameCompany(c.id, next);
      renameProjectLocally(c.id, next);
      dropCachedDetail(c.id);
    } catch (e) {
      setError(errorText(e));
    }
  };
  const rename = () => {
    if (!isMobile) {
      setValue(c.title);
      return setRenaming(true);
    }
    const next = window.prompt(t("rename"), c.title);
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
      setError(errorText(e));
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
          <h1 className="cc-greeting truncate text-3xl">{c.title}</h1>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-2 text-ink-3 text-sm">
          <span className="truncate">{[c.legal_name && c.legal_name !== c.title ? c.legal_name : "", companyLine(c)].filter(Boolean).join(" · ")}</span>
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
const NotesSection: FC<{ company: string; notes: Note[]; reload: () => Promise<void> }> = ({ company, notes, reload }) => {
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState("");
  const [error, setError] = useState("");
  const run = async (work: () => Promise<unknown>) => {
    setError("");
    try {
      await work();
      await reload();
    } catch (e) {
      setError(errorText(e));
    }
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
        {notes.map((note) =>
          editing === note.id ? (
            <NoteInput
              key={note.id}
              initial={note.text}
              onDone={(text) => {
                setEditing("");
                if (text && text !== note.text) void run(() => saveNote(company, text, note.id));
              }}
            />
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
                onClick={() => void run(() => deleteNote(company, note.id))}
              >
                <XIcon />
              </button>
            </div>
          ),
        )}
        {adding && (
          <NoteInput
            initial=""
            onDone={(text) => {
              setAdding(false);
              if (text) void run(() => saveNote(company, text));
            }}
          />
        )}
        {error && <p role="alert" className="text-danger text-sm">{error}</p>}
      </div>
    </Section>
  );
};

/** 就地编辑一条：回车或离开保存，Esc 放弃；Shift+回车换行。 */
const NoteInput: FC<{ initial: string; onDone: (text: string) => void }> = ({ initial, onDone }) => {
  const [value, setValue] = useState(initial);
  const finished = useRef(false);
  const done = (text: string) => {
    if (finished.current) return;
    finished.current = true;
    onDone(text.replace(/\s+/g, " ").trim());
  };
  return (
    <textarea
      autoFocus
      rows={2}
      maxLength={NOTE_MAX}
      value={value}
      aria-label={co.notes}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => done(value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
          e.preventDefault();
          done(value);
        }
        if (e.key === "Escape") done(initial);
      }}
      className="cc-input h-auto w-full py-1.5"
    />
  );
};

// ---------------------------------------------------------------- 单据文件夹
export const FolderSection: FC<{ company: string; folders: CompanyFolder[]; reload: () => Promise<void> }> = ({ company, folders, reload }) => {
  const { app, companyFolders } = useDesktopFeatures();
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
      await reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async (reader: string) => {
    setError("");
    try {
      await unbindFolder(company, reader);
      await reload();
    } catch (e) {
      setError(errorText(e));
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

const ChoicesSection: FC<{ company: string; choices: Choice[]; period: string; reload: () => Promise<void> }> = ({ company, choices, period, reload }) => {
  const [error, setError] = useState("");
  const groups = useMemo(() => {
    const map = new Map<string, Choice[]>();
    for (const c of choices) map.set(choiceSection(c), [...(map.get(choiceSection(c)) ?? []), c]);
    return [...map.entries()].sort((a, b) => SECTION_ORDER.indexOf(a[0]) - SECTION_ORDER.indexOf(b[0]));
  }, [choices]);
  if (!choices.length) return null;
  const run = async (work: () => Promise<unknown>) => {
    setError("");
    try {
      await work();
      await reload();
    } catch (e) {
      setError(errorText(e));
    }
  };
  return (
    <Section title={co.remembered} slot="cc-company-choices">
      {groups.map(([section, rows]) => (
        <div key={section} className="flex flex-col">
          <div className="cc-subheading">{co.kind[section] ?? section}</div>
          {rows.map((c) => (
            <ChoiceRow key={c.key} choice={c} onSet={(v) => void run(() => setChoice(company, c.key, v, period))} onForget={() => void run(() => forgetChoice(company, c.key))} />
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

