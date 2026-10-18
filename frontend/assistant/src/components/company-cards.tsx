// 公司资料的卡片（ADR-020，只在 Cowork）：右侧会话栏（components/session-panel.tsx）用的一套，对话里与公司页里都是它
// （公司页的资料全在会话栏，开发窗口 10-16 定）。
//
//   卡片外框 PanelCard（设计文档 3.13）：透明底 + 1px 内环、圆角 10；卡头 26：标题 + 箭头是折叠开关（整行都能点），右侧一列
//            12px 淡色摘要（「2 条」「1 个任务」「需处理 2」，一直显示），卡头操作（＋）悬停 / 聚焦时盖在摘要的位置上；最右边留着
//            6 点把手的宽度，把手悬停 / 聚焦才出，摘要一列右对齐、不因把手或操作出现而挪动。在会话栏里（PanelControlContext）
//            折叠、顺序记在本机，拖动（共用拖动 components/ui/drag.tsx）或 Alt+↑/↓ 调顺序；不在会话栏里时各自记折叠、默认展开、没有把手。
//   各卡     待处理（这家的，当场选沿用 F18 规则）→ 知识库（新增 / 修改有「取消」「保存」，F11e）→ 单据文件夹（只有 CC 桌面版
//            窗口能选，桥 pick_folder 的结果原样交给 folder_bind）→ 账套（多分店按分店分组，覆盖本月的高亮）→ 定时任务
//            （点一行跳任务页，「＋」带预填）→ 申报草稿（按分店分组）→ 记住的选择（按类别，一条一行、点开才有按钮；付款方式、
//            简易税票抵不抵能直接改，其余只能删；Express 历史学到的只读可删）。没内容的卡在会话栏里不显示（keepEmpty 时照样显示，公司页用）。
//   公司规则（数字会计员第 2 期）：「记住的选择」里公司规则的行展开后写明「全公司 / 分店 X」，付款 / 抵进项用两段式直接改；
//            账套行与历史行展开后多一个「设为全公司」。公司页知识库是空的时给「让 CC 起草」（新对话里预填、不自动发送）。
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
  useRef,
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
import { altMove, type MoveSpot, useSortableItem } from "@/components/ui/drag";
import { Marquee } from "@/components/ui/marquee";
import { Skeleton } from "@/components/ui/skeleton";

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

/** 记住的选择分小节：Express 按类别，其余按来源（vat 预设叫「增值税核对」，免得与简易税票那一类混）。 */
export function choiceSection(choice: Pick<Choice, "source" | "kind">): string {
  if (choice.source === "express" || choice.source === "rule") return choice.kind;
  if (choice.source === "vat") return "vatCheck";
  return choice.source;
}

const SECTION_ORDER = ["party", "account", "payment", "vat", "item", "department", "receipt", "pos", "income", "vatCheck", "history"];

// ---------------------------------------------------------------- 卡片外框
/** 会话栏给卡片的控制：折叠（记在本机）、调顺序（Alt+↑/↓；拖动由会话栏的共用拖动负责）、登记看得见的卡片
 *  （排序列表只在登记了的卡片之间排，空卡不显示也不占位；标题给读屏播报用）。不在会话栏里时卡片各自记折叠、没有把手。 */
export type PanelControl = {
  collapsed: readonly string[];
  toggle: (id: string) => void;
  /** 挪一格；回挪完在看得见的卡片里的位置（到头了没挪回 null），Alt+↑/↓ 据此念。 */
  move: (id: string, delta: number) => MoveSpot | null;
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
  // Alt+↑/↓ 挪一格（挪动时 React 可能把这个节点摘下再插回，altMove 挪完把焦点放回原来的按钮）。先挪再念：没有播报也照样挪
  const keys = (e: ReactKeyboardEvent) =>
    control !== null &&
    altMove(e, (delta) => {
      const spot = control.move(id, delta);
      sortable?.moved(spot);
    });
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
            <Marquee id={summaryId} className="cc-panel-card-summary">
              {summary}
            </Marquee>
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

/** 会话栏里还没取到时的占位（会话栏整栏、公司页「月结」卡）：加载用骨架、不用转圈（ccs.md 卡片规矩，V27），两条字高的条。 */
export const PanelSkeleton: FC = () => (
  <div aria-hidden className="flex flex-col gap-(--cc-space-8) py-(--cc-space-8)" data-slot="cc-panel-skeleton">
    <Skeleton />
    <Skeleton className="w-2/3" />
  </div>
);

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
              <button type="button" className="cc-note-text" onClick={() => setEditing(note.id)}>
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
            <SparklesIcon />
            {co.draftNotes}
          </button>
        )}
        {error && <p role="alert" className="text-danger">{error}</p>}
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
    <div className="flex flex-col gap-(--cc-space-8) py-(--cc-space-4)" data-slot="cc-note-editor">
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
        className="cc-input h-auto w-full py-(--cc-space-6)"
      />
      {error && <p role="alert" className="text-danger">{error}</p>}
      {/* 同一栏其他卡片按钮（记住的选择、允许卡）：.cc-card-btn，电脑 24、手机 44 胶囊 */}
      <div className="cc-card-actions">
        <button type="button" className="cc-card-btn" disabled={busy} onClick={onCancel}>
          {t("cancel")}
        </button>
        <button type="button" className="cc-card-btn" data-primary="" disabled={!canSave} onClick={() => void save()}>
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
      <div className="flex flex-col gap-(--cc-space-4)">
        {folders.map((f) => (
          <div key={`${idOf(f)}-${f.device}`} className="cc-folder-line" data-state={f.reader_gone ? "missing" : f.state}>
            <FolderIcon />
            <span className="min-w-0 flex-1">
              <Marquee className="block truncate" title={f.label}>{[f.device, f.label].filter(Boolean).join(" · ")}</Marquee>
              <Marquee className="block truncate text-ink-3 text-caption">
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
            {busy ? <LoaderIcon className="cc-spinner" /> : <FolderIcon />}
            {co.chooseOnComputer}
          </button>
        )}
        {app && !companyFolders && (
          <button type="button" className="cc-btn self-start" onClick={() => openSettings("system")}>
            {co.updateDesktop}
          </button>
        )}
        {!app && !folders.length && <p className="text-ink-3">{co.chooseOnComputer}</p>}
        {error && <p role="alert" className="text-danger">{error}</p>}
      </div>
    </PanelCard>
  );
};

// ---------------------------------------------------------------- 账套
const SetRow: FC<{ set: AccountSet; covering: boolean }> = ({ set, covering }) => (
  <div className="cc-list-row cursor-default" data-covering={covering || undefined}>
    <Marquee className="min-w-0 flex-1 truncate">{set.label}</Marquee>
    <span className="shrink-0 text-ink-3 text-caption">{set.year_label}</span>
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
              <Marquee className="block truncate text-ink-3 text-caption">
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
      <span className="shrink-0 text-ink-3 text-caption">{formatWhen(d.updated_at)}</span>
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
};

/** 改了一条记住的选择后列表的样子：值与显示名换成刚选的（选项的显示名在 EDITABLE）。 */
export function setChoiceShown(choices: readonly Choice[], key: string, value: string): Choice[] {
  return choices.map((c) => {
    if (c.key !== key) return c;
    const label = EDITABLE[c.kind]?.find((o) => o.value === value)?.label();
    return { ...c, value, ...(label ? { value_label: label } : {}) };
  });
}

/** 一张卡一次只展开一条：点开着的那条收起，点别的换成它。 */
export const toggleChoice = (open: string, key: string) => (open === key ? "" : key);

/** 值是主档代码的几类：科目（含 Express 历史）、收款科目、对方档、存货、部门。收起时只写代码（280 宽右侧栏里「代码 名称」长串会把
 *  左边的对方名挤得看不见，对方档的名称又常就是对方名本身），完整的「代码 名称」在点开后的说明里（第 10 轮审查）。 */
const CODE_KINDS = new Set(["account", "receipt", "party", "item", "department"]);

/** 行尾的值（收起时 12px 淡色、不换行，放不下渐隐）：能改的写选项名（现购 / 赊购），代码类只写代码，POS 版式不写（值是内部的
 *  auto / person，没有给人看的说法），其余写显示名。 */
export function choiceValue(choice: Pick<Choice, "kind" | "value" | "value_label" | "account_label">): string {
  const option = EDITABLE[choice.kind]?.find((o) => o.value === choice.value);
  if (option) return option.label();
  if (CODE_KINDS.has(choice.kind)) return choice.value;
  if (choice.kind === "pos_layout") return "";
  return choice.value_label || choice.account_label || choice.value;
}

/** 代码的完整写法「代码 名称」（名称里已带码的照原样；没有名称只写码）。 */
export function accountFull(choice: Pick<Choice, "value" | "value_label" | "account_label">): string {
  const name = choice.account_label || choice.value_label || "";
  if (!name || name === choice.value) return choice.value;
  return name.startsWith(choice.value) ? name : `${choice.value} ${name}`;
}

/** 点开后「对方 → 代码 名称」那一行：只有代码类、且知道名称时才有（Express 账套行只有代码，再写一遍就和收起时重复了）。 */
export function choiceFullLine(choice: Pick<Choice, "kind" | "value" | "value_label" | "account_label">, subject: string): string {
  if (!CODE_KINDS.has(choice.kind)) return "";
  const full = accountFull(choice);
  return full === choice.value ? "" : `${subject} → ${full}`;
}

/** 删掉 / 提升走了一条后焦点放哪一条：下一条，没有下一条就上一条；一条都不剩回 ""。 */
export function choiceAfter(keys: readonly string[], gone: string): string {
  const i = keys.indexOf(gone);
  if (i < 0) return "";
  return keys[i + 1] ?? keys[i - 1] ?? "";
}

/** choices：服务端在没有录入权限时给 null，按没有处理。 */
export const ChoicesSection: FC<{ company: string; choices: Choice[] | null; period: string; reload: () => Promise<void> }> = ({
  company,
  choices,
  period,
  reload,
}) => {
  // 页面上显示的：改 / 删成功时先按刚做的改这里（两段式不弹回旧值），整页重取到了以服务端为准
  // 两段式改了还没回来的（键 → 刚选的值）：期间整页重取到的旧值不把它盖回去（第 11 轮审查）
  const changing = useRef(new Map<string, string>());
  const withChanging = (rows: readonly Choice[]) =>
    [...changing.current].reduce<Choice[]>((acc, [key, value]) => setChoiceShown(acc, key, value), [...rows]);
  const [list, setList] = useState(choices ?? []);
  useEffect(() => setList(withChanging(choices ?? [])), [choices]);
  // 没成功的原因写在出错的那一条里、紧挨着按钮（第 11 轮 F40：以前写在整张卡最底下，右侧栏没滚动时在视区外，只看到值跳回去）
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  // 展开的那一条（键）；一张卡一次只开一条
  const [open, setOpen] = useState("");
  // 开合换了（收起、点开别的一条）：上一条的原因不再留着
  useEffect(() => setError(null), [open]);
  // 请求还没回来的那一条：再点它的按钮不理（连点「删除」会发两次，第二次回「找不到这条选择」显示成红字，第 10 轮审查）。
  // 按钮不设 disabled：设了刚按的那一格会失焦，键盘焦点掉到页面别处（第 10 轮 D28），只在按钮行标 aria-busy。
  // 同一刻连点两下时 busy 还没重画，另记一份（pending）挡住第二下
  const [busy, setBusy] = useState("");
  const pending = useRef(new Set<string>());
  // 删掉 / 提升走了一条后键盘焦点放到哪一条（下一条，没有就上一条）：按钮跟着那一条卸掉了，焦点不能落回页面最外层
  const [focusKey, setFocusKey] = useState("");
  const anchor = useRef<HTMLSpanElement>(null);
  const groups = useMemo(() => {
    const map = new Map<string, Choice[]>();
    for (const c of list) map.set(choiceSection(c), [...(map.get(choiceSection(c)) ?? []), c]);
    return [...map.entries()].sort((a, b) => SECTION_ORDER.indexOf(a[0]) - SECTION_ORDER.indexOf(b[0]));
  }, [list]);
  useEffect(() => {
    if (!focusKey) return;
    setFocusKey("");
    const card = anchor.current?.closest('[data-slot="cc-company-choices"]');
    const row = [...(card?.querySelectorAll<HTMLElement>("[data-choice-key]") ?? [])].find((el) => el.dataset.choiceKey === focusKey);
    row?.querySelector<HTMLElement>(".cc-choice-row")?.focus();
  }, [focusKey, list]);
  if (!list.length) return null;
  // gone：这一条做完就不在列表里了（删掉 / 提升成全公司），展开着的话收起，焦点放到它旁边那一条
  const start = (key: string) => {
    if (pending.current.has(key)) return false;
    pending.current.add(key);
    setError(null);
    setBusy(key);
    return true;
  };
  const end = (key: string) => {
    pending.current.delete(key);
    setBusy((b) => (b === key ? "" : b));
  };
  const run = async (key: string, work: () => Promise<unknown>, shown: (rows: readonly Choice[]) => Choice[], gone = "") => {
    if (!start(key)) return;
    try {
      await work();
      if (gone) setFocusKey(choiceAfter(groups.flatMap(([, rows]) => rows.map((row) => row.key)), gone));
      setList(shown);
      if (gone) setOpen((o) => (o === gone ? "" : o));
      void reload();
    } catch (e) {
      setError({ key, message: shownError(e) });
    } finally {
      end(key);
    }
  };
  // 两段式改现 / 赊：点下立即显示新选择（第 10 轮 D28：以前等接口回来约 200ms 才变）；没成功只把这一条的值退回原样
  // （不整条换回点下那一刻的，期间重取到的别的字段留着），卡内报错
  const change = async (c: Choice, v: string) => {
    if (!start(c.key)) return;
    changing.current.set(c.key, v);
    setList((rows) => setChoiceShown(rows, c.key, v));
    try {
      await setChoice(company, c.key, v, period);
      // 自己这次重取回来之前一直按刚选的显示（之前发出的重取可能还带着旧值）
      void reload().finally(() => changing.current.get(c.key) === v && changing.current.delete(c.key));
    } catch (e) {
      changing.current.delete(c.key);
      setList((rows) => setChoiceShown(rows, c.key, c.value));
      setError({ key: c.key, message: shownError(e) });
    } finally {
      end(c.key);
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
              open={open === c.key}
              busy={busy === c.key}
              error={error?.key === c.key ? error.message : ""}
              onToggle={() => setOpen((o) => toggleChoice(o, c.key))}
              onSet={(v) => void change(c, v)}
              onForget={() => void run(c.key, () => forgetChoice(company, c.key), (rows) => rows.filter((row) => row.key !== c.key), c.key)}
              onPromote={() => void run(c.key, () => promoteChoice(company, c.key, period), (rows) => rows.filter((row) => row.key !== c.key), c.key)}
            />
          ))}
        </div>
      ))}
      <span ref={anchor} hidden />
    </PanelCard>
  );
};

/** 公司规则的范围：全公司 / 分店 X（总公司照分店胶囊写「总公司」）。 */
export const ruleScope = (choice: Pick<Choice, "branch">) => (choice.branch ? (choice.branch === "00000" ? branchShort(choice.branch) : co.branchN(choice.branch)) : co.wholeCompany);

type ChoiceRowProps = {
  choice: Choice;
  /** 展开着（由卡片管：一次只开一条）；不给按收起。 */
  open?: boolean;
  /** 这一条的请求还没回来：按钮行标 aria-busy，再点不理（不设 disabled，焦点留在刚按的那一格）。 */
  busy?: boolean;
  /** 这一条刚才没成功的原因（改现 / 赊、删除、设为全公司）：写在按钮下面，紧挨着刚按的操作；收着时写在行下面。 */
  error?: string;
  onToggle?: () => void;
  onSet: (value: string) => void;
  onForget: () => void;
  /** 账套行、历史行的「设为全公司」（不给就不显示）。 */
  onPromote?: () => void;
};

/** 一条记住的选择（用户 10-17 看样子图定）：收起时一行 = 对方名 + 右侧 12px 淡色的值（代码类只写代码，放不下都渐隐），
 *  不常驻按钮；整行是开合按钮（aria-expanded，回车 / 空格照原生按钮开合）。展开后整块填悬停底，对方名整段换行显示（手机上
 *  没有悬停滚动，看不全），下面 12px 淡色说明（范围 / 账套 · 谁设的 · 日期；Express 历史写来源与次数；代码类知道名称时再给
 *  「对方 → 代码 名称」），再下面靠右卡片按钮：能改的现 / 赊两段式、能提升的「设为全公司」、「删除」（幽灵、危险色字）；
 *  请求没回来时（busy）按钮行标 aria-busy、再点不理，不设 disabled（第 10 轮 D28：设了焦点会掉）。不用 hook：状态在卡片上，测试可以直接调它拿元素树。 */
export const ChoiceRow: FC<ChoiceRowProps> = ({ choice, open = false, busy = false, error = "", onToggle, onSet, onForget, onPromote }) => {
  const history = choice.source === "history";
  const rule = choice.source === "rule";
  const options = (choice.source === "express" || rule) && choice.editable !== false ? EDITABLE[choice.kind] : undefined;
  const subject = choice.subject_label || choice.supplier_name || "";
  const meta = history
    ? [co.fromHistory(Number(choice.count) || 0), formatWhen(choice.last_date ?? choice.set_at)]
    : rule
      ? [ruleScope(choice), choice.set_by ?? "", formatWhen(choice.set_at)]
      : [choice.year_label ?? "", choice.set_by ?? "", formatWhen(choice.set_at)];
  const value = choiceValue(choice);
  const full = choiceFullLine(choice, subject);
  const alert = error ? <p role="alert" className="cc-choice-error text-danger" data-slot="cc-choice-error">{error}</p> : null;
  return (
    <div className="cc-choice" data-slot="cc-choice" data-source={choice.source} data-choice-key={choice.key} data-open={open ? "" : undefined}>
      <button type="button" className="cc-choice-row" data-slot="cc-choice-row" aria-expanded={open} onClick={onToggle}>
        {open ? <span className="cc-choice-name">{subject}</span> : <Marquee className="min-w-0 flex-1 truncate">{subject}</Marquee>}
        {value && <Marquee className="cc-choice-value" data-slot="cc-choice-value">{value}</Marquee>}
      </button>
      {open && (
        <div className="cc-choice-detail" data-slot="cc-choice-detail">
          <Marquee className="block truncate">{meta.filter(Boolean).join(" · ")}</Marquee>
          {full && <span className="cc-choice-full">{full}</span>}
          <div className="cc-card-actions" aria-busy={busy || undefined}>
            {options && (
              <div role="group" aria-label={subject} className="cc-choice-seg" data-slot="cc-choice-seg">
                {options.map((o) => (
                  <button
                    key={o.value}
                    type="button"
                    className="cc-card-btn"
                    aria-pressed={choice.value === o.value}
                    onClick={() => !busy && choice.value !== o.value && onSet(o.value)}
                  >
                    {o.label()}
                  </button>
                ))}
              </div>
            )}
            {onPromote && choice.promotable && (
              <button type="button" className="cc-card-btn" data-slot="cc-choice-promote" onClick={() => !busy && onPromote()}>
                {co.makeCompanyWide}
              </button>
            )}
            <button type="button" className="cc-card-btn" data-ghost="" data-danger="" data-slot="cc-choice-forget" onClick={() => !busy && onForget()}>
              {t("delete")}
            </button>
          </div>
          {alert}
        </div>
      )}
      {!open && alert}
    </div>
  );
};
