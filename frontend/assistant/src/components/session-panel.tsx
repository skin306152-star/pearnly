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
//   拖动   共用拖动（components/ui/drag.tsx，同 Claude 视频：浮起跟手、其余卡片让位、松手当帧平滑落位、只在这一列、Esc 只取消拖动、
//          键盘与读屏、手机长按把手 250ms）；6 点把手 grab / grabbing。放下时记顺序；Alt+↑/↓ 同样挪。
//   数据   公司详情按「公司|月份」缓存，只在会话栏挂着（看得见）时跟着站点事件重取（lib/company-panel 的 usePanelDetail）。
//
// 样式只用 globals.css 的 .cc-panel* 与变量。

import { useAuiState } from "@assistant-ui/react";
import { BookOpenIcon, Building2Icon, CalendarIcon, ChevronRightIcon, LoaderIcon, StoreIcon, XIcon } from "lucide-react";
import { Fragment, type FC, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { isDragEscape } from "@/lib/layers";
import { DragArea, type DragData, DragList, type DragTexts } from "@/components/ui/drag";
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
      // 输入法选字时的 Esc 只是取消候选词；拖动中的 Esc 只是取消拖动（鼠标、键盘拖都一样）
      if (e.key !== "Escape" || e.isComposing || e.defaultPrevented || document.querySelector(OPEN_LAYERS)) return;
      if (isDragEscape(e)) return;
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
        <div className="cc-panel-top" data-marquee-host="">
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
