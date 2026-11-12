// 右侧会话栏（设计文档 3.13 / 4.4；有公司分组的产品挂了公司的对话里，DMS 与没挂公司的对话没有它，ADR-020）。
// 公司卡与输出文件各产品都有；其余卡片只在产品声明了那个分区时出（sections：month / pending / notes / folders / tasks / choices /
// drafts；ERP 只有待处理与知识库），不按产品名分支。
//
//   版式   宽 280；卡片纵向、间距统一一个变量（--cc-panel-gap）。宽屏与页面并排停靠；放不下「对话最窄 + 会话栏」时浮在页面
//          上方（Esc、× 关）；手机盖满屏（从胶囊菜单里的公司名打开），返回键关。
//          对话里与文件预览共用右侧一个位置：打开文件时换成预览，关了预览回到会话栏（assistant.tsx 的 Layout）。
//   卡片   默认顺序 公司 / 本月（月结台账，components/month-ledger.tsx）/ 待处理 / 知识库 / 单据文件夹 / 定时任务 / 记住的选择 /
//          申报草稿 / 输出文件；没内容的不显示，知识库、定时任务空的也显示（「＋」加第一条，用户 11-07：公司页删了，对话里要能加）。
//          对话里「待处理」只列对话月份以外的（对话那个月的在「本月」卡，开发窗口 10-16 定）。
//          公司卡「···」：改名 / 刷新账套 / 在 ERP 中打开 / 删除公司（components/company-menu.tsx，同侧栏组头的「···」）；
//          「全部账套」在卡里展开（公司页删了以后看全部账套的地方）。「本月」卡的月份胶囊只换卡里看的月份，不移动对话（定案 #4）。
//          卡头右侧一句状态；顺序、折叠记在这台设备（lib/company-panel.ts）。
//   拖动   共用拖动（components/ui/drag.tsx，同 Claude 视频：浮起跟手、其余卡片让位、松手当帧平滑落位、只在这一列、Esc 只取消拖动、
//          键盘与读屏、手机长按把手 250ms）；6 点把手 grab / grabbing。放下时记顺序；Alt+↑/↓ 同样挪。
//   数据   公司详情按「公司|月份」缓存，只在会话栏挂着（看得见）时跟着站点事件重取（lib/company-panel 的 usePanelDetail）。
//
// 样式只用 globals.css 的 .cc-panel* 与变量。

import { useAuiState } from "@assistant-ui/react";
import { BookOpenIcon, Building2Icon, CalendarIcon, ChevronDownIcon, MoreHorizontalIcon, StoreIcon, XIcon } from "lucide-react";
import { Fragment, type FC, type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { InlineRename } from "@/components/assistant-ui/thread-list";
import { CompanyMenuItems, saveCompanyName, useCompanyMenu, useRenameAfterMenu } from "@/components/company-menu";
import { DropdownMenu, menuOffset } from "@/components/ui/menu";
import {
  ChoicesSection,
  DraftsSection,
  FolderSection,
  NotesSection,
  PanelCard,
  PanelControlContext,
  type PanelControl,
  PanelSkeleton,
  PendingCard,
  SetsList,
  TasksSection,
} from "@/components/company-cards";
import { MonthCard } from "@/components/month-ledger";
import { branchName, Choice } from "@/components/period-chip";
import { FileTypeIcon, useResults } from "@/components/results-panel";
import { co, t } from "@/i18n";
import { branchesOf, branchShort, companyLine, type Detail, monthLabel } from "@/lib/companies";
import { chooseLedger, ledgerLabel, type LedgerView, useConversationLedger } from "@/lib/conversation-ledger";
import { shownError } from "@/lib/frappe";
import { withReport } from "@/lib/caught";
import { moveCard, type PanelCard as CardId, setCardOrder, toggleCard, usePanel, usePanelDetail } from "@/lib/company-panel";
import { type Filing, NO_FILING, type Section, setError, useProjects } from "@/lib/managed-agents/projects";
import { OPEN_LAYERS } from "@/lib/back-stack";
import { opener, returnFocusAfterClose } from "@/lib/focus-return";
import { isDragEscape } from "@/lib/layers";
import { DragArea, type DragData, DragList, type DragTexts, spotIn } from "@/components/ui/drag";
import { Marquee } from "@/components/ui/marquee";

export type PanelMode = "docked" | "floating" | "phone";

/** 当前对话挂在哪家公司、哪个月、哪个分店（产品有公司分组时才有；DMS、没挂公司的对话 project 为空）。 */
export function useFiledCompany(): Filing {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const kind = useProjects((s) => s.kind);
  const filing = useProjects((s) => (remoteId && s.filings[remoteId]) || (localId && s.filings[localId]) || NO_FILING);
  return kind ? filing : NO_FILING;
}

/** 卡片拖动的读屏播报（三种语言；位置只数看得见的卡片）。 */
export const cardTexts = (): DragTexts => ({
  instructions: co.card.instructions,
  pickedUp: co.card.pickedUp,
  movedTo: co.card.movedTo,
  droppedAt: co.card.droppedAt,
  cancelled: co.card.cancelled,
});

/** 放下：在看得见的卡片里挪到落点的位置，记下整份顺序；没动、认不出不改。 */
export function dropOrder(order: readonly CardId[], items: readonly string[], active: string, over: string): CardId[] | null {
  if (active === over) return null;
  const from = items.indexOf(active);
  const to = items.indexOf(over);
  if (from < 0 || to < 0) return null;
  return moveCard(order, active as CardId, to - from, items);
}

const sameColumn = (active: DragData, target: DragData) => active.list === "cards" && target.list === "cards";

/** 会话栏里可拖动排序的那一列卡片（对话与公司页同一套）：登记看得见的卡片、共用拖动的排序列表（ui/drag）、折叠与 Alt+↑/↓。
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
      move: (id, delta) => {
        const shown: readonly string[] = itemsNow.current;
        const next = moveCard(usePanel.getState().order, id as CardId, delta, shown);
        setCardOrder(next);
        // 挪完在看得见的卡片里的位置（Alt+↑/↓ 念它）；到头了没挪不念
        const visible: string[] = next.filter((card) => shown.includes(card));
        return shown.indexOf(id) === visible.indexOf(id) ? null : spotIn(visible, id);
      },
      register,
    }),
    [collapsed, register],
  );

  const texts = useMemo(cardTexts, []);
  const onDrop = (active: DragData, target: DragData) => {
    const next = dropOrder(usePanel.getState().order, itemsNow.current, active.id, target.id);
    if (next) setCardOrder(next);
  };
  return (
    <PanelControlContext.Provider value={control}>
      <DragArea accepts={sameColumn} onDrop={onDrop} texts={texts}>
        <DragList id="cards" items={items}>
          {/* 键带公司：换到另一家时各卡重新挂上，新增 / 编辑框等不跟到另一家（同一家换对话时照常保留） */}
          {order.map((id) => (
            <Fragment key={`${scope}:${id}`}>{cards[id] ?? null}</Fragment>
          ))}
        </DragList>
      </DragArea>
    </PanelControlContext.Provider>
  );
};

/** 产品没声明的分区，那张卡不出（公司、输出文件各产品都有）。 */
export const CARD_SECTIONS: Partial<Record<CardId, Section>> = {
  month: "month", pending: "pending", notes: "notes", folders: "folders", tasks: "tasks", choices: "choices", drafts: "drafts",
};

export function declaredCards(cards: Record<CardId, ReactNode>, sections: readonly Section[]): Record<CardId, ReactNode> {
  return Object.fromEntries(Object.entries(cards).map(([id, card]) => {
    const section = CARD_SECTIONS[id as CardId];
    return [id, section && !sections.includes(section) ? null : card];
  })) as Record<CardId, ReactNode>;
}

// 会话栏开关（顶栏右侧，assistant.tsx）：关掉浮着的会话栏后焦点的去处
const PANEL_TOGGLE = '[data-slot="cc-panel-toggle"]';

/** session：这段对话的服务端编号（右栏的本对话账套按它读、按它改）。 */
export const SessionPanel: FC<{ session: string; filing: Filing; mode: PanelMode; onClose: () => void }> = ({ session, filing, mode, onClose }) => {
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

  // 浮层、全屏：Esc 关（菜单、弹窗开着时它们先关）。一次只关一层：关了会话栏就标记已处理，侧栏多选（挂在 window 上、随后
  // 才收到同一个 Esc）这一次不清，再按一次才清（测试窗口第 5 轮：取消拖动后再按 Esc，会话栏和多选被一起清掉）
  useEffect(() => {
    if (mode === "docked") return;
    const onKey = (e: KeyboardEvent) => {
      // 输入法选字时的 Esc 只是取消候选词；拖动中的 Esc 只是取消拖动（鼠标、键盘拖都一样）
      if (e.key !== "Escape" || e.isComposing || e.defaultPrevented || document.querySelector(OPEN_LAYERS)) return;
      if (isDragEscape(e)) return;
      e.preventDefault();
      onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [mode, onClose]);

  // 浮层、全屏关掉（Esc、关闭按钮、换页）以后焦点回到打开它的控件（会话栏开关、「公司名 ›」）；那个不在了就回会话栏开关。
  // 焦点原来在会话栏里才还（第 6 轮：Esc 关掉后焦点落到 BODY）。只在卸下时（停靠 ↔ 浮层切换不算），在节点拿走之前量
  const [from] = useState(() => (mode === "docked" ? null : opener()));
  const floating = useRef(mode !== "docked");
  floating.current = mode !== "docked";
  useLayoutEffect(
    () => () => {
      if (!floating.current) return;
      returnFocusAfterClose(!!box.current?.contains(document.activeElement), from, PANEL_TOGGLE);
    },
    [from],
  );

  const branches = detail ? branchesOf(detail) : [];
  const multi = branches.length > 1 ? branches : [];
  const sections = useProjects((s) => s.sections);
  const all: Record<CardId, ReactNode> = {
    company: detail ? <CompanyCard detail={detail} session={session} filing={filing} reload={reload} /> : null,
    month: <MonthCard company={company} period={period} />,
    pending: <PendingCard company={company} period={period} />,
    notes: detail ? <NotesSection company={company} notes={detail.notes} reload={reload} /> : null,
    folders: detail ? <FolderSection company={company} folders={detail.folders} reload={reload} hideEmpty /> : null,
    tasks: detail ? <TasksSection company={company} detail={detail} /> : null,
    choices: detail ? <ChoicesSection company={company} choices={detail.choices} period={period} reload={reload} /> : null,
    drafts: detail?.drafts ? (
      <DraftsSection company={company} drafts={detail.drafts} branches={multi} combined={!!detail.company.combined_vat} />
    ) : null,
    outputs: <OutputsCard />,
  };
  const cards = declaredCards(all, sections);
  return (
    <aside
      ref={box}
      className="cc-panel"
      data-mode={mode}
      data-slot="cc-session-panel"
      aria-label={detail?.company.title || co.company}
    >
      {mode !== "docked" && (
        <div className="cc-panel-top" data-marquee-host="">
          <Marquee className="min-w-0 flex-1 truncate">{detail?.company.title ?? ""}</Marquee>
          <button type="button" className="cc-icon-btn" aria-label={t("close")} title={t("close")} onClick={onClose}>
            <XIcon />
          </button>
        </div>
      )}
      {!detail && !error && <PanelSkeleton />}
      {error && <p role="alert" className="px-(--cc-space-8) text-danger text-body">{error}</p>}
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

/** 「ชุดบัญชี」一行：本对话账套（服务端定的写入目标，lib/conversation-ledger；A 线 11-12 R2-5：原先页面按日期现算，
 *  与服务端写入的不是同一本）。点开是这家这个月可选的几本（同月份胶囊的下拉），选了就 move 带 ledger；只有一本也照样能点开。
 *  未定时值空着；这家没有本对话账套（产品没有、ERP 不接 Express 的公司）时不出这一行。 */
// map: ledger-picker
export const LedgerRow: FC<{ session: string; filing: Filing }> = ({ session, filing }) => {
  const entry = useConversationLedger(session, filing);
  const choose = (id: string) =>
    void chooseLedger(session, filing, id).catch(withReport((error: unknown) => setError(shownError(error)), "ledger"));
  return <LedgerMenu entry={entry} onChoose={choose} />;
};

/** 那一行本身（当前值与可选项都是服务端给的 LedgerRow 传进来）。 */
export const LedgerMenu: FC<{ entry: LedgerView | null; onChoose: (id: string) => void }> = ({ entry, onChoose }) => {
  if (!entry?.options.length) return null;
  const label = ledgerLabel(entry);
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button type="button" className="cc-panel-row" data-slot="cc-company-ledger" title={label}>
          <BookOpenIcon />
          <Marquee className="min-w-0 flex-1 truncate">{co.accountSets}</Marquee>
          <Marquee className="cc-panel-extra">{label}</Marquee>
          <ChevronDownIcon />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={menuOffset()} className="cc-menu min-w-(--cc-menu-w-sm) [--cc-menu-max:var(--cc-menu-max-list)]">
          {entry.options.map((item) => (
            <Choice key={item.id} chosen={item.id === entry.current} onSelect={() => item.id !== entry.current && onChoose(item.id)}>
              {item.label}
            </Choice>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};

/** 公司卡：名称、法定名、税号 · 分店，合并申报；本对话的月份、分店与本对话账套（LedgerRow，服务端定的、可改），下面「全部账套」
 *  展开看这家的全部账套（多分店按分店分组，覆盖这个月的高亮：服务端标的 covers）。
 *  卡头「···」：这家公司的改名 / 刷新账套 / 在 ERP 中打开 / 删除公司（同侧栏组头，按产品声明；一项都没有时不出）；改名就地改名称那一行。 */
const CompanyCard: FC<{ detail: Detail; session: string; filing: Filing; reload: () => Promise<void> }> = ({ detail, session, filing, reload }) => {
  const { period, scope } = filing;
  const c = detail.company;
  const branches = branchesOf(detail);
  const branch = branches.find((b) => b.branch === scope);
  const legal = c.legal_name && c.legal_name !== c.title ? c.legal_name : "";
  const line = companyLine(c);
  const menu = useCompanyMenu();
  const [renaming, setRenaming] = useState(false);
  const rename = useRenameAfterMenu(() => setRenaming(true));
  const [allSets, setAllSets] = useState(false);
  return (
    <PanelCard
      id="company"
      title={co.company}
      slot="cc-panel-company"
      forceOpen={renaming}
      action={
        menu.any ? (
          <DropdownMenu.Root>
            <DropdownMenu.Trigger className="cc-panel-btn" aria-label={t("more")} title={t("more")} data-slot="cc-company-more">
              <MoreHorizontalIcon />
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content align="end" sideOffset={menuOffset()} className="cc-menu min-w-(--cc-menu-w-md)" onCloseAutoFocus={rename.onCloseAutoFocus}>
                <CompanyMenuItems company={c.id} onRename={rename.request} onChanged={() => void reload()} />
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        ) : undefined
      }
    >
      <div className="cc-panel-row" data-strong="" title={c.title}>
        <Building2Icon />
        {renaming ? (
          <InlineRename
            value={c.title}
            onSave={(next) => void saveCompanyName(c.id, next).then((saved) => (saved ? reload() : undefined))}
            onDone={() => setRenaming(false)}
          />
        ) : (
          <Marquee className="min-w-0 flex-1 truncate">{c.title}</Marquee>
        )}
      </div>
      {legal && <div className="cc-panel-row cc-panel-indent"><Marquee className="truncate">{legal}</Marquee></div>}
      {line && <div className="cc-panel-row cc-panel-indent"><Marquee className="truncate">{line}</Marquee></div>}
      {!!c.combined_vat && (
        <div className="cc-panel-row cc-panel-indent">
          <span className="cc-chip">{co.combinedVat}</span>
        </div>
      )}
      {period && <InfoRow icon={<CalendarIcon />} label={co.period} value={monthLabel(period)} />}
      {scope && <InfoRow icon={<StoreIcon />} label={co.branch} value={branch ? branchName(branch) : branchShort(scope)} />}
      <LedgerRow session={session} filing={filing} />
      {detail.account_sets.length > 0 && (
        <>
          {/* 同待处理卡的展开行（.cc-pending-toggle） */}
          <button type="button" className="cc-pending-toggle cc-panel-row" aria-expanded={allSets} data-slot="cc-company-all-sets" onClick={() => setAllSets((v) => !v)}>
            <BookOpenIcon />
            <Marquee className="min-w-0 flex-1 truncate">{co.allSets}</Marquee>
            <span className="cc-panel-extra">{detail.account_sets.length}</span>
            <ChevronDownIcon className="cc-project-chevron shrink-0 text-ink-3" />
          </button>
          {allSets && <SetsList sets={detail.account_sets} branches={branches.length > 1 ? branches : []} />}
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
