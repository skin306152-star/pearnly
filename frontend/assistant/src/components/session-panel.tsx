// 右侧会话栏（设计文档 3.13 / 4.4；Cowork 挂了公司的对话与公司页里，ERP / DMS 与没挂公司的对话没有它，ADR-020）。
//
//   版式   宽 280；卡片纵向、间距统一一个变量（--cc-panel-gap）。宽屏与页面并排停靠；放不下「对话最窄 + 会话栏」时浮在页面
//          上方（Esc、× 关）；手机盖满屏（对话页从胶囊菜单里的公司名、公司页从标题旁的按钮打开），返回键关。
//          对话里与文件预览共用右侧一个位置：打开文件时换成预览，关了预览回到会话栏（assistant.tsx 的 Layout）。
//   卡片   默认顺序 公司 / 本月（月结台账，components/month-ledger.tsx）/ 待处理 / 知识库 / 单据文件夹 / 定时任务 / 记住的选择 /
//          申报草稿 / 输出文件；没内容的不显示。对话里「待处理」只列对话月份以外的（对话那个月的在「本月」卡，开发窗口 10-16 定）。
//          公司页（page）：公司卡写公司信息与全部账套，「月结」卡带月份胶囊（公司页自己的月份），待处理列全部月份，
//          知识库（空时「让 CC 起草」）与定时任务空的也显示，没有输出文件。
//          卡头右侧一句状态；顺序、折叠记在这台设备（lib/company-panel.ts，对话与公司页同一份）。
//   拖动   同 Claude：dnd-kit 排序列表，只能上下、不出卡片这一列（按整列内容算，不按会话栏看得见的那一截，长卡、
//          滚动中照样跟手；拖到边上会话栏自己滚）；6 点把手 grab / grabbing，拿起的卡浮起（阴影、略透明、略放大）
//          跟着指针走，其余卡片实时让位（变换过渡用动效变量），放下有过渡、放下时记顺序；键盘 空格拿起、方向键移动、空格放下、
//          Esc 取消（三种语言读屏播报）；手机长按 250ms（5px 内）开始拖。
//   数据   公司详情按「公司|月份」缓存，只在会话栏挂着（看得见）时跟着站点事件重取（lib/company-panel 的 usePanelDetail）。
//
// 样式只用 globals.css 的 .cc-panel* 与变量。

import {
  type Announcements,
  closestCenter,
  DndContext,
  type DragEndEvent,
  type DragStartEvent,
  KeyboardSensor,
  type Modifier,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { restrictToVerticalAxis } from "@dnd-kit/modifiers";
import { SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { useAuiState } from "@assistant-ui/react";
import { BookOpenIcon, Building2Icon, CalendarIcon, ChevronRightIcon, LoaderIcon, StoreIcon, XIcon } from "lucide-react";
import { Fragment, type FC, type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useOpenCompany } from "@/components/assistant-ui/projects";
import { companyLine } from "@/components/companies";
import {
  ChoicesSection,
  DraftsSection,
  FolderSection,
  NotesSection,
  PanelCard,
  PanelControlContext,
  type PanelControl,
  PendingCard,
  SetsList,
  TasksSection,
} from "@/components/company-cards";
import { MonthCard, MonthSection } from "@/components/month-ledger";
import { branchName } from "@/components/period-chip";
import { FileTypeIcon, useResults } from "@/components/results-panel";
import { co, t } from "@/i18n";
import { branchesOf, branchShort, type Detail, monthLabel, setsForScope } from "@/lib/companies";
import { moveCard, type PanelCard as CardId, setCardOrder, setPagePeriod, toggleCard, usePanel, usePanelDetail } from "@/lib/company-panel";
import { type Filing, NO_FILING, useProjects } from "@/lib/managed-agents/projects";
import { OPEN_LAYERS } from "@/lib/back-stack";
import { isCardDragging, setCardDragging } from "@/lib/layers";
import { Marquee } from "@/components/ui/marquee";

export type PanelMode = "docked" | "floating" | "phone";

/** 当前对话挂在哪家公司、哪个月、哪个分店（产品有公司分组时才有；ERP / DMS、没挂公司的对话 project 为空）。 */
export function useFiledCompany(): Filing {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const kind = useProjects((s) => s.kind);
  const filing = useProjects((s) => (remoteId && s.filings[remoteId]) || (localId && s.filings[localId]) || NO_FILING);
  return kind ? filing : NO_FILING;
}

/** 卡片拖动的读屏播报（三种语言；位置只数看得见的卡片）。 */
export function cardAnnouncements(items: readonly string[], titles: Readonly<Record<string, string>>): Announcements {
  const name = (id: string | number) => titles[String(id)] || String(id);
  const at = (id: string | number | undefined) => (id === undefined ? 0 : items.indexOf(String(id)) + 1);
  return {
    onDragStart: ({ active }) => co.card.pickedUp(name(active.id)),
    onDragOver: ({ active, over }) => (over ? co.card.movedTo(name(active.id), at(over.id), items.length) : undefined),
    onDragEnd: ({ active, over }) => (over ? co.card.droppedAt(name(active.id), at(over.id), items.length) : undefined),
    onDragCancel: ({ active }) => co.card.cancelled(name(active.id)),
  };
}

/** 放下：在看得见的卡片里挪到落点的位置，记下整份顺序；没动、落在外面不改。 */
export function dropOrder(order: readonly CardId[], items: readonly string[], event: Pick<DragEndEvent, "active" | "over">): CardId[] | null {
  const { active, over } = event;
  if (!over || active.id === over.id) return null;
  const from = items.indexOf(String(active.id));
  const to = items.indexOf(String(over.id));
  if (from < 0 || to < 0) return null;
  return moveCard(order, String(active.id) as CardId, to - from, items);
}

// 拖动的手感（同 Claude 的会话栏）：鼠标按住动 3px 才开始（点一下照样是点），手机长按 250ms 且没挪出 5px 才开始（滑动照样滚动）
const MOUSE_START = { distance: 3 };
const TOUCH_START = { delay: 250, tolerance: 5 };

/** 拿起的卡只在这一列卡片的上下边之间（第一张卡的顶边到最后一张的底边）。用版面位置（offsetTop，不含变换、不随滚动变），
 *  不用会话栏看得见的框：会话栏本身是滚动容器，按它限制时超出可视区的长卡一拿起就被拽上去、再也拖不下来（审查第 4 轮）。 */
export function clampToList(y: number, card: { top: number; height: number }, list: { top: number; bottom: number }): number {
  const min = list.top - card.top;
  const max = list.bottom - (card.top + card.height);
  return Math.min(Math.max(y, min), Math.max(min, max));
}

function withinList(node: { current: HTMLElement | null }): Modifier {
  return ({ transform }) => {
    const el = node.current;
    const column = el?.parentElement;
    if (!el || !column) return transform;
    const cards = [...column.children].filter((c): c is HTMLElement => c instanceof HTMLElement && c.dataset.card !== undefined);
    if (!cards.length) return transform;
    const top = Math.min(...cards.map((c) => c.offsetTop));
    const bottom = Math.max(...cards.map((c) => c.offsetTop + c.offsetHeight));
    return { ...transform, y: clampToList(transform.y, { top: el.offsetTop, height: el.offsetHeight }, { top, bottom }) };
  };
}

/** 会话栏里可拖动排序的那一列卡片（对话与公司页同一套）：登记看得见的卡片、dnd-kit 排序列表、折叠与 Alt+↑/↓。
 *  不画外框（卡片直接是会话栏的子节点，「不出会话栏」按父节点算）。cards 里给 null 的卡不显示、也不占位。 */
export const SortableCards: FC<{ cards: Partial<Record<CardId, ReactNode>>; scope: string }> = ({ cards, scope }) => {
  const order = usePanel((s) => s.order);
  const collapsed = usePanel((s) => s.collapsed);

  // 看得见的卡片（各卡挂上时登记、卸下时注销；空卡不渲染就不登记）：排序列表与 Alt+↑/↓ 只在它们之间挪
  const [titles, setTitles] = useState<Record<string, string>>({});
  const register = useCallback((id: string, title: string) => {
    setTitles((before) => (before[id] === title ? before : { ...before, [id]: title }));
    return () =>
      setTitles((before) => {
        if (!(id in before)) return before;
        const { [id]: _gone, ...rest } = before;
        return rest;
      });
  }, []);
  const items = useMemo(() => order.filter((id) => id in titles), [order, titles]);
  const itemsNow = useRef(items);
  itemsNow.current = items;

  const control = useMemo<PanelControl>(
    () => ({
      collapsed,
      toggle: (id) => toggleCard(id as CardId),
      move: (id, delta) => setCardOrder(moveCard(usePanel.getState().order, id as CardId, delta, itemsNow.current)),
      register,
    }),
    [collapsed, register],
  );

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: MOUSE_START }),
    useSensor(TouchSensor, { activationConstraint: TOUCH_START }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const accessibility = useMemo(
    () => ({ announcements: cardAnnouncements(items, titles), screenReaderInstructions: { draggable: co.card.instructions } }),
    [items, titles],
  );
  // 拿起的那张卡（把手所在的 [data-card]）：限制在卡片这一列里用
  const lifted = useRef<HTMLElement | null>(null);
  const modifiers = useMemo(() => [restrictToVerticalAxis, withinList(lifted)], []);
  useEffect(() => () => setCardDragging(false), []);
  // 放下 / 取消的过渡：dnd-kit 不带 DragOverlay 时放下是直接跳到位。记下放手那一刻卡的位置（中线，不受略放大影响），
  // 换到新位置的那次提交里先用 translate 摆回放手处、再过渡到 0（globals.css 的 data-landing，动效变量）。
  // 用 translate 而不是 transform：transform 归 dnd-kit 管。
  const landing = useRef<{ el: HTMLElement; mid: number } | null>(null);
  const [landed, setLanded] = useState(0);
  const release = () => {
    const el = lifted.current;
    lifted.current = null;
    setCardDragging(false);
    if (!el) return;
    const r = el.getBoundingClientRect();
    landing.current = { el, mid: r.top + r.height / 2 };
    setLanded((n) => n + 1);
  };
  useLayoutEffect(() => {
    const l = landing.current;
    landing.current = null;
    if (!l || !l.el.isConnected) return;
    const r = l.el.getBoundingClientRect();
    const dy = l.mid - (r.top + r.height / 2);
    if (Math.abs(dy) < 1) return;
    l.el.style.setProperty("--cc-land-y", `${dy}px`);
    l.el.dataset.landing = "";
    void l.el.offsetHeight; // 先在放手处排一次版，去掉标记后才有过渡
    delete l.el.dataset.landing;
  }, [landed]);
  const onDragStart = (event: DragStartEvent) => {
    const target = event.activatorEvent.target;
    lifted.current = target instanceof Element ? target.closest<HTMLElement>("[data-card]") : null;
    setCardDragging(true);
  };
  const onDragCancel = () => release();
  const onDragEnd = (event: DragEndEvent) => {
    release();
    const next = dropOrder(usePanel.getState().order, itemsNow.current, event);
    if (next) setCardOrder(next);
  };
  return (
    <PanelControlContext.Provider value={control}>
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        modifiers={modifiers}
        accessibility={accessibility}
        onDragStart={onDragStart}
        onDragCancel={onDragCancel}
        onDragEnd={onDragEnd}
      >
        <SortableContext items={items} strategy={verticalListSortingStrategy}>
          {/* 键带公司：换到另一家时各卡重新挂上，新增 / 编辑框等不跟到另一家（同一家换对话时照常保留） */}
          {order.map((id) => (
            <Fragment key={`${scope}:${id}`}>{cards[id] ?? null}</Fragment>
          ))}
        </SortableContext>
      </DndContext>
    </PanelControlContext.Provider>
  );
};

export const SessionPanel: FC<{ filing: Filing; mode: PanelMode; onClose: () => void; page?: boolean }> = ({ filing, mode, onClose, page = false }) => {
  const { project: company, period, scope } = filing;
  const { detail, error, reload } = usePanelDetail(company, period);
  const focus = usePanel((s) => s.focus);
  const box = useRef<HTMLElement>(null);

  // 点了「公司名 ›」等：滚到那张卡（同一次只滚一回，之后的重取不再抢位置）
  const scrolled = useRef(0);
  useEffect(() => {
    if (!focus || scrolled.current === focus.at) return;
    const card = box.current?.querySelector<HTMLElement>(`[data-card="${focus.card}"]`);
    if (!card) return;
    scrolled.current = focus.at;
    card.scrollIntoView({ block: "nearest" });
  }, [focus, detail]);

  // 浮层、全屏：Esc 关（菜单、弹窗开着时它们先关）
  useEffect(() => {
    if (mode === "docked") return;
    const onKey = (e: KeyboardEvent) => {
      // 输入法选字时的 Esc 只是取消候选词；拖着卡片时的 Esc 只是取消拖动
      if (e.key !== "Escape" || e.isComposing || e.defaultPrevented || document.querySelector(OPEN_LAYERS)) return;
      if (isCardDragging()) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [mode, onClose]);

  const branches = detail ? branchesOf(detail) : [];
  const multi = branches.length > 1 ? branches : [];
  const cards: Record<CardId, ReactNode> = page
    ? {
        company: detail ? <CompanyPageCard detail={detail} period={period} /> : null,
        month: <MonthSection company={company} period={period} onPeriod={setPagePeriod} />,
        pending: <PendingCard company={company} />,
        notes: detail ? <NotesSection company={company} notes={detail.notes} reload={reload} keepEmpty /> : null,
        folders: detail ? <FolderSection company={company} folders={detail.folders} reload={reload} /> : null,
        tasks: detail ? <TasksSection company={company} detail={detail} period={period} keepEmpty /> : null,
        choices: detail ? <ChoicesSection company={company} choices={detail.choices} period={period} reload={reload} /> : null,
        drafts: detail?.drafts ? (
          <DraftsSection company={company} drafts={detail.drafts} branches={multi} combined={!!detail.company.combined_vat} />
        ) : null,
        outputs: null,
      }
    : {
        company: detail ? <CompanyCard detail={detail} period={period} scope={scope} /> : null,
        month: <MonthCard company={company} period={period} />,
        pending: <PendingCard company={company} period={period} />,
        notes: detail ? <NotesSection company={company} notes={detail.notes} reload={reload} /> : null,
        folders: detail ? <FolderSection company={company} folders={detail.folders} reload={reload} hideEmpty /> : null,
        tasks: detail ? <TasksSection company={company} detail={detail} period={period} /> : null,
        choices: detail ? <ChoicesSection company={company} choices={detail.choices} period={period} reload={reload} /> : null,
        drafts: detail?.drafts ? (
          <DraftsSection company={company} drafts={detail.drafts} branches={multi} combined={!!detail.company.combined_vat} />
        ) : null,
        outputs: <OutputsCard />,
      };
  return (
    <aside
      ref={box}
      className="cc-panel"
      data-mode={mode}
      data-page={page || undefined}
      data-slot="cc-session-panel"
      aria-label={detail?.company.title || co.company}
    >
      {mode !== "docked" && (
        <div className="cc-panel-top">
          <Marquee className="min-w-0 flex-1 truncate">{detail?.company.title ?? ""}</Marquee>
          <button type="button" className="cc-icon-btn" aria-label={t("close")} title={t("close")} onClick={onClose}>
            <XIcon />
          </button>
        </div>
      )}
      {!detail && !error && (
        <div className="flex justify-center py-4 text-ink-3">
          <LoaderIcon className="size-4 animate-spin" />
        </div>
      )}
      {error && <p role="alert" className="px-2 text-danger text-sm">{error}</p>}
      <SortableCards cards={cards} scope={company} />
    </aside>
  );
};

// ---------------------------------------------------------------- 公司
const InfoRow: FC<{ icon: ReactNode; label: string; value: string }> = ({ icon, label, value }) => (
  <div className="cc-panel-row" title={value}>
    {icon}
    <Marquee className="min-w-0 flex-1 truncate">{label}</Marquee>
    <Marquee className="cc-panel-extra">{value}</Marquee>
  </div>
);

/** 公司卡：名称（点了打开公司页）、法定名、税号 · 分店，合并申报；本对话的月份、分店与覆盖这个月的账套
 *  （没指定分店时各分店的都列，总店在前，一份一行；lib/companies 的 setsForScope）。 */
const CompanyCard: FC<{ detail: Detail; period: string; scope: string }> = ({ detail, period, scope }) => {
  const openCompany = useOpenCompany();
  const c = detail.company;
  const branch = branchesOf(detail).find((b) => b.branch === scope);
  const sets = setsForScope(detail, period, scope);
  const legal = c.legal_name && c.legal_name !== c.title ? c.legal_name : "";
  const line = companyLine(c);
  return (
    <PanelCard id="company" title={co.company} summary={sets.length ? co.card.sets(sets.length) : undefined} slot="cc-panel-company">
      <button type="button" className="cc-panel-row" data-strong="" title={co.openCompany} onClick={() => openCompany(c.id)}>
        <Building2Icon />
        <Marquee className="min-w-0 flex-1 truncate">{c.title}</Marquee>
        <ChevronRightIcon />
      </button>
      {legal && <div className="cc-panel-row cc-panel-indent"><Marquee className="truncate">{legal}</Marquee></div>}
      {line && <div className="cc-panel-row cc-panel-indent"><Marquee className="truncate">{line}</Marquee></div>}
      {!!c.combined_vat && (
        <div className="cc-panel-row cc-panel-indent">
          <span className="cc-chip">{co.combinedVat}</span>
        </div>
      )}
      {period && <InfoRow icon={<CalendarIcon />} label={co.period} value={monthLabel(period)} />}
      {scope && <InfoRow icon={<StoreIcon />} label={co.branch} value={branch ? branchName(branch) : branchShort(scope)} />}
      {sets.length === 1 && <InfoRow icon={<BookOpenIcon />} label={co.accountSets} value={sets[0]!.label} />}
      {sets.length > 1 && (
        <>
          <InfoRow icon={<BookOpenIcon />} label={co.accountSets} value="" />
          {sets.map((set) => (
            <div key={`${set.connection}|${set.key}`} className="cc-panel-row cc-panel-indent" title={set.label}>
              <Marquee className="min-w-0 flex-1 truncate">{set.label}</Marquee>
            </div>
          ))}
        </>
      )}
    </PanelCard>
  );
};

/** 公司页的公司卡（名称已是页面标题）：法定名（与名称不同才写）、税号 · 分店、合并申报，下面是全部账套（多分店按分店分组，覆盖公司页这个月的高亮）。 */
const CompanyPageCard: FC<{ detail: Detail; period: string }> = ({ detail, period }) => {
  const c = detail.company;
  const branches = branchesOf(detail);
  const legal = c.legal_name && c.legal_name !== c.title ? c.legal_name : "";
  const line = companyLine(c);
  const sets = detail.account_sets;
  if (!legal && !line && !c.combined_vat && !sets.length) return null;
  return (
    <PanelCard id="company" title={co.company} summary={sets.length ? co.card.sets(sets.length) : undefined} slot="cc-panel-company">
      {legal && <div className="cc-panel-row text-ink" title={legal}><Marquee className="truncate">{legal}</Marquee></div>}
      {line && <div className="cc-panel-row" title={line}><Marquee className="truncate">{line}</Marquee></div>}
      {!!c.combined_vat && (
        <div className="cc-panel-row">
          <span className="cc-chip">{co.combinedVat}</span>
        </div>
      )}
      {sets.length > 0 && (
        <>
          <InfoRow icon={<BookOpenIcon />} label={co.accountSets} value="" />
          <SetsList sets={sets} branches={branches.length > 1 ? branches : []} period={period} />
        </>
      )}
    </PanelCard>
  );
};

// ---------------------------------------------------------------- 输出文件
/** 本对话产出的文件：点一行在右侧预览（换成文件预览，关了回到会话栏）。 */
const OutputsCard: FC = () => {
  const { files, openFile } = useResults();
  if (!files.length) return null;
  return (
    <PanelCard id="outputs" title={t("files")} summary={co.card.files(files.length)} slot="cc-panel-outputs">
      {files.map((file) => (
        <button key={file.id} type="button" className="cc-panel-row" title={file.filename} onClick={() => openFile(file.id)}>
          <FileTypeIcon filename={file.filename} />
          <Marquee className="min-w-0 flex-1 truncate">{file.filename}</Marquee>
        </button>
      ))}
    </PanelCard>
  );
};
