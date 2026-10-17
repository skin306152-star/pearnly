// 公司资料的卡片（ADR-020，只在 Cowork）：右侧会话栏（components/session-panel.tsx）用的一套，对话里与公司页里都是它
// （公司页的资料全在会话栏，开发窗口 10-16 定）。
//
//   卡片外框 PanelCard（设计文档 3.13）：透明底 + 1px 内环、圆角 10；卡头 26：标题 + 箭头是折叠开关（整行都能点），右侧一列
//            12px 淡色摘要（「2 条」「1 个任务」「需处理 2」，一直显示），卡头操作（＋）悬停 / 聚焦时盖在摘要的位置上；最右边留着
//            6 点把手的宽度，把手悬停 / 聚焦才出，摘要一列右对齐、不因把手或操作出现而挪动。在会话栏里（PanelControlContext）
//            折叠、顺序记在本机，拖动（共用拖动 components/ui/drag.tsx）或 Alt+↑/↓ 调顺序；不在会话栏里时各自记折叠、默认展开、没有把手。
//   各卡     待处理（这家的，当场选沿用 F18 规则）→ 知识库（新增 / 修改有「取消」「保存」，F11e）→ 单据文件夹（只有 CC 桌面版
//            窗口能选，桥 pick_folder 的结果原样交给 folder_bind）→ 账套（多分店按分店分组，覆盖本月的高亮）→ 定时任务
//            （点一行跳任务页，「＋」带预填）→ 申报草稿（按分店分组）→ 记住的选择（按类别；付款方式、简易税票抵不抵能直接改，
//            其余只能删；Express 历史学到的只读可删）。没内容的卡在会话栏里不显示（keepEmpty 时照样显示，公司页用）。
//   公司规则（数字会计员第 2 期）：「记住的选择」里公司规则的行写明「全公司 / 分店 X」，付款 / 抵进项可用下拉直接改；账套行与
//            历史行多一个「设为全公司」。公司页知识库是空的时给「让 CC 起草」（新对话里预填、不自动发送）。
//
// 文案走 i18n.ts 的 co；样式只用 globals.css 的 .cc-* 与变量。

import { ChevronRightIcon, FolderIcon, GripVerticalIcon, LoaderIcon, PlusIcon, SparklesIcon, XIcon } from "lucide-react";
import {
  createContext,
  type FC,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useState,
} from "react";
import { useStartIn } from "@/components/assistant-ui/projects";
import { otherMonths, PendingItems, PendingList, usePendingItems } from "@/components/pending";
import { branchName } from "@/components/period-chip";
import { co, t } from "@/i18n";
import {
  type AccountSet,
  bindFolder,
  branchShort,
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
  promoteChoice,
  saveNote,
  setChoice,
  setForPeriod,
  taskSeedOf,
  folderIsMine,
  unbindFolder,
  useCompanyNav,
  usePendingCounts,
} from "@/lib/companies";
import { usePageMonth } from "@/lib/company-panel";
import { knownReaders, openSettings, pickFolder, rememberReader, useDesktopFeatures } from "@/lib/desktop";
import { useProjects } from "@/lib/managed-agents/projects";
import { useView } from "@/lib/nav";
import { shownError } from "@/lib/frappe";
import { touchOnly } from "@/lib/touch";
import { altMove, useSortableItem } from "@/components/ui/drag";
import { Marquee } from "@/components/ui/marquee";

const NOTE_MAX = 500; // 同服务端：每条 ≤500 字

/** 覆盖这个月的账套（没有月份或都不覆盖时取最新可写的；lib/companies 的 setForPeriod）。 */
export const setFor: (sets: readonly AccountSet[], period: string) => AccountSet | undefined = setForPeriod;

/** 文件夹状态的字。 */
export function folderState(folder: Pick<CompanyFolder, "state" | "reader_gone">): string {
  // 读它的电脑已被移除（ADR-021 第 6 批）：别的状态都无从说起
  if (folder.reader_gone) return co.folderGone;
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

/** 单据文件夹卡头的一句：一个文件夹写它的状态，几个写个数，没有不写。 */
export function folderSummary(folders: ReadonlyArray<Pick<CompanyFolder, "state" | "reader_gone">>): string {
  if (folders.length === 1) return folderState(folders[0]!);
  return folders.length ? co.card.folders(folders.length) : "";
}

/** 记住的选择分小节：Express、公司规则与按公司记的口径（入账方式、现用账套）按类别，其余按来源（vat 预设叫「增值税核对」，
 *  免得与简易税票那一类混；Express 历史定下的入账方式单列「入账方式历史」）。 */
export function choiceSection(choice: Pick<Choice, "source" | "kind">): string {
  if (choice.source === "express" || choice.source === "rule" || choice.source === "policy") return choice.kind;
  if (choice.source === "vat") return "vatCheck";
  if (choice.source === "history" && choice.kind === "posting") return "posting_history";
  return choice.source;
}

const SECTION_ORDER = ["party", "account", "payment", "vat", "posting", "item", "department", "receipt", "current_book", "pos", "income",
  "vatCheck", "history", "posting_history"];

// ---------------------------------------------------------------- 卡片外框
/** 会话栏给卡片的控制：折叠（记在本机）、调顺序（Alt+↑/↓；拖动由会话栏的共用拖动负责）、登记看得见的卡片
 *  （排序列表只在登记了的卡片之间排，空卡不显示也不占位；标题给读屏播报用）。不在会话栏里时卡片各自记折叠、没有把手。 */
export type PanelControl = {
  collapsed: readonly string[];
  toggle: (id: string) => void;
  move: (id: string, delta: number) => void;
  register: (id: string, title: string) => () => void;
};
export const PanelControlContext = createContext<PanelControl | null>(null);

type CardProps = {
  id: string;
  title: string;
  /** 卡头右侧淡色的一句状态（「2 条」「需处理 2」）；空、0 不显示。 */
  summary?: ReactNode;
  /** 卡头操作（＋）：悬停、键盘聚焦卡头时才出，盖在摘要的位置上（摘要那一列不挪动）。 */
  action?: ReactNode;
  /** 一直显示的卡头控件（公司页「本月」的月份胶囊），在标题后面。 */
  extra?: ReactNode;
  slot?: string;
  forceOpen?: boolean;
  children: ReactNode;
};

/** 一张卡：卡头标题与箭头是折叠开关（整行都能点）；右侧摘要一列右对齐，操作悬停时盖在它上面；会话栏里最右边留出把手的
 *  宽度（6 点把手悬停、聚焦时才出，摘要不因它出现而挪动）。forceOpen = 正在里面新增 / 编辑，折叠了也展开。 */
export const PanelCard: FC<CardProps> = (props) => {
  const control = useContext(PanelControlContext);
  return control ? <SortableCard {...props} control={control} /> : <CardFrame {...props} control={null} sortable={null} />;
};

/** 会话栏里的卡：登记自己、接上共用拖动的排序列表（components/session-panel.tsx 的 DragArea / DragList）。 */
const SortableCard: FC<CardProps & { control: PanelControl }> = ({ control, ...props }) => {
  const { register } = control;
  useLayoutEffect(() => register(props.id, props.title), [register, props.id, props.title]);
  const sortable = useSortableItem({ id: props.id, kind: "card", label: props.title, list: "cards" });
  return <CardFrame {...props} control={control} sortable={sortable} />;
};

const CardFrame: FC<CardProps & { control: PanelControl | null; sortable: ReturnType<typeof useSortableItem> | null }> = ({
  id,
  title,
  summary,
  action,
  extra,
  slot,
  forceOpen = false,
  children,
  control,
  sortable,
}) => {
  const [folded, setFolded] = useState(false);
  const summaryId = useId();
  const collapsed = !forceOpen && (control ? control.collapsed.includes(id) : folded);
  const toggle = () => (control ? control.toggle(id) : setFolded((v) => !v));
  // Alt+↑/↓ 挪一格（挪动时 React 可能把这个节点摘下再插回，altMove 挪完把焦点放回原来的按钮）
  const keys = (e: ReactKeyboardEvent) => control !== null && altMove(e, (delta) => control.move(id, delta));
  const shownSummary = summary !== undefined && summary !== null && summary !== "" && summary !== 0;
  return (
    <section {...sortable?.node} className="cc-panel-card" data-card={id} data-slot={slot}>
      <div className="cc-panel-card-head">
        <button
          type="button"
          className="cc-panel-card-toggle"
          aria-expanded={!collapsed}
          aria-describedby={shownSummary ? summaryId : undefined}
          onClick={toggle}
          onKeyDown={keys}
        >
          <Marquee className="truncate">{title}</Marquee>
          <ChevronRightIcon className="cc-panel-card-chevron" aria-hidden />
        </button>
        {extra && <span className="cc-panel-card-extra">{extra}</span>}
        <span className="cc-panel-card-end" data-slot="cc-card-end">
          {shownSummary && (
            <span id={summaryId} className="cc-panel-card-summary">
              {summary}
            </span>
          )}
          {action && <span className="cc-panel-card-tools">{action}</span>}
        </span>
        {sortable && (
          <span className="cc-panel-card-grip" data-slot="cc-card-grip">
            <button
              type="button"
              {...sortable.handle}
              className="cc-panel-btn cc-panel-grip"
              aria-label={t("reorder")}
              title={t("reorder")}
              onKeyDown={(e) => {
                if (!keys(e)) sortable.handle.onKeyDown?.(e);
              }}
            >
              <GripVerticalIcon aria-hidden />
            </button>
          </span>
        )}
      </div>
      {!collapsed && <div className="cc-panel-card-body">{children}</div>}
    </section>
  );
};

// ---------------------------------------------------------------- 待处理
/** 这家的待处理（当场选、交给 CC 都在 PendingList 里，F18 规则不变）；计数为 0 不显示。
 *  会话栏给 period：只列对话月份以外的（开发窗口 10-16 定，对话那个月的只在「本月」卡），一张不剩时同别的空卡一样不显示；
 *  公司页不给，照旧全列。 */
export const PendingCard: FC<{ company: string; period?: string }> = ({ company, period }) => {
  const count = usePendingCounts((s) => s.companies[company] ?? 0);
  if (!count) return null;
  if (period === undefined) {
    return (
      <PanelCard id="pending" title={co.pending} summary={co.card.docs(count)} slot="cc-company-pending">
        <PendingList company={company} embedded />
      </PanelCard>
    );
  }
  return <OtherMonthsPending company={company} period={period} />;
};

const OtherMonthsPending: FC<{ company: string; period: string }> = ({ company, period }) => {
  const state = usePendingItems(company);
  const items = useMemo(() => (state.items ? otherMonths(state.items, period) : null), [state.items, period]);
  if (!items?.length && !state.error) return null;
  return (
    <PanelCard id="pending" title={co.pending} summary={items?.length ? co.card.docs(items.length) : undefined} slot="cc-company-pending">
      <PendingItems {...state} items={items} company={company} embedded />
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
  const start = useStartIn();
  // 公司页此刻的月份（输入框、「月结」卡的月份胶囊同一个；服务端不收月份的产品是空）
  const pageMonth = usePageMonth();
  /** 让 CC 起草（只在公司页的空知识库里）：在这家开新对话（月份 = 公司页的月份），预填一句、不自动发送。 */
  const draft = () => start({ project: company, period: pageMonth, scope: "" }, co.draftNotesText, false);
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
      summary={list.length ? co.card.notes(list.length) : undefined}
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
        {keepEmpty && !list.length && !adding && (
          <button type="button" className="cc-btn self-start" data-slot="cc-notes-draft" onClick={draft}>
            <SparklesIcon className="size-4" />
            {co.draftNotes}
          </button>
        )}
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
  const { app, companyFolders, deviceName } = useDesktopFeatures();
  // 刚移除的（服务端已删）先不显示，不等整页重取；重取到了以服务端为准。按读者编号（「电脑|连接」）认行：
  // 同一条连接上几台电脑各绑一行（ADR-021 第 6 批），只按连接会把别人那行也藏掉、解绑时服务端不猜是哪台
  const [gone, setGone] = useState<string[]>([]);
  useEffect(() => setGone([]), [all]);
  const idOf = (f: CompanyFolder) => f.reader_id || f.reader;
  const folders = all.filter((f) => !gone.includes(idOf(f)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const readers = knownReaders();
  const mine = (f: CompanyFolder) => !f.reader_gone && folderIsMine(f, readers, deviceName);
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
      setGone([]);
      await reload();
    } catch (e) {
      setError(shownError(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async (f: CompanyFolder) => {
    setError("");
    try {
      await unbindFolder(company, idOf(f));
      setGone((before) => [...before, idOf(f)]);
      void reload();
    } catch (e) {
      setError(shownError(e));
    }
  };
  if (hideEmpty && !app && !folders.length && !error) return null;
  return (
    <PanelCard id="folders" title={co.docFolder} summary={folderSummary(folders)} slot="cc-company-folders">
      <div className="flex flex-col gap-1">
        {folders.map((f) => (
          <div key={`${idOf(f)}-${f.device}`} className="cc-folder-line" data-state={f.reader_gone ? "missing" : f.state}>
            <FolderIcon />
            <span className="min-w-0 flex-1">
              <Marquee className="block truncate" title={f.label}>{[f.device, f.label].filter(Boolean).join(" · ")}</Marquee>
              <Marquee className="block truncate text-ink-3 text-xs">
                {[folderState(f), f.month_folder ? co.monthFolder(f.month_folder) : ""].filter(Boolean).join(" · ")}
              </Marquee>
            </span>
            {app && companyFolders && mine(f) && (
              <>
                <button type="button" className="cc-link-btn" disabled={busy} onClick={() => void choose()}>
                  {co.changeFolder}
                </button>
                <button type="button" className="cc-link-btn" disabled={busy} onClick={() => void remove(f)}>
                  {co.removeFolder}
                </button>
              </>
            )}
            {/* 读它的电脑已被移除：那台电脑不会再来，哪台电脑、浏览器里都能把这一行移除 */}
            {f.reader_gone && (
              <button type="button" className="cc-link-btn" disabled={busy} onClick={() => void remove(f)}>
                {co.removeFolder}
              </button>
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
    <Marquee className="min-w-0 flex-1 truncate">{set.label}</Marquee>
    <span className="shrink-0 text-ink-3 text-xs">{set.year_label}</span>
  </div>
);

/** 账套清单（多分店按分店分组，覆盖这个月的高亮）：账套卡与公司页会话栏的「公司」卡共用。 */
export const SetsList: FC<{ sets: AccountSet[]; branches: Branch[]; period: string }> = ({ sets, branches, period }) => {
  const covering = setFor(sets, period);
  if (!sets.length) return null;
  return branches.length ? (
    <>
      {branches.map((b) => (
        <div key={b.branch} className="flex flex-col">
          <div className="cc-subheading">{branchName(b)}</div>
          {b.account_sets.map((set) => (
            <SetRow key={`${set.connection}-${set.key}`} set={set} covering={setFor(b.account_sets, period) === set} />
          ))}
        </div>
      ))}
    </>
  ) : (
    <div className="flex flex-col">
      {sets.map((set) => <SetRow key={`${set.connection}-${set.key}`} set={set} covering={covering === set} />)}
    </div>
  );
};

export const SetsSection: FC<{ sets: AccountSet[]; branches: Branch[]; period: string }> = ({ sets, branches, period }) => {
  if (!sets.length) return null;
  return (
    <PanelCard id="sets" title={co.accountSets} summary={co.card.sets(sets.length)} slot="cc-company-sets">
      <SetsList sets={sets} branches={branches} period={period} />
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
  const { app, companyFolders, deviceName } = useDesktopFeatures();
  const tasks = detail.tasks ?? [];
  const open = (name: string) => {
    useCompanyNav.setState({ taskCompany: company, taskFocus: name, taskSeed: null });
    setView("tasks");
  };
  const add = () => {
    useCompanyNav.setState({
      taskCompany: company,
      taskFocus: "",
      taskSeed: taskSeedOf(detail, period, knownReaders(), app && companyFolders, deviceName),
    });
    setView("tasks");
  };
  if (!detail.tasks || (!tasks.length && !keepEmpty)) return null;
  return (
    <PanelCard
      id="tasks"
      title={t("scheduledTasks")}
      summary={tasks.length ? co.card.tasks(tasks.length) : undefined}
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
              <Marquee className="block truncate text-ink">{task.title}</Marquee>
              <Marquee className="block truncate text-ink-3 text-xs">
                {[task.follows ? co.followCompany : task.account_label, task.folder_label].filter(Boolean).join(" · ")}
              </Marquee>
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
      <Marquee className="min-w-0 flex-1 truncate text-ink">{[d.period, d.kind].filter(Boolean).join(" · ")}</Marquee>
      <span className="shrink-0 text-ink-3 text-xs">{formatWhen(d.updated_at)}</span>
    </button>
  );
  const grouped = branches.length > 1 && !combined;
  return (
    <PanelCard id="drafts" title={co.taxDrafts} summary={co.card.drafts(drafts.length)} slot="cc-company-drafts">
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
  posting: [
    { value: "stock", label: () => co.postStock },
    { value: "service", label: () => co.postService },
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
    <PanelCard id="choices" title={co.remembered} summary={co.card.choices(list.length)} slot="cc-company-choices">
      {groups.map(([section, rows]) => (
        <div key={section} className="flex flex-col">
          <div className="cc-subheading">{co.kind[section] ?? section}</div>
          {rows.map((c) => (
            <ChoiceRow
              key={c.key}
              choice={c}
              onSet={(v) => void run(() => setChoice(company, c.key, v, period), (rows) => setChoiceShown(rows, c.key, v))}
              onForget={() => void run(() => forgetChoice(company, c.key), (rows) => rows.filter((row) => row.key !== c.key))}
              onPromote={() => void run(() => promoteChoice(company, c.key, period), (rows) => rows.filter((row) => row.key !== c.key))}
            />
          ))}
        </div>
      ))}
      {error && <p role="alert" className="text-danger text-sm">{error}</p>}
    </PanelCard>
  );
};

/** 公司规则的范围：全公司 / 分店 X（总公司照分店胶囊写「总公司」）。 */
export const ruleScope = (choice: Pick<Choice, "branch">) => (choice.branch ? (choice.branch === "00000" ? branchShort(choice.branch) : co.branchN(choice.branch)) : co.wholeCompany);

/** onPromote：账套行、历史行的「设为全公司」（不给就不显示）。 */
export const ChoiceRow: FC<{ choice: Choice; onSet: (value: string) => void; onForget: () => void; onPromote?: () => void }> = ({
  choice,
  onSet,
  onForget,
  onPromote,
}) => {
  const history = choice.source === "history";
  const rule = choice.source === "rule";
  const options = (choice.source === "express" || rule || choice.source === "policy") && choice.editable !== false ? EDITABLE[choice.kind] : undefined;
  const subject = choice.subject_label || choice.supplier_name || "";
  const value = choice.value_label || choice.account_label || choice.value;
  const meta = history
    ? [co.fromHistory(Number(choice.count) || 0), formatWhen(choice.last_date ?? choice.set_at)]
    : rule
      ? [ruleScope(choice), choice.set_by ?? "", formatWhen(choice.set_at)]
      : [choice.year_label ?? "", choice.set_by ?? "", formatWhen(choice.set_at)];
  return (
    <div className="cc-choice-row" data-slot="cc-choice-row" data-source={choice.source}>
      <span className="min-w-0 flex-1">
        <Marquee className="block truncate text-ink">
          {subject}
          {!options && <span className="text-ink-3">{` → ${value}`}</span>}
        </Marquee>
        <Marquee className="block truncate text-ink-3 text-xs">{meta.filter(Boolean).join(" · ")}</Marquee>
      </span>
      {onPromote && choice.promotable && (
        <button type="button" className="cc-link-btn shrink-0" data-slot="cc-choice-promote" onClick={onPromote}>
          {co.makeCompanyWide}
        </button>
      )}
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
